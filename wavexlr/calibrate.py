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
import struct
import subprocess

RATE = 48000
WINDOW = 1600           # ~33 ms of s16 mono @ 48 kHz
FLOOR_SECONDS = 3
SPEECH_SECONDS = 5


class CalibrationError(Exception):
    pass


def capture_window_peaks_db(node_name, seconds):
    """Per-window peak dBFS from `seconds` of one node, transient skipped."""
    # pw-cat records until killed; the duration is ours to enforce by
    # reading exactly the byte budget and then stopping the child.
    budget = RATE * 2 * seconds + RATE  # + half a second of transient
    try:
        proc = subprocess.Popen(
            ["pw-cat", "--record", "--target", node_name,
             "--rate", str(RATE), "--channels", "1", "--format", "s16", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise CalibrationError(f"could not record {node_name}: {exc}")
    chunks, got = [], 0
    try:
        while got < budget:
            chunk = proc.stdout.read(min(65536, budget - got))
            if not chunk:
                break
            chunks.append(chunk)
            got += len(chunk)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    raw = b"".join(chunks)[RATE:]  # drop the connection transient
    peaks = []
    for i in range(0, len(raw) - WINDOW, WINDOW):
        n = WINDOW // 2
        samples = struct.unpack(f"<{n}h", raw[i:i + WINDOW])
        peak = max(abs(s) for s in samples) / 32768.0
        peaks.append(20 * math.log10(max(peak, 1e-7)))
    if len(peaks) < seconds * 10:
        raise CalibrationError(
            f"{node_name} delivered almost no audio — is the device stalled?")
    return peaks


def _percentile(values, pct):
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
    if len(voiced) < len(speech_peaks_db) * 0.1:
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
