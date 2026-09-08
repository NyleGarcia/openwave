#!/bin/sh
# OpenWave installer — works on Arch, Debian/Ubuntu, Fedora, openSUSE, Void.
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/rikkichy/openwave/main/install.sh | sh
#   ./install.sh                  (from a checkout)
#   PREFIX=/usr ./install.sh      (default is /usr/local)
set -eu

REPO="https://github.com/rikkichy/openwave.git"
PREFIX="${PREFIX:-/usr/local}"

msg()  { printf '\033[1;34m::\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarn:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# --- privilege escalation -----------------------------------------------------
if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
elif command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
elif command -v doas >/dev/null 2>&1; then
    SUDO="doas"
elif command -v pkexec >/dev/null 2>&1; then
    SUDO="pkexec"
else
    die "need root, sudo, doas, or pkexec"
fi

# --- package manager detection ------------------------------------------------
# PKGS is what the app cannot start or route without: the GTK stack, libusb,
# the pw-* tools, the pactl the mixer moves masters with, the amixer/aplay
# device.py finds ALSA controls through, wpctl, and the pkexec first-run setup
# shells to. OPT_PKGS is wanted but not fatal — and, because one unknown name
# fails the whole transaction, it is installed a package at a time so a distro
# that spells one of them differently still gets everything else.
if command -v pacman >/dev/null 2>&1; then
    PM=pacman
    PKGS="python python-gobject gtk4 libadwaita libusb pipewire wireplumber libpulse alsa-utils polkit"
    OPT_PKGS="adwaita-icon-theme swh-plugins python-xlib"
    INSTALL_CMD="pacman -S --needed --noconfirm $PKGS"
    INSTALL_ONE="pacman -S --needed --noconfirm"
elif command -v apt-get >/dev/null 2>&1; then
    PM=apt
    PKGS="python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 libadwaita-1-0 libusb-1.0-0 pipewire pipewire-bin wireplumber pulseaudio-utils alsa-utils"
    OPT_PKGS="adwaita-icon-theme swh-plugins python3-xlib policykit-1 pkexec"
    INSTALL_CMD="apt-get update && apt-get install -y $PKGS"
    INSTALL_ONE="apt-get install -y"
elif command -v dnf >/dev/null 2>&1; then
    PM=dnf
    PKGS="python3 python3-gobject gtk4 libadwaita libusb1 pipewire pipewire-utils wireplumber pulseaudio-utils alsa-utils polkit"
    OPT_PKGS="adwaita-icon-theme ladspa-swh-plugins python3-xlib"
    INSTALL_CMD="dnf install -y $PKGS"
    INSTALL_ONE="dnf install -y"
elif command -v zypper >/dev/null 2>&1; then
    PM=zypper
    PKGS="python3 python3-gobject gtk4 libadwaita-1-0 typelib-1_0-Adw-1 libusb-1_0-0 pipewire pipewire-tools wireplumber pulseaudio-utils alsa-utils polkit"
    OPT_PKGS="adwaita-icon-theme swh-plugins python3-xlib"
    INSTALL_CMD="zypper install -y $PKGS"
    INSTALL_ONE="zypper install -y"
elif command -v xbps-install >/dev/null 2>&1; then
    PM=xbps
    PKGS="python3-gobject gtk4 libadwaita libusb pipewire wireplumber pulseaudio-utils alsa-utils polkit"
    OPT_PKGS="adwaita-icon-theme swh-plugins python3-Xlib"
    INSTALL_CMD="xbps-install -Sy $PKGS"
    INSTALL_ONE="xbps-install -Sy"
else
    die "no supported package manager (pacman / apt / dnf / zypper / xbps)"
fi

msg "package manager: $PM"
msg "installing: $PKGS"
$SUDO sh -c "$INSTALL_CMD"

# Best-effort. A name this distro does not use costs that one package, not the
# install: without swh-plugins the gate and compressor are unavailable and the
# rest of the DSP chain still works; without python-xlib the Add Source picker
# shows PipeWire's names instead of friendly ones.
for pkg in $OPT_PKGS; do
    $SUDO sh -c "$INSTALL_ONE $pkg" >/dev/null 2>&1 \
        || msg "optional package unavailable, skipping: $pkg"
done

# --- fetch source -------------------------------------------------------------
if [ -f Makefile ] && [ -d wavexlr ] && [ -f wavexlr.desktop ]; then
    SRC="$PWD"
    msg "using checkout: $SRC"
else
    command -v git >/dev/null 2>&1 || die "git not found"
    SRC="$(mktemp -d)/openwave"
    msg "cloning $REPO"
    git clone --depth 1 "$REPO" "$SRC"
fi

# --- install ------------------------------------------------------------------
msg "installing to $PREFIX"
$SUDO make -C "$SRC" install PREFIX="$PREFIX"

# refresh desktop database when possible (failures are harmless)
if command -v update-desktop-database >/dev/null 2>&1; then
    $SUDO update-desktop-database -q "$PREFIX/share/applications" 2>/dev/null || true
fi

msg "done — launch with: openwave"
