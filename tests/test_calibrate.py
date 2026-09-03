"""Calibration analysis: measurements in, sane thresholds out."""

import os
import threading
import time
import unittest
from unittest import mock

from wavexlr import calibrate


def windows(db, count=100):
    return [float(db)] * count


class Analyze(unittest.TestCase):
    def test_typical_setup(self):
        """Floor -62, voice -30ish: gate lands between them, comp above."""
        speech = windows(-30, 80) + windows(-60, 20)  # pauses included
        r = calibrate.analyze(windows(-62), speech)
        f = r["fx"]
        self.assertTrue(f["gate"] and f["comp"])
        self.assertGreater(f["gate_thresh"], -62 + 7)
        self.assertLess(f["gate_thresh"], -30, "gate must sit below voice")
        self.assertAlmostEqual(f["comp_thresh"], -36, delta=3)

    def test_quiet_voice_still_wins_over_margin(self):
        """A voice barely above the floor: the gate hugs the floor rather
        than eating words."""
        r = calibrate.analyze(windows(-60), windows(-45, 90) + windows(-60, 10))
        self.assertLessEqual(r["fx"]["gate_thresh"], -52,
                             "quiet-voice margin must dominate")

    def test_loud_floor_clamps_into_range(self):
        r = calibrate.analyze(windows(-25), windows(-8))
        self.assertGreaterEqual(r["fx"]["gate_thresh"], -70)
        self.assertLessEqual(r["fx"]["gate_thresh"], -20)
        self.assertLessEqual(r["fx"]["comp_thresh"], 0)

    def test_no_speech_is_an_explanation_not_a_threshold(self):
        with self.assertRaisesRegex(calibrate.CalibrationError, "speech"):
            calibrate.analyze(windows(-62), windows(-60))

    def test_measured_levels_are_reported(self):
        r = calibrate.analyze(windows(-62), windows(-28))
        self.assertEqual(r["measured"]["floor_db"], -62.0)
        self.assertEqual(r["measured"]["loud_voice_db"], -28.0)


def _tone_metrics(sub_db=-20, voice_low_db=-20, tilt_db=-15, balance=1.0):
    return {"sub_db": sub_db, "voice_low_db": voice_low_db,
            "tilt_db": tilt_db, "balance": balance, "peaks_db": []}


class AnalyzeTone(unittest.TestCase):
    def test_rumbly_floor_gets_the_higher_cut(self):
        fx = calibrate.analyze_tone(_tone_metrics(sub_db=-3),
                                    _tone_metrics())
        self.assertEqual(fx["lowcut"], 120)

    def test_a_deep_voice_vetoes_the_high_cut(self):
        """Fundamentals in the 90-180 octave: cutting at 120 thins the
        voice, however rumbly the room."""
        fx = calibrate.analyze_tone(_tone_metrics(sub_db=-3),
                                    _tone_metrics(voice_low_db=-6))
        self.assertEqual(fx["lowcut"], 80)

    def test_clean_floor_gets_the_gentle_default(self):
        fx = calibrate.analyze_tone(_tone_metrics(sub_db=-25),
                                    _tone_metrics())
        self.assertEqual(fx["lowcut"], 80)

    def test_dull_speech_earns_a_bounded_shelf_boost(self):
        fx = calibrate.analyze_tone(_tone_metrics(),
                                    _tone_metrics(tilt_db=-30))
        self.assertEqual(fx["eq_high"], 4.0, "clamped, never wild")

    def test_bright_speech_gets_a_trim(self):
        fx = calibrate.analyze_tone(_tone_metrics(),
                                    _tone_metrics(tilt_db=-7))
        self.assertLess(fx["eq_high"], 0)

    def test_normal_tilt_leaves_the_shelf_alone(self):
        fx = calibrate.analyze_tone(_tone_metrics(),
                                    _tone_metrics(tilt_db=-15))
        self.assertEqual(fx["eq_high"], 0.0)

    def test_one_sided_capture_suggests_mono(self):
        fx = calibrate.analyze_tone(_tone_metrics(),
                                    _tone_metrics(balance=0.01))
        self.assertTrue(fx.get("mono"))
        fx = calibrate.analyze_tone(_tone_metrics(),
                                    _tone_metrics(balance=0.8))
        self.assertNotIn("mono", fx)


