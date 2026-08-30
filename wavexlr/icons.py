"""Icon names that survive a theme which is not Adwaita.

The names used throughout the UI are the Adwaita/freedesktop ones. GTK does not
fall back when the active theme lacks one -- it draws the broken-image glyph --
and Breeze, which is what a Plasma session hands a GTK application, is missing
several of them. A fresh install on KDE therefore showed a missing-image icon
where the Browser row's globe belongs.

The substitution has to happen when a name is drawn rather than when it is
chosen, because icon_name is stored: it is written into sources.json and
mixes.json and travels with the configuration. Rewriting the stored name would
fix one machine and corrupt the choice for the next, and would do nothing for a
configuration that already exists -- which is exactly the case that showed the
bug. Resolving at draw time leaves the user's choice intact, follows a theme
change in either direction, and needs no migration.
"""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

# Preferred name -> names to try when the active theme does not have it, best
# first. Every alternative here was checked against Breeze; the preferred name
# is still used whenever the theme has it, so Adwaita is unaffected.
_ALTERNATIVES = {
    "web-browser-symbolic": (
        "internet-web-browser-symbolic",
        "applications-internet-symbolic",
        "globe-symbolic",
    ),
    "input-gaming-symbolic": (
        "applications-games-symbolic",
        "input-gamepad-symbolic",
    ),
    "audio-x-generic-symbolic": (
        "multimedia-player-symbolic",
        "media-optical-audio-symbolic",
    ),
    "list-drag-handle-symbolic": (
        "view-list-symbolic",
        "open-menu-symbolic",
    ),
    "network-transmit-symbolic": (
        "network-wired-symbolic",
        "network-connect-symbolic",
    ),
    "preferences-desktop-multimedia-symbolic": (
        "multimedia-player-symbolic",
        "applications-multimedia-symbolic",
    ),
    "video-display-symbolic": (
        "computer-symbolic",
        "preferences-desktop-display-symbolic",
    ),
}

_cache = {}
_watched = False


def _theme():
    """The display's icon theme, or None when there is no display yet."""
    global _watched
    display = Gdk.Display.get_default()
    if display is None:
        return None
    theme = Gtk.IconTheme.get_for_display(display)
    if theme is not None and not _watched:
        # A theme change makes every earlier answer stale, including the ones
        # that needed no substitution.
        theme.connect("changed", lambda *_: _cache.clear())
        _watched = True
    return theme


def resolve(name):
    """Return name, or the nearest name the active theme actually has.

    Unknown names are returned untouched: a theme we have no table for is not
    improved by guessing, and the broken glyph is at least honest about it.
    """
    if not name:
        return name
    if name in _cache:
        return _cache[name]

    theme = _theme()
    chosen = name
    if theme is not None and not theme.has_icon(name):
        for alternative in _ALTERNATIVES.get(name, ()):
            if theme.has_icon(alternative):
                chosen = alternative
                break

    _cache[name] = chosen
    return chosen
