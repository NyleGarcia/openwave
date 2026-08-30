"""Substituting an icon name the active theme does not have.

GTK draws the broken-image glyph rather than falling back, and Breeze -- what a
Plasma session hands a GTK application -- lacks several of the Adwaita names the
UI uses. The Browser row's web-browser-symbolic is the one that showed it.

icon_name is stored in sources.json and mixes.json, so the substitution belongs
at draw time: a configuration written under one theme has to render under
another, and the user's recorded choice must survive the trip back.
"""

import unittest
from unittest import mock

from wavexlr import icons


class FakeTheme:
    """Stands in for the display's icon theme, which tests have no display for."""

    def __init__(self, *available):
        self.available = set(available)

    def has_icon(self, name):
        return name in self.available


class Resolving(unittest.TestCase):
    def setUp(self):
        icons._cache.clear()
        self.addCleanup(icons._cache.clear)

    def theme(self, *available):
        ctx = mock.patch.object(icons, "_theme", lambda: FakeTheme(*available))
        ctx.start()
        self.addCleanup(ctx.stop)

    def test_a_name_the_theme_has_is_left_alone(self):
        """Adwaita must be entirely unaffected by any of this."""
        self.theme("web-browser-symbolic")
        self.assertEqual(icons.resolve("web-browser-symbolic"),
                         "web-browser-symbolic")

    def test_a_missing_name_becomes_one_the_theme_has(self):
        """The regression: Breeze has no web-browser-symbolic."""
        self.theme("internet-web-browser-symbolic")
        self.assertEqual(icons.resolve("web-browser-symbolic"),
                         "internet-web-browser-symbolic")

    def test_it_keeps_looking_past_an_absent_alternative(self):
        self.theme("globe-symbolic")
        self.assertEqual(icons.resolve("web-browser-symbolic"),
                         "globe-symbolic")

    def test_an_unknown_name_is_returned_untouched(self):
        """No table for it means no better guess than what was asked for."""
        self.theme()
        self.assertEqual(icons.resolve("nonesuch-symbolic"), "nonesuch-symbolic")

    def test_no_display_changes_nothing(self):
        """Headless -- a daemon importing the module must not crash on it."""
        ctx = mock.patch.object(icons, "_theme", lambda: None)
        ctx.start()
        self.addCleanup(ctx.stop)
        self.assertEqual(icons.resolve("web-browser-symbolic"),
                         "web-browser-symbolic")

    def test_an_empty_name_is_not_looked_up(self):
        self.theme()
        self.assertEqual(icons.resolve(""), "")
        self.assertIsNone(icons.resolve(None))

    def test_every_alternative_is_itself_a_plausible_icon_name(self):
        """A typo here would silently become the broken glyph it replaces."""
        for preferred, alternatives in icons._ALTERNATIVES.items():
            self.assertTrue(preferred.endswith("-symbolic"), preferred)
            self.assertTrue(alternatives, f"{preferred} has no alternatives")
            for alternative in alternatives:
                self.assertTrue(alternative.endswith("-symbolic"), alternative)
                self.assertNotEqual(alternative, preferred)


if __name__ == "__main__":
    unittest.main()
