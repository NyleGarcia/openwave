"""Auto-calibration: measure a microphone, propose gate and compressor.

Two captures off the RAW device node — a silent stretch for the noise
floor, a spoken stretch for the voice — reduced to per-window peak
levels, then turned into settings by rules a broadcast engineer would
recognise: the gate threshold sits safely above the floor but below the
quietest voiced material, the compressor threshold rides a bit under the
loudest. Analysis is pure and unit-tested; only the capture touches the
graph.
"""

import math
import os
import select
import struct
import subprocess
import time

from .mixer import _set_pdeathsig   # same child-dies-with-us rule as the meters

RATE = 48000
WINDOW = 1600           # 800 s16 mono samples — 16.7 ms @ 48 kHz
FLOOR_SECONDS = 3
SPEECH_SECONDS = 5
# How long past its own duration a capture is given before it is called
# stalled. pw-cat delivers in real time, so anything beyond this is a node
# that stopped producing rather than a slow one.
GRACE_SECONDS = 3
# Longest a single read may block, and so the worst-case latency of a cancel.
_POLL_SECONDS = 0.25


class CalibrationError(Exception):
    pass


class CalibrationCancelled(Exception):
    """The caller asked for the capture to stop before it finished."""


def _read_exactly(proc, budget, deadline, cancel):
    """Up to `budget` bytes from `proc`, honouring a deadline and a cancel.

    A plain `read()` on the pipe was the bug this exists to avoid: pw-cat
    neither exits nor delivers when its node goes away mid-capture (an
    unplugged microphone, a suspended device), so the read blocked forever
    — a worker thread parked for good and a modal "Calibrating…" that could
    never be dismissed. Polled instead, so both the clock and the Cancel
    button can end it.
    """
    chunks, got = [], 0
    while got < budget:
        if cancel is not None and cancel():
            raise CalibrationCancelled()
        if time.monotonic() > deadline:
            break
        ready, _, _ = select.select([proc.stdout], [], [], _POLL_SECONDS)
        if not ready:
            continue
        # os.read, not stdout.read: the latter blocks until it has the full
        # count, which is the blocking this loop exists to avoid.
        chunk = os.read(proc.stdout.fileno(), min(65536, budget - got))
        if not chunk:
            break
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def _capture(node_name, seconds, channels, cancel):
    """`seconds` of s16 off one node as raw bytes, transient included."""
    frame = 2 * channels
    budget = RATE * frame * seconds + RATE * frame // 2
    try:
        proc = subprocess.Popen(
            ["pw-cat", "--record", "--target", node_name,
             "--rate", str(RATE), "--channels", str(channels),
             "--format", "s16", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            preexec_fn=_set_pdeathsig,
        )
    except OSError as exc:
        raise CalibrationError(f"could not record {node_name}: {exc}")
    deadline = time.monotonic() + seconds + GRACE_SECONDS
    try:
        return _read_exactly(proc, budget, deadline, cancel)
    finally:
        # Reaped, not merely signalled: an unreaped pw-cat is a zombie for
        # the life of the app, and one left running holds a stream open on
        # the node that health.py then reads as a stalled device.
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        except (OSError, ProcessLookupError):
            pass
        try:
            proc.stdout.close()
        except OSError:
            pass


def capture_raw(node_name, seconds, channels=2, cancel=None):
    """Exactly `seconds` of raw s16 off one node, transient dropped.

    Stereo by default: channel balance is one of the things calibration
    can judge, and a mono device simply delivers two equal channels.

    `cancel`, if given, is polled while reading; when it returns True the
    child is stopped and CalibrationCancelled is raised.
    """
    frame = 2 * channels
    raw = _capture(node_name, seconds, channels, cancel)[RATE * frame // 2:]
    if len(raw) < RATE * frame * seconds // 2:
        raise CalibrationError(
            f"{node_name} delivered almost no audio — is the device stalled?")
    return raw


def _one_pole_energy(samples, cutoff):
    """Mean energy of `samples` low-passed at `cutoff` Hz. Pure python —
    numpy is not a dependency this project has, and a first-order filter
    is plenty for octave-coarse decisions."""
    a = math.exp(-2.0 * math.pi * cutoff / RATE)
    b = 1.0 - a
    y = 0.0
    acc = 0.0
    for s in samples:
        y = b * s + a * y
        acc += y * y
    return acc / max(len(samples), 1)


def metrics_from_raw(raw, channels=2):
    """Everything the rules need, from one capture.

    Level metrics ride the mono mixdown; tone metrics are octave-coarse
    energies from first-order filters; balance compares the channels.
    """
    n = len(raw) // 2
    ints = struct.unpack(f"<{n}h", raw[:n * 2])
    if channels == 2:
        left = ints[0::2]
        right = ints[1::2]
        mono = [(l + r) / 2.0 for l, r in zip(left, right)]
        e_l = sum(v * v for v in left) / max(len(left), 1)
        e_r = sum(v * v for v in right) / max(len(right), 1)
        balance = (min(e_l, e_r) / max(e_l, e_r)) if max(e_l, e_r) else 1.0
    else:
        mono = [float(v) for v in ints]
        balance = 1.0

    peaks = []
    half = WINDOW // 2
    for i in range(0, len(mono) - half, half):
        peak = max(abs(s) for s in mono[i:i + half]) / 32768.0
        peaks.append(20 * math.log10(max(peak, 1e-7)))

    total = sum(v * v for v in mono) / max(len(mono), 1)
    e90 = _one_pole_energy(mono, 90)       # rumble + deepest fundamentals
    e180 = _one_pole_energy(mono, 180)     # ...plus the voice's low octave
    e2k = _one_pole_energy(mono, 2000)

    def db(x, ref):
        return 10 * math.log10(max(x, 1e-9) / max(ref, 1e-9))

    return {
        "peaks_db": peaks,
        "balance": balance,
        "sub_db": db(e90, total),           # how much of it lives below ~90 Hz
        "voice_low_db": db(e180 - e90, total),  # the 90–180 Hz octave
        "tilt_db": db(total - e2k, total),  # energy above ~2 kHz vs everything
    }


def analyze_tone(floor_metrics, speech_metrics):
    """Low cut, high shelf and mono from the tone metrics.

    Every rule bounded and explainable: the low cut never sits on top of
    a deep voice's fundamentals, the shelf only nudges toward a normal
    speech tilt, and mono is suggested only for a lopsided capture.
    """
    fx = {}
    # Deep voice: real energy in the 90–180 octave vetoes the 120 Hz cut.
    deep_voice = speech_metrics["voice_low_db"] > -12.0
    rumbly_floor = floor_metrics["sub_db"] > -6.0
    fx["lowcut"] = 80 if deep_voice else (120 if rumbly_floor else 80)

    # Typical close-mic speech carries its top ~10–20 dB under the body;
    # nudge halfway toward that, clamped so a wild measurement cannot
    # order a wild shelf.
    target = -15.0
    delta = (target - speech_metrics["tilt_db"]) * 0.5
    fx["eq_high"] = float(max(-4.0, min(4.0, round(delta))))

    if speech_metrics["balance"] < 0.05:
        fx["mono"] = True
    return fx


def _percentile(values, pct):
    # Empty in means ordered[-1] — the largest value — silently standing in
    # for a percentile of nothing. Say so instead.
    if not values:
        raise CalibrationError("nothing was measured — is the device stalled?")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * pct / 100))]


