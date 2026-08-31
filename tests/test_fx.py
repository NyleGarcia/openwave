"""The per-microphone DSP chain: config render and lifecycle.

The chain is a filter-chain hosted by a `pipewire -c <generated conf>`
child. Neutral settings hold no process, cells drink from the chain's
published Source while it runs, and a settings change respawns rather
than patching — one code path.
"""

import os
import unittest

from wavexlr import mixer as mixer_mod
from wavexlr import sources
from .support import FakePipeWire, bare_mixer, temp_config

ARCTIS = "alsa_input.usb-Arctis-00.mono-fallback"


def _dev_source(**fx):
    return {"id": "dock", "kind": sources.KIND_DEVICE, "name": "Dock",
            "node_name": ARCTIS, "level": 1.0, "fx": fx}


class RenderConfig(unittest.TestCase):
    def test_each_effect_contributes_its_node(self):
        conf = mixer_mod.render_fx_config(_dev_source(
            lowcut=80, eq_low=3.0, eq_mid=-2.0, eq_high=1.5, delay_ms=120))
        for label in ("bq_highpass", "bq_lowshelf", "bq_peaking",
                      "bq_highshelf", "delay"):
            self.assertIn(label, conf)
        self.assertIn('"Freq" = 80.0', conf)
        self.assertIn('"Delay (s)" = 0.1200', conf)
        # sequential chain: every adjacent pair is linked
        self.assertIn('output = "hp:Out" input = "eql:In"', conf)

    def test_gate_and_compressor_are_ladspa_nodes_in_strip_order(self):
        conf = mixer_mod.render_fx_config(_dev_source(
            lowcut=80, gate=True, gate_thresh=-45.0,
            comp=True, comp_thresh=-20.0, comp_ratio=4.0))
        self.assertIn("type = ladspa", conf)
        self.assertIn('plugin = "gate_1410"', conf)
        self.assertIn('"Threshold (dB)" = -45.0', conf)
        self.assertIn('plugin = "sc4m_1916"', conf)
        self.assertIn('"Ratio (1:n)" = 4.0', conf)
        # channel-strip order: cut, gate, compress
        self.assertIn('output = "hp:Out" input = "gate:Input"', conf)
        self.assertIn('output = "gate:Output" input = "comp:Input"', conf)

    def test_neutral_plus_mono_is_a_bare_copy(self):
        conf = mixer_mod.render_fx_config(_dev_source(mono=True))
        self.assertIn("label = copy", conf)
        self.assertNotIn("bq_", conf)

    def test_chain_captures_the_raw_device_and_publishes_a_source(self):
        conf = mixer_mod.render_fx_config(_dev_source(lowcut=120))
        self.assertIn(f'target.object = "{ARCTIS}"', conf)
        self.assertIn(f'node.name = "{mixer_mod.fx_node_name("dock")}"', conf)
        self.assertIn("media.class = Audio/Source", conf)


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self._ctx = temp_config()
        self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)
        self.pw = FakePipeWire()
        self.mx = bare_mixer(_pw=self.pw)
        self.mx._sources = {"dock": _dev_source(lowcut=80)}
        self.mx._live_captures = frozenset({ARCTIS})

    def _fx_procs(self):
        return [p for p in self.pw.spawned if p.argv[0] == "pipewire"]

    def test_active_fx_spawns_the_chain_and_writes_its_config(self):
        self.mx._reconcile_fx("dock")
        procs = self._fx_procs()
        self.assertEqual(len(procs), 1)
        path = procs[0].argv[2]
        self.assertTrue(os.path.exists(path))
        self.assertIn("bq_highpass", open(path).read())

    def test_neutral_fx_holds_no_process(self):
        self.mx._reconcile_fx("dock")
        self.mx._sources["dock"]["fx"] = {}
        self.mx._reconcile_fx("dock")
        self.assertTrue(self._fx_procs()[0].terminated)

    def test_unchanged_settings_do_not_respawn(self):
        self.mx._reconcile_fx("dock")
        self.mx._reconcile_fx("dock")
        self.assertEqual(len(self._fx_procs()), 1)

    def test_changed_settings_respawn(self):
        self.mx._reconcile_fx("dock")
        self.mx._sources["dock"]["fx"] = {"lowcut": 120}
        self.mx._reconcile_fx("dock")
        procs = self._fx_procs()
        self.assertEqual(len(procs), 2)
        self.assertTrue(procs[0].terminated)

    def test_cells_drink_from_the_chain_while_it_runs(self):
        self.mx._mixes = {"chat": {"id": "chat", "name": "Chat",
                                   "sink": "openwave_chat_mix"}}
        self.mx._state = {"dock.chat": {"volume": 0.8, "muted": False}}
        self.mx._reconcile_fx("dock")
        self.mx._reconcile_cell("dock", "chat")
        fx_node = mixer_mod.fx_node_name("dock")
        self.assertIn(("ports", "-o", fx_node), self.pw.calls,
                      "the cell loopback must link from the fx node, "
                      "not the raw device")
        self.assertNotIn(("ports", "-o", ARCTIS), self.pw.calls)

    def test_a_dying_chain_does_not_respawn_loop(self):
        """A missing LADSPA library kills the chain instantly; respawning
        every reconcile would fork a corpse every two seconds forever."""
        self.mx._sources["dock"]["fx"] = {"gate": True}
        self.mx._reconcile_fx("dock")
        self._fx_procs()[0].dies()
        self.mx._reconcile_fx("dock")
        self.assertEqual(len(self._fx_procs()), 1, "no respawn after death")
        self.mx._reconcile_fx("dock")
        self.assertEqual(len(self._fx_procs()), 1)
        # a settings change is consent to try again
        self.mx._sources["dock"]["fx"] = {"gate": True, "lowcut": 80}
        self.mx._reconcile_fx("dock")
        self.assertEqual(len(self._fx_procs()), 2)

    def test_a_reaped_corpse_still_reads_as_death(self):
        """_reap_dead collects dead children before the fx pass looks, so
        an absent proc under a known config is the failure, not a fresh
        start — treating it as fresh was a slow respawn loop."""
        self.mx._sources["dock"]["fx"] = {"gate": True}
        self.mx._reconcile_fx("dock")
        self._fx_procs()[0].dies()
        self.mx._procs.pop(self.mx._fx_key("dock"))  # what _reap_dead does
        self.mx._reconcile_fx("dock")
        self.assertEqual(len(self._fx_procs()), 1, "no respawn after reap")
        self.assertIn("dock", self.mx._fx_failed)

    def test_replug_tears_the_chain_down_for_respawn(self):
        self.mx._reconcile_fx("dock")
        first = self._fx_procs()[0]
        self.mx._drop_device_cell_loopbacks(frozenset({ARCTIS}))
        self.assertTrue(first.terminated)
        self.mx._reconcile_fx("dock")
        self.assertEqual(len(self._fx_procs()), 2)


if __name__ == "__main__":
    unittest.main()