class Metrics(unittest.TestCase):
    def test_sine_energy_lands_in_its_band(self):
        """A 60 Hz tone reads sub-heavy; a 6 kHz tone reads top-heavy."""
        import math as m
        def stereo(freq, secs=1):
            out = bytearray()
            for i in range(48000 * secs):
                v = int(20000 * m.sin(2 * m.pi * freq * i / 48000))
                out += v.to_bytes(2, "little", signed=True) * 2
            return bytes(out)
        low = calibrate.metrics_from_raw(stereo(60))
        high = calibrate.metrics_from_raw(stereo(6000))
        self.assertGreater(low["sub_db"], -3)
        self.assertLess(high["sub_db"], -20)
        self.assertGreater(high["tilt_db"], low["tilt_db"])

    def test_one_sided_stereo_reads_unbalanced(self):
        frames = (b"\x10\x27" + b"\x00\x00") * 48000  # L loud, R silent
        m = calibrate.metrics_from_raw(frames)
        self.assertLess(m["balance"], 0.05)


class FakeProc:
    """A pw-cat that writes what it is told to, down a real pipe.

    A real pipe rather than a stub file object because the capture loop
    selects on the descriptor — a mock that merely returns bytes would not
    exercise the thing being tested.
    """

    def __init__(self, payload=b"", chunk=8192):
        self._read_fd, self._write_fd = os.pipe()
        self.stdout = os.fdopen(self._read_fd, "rb", buffering=0)
        self.terminated = False
        self.killed = False
        self.reaped = False
        self._writer = threading.Thread(
            target=self._write, args=(payload, chunk), daemon=True)
        self._writer.start()

    def _write(self, payload, chunk):
        try:
            for i in range(0, len(payload), chunk):
                os.write(self._write_fd, payload[i:i + chunk])
        except OSError:
            pass
        # Deliberately left open: a stalled pw-cat neither delivers nor
        # exits, which is exactly the case the deadline exists for.

    def terminate(self):
        self.terminated = True
        try:
            os.close(self._write_fd)
        except OSError:
            pass

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.reaped = True
        return 0


class Capture(unittest.TestCase):
    def setUp(self):
        self.procs = []

    def _popen(self, payload=b""):
        def factory(*_a, **_kw):
            proc = FakeProc(payload)
            self.procs.append(proc)
            return proc
        return factory

    def test_a_stalled_node_ends_at_the_deadline(self):
        """No audio, no EOF: the read must give up rather than block forever."""
        with mock.patch("subprocess.Popen", self._popen(b"")), \
                mock.patch.object(calibrate, "GRACE_SECONDS", 0.2):
            started = time.monotonic()
            with self.assertRaisesRegex(calibrate.CalibrationError, "stalled"):
                calibrate.capture_raw("node", 1, channels=1)
            self.assertLess(time.monotonic() - started, 5,
                            "a stalled capture must not hang the worker")
        self.assertTrue(self.procs[0].terminated)
        self.assertTrue(self.procs[0].reaped, "an unreaped pw-cat is a zombie")

    def test_cancel_stops_the_capture_in_flight(self):
        """Cancel is polled during the read, not only between captures."""
        cancelled = threading.Event()
        cancelled.set()
        with mock.patch("subprocess.Popen", self._popen(b"")):
            with self.assertRaises(calibrate.CalibrationCancelled):
                calibrate.capture_raw("node", 5, cancel=cancelled.is_set)
        self.assertTrue(self.procs[0].terminated,
                        "cancelling must stop the child, not abandon it")

    def test_a_full_capture_returns_its_seconds_of_audio(self):
        rate, frame, seconds = calibrate.RATE, 4, 1
        payload = b"\x10\x27\x10\x27" * (rate * (seconds + 1))
        with mock.patch("subprocess.Popen", self._popen(payload)):
            raw = calibrate.capture_raw("node", seconds)
        # The half-second connection transient is dropped, the rest kept.
        self.assertGreaterEqual(len(raw), rate * frame * seconds // 2)
        self.assertEqual(len(raw) % frame, 0)

    def test_a_missing_pw_cat_is_a_calibration_error(self):
        with mock.patch("subprocess.Popen", side_effect=OSError("no pw-cat")):
            with self.assertRaisesRegex(calibrate.CalibrationError, "record"):
                calibrate.capture_raw("node", 1)


class EmptyMeasurements(unittest.TestCase):
    def test_percentile_of_nothing_explains_itself(self):
        """Not IndexError, and not the largest value standing in silently."""
        with self.assertRaises(calibrate.CalibrationError):
            calibrate._percentile([], 50)

    def test_analyze_with_no_speech_windows(self):
        with self.assertRaises(calibrate.CalibrationError):
            calibrate.analyze(windows(-62), [])


if __name__ == "__main__":
    unittest.main()
