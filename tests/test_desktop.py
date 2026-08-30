"""The app drawer entry and starting at login.

Both are files written into the user's own directories, and both are silently
wrong in the same way: an Exec line that does not resolve produces an entry
that is present, looks right, and does nothing when clicked -- or worse, does
nothing at login, where nobody is watching.
"""

import os
import tempfile
import unittest

from wavexlr import desktop


class TempHome(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = {k: os.environ.get(k)
                     for k in ("XDG_DATA_HOME", "XDG_CONFIG_HOME",
                               "XDG_DATA_DIRS")}
        os.environ["XDG_DATA_HOME"] = os.path.join(self._tmp.name, "data")
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self._tmp.name, "config")
        # Point system data dirs into the sandbox too, so a machine that has
        # OpenWave's icons installed for real cannot leak into the tests.
        os.environ["XDG_DATA_DIRS"] = os.path.join(self._tmp.name, "sysdata")

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()


class MenuEntry(TempHome):
    def test_it_lands_where_the_drawer_looks(self):
        desktop.ensure_menu_entry()
        self.assertTrue(os.path.isfile(desktop.menu_entry_path()))
        self.assertTrue(
            desktop.menu_entry_path().endswith("/applications/openwave.desktop"))

    def test_it_is_a_valid_entry(self):
        desktop.ensure_menu_entry()
        body = open(desktop.menu_entry_path()).read()
        self.assertTrue(body.startswith("[Desktop Entry]"))
        for key in ("Type=Application", "Name=", "Exec=", "Icon=",
                    "Categories="):
            self.assertIn(key, body)

    def test_it_groups_the_window_with_its_tray_icon(self):
        """Without StartupWMClass the shell shows two entries for one app."""
        desktop.ensure_menu_entry()
        self.assertIn("StartupWMClass=com.github.openwave",
                      open(desktop.menu_entry_path()).read())

    def test_writing_it_twice_changes_nothing(self):
        self.assertTrue(desktop.ensure_menu_entry())
        self.assertFalse(desktop.ensure_menu_entry())

    def test_a_stale_entry_is_rewritten(self):
        """An entry written from a checkout that has since been installed
        would otherwise keep launching the path that no longer exists."""
        desktop.ensure_menu_entry()
        path = desktop.menu_entry_path()
        with open(path, "w") as handle:
            handle.write("[Desktop Entry]\nExec=/gone/openwave\n")
        self.assertTrue(desktop.ensure_menu_entry())
        self.assertIn(desktop.launch_command(), open(path).read())


class LaunchCommand(unittest.TestCase):
    def test_it_is_absolute(self):
        """A desktop file inherits no working directory, so a relative
        command resolves at login only by accident."""
        command = desktop.launch_command()
        first = command.split()[0]
        self.assertTrue(first.startswith("/") or first == "env", command)

    def test_a_checkout_carries_its_own_path(self):
        import shutil
        real = shutil.which
        shutil.which = lambda _name: None
        try:
            command = desktop.launch_command()
        finally:
            shutil.which = real
        self.assertIn("PYTHONPATH=", command)
        self.assertIn("-m wavexlr", command)
        checkout = command.split("PYTHONPATH=")[1].split()[0]
        self.assertTrue(os.path.isdir(os.path.join(checkout, "wavexlr")))


class Autostart(TempHome):
    def test_off_by_default(self):
        self.assertEqual(desktop.autostart_state(), (False, False))
        self.assertFalse(os.path.exists(desktop.autostart_path()))

    def test_turning_it_on_writes_the_file(self):
        self.assertEqual(desktop.set_autostart(True), (True, False))
        self.assertTrue(os.path.isfile(desktop.autostart_path()))
        self.assertEqual(desktop.autostart_state(), (True, False))

    def test_turning_it_off_removes_it(self):
        desktop.set_autostart(True)
        desktop.set_autostart(False)
        self.assertFalse(os.path.exists(desktop.autostart_path()))
        self.assertEqual(desktop.autostart_state(), (False, False))

    def test_turning_it_off_twice_is_not_an_error(self):
        desktop.set_autostart(False)
        desktop.set_autostart(False)

    def test_starting_hidden_passes_the_flag(self):
        desktop.set_autostart(True, hidden=True)
        self.assertIn("--hide", open(desktop.autostart_path()).read())
        self.assertEqual(desktop.autostart_state(), (True, True))

    def test_the_flag_can_be_taken_away_again(self):
        desktop.set_autostart(True, hidden=True)
        desktop.set_autostart(True, hidden=False)
        self.assertNotIn("--hide", open(desktop.autostart_path()).read())
        self.assertEqual(desktop.autostart_state(), (True, False))

    def test_the_desktop_environment_is_told_it_is_enabled(self):
        desktop.set_autostart(True)
        self.assertIn("X-GNOME-Autostart-enabled=true",
                      open(desktop.autostart_path()).read())

    def test_an_entry_disabled_by_the_desktop_reads_as_off(self):
        """GNOME's own tweaks disable an entry in place rather than deleting
        it, and a switch that ignored that would lie about the next login."""
        desktop.set_autostart(True)
        path = desktop.autostart_path()
        body = open(path).read().replace(
            "X-GNOME-Autostart-enabled=true",
            "X-GNOME-Autostart-enabled=false")
        with open(path, "w") as handle:
            handle.write(body)
        self.assertEqual(desktop.autostart_state()[0], False)

    def test_autostart_and_the_menu_entry_are_separate_files(self):
        """Removing one must never remove the other."""
        desktop.ensure_menu_entry()
        desktop.set_autostart(True)
        self.assertNotEqual(desktop.menu_entry_path(),
                            desktop.autostart_path())
        desktop.set_autostart(False)
        self.assertTrue(os.path.isfile(desktop.menu_entry_path()))


class Identity(TempHome):
    """The generated entry and the packaged wavexlr.desktop must agree.

    They drifted once: the generated one kept the pre-rename tagline and a
    generic icon, so the app introduced itself differently depending on how
    it was installed.
    """

    def _packaged(self):
        path = os.path.join(os.path.dirname(desktop.__file__),
                            "..", "wavexlr.desktop")
        entries = {}
        for line in open(path):
            if "=" in line:
                key, value = line.strip().split("=", 1)
                entries[key] = value
        return entries

    def test_name_comment_categories_match_the_packaged_entry(self):
        desktop.ensure_menu_entry()
        generated = open(desktop.menu_entry_path()).read()
        packaged = self._packaged()
        for key in ("Name", "Comment", "Categories", "StartupWMClass"):
            self.assertIn(f"{key}={packaged[key]}", generated)

    def test_icon_falls_back_when_the_themed_one_is_absent(self):
        self.assertEqual(desktop.icon_name(), desktop.ICON_FALLBACK)

    def test_icon_is_the_themed_one_when_installed(self):
        icon = os.path.join(os.environ["XDG_DATA_DIRS"], "icons", "hicolor",
                            "scalable", "apps", "openwave.svg")
        os.makedirs(os.path.dirname(icon))
        open(icon, "w").close()
        self.assertEqual(desktop.icon_name(), desktop.ICON)


if __name__ == "__main__":
    unittest.main()
