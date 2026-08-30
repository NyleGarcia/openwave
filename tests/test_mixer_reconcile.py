"""The mixer's decisions about the graph, exercised against a fake of it.

The reconcile and spawn paths are where the worst regressions have lived --
double-routed audio, loopbacks against dead links, faders driving nothing --
and until the PipeWire seam nothing could test them: they were ~30 scattered
subprocess calls. With Mixer(pw=FakePipeWire()) they are call-sequence
assertions: configure what the graph holds, run one reconcile, read back what
the mixer decided to do about it.
"""

import unittest
from unittest import mock

from wavexlr import mixer as mixer_mod
from .support import FakePipeWire, bare_mixer, temp_config

MIXES = {
    "personal": {"id": "personal", "name": "Personal Mix",
                 "sink": "openwave_personal_mix"},
    "chat": {"id": "chat", "name": "Chat Mix", "sink": "openwave_chat_mix"},
}
ARCTIS = "alsa_input.usb-Arctis-00.mono-fallback"


class Base(unittest.TestCase):
    def setUp(self):
        self._ctx = temp_config()
        self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)
        self.pw = FakePipeWire()
        self.mx = bare_mixer(_pw=self.pw, _mixes=dict(MIXES))
        # _link_capture polls for ports with real sleeps; a fake graph is
        # instantaneous, so waiting on it is only wasted wall-clock.
        ctx = mock.patch.object(mixer_mod.time, "sleep", lambda _s: None)
        ctx.start()
        self.addCleanup(ctx.stop)

    def loop_name(self, source_id="dock", mix_id="personal"):
        return self.mx._capture_loopback_name(source_id, mix_id)


class CaptureCells(Base):
    def setUp(self):
        super().setUp()
        self.mx._sources = {"dock": {"id": "dock", "name": "Dock",
                                     "node_name": ARCTIS, "level": 1.0}}
        self.mx._live_captures = frozenset({ARCTIS})

    def test_a_live_cell_spawns_its_loopback_and_sets_its_level(self):
        name = self.loop_name()
        self.pw.node_ids[name] = "77"
        self.mx._reconcile_capture_cell("dock", "personal", ARCTIS, 0.8, False)
        self.assertEqual(len(self.pw.spawned), 1)
        self.assertIn(("wpctl", "set-volume", "77", "0.800"), self.pw.calls)
        self.assertIn(("wpctl", "set-mute", "77", "0"), self.pw.calls)

    def test_the_cell_fader_composes_with_the_source_trim(self):
        """cell x trim is the whole level model; the graph gets the product."""
        self.mx._sources["dock"]["level"] = 0.5
        self.pw.node_ids[self.loop_name()] = "77"
        self.mx._reconcile_capture_cell("dock", "personal", ARCTIS, 0.8, False)
        self.assertIn(("wpctl", "set-volume", "77", "0.400"), self.pw.calls)

    def test_a_muted_source_silences_the_cell_without_tearing_it_down(self):
        self.mx._sources["dock"]["muted"] = True
        self.pw.node_ids[self.loop_name()] = "77"
        self.mx._reconcile_capture_cell("dock", "personal", ARCTIS, 0.8, False)
        self.assertIn(("wpctl", "set-volume", "77", "0.000"), self.pw.calls)
        self.assertEqual(len(self.pw.spawned), 1)

    def test_a_zero_cell_tears_the_loopback_down(self):
        self.mx._reconcile_capture_cell("dock", "personal", ARCTIS, 0.8, False)
        proc = self.pw.spawned[0]
        self.mx._reconcile_capture_cell("dock", "personal", ARCTIS, 0.0, False)
        self.assertTrue(proc.terminated)
        self.assertNotIn(("dock", "personal"), self.mx._procs)

    def test_an_absent_capture_node_is_not_looped_from(self):
        """The device vanished; a loopback would capture nothing forever."""
        self.mx._live_captures = frozenset({"some_other_node"})
        self.mx._reconcile_capture_cell("dock", "personal", ARCTIS, 0.8, False)
        self.assertEqual(self.pw.spawned, [])

    def test_a_second_reconcile_does_not_spawn_a_second_loopback(self):
        self.pw.node_ids[self.loop_name()] = "77"
        self.mx._reconcile_capture_cell("dock", "personal", ARCTIS, 0.8, False)
        self.mx._reconcile_capture_cell("dock", "personal", ARCTIS, 0.6, False)
        self.assertEqual(len(self.pw.spawned), 1)

    def test_the_volume_is_reapplied_every_pass(self):
        """The immune-by-construction claim from the cryo-port spec: no cached
        cell state, so a spawn that failed and was retried still ends at the
        right level. This is the property 07579a1 existed to patch around."""
        name = self.loop_name()
        self.pw.node_ids[name] = "77"
        self.mx._reconcile_capture_cell("dock", "personal", ARCTIS, 0.8, False)
        self.mx._reconcile_capture_cell("dock", "personal", ARCTIS, 0.8, False)
        sets = [c for c in self.pw.calls
                if c[:2] == ("wpctl", "set-volume") and c[2] == "77"]
        self.assertEqual(len(sets), 2)


