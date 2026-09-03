"""Per-source level metering.

For each source we want a level bar for, spawn a low-rate `pw-cat --record`
in mono s16 at 8 kHz (16 KB/s — cheap), read in a daemon thread, compute the
peak of each chunk, and marshal the value onto the GTK main thread via
GLib.idle_add. Independent of the loopback subprocess plumbing in mixer.py
to keep concerns separate.
"""

import json
import os
import struct
import subprocess
import threading
import time

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib  # noqa: E402

from .mixer import _set_pdeathsig  # share the pdeathsig helper


class MeterMonitor:
    SAMPLE_RATE = 8000
    # ~64 ms of s16 mono @ 8 kHz → ~15 Hz updates. Was 256 bytes / 60 Hz,
    # which cost a GLib.idle_add per chunk per meter — over 400 main-loop
    # wakeups a second across seven meters, for bars the eye cannot follow
    # past ~15 Hz anyway. The peak of a 64 ms window still catches every
    # transient; it is the standard meter integration ballpark.
    CHUNK_BYTES = 1024

    def __init__(self):
        # While the window is hidden the bars do not exist to anyone;
        # readers keep draining (byte-flow stall detection depends on it)
        # but nothing crosses to the GTK thread.
        self.ui_suspended = False
        self._procs = {}        # source_id -> Popen
        self._threads = {}      # source_id -> Thread
        self._stop_flags = {}   # source_id -> threading.Event
        self._cbs = {}          # source_id -> callable(float)
        # When each meter last received *any* bytes. A stalled capture device
        # delivers nothing rather than delivering zeros, so this distinguishes
        # "dead" from "quiet" -- which a peak level cannot, since a muted
        # microphone in a quiet room is legitimately near zero.
        self._last_data = {}    # source_id -> monotonic seconds

    def start(self, source_id, source_node_name, callback, capture_sink=False):
        """Begin streaming peak values for `source_id`. Replaces any existing
        meter for that id. `callback(level: float)` is invoked on the main
        thread at the chunk rate.

        `capture_sink=True` meters a SINK by its monitor. Without it a
        record stream targeting a sink is not an error: the session manager
        quietly links it to the default source instead, so a mix meter
        showed whatever microphone happened to be the default input.
        """
        if source_id in self._procs:
            self.stop(source_id)
        props = {
            # Labelled so a level tap is identifiable in a mixer or a
            # monitoring script. Unlabelled these appear as bare
            # "pw-cat" entries indistinguishable from anyone else's.
            "node.name": f"openwave_meter_{source_id}",
            "node.description": f"OpenWave level meter ({source_id})",
            "application.name": "OpenWave",
        }
        if capture_sink:
            props["stream.capture.sink"] = True
        try:
            proc = subprocess.Popen(
                [
                    "pw-cat", "--record",
                    "--target", source_node_name,
                    "--properties", json.dumps(props),
                    "--rate", str(self.SAMPLE_RATE),
                    "--channels", "1",
                    "--format", "s16",
                    "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                preexec_fn=_set_pdeathsig,
            )
        except (FileNotFoundError, OSError):
            return

        stop_flag = threading.Event()
        thread = threading.Thread(
            target=self._reader,
            args=(source_id, proc, stop_flag),
            daemon=True,
        )
        self._procs[source_id] = proc
        self._threads[source_id] = thread
        self._stop_flags[source_id] = stop_flag
        self._cbs[source_id] = callback
        # Seeded at start, not left unset: a meter that has never received a
        # byte is exactly the stall being looked for, and would otherwise
        # look like a meter that simply has no history yet.
        self._last_data[source_id] = time.monotonic()
        thread.start()

    def running(self, source_id):
        """Whether a live meter subprocess exists for this id."""
        proc = self._procs.get(source_id)
        return proc is not None and proc.poll() is None

    def stop(self, source_id):
        flag = self._stop_flags.pop(source_id, None)
        if flag is not None:
            flag.set()
        proc = self._procs.pop(source_id, None)
        self._threads.pop(source_id, None)
        self._cbs.pop(source_id, None)
        self._last_data.pop(source_id, None)
        if proc is None:
            return
        try:
            proc.terminate()
        except (OSError, ProcessLookupError):
            return
        # Reaped off the caller's thread. stop() is called from the GTK
        # thread on every meter refresh, and the waits below are up to two
        # seconds each: a main loop sitting in waitpid is a main loop not
        # servicing its Wayland connection, which is how a window being
        # moved around ends up killed for not draining its socket.
        threading.Thread(
            target=self._reap, args=(proc,), daemon=True,
        ).start()

    @staticmethod
    def _reap(proc):
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=1)
            except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
                pass

    def stop_all(self):
        for sid in list(self._procs.keys()):
            self.stop(sid)

    # Frames of continued dispatch after the signal goes quiet, so a
    # bar with peak-hold ballistics animates down before the stream of
    # updates stops. ~20 frames at ~15 Hz is over a second of tail.
    _QUIET = 0.004
    _TAIL_FRAMES = 20

    def _reader(self, source_id, proc, stop_flag):
        """Background thread: read s16 chunks, compute peak, marshal to UI.

        Silence is suppressed: nine meters at 15 Hz were over a hundred
        main-loop wakeups and redraws a second for bars sitting at zero.
        A quiet chunk still counts for the byte-flow stall detection — it
        is only the UI dispatch that rests.
        """
        tail = 0
        settled = False
        try:
            while not stop_flag.is_set():
                data = proc.stdout.read(self.CHUNK_BYTES)
                if not data or len(data) < 2:
                    break
                if source_id in self._procs:   # not a meter already stopped
                    self._last_data[source_id] = time.monotonic()
                n = len(data) // 2
                samples = struct.unpack(f"<{n}h", data[: n * 2])
                peak = max(abs(s) for s in samples) / 32768.0
                if self.ui_suspended:
                    settled = False
                    tail = 0
                    continue
                if peak >= self._QUIET:
                    tail = self._TAIL_FRAMES
                    settled = False
                elif tail:
                    tail -= 1
                elif settled:
                    continue
                else:
                    peak = 0.0
                    settled = True
                GLib.idle_add(self._dispatch, source_id, peak)
        except (OSError, ValueError):
            pass
        # Final zero so the UI doesn't get stuck on the last value when the
        # subprocess dies (mic unplugged, app closed, etc.)
        GLib.idle_add(self._dispatch, source_id, 0.0)

    def silent_for(self, source_id):
        """Seconds since this meter last received any data, or None.

        None means nothing is being measured, which is deliberately NOT the
        same as measuring nothing. Two cases return it, and conflating either
        with a stalled device would have something act on the silence:

        - no meter is running for that source at all;
        - the meter's own subprocess has died, so its silence says something
          about pw-cat and nothing whatsoever about the hardware.
        """
        last = self._last_data.get(source_id)
        if last is None:
            return None
        proc = self._procs.get(source_id)
        if proc is None or proc.poll() is not None:
            return None
        return time.monotonic() - last

    def _dispatch(self, source_id, peak):
        cb = self._cbs.get(source_id)
        if cb is not None:
            cb(peak)
        return False  # one-shot idle handler
