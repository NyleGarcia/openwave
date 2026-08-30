"""What the tray icon claims about the microphone.

There are two mutes on one microphone and they move independently: the USB bit
that the hardware button and the window's switch drive, and the PipeWire row
mute that group hand-over drives without touching the hardware at all. A tray
reading only the first reports a live microphone while nothing is being
captured -- the one error this icon must not make, since being on air is the
only reason to look at it.

The rule is a pure function so it can be tested without a session bus, a tray
host, or a Wave.
"""

import unittest

# The CI runner installs no PyGObject on purpose -- the suite is meant to run
# with no GTK, no audio server and no hardware -- and tray.py's D-Bus surface
# is GLib through and through. The reducer itself is pure, but it lives in a
# module that cannot load without gi, so without gi this file politely
# excuses itself instead of erroring.
try:
    from wavexlr import tray
    from wavexlr.app import WaveXLRWindow
except ImportError as exc:
    raise unittest.SkipTest(f"PyGObject not available: {exc}")


class TheRule(unittest.TestCase):
    """compute(): three facts in, what to draw out."""

    def test_no_device_is_not_a_live_microphone(self):
        state = tray.compute(connected=False, hardware_muted=False,
                             row_muted=False)
        self.assertEqual(state["icon"], tray.ICON_ABSENT)
        self.assertEqual(state["tooltip"], "No Wave connected")

    def test_muting_cannot_be_chosen_with_no_device(self):
        """The menu item would do nothing; saying so beats it silently failing."""
        state = tray.compute(False, False, False)
        self.assertFalse(state["mute_enabled"])

    def test_a_connected_open_microphone_is_live(self):
        state = tray.compute(True, False, False)
        self.assertEqual(state["icon"], tray.ICON_LIVE)
        self.assertEqual(state["tooltip"], "Live")
        self.assertFalse(state["muted"])

    def test_the_hardware_bit_mutes(self):
        state = tray.compute(True, hardware_muted=True, row_muted=False)
        self.assertEqual(state["icon"], tray.ICON_MUTED)
        self.assertEqual(state["tooltip"], "Muted (hardware)")

    def test_the_row_mute_mutes_on_its_own(self):
        """The regression: hardware says open, nothing is captured.

        This is what group hand-over leaves behind on the microphone it
        handed away from, and reading the USB bit alone calls it live.
        """
        state = tray.compute(True, hardware_muted=False, row_muted=True)
        self.assertEqual(state["icon"], tray.ICON_MUTED)
        self.assertTrue(state["muted"])
        self.assertEqual(state["tooltip"], "Muted (matrix row)")

    def test_the_two_mutes_are_told_apart(self):
        """The way out differs, so naming the wrong one strands the user."""
        self.assertEqual(tray.compute(True, True, True)["tooltip"],
                         "Muted (hardware and matrix)")

    def test_the_menu_offers_the_action_not_the_state(self):
        self.assertEqual(tray.compute(True, False, False)["mute_label"],
                         "Mute Mic")
        self.assertEqual(tray.compute(True, True, False)["mute_label"],
                         "Unmute Mic")


class Announcing(unittest.TestCase):
    """set_state(): hosts redraw on every signal, so only real changes go out."""

    def setUp(self):
        self.tray = tray.TrayIcon()

    def test_it_starts_out_assuming_no_device(self):
        """Before the first poll nothing is known, and 'live' would be a guess."""
        self.assertEqual(self.tray._state["icon"], tray.ICON_ABSENT)

    def test_a_change_is_reported(self):
        self.assertTrue(self.tray.set_state(True, False, False))
        self.assertEqual(self.tray._state["icon"], tray.ICON_LIVE)

    def test_an_unchanged_state_is_not_reported(self):
        self.tray.set_state(True, False, False)
        self.assertFalse(self.tray.set_state(True, False, False))

    def test_the_menu_label_follows_the_state(self):
        self.tray.set_state(True, True, False)
        self.assertEqual(self.tray._menu_items[2]["label"].unpack(),
                         "Unmute Mic")


class CaptureRows(unittest.TestCase):
    """capture_rows_muted(): the row half of the answer, read off the sources."""

    def muted(self, sources):
        stub = type("W", (), {})()
        stub._sources = sources
        return WaveXLRWindow.capture_rows_muted(stub)

    def test_no_capture_rows_is_not_muted(self):
        """Nothing to be silenced by is not the same as silenced."""
        self.assertFalse(self.muted({"a": {"name": "Game"}}))

    def test_one_live_row_is_enough(self):
        self.assertFalse(self.muted({
            "a": {"node_name": "alsa_in.one", "muted": True},
            "b": {"node_name": "alsa_in.two", "muted": False},
        }))

    def test_every_row_muted_is_muted(self):
        self.assertTrue(self.muted({
            "a": {"node_name": "alsa_in.one", "muted": True},
            "b": {"node_name": "alsa_in.two", "muted": True},
        }))

    def test_application_rows_are_not_capture_rows(self):
        """A muted Music row says nothing about the microphone."""
        self.assertFalse(self.muted({
            "music": {"muted": True},
            "mic": {"node_name": "alsa_in.one", "muted": False},
        }))


if __name__ == "__main__":
    unittest.main()


class RememberedGeometry(unittest.TestCase):
    """_save_ui_state on a window that is lying about its size.

    Quit arrives via the tray with the window hidden, and a hidden GTK
    window reports 0x0. Writing that through destroyed the remembered
    geometry; the restore guard then discarded it and the window came back
    at the 1360px default, clipping the matrix -- read as "stuck half
    opened".
    """

    def save(self, width, height, maximized=False, previous=None):
        import json
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            stub = type("W", (), {})()
            stub._UI_STATE = os.path.join(tmp, "ui-state.json")
            if previous:
                with open(stub._UI_STATE, "w") as f:
                    json.dump(previous, f)
            stub.get_width = lambda: width
            stub.get_height = lambda: height
            stub.is_maximized = lambda: maximized
            stub._load_ui_state = lambda: (
                WaveXLRWindow._load_ui_state(stub))
            stub.gain_lock = None
            stub._offered_nodes = set()
            WaveXLRWindow._save_ui_state(stub)
            with open(stub._UI_STATE) as f:
                return json.load(f)

    def test_an_honest_size_is_recorded(self):
        state = self.save(1900, 1100)
        self.assertEqual((state["width"], state["height"]), (1900, 1100))

    def test_a_hidden_window_does_not_destroy_the_remembered_size(self):
        state = self.save(0, 0, previous={"width": 1900, "height": 1100})
        self.assertEqual((state["width"], state["height"]), (1900, 1100))

    def test_a_maximized_window_keeps_the_unmaximized_size(self):
        state = self.save(2560, 1440, maximized=True,
                          previous={"width": 1900, "height": 1100})
        self.assertEqual((state["width"], state["height"]), (1900, 1100))
