"""OpenWave with no Elgato hardware attached at all.

A Wave XLR is the reason most people install this, but it is not a
requirement: with a headset microphone and nothing else, OpenWave is still a
mixer -- sources, mixes, per-mix outputs and application matching all work
without a single vendor USB transfer. Nothing here may quietly assume a Wave
is present, because the failure that assumption produces is silence rather
than an error.
"""

import unittest

from wavexlr import mixer as mixer_mod
from wavexlr import sources as sources_module
from .support import bare_mixer, temp_config

# A graph with a SteelSeries headset and OpenWave's own nodes -- and no Elgato
# card of any kind.
ARCTIS_SOURCES = [
    ["48", "alsa_input.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.mono-fallback"],
    ["31", "openwave_personal_mix.monitor"],
]
ARCTIS_SINKS = [
    ["49", "alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.iec958-stereo"],
    ["31", "openwave_personal_mix"],
]


class NoWaveDevice(unittest.TestCase):
    def setUp(self):
        self._real = mixer_mod._pactl_short
        mixer_mod._pactl_short = lambda kind: (
            ARCTIS_SOURCES if kind == "sources" else
            ARCTIS_SINKS if kind == "sinks" else [])

    def tearDown(self):
        mixer_mod._pactl_short = self._real

    def test_the_lookup_reports_nothing_rather_than_guessing(self):
        """A headset is not a Wave, however much it looks like one to a
        substring match: pairing the gain slider to it would drive the wrong
        device with nothing on screen to say so."""
        self.assertEqual(mixer_mod.find_wave_xlr_alsa(), (None, None))

    def test_a_headset_is_not_mistaken_for_a_wave_card(self):
        for node in (s[1] for s in ARCTIS_SOURCES + ARCTIS_SINKS):
            self.assertFalse(mixer_mod._is_wave_card(node), node)

    def test_a_wave_is_still_found_when_one_is_present(self):
        """The negative tests above would also pass if matching were broken
        outright, so the positive case is asserted alongside them."""
        dock = ("alsa_input.usb-Elgato_Systems_Elgato_XLR_Dock_A8A9-00"
                ".mono-fallback")
        dock_out = ("alsa_output.usb-Elgato_Systems_Elgato_XLR_Dock_A8A9-00"
                    ".analog-stereo")
        mixer_mod._pactl_short = lambda kind: (
            ARCTIS_SOURCES + [["50", dock]] if kind == "sources" else
            ARCTIS_SINKS + [["51", dock_out]] if kind == "sinks" else [])
        self.assertEqual(mixer_mod.find_wave_xlr_alsa(), (dock, dock_out))


class RoutingWithoutAWave(unittest.TestCase):
    """The matrix is the product; the Wave is one possible row in it."""

    ARCTIS = "alsa_input.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.mono-fallback"

    def _mixer(self):
        mx = bare_mixer()
        mx._sources = {
            "arctis": {"id": "arctis", "kind": "device", "name": "Arctis",
                       "node_name": self.ARCTIS, "level": 1.0,
                       "muted": False},
            "music": {"id": "music", "name": "Music",
                      "match_app_names": ["Spotify"], "level": 1.0,
                      "muted": False},
        }
        mx._mixes = {
            "personal": {"id": "personal", "name": "Personal Mix",
                         "sink": "openwave_personal_mix"},
            "chat": {"id": "chat", "name": "Chat Mix",
                     "sink": "openwave_chat_mix"},
        }
        return mx

    def test_mic_and_hp_are_simply_absent(self):
        mx = self._mixer()
        self.assertIsNone(mx.mic)
        self.assertIsNone(mx.hp)

    def test_a_headset_microphone_still_reaches_every_mix(self):
        """The capture path is a loopback from the node to the mix sink, and
        it does not care which vendor made the node."""
        mx = self._mixer()
        mx.set_cell("arctis", "personal", 0.8, False)
        mx.set_cell("arctis", "chat", 0.5, False)
        self.assertAlmostEqual(mx.get_cell("arctis", "personal")["volume"], 0.8)
        self.assertAlmostEqual(mx.get_cell("arctis", "chat")["volume"], 0.5)

    def test_application_sources_are_unaffected(self):
        mx = self._mixer()
        mx.set_cell("music", "personal", 0.55, False)
        self.assertAlmostEqual(mx.get_cell("music", "personal")["volume"], 0.55)

    def test_a_trim_still_composes_with_a_send(self):
        """Trim x send is the whole level model, and it is computed from the
        source record -- there is no hardware in it."""
        mx = self._mixer()
        mx.set_cell("arctis", "personal", 0.5, False)
        mx._sources["arctis"]["level"] = 0.5
        self.assertAlmostEqual(mx._source_gain("arctis"), 0.5)

    def test_a_muted_source_contributes_nothing(self):
        mx = self._mixer()
        mx._sources["arctis"]["muted"] = True
        self.assertEqual(mx._source_gain("arctis"), 0.0)


class DiscoveryWithoutElgato(unittest.TestCase):
    def test_only_elgato_vendor_ids_are_auto_added(self):
        """Auto-discovery is keyed on the USB vendor id, not on a name, so a
        headset never acquires a row it cannot be removed from."""
        self.assertEqual(mixer_mod.ELGATO_VID, 0x0FD9)
        self.assertNotEqual(0x1038, mixer_mod.ELGATO_VID)  # SteelSeries

    def test_a_headset_row_stays_removable(self):
        """Elgato rows are protected because deleting one would leave the
        device unreachable; nothing else should inherit that."""
        arctis = {"id": "arctis", "kind": "device", "name": "Arctis"}
        self.assertFalse(sources_module.is_protected(arctis))
        dock = {"id": "dock", "kind": "device", "name": "XLR Dock",
                "protected": True}
        self.assertTrue(sources_module.is_protected(dock))

    def test_the_default_sources_need_no_hardware(self):
        """System, Game, Music, Browser and Voice are application matches, so
        a fresh install with no Elgato device is usable immediately."""
        defaults = sources_module.DEFAULT_SOURCES
        self.assertTrue(defaults)
        for source in defaults.values():
            self.assertNotIn("node_name", source)

    def test_a_fresh_install_seeds_those_sources_with_no_device(self):
        with temp_config():
            seeded = sources_module.load_seeded()
            self.assertEqual(set(seeded), set(sources_module.DEFAULT_SOURCES))


if __name__ == "__main__":
    unittest.main()
