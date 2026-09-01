"""The watchdogs for faults every byte-level check passes.

Both decisions are pure so they can be tested without a sound card, and
both mistakes are silent: missing the fault leaves robotic or inaudible
audio that every layer reports as healthy, and acting too eagerly cycles
hardware underneath someone who is using it.
"""

import unittest

from wavexlr import health


DOCK = ("alsa_input.usb-Elgato_Systems_Elgato_XLR_Dock_A8A9A40411NOP9-00"
        ".mono-fallback")
SINK = "alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.analog-stereo"


# Real pw-top output shapes, including the quirks the parser must
# survive: the header, '---' placeholder rows, '???' warmup ratios, the
# FORMAT column being present, absent, or three tokens wide, and the
# first iteration printing zeros before the profiler warms up.
PW_TOP_OUTPUT = f"""\
S   ID  QUANT   RATE    WAIT    BUSY   W/Q   B/Q  ERR FORMAT           NAME
C   73      0      0    ---     ---   ---   ---     0                  {DOCK}
R   73      0      0   0.0us   0.0us  ???   ???     0    S24LE 1 48000 {DOCK}
S   ID  QUANT   RATE    WAIT    BUSY   W/Q   B/Q  ERR FORMAT           NAME
R   73      0      0  12.3us   4.2us  0.00  0.00  30367    S24LE 1 48000  + {DOCK}
R  199      0      0   1.2us   7.4us  0.00  0.00    5         F32P 1 0  + openwave_fx_2f216c26f5e3
"""


class ParsingPwTop(unittest.TestCase):
    def test_the_last_iteration_wins(self):
        counts = health._parse_pw_top(PW_TOP_OUTPUT)
        self.assertEqual(counts[DOCK], 30367)

    def test_every_printed_node_is_counted(self):
        counts = health._parse_pw_top(PW_TOP_OUTPUT)
        self.assertEqual(counts["openwave_fx_2f216c26f5e3"], 5)

    def test_headers_and_placeholder_rows_do_not_crash_or_count(self):
        counts = health._parse_pw_top(PW_TOP_OUTPUT)
        self.assertNotIn("NAME", counts)
        self.assertNotIn("FORMAT", counts)


class GlitchDeciding(unittest.TestCase):
    def setUp(self):
        self.w = health.GlitchWatch(
            threshold=50, confirm=2, cooldown_seconds=60, max_attempts=2)

    def feed(self, counts, start=0.0, step=10.0):
        verdicts = []
        for i, c in enumerate(counts):
            verdicts.append(self.w.observe(DOCK, c, start + i * step))
        return verdicts

    def test_first_sight_only_baselines(self):
        """A node first seen with a huge historical count has not been
        observed glitching — the count could be weeks old."""
        self.assertEqual(self.feed([61994]), [False])
        self.assertFalse(self.w.glitching(DOCK))

    def test_a_flat_counter_is_healthy(self):
        self.feed([100, 100, 102, 102])
        self.assertFalse(self.w.glitching(DOCK))

    def test_the_robotic_fault_is_confirmed_in_two_windows(self):
        # ~23 xruns/s over 10 s windows, as measured on hardware.
        self.feed([0, 230, 460])
        self.assertTrue(self.w.glitching(DOCK))
        self.assertTrue(self.w.should_recover(DOCK, now=100.0))

    def test_one_burst_is_an_event_not_a_state(self):
        """A single bad window (game launch, compile) must not cycle a
        card someone is speaking into."""
        self.feed([0, 230, 235])
        self.assertFalse(self.w.glitching(DOCK))

    def test_a_wireless_followers_own_jitter_stays_below_threshold(self):
        # The Arctis was observed bursting 23 in one window while healthy.
        self.feed([238, 261, 284])
        self.assertFalse(self.w.glitching(DOCK))

    def test_a_recreated_node_baselines_instead_of_panicking(self):
        """The profiler counter resets when a node is recreated; the
        shrink must start a fresh baseline, not be treated as glitching
        or as a 4-billion-xrun window."""
        self.feed([30000, 30230, 5])
        self.assertEqual(self.w._streak[DOCK], 0)

    def test_attempts_are_capped(self):
        self.feed([0, 230, 460])
        self.w.record_attempt(DOCK, 20.0)
        self.feed([690, 920], start=100.0)
        self.w.record_attempt(DOCK, 120.0)
        self.feed([1150, 1380], start=300.0)
        self.assertFalse(self.w.should_recover(DOCK, now=400.0))

    def test_cooldown_blocks_a_rapid_second_attempt(self):
        self.feed([0, 230, 460])
        self.w.record_attempt(DOCK, 20.0)
        self.feed([690], start=30.0)
        self.assertFalse(self.w.should_recover(DOCK, now=30.0))
        self.assertTrue(self.w.should_recover(DOCK, now=90.0))

    def test_a_clean_window_refills_the_budget(self):
        """Recovery is per incident, not per process lifetime."""
        self.feed([0, 230, 460])
        self.w.record_attempt(DOCK, 20.0)
        self.w.record_attempt(DOCK, 90.0)
        self.feed([461, 462], start=100.0)     # clean: recovered
        self.feed([700, 940], start=200.0)     # a fresh incident
        self.assertTrue(self.w.should_recover(DOCK, now=300.0))

    def test_forget_starts_clean(self):
        self.feed([0, 230, 460])
        self.w.record_attempt(DOCK, 20.0)
        self.w.forget(DOCK)
        self.assertEqual(self.feed([9000]), [False])
        self.assertFalse(self.w.glitching(DOCK))


