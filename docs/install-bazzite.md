# Installing on Bazzite (and other Fedora Atomic systems)

Bazzite's `/usr` is an immutable OSTree image, which changes what "install"
means: `make install` cannot put the module into the system
`site-packages`, and layering packages with `rpm-ostree` costs a reboot per
change. What still works exactly as designed: `/etc` is writable, so the
first-run udev setup succeeds; systemd user units live in your home, so the
capture-fix service installs normally; and PipeWire, WirePlumber and their
CLI tools ship in the base image.

Written for Bazzite; applies equally to Silverblue, Kinoite and other
uBlue images. Not yet CI-tested on an Atomic system — reports welcome
([Reporting problems](../README.md#reporting-problems)).

## Recommended: run from a checkout

OpenWave has no build step and no Python dependencies beyond PyGObject, so
the checkout IS the install.

1. Check what the base image already has:

   ```bash
   rpm -q python3-gobject gtk4 libadwaita libusb1 alsa-utils pipewire-utils
   ```

   On Bazzite's GNOME images everything is usually present; KDE images may
   lack `python3-gobject` or `libadwaita`. Layer whatever is missing
   (one reboot):

   ```bash
   rpm-ostree install python3-gobject libadwaita
   systemctl reboot
   ```

2. Clone and run:

   ```bash
   git clone https://github.com/NyleGarcia/openwave.git ~/openwave
   cd ~/openwave && python3 -m wavexlr
   ```

3. Let the first-run setup do its work. Both halves function on Atomic:
   the udev rules go to `/etc/udev/rules.d/` (writable, via pkexec) and
   the audio service is a **user** unit under `~/.config/systemd/user/`.

4. The app writes its own drawer entry and autostart file on launch, so
   after the first run it behaves like any installed application — the
   `Exec` line records where the checkout lives. Updating is `git pull`.

Do not run OpenWave from inside a distrobox: it needs the host's PipeWire
tools, ALSA cards and raw USB access, and a container adds three seams
that can each fail silently.

## Alternative: Flatpak (experimental)

The [manifest](../packaging/flatpak/com.github.openwave.yml) builds and
installs without touching the OS image — the most Bazzite-native shape —
but the sandbox cannot install udev rules or the capture-fix daemon, so
USB access needs one manual step:

```bash
sudo tee /etc/udev/rules.d/99-openwave.rules >/dev/null <<'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="0fd9", ATTR{idProduct}=="007d", MODE="0666"
SUBSYSTEM=="usb", ATTR{idVendor}=="0fd9", ATTR{idProduct}=="00a6", MODE="0666"
SUBSYSTEM=="usb", ATTR{idVendor}=="0fd9", ATTR{idProduct}=="0070", MODE="0666"
EOF
sudo udevadm control --reload && sudo udevadm trigger
```

Then build as the README's [Flatpak section](../README.md#flatpak-experimental)
describes. Without the daemon, hardware that hits the UAC1 firmware race
has no keepalive — if your microphone goes silent after the machine sits
idle, that is what the native daemon exists for, and the checkout install
above is the answer.

## What not to do

- `sudo make install` — the default `PREFIX=/usr/local` half-works
  (OSTree maps it to `/var/usrlocal`), but `SITEPKG` resolves into the
  read-only `/usr/lib/python3.*/site-packages` and the install fails
  there. The checkout install needs none of it.
- `rpm-ostree install` of OpenWave itself — there is no RPM; layering is
  only for the PyGObject/libadwaita dependencies.
