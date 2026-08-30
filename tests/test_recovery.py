"""Recovering a capture device that enumerated but never started.

The decision is separated from the act so it can be tested without a sound
card. What makes this worth testing is that both mistakes are silent: failing
to recover leaves a dead microphone that every layer reports as healthy, and
recovering too eagerly cycles a card underneath someone who is using it.
"""

import unittest

from wavexlr import recovery

DOCK = ("alsa_input.usb-Elgato_Systems_Elgato_XLR_Dock_A8A9A40411NOP9-00"
        ".mono-fallback")


class CardNames(unittest.TestCase):
    def test_a_capture_node_names_its_card(self):
        self.assertEqual(
            recovery.card_name_for(DOCK),
            "alsa_card.usb-Elgato_Systems_Elgato_XLR_Dock_A8A9A40411NOP9-00")

    def test_the_profile_is_not_part_of_the_device(self):
        """Two profiles of one card must resolve to the same card, or the
        remedy would be aimed at a card that does not exist."""
        analog = ("alsa_output.usb-Elgato_Systems_Elgato_XLR_Dock_A8A9-00"
                  ".analog-stereo")
        mono = ("alsa_input.usb-Elgato_Systems_Elgato_XLR_Dock_A8A9-00"
                ".mono-fallback")
        self.assertEqual(recovery.card_name_for(analog),
                         recovery.card_name_for(mono))

    def test_nodes_that_are_not_alsa_have_no_card(self):
        for name in ("openwave_personal_mix", "openwave_src_music",
                     "spotify", "", None):
            self.assertIsNone(recovery.card_name_for(name), name)


class Deciding(unittest.TestCase):
    def setUp(self):
        self.watch = recovery.StallWatch(
            stall_seconds=8.0, cooldown_seconds=60.0, max_attempts=2)

    def test_a_live_node_delivering_nothing_is_recovered(self):
        self.assertTrue(
            self.watch.should_recover(DOCK, True, silent_for=9.0, now=100.0))

    def test_a_node_delivering_recently_is_left_alone(self):
        self.assertFalse(
            self.watch.should_recover(DOCK, True, silent_for=1.0, now=100.0))

    def test_an_absent_node_is_not_stalled(self):
        """Unplugged is not broken. Cycling the card for a device someone has
        just removed fights the person who removed it."""
        self.assertFalse(
            self.watch.should_recover(DOCK, False, silent_for=999.0,
                                      now=100.0))

    def test_a_source_with_no_meter_is_not_stalled(self):
        """silent_for is None when nothing is metering it, which is not the
        same as a meter that is receiving nothing."""
        self.assertFalse(
            self.watch.should_recover(DOCK, True, silent_for=None, now=100.0))

    def test_the_remedy_is_not_repeated_immediately(self):
        """Cycling a card is disruptive; a stall that survives one attempt
        must not become a loop."""
        self.assertTrue(self.watch.should_recover(DOCK, True, 9.0, 100.0))
        self.watch.record_attempt(DOCK, 100.0)
        self.assertFalse(self.watch.should_recover(DOCK, True, 9.0, 110.0))

    def test_it_may_be_retried_after_the_cooldown(self):
        self.watch.record_attempt(DOCK, 100.0)
        self.assertTrue(self.watch.should_recover(DOCK, True, 9.0, 200.0))

    def test_a_device_that_will_not_come_back_is_given_up_on(self):
        """Two attempts, then left alone to be noticed rather than cycled
        every minute forever."""
        for attempt, now in enumerate((100.0, 200.0)):
            self.assertTrue(
                self.watch.should_recover(DOCK, True, 9.0, now), attempt)
            self.watch.record_attempt(DOCK, now)
        self.assertFalse(self.watch.should_recover(DOCK, True, 9.0, 300.0))

    def test_replugging_restores_the_budget(self):
        """Unplugging and replugging is how the stall arises in the first
        place, so it must not inherit the previous appearance's attempts."""
        for now in (100.0, 200.0):
            self.watch.record_attempt(DOCK, now)
        self.assertFalse(self.watch.should_recover(DOCK, True, 9.0, 300.0))
        self.watch.should_recover(DOCK, False, 9.0, 310.0)
        self.watch.forget(DOCK)
        self.assertTrue(self.watch.should_recover(DOCK, True, 9.0, 320.0))

    def test_audio_returning_clears_the_count(self):
        self.watch.record_attempt(DOCK, 100.0)
        self.watch.record_recovered(DOCK)
        self.assertTrue(self.watch.should_recover(DOCK, True, 9.0, 105.0))

    def test_two_devices_are_counted_separately(self):
        other = "alsa_input.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.mono-fallback"
        for now in (100.0, 200.0):
            self.watch.record_attempt(DOCK, now)
        self.assertFalse(self.watch.should_recover(DOCK, True, 9.0, 300.0))
        self.assertTrue(self.watch.should_recover(other, True, 9.0, 300.0))


