"""Remembering what a mix master is set to.

The mix sinks are context.objects in PipeWire's own configuration, so the
daemon recreates them from scratch on every start, at unity, with no memory.
WirePlumber does not restore them either -- they are neither streams nor
devices it manages -- so without this every mix master silently resets to
100% at each boot, including anything set from a control surface.
"""

import json
import unittest

from wavexlr import mixer as mixer_mod
from .support import bare_mixer, temp_config

MIXES = {
    "personal": {"id": "personal", "name": "Personal Mix",
                 "sink": "openwave_personal_mix"},
    "chat": {"id": "chat", "name": "Chat Mix", "sink": "openwave_chat_mix"},
    "quiet": {"id": "quiet", "name": "Unrouted", "sink": ""},
}


class Remembering(unittest.TestCase):
    def setUp(self):
        self._ctx = temp_config()
        self._ctx.__enter__()
        self.mixer = bare_mixer(_mixes=dict(MIXES))

    def tearDown(self):
        self._ctx.__exit__(None, None, None)

    def test_an_unseen_mix_has_nothing_remembered(self):
        self.assertIsNone(self.mixer.mix_volume("personal"))

    def test_a_level_survives_a_round_trip(self):
        self.mixer.remember_mix_volume("personal", 0.62, False)
        self.assertEqual(self.mixer.mix_volume("personal"), (0.62, False))

    def test_a_mute_is_remembered_with_it(self):
        self.mixer.remember_mix_volume("chat", 0.4, True)
        self.assertEqual(self.mixer.mix_volume("chat"), (0.4, True))

    def test_it_reaches_disk_immediately(self):
        """A reboot is not a graceful shutdown; nothing may wait for one."""
        self.mixer.remember_mix_volume("personal", 0.33, False)
        stored = json.load(open(mixer_mod.CONFIG_PATH))
        self.assertEqual(stored["volumes"]["personal"],
                         {"volume": 0.33, "muted": False})

    def test_an_unchanged_level_is_not_rewritten(self):
        """Observed twice a second; rewriting the file each time would be a
        write every tick for the life of the process."""
        self.assertTrue(self.mixer.remember_mix_volume("personal", 0.5, False))
        self.assertFalse(self.mixer.remember_mix_volume("personal", 0.5, False))

    def test_a_tiny_drift_is_not_a_change(self):
        """pactl reports percent, so a value set as 0.62 reads back rounded;
        without a tolerance that alone would rewrite the file forever."""
        self.mixer.remember_mix_volume("personal", 0.62, False)
        self.assertFalse(
            self.mixer.remember_mix_volume("personal", 0.6203, False))

    def test_a_real_change_is_written(self):
        self.mixer.remember_mix_volume("personal", 0.5, False)
        self.assertTrue(self.mixer.remember_mix_volume("personal", 0.7, False))

    def test_a_mute_alone_is_a_change(self):
        self.mixer.remember_mix_volume("personal", 0.5, False)
        self.assertTrue(self.mixer.remember_mix_volume("personal", 0.5, True))

    def test_levels_are_clamped(self):
        self.mixer.remember_mix_volume("personal", 4.0, False)
        self.assertEqual(self.mixer.mix_volume("personal"), (1.0, False))

    def test_corrupt_state_reads_as_unknown_rather_than_raising(self):
        """Restoring runs at startup; a bad value must not stop the mixer."""
        for bad in ("nonsense", {"volume": "loud"}, {}, None, []):
            self.mixer._state["volumes"] = {"personal": bad}
            self.assertIsNone(self.mixer.mix_volume("personal"), bad)

    def test_volumes_is_not_mistaken_for_a_cell(self):
        """Cell keys are "<source>.<mix>"; a reserved bare word is not one,
        and treating it as a cell would put a dict where a level belongs."""
        self.mixer.remember_mix_volume("personal", 0.5, False)
        self.mixer._state["music.personal"] = {"volume": 0.5, "muted": False}
        self.assertEqual(list(self.mixer.cells()), ["music.personal"])


