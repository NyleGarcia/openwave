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


if __name__ == "__main__":
    unittest.main()
