"""The JSON stores. A bug here loses configuration rather than misroutes audio."""

import json
import os
import unittest

from wavexlr import mixes, sources

from .support import temp_config


class MixStore(unittest.TestCase):
    def test_first_run_seeds_the_built_ins(self):
        with temp_config():
            seeded = mixes.load_seeded()
            self.assertEqual(list(seeded), ["personal", "chat", "record"])
            self.assertTrue(os.path.exists(mixes.CONFIG_PATH))

    def test_seeded_sinks_keep_their_legacy_names(self):
        # Other applications target these by name; renaming one silently
        # breaks an OBS or Discord capture pointed at it.
        with temp_config():
            seeded = mixes.load_seeded()
            self.assertEqual(
                [m["sink"] for m in seeded.values()],
                ["openwave_personal_mix", "openwave_chat_mix",
                 "openwave_record_mix"],
            )

    def test_an_empty_store_is_respected_not_reseeded(self):
        # Deleting every mix is a decision, not a corruption.
        with temp_config():
            with open(mixes.CONFIG_PATH, "w") as f:
                json.dump({}, f)
            self.assertEqual(mixes.load_seeded(), {})

    def test_a_corrupt_store_is_quarantined_and_replaced(self):
        with temp_config():
            with open(mixes.CONFIG_PATH, "w") as f:
                f.write("not json at all")
            seeded = mixes.load_seeded()
            self.assertEqual(list(seeded), ["personal", "chat", "record"])
            self.assertTrue(os.path.exists(mixes.CONFIG_PATH + ".corrupt"))

    def test_a_non_object_payload_counts_as_corrupt(self):
        with temp_config():
            with open(mixes.CONFIG_PATH, "w") as f:
                json.dump([1, 2, 3], f)
            self.assertEqual(list(mixes.load_seeded()),
                             ["personal", "chat", "record"])

    def test_update_cannot_change_id_or_sink(self):
        # The id prefixes every "<source>.<mix>" cell key and the sink is what
        # other applications target; both are structural.
        with temp_config():
            store = mixes.load_seeded()
            mixes.update(store, "chat", name="Stream", id="HACK", sink="HACK")
            self.assertEqual(store["chat"]["id"], "chat")
            self.assertEqual(store["chat"]["sink"], "openwave_chat_mix")
            self.assertEqual(store["chat"]["name"], "Stream")

    def test_a_new_mix_gets_an_interpolation_safe_id(self):
        # The id is interpolated unquoted into pw-loopback properties and into
        # dot-separated cell keys.
        with temp_config():
            mix = mixes.new_mix(name="My \"Odd\" Mix / 2")
            self.assertRegex(mix["id"], r"^[a-z0-9_]+$")
            self.assertNotIn(".", mix["id"])
            self.assertTrue(mix["sink"].startswith("openwave_mix_"))

    def test_stale_version_subtitles_are_replaced_on_load(self):
        with temp_config():
            store = mixes.load_seeded()
            store["chat"]["subtitle"] = "To voice apps (v0.3.0)"
            mixes.save(store)
            self.assertEqual(mixes.load_seeded()["chat"]["subtitle"],
                             "Send to voice apps")

    def test_a_user_edited_subtitle_is_left_alone(self):
        with temp_config():
            store = mixes.load_seeded()
            store["chat"]["subtitle"] = "my own words"
            mixes.save(store)
            self.assertEqual(mixes.load_seeded()["chat"]["subtitle"],
                             "my own words")


class SourceStore(unittest.TestCase):
    def test_first_run_seeds_five_rows(self):
        with temp_config():
            seeded = sources.load_seeded()
            self.assertEqual(list(seeded),
                             ["system", "game", "music", "browser", "voice"])

    def test_exactly_one_row_is_the_catch_all(self):
        with temp_config():
            catch = [s for s in sources.load_seeded().values()
                     if s.get("catch_all")]
            self.assertEqual(len(catch), 1)
            self.assertEqual(catch[0]["id"], "system")

    def test_an_emptied_store_is_respected(self):
        with temp_config():
            with open(sources.CONFIG_PATH, "w") as f:
                json.dump({}, f)
            self.assertEqual(sources.load_seeded(), {})

    def test_kind_defaults_to_app_for_older_records(self):
        self.assertEqual(sources.kind({"match_app_name": "X"}),
                         sources.KIND_APP)
        self.assertEqual(sources.kind({"kind": "device"}), sources.KIND_DEVICE)

    def test_bindings_round_trip_through_the_entry_field(self):
        src = {"match_app_names": ["Spotify", "Tidal"]}
        self.assertEqual(sources.format_bindings(src), "Spotify, Tidal")
        self.assertEqual(sources.parse_bindings(" Spotify , Tidal ,, "),
                         ["Spotify", "Tidal"])

    def test_update_preserves_the_id(self):
        # A fresh id would orphan every persisted level for that row.
        with temp_config():
            store = sources.load_seeded()
            sources.update(store, "music", name="Tunes", id="HACK")
            self.assertEqual(store["music"]["id"], "music")
            self.assertEqual(store["music"]["name"], "Tunes")


class Reordering(unittest.TestCase):
    def setUp(self):
        self.order = ["system", "game", "music", "browser", "voice"]
        self.store = {k: {"id": k} for k in self.order}

    def test_moves_one_place(self):
        with temp_config():
            moved = sources.reorder(self.store, "voice", -1)
            self.assertEqual(list(moved),
                             ["system", "game", "music", "voice", "browser"])

    def test_clamps_rather_than_wrapping(self):
        # A row at the top must not jump to the bottom.
        with temp_config():
            moved = sources.reorder(self.store, "system", -5)
            self.assertEqual(list(moved), self.order)

    def test_a_no_op_move_returns_the_same_order(self):
        with temp_config():
            self.assertEqual(list(sources.reorder(self.store, "voice", 1)),
                             self.order)

    def test_an_unknown_id_is_ignored(self):
        with temp_config():
            self.assertEqual(list(sources.reorder(self.store, "nope", 1)),
                             self.order)


if __name__ == "__main__":
    unittest.main()
