"""Audio mixer — manages pw-loopback subprocesses for the matrix.

A loopback exists for each non-zero cell in the matrix (mic → mix), plus one
that always routes Personal Mix → Wave XLR headphones so the user hears
anything routed there. Volume + mute per cell are pushed onto the loopback's
playback node via wpctl.

State is persisted to ~/.config/openwave/mixes.json so per-cell levels survive
restarts (the loopbacks themselves do not — they're respawned by start()).
"""

import atexit
import ctypes
import json
import logging
import os
import signal
import re
import subprocess
import threading
import time
from threading import Event, Lock
from . import sources

_log = logging.getLogger(__name__)

# Linux-only: make spawned children receive SIGTERM if our process dies.
# Survives SIGKILL on the parent, hard crashes, anything that skips Python
# cleanup paths. Without this, pw-loopback children leak on unclean exit.
_PR_SET_PDEATHSIG = 1
try:
    _libc = ctypes.CDLL("libc.so.6", use_errno=True)
    _libc.prctl.argtypes = (
        ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
    )
    _libc.prctl.restype = ctypes.c_int
except (OSError, AttributeError):
    _libc = None


ELGATO_VID = 0x0FD9


def _alsa_card_vendor(card_index):
    """USB vendor id behind an ALSA card, or None if it is not a USB card."""
    try:
        with open(f"/proc/asound/card{int(card_index)}/usbid") as f:
            return int(f.read().strip().split(":")[0], 16)
    except (OSError, ValueError, TypeError):
        return None


def friendly_device_name(description):
    """Trim a capture device's description to something worth showing.

    ALSA reports "Elgato XLR Dock Mono"; the vendor and the channel layout are
    noise in a mixer row that already sits under an Elgato heading.
    """
    name = re.sub(r"^Elgato\s+", "", str(description or "").strip())
    name = re.sub(r"\s+(Mono|Stereo|Analog Stereo|Digital Stereo)$", "", name)
    return name or str(description or "")


SOURCE_SINK_PREFIX = "openwave_src_"


def source_sink_name(source_id):
    """The intake sink an application source's streams are moved onto."""
    return f"{SOURCE_SINK_PREFIX}{source_id}"


