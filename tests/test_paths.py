"""Finding the files the Makefile installed, whatever PREFIX it was given.

site-packages is chosen by the interpreter and is absolute, so it does not move
when PREFIX does. Installing with the Makefile's own default PREFIX=/usr/local
on a distribution whose site-packages is /usr/lib/python3.N/site-packages
therefore splits the install: the module under /usr, share/openwave under
/usr/local. Walking up from the module never reaches /usr/local, so the
WirePlumber rule was reported missing by first-run setup on an install that had
in fact just written it -- and setup aborted before the rule and the service
were in place.
"""

import os
import unittest
from unittest import mock

from wavexlr import paths

RULE = ("wireplumber", "51-openwave-wave-xlr.conf")


def tree(root, *relative_dirs):
    """Create directories under root and return root."""
    for d in relative_dirs:
        os.makedirs(os.path.join(root, d), exist_ok=True)
    return root


def touch(path, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write("")
    os.chmod(path, mode)
    return path


class Layouts(unittest.TestCase):
    """Each shape of install the Makefile and a checkout can produce."""

    def setUp(self):
        ctx = mock.patch.object(paths, "_FALLBACK_PREFIXES", ())
        ctx.start()
        self.addCleanup(ctx.stop)

        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def at(self, *parts):
        return os.path.join(self.root, *parts)

    def module_at(self, *parts):
        d = self.at(*parts)
        os.makedirs(d, exist_ok=True)
        ctx = mock.patch.object(paths, "_MODULE_DIR", d)
        ctx.start()
        self.addCleanup(ctx.stop)
        return d

    def fallbacks(self, *prefixes):
        ctx = mock.patch.object(paths, "_FALLBACK_PREFIXES", prefixes)
        ctx.start()
        self.addCleanup(ctx.stop)

    def test_a_checkout_keeps_its_data_beside_the_package(self):
        self.module_at("checkout", "wavexlr")
        want = touch(self.at("checkout", *RULE))
        self.assertEqual(paths.data_file(*RULE), want)

    def test_a_matching_prefix_is_found_by_walking_up(self):
        """PREFIX=/usr with site-packages under /usr: an ancestor holds it."""
        self.module_at("usr", "lib", "python3.14", "site-packages", "wavexlr")
        want = touch(self.at("usr", "share", "openwave", *RULE))
        self.assertEqual(paths.data_file(*RULE), want)

    def test_a_split_install_is_still_found(self):
        """The regression: PREFIX=/usr/local, site-packages under /usr.

        No ancestor of the module is the data's prefix, so without the
        fallback list this returned None and first-run setup aborted.
        """
        self.module_at("usr", "lib", "python3.14", "site-packages", "wavexlr")
        self.fallbacks(self.at("usr", "local"))
        want = touch(self.at("usr", "local", "share", "openwave", *RULE))
        self.assertEqual(paths.data_file(*RULE), want)

    def test_the_module_own_prefix_wins_over_a_fallback(self):
        """Two installs present: the one this module belongs to is the one."""
        self.module_at("usr", "lib", "python3.14", "site-packages", "wavexlr")
        self.fallbacks(self.at("usr", "local"))
        mine = touch(self.at("usr", "share", "openwave", *RULE))
        touch(self.at("usr", "local", "share", "openwave", *RULE))
        self.assertEqual(paths.data_file(*RULE), mine)

    def test_genuinely_missing_is_still_None(self):
        """The caller reports it; it must not be masked by a stale install."""
        self.module_at("usr", "lib", "python3.14", "site-packages", "wavexlr")
        self.assertIsNone(paths.data_file(*RULE))


class Launchers(Layouts):
    """bin/ follows PREFIX too, so bin_file splits the same way."""

    def test_a_launcher_under_the_own_prefix_is_found(self):
        self.module_at("usr", "lib", "python3.14", "site-packages", "wavexlr")
        want = touch(self.at("usr", "bin", "openwave-daemon"), 0o755)
        self.assertEqual(paths.bin_file("openwave-daemon"), want)

    def test_a_split_install_finds_its_launcher(self):
        self.module_at("usr", "lib", "python3.14", "site-packages", "wavexlr")
        self.fallbacks(self.at("usr", "local"))
        want = touch(self.at("usr", "local", "bin", "openwave-daemon"), 0o755)
        self.assertEqual(paths.bin_file("openwave-daemon"), want)

    def test_a_non_executable_file_is_not_a_launcher(self):
        """A service unit pointing at it would fail at start, not here."""
        self.module_at("usr", "lib", "python3.14", "site-packages", "wavexlr")
        touch(self.at("usr", "bin", "openwave-daemon"), 0o644)
        self.assertIsNone(paths.bin_file("openwave-daemon"))


if __name__ == "__main__":
    unittest.main()
