"""Scenes: named level snapshots, captured live and applied partially.

A scene sets levels on the matrix that exists. It never restructures it,
and a scene naming things that are gone applies what still matches and
reports the rest — recalling an old scene must never be dangerous.
"""

import json
import os
import unittest

from wavexlr import scenes
from .support import FakePipeWire, bare_mixer, temp_config

SOURCES = {
    "dock": {"id": "dock", "name": "XLR Dock", "level": 0.8, "muted": False},
    "music": {"id": "music", "name": "Music", "level": 0.5, "muted": True},
}
MIXES = {
    "personal": {"id": "personal", "name": "Personal Mix",
                 "sink": "openwave_personal_mix"},
    "chat": {"id": "chat", "name": "Chat Mix", "sink": "openwave_chat_mix"},
}


class Store(unittest.TestCase):
    def setUp(self):
        try:
            os.remove(scenes.CONFIG_PATH)
        except OSError:
            pass

    def test_empty_on_first_run(self):
        self.assertEqual(scenes.load(), {})

    def test_round_trip(self):
        sid = scenes.put("Streaming", {"cells": {"dock.personal":
                                                 {"volume": 1.0}}})
        self.assertEqual(sid, "streaming")
        loaded = scenes.load()
        self.assertEqual(loaded[sid]["name"], "Streaming")
        self.assertIn("dock.personal", loaded[sid]["cells"])

    def test_saving_again_replaces(self):
        scenes.put("Streaming", {"volumes": {"personal": {"volume": 0.2}}})
        scenes.put("Streaming", {"volumes": {"personal": {"volume": 0.9}}})
        loaded = scenes.load()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded["streaming"]["volumes"]["personal"]["volume"],
                         0.9)

    def test_remove(self):
        scenes.put("Late Night", {})
        self.assertTrue(scenes.remove("late-night"))
        self.assertFalse(scenes.remove("late-night"))
        self.assertEqual(scenes.load(), {})

    def test_a_corrupt_store_is_set_aside_not_fatal(self):
        with open(scenes.CONFIG_PATH, "w") as f:
            f.write("{not json")
        self.assertEqual(scenes.load(), {})
        self.assertTrue(os.path.exists(scenes.CONFIG_PATH + ".corrupt"))
        os.remove(scenes.CONFIG_PATH + ".corrupt")

    def test_ids_are_slugs(self):
        self.assertEqual(scenes.scene_id("Late Night!  Stream #2"),
                         "late-night-stream-2")
        self.assertEqual(scenes.scene_id("???"), "scene")


class HardwareKeying(unittest.TestCase):
    """Two devices of one model must not share a scene entry."""

    HW = {
        "wave_xlr_mk2:AAAA": {"gain_raw": 100},
        "wave_xlr_mk2:BBBB": {"gain_raw": 200},
        "wave3": {"gain_raw": 300},  # pre-serial scene
    }

    def test_exact_serial_wins(self):
        self.assertEqual(
            scenes.pick_hardware_entry(self.HW, "wave_xlr_mk2", "BBBB"),
            {"gain_raw": 200})

    def test_legacy_bare_profile_key_still_applies(self):
        self.assertEqual(
            scenes.pick_hardware_entry(self.HW, "wave3", "CCCC"),
            {"gain_raw": 300})

    def test_a_replacement_unit_inherits_the_model_entry(self):
        entry = scenes.pick_hardware_entry(self.HW, "wave_xlr_mk2", "NEW1")
        self.assertIn(entry, ({"gain_raw": 100}, {"gain_raw": 200}))

    def test_a_different_model_gets_nothing(self):
        self.assertIsNone(
            scenes.pick_hardware_entry(self.HW, "wave_xlr", "AAAA"))
        self.assertIsNone(scenes.pick_hardware_entry({}, "wave3", "X"))

    def test_key_shape(self):
        self.assertEqual(scenes.hardware_key("wave3", "S1"), "wave3:S1")
        self.assertEqual(scenes.hardware_key("wave3", ""), "wave3")