def _move_stream(serial, sink_name):
    """Move a stream onto a sink. `serial` is PulseAudio's index for it."""
    try:
        subprocess.run(
            ["pactl", "move-sink-input", str(serial), sink_name],
            capture_output=True, text=True, timeout=3,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        pass


def _is_output_key(key):
    """True for a mix's output loopback, which outlives this process."""
    return isinstance(key, tuple) and len(key) == 2 and key[0] == "output"


def _set_pdeathsig():
    if _libc is not None:
        _libc.prctl(_PR_SET_PDEATHSIG, int(signal.SIGTERM), 0, 0, 0)

CONFIG_PATH = os.path.expanduser("~/.config/openwave/mixes.json")


# Reserved key in mixes.json holding the Personal Mix's output device. Cell
# keys are always "<source>.<mix>", so a bare word cannot collide with one.
# Per-mix output devices live under a nested reserved key. Cell keys are
# always "<source>.<mix>", so a dot-free word cannot collide with one.
OUTPUTS_STATE_KEY = "outputs"
# Superseded scalar holding the Personal Mix's output. Still written for one
# release so an older build reading this file keeps working.
LEGACY_OUTPUT_KEY = "output"
OUTPUT_AUTO = "auto"
OUTPUT_NONE = "none"
# The mix seeded as "what you hear" monitors by default; anything else stays
# silent until asked, which is correct for a mix that only gets captured.
_MONITORING_MIX_ID = "personal"


def _pactl_short(kind):
    try:
        r = subprocess.run(
            ["pactl", "list", "short", kind],
            capture_output=True, text=True, timeout=3,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    return [line.split("\t") for line in r.stdout.splitlines() if line.strip()]


# ALSA node-name fragments that identify a Wave device. The MK.2 enumerates as
# "Elgato XLR Dock" rather than "Elgato Wave ...", so matching only the latter
# misses it entirely and leaves both mic and hp unresolved.
CARD_NAME_TOKENS = ("Elgato_Wave_", "Elgato_XLR_Dock")


def _is_wave_card(node_name):
    return any(token in node_name for token in CARD_NAME_TOKENS)


def _node_device_stem(node_name):
    """The device-identifying middle of an ALSA node name.

    alsa_input.usb-Elgato_Systems_Elgato_XLR_Dock_A8A9A40411NOP9-00.mono-fallback
      -> usb-Elgato_Systems_Elgato_XLR_Dock_A8A9A40411NOP9-00

    It carries the serial, so it distinguishes two devices of the same model.
    """
    body = node_name.split(".", 1)[-1]
    return body.rsplit(".", 1)[0] if "." in body else body


def find_wave_xlr_alsa():
    """Return (mic_node_name, hp_node_name) for ONE Wave device.

    Both halves must come from the same physical device. Picking the first
    matching capture node and the first matching sink independently paired the
    microphone of one device with the headphone output of another as soon as
    two were connected -- so the gain slider drove one box and the headphone
    slider another, with nothing to say so.
    """
    captures = [p[1] for p in _pactl_short("sources")
                if len(p) > 1 and p[1].startswith("alsa_input")
                and _is_wave_card(p[1])]
    sinks = {_node_device_stem(p[1]): p[1] for p in _pactl_short("sinks")
             if len(p) > 1 and p[1].startswith("alsa_output")
             and _is_wave_card(p[1])}

    # Prefer a device that offers both, so the two controls agree.
    for capture in captures:
        hp = sinks.get(_node_device_stem(capture))
        if hp:
            return capture, hp

    # Otherwise take what exists: a card set to an input-only profile has a
    # microphone and no output, which is a normal configuration.
    return (captures[0] if captures else None,
            next(iter(sinks.values()), None) if not captures else None)


def _node_id_by_name(name, retries=20):
    """Look up a PipeWire node's global id by node.name, polling briefly so
    we don't race a just-spawned pw-loopback. Returns None if not found."""
    for _ in range(retries):
        try:
            r = subprocess.run(
                ["pw-cli", "ls", "Node"],
                capture_output=True, text=True, timeout=3,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return None
        current_id = None
        for raw in r.stdout.splitlines():
            line = raw.strip()
            if line.startswith("id "):
                try:
                    current_id = line.split()[1].rstrip(",")
                except (IndexError, ValueError):
                    current_id = None
            elif current_id and line == f'node.name = "{name}"':
                return current_id
        time.sleep(0.05)
    return None


def _wpctl(*args):
    try:
        subprocess.run(
            ["wpctl", *args],
            capture_output=True, text=True, timeout=3,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        pass


def _ports(direction_flag, node_name):
    """Return the list of `node:port` strings for one direction of a node.

    direction_flag is '-i' (inputs) or '-o' (outputs). Filters pw-link's
    global output to ports whose node.name equals `node_name`.
    """
    try:
        r = subprocess.run(
            ["pw-link", direction_flag],
            capture_output=True, text=True, timeout=3,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    prefix = f"{node_name}:"
    return [line.strip() for line in r.stdout.splitlines() if line.strip().startswith(prefix)]


def list_output_sinks():
    """Return [{name, description}, ...] of sinks the Personal Mix may feed.

    Only sinks backed by a real device are eligible. Virtual sinks are
    excluded because routing the mix into one risks a feedback loop, and not
    only via our own mix sinks: a user's per-application virtual sinks
    typically feed *into* the Personal Mix, so selecting one would close a
    cycle. A hardware sink is a terminus and cannot. `device.id` is the
    discriminator — null sinks and loopback sinks do not carry one.
    """
    import json as _json
    try:
        r = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return []
        objects = _json.loads(r.stdout)
    except (FileNotFoundError, subprocess.SubprocessError, _json.JSONDecodeError):
        return []

    out = []
    for obj in objects:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        props = (obj.get("info") or {}).get("props") or {}
        if props.get("media.class") != "Audio/Sink":
            continue
        if props.get("device.id") is None:
            continue
        name = props.get("node.name", "")
        if not name:
            continue
        try:
            priority = int(props.get("priority.session", 0))
        except (TypeError, ValueError):
            priority = 0
        out.append({
            "name": name,
            "description": props.get("node.description") or name,
            "priority": priority,
        })
    out.sort(key=lambda sink: sink["description"].lower())
    return out


def list_capture_sources():
    """Return [{name, description, priority}, ...] of hardware capture devices.

    The mirror of list_output_sinks, using the same discriminator for the same
    reason: `device.id` is non-null only on a node backed by a real device, so
    one test separates a headset microphone from every virtual Audio/Source —
    our own mix sources (openwave_*_mix_source) and any null-sink source the
    user has configured. Verified against pw-dump on a machine carrying an
    Elgato XLR Dock, a SteelSeries Arctis Nova Pro and a generic USB codec:
    the three hardware inputs each carry a device.id, the three openwave
    virtual sources carry none.

    Monitor sources are excluded for free. A sink's monitor is a set of ports
    on the Audio/Sink node, not a node of its own, so it never appears here as
    an Audio/Source at all — only pactl synthesises the "<sink>.monitor"
    names. The name guards below are belt and braces against a future PipeWire
    that publishes them as nodes. Keeping monitors out matters for the reason
    list_output_sinks keeps virtual sinks out: a mix sink's monitor fed back
    into that mix is a feedback loop.
    """
    import json as _json
    try:
        r = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return []
        objects = _json.loads(r.stdout)
    except (FileNotFoundError, subprocess.SubprocessError, _json.JSONDecodeError):
        return []

    out = []
    for obj in objects:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        props = (obj.get("info") or {}).get("props") or {}
        if props.get("media.class") != "Audio/Source":
            continue
        if props.get("device.id") is None:
            continue
        name = props.get("node.name", "")
        if not name or name.startswith("openwave_") or name.endswith(".monitor"):
            continue
        try:
            priority = int(props.get("priority.session", 0))
        except (TypeError, ValueError):
            priority = 0
        description = props.get("node.description") or name
        out.append({
            "name": name,
            "description": description,
            "priority": priority,
            # Trimmed for display, plus the vendor behind the card so an
            # Elgato input can be recognised without matching on strings.
            "short_name": friendly_device_name(description),
            "vendor_id": _alsa_card_vendor(props.get("alsa.card")),
        })
    out.sort(key=lambda source: source["description"].lower())
    return out

def _default_sink_name():
    try:
        r = subprocess.run(
            ["pactl", "get-default-sink"], capture_output=True, text=True, timeout=3,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None


def default_sink_name():
    """The system default sink's node.name, or None.

    Public wrapper so a caller resolving several mixes at once can pay for the
    `pactl get-default-sink` call once and hand it to resolve_output, instead
    of resolve_output re-running it per mix.
    """
    return _default_sink_name()


def list_audio_streams():
    """Return [{id, app_name, media_name, node_name}, ...] for active output streams."""
    import json as _json
    try:
        r = subprocess.run(
            ["pw-dump"], capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return []
        objects = _json.loads(r.stdout)
    except (FileNotFoundError, subprocess.SubprocessError, _json.JSONDecodeError):
        return []

    out = []
    for obj in objects:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        props = (obj.get("info") or {}).get("props") or {}
        if props.get("media.class") != "Stream/Output/Audio":
            continue
        app = props.get("application.name") or props.get("node.name") or "Unknown"
        node_name = props.get("node.name", "")
        # Skip our own loopbacks, and anyone else's. A loopback's playback node
        # is a Stream/Output like any other, so "playback.game_output" was
        # offered in the Add Source picker as though it were an application --
        # binding one captures whatever is routed through that channel rather
        # than a program, which is never what the picker appears to promise.
        if node_name.startswith("openwave_") or node_name.startswith("playback."):
            continue
        # A real application publishes a process binary; a virtual node does not.
        if not props.get("application.process.binary") and "." in node_name:
            continue
        out.append({
            "id": obj["id"],
            # PulseAudio addresses a stream by object.serial, and pactl is the
            # only thing that reliably moves one (pw-metadata target.object was
            # measured not to).
            "serial": props.get("object.serial"),
            "app_name": app,
            "media_name": props.get("media.name", ""),
            "node_name": node_name,
            "binary": props.get("application.process.binary", ""),
        })
    return out

# ----- application matching -------------------------------------------------
# One definition of "does this stream belong to this source", shared by the
# routing path (Mixer._reconcile_app_cell) and the metering path
# (app._refresh_app_meter). The comparison used to be written out at both
# sites; if they drift, a row shows a dead level bar while audio is routing,
# or a moving one while nothing is.


def _normalize(value):
    """Case-folded, whitespace-collapsed form used for every name comparison."""
    return " ".join(str(value or "").split()).casefold()


def _stream_identities(stream):
    """The names a stream may legitimately be known by, most specific first.

    application.name comes first because it is what the add-source picker
    offers. node.name and the process binary follow because a hand-typed name
    rarely reproduces application.name byte for byte: Discord's stream is
    application.name "WEBRTC VoiceEngine" with binary "Discord", plenty of apps
    set no application.name at all (list_audio_streams already falls back to
    node.name), and application.process.binary is sometimes an absolute path,
    hence the basename entry.

    Every comparison against these is EXACT equality, never substring or
    prefix. "Chrome" as a substring also matches "Chromium", "Chrome Remote
    Desktop" and "chrome_crashpad_handler", which would silently route another
    process's audio into a live mix that may be feeding OBS or Discord. Case
    and whitespace are the only tolerances.
    """
    binary = str(stream.get("binary") or "")
    return (
        _normalize(stream.get("app_name")),
        _normalize(stream.get("node_name")),
        _normalize(binary),
        _normalize(os.path.basename(binary)),
    )


def _match_rank(source, stream, identities=None):
    """Index of the identity `source` matches on, or None if it matches none.

    The index doubles as a specificity score for claim_streams' tie-break.
    `identities` may be passed in so a caller checking many sources against one
    stream normalizes that stream once.
    """
    from . import sources as _sources
    wanted = {_normalize(name) for name in _sources.bindings(source)}
    wanted.discard("")
    if not wanted:
        return None
    if identities is None:
        identities = _stream_identities(stream)
    for rank, identity in enumerate(identities):
        if identity and identity in wanted:
            return rank
    return None


def stream_matches(source, stream):
    """True if `stream` is one of the streams `source` is bound to."""
    return _match_rank(source, stream) is not None


def claim_streams(sources, streams):
    """Assign each stream to at most one source. {source_id: {stream_id, ...}}.

    Matching alone is not safe to route by. Two sources can match one stream:
    trivially two sources bound to the same application, and now also a source
    bound to application.name "Chromium" beside one bound to the binary
    "chromium". Both would be routed into the same mix as separate loopbacks —
    distinct keys, distinct node names, so nothing errors — and PipeWire sums
    them at the sink. Two sample-aligned copies of one stream is 2x amplitude,
    +6.02 dB, and since each source's fader is pushed onto its own loopback it
    attenuates only its own copy: pulling one source to zero leaves the app
    audible 6 dB down, which reads as a broken fader.

    Giving every stream exactly one owner removes that by construction, in the
    one place that decides what gets spawned, so a hand-edited sources.json
    cannot bypass it. Ownership is deterministic — most specific match wins,
    ties broken on source id — so it cannot flip between polls and thrash the
    loopbacks. Sources that match nothing get an empty set, never a KeyError.
    """
    claims = {source_id: set() for source_id in sources}
    for stream_id, stream in streams.items():
        identities = _stream_identities(stream)
        best_key = None
        best_id = None
        for source_id, source in sources.items():
            rank = _match_rank(source, stream, identities)
            if rank is None:
                continue
            key = (rank, str(source_id))
            if best_key is None or key < best_key:
                best_key, best_id = key, source_id
        if best_id is None:
            # Nothing named it. A catch-all source takes what no other source
            # claimed, so an application whose reported name matches no row
            # still lands somewhere with a fader instead of bypassing the
            # matrix entirely. Only ever a fallback: an explicit name always
            # wins, and a stream is still owned exactly once.
            best_id = next(
                (sid for sid, src in sources.items() if src.get("catch_all")),
                None,
            )
        if best_id is not None:
            claims[best_id].add(stream_id)
    return claims


class Mixer:
    """Manages pw-loopback subprocesses for the matrix's mic row."""

    def __init__(self):
        self._lock = Lock()
        self._procs = {}
        self._state = self._load_state()
        if self._migrate_state():
            self._save_state()
        self._sources = {}
        self._mixes = {}
        self._streams = {}
        # node.name set of the hardware capture devices PipeWire currently
        # has. _reconcile_capture_cell consults it to decide whether a device
        # source can be wired at all. Always *rebound*, never mutated in
        # place, so a worker-thread read always sees one whole snapshot.
        self._live_captures = frozenset()
        # Intake sinks we have created, so tearing one down costs no subprocess
        # when there was never one to tear down.
        self._intakes = set()
        # _do_start ends with a full reconcile. Reconciling before it would
        # route cells into sinks it has not yet created or swept, so
        # set_sources/set_mixes stay silent until it has run once.
        self._started = False
        self.mic, self.hp = find_wave_xlr_alsa()

        # Background worker: every operation that talks to pw-loopback /
        # pw-cli / wpctl runs here so the GTK main thread never blocks on a
        # subprocess. Pending work is a dict keyed by (kind, …) so successive
        # set_cell calls on the same cell collapse to a single reconcile.
        self._pending = {}
        self._pending_lock = Lock()
        self._wake = Event()
        self._worker_running = True
        self._worker = threading.Thread(
            target=self._worker_loop, name="openwave-mixer", daemon=True,
        )
        self._worker.start()

        # Belt-and-suspenders: even if do_shutdown is skipped, the interpreter
        # almost always runs atexit before the process image goes away.
        atexit.register(self._atexit_cleanup)

    # ----- worker thread -----
    def _enqueue(self, key, task):
        """Coalesce a task by key. Latest task for the same key wins."""
        with self._pending_lock:
            self._pending[key] = task
            self._wake.set()

    def _worker_loop(self):
        while self._worker_running:
            self._wake.wait(timeout=1.0)
            while True:
                with self._pending_lock:
                    if not self._pending:
                        self._wake.clear()
                        break
                    key = next(iter(self._pending))
                    task = self._pending.pop(key)
                try:
                    task()
                except Exception:
                    _log.exception("mixer task failed: %s", key)
            if not self._worker_running:
                return

    # ----- persistence -----
    def _load_state(self):
        """Read persisted state. Pure: never writes, since it is what
        produces self._state and writing from here would race its own caller."""
        try:
            with open(CONFIG_PATH) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _migrate_state(self):
        """Fold the legacy scalar output key into the per-mix mapping.

        Returns True if anything changed. Called once from __init__ after
        _load_state, never from inside it.
        """
        outputs = self._state.get(OUTPUTS_STATE_KEY)
        if not isinstance(outputs, dict):
            outputs = {}
        legacy = self._state.get(LEGACY_OUTPUT_KEY)
        changed = False
        if isinstance(legacy, str) and _MONITORING_MIX_ID not in outputs:
            # Only when unset: a per-mix choice is newer than the scalar.
            outputs[_MONITORING_MIX_ID] = legacy
            changed = True
        if changed or OUTPUTS_STATE_KEY not in self._state:
            self._state[OUTPUTS_STATE_KEY] = outputs
            changed = True
        return changed

    def _save_state(self):
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._state, f, indent=2)
        os.replace(tmp, CONFIG_PATH)

    def get_cell(self, source_id, mix_id):
        return self._state.get(
            f"{source_id}.{mix_id}", {"volume": 0.0, "muted": False}
        )

    def cells(self):
        """Per-cell state only; reserved scalar keys are not cells."""
        return {k: v for k, v in self._state.items() if "." in k}

    def _default_output_for(self, mix_id):
        """Only the first mix monitors by default.

        Keying this to the literal id "personal" was safe while the built-in
        mixes could not be removed. They can be now, and deleting that one
        would otherwise leave nothing monitored by default. Insertion order is
        column order, so the first mix is the leftmost one.
        """
        first = next(iter(self._mixes), None) or _MONITORING_MIX_ID
        return OUTPUT_AUTO if mix_id == first else OUTPUT_NONE

    def get_output(self, mix_id):
        """The persisted choice for a mix: a sink name, OUTPUT_AUTO or OUTPUT_NONE."""
        outputs = self._state.get(OUTPUTS_STATE_KEY) or {}
        return outputs.get(mix_id, self._default_output_for(mix_id))

    def resolve_output(self, mix_id, sinks=None, default_sink=None):
        """The sink a mix should feed, or None if it should not be monitored.

        Explicit choice first, then the Wave device's own headphone jack, then
        the system default, then the highest-priority output. Each candidate is
        checked against the live sink list, so an unplugged device or a card
        profile that no longer exposes an output falls through instead of
        leaving the mix with no outlet.

        The default-sink step rarely fires: the monitoring mix is typically
        itself the default sink, and mix sinks are not eligible. The priority
        fallback is what makes OUTPUT_AUTO resolve to something audible on a
        machine whose Wave device has no usable headphone output.

        `sinks` and `default_sink` may be passed in by a caller resolving
        several mixes at once, so the subprocess cost is paid once rather than
        per mix.
        """
        choice = self.get_output(mix_id)
        if choice == OUTPUT_NONE:
            return None

        if sinks is None:
            sinks = list_output_sinks()
        eligible = {sink["name"] for sink in sinks}

        if choice and choice != OUTPUT_AUTO and choice in eligible:
            return choice

        if self.hp and self.hp in eligible:
            return self.hp

        if default_sink is None:
            default_sink = _default_sink_name()
        if default_sink and default_sink in eligible:
            return default_sink

        if sinks:
            return max(sinks, key=lambda sink: sink["priority"])["name"]
        return None

    def set_output(self, mix_id, name):
        """Persist a mix's output choice and respawn its loopback."""
        with self._lock:
            outputs = self._state.get(OUTPUTS_STATE_KEY)
            if not isinstance(outputs, dict):
                outputs = {}
                self._state[OUTPUTS_STATE_KEY] = outputs
            outputs[mix_id] = name or OUTPUT_AUTO
            if mix_id == _MONITORING_MIX_ID:
                # Keep the superseded scalar in step for one release.
                self._state[LEGACY_OUTPUT_KEY] = outputs[mix_id]
            self._save_state()
        self._enqueue(
            ("output", mix_id), lambda mid=mix_id: self._do_retarget_output(mid),
        )

    def streams(self):
        """Snapshot of currently-known PipeWire output streams (id → info)."""
        with self._lock:
            return dict(self._streams)

    # ----- subprocess lifecycle -----
    def _spawn_loopback(self, key, capture_source_name, playback_target,
                        node_name, detach=False, playback_extra="",
                        description=None):
        """Spawn a pw-loopback and *manually* link the capture side to
        `capture_source_name`'s output ports. We disable autoconnect on capture
        because the session manager will otherwise hijack the loopback by
        wiring the default source (the Wave XLR mic) into it whenever
        target.object can't be resolved to a Source node — which is exactly
        the case for null-sink monitors. The link is set up after a brief
        wait so the node has time to register.

        detach=True leaves the child outside this process's lifetime: no
        PR_SET_PDEATHSIG and its own session. That is for the loopbacks that
        carry a mix to hardware, which must keep playing when the window is
        closed -- the default sink is a null sink, so losing them silences the
        whole machine, not just OpenWave. Cell loopbacks stay tied to the
        process: they are mixing state, and are rebuilt on the next start.
        """
        if key in self._procs:
            return
        capture_node_name = f"{node_name}_cap"
        # Both halves are labelled. Unlabelled they show up as
        # "pw-loopback-542152" in every mixer and monitoring tool, which makes
        # OpenWave's plumbing indistinguishable from anyone else's and
        # impossible to filter on.
        label = description or node_name
        ident = f'application.name=OpenWave node.description="{label}" '
        cap_ident = f'application.name=OpenWave node.description="{label} (capture)" '

        try:
            proc = subprocess.Popen(
                [
                    "pw-loopback",
                    "--capture-props="
                    f"node.autoconnect=false node.name={capture_node_name} "
                    + cap_ident +
                    "audio.channels=2 audio.position=[FL,FR]",
                    "--playback-props="
                    + (f"target.object={playback_target} " if playback_target else "")
                    + f"node.name={node_name} "
                    + ("" if "node.description" in playback_extra else ident)
                    + playback_extra +
                    "audio.channels=2 audio.position=[FL,FR]",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=None if detach else _set_pdeathsig,
                start_new_session=detach,
            )
        except (FileNotFoundError, OSError):
            return
        self._procs[key] = proc
        self._link_capture(capture_source_name, capture_node_name)

    @staticmethod
    def _link_capture(source_node_name, capture_node_name, retries=20):
        """Wire each output port of `source_node_name` to a corresponding
        input port of `capture_node_name`. Mono → stereo duplicates."""
        for _ in range(retries):
            src_ports = _ports("-o", source_node_name)
            dst_ports = _ports("-i", capture_node_name)
            if src_ports and dst_ports:
                break
            time.sleep(0.05)
        else:
            return
        for i, dst in enumerate(dst_ports):
            src = src_ports[i % len(src_ports)]
            try:
                subprocess.run(
                    ["pw-link", src, dst],
                    capture_output=True, text=True, timeout=2,
                )
            except (FileNotFoundError, subprocess.SubprocessError):
                return

    def _destroy_loopback(self, key):
        proc = self._procs.pop(key, None)
        if proc is None:
            return
        try:
            proc.terminate()
        except (OSError, ProcessLookupError):
            return
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=1)
            except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
                pass

    def _atexit_cleanup(self):
        """Fast best-effort tear-down on interpreter exit. No locking, no waits.

        Output loopbacks are skipped for the same reason stop() skips them.
        """
        for key, proc in list(self._procs.items()):
            if _is_output_key(key):
                continue
            try:
                proc.terminate()
            except (OSError, ProcessLookupError):
                continue
        self._procs.clear()

    # Below this, the slider snaps to 0 — sub-1% values keep the loopback alive
    # at imperceptible-but-not-silent volume and confuse "I put it back to 0".
    _ZERO_THRESHOLD = 0.01

    # ----- public API (returns immediately; subprocess work runs on worker) -----
    def start(self):
        """Spawn always-on Personal→HP loopback, snapshot streams, restore cells."""
        self._enqueue(("start",), self._do_start)

    def stop(self):
        """Stop the worker and tear down every loopback. Brief block expected."""
        self._worker_running = False
        self._wake.set()
        try:
            self._worker.join(timeout=3)
        except RuntimeError:
            pass
        with self._lock:
            source_ids = list(self._sources)
        for source_id in source_ids:
            # Hand every moved stream back before we go: an intake sink lingers
            # by necessity, so leaving one behind would strand the application
            # in silence until OpenWave next runs.
            self._destroy_source_sink(source_id)
        with self._lock:
            for key in list(self._procs.keys()):
                if _is_output_key(key):
                    # Deliberately left running; _sweep_stale_loopbacks reclaims
                    # it on the next start. Tearing it down here would undo the
                    # detach for every ordinary quit.
                    self._procs.pop(key, None)
                    continue
                self._destroy_loopback(key)

    def set_cell(self, source_id, mix_id, volume, muted):
        """Persist state synchronously; reconcile the cell on the worker."""
        volume = max(0.0, min(1.0, float(volume)))
        if volume < self._ZERO_THRESHOLD:
            volume = 0.0
        with self._lock:
            self._state[f"{source_id}.{mix_id}"] = {
                "volume": volume, "muted": bool(muted),
            }
            self._save_state()
        self._enqueue(
            ("cell", source_id, mix_id),
            lambda sid=source_id, mid=mix_id: self._reconcile_cell(sid, mid),
        )

    def set_sources(self, sources):
        """Update the app-source configuration; reconcile on worker."""
        with self._lock:
            self._sources = dict(sources)
        self._push_reconcile()

    def set_mixes(self, mixes):
        """Update the mix configuration; reconcile on worker."""
        with self._lock:
            self._mixes = dict(mixes)
        self._push_reconcile()

    def _push_reconcile(self):
        """Queue one reconcile pass, coalescing with any already pending.

        set_sources and set_mixes share a key so configuring both at startup
        costs one pass, not two.
        """
        if self._started:
            self._enqueue(("reconcile",), self._reconcile_all)

    def _mix_sink(self, mix_id):
        """The PipeWire sink carrying a mix, or None if it is not defined."""
        return (self._mixes.get(mix_id) or {}).get("sink")

    def remove_source(self, source_id):
        """Forget persisted cells now; tear down loopbacks on worker."""
        with self._lock:
            prefix = f"{source_id}."
            for cell_key in [k for k in self._state if k.startswith(prefix)]:
                del self._state[cell_key]
            self._save_state()
            self._sources.pop(source_id, None)
        self._enqueue(
            ("remove", source_id),
            lambda sid=source_id: self._do_remove_source(sid),
        )

    def remove_mix(self, mix_id):
        """Forget a mix: purge its persisted state now, tear its audio down
        on the worker.

        The sink name is read here, before the definition is dropped, because
        the worker needs it to destroy the live node and _mix_sink() would
        already return None by the time the task runs.
        """
        with self._lock:
            sink = self._mix_sink(mix_id)
            # Cell keys are exactly "<source>.<mix>" — split rather than match a
            # suffix so a source id that happens to end in the mix id survives.
            for cell_key in [
                k for k in self._state
                if "." in k and k.rsplit(".", 1)[1] == mix_id
            ]:
                del self._state[cell_key]
            outputs = self._state.get(OUTPUTS_STATE_KEY)
            if isinstance(outputs, dict):
                outputs.pop(mix_id, None)
            self._save_state()
            self._mixes.pop(mix_id, None)
        self._enqueue(
            ("remove_mix", mix_id),
            lambda mid=mix_id, snk=sink: self._do_remove_mix(mid, snk),
        )

    def poll_streams(self):
        """Refresh the active-stream cache; reconcile on worker if anything moved.

        Returns (added, removed) stream-id sets for the caller's bookkeeping."""
        new = {s["id"]: s for s in list_audio_streams()}
        with self._lock:
            added = set(new) - set(self._streams)
            removed = set(self._streams) - set(new)
            self._streams = new
        if added or removed:
            self._enqueue(("poll",), self._reconcile_all)
        return added, removed
    def live_captures(self):
        """node.name set of the capture devices PipeWire currently has.

        Rebound rather than mutated by the worker, so a read from the GTK
        thread always sees one whole snapshot rather than a set mid-update.
        """
        return self._live_captures

    def _refresh_live_captures(self):
        """Re-snapshot present capture devices. Returns (added, removed) names.

        An empty result is discarded rather than believed. pw-dump failing — a
        timeout, a session manager restarting under us — is indistinguishable
        from "every capture device vanished", and acting on the latter would
        tear down the mic row's loopbacks along with everything else. A machine
        with genuinely no capture hardware has nothing for this snapshot to
        gate (the `not capture_node` guard already covers "no Wave"), so
        keeping the previous value costs nothing and refusing to act on a
        transient failure is the safe side to err on.
        """
        names = frozenset(source["name"] for source in list_capture_sources())
        if not names:
            return set(), set()
        with self._lock:
            previous = self._live_captures
            self._live_captures = names
        return set(names) - set(previous), set(previous) - set(names)

    def poll_capture_devices(self):
        """Refresh the capture-device snapshot; reconcile if anything moved.

        The counterpart to poll_streams for device sources: a headset powering
        off or coming back changes no stream, so without this nothing would
        ever notice. Shares poll_streams' ("poll",) enqueue key, so a tick that
        sees both kinds of change still costs one reconcile pass.

        Returns (added, removed) node-name sets for the caller's bookkeeping.
        """
        added, removed = self._refresh_live_captures()
        if added or removed:
            self._enqueue(("poll",), self._reconcile_all)
        return added, removed

    def request_capture_poll(self):
        """Re-snapshot capture devices on the worker, reconciling if it moved.

        The subprocess belongs off the GTK thread: list_capture_sources runs
        pw-dump with a 5 second timeout, and this is driven from a GLib
        timeout. Shares poll_streams' key so a tick seeing both kinds of
        change still costs one reconcile.
        """
        self._enqueue(("poll",), self._do_poll_capture_devices)

    def _do_poll_capture_devices(self):
        added, removed = self._refresh_live_captures()
        if added or removed:
            self._reconcile_all()

    def capture_device_present(self, node_name):
        """True if `node_name` is a capture device PipeWire currently has.

        Fail-open, deliberately, and identically to the routing gate in
        _reconcile_capture_cell: an empty snapshot means "not yet seeded, or
        pw-dump failed", not "every device vanished". Reading it fail-closed
        here while the gate reads it fail-open made the two disagree -- audio
        routed while the row was drawn as dead.
        """
        if not node_name:
            return False
        live = self._live_captures
        return not live or node_name in live

    # ----- worker-side implementations -----
    def _do_start(self):
        self._sweep_stale_loopbacks()
        self._sweep_orphan_source_sinks()
        self._rescue_default_sink()
        self._respawn_mix_sources()
        self._respawn_all_output_loopbacks()
        with self._lock:
            self._streams = {s["id"]: s for s in list_audio_streams()}
        # Outside the lock above: _refresh_live_captures takes it itself.
        self._refresh_live_captures()
        self._started = True
        self._reconcile_all()

    def _pin_unity(self, node_name):
        """Force a plumbing node to unity gain, unmuted.

        These loopbacks carry a mix to hardware or publish it as a source;
        neither is a user control, and the mix's own volume is what people
        reach for. But WirePlumber remembers a volume per node NAME and
        restores it whenever the node reappears, so a stray zero -- set by
        hand, or by anything walking the graph -- silences that path on every
        launch afterwards, with the routing looking perfectly correct.
        """
        node_id = _node_id_by_name(node_name)
        if node_id is None:
            return
        _wpctl("set-volume", node_id, "1.0")
        _wpctl("set-mute", node_id, "0")

    def _respawn_output_loopback(self, mix_id, sinks=None, default_sink=None):
        """(Re)create one mix's output loopback for its current target."""
        key = ("output", mix_id)
        self._destroy_loopback(key)
        mix_sink = self._mix_sink(mix_id)
        if not mix_sink:
            return
        target = self.resolve_output(mix_id, sinks=sinks, default_sink=default_sink)
        if target is None:
            return
        mix_name = (self._mixes.get(mix_id) or {}).get("name", mix_id)
        node_name = f"openwave_loop_out_{mix_id}"
        self._spawn_loopback(
            key, mix_sink, target, node_name, detach=True,
            description=f"{mix_name} \u2192 output",
        )
        self._pin_unity(node_name)

    def _mix_source_node(self, sink):
        """<sink>_source -- the name the hand-written config used, so an
        application that has already selected it keeps working."""
        return f"{sink}_source"

    def _rescue_default_sink(self):
        """Move the system default off an intake sink if it landed there.

        Intake sinks are internal, and a session manager choosing one as the
        default sends every application into a single source row at that row's
        send level -- audio does not stop, it goes quiet and lands in the wrong
        place, which reads as "I cannot hear anything" with no obvious cause.
        Observed after a PipeWire restart, when the mix sinks were not yet
        present for the election.

        priority.session=0 makes it unlikely; this makes it recoverable.
        """
        default = _default_sink_name()
        if not default or not default.startswith(SOURCE_SINK_PREFIX):
            return
        with self._lock:
            mixes = list(self._mixes.values())
        target = next((m.get("sink") for m in mixes if m.get("sink")), None)
        if target is None:
            return
        try:
            subprocess.run(["pactl", "set-default-sink", target],
                           capture_output=True, timeout=3)
        except (FileNotFoundError, subprocess.SubprocessError):
            return

    def _respawn_mix_sources(self):
        """Publish each mix as an ordinary capture source, and keep it linked.

        A mix's monitor already carries its audio, but voice applications --
        Discord among them -- filter monitor sources out of their input lists
        entirely, so a mix cannot be selected there. A loopback whose playback
        side declares media.class=Audio/Source presents the same audio as a
        microphone, which every application lists.

        The capture side is re-linked on every pass, not once at creation.
        Installing mixes destroys and recreates their sinks, and the new sink
        is a different node: a loopback pinned to the old one keeps running
        against a dead link, so the source exists, is selectable, and is
        silent. Nothing else repairs that, and nothing reports it.

        priority.session is low so these never win the default-source election
        and displace a real microphone.
        """
        with self._lock:
            mixes = dict(self._mixes)
        for mix_id, mix in mixes.items():
            sink = mix.get("sink")
            if not sink:
                continue
            key = ("mixsrc", mix_id)
            node_name = self._mix_source_node(sink)
            if key not in self._procs:
                self._spawn_loopback(
                    key, sink, None, node_name,
                    description=f"{mix.get('name', mix_id)} (capture source)",
                    playback_extra=(
                        "media.class=Audio/Source priority.session=100 "
                        f'node.description="OpenWave {mix.get("name", mix_id)}" '
                    ),
                )
                self._pin_unity(node_name)
            else:
                # Already running: re-assert the link in case its sink was
                # replaced underneath it. pw-link is harmless when the link
                # already exists.
                self._link_capture(sink, f"{node_name}_cap", retries=1)

    def _respawn_all_output_loopbacks(self):
        """Retarget every mix, paying the sink-enumeration cost once."""
        sinks = list_output_sinks()
        default_sink = _default_sink_name()
        with self._lock:
            mix_ids = list(self._mixes)
        for mix_id in mix_ids:
            self._respawn_output_loopback(
                mix_id, sinks=sinks, default_sink=default_sink,
            )

    def _do_retarget_output(self, mix_id):
        self._respawn_output_loopback(mix_id)

    def _do_remove_source(self, source_id):
        # Destroy the intake first: it returns any parked stream to the default
        # sink, so removing a source hands the application back rather than
        # leaving it playing into a sink nothing drains.
        self._destroy_source_sink(source_id)
        with self._lock:
            keys = [
                k for k in self._procs
                if isinstance(k, tuple) and k and k[0] == source_id
            ]
        for k in keys:
            self._destroy_loopback(k)

    def _do_remove_mix(self, mix_id, sink_name):
        """Worker-side: every loopback touching the mix, then the sink itself.

        Order matters. Destroying the sink while loopbacks still feed it leaves
        those pw-loopback children alive and reconnecting against a node that
        no longer exists, so they go first.

        Every proc key is shaped ("output", mix), ("mic", mix) or
        (source, mix, stream) — the mix id is index 1 in all three.
        """
        with self._lock:
            keys = [
                k for k in self._procs
                if isinstance(k, tuple) and len(k) >= 2 and k[1] == mix_id
            ]
        for key in keys:
            self._destroy_loopback(key)
        if sink_name:
            # Deferred: keeps mixer's module-level imports free of setup, which
            # already reaches back into this package the same way.
            from . import setup as setup_module
            setup_module.destroy_mix_sink(sink_name)

    def _sweep_orphan_source_sinks(self):
        """Destroy intake sinks with no source behind them.

        They linger by necessity, so a crash leaves them holding whatever
        application was parked on them -- silent, because nothing drains an
        intake sink but the loopback that died with us. Destroying them here
        returns those streams to the default sink.
        """
        from . import setup
        with self._lock:
            known = {source_sink_name(sid) for sid in self._sources}
        for name in setup.list_sink_names(SOURCE_SINK_PREFIX):
            if name not in known:
                setup.destroy_mix_sink(name)

    @staticmethod
    def _sweep_stale_loopbacks():
        try:
            subprocess.run(
                # Broader than openwave_loop_: mix capture sources are named
                # after their sink, so a narrower pattern would leak one per
                # unclean exit.
                ["pkill", "-f", "pw-loopback.*openwave_"],
                capture_output=True, timeout=2,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return
        time.sleep(0.2)  # give the kernel a beat to reap so we don't race

    # ----- internal -----
    def _reap_dead(self):
        """Drop bookkeeping for loopbacks whose process has already exited.

        Nothing else reconciles self._procs against process reality, so an
        out-of-band death — the child killed, or PipeWire restarted under it —
        leaves a key that permanently blocks respawn, because _spawn_loopback
        returns early on `key in self._procs`. The dead child also stays a
        zombie, since only _destroy_loopback ever wait()s one.
        """
        for key, proc in list(self._procs.items()):
            if proc.poll() is None:
                continue
            try:
                proc.wait(timeout=0)
            except (subprocess.SubprocessError, OSError):
                pass
            self._procs.pop(key, None)

    def _reconcile_all(self):
        self._reap_dead()
        if self._started:
            self._respawn_mix_sources()
        # Snapshot both axes under the lock: set_sources/set_mixes replace
        # these dicts from the GTK thread, and a mutation mid-iteration would
        # raise into _worker_loop's bare except, silently leaving a mix
        # unwired.
        with self._lock:
            # No "mic" pseudo-source: a Wave device's input is an ordinary
            # device source now, so routing it here as well would put the same
            # microphone into every mix twice.
            source_ids = list(self._sources)
            mix_ids = list(self._mixes)
        for source_id in source_ids:
            for mix_id in mix_ids:
                self._reconcile_cell(source_id, mix_id)

    def _reconcile_cell(self, source_id, mix_id):
        state = self._state.get(
            f"{source_id}.{mix_id}", {"volume": 0.0, "muted": False}
        )
        # Read without the lock, exactly as _reconcile_app_cell already does:
        # set_sources rebinds this dict rather than mutating it, so worker code
        # only ever sees a finished one.
        source = self._sources.get(source_id)
        if source is not None and sources.kind(source) == sources.KIND_DEVICE:
            self._reconcile_capture_cell(
                source_id, mix_id, source.get("node_name"),
                state["volume"], state["muted"],
            )
            return
        self._reconcile_app_cell(source_id, mix_id, state["volume"], state["muted"])

    @staticmethod
    def _capture_loopback_name(source_id, mix_id):
        """node.name for a capture→mix loopback.

        The built-in mic keeps its historical name so upgrading does not orphan
        a loopback that is already running under it. Source ids are uuid4 hex
        and mix ids are [a-z0-9_], so "dev_<source>_to_<mix>" can collide
        neither with the mic form (no source id is the literal "mic") nor with
        an app cell's "<source>_<mix>_<stream>" (no source id is the literal
        "dev"). Every form keeps the openwave_loop_ prefix that
        _sweep_stale_loopbacks pkills.
        """
        if source_id == "mic":
            return f"openwave_loop_mic_to_{mix_id}"
        return f"openwave_loop_dev_{source_id}_to_{mix_id}"

    def _reconcile_capture_cell(self, source_id, mix_id, capture_node, volume, muted):
        """Wire one capture *node* into one mix sink at a per-cell level.

        Generalises what used to be _reconcile_mic_cell. A hardware capture
        device is a Source node, precisely like the Wave's own mic, so the only
        things that differ between the built-in mic row and a headset row are
        which node name goes in and what the loopback is called.

        A node PipeWire does not currently have cannot be linked, and spawning
        anyway is worse than doing nothing: pw-loopback starts fine (the
        playback target exists), _link_capture finds no source ports and gives
        up, and the resulting live-but-silent process leaves a key in
        self._procs that blocks forever the respawn that would fix it when the
        device returns. So tear down instead and let the next
        poll_capture_devices pass rebuild it.
        """
        key = (source_id, mix_id)
        node_name = self._capture_loopback_name(source_id, mix_id)
        mix_sink = self._mix_sink(mix_id)
        live = self._live_captures
        absent = bool(live) and capture_node not in live
        if not capture_node or not mix_sink or volume <= 0.0 or absent:
            self._destroy_loopback(key)
            return
        if key not in self._procs:
            with self._lock:
                src_name = (self._sources.get(source_id) or {}).get(
                    "name", source_id)
                mix_name = (self._mixes.get(mix_id) or {}).get("name", mix_id)
            self._spawn_loopback(
                key, capture_node, mix_sink, node_name,
                description=f"{src_name} \u2192 {mix_name}",
            )
        node_id = _node_id_by_name(node_name)
        if node_id is not None:
            # cell fader x source trim: the row slider scales this source
            # everywhere, the cell decides how much of it this mix gets.
            _wpctl("set-volume", node_id,
                   f"{volume * self._source_gain(source_id):.3f}")
            _wpctl("set-mute", node_id, "1" if muted else "0")

    def _reconcile_app_cell(self, source_id, mix_id, volume, muted):
        """Route an application source into one mix.

        The stream is MOVED onto the source's own intake sink, not copied from
        wherever it already plays. Copying left the application still connected
        to its original sink, so when that sink was one of our mixes -- which it
        normally is, the monitoring mix being the system default -- the audio
        arrived twice and this cell's fader could only add a second copy on top
        of the untouched original. Pulling it to zero changed nothing audible.

        With the stream moved, the loopback out of the intake sink is the only
        path, so the fader is authoritative.
        """
        with self._lock:
            sources = dict(self._sources)
            streams = dict(self._streams)
        source = sources.get(source_id)
        if source is None:
            return
        mix_sink = self._mix_sink(mix_id)
        if not mix_sink:
            return

        if not self._source_is_routed(source_id):
            # Nothing carries this source anywhere. Hand back any stream we
            # parked and leave the application on whatever it chose.
            self._destroy_loopback((source_id, mix_id))
            if source_id in self._intakes:
                self._destroy_source_sink(source_id)
            return

        intake = self._ensure_source_sink(source_id, source.get("name", source_id))
        if intake is None:
            return

        for stream_id in claim_streams(sources, streams).get(source_id, set()):
            stream = streams.get(stream_id) or {}
            serial = stream.get("serial")
            if serial is not None:
                _move_stream(serial, intake)

        # One loopback per (source, mix), not per stream: every stream for this
        # source shares the intake sink, so they share the path out of it and
        # one volume applies to all of them.
        key = (source_id, mix_id)
        node_name = f"openwave_loop_{source_id}_{mix_id}"
        if volume <= 0.0:
            self._destroy_loopback(key)
            return
        if key not in self._procs:
            mix_name = (self._mixes.get(mix_id) or {}).get("name", mix_id)
            self._spawn_loopback(
                key, intake, mix_sink, node_name,
                description=f"{source.get('name', source_id)} \u2192 {mix_name}",
            )
        node_id = _node_id_by_name(node_name)
        if node_id is not None:
            # cell fader x source trim: the row slider scales this source
            # everywhere, the cell decides how much of it this mix gets.
            _wpctl("set-volume", node_id,
                   f"{volume * self._source_gain(source_id):.3f}")
            _wpctl("set-mute", node_id, "1" if muted else "0")

    def _source_is_routed(self, source_id):
        """True if any mix carries this source above zero.

        Moving a stream onto an intake sink that nothing drains would mute the
        application outright, so a source routed nowhere is left where it is.
        """
        with self._lock:
            mix_ids = list(self._mixes)
            state = dict(self._state)
        return any(
            (state.get(f"{source_id}.{mix_id}") or {}).get("volume", 0.0) > 0.0
            for mix_id in mix_ids
        )

    def set_source_level(self, source_id, volume, muted):
        """A source's overall level: the volume of its intake sink.

        Applies to that source in every mix at once, ahead of the per-mix
        faders -- a channel trim rather than a send. Persisted on the source
        record so it is restored deterministically rather than depending on
        WirePlumber having remembered the sink.
        """
        with self._lock:
            source = self._sources.get(source_id)
            if source is not None:
                source["level"] = max(0.0, min(1.0, float(volume)))
                source["muted"] = bool(muted)
        self._enqueue(
            ("srclevel", source_id),
            lambda sid=source_id: self._do_apply_source_level(sid),
        )

    def _do_apply_source_level(self, source_id):
        """Re-apply every cell for this source, so the trim takes effect.

        Deliberately NOT the intake sink's own volume. A null sink's monitor
        does not follow it: measured, setting the sink to zero left the monitor
        at full scale, because the pulse layer's flat-volume handling raises the
        stream to compensate. The per-mix loopback volume is the one control
        that demonstrably attenuates, so the trim multiplies into that.
        """
        with self._lock:
            mix_ids = list(self._mixes)
        for mix_id in mix_ids:
            self._reconcile_cell(source_id, mix_id)

    def _source_gain(self, source_id):
        """A source's trim: its level, or 0 while it is muted."""
        with self._lock:
            source = self._sources.get(source_id) or {}
            if source.get("muted"):
                return 0.0
            try:
                return max(0.0, min(1.0, float(source.get("level", 1.0))))
            except (TypeError, ValueError):
                return 1.0

    def _ensure_source_sink(self, source_id, description):
        """Create the source's intake sink if it is not already live."""
        from . import setup
        name = source_sink_name(source_id)
        try:
            setup.create_null_sink(
                name, f"OpenWave: {description}", priority=0,
            )
        except Exception:
            return None
        if source_id not in self._intakes:
            self._intakes.add(source_id)
            # A freshly created sink is at unity and unmuted; push the stored
            # level onto it so the slider means something immediately.
            self._do_apply_source_level(source_id)
        return name

    def _destroy_source_sink(self, source_id):
        """Remove an intake sink, returning any parked stream to the default.

        Measured: destroying the sink reroutes its streams rather than killing
        them, which is what makes moving them safe to undo.
        """
        from . import setup
        setup.destroy_mix_sink(source_sink_name(source_id))
        self._intakes.discard(source_id)
