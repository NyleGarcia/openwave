"""Calibration analysis: measurements in, sane thresholds out."""

import unittest

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


if __name__ == "__main__":
    unittest.main()