class MixerScenes(unittest.TestCase):
    def setUp(self):
        self._ctx = temp_config()
        self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)
        self.pw = FakePipeWire()
        self.mx = bare_mixer(
            _pw=self.pw,
            _sources={k: dict(v) for k, v in SOURCES.items()},
            _mixes={k: dict(v) for k, v in MIXES.items()},
        )

    def test_capture_reads_live_state(self):
        self.mx.set_cell("dock", "personal", 0.7, False)
        self.mx.remember_mix_volume("personal", 0.65, False)
        state = self.mx.scene_state()
        self.assertEqual(state["sources"]["dock"],
                         {"level": 0.8, "muted": False})
        self.assertEqual(state["cells"]["dock.personal"]["volume"], 0.7)
        self.assertEqual(state["volumes"]["personal"]["volume"], 0.65)
        self.assertIn("personal", state["outputs"])

    def test_capture_and_apply_round_trip(self):
        self.mx.set_cell("dock", "personal", 0.7, False)
        self.mx.set_cell("music", "chat", 0.3, True)
        state = self.mx.scene_state()
        # Move everything, then recall.
        self.mx.set_cell("dock", "personal", 0.1, True)
        self.mx._sources["dock"]["level"] = 0.2
        skipped = self.mx.apply_scene(state)
        self.assertEqual(skipped, [])
        self.assertEqual(self.mx.get_cell("dock", "personal"),
                         {"volume": 0.7, "muted": False})
        self.assertEqual(self.mx._sources["dock"]["level"], 0.8)

    def test_gone_entries_are_skipped_and_reported(self):
        scene = {
            "sources": {"gone": {"level": 1.0}},
            "cells": {"gone.personal": {"volume": 1.0},
                      "dock.gone_mix": {"volume": 1.0}},
            "outputs": {"gone_mix": "some_sink"},
            "volumes": {"gone_mix": {"volume": 0.5}},
        }
        skipped = self.mx.apply_scene(scene)
        self.assertEqual(sorted(skipped),
                         ["cell dock.gone_mix", "cell gone.personal",
                          "output gone_mix", "source gone",
                          "volume gone_mix"])
        # Nothing was created for them.
        self.assertNotIn("gone", self.mx._sources)
        self.assertNotIn("gone.personal", self.mx.cells())

    def test_volumes_hit_the_sink_and_are_remembered(self):
        self.mx.apply_scene(
            {"volumes": {"personal": {"volume": 0.4, "muted": True}}})
        self.assertIn(("set_sink_volume", "openwave_personal_mix", 0.4),
                      self.pw.calls)
        self.assertIn(("set_sink_mute", "openwave_personal_mix", True),
                      self.pw.calls)
        self.assertEqual(self.mx.mix_volume("personal"), (0.4, True))

    def test_ui_master_write_hits_sink_and_store(self):
        """The header slider and an external mover must be indistinguishable
        downstream: volume onto the sink, value into the store."""
        self.mx.set_mix_volume("personal", 0.55)
        self.assertIn(("set_sink_volume", "openwave_personal_mix", 0.55),
                      self.pw.calls)
        self.assertEqual(self.mx.mix_volume("personal"), (0.55, False))

    def test_ui_master_write_preserves_remembered_mute(self):
        self.mx.remember_mix_volume("personal", 0.9, True)
        self.mx.set_mix_volume("personal", 0.4)
        self.assertEqual(self.mx.mix_volume("personal"), (0.4, True))

    def test_apply_reconciles_through_the_normal_paths(self):
        """set_cell must be the entry point, so send × trim stays law."""
        self.mx.apply_scene({"cells": {"dock.personal": {"volume": 0.7}}})
        with self.mx._pending_lock:
            self.assertIn(("cell", "dock", "personal"), self.mx._pending)


if __name__ == "__main__":
    unittest.main()
