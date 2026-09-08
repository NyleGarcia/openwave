"""The udev rules and the installed-check must both cover every profile.

Twice now a device was added to PROFILES while one of the two stayed
hardcoded to an older subset: 0070 was missing from udev_installed() (the
flake documents it), then 00a6 was. The symptom is first-run setup
re-prompting forever on the device the check skipped. Both now derive from
PROFILES; these tests pin that a third recurrence cannot compile quietly.
"""

import os
import tempfile
import unittest
from unittest import mock

from wavexlr import setup
from wavexlr.profiles import PROFILES


class TestUdevRules(unittest.TestCase):
    def test_every_profile_has_a_rule(self):
        text = "\n".join(setup.UDEV_RULES)
        for p in PROFILES:
            self.assertIn(f'ATTR{{idProduct}}=="{p.pid:04x}"', text)
            self.assertIn(f'ATTR{{idVendor}}=="{p.vid:04x}"', text)

    def test_one_rule_per_profile(self):
        self.assertEqual(len(setup.UDEV_RULES), len(PROFILES))

    def test_rules_carry_no_inline_comment(self):
        # udev only ignores lines *starting* with '#'; a trailing comment
        # would be part of the rule and break it.
        for rule in setup.UDEV_RULES:
            self.assertNotIn("#", rule)


class TestInstalledScript(unittest.TestCase):
    """What the pkexec script is allowed to do as root.

    It once ended by walking /dev/bus/usb and chmod 0666-ing every node whose
    ID_VENDOR_ID was 0fd9, to spare the user a replug. That is every Elgato
    device on the bus — a Stream Deck, a Cam Link, a key light — handed
    world-writable raw USB access by a rule set that is otherwise careful to
    name three product ids. The trigger lines already re-apply MODE="0666" to
    connected supported devices, so the loop bought nothing it was entitled to.
    """

    def _script(self):
        captured = {}

        def fake_run(argv, **kwargs):
            with open(argv[1]) as f:
                captured["text"] = f.read()
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(setup.subprocess, "run", fake_run), \
             mock.patch.object(setup.os, "unlink", lambda _p: None):
            setup.install_udev()
        return captured["text"]

    def test_grants_no_permission_outside_the_rules(self):
        script = self._script()
        self.assertNotIn("chmod", script)
        self.assertNotIn("/dev/bus/usb", script)
        self.assertNotIn("ID_VENDOR_ID", script)

    def test_every_profile_is_triggered(self):
        script = self._script()
        for p in PROFILES:
            self.assertIn(
                f"--attr-match=idVendor={p.vid:04x} "
                f"--attr-match=idProduct={p.pid:04x}",
                script,
                f"{p.display_name} is ruled but never triggered",
            )

    def test_one_trigger_per_profile(self):
        self.assertEqual(self._script().count("udevadm trigger"), len(PROFILES))


class TestUdevInstalled(unittest.TestCase):
    def _check(self, content):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "99-openwave.rules")
            if content is not None:
                with open(path, "w") as f:
                    f.write(content)
            with mock.patch.object(setup, "UDEV_PATH", path), \
                 mock.patch.object(setup, "UDEV_PATH_OLD",
                                   os.path.join(d, "absent")):
                return setup.udev_installed()

    def test_complete_rules_pass(self):
        self.assertTrue(self._check("\n".join(setup.UDEV_RULES)))

    def test_any_missing_profile_fails(self):
        for skipped in PROFILES:
            content = "\n".join(
                r for p, r in zip(PROFILES, setup.UDEV_RULES) if p is not skipped
            )
            self.assertFalse(
                self._check(content),
                f"udev_installed() ignored a missing {skipped.display_name}",
            )

    def test_no_file_fails(self):
        self.assertFalse(self._check(None))


if __name__ == "__main__":
    unittest.main()
