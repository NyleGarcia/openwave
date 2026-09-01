"""Watchdogs for the two faults that pass every existing health check.

Both were observed on real hardware on the same day, and both are
invisible to the capture keepalive: bytes flow, nothing is muted, every
node reports "running" — and the audio is still wrong.

Glitchy capture: the Wave's ALSA node accumulates xruns continuously
(~23/s when observed) because the graph clock it follows can't be
tracked — concretely, a wireless headset dongle winning the driver
election on an object-id tiebreak and feeding the Wave's follower DLL a
jittery clock it resynced against forever. The audible result is a
robotic, granular microphone. The wireplumber conf now pins the Wave's
`priority.driver` above the default so it wins the election, but the
watchdog stays: any future source of sustained xruns sounds the same,
and the counter is the only place it shows.

Stalled output: a sink's PipeWire node runs, the graph delivers real
samples to it, volume and mute read fine — and the ALSA device behind
it consumes nothing, so the hardware plays silence. Observed after a
WirePlumber restart recreated the device nodes. The graph cannot see
this at all; only the kernel's hw_ptr shows it, by not moving. The
remedy is to close and reopen the PCM, which suspending and resuming
the sink does.

Detection is separated from the acting on it, StallWatch-style, so the
decisions can be tested without a sound card: every input is a name, a
number, or a bool.

xrun counts come from `pw-top`, because that is the only place PipeWire
exports the profiler's per-node xrun counter (`pw-dump` carries no such
field). Three iterations are requested because the count is verified to
need them: the first two print placeholder zeros while the profiler
warms up — measured directly, 2 iterations read 0 where 3 read the true
count — and the parser takes the last value printed per node.
"""

import logging
import os
import re
import subprocess
import threading
import time

from . import recovery
from .audio import _pw_dump

log = logging.getLogger("wavexlr.health")

# Seconds between health checks. Sampling pw-top blocks for about a
# second of profiler iterations, so this is deliberately much slower
# than the keepalive watchdog's 1 s tick.
CHECK_INTERVAL = 10.0

# xruns per check window that count as glitching. A healthy node logs a
# handful at stream start and then stays flat; the robotic-mic fault ran
# at ~230 per window. A wireless follower absorbing its own jitter was
# observed bursting ~23 in one window, and must not trip this.
GLITCH_XRUNS_PER_CHECK = 50

# Consecutive glitchy windows before acting. One window can be a system
# hiccup (game launch, compile); two in a row is a state, not an event.
GLITCH_CONFIRM_CHECKS = 2

# Rate limits for both remedies, matching recovery.py's reasoning: a
# failed recovery must not become a loop, and a device that stays broken
# through two attempts should be left alone to be noticed.
COOLDOWN_SECONDS = 60.0
MAX_ATTEMPTS = 2


# --- sampling seams (each one shell or /proc; patched out in tests) ---

