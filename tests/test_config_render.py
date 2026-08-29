"""The generated PipeWire config, and the device profiles behind it."""

import re
import unittest

from wavexlr import mixes, profiles, setup


class SpaEscaping(unittest.TestCase):
    def test_a_plain_value_is_quoted(self):
        self.assertEqual(setup._spa_str("OpenWave Music"), '"OpenWave Music"')

    def test_quotes_are_escaped(self):
        # A mix name is typed by the user and reaches both the config and a
        # pw-cli argument; an unescaped quote truncates the property and
        # corrupts every sink defined after it.
        self.assertEqual(setup._spa_str('My "Mix"'), '"My \\"Mix\\""')

    def test_backslashes_are_escaped(self):
        self.assertEqual(setup._spa_str("a\\b"), '"a\\\\b"')


class RenderedConfig(unittest.TestCase):
    def setUp(self):
        self.rendered = setup.render_mixes_conf(mixes.DEFAULT_MIXES)

    def test_it_declares_every_mix(self):
        names = re.findall(r"node\.name\s*=\s*(\S+)", self.rendered)
        self.assertEqual(names, ["openwave_personal_mix", "openwave_chat_mix",
                                 "openwave_record_mix"])

    def test_descriptions_are_separate_from_display_names(self):
        # Renaming a mix in the UI must not rename what PipeWire publishes.
        descs = re.findall(r'node\.description\s*=\s*"([^"]+)"', self.rendered)
        self.assertEqual(descs, ["OpenWave Personal Mix", "OpenWave Chat Mix",
                                 "OpenWave Record Mix"])

    def test_every_sink_lingers_and_exposes_a_post_volume_monitor(self):
        # object.linger keeps the sink alive without its creator;
        # monitor.channel-volumes is what makes a sink's volume affect what is
        # captured from it.
        self.assertEqual(self.rendered.count("object.linger     = true"), 3)
        self.assertEqual(
            self.rendered.count("monitor.channel-volumes = true"), 3)

    def test_it_is_marked_generated(self):
        self.assertIn(setup.GENERATED_MARKER, self.rendered)

    def test_a_hostile_name_cannot_break_the_syntax(self):
        hostile = {"x": {
            "id": "x", "sink": "openwave_mix_x", "name": "n", "subtitle": "",
            "description": 'Evil " } node.name = pwned', "icon_name": "i",
        }}
        line = [ln for ln in setup.render_mixes_conf(hostile).splitlines()
                if "node.description" in ln][0]
        self.assertNotIn("pwned", line.split("=", 1)[0])
        self.assertTrue(line.strip().endswith('"'))


class DeviceProfiles(unittest.TestCase):
    def test_the_mk2_is_registered(self):
        pids = {p.pid for p in profiles.PROFILES}
        self.assertIn(0x00A6, pids)

    def test_the_mk2_clones_the_original_layout(self):
        mk2 = next(p for p in profiles.PROFILES if p.pid == 0x00A6)
        xlr = next(p for p in profiles.PROFILES if p.pid == 0x007D)
        for field in ("off_gain", "off_mute", "off_hp_vol", "off_low_z",
                      "config_len", "windex", "gain_max", "gain_scale"):
            self.assertEqual(getattr(mk2, field), getattr(xlr, field), field)

    def test_the_mk2_keeps_its_own_identity(self):
        mk2 = next(p for p in profiles.PROFILES if p.pid == 0x00A6)
        self.assertEqual(mk2.key, "wave_xlr_mk2")
        self.assertIn("XLR Dock", mk2.card_match)

    def test_gain_is_expressed_in_dB_not_raw_units(self):
        # Measured against the card's ALSA control: 256 raw units per dB.
        for prof in profiles.PROFILES:
            self.assertTrue(prof.gain_scale, f"{prof.display_name} has no scale")
        xlr = next(p for p in profiles.PROFILES if p.pid == 0x007D)
        self.assertEqual(xlr.gain_max / xlr.gain_scale, 80.0)


if __name__ == "__main__":
    unittest.main()


class PhantomPower(unittest.TestCase):
    """48 V phantom, at config byte 6.

    Found by watching the config block while the dial was held on a Wave XLR:
    byte 6 flipped with the 48V LED and nothing else moved. Writing it was then
    confirmed to move the LED, so it is a control and not a status mirror.
    """

    def test_devices_with_an_xlr_input_expose_it(self):
        for pid in (0x007D, 0x00A6):
            prof = next(p for p in profiles.PROFILES if p.pid == pid)
            self.assertTrue(prof.has_phantom, prof.display_name)
            self.assertEqual(prof.off_phantom, 6, prof.display_name)

    def test_a_device_without_an_xlr_input_does_not(self):
        # The Wave:3 is a microphone; there is nothing to power.
        wave3 = next(p for p in profiles.PROFILES if p.pid == 0x0070)
        self.assertFalse(wave3.has_phantom)
        self.assertIsNone(wave3.off_phantom)

    def test_it_does_not_collide_with_another_mapped_field(self):
        # Byte 6 sits between mute (4) and headphone volume (9); a clash would
        # mean toggling phantom silently moved something else.
        prof = next(p for p in profiles.PROFILES if p.pid == 0x00A6)
        others = {prof.off_gain, prof.off_gain + 1, prof.off_mute,
                  prof.off_hp_vol, prof.off_hp_vol + 1, prof.off_vol_select,
                  prof.off_low_z}
        self.assertNotIn(prof.off_phantom, others)