class Cycling(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._real = recovery._pactl
        self.profile = "input:mono-fallback"

        def fake(*args, timeout=5):
            self.calls.append(args)
            if args[0] == "list":
                return (f"Card #3\n\tName: alsa_card.other\n"
                        f"\tActive Profile: off\n"
                        f"Card #4\n\tName: alsa_card.dock\n"
                        f"\tActive Profile: {self.profile}\n")
            return ""
        recovery._pactl = fake

    def tearDown(self):
        recovery._pactl = self._real

    def test_it_reads_the_right_card(self):
        """Two cards in the listing; picking the wrong one would restore a
        profile that belongs to another device."""
        self.assertEqual(recovery.active_profile("alsa_card.dock"),
                         "input:mono-fallback")
        self.assertEqual(recovery.active_profile("alsa_card.other"), "off")
        self.assertIsNone(recovery.active_profile("alsa_card.absent"))

    def test_it_goes_through_off_and_back(self):
        """Setting a card to the profile it already has is a no-op, and the
        close-and-reopen is the entire point."""
        self.assertTrue(recovery.cycle_card("alsa_card.dock"))
        sets = [c for c in self.calls if c[0] == "set-card-profile"]
        self.assertEqual(
            sets,
            [("set-card-profile", "alsa_card.dock", "off"),
             ("set-card-profile", "alsa_card.dock", "input:mono-fallback")])

    def test_the_users_profile_is_what_comes_back(self):
        """OpenWave deliberately puts a Wave into an input-only profile;
        returning on a different one would silently change the device."""
        self.profile = "input:mono-fallback"
        recovery.cycle_card("alsa_card.dock")
        self.assertEqual(self.calls[-1][2], "input:mono-fallback")

    def test_a_card_already_off_is_left_alone(self):
        self.assertFalse(recovery.cycle_card("alsa_card.other"))
        self.assertFalse([c for c in self.calls if c[0] == "set-card-profile"])

    def test_an_unknown_card_is_not_touched(self):
        self.assertFalse(recovery.cycle_card("alsa_card.absent"))
        self.assertFalse([c for c in self.calls if c[0] == "set-card-profile"])


if __name__ == "__main__":
    unittest.main()


class MeterSilence(unittest.TestCase):
    """`silent_for` is the input the whole decision rests on."""

    def setUp(self):
        from wavexlr.meter import MeterMonitor
        self.meter = MeterMonitor()

    def test_no_meter_reads_as_nothing_measured(self):
        self.assertIsNone(self.meter.silent_for("absent"))

    def test_a_running_meter_reports_its_age(self):
        import time as _time

        class Alive:
            def poll(self):
                return None

        self.meter._procs["dock"] = Alive()
        self.meter._last_data["dock"] = _time.monotonic() - 5.0
        self.assertAlmostEqual(self.meter.silent_for("dock"), 5.0, delta=0.5)

    def test_a_dead_meter_says_nothing_about_the_hardware(self):
        """If pw-cat itself died, its silence is about pw-cat. Treating that
        as a stalled device would cycle a card that is working fine."""
        import time as _time

        class Exited:
            def poll(self):
                return 1

        self.meter._procs["dock"] = Exited()
        self.meter._last_data["dock"] = _time.monotonic() - 999.0
        self.assertIsNone(self.meter.silent_for("dock"))


class TrayHostProbe(unittest.TestCase):
    """Whether a tray exists decides whether hiding the window is safe."""

    def _probe(self, answer):
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import GLib
        from wavexlr.tray import TrayIcon

        class Bus:
            def call_sync(self, *a, **k):
                if isinstance(answer, Exception):
                    raise answer
                return GLib.Variant("(b)", (answer,))

        return TrayIcon.host_available(Bus())

    def test_a_watcher_on_the_bus_means_yes(self):
        self.assertTrue(self._probe(True))

    def test_no_watcher_means_no(self):
        """GNOME ships no StatusNotifier host: the name appears only when an
        AppIndicator extension is installed."""
        self.assertFalse(self._probe(False))

    def test_a_bus_error_means_no(self):
        from gi.repository import GLib
        self.assertFalse(self._probe(
            GLib.Error.new_literal(GLib.quark_from_string("g-io"), "x", 0)))
