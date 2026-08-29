"""Firmware-to-ALSA conversions for the Wave's own controls."""

import unittest

from wavexlr import device


class GainScaling(unittest.TestCase):
    SCALE = 256          # raw units per dB, measured against the ALSA control

    def test_it_matches_the_measured_mapping(self):
        # Driven on hardware at four points; ALSA counts half-dB steps.
        for db, raw in ((20, 0x1400), (40, 0x2800), (60, 0x3C00), (75, 0x4B00)):
            self.assertEqual(device._fw_gain_to_alsa(raw, self.SCALE),
                             int(db / 0.5), f"{db} dB")

    def test_it_does_not_truncate_above_forty_dB(self):
        # The old constant clamped to 80 steps, which is 40 dB -- correct for
        # the Wave:3 and half of what a Wave XLR can do.
        self.assertEqual(device._fw_gain_to_alsa(75 * self.SCALE, self.SCALE), 150)

    def test_it_never_returns_a_negative_step(self):
        self.assertEqual(device._fw_gain_to_alsa(-1000, self.SCALE), 0)


class HeadphoneScaling(unittest.TestCase):
    SCALE = 256

    def test_zero_dB_is_the_top_of_the_range(self):
        self.assertEqual(device._fw_hp_to_alsa(0, self.SCALE), 120)

    def test_it_saturates_at_the_bottom(self):
        # The driver caps at 0, which is -60 dB; anything below saturates.
        self.assertEqual(device._fw_hp_to_alsa(-100 * self.SCALE, self.SCALE), 0)

    def test_it_round_trips(self):
        for db in (0, -10, -30, -60):
            alsa = device._fw_hp_to_alsa(db * self.SCALE, self.SCALE)
            self.assertAlmostEqual(
                device._alsa_hp_to_fw(alsa, self.SCALE) / self.SCALE, db, places=1)


class ControlRanges(unittest.TestCase):
    def test_an_unreadable_control_uses_the_stated_fallback(self):
        # The range is read from the driver; a card that cannot answer must
        # not silently clamp to a range belonging to another device.
        original = device._amixer
        device._amixer = lambda *a, **k: ""
        device._ALSA_CTL_MAX.clear()
        try:
            self.assertEqual(device._alsa_ctl_max("99", 6, 150), 150)
            self.assertEqual(device._alsa_ctl_max("99", 4, 120), 120)
        finally:
            device._amixer = original
            device._ALSA_CTL_MAX.clear()

    def test_it_parses_and_caches_the_reported_maximum(self):
        calls = []

        def fake(card, *args):
            calls.append(args)
            return "  ; type=INTEGER,access=rw---R--,values=1,min=0,max=150,step=0\n"

        original = device._amixer
        device._amixer = fake
        device._ALSA_CTL_MAX.clear()
        try:
            self.assertEqual(device._alsa_ctl_max("3", 6, 999), 150)
            self.assertEqual(device._alsa_ctl_max("3", 6, 999), 150)
            self.assertEqual(len(calls), 1, "the range should be read once")
        finally:
            device._amixer = original
            device._ALSA_CTL_MAX.clear()


if __name__ == "__main__":
    unittest.main()
