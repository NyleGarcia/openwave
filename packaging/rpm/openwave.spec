# Built by release.yml, which substitutes @VERSION@ from the tag and
# feeds the release tarball in as Source0. noarch: pure Python.
#
# The module tree deliberately does NOT go into %{python3_sitelib}: this
# rpm is built on the release runner, not on Fedora, and a noarch package
# hardcoding one Fedora release's python3.X path would break on the next.
# Instead the tree lives under /usr/share/openwave and the two launchers
# carry PYTHONPATH — the same shape the Nix package uses for the same
# reason.

Name:           openwave
Version:        @VERSION@
Release:        1%{?dist}
Summary:        The audio mixing matrix for Linux
License:        MIT
URL:            https://github.com/NyleGarcia/openwave
BuildArch:      noarch
# @SRCVER@ is the tarball's own version string, which for a dispatch
# dry-run contains characters (0.0.0-dev.<sha>) rpm's Version cannot.
Source0:        openwave-@SRCVER@.tar.gz

Requires:       python3 >= 3.10
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       libusb1
Requires:       pipewire-utils
Requires:       wireplumber
Requires:       alsa-utils
Recommends:     python3-xlib

%description
Per-app mixes with per-mix outputs, plus native control of Elgato Wave
hardware - the Wave XLR interface (original and MK.2/XLR Dock) and the
Wave:3 microphone. A reverse-engineered replacement for Elgato Wave
Link, built with GTK4 and libadwaita on PipeWire.

%prep
%autosetup -n openwave-@SRCVER@

%install
make install DESTDIR=%{buildroot} PREFIX=/usr PYTHON=python3 \
    SITEPKG=/usr/share/openwave/site-packages
# The generated launchers assume the module is importable; put the
# install's own tree on the path.
sed -i 's|exec python3|exec env PYTHONPATH=/usr/share/openwave/site-packages python3|' \
    %{buildroot}/usr/bin/openwave %{buildroot}/usr/bin/openwave-daemon

%files
%license LICENSE
/usr/bin/openwave
/usr/bin/openwave-daemon
/usr/share/openwave/
/usr/share/applications/openwave.desktop
/usr/share/metainfo/com.github.openwave.metainfo.xml
/usr/share/icons/hicolor/scalable/apps/openwave.svg
/usr/share/icons/hicolor/symbolic/apps/openwave-symbolic.svg
/usr/share/icons/hicolor/symbolic/apps/openwave-muted-symbolic.svg
/usr/share/icons/hicolor/symbolic/apps/openwave-attention-symbolic.svg
/usr/share/doc/openwave/
/usr/share/licenses/openwave/

%changelog
# Release notes live in CHANGELOG.md and the GitHub Releases page.
