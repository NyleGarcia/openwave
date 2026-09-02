"""A device's own mute and its matrix row, kept telling the same story.

A headset's hardware mute button flips the source's ALSA mute and nothing
downstream can tell that silence from a quiet room: the row reads live over
a microphone delivering nothing. The reverse lie is a muted row over a
device whose own state says on-air. hw_mute_changes decides when the row
follows the device; the pactl plumbing carries the row back to the device.
"""

import json
import unittest
from unittest import mock

from wavexlr import mixer as mixer_mod
from wavexlr import sources


def device(source_id, node, muted=False):
    return {"id": source_id, "kind": sources.KIND_DEVICE,
            "name": source_id, "node_name": node, "muted": muted}


class Deciding(unittest.TestCase):
    def test_first_sight_mismatch_writes_the_row_to_the_device(self):
        """The muted-headset-at-startup trap: the row is deliberate mixer
        state, the device's mute may be a session manager's stale restore,
        so the row wins the first look -- which unmutes the device here."""
        srcs = {"hs": device("hs", "alsa_input.headset")}
        seen, moves, writes = sources.hw_mute_changes(
            {}, {"alsa_input.headset": True}, srcs)
        self.assertEqual(moves, [])
        self.assertEqual(writes, [("alsa_input.headset", False)])
        # Observed value remembered, not the written one.
        self.assertEqual(seen, {"alsa_input.headset": True})

    def test_first_sight_keeps_a_grouped_backup_muted(self):
        """A row muted by a group hand-over stays muted; hardware-wins here
        would put the backup mic on air at every launch."""
        srcs = {"hs": device("hs", "alsa_input.headset", muted=True)}
        seen, moves, writes = sources.hw_mute_changes(
            {}, {"alsa_input.headset": False}, srcs)
        self.assertEqual(moves, [])
        self.assertEqual(writes, [("alsa_input.headset", True)])

    def test_first_sight_agreement_touches_nothing(self):
        srcs = {"hs": device("hs", "alsa_input.headset", muted=True)}
        seen, moves, writes = sources.hw_mute_changes(
            {}, {"alsa_input.headset": True}, srcs)
        self.assertEqual((moves, writes), ([], []))
        self.assertEqual(seen, {"alsa_input.headset": True})

    def test_an_edge_moves_the_row(self):
        srcs = {"hs": device("hs", "alsa_input.headset")}
        seen, moves, writes = sources.hw_mute_changes(
            {"alsa_input.headset": False}, {"alsa_input.headset": True}, srcs)
        self.assertEqual(moves, [("hs", True)])
        self.assertEqual(writes, [])

    def test_disagreement_without_an_edge_is_left_alone(self):
        """The row's own writes travel the other way; acting on a mere
        disagreement would race a click and flip it back."""
        srcs = {"hs": device("hs", "alsa_input.headset", muted=True)}
        seen, moves, writes = sources.hw_mute_changes(
            {"alsa_input.headset": False}, {"alsa_input.headset": False}, srcs)
        self.assertEqual((moves, writes), ([], []))

    def test_a_row_click_racing_a_stale_snapshot_is_not_undone(self):
        """User mutes the row (row True, hardware written True) but the poll
        still carries the pre-click snapshot: no edge, no counter-flip; the
        next fresh snapshot is an edge that already agrees with the row."""
        srcs = {"hs": device("hs", "alsa_input.headset", muted=True)}
        seen = {"alsa_input.headset": False}
        seen, moves, writes = sources.hw_mute_changes(
            seen, {"alsa_input.headset": False}, srcs)
        self.assertEqual((moves, writes), ([], []))
        seen, moves, writes = sources.hw_mute_changes(
            seen, {"alsa_input.headset": True}, srcs)
        self.assertEqual((moves, writes), ([], []))
        self.assertEqual(seen, {"alsa_input.headset": True})

    def test_a_first_sight_write_is_not_undone_by_a_stale_snapshot(self):
        """After row-wins wrote unmute, a stale snapshot still reading muted
        is not an edge (the observed value was remembered), and the fresh
        snapshot that follows agrees with the row."""
        srcs = {"hs": device("hs", "alsa_input.headset")}
        seen, moves, writes = sources.hw_mute_changes(
            {}, {"alsa_input.headset": True}, srcs)
        self.assertEqual(writes, [("alsa_input.headset", False)])
        seen, moves, writes = sources.hw_mute_changes(
            seen, {"alsa_input.headset": True}, srcs)
        self.assertEqual((moves, writes), ([], []))
        seen, moves, writes = sources.hw_mute_changes(
            seen, {"alsa_input.headset": False}, srcs)
        self.assertEqual((moves, writes), ([], []))

    def test_app_sources_and_unknown_nodes_are_ignored(self):
        srcs = {
            "browser": {"id": "browser", "kind": sources.KIND_APP,
                        "name": "browser"},
            "ghost": device("ghost", "alsa_input.unplugged"),
            "nameless": device("nameless", ""),
        }
        seen, moves, writes = sources.hw_mute_changes(
            {}, {"alsa_input.other": True}, srcs)
        self.assertEqual((moves, writes), ([], []))
        self.assertEqual(seen, {})

    def test_a_vanished_device_is_forgotten_not_remembered(self):
        """Its next appearance is a first sight again, so the row's state
        reasserts itself over whatever the device came back wearing."""
        srcs = {"hs": device("hs", "alsa_input.headset")}
        seen, moves, writes = sources.hw_mute_changes(
            {"alsa_input.headset": True}, {}, srcs)
        self.assertEqual(seen, {})
        self.assertEqual((moves, writes), ([], []))


class ReadingPactl(unittest.TestCase):
    def _run(self, stdout, returncode=0):
        result = mock.Mock(stdout=stdout, returncode=returncode)
        return mock.patch.object(
            mixer_mod.subprocess, "run", return_value=result)

    def test_mutes_come_back_by_name(self):
        payload = json.dumps([
            {"name": "alsa_input.headset", "mute": True},
            {"name": "alsa_input.wave", "mute": False},
            {"no_name": "ignored"},
        ])
        with self._run(payload):
            self.assertEqual(mixer_mod._pactl_source_mutes(), {
                "alsa_input.headset": True,
                "alsa_input.wave": False,
            })

    def test_failure_reads_as_nothing_not_as_all_unmuted(self):
        with self._run("", returncode=1):
            self.assertEqual(mixer_mod._pactl_source_mutes(), {})
        with self._run("not json"):
            self.assertEqual(mixer_mod._pactl_source_mutes(), {})

    def test_setting_goes_through_pactl(self):
        with mock.patch.object(mixer_mod, "_run_quiet") as run:
            mixer_mod._pactl_set_source_mute("alsa_input.headset", True)
            run.assert_called_once_with(
                ["pactl", "set-source-mute", "alsa_input.headset", "1"])


class MixerSurface(unittest.TestCase):
    def test_set_capture_mute_reaches_the_seam(self):
        from .support import bare_mixer
        pw = mock.Mock()
        m = bare_mixer(_pw=pw)
        m.set_capture_mute("alsa_input.headset", True)
        pw.set_source_mute.assert_called_once_with(
            "alsa_input.headset", True)

    def test_an_empty_node_name_is_not_sent(self):
        from .support import bare_mixer
        pw = mock.Mock()
        m = bare_mixer(_pw=pw)
        m.set_capture_mute("", True)
        pw.set_source_mute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