class SinkStallDeciding(unittest.TestCase):
    def setUp(self):
        self.w = health.SinkStallWatch(cooldown_seconds=60, max_attempts=2)

    def test_an_advancing_pointer_is_healthy(self):
        self.w.observe(SINK, True, 1000, "RUNNING", 0.0)
        stalled = self.w.observe(SINK, True, 49000, "RUNNING", 10.0)
        self.assertFalse(stalled)

    def test_the_first_observation_only_baselines(self):
        """A sink that just started gets a full window before being
        judged, even though its pointer has no history."""
        self.assertFalse(self.w.observe(SINK, True, 1000, "RUNNING", 0.0))
        self.assertFalse(self.w.should_recover(SINK, now=0.0))

    def test_a_static_pointer_while_running_is_a_stall(self):
        self.w.observe(SINK, True, 1000, "RUNNING", 0.0)
        stalled = self.w.observe(SINK, True, 1000, "RUNNING", 10.0)
        self.assertTrue(stalled)
        self.assertTrue(self.w.should_recover(SINK, now=10.0))

    def test_an_idle_sink_holding_still_is_not_a_stall(self):
        """Suspended and idle sinks legitimately stop consuming; cycling
        one would wake hardware nobody is playing to."""
        self.w.observe(SINK, False, 1000, "SETUP", 0.0)
        stalled = self.w.observe(SINK, False, 1000, "SETUP", 10.0)
        self.assertFalse(stalled)

    def test_xrun_state_is_an_immediate_stall(self):
        stalled = self.w.observe(SINK, True, 1000, "XRUN", 0.0)
        self.assertTrue(stalled)

    def test_a_missing_proc_entry_is_not_ours_to_judge(self):
        self.w.observe(SINK, True, None, None, 0.0)
        stalled = self.w.observe(SINK, True, None, None, 10.0)
        self.assertFalse(stalled)

    def test_a_recycle_does_not_feed_its_own_reset_back_as_a_stall(self):
        """suspend/resume resets hw_ptr to zero; comparing the next
        window against the pre-recycle value would misread recovery."""
        self.w.observe(SINK, True, 1000, "RUNNING", 0.0)
        self.w.observe(SINK, True, 1000, "RUNNING", 10.0)
        self.w.record_attempt(SINK, 10.0)
        self.assertFalse(self.w.observe(SINK, True, 0, "RUNNING", 20.0))

    def test_attempts_are_capped_and_cooled_down(self):
        self.w.observe(SINK, True, 1000, "RUNNING", 0.0)
        self.w.observe(SINK, True, 1000, "RUNNING", 10.0)
        self.w.record_attempt(SINK, 10.0)
        self.w.observe(SINK, True, 500, "RUNNING", 20.0)
        self.w.observe(SINK, True, 500, "RUNNING", 30.0)
        self.assertFalse(self.w.should_recover(SINK, now=30.0))   # cooling
        self.assertTrue(self.w.should_recover(SINK, now=80.0))
        self.w.record_attempt(SINK, 80.0)
        self.w.observe(SINK, True, 500, "RUNNING", 150.0)
        self.w.observe(SINK, True, 500, "RUNNING", 160.0)
        self.assertFalse(self.w.should_recover(SINK, now=300.0))  # spent

    def test_movement_refills_the_budget(self):
        self.w.observe(SINK, True, 1000, "RUNNING", 0.0)
        self.w.observe(SINK, True, 1000, "RUNNING", 10.0)
        self.w.record_attempt(SINK, 10.0)
        self.w.record_attempt(SINK, 80.0)
        self.w.observe(SINK, True, 2000, "RUNNING", 90.0)   # recovered
        self.w.observe(SINK, True, 50000, "RUNNING", 100.0)
        self.w.observe(SINK, True, 50000, "RUNNING", 110.0)  # new stall
        self.assertTrue(self.w.should_recover(SINK, now=200.0))


if __name__ == "__main__":
    unittest.main()