class Restoring(unittest.TestCase):
    def setUp(self):
        self._ctx = temp_config()
        self._ctx.__enter__()
        self.applied = []
        self._vol = mixer_mod._pactl_set_sink_volume
        self._mute = mixer_mod._pactl_set_sink_mute
        mixer_mod._pactl_set_sink_volume = \
            lambda s, v: self.applied.append(("volume", s, round(v, 3)))
        mixer_mod._pactl_set_sink_mute = \
            lambda s, m: self.applied.append(("mute", s, m))
        self._live = mixer_mod._pactl_sink_volumes
        mixer_mod._pactl_sink_volumes = lambda: {
            "openwave_personal_mix": (1.0, False),
            "openwave_chat_mix": (1.0, False),
        }
        self.mixer = bare_mixer(_mixes=dict(MIXES))
        self.mixer._volumes_restored = False

    def tearDown(self):
        mixer_mod._pactl_set_sink_volume = self._vol
        mixer_mod._pactl_set_sink_mute = self._mute
        mixer_mod._pactl_sink_volumes = self._live
        self._ctx.__exit__(None, None, None)

    def test_it_puts_back_what_was_remembered(self):
        self.mixer.remember_mix_volume("personal", 0.62, False)
        self.mixer.restore_mix_volumes()
        self.assertIn(("volume", "openwave_personal_mix", 0.62), self.applied)
        self.assertIn(("mute", "openwave_personal_mix", False), self.applied)

    def test_a_mix_never_seen_is_left_at_whatever_it_came_up_as(self):
        """Restoring an unknown mix to a made-up default would be inventing a
        level nobody chose."""
        self.mixer.restore_mix_volumes()
        self.assertEqual(self.applied, [])

    def test_a_mix_routed_nowhere_is_skipped(self):
        self.mixer.remember_mix_volume("quiet", 0.5, False)
        self.mixer.restore_mix_volumes()
        self.assertEqual(self.applied, [])


class Observing(unittest.TestCase):
    def setUp(self):
        self._ctx = temp_config()
        self._ctx.__enter__()
        self._real = mixer_mod._pactl_sink_volumes
        self.live = {}
        mixer_mod._pactl_sink_volumes = lambda: self.live
        self.mixer = bare_mixer(_mixes=dict(MIXES))

    def tearDown(self):
        mixer_mod._pactl_sink_volumes = self._real
        self._ctx.__exit__(None, None, None)

    def test_it_records_whatever_moved_the_master(self):
        """Polled rather than hooked: anything may move a sink volume -- this
        window, a Stream Deck, pavucontrol, a media key -- and whoever moved
        it, that is the value that should come back after a reboot."""
        self.live = {"openwave_personal_mix": (0.45, False)}
        self.mixer.observe_mix_volumes()
        self.assertEqual(self.mixer.mix_volume("personal"), (0.45, False))

    def test_it_records_a_mute_made_elsewhere(self):
        self.live = {"openwave_chat_mix": (0.8, True)}
        self.mixer.observe_mix_volumes()
        self.assertEqual(self.mixer.mix_volume("chat"), (0.8, True))

    def test_a_sink_that_is_not_there_is_not_invented(self):
        self.live = {}
        self.mixer.observe_mix_volumes()
        self.assertIsNone(self.mixer.mix_volume("personal"))

    def test_pactl_failing_does_not_erase_what_was_known(self):
        self.mixer.remember_mix_volume("personal", 0.62, False)
        self.live = {}
        self.mixer.observe_mix_volumes()
        self.assertEqual(self.mixer.mix_volume("personal"), (0.62, False))


class ReadingPactl(unittest.TestCase):
    def _parse(self, payload):
        class Result:
            returncode = 0
            stdout = json.dumps(payload)
        real = mixer_mod.subprocess.run
        mixer_mod.subprocess.run = lambda *a, **k: Result()
        try:
            return mixer_mod._pactl_sink_volumes()
        finally:
            mixer_mod.subprocess.run = real

    def test_it_reads_volume_and_mute(self):
        got = self._parse([{
            "name": "openwave_chat_mix", "mute": True,
            "volume": {"front-left": {"value": 32768},
                       "front-right": {"value": 32768}},
        }])
        self.assertEqual(got["openwave_chat_mix"][1], True)
        self.assertAlmostEqual(got["openwave_chat_mix"][0], 0.5, places=2)

    def test_the_loudest_channel_wins(self):
        """A mix balanced off-centre still has one master; taking the first
        channel would report the quiet side as the level."""
        got = self._parse([{
            "name": "s", "mute": False,
            "volume": {"front-left": {"value": 16384},
                       "front-right": {"value": 65536}},
        }])
        self.assertAlmostEqual(got["s"][0], 1.0, places=2)

    def test_a_sink_with_no_channels_is_skipped(self):
        self.assertEqual(self._parse([{"name": "s", "volume": {}}]), {})

    def test_junk_is_not_an_exception(self):
        """This runs on a poll tick; raising here would stop the tick."""
        for payload in ({}, "text", [None], [{"volume": None}]):
            self.assertIsInstance(self._parse(payload), dict)


