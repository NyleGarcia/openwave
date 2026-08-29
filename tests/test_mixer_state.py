"""Mixer state: migration, output resolution, and the trim-and-send arithmetic."""

import json
import unittest

from wavexlr import mixer as mixer_mod
from wavexlr.mixer import OUTPUT_AUTO, OUTPUT_NONE, OUTPUTS_STATE_KEY

from .support import bare_mixer, temp_config


class StateMigration(unittest.TestCase):
    def test_the_legacy_scalar_folds_into_the_per_mix_mapping(self):
        mx = bare_mixer(_state={
            "mic.personal": {"volume": 0.5, "muted": False},
            "output": "alsa_output.FOO",
        })
        self.assertTrue(mx._migrate_state())
        self.assertEqual(mx._state[OUTPUTS_STATE_KEY]["personal"],
                         "alsa_output.FOO")

    def test_cells_survive_the_migration(self):
        mx = bare_mixer(_state={
            "mic.personal": {"volume": 0.5, "muted": False},
            "output": "alsa_output.FOO",
        })
        mx._migrate_state()
        self.assertEqual(mx.get_cell("mic", "personal"),
                         {"volume": 0.5, "muted": False})

    def test_an_existing_per_mix_choice_wins_over_the_scalar(self):
        # The mapping is the newer of the two; the scalar is only a fallback.
        mx = bare_mixer(_state={
            "output": "alsa_output.OLD",
            OUTPUTS_STATE_KEY: {"personal": "alsa_output.NEW"},
        })
        mx._migrate_state()
        self.assertEqual(mx._state[OUTPUTS_STATE_KEY]["personal"],
                         "alsa_output.NEW")

    def test_migrating_twice_changes_nothing_further(self):
        mx = bare_mixer(_state={"output": "alsa_output.FOO"})
        mx._migrate_state()
        snapshot = dict(mx._state[OUTPUTS_STATE_KEY])
        mx._migrate_state()
        self.assertEqual(mx._state[OUTPUTS_STATE_KEY], snapshot)

    def test_load_state_rejects_a_non_object_payload(self):
        # _migrate_state mutates whatever this returns, so a list or a string
        # reaching it would raise on the first .get().
        with temp_config():
            for payload in ("[1, 2, 3]", '"a string"', "not json"):
                with open(mixer_mod.CONFIG_PATH, "w") as f:
                    f.write(payload)
                self.assertEqual(bare_mixer()._load_state(), {},
                                 f"payload {payload!r} was not rejected")

    def test_load_state_reads_a_well_formed_file(self):
        with temp_config():
            with open(mixer_mod.CONFIG_PATH, "w") as f:
                json.dump({"mic.personal": {"volume": 0.5, "muted": False}}, f)
            self.assertEqual(
                bare_mixer()._load_state()["mic.personal"]["volume"], 0.5)

    def test_cells_excludes_reserved_keys(self):
        mx = bare_mixer(_state={
            "mic.personal": {"volume": 1.0, "muted": False},
            "output": "auto",
            OUTPUTS_STATE_KEY: {"personal": "auto"},
        })
        self.assertEqual(list(mx.cells()), ["mic.personal"])


class DefaultOutput(unittest.TestCase):
    def test_the_first_mix_monitors_by_default(self):
        mx = bare_mixer(_mixes={"personal": {}, "chat": {}})
        self.assertEqual(mx._default_output_for("personal"), OUTPUT_AUTO)
        self.assertEqual(mx._default_output_for("chat"), OUTPUT_NONE)

    def test_it_follows_the_order_rather_than_the_name(self):
        # The built-in mixes are deletable, so keying on the literal
        # "personal" would leave nothing monitored once it is gone.
        mx = bare_mixer(_mixes={"chat": {}, "record": {}})
        self.assertEqual(mx._default_output_for("chat"), OUTPUT_AUTO)
        self.assertEqual(mx._default_output_for("record"), OUTPUT_NONE)

    def test_a_stored_choice_beats_the_default(self):
        mx = bare_mixer(
            _mixes={"personal": {}, "chat": {}},
            _state={OUTPUTS_STATE_KEY: {"chat": "alsa_output.X"}},
        )
        self.assertEqual(mx.get_output("chat"), "alsa_output.X")

    def test_output_none_resolves_to_no_sink(self):
        mx = bare_mixer(
            _mixes={"personal": {}},
            _state={OUTPUTS_STATE_KEY: {"personal": OUTPUT_NONE}},
        )
        # Passing sinks in keeps this off the live system.
        self.assertIsNone(mx.resolve_output("personal", sinks=[], default_sink=None))

    def test_resolution_falls_through_an_absent_device(self):
        sinks = [{"name": "alsa_output.LIVE", "description": "Live", "priority": 100}]
        mx = bare_mixer(
            _mixes={"personal": {}},
            _state={OUTPUTS_STATE_KEY: {"personal": "alsa_output.UNPLUGGED"}},
        )
        self.assertEqual(
            mx.resolve_output("personal", sinks=sinks, default_sink=None),
            "alsa_output.LIVE",
        )

    def test_auto_prefers_the_highest_priority_output(self):
        sinks = [
            {"name": "alsa_output.LOW", "description": "Low", "priority": 100},
            {"name": "alsa_output.HIGH", "description": "High", "priority": 900},
        ]
        mx = bare_mixer(_mixes={"personal": {}})
        self.assertEqual(
            mx.resolve_output("personal", sinks=sinks, default_sink=None),
            "alsa_output.HIGH",
        )