def analyze(floor_peaks_db, speech_peaks_db):
    """Turn the two measurements into fx settings, or raise with a reason.

    Voiced windows are those clearly above the floor; without enough of
    them the speech phase heard nothing worth calibrating to, and saying
    so beats emitting a gate threshold computed from silence.
    """
    floor = _percentile(floor_peaks_db, 50)
    voiced = [p for p in speech_peaks_db if p > floor + 10]
    # `0 < 0` is False, so an empty speech capture would otherwise walk
    # straight past the ratio test into a percentile of nothing.
    if not voiced or len(voiced) < len(speech_peaks_db) * 0.1:
        raise CalibrationError(
            "I did not hear speech clearly above the noise floor — "
            "try again closer to the microphone.")
    quiet_voice = _percentile(voiced, 10)
    loud_voice = _percentile(voiced, 90)

    # Gate: above the floor with margin, below the quietest voiced
    # material with more margin — words must always win the argument.
    gate_thresh = max(floor + 8.0, min(quiet_voice - 12.0, -20.0))
    gate_thresh = max(-70.0, min(-20.0, gate_thresh))

    # Compressor: catch the loud peaks, leave normal speech alone.
    comp_thresh = max(-40.0, min(0.0, loud_voice - 6.0))

    return {
        "measured": {"floor_db": round(floor, 1),
                     "quiet_voice_db": round(quiet_voice, 1),
                     "loud_voice_db": round(loud_voice, 1)},
        "fx": {"gate": True, "gate_thresh": round(gate_thresh, 1),
               "comp": True, "comp_thresh": round(comp_thresh, 1),
               "comp_ratio": 3.0},
    }