class TheBootRace(unittest.TestCase):
    """The one that matters: at boot the sinks exist at unity before
    OpenWave does. An observation that lands before the restore persists that
    unity and destroys the saved value -- silently, once per boot, which is
    indistinguishable from never having saved anything."""

    def setUp(self):
        self._ctx = temp_config()
        self._ctx.__enter__()
        self._real = mixer_mod._pactl_sink_volumes
        self._vol = mixer_mod._pactl_set_sink_volume
        self._mute = mixer_mod._pactl_set_sink_mute
        self.applied = []
        mixer_mod._pactl_set_sink_volume = \
            lambda s, v: self.applied.append((s, round(v, 3)))
        mixer_mod._pactl_set_sink_mute = lambda s, m: None
        # What the daemon just created the sinks at.
        mixer_mod._pactl_sink_volumes = lambda: {
            "openwave_personal_mix": (1.0, False)}
        self.mixer = bare_mixer(_mixes=dict(MIXES))
        self.mixer._volumes_restored = False

    def tearDown(self):
        mixer_mod._pactl_sink_volumes = self._real
        mixer_mod._pactl_set_sink_volume = self._vol
        mixer_mod._pactl_set_sink_mute = self._mute
        self._ctx.__exit__(None, None, None)

    def test_observing_before_restoring_changes_nothing(self):
        self.mixer.remember_mix_volume("personal", 0.62, False)
        self.mixer.observe_mix_volumes()
        self.assertEqual(self.mixer.mix_volume("personal"), (0.62, False))

    def test_the_saved_value_is_what_gets_applied(self):
        self.mixer.remember_mix_volume("personal", 0.62, False)
        self.assertTrue(self.mixer.restore_mix_volumes())
        self.assertIn(("openwave_personal_mix", 0.62), self.applied)

    def test_observation_resumes_once_restored(self):
        self.mixer.remember_mix_volume("personal", 0.62, False)
        self.mixer.restore_mix_volumes()
        self.mixer.observe_mix_volumes()
        self.assertEqual(self.mixer.mix_volume("personal"), (1.0, False))

    def test_restoring_with_no_mixes_leaves_the_gate_shut(self):
        """Called before the mix definitions arrive, it must not open the
        gate: doing so would let the next tick persist unity."""
        self.mixer._mixes = {}
        self.mixer.remember_mix_volume("personal", 0.62, False)
        self.assertFalse(self.mixer.restore_mix_volumes())
        self.mixer.observe_mix_volumes()
        self.assertEqual(self.mixer.mix_volume("personal"), (0.62, False))

    def test_the_gate_stays_shut_until_the_sinks_exist(self):
        """The definitions can be loaded while the sinks still are not.

        First run writes the PipeWire config, so the daemon creates the mix
        sinks after OpenWave is already running; the same gap opens whenever
        PipeWire is restarted under it. The restore writes into that gap and
        pactl fails, silently -- _run_quiet does not even look at the return
        code -- so the gate would open on a restore that did nothing, and the
        next tick persists the unity the sinks then come up at.
        """
        mixer_mod._pactl_sink_volumes = lambda: {}      # not created yet
        self.mixer.remember_mix_volume("personal", 0.62, False)
        self.assertFalse(self.mixer.restore_mix_volumes())

        # a moment later the daemon creates them, at unity
        mixer_mod._pactl_sink_volumes = lambda: {
            "openwave_personal_mix": (1.0, False)}
        self.mixer.observe_mix_volumes()
        self.assertEqual(self.mixer.mix_volume("personal"), (0.62, False))

    def test_a_late_sink_is_restored_when_it_does_arrive(self):
        """Shutting the gate is only right if something reopens it."""
        mixer_mod._pactl_sink_volumes = lambda: {}
        self.mixer.remember_mix_volume("personal", 0.62, False)
        self.assertFalse(self.mixer.restore_mix_volumes())

        mixer_mod._pactl_sink_volumes = lambda: {
            "openwave_personal_mix": (1.0, False)}
        self.assertTrue(self.mixer.restore_mix_volumes())
        self.assertIn(("openwave_personal_mix", 0.62), self.applied)

    def test_nothing_remembered_does_not_wait_for_a_sink(self):
        """A first run has nothing to protect, and must still start
        observing -- otherwise the first level a user sets is never saved."""
        mixer_mod._pactl_sink_volumes = lambda: {}
        self.assertTrue(self.mixer.restore_mix_volumes())


if __name__ == "__main__":
    unittest.main()
