"""Enumerating every supported Wave, including two of the same model.

connect() with no arguments opens the first device of a vid:pid, which
made a second identical unit invisible; scan() walks the bus and reports
each one with its (bus, addr) so callers can open them individually.
"""

import unittest
from unittest import mock

from wavexlr import device
from wavexlr.profiles import PROFILES, WAVE3, WAVE_XLR_MK2


def _fake_bus(entries):
    """A _each_usb_device that visits the given (vid, pid, bus, addr)."""
    def each(visit):
        for vid, pid, bus, addr in entries:
            visit(vid, pid, bus, addr, object())
    return each


class Scan(unittest.TestCase):
    def test_two_identical_models_are_two_results(self):
        bus = _fake_bus([
            (WAVE_XLR_MK2.vid, WAVE_XLR_MK2.pid, 1, 5),
            (WAVE_XLR_MK2.vid, WAVE_XLR_MK2.pid, 3, 2),
        ])
        with mock.patch.object(device, "_each_usb_device", bus):
            found = device.scan()
        self.assertEqual(len(found), 2)
        self.assertEqual({(b, a) for _p, b, a in found}, {(1, 5), (3, 2)})

    def test_unsupported_hardware_is_ignored(self):
        bus = _fake_bus([
            (0x046D, 0x0825, 1, 4),          # some webcam
            (WAVE3.vid, 0x9999, 1, 6),        # right vendor, unknown product
            (WAVE3.vid, WAVE3.pid, 2, 3),
        ])
        with mock.patch.object(device, "_each_usb_device", bus):
            found = device.scan()
        self.assertEqual([(p.key, b, a) for p, b, a in found],
                         [("wave3", 2, 3)])

    def test_results_come_in_bus_order(self):
        entries = [(p.vid, p.pid, bus, addr)
                   for (p, bus, addr) in zip(PROFILES, (9, 1, 5), (9, 1, 5))]
        with mock.patch.object(device, "_each_usb_device", _fake_bus(entries)):
            found = device.scan()
        self.assertEqual([(b, a) for _p, b, a in found],
                         [(1, 1), (5, 5), (9, 9)])


class ClosedHandle(unittest.TestCase):
    """A transfer after disconnect must be an error, never a crash.

    get_all() releases the device lock between transfers, and unplug
    handling can disconnect in that gap. libusb does not NULL-check its
    handle argument, so before the guard this was a segfault that took the
    whole app down the moment a device was unplugged mid-poll.
    """

    def test_read_on_a_cleared_handle_raises(self):
        dev = device.WaveDevice()
        dev.profile = WAVE_XLR_MK2
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            dev._ctrl_read(0x0000, 34)

    def test_write_on_a_cleared_handle_raises(self):
        dev = device.WaveDevice()
        dev.profile = WAVE_XLR_MK2
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            dev._ctrl_write(0x0000, b"\x00" * 34)


if __name__ == "__main__":
    unittest.main()
