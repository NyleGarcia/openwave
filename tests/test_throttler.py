"""Pacing the device sliders: leading, periodic, trailing — and nothing extra.

Three sliders used to carry three copy-pasted 200 ms trailing-only debounces,
so the hardware heard about a drag only after it stopped. The Throttler sends
the first value immediately, then at most one per interval while the drag
continues, then the final position. Its clock is injected, so the pacing is
tested with a hand-cranked scheduler — no GLib, no main loop.
"""

import unittest

from wavexlr.scheduler import Throttler


class FakeScheduler:
    """Timers fire only when tick() is called; time passes by hand."""

    def __init__(self):
        self._timers = {}
        self._next = 0

    def call_every(self, interval_s, fn):
        handle = self._next
        self._next += 1
        self._timers[handle] = fn
        return handle

    def cancel(self, handle):
        self._timers.pop(handle, None)

    def tick(self):
        for handle, fn in list(self._timers.items()):
            if not fn():
                self._timers.pop(handle, None)


class Pacing(unittest.TestCase):
    def setUp(self):
        self.sched = FakeScheduler()
        self.throttle = Throttler(self.sched, 0.08)
        self.sent = []

    def push(self, name, value):
        self.throttle.push(name, value, lambda v, n=name: self.sent.append((n, v)))

    def test_the_first_value_goes_out_immediately(self):
        """A drag's first movement reaches the device with no delay at all —
        the whole point over the trailing-only debounce it replaces."""
        self.push("gain", 10)
        self.assertEqual(self.sent, [("gain", 10)])

    def test_a_drag_is_paced_not_replayed(self):
        """Many values inside one interval collapse to the latest."""
        self.push("gain", 10)
        for v in (11, 12, 13, 14):
            self.push("gain", v)
        self.sched.tick()
        self.assertEqual(self.sent, [("gain", 10), ("gain", 14)])

    def test_the_final_position_is_never_dropped(self):
        """The trailing edge: where the slider stopped is what must stick."""
        self.push("gain", 10)
        self.push("gain", 55)
        self.sched.tick()          # sends 55
        self.sched.tick()          # idle — timer stops
        self.push("gain", 56)      # a new drag leads again
        self.assertEqual(self.sent[-1], ("gain", 56))

    def test_an_idle_control_stops_ticking(self):
        self.push("gain", 10)
        self.sched.tick()
        self.sched.tick()
        self.assertEqual(self.sched._timers, {})

    def test_sliders_pace_independently(self):
        """Dragging gain must not delay or reorder headphone sends."""
        self.push("gain", 10)
        self.push("hp", -20)
        self.assertEqual(self.sent, [("gain", 10), ("hp", -20)])
        self.push("gain", 11)
        self.push("hp", -21)
        self.sched.tick()
        self.assertIn(("gain", 11), self.sent)
        self.assertIn(("hp", -21), self.sent)

    def test_cancel_all_stops_every_timer(self):
        self.push("gain", 10)
        self.push("hp", -20)
        self.throttle.cancel_all()
        self.assertEqual(self.sched._timers, {})


if __name__ == "__main__":
    unittest.main()