def sample_xruns():
    """Per-node cumulative xrun counts, from pw-top's profiler view.

    Returns {node.name: xruns} for every node pw-top prints. The counter
    is cumulative for the node's lifetime and resets when the node is
    recreated; GlitchWatch handles the reset.
    """
    try:
        r = subprocess.run(
            ["pw-top", "--batch-mode", "--iterations", "3"],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return {}
    if r.returncode != 0:
        return {}
    return _parse_pw_top(r.stdout)


def _parse_pw_top(text):
    counts = {}
    for line in text.splitlines():
        tokens = line.split()
        # S ID QUANT RATE WAIT BUSY W/Q B/Q ERR [FORMAT...] NAME
        if len(tokens) < 10 or not tokens[8].isdigit():
            continue
        # Later iterations overwrite earlier ones: last wins.
        counts[tokens[-1]] = int(tokens[8])
    return counts


def read_playback_status(card, device, subdevice):
    """(hw_ptr, state) of one ALSA playback substream, or (None, None).

    hw_ptr is the DMA position the hardware has consumed up to. A
    running playback stream always advances it — even one playing pure
    silence — which is what makes a static pointer a hardware verdict
    rather than a signal-level one.
    """
    path = (f"/proc/asound/card{card}/pcm{device}p/"
            f"sub{subdevice}/status")
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return None, None
    ptr = re.search(r"^hw_ptr\s*:\s*(\d+)", text, re.MULTILINE)
    state = re.search(r"^state:\s*(\S+)", text, re.MULTILINE)
    return (int(ptr.group(1)) if ptr else None,
            state.group(1) if state else None)


def recycle_sink(sink_name):
    """Close and reopen a sink's PCM by suspending and resuming it."""
    for flag in ("1", "0"):
        try:
            r = subprocess.run(
                ["pactl", "suspend-sink", sink_name, flag],
                capture_output=True, timeout=5,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return False
        if r.returncode != 0:
            return False
        if flag == "1":
            time.sleep(1.0)
    return True


def snapshot_graph():
    """One pw-dump distilled to what the health checks need.

    Returns (wave_captures, watched_sinks) where wave_captures is the
    list of Wave capture node names and watched_sinks maps each ALSA
    sink some openwave loop-out targets to its
    {running, card, device, subdevice}.
    """
    from .audio import SOURCE_MATCHES
    dump = _pw_dump()
    captures = []
    sinks = {}
    targets = set()
    for obj in dump:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        props = obj.get("info", {}).get("props", {})
        name = props.get("node.name", "")
        if name.startswith(SOURCE_MATCHES):
            captures.append(name)
        if (name.startswith("openwave_loop_out")
                and not name.endswith("_cap")):
            target = props.get("target.object")
            if isinstance(target, str) and target.startswith("alsa_output."):
                targets.add(target)
    for obj in dump:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        info = obj.get("info", {})
        props = info.get("props", {})
        name = props.get("node.name", "")
        if name not in targets:
            continue
        try:
            sinks[name] = {
                "running": info.get("state") == "running",
                "card": int(props["alsa.card"]),
                "device": int(props["alsa.device"]),
                "subdevice": int(props.get("alsa.subdevice", 0)),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return captures, sinks


# --- decisions ---

class GlitchWatch:
    """Decides when a capture node's xrun counter means glitching audio.

    Fed one cumulative count per check window. The counter resets to
    zero when a node is recreated; a shrinking count therefore starts a
    fresh baseline instead of celebrating a negative delta.
    """

    def __init__(self, threshold=GLITCH_XRUNS_PER_CHECK,
                 confirm=GLITCH_CONFIRM_CHECKS,
                 cooldown_seconds=COOLDOWN_SECONDS,
                 max_attempts=MAX_ATTEMPTS):
        self.threshold = threshold
        self.confirm = confirm
        self.cooldown_seconds = cooldown_seconds
        self.max_attempts = max_attempts
        self._prev = {}          # node_name -> last cumulative count
        self._streak = {}        # node_name -> consecutive bad windows
        self._attempts = {}      # node_name -> remedies spent
        self._last_attempt = {}  # node_name -> monotonic time

    def forget(self, node_name):
        for d in (self._prev, self._streak,
                  self._attempts, self._last_attempt):
            d.pop(node_name, None)

    def observe(self, node_name, xruns, now):
        """Account one window; True when that window was glitchy."""
        prev = self._prev.get(node_name)
        self._prev[node_name] = xruns
        if prev is None or xruns < prev:
            # First sight, or the node was recreated: baseline only.
            self._streak[node_name] = 0
            return False
        if xruns - prev >= self.threshold:
            self._streak[node_name] = self._streak.get(node_name, 0) + 1
            return True
        # A clean window after a remedy is the recovery signal: the
        # budget refills for the next incident rather than staying
        # spent forever.
        self._streak[node_name] = 0
        self._attempts.pop(node_name, None)
        return False

    def glitching(self, node_name):
        return self._streak.get(node_name, 0) >= self.confirm

    def should_recover(self, node_name, now):
        if not self.glitching(node_name):
            return False
        if self._attempts.get(node_name, 0) >= self.max_attempts:
            return False
        last = self._last_attempt.get(node_name)
        if last is not None and now - last < self.cooldown_seconds:
            return False
        return True

    def record_attempt(self, node_name, now):
        self._attempts[node_name] = self._attempts.get(node_name, 0) + 1
        self._last_attempt[node_name] = now


class SinkStallWatch:
    """Decides when a running sink's hardware has stopped consuming.

    Fed (running, hw_ptr, alsa_state) per check window. A stall is a
    pointer that did not move between two windows while the node claims
    to be running, or the kernel reporting the stream in XRUN. The first
    observation of a sink only baselines the pointer — a sink that just
    started gets a full window before being judged.
    """

    def __init__(self, cooldown_seconds=COOLDOWN_SECONDS,
                 max_attempts=MAX_ATTEMPTS):
        self.cooldown_seconds = cooldown_seconds
        self.max_attempts = max_attempts
        self._prev_ptr = {}      # sink_name -> last hw_ptr
        self._stalled = {}       # sink_name -> bool
        self._attempts = {}      # sink_name -> remedies spent
        self._last_attempt = {}  # sink_name -> monotonic time

    def forget(self, sink_name):
        for d in (self._prev_ptr, self._stalled,
                  self._attempts, self._last_attempt):
            d.pop(sink_name, None)

    def observe(self, sink_name, running, hw_ptr, alsa_state, now):
        """Account one window; True when the sink is stalled."""
        prev = self._prev_ptr.get(sink_name)
        self._prev_ptr[sink_name] = hw_ptr
        if not running or hw_ptr is None:
            # Idle and suspended sinks legitimately hold still, and a
            # sink whose /proc entry vanished is not ours to judge.
            self._stalled[sink_name] = False
            self._prev_ptr.pop(sink_name, None)
            return False
        if alsa_state == "XRUN":
            self._stalled[sink_name] = True
            return True
        if prev is None:
            self._stalled[sink_name] = False
            return False
        stalled = hw_ptr == prev
        self._stalled[sink_name] = stalled
        if not stalled:
            self._attempts.pop(sink_name, None)
        return stalled

    def should_recover(self, sink_name, now):
        if not self._stalled.get(sink_name):
            return False
        if self._attempts.get(sink_name, 0) >= self.max_attempts:
            return False
        last = self._last_attempt.get(sink_name)
        if last is not None and now - last < self.cooldown_seconds:
            return False
        return True

    def record_attempt(self, sink_name, now):
        self._attempts[sink_name] = self._attempts.get(sink_name, 0) + 1
        self._last_attempt[sink_name] = now
        # The recycle itself resets the pointer; don't let the next
        # window compare against a pre-recycle value.
        self._prev_ptr.pop(sink_name, None)


# --- orchestration ---

class HealthMonitor:
    """Runs both watchdogs on a slow loop; remedies are rate-limited.

    The glitch remedy is recovery.cycle_card — close and reopen the
    device — because the fault lives at the ALSA/clock layer where
    restarting a stream changes nothing. The stall remedy is a sink
    suspend/resume, verified on hardware to restart a wedged PCM.
    """

    def __init__(self):
        self._running = False
        self._thread = None
        self.glitch = GlitchWatch()
        self.stall = SinkStallWatch()
        self._known_captures = set()
        self._known_sinks = set()

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def check_once(self, now=None):
        """One pass over both watchdogs; separate so tests can drive it."""
        now = time.monotonic() if now is None else now
        captures, sinks = snapshot_graph()

        # A node that went away starts clean when it comes back —
        # replugging is itself part of several failure stories, and must
        # not inherit a spent remedy budget or a stale counter baseline.
        for gone in self._known_captures - set(captures):
            self.glitch.forget(gone)
        for gone in self._known_sinks - set(sinks):
            self.stall.forget(gone)
        self._known_captures = set(captures)
        self._known_sinks = set(sinks)

        if captures:
            counts = sample_xruns()
            for name in captures:
                if name not in counts:
                    continue
                if not self.glitch.observe(name, counts[name], now):
                    continue
                log.warning(
                    "%s accumulated xruns this window — the capture is "
                    "glitching (robotic audio) while every byte-level "
                    "check passes", name)
                if self.glitch.should_recover(name, now):
                    self.glitch.record_attempt(name, now)
                    card = recovery.card_name_for(name)
                    if card and recovery.cycle_card(card):
                        log.warning(
                            "cycled %s to reopen the glitching capture; "
                            "if this recurs, another node is winning the "
                            "graph-driver election over the Wave — check "
                            "priority.driver in the wireplumber conf",
                            card)

        for name, sink in sinks.items():
            ptr, state = read_playback_status(
                sink["card"], sink["device"], sink["subdevice"])
            if not self.stall.observe(name, sink["running"], ptr,
                                      state, now):
                continue
            log.warning(
                "%s claims to be running but its hardware pointer is "
                "not moving — the graph is delivering audio the device "
                "is not playing", name)
            if self.stall.should_recover(name, now):
                self.stall.record_attempt(name, now)
                if recycle_sink(name):
                    log.warning(
                        "suspended and resumed %s to reopen its PCM",
                        name)

    def _run(self):
        while self._running:
            try:
                self.check_once()
            except Exception as e:
                log.error("health monitor error: %s", e)
            time.sleep(CHECK_INTERVAL)