class SourceTrim(unittest.TestCase):
    def test_absent_level_is_unity(self):
        mx = bare_mixer(_sources={"music": {}})
        self.assertEqual(mx._source_gain("music"), 1.0)

    def test_a_muted_source_contributes_nothing(self):
        mx = bare_mixer(_sources={"music": {"level": 0.8, "muted": True}})
        self.assertEqual(mx._source_gain("music"), 0.0)

    def test_the_level_is_clamped(self):
        mx = bare_mixer(_sources={"a": {"level": 5.0}, "b": {"level": -2.0}})
        self.assertEqual(mx._source_gain("a"), 1.0)
        self.assertEqual(mx._source_gain("b"), 0.0)

    def test_a_nonsense_level_falls_back_to_unity(self):
        mx = bare_mixer(_sources={"music": {"level": "loud"}})
        self.assertEqual(mx._source_gain("music"), 1.0)

    def test_an_unknown_source_is_unity(self):
        # The built-in microphone row is not in the sources store.
        self.assertEqual(bare_mixer()._source_gain("mic"), 1.0)


class DefaultSinkRescue(unittest.TestCase):
    """An intake sink must never be where the system sends its audio.

    It is an ordinary sink to the session manager, so it can win the
    default-sink election -- observed after a PipeWire restart, when the mix
    sinks were not yet present. The result is not silence: every application
    lands in one source row at that row's send level, which is quiet and in
    the wrong place, and looks like nothing is broken.
    """

    def setUp(self):
        self.moved = []
        self._run = mixer_mod.subprocess.run
        self._default = mixer_mod._default_sink_name
        mixer_mod.subprocess.run = lambda cmd, **kw: self.moved.append(cmd)

    def tearDown(self):
        mixer_mod.subprocess.run = self._run
        mixer_mod._default_sink_name = self._default

    def _rescue(self, current_default, mixes):
        mixer_mod._default_sink_name = lambda: current_default
        bare_mixer(_mixes=mixes)._rescue_default_sink()

    def test_it_moves_off_an_intake_sink(self):
        self._rescue("openwave_src_system",
                     {"personal": {"sink": "openwave_personal_mix"}})
        self.assertEqual(self.moved,
                         [["pactl", "set-default-sink", "openwave_personal_mix"]])

    def test_it_targets_the_first_mix(self):
        self._rescue("openwave_src_music", {
            "chat": {"sink": "openwave_chat_mix"},
            "personal": {"sink": "openwave_personal_mix"},
        })
        self.assertEqual(self.moved[0][-1], "openwave_chat_mix")

    def test_it_leaves_a_hardware_default_alone(self):
        self._rescue("alsa_output.usb-Headset",
                     {"personal": {"sink": "openwave_personal_mix"}})
        self.assertEqual(self.moved, [])

    def test_it_leaves_a_mix_default_alone(self):
        # The normal, intended state.
        self._rescue("openwave_personal_mix",
                     {"personal": {"sink": "openwave_personal_mix"}})
        self.assertEqual(self.moved, [])

    def test_it_does_nothing_with_no_mix_to_move_to(self):
        self._rescue("openwave_src_system", {})
        self.assertEqual(self.moved, [])

    def test_it_tolerates_an_unknown_default(self):
        self._rescue(None, {"personal": {"sink": "openwave_personal_mix"}})
        self.assertEqual(self.moved, [])


class SinkNaming(unittest.TestCase):
    def test_intake_names_are_derived_from_the_source_id(self):
        self.assertEqual(mixer_mod.source_sink_name("music"),
                         "openwave_src_music")

    def test_mix_source_keeps_the_published_node_name(self):
        # An application that has already selected this source stores it by
        # name, so changing the pattern silently re-points nothing and the
        # user's microphone selection goes dead.
        mx = bare_mixer()
        self.assertEqual(mx._mix_source_node("openwave_chat_mix"),
                         "openwave_chat_mix_source")

    def test_output_loopback_keys_are_recognised(self):
        # stop() and the atexit handler skip these so a mix keeps playing.
        self.assertTrue(mixer_mod._is_output_key(("output", "personal")))
        self.assertFalse(mixer_mod._is_output_key(("mic", "personal")))
        self.assertFalse(mixer_mod._is_output_key(("src", "mix", 7)))


if __name__ == "__main__":
    unittest.main()
