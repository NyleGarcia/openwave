"""Desktop integration: the app drawer entry and starting at login.

Both are freedesktop .desktop files in the user's own directories, so neither
needs privileges and neither belongs in the first-run setup dialog that asks
for a password. The menu entry is written on every launch if it is missing or
stale; autostart is a choice, so it is only ever written when asked for.
"""

import os
import shutil
import sys

APP_ID = "openwave"
NAME = "OpenWave"
COMMENT = "Elgato Wave control for Linux"
ICON = "audio-input-microphone"
# One main category only. AudioVideo plus Settings validates, but
# desktop-file-validate warns it may list the app twice in the menu,
# and a mixer belongs under Audio rather than under system settings.
CATEGORIES = "AudioVideo;Audio;Mixer;"


def _data_home():
    return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")


def _config_home():
    return os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")


def menu_entry_path():
    return os.path.join(_data_home(), "applications", f"{APP_ID}.desktop")


def autostart_path():
    return os.path.join(_config_home(), "autostart", f"{APP_ID}.desktop")


def launch_command():
    """How to start OpenWave again, from however it was started this time.

    `openwave` on PATH when there is one, because that survives the checkout
    moving. Otherwise the running interpreter and the module, with an absolute
    path: a desktop file has no working directory to inherit, so a bare
    "python3 -m wavexlr" would only work when the checkout happens to be the
    session's cwd, which it never is at login.
    """
    installed = shutil.which(APP_ID)
    if installed:
        return installed
    # PYTHONPATH rather than a flag or a Path= key: a desktop file inherits no
    # working directory, so "python3 -m wavexlr" would only resolve when the
    # checkout happened to be the session's cwd, which at login it never is.
    checkout = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return f"env PYTHONPATH={checkout} {sys.executable} -m wavexlr"


def _render(exec_command, autostart=False):
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={NAME}",
        f"Comment={COMMENT}",
        f"Exec={exec_command}",
        f"Icon={ICON}",
        f"Categories={CATEGORIES}",
        "Terminal=false",
        # Without this the tray icon and the window are two entries in the
        # dock, because the shell has no way to tell they are one app.
        f"StartupWMClass=com.github.openwave",
        "X-GNOME-UsesNotifications=true",
    ]
    if autostart:
        # Honoured by GNOME and KDE; ignored elsewhere, where the file simply
        # being present is what enables it.
        lines.append("X-GNOME-Autostart-enabled=true")
    return "\n".join(lines) + "\n"


def _write(path, contents):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as handle:
        handle.write(contents)
    os.replace(tmp, path)


def ensure_menu_entry():
    """Put OpenWave in the app drawer, rewriting a stale entry.

    Rewritten rather than only created, because the Exec line embeds where
    OpenWave was found: an entry written from a checkout that has since been
    installed properly would otherwise keep launching the old path forever.
    """
    path = menu_entry_path()
    wanted = _render(launch_command())
    try:
        if os.path.exists(path) and open(path).read() == wanted:
            return False
        _write(path, wanted)
    except OSError:
        return False
    return True


def autostart_state():
    """(enabled, hidden) for starting at login."""
    path = autostart_path()
    try:
        contents = open(path).read()
    except OSError:
        return False, False
    enabled = "X-GNOME-Autostart-enabled=false" not in contents
    hidden = False
    for line in contents.splitlines():
        if line.startswith("Exec="):
            hidden = "--hide" in line
    return enabled, hidden


def set_autostart(enabled, hidden=False):
    """Turn starting at login on or off. Returns the new (enabled, hidden)."""
    path = autostart_path()
    if not enabled:
        try:
            os.remove(path)
        except OSError:
            pass
        return False, hidden
    command = launch_command() + (" --hide" if hidden else "")
    try:
        _write(path, _render(command, autostart=True))
    except OSError:
        return autostart_state()
    return True, hidden
