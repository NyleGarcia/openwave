"""Finding the ALSA controls by name instead of trusting their numbers.

numid=4/5/6 hold on the hardware in hand, but numids are not promised across
firmware revisions or models. The control names vary only in their
product-string prefix -- the XLR Dock says "PCM Playback Volume" and
"Mic Capture Switch" -- so the suffix is what gets matched. Discovery is fed
the amixer output verbatim; a card it cannot read falls back to the
historical numbers, so nothing that works today can regress.
"""

import unittest
from unittest import mock

from wavexlr import device

# Captured from a real 0fd9:00a6 XLR Dock, 2026-08-30.
DOCK = """\
numid=3,iface=MIXER,name='PCM Playback Switch'
  ; type=BOOLEAN,access=rw------,values=1
  : values=on
numid=4,iface=MIXER,name='PCM Playback Volume'
  ; type=INTEGER,access=rw---R--,values=1,min=0,max=120,step=0
  : values=73
numid=5,iface=MIXER,name='Mic Capture Switch'
  ; type=BOOLEAN,access=rw------,values=1
  : values=on
numid=6,iface=MIXER,name='Mic Capture Volume'
  ; type=INTEGER,access=rw---R--,values=1,min=0,max=150,step=0
  : values=150
numid=2,iface=PCM,name='Capture Channel Map'
  ; type=INTEGER,access=r--v-R--,values=1,min=0,max=36,step=0
  : values=2
"""

# The same controls under different numids and another product prefix.
SHUFFLED = """\
numid=11,iface=MIXER,name='Wave XLR Mk3 Capture Switch'
  ; type=BOOLEAN,access=rw------,values=1
  : values=on
numid=12,iface=MIXER,name='Wave XLR Mk3 Capture Volume'
  ; type=INTEGER,access=rw---R--,values=1,min=0,max=200,step=0
  : values=0
numid=13,iface=MIXER,name='Wave XLR Mk3 Playback Volume'
  ; type=INTEGER,access=rw---R--,values=1,min=0,max=99,step=0
  : values=0
"""


class Discovery(unittest.TestCase):
    def setUp(self):
        device._ALSA_NUMIDS.clear()
        device._ALSA_CTL_MAX.clear()
        self.addCleanup(device._ALSA_NUMIDS.clear)
        self.addCleanup(device._ALSA_CTL_MAX.clear)

    def with_amixer(self, output):
        ctx = mock.patch.object(
            device, "_amixer",
            lambda card, *args: output if args == ("contents",) else "")
        ctx.start()
        self.addCleanup(ctx.stop)

    def test_the_dock_resolves_to_its_historical_numids(self):
        """The capture above is real hardware; discovery must agree with the
        numbers that were hardcoded, or discovery is what regresses."""
        self.with_amixer(DOCK)
        self.assertEqual(device._numid("c", "mute"), 5)
        self.assertEqual(device._numid("c", "gain"), 6)
        self.assertEqual(device._numid("c", "hp_vol"), 4)

    def test_moved_controls_are_still_found(self):
        """The case the fallback cannot cover: a firmware that renumbers."""
        self.with_amixer(SHUFFLED)
        self.assertEqual(device._numid("c", "mute"), 11)
        self.assertEqual(device._numid("c", "gain"), 12)
        self.assertEqual(device._numid("c", "hp_vol"), 13)

    def test_discovery_also_learns_the_maxima(self):
        """One pass feeds the max cache, so the clamp needs no second call."""
        self.with_amixer(SHUFFLED)
        device._discover_numids("c")
        self.assertEqual(device._ALSA_CTL_MAX[("c", 12)], 200)
        self.assertEqual(device._ALSA_CTL_MAX[("c", 13)], 99)

    def test_an_unreadable_card_falls_back(self):
        self.with_amixer("")
        self.assertEqual(device._numid("c", "mute"), 5)
        self.assertEqual(device._numid("c", "gain"), 6)
        self.assertEqual(device._numid("c", "hp_vol"), 4)

    def test_a_non_mixer_interface_is_not_a_control(self):
        """'Capture Channel Map' is iface=PCM; matching it would be wrong
        even though nothing in the suffix table collides with it today."""
        self.with_amixer(DOCK)
        found = device._discover_numids("c")
        self.assertNotIn(2, found.values())

    def test_the_scan_runs_once_per_card(self):
        calls = []
        with mock.patch.object(
                device, "_amixer",
                lambda card, *a: calls.append(card) or DOCK):
            device._numid("c", "mute")
            device._numid("c", "gain")
            device._numid("c", "hp_vol")
        self.assertEqual(calls, ["c"])


if __name__ == "__main__":
    unittest.main()