class SpawnAndLink(Base):
    def test_the_capture_side_is_linked_port_by_port(self):
        self.pw.port_map[("-o", ARCTIS)] = [f"{ARCTIS}:capture_1"]
        loop = "openwave_loop_test"
        self.pw.port_map[("-i", f"{loop}_cap")] = [
            f"{loop}_cap:input_FL", f"{loop}_cap:input_FR"]
        self.mx._spawn_loopback(("k",), ARCTIS, "openwave_personal_mix", loop)
        links = [c for c in self.pw.calls if c[0] == "link"]
        # Mono source, stereo capture: the one port feeds both inputs.
        self.assertEqual(links, [
            ("link", f"{ARCTIS}:capture_1", f"{loop}_cap:input_FL"),
            ("link", f"{ARCTIS}:capture_1", f"{loop}_cap:input_FR"),
        ])

    def test_a_failed_spawn_leaves_no_bookkeeping(self):
        """A key with no process would block every future respawn."""
        self.pw.spawn_fails = True
        self.mx._spawn_loopback(("k",), ARCTIS, "sink", "openwave_loop_test")
        self.assertEqual(self.mx._procs, {})

    def test_the_loopback_carries_its_label(self):
        """Unlabelled it shows as pw-loopback-<pid> in every mixer tool."""
        self.mx._spawn_loopback(("k",), ARCTIS, "sink", "openwave_loop_test",
                                description="Dock → Personal Mix")
        argv = self.pw.spawned[0].argv
        self.assertIn("Dock → Personal Mix", " ".join(argv))


class ReapingTheDead(Base):
    def test_a_dead_loopback_frees_its_key_for_respawn(self):
        """PipeWire restarted under the child: the stale key must not block
        _spawn_loopback forever."""
        self.mx._spawn_loopback(("k",), ARCTIS, "sink", "openwave_loop_test")
        self.pw.spawned[0].dies()
        self.mx._reap_dead()
        self.assertEqual(self.mx._procs, {})

    def test_a_living_loopback_is_left_alone(self):
        self.mx._spawn_loopback(("k",), ARCTIS, "sink", "openwave_loop_test")
        self.mx._reap_dead()
        self.assertIn(("k",), self.mx._procs)


class RestoringThroughTheSeam(Base):
    def test_the_masters_are_applied_via_the_adapter(self):
        """The whole restore path, asserted on graph calls rather than on
        patched module functions."""
        self.mx._volumes_restored = False
        self.mx.remember_mix_volume("personal", 0.62, False)
        self.pw.volumes = {"openwave_personal_mix": (1.0, False),
                           "openwave_chat_mix": (1.0, False)}
        self.assertTrue(self.mx.restore_mix_volumes())
        self.assertIn(("set_sink_volume", "openwave_personal_mix", 0.62),
                      self.pw.calls)
        self.assertIn(("set_sink_mute", "openwave_personal_mix", False),
                      self.pw.calls)


if __name__ == "__main__":
    unittest.main()


class RedetectingTheDevice(unittest.TestCase):
    """mic/hp were resolved once, in __init__: a Wave plugged in after launch
    stayed None forever, so monitoring pointed at nothing while the USB side
    reconnected fine."""

    def setUp(self):
        self._ctx = temp_config()
        self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)
        self.pw = FakePipeWire()
        self.mx = bare_mixer(_pw=self.pw)
        self.mx.mic = None
        self.mx.hp = None

    def test_a_wave_that_appeared_is_adopted(self):
        self.pw.find_wave = lambda: ("alsa_input.usb-Wave-00.mono",
                                     "alsa_output.usb-Wave-00.stereo")
        self.assertTrue(self.mx.redetect_device())
        self.assertEqual(self.mx.mic, "alsa_input.usb-Wave-00.mono")

    def test_an_unchanged_answer_reconciles_nothing(self):
        """Called from every successful connect, so the common case -- same
        device, same nodes -- must not queue a graph pass."""
        self.mx.mic = "alsa_input.usb-Wave-00.mono"
        self.mx.hp = "alsa_output.usb-Wave-00.stereo"
        self.pw.find_wave = lambda: (self.mx.mic, self.mx.hp)
        self.assertFalse(self.mx.redetect_device())

    def test_a_changed_device_queues_a_reconcile(self):
        self.mx._started = True   # redetect happens on a running mixer
        self.pw.find_wave = lambda: ("alsa_input.usb-Wave-00.mono", None)
        self.mx.redetect_device()
        self.assertTrue(self.mx._pending)
