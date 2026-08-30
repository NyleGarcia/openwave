"""Friendly names for the Add Source picker, without touching the match key.

app_name is what claim_streams() matches on and must stay exact; display_name
exists only so a Java app reads "RuneLite" instead of "ALSA plug-in [java]" in
the picker. The rules are pure functions, tested as such.
"""

import unittest

from wavexlr.mixer import _binary_name, _is_generic
from wavexlr.wmnames import _pick_name


class WhatCountsAsGeneric(unittest.TestCase):
    def test_bridge_names_are_generic(self):
        for name in ("ALSA plug-in [java]", "alsa-playback", "PulseAudio"):
            self.assertTrue(_is_generic(name, ""), name)

    def test_toolkit_defaults_are_generic(self):
        for name in ("Chromium", "electron", "unknown", "java"):
            self.assertTrue(_is_generic(name, ""), name)

    def test_a_real_app_name_is_not(self):
        for name in ("Spotify", "Discord", "Firefox"):
            self.assertFalse(_is_generic(name, ""), name)

    def test_a_name_matching_its_binary_is_not_generic(self):
        """The regression Cryo hit twice: Zen/zen is the normal good case.

        Sending it through the window lookup let a Flatpak's namespaced PID
        collide with another sandbox's window, and Zen showed as "Bolt
        Launcher".
        """
        self.assertFalse(_is_generic("Zen", "/app/bin/zen"))
        self.assertFalse(_is_generic("Discord", "discord"))

    def test_empty_is_generic(self):
        self.assertTrue(_is_generic("", ""))
        self.assertTrue(_is_generic(None, None))


class NameFromBinary(unittest.TestCase):
    def test_a_meaningful_binary_names_the_app(self):
        self.assertEqual(_binary_name("/usr/bin/cider", "Chromium"), "cider")

    def test_a_runtime_binary_says_nothing(self):
        for b in ("java", "/usr/bin/python3", "wine64", "node"):
            self.assertIsNone(_binary_name(b, "ALSA plug-in"), b)

    def test_a_binary_echoing_the_name_adds_nothing(self):
        self.assertIsNone(_binary_name("spotify", "Spotify"))

    def test_no_binary_is_no_name(self):
        self.assertIsNone(_binary_name("", "whatever"))
        self.assertIsNone(_binary_name(None, "whatever"))


class PickingTheWindowName(unittest.TestCase):
    def test_a_clean_class_beats_the_volatile_title(self):
        """WM_CLASS is the app identity; _NET_WM_NAME is the tab title."""
        self.assertEqual(
            _pick_name("Chromium", "Funny Cat Video - YouTube"), "Chromium")

    def test_a_reverse_dns_class_falls_back_to_the_title(self):
        self.assertEqual(
            _pick_name("net-runelite-client-RuneLite", "RuneLite"), "RuneLite")
        self.assertEqual(
            _pick_name("com.adamcake.Bolt", "Bolt Launcher"), "Bolt Launcher")

    def test_nothing_useful_returns_what_there_is(self):
        self.assertEqual(_pick_name("", "Title"), "Title")
        self.assertEqual(_pick_name("a.b", ""), "a.b")
        self.assertEqual(_pick_name("", ""), "")


class MatchKeyIsUntouched(unittest.TestCase):
    def test_enrichment_never_rewrites_app_name(self):
        """display_name is additive; the key claim_streams matches on is not
        modified by any of this. Guarded here as a rule, since the routing
        depends on exact equality."""
        from wavexlr import mixer
        import inspect
        src = inspect.getsource(mixer.list_audio_streams)
        self.assertIn('stream["display_name"] =', src)
        self.assertNotIn('stream["app_name"] =', src)


if __name__ == "__main__":
    unittest.main()
