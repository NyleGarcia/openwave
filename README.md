# OpenWave

Linux control application for **Elgato Wave** audio devices — the **Wave XLR** microphone interface and the **Wave:3** microphone. A reverse-engineered replacement for Elgato Wave Link, built with GTK4 + Adwaita.

## Supported devices

| Device | USB ID | Controls |
|---|---|---|
| Wave XLR | `0fd9:007d` | Gain, mute, headphone volume, low impedance mode |
| Wave XLR MK.2 | `0fd9:00a6` | as the Wave XLR — it enumerates as "Elgato XLR Dock" and speaks the same vendor protocol |
| Wave:3 | `0fd9:0070` | Gain, mute, headphone volume, monitor mix |

## Features

- **Mixing matrix** — user-defined mixes as columns, sources as rows. Each cell
  is how much of that source the mix receives; each source row carries a trim
  applying everywhere. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- **Sources** — an application matched by name (several names per row, so one
  fader can cover every game or two music players), or a hardware capture
  device such as a headset microphone. One row may be the catch-all for
  anything unmatched.
- **Per-mix output** — every mix chooses its own output device, or none at all
  for a mix that exists only to be captured. A mix keeps playing when the
  window is closed.
- **Microphone controls** — Gain, mute (syncs with hardware button)
- **Headphone controls** — Volume (syncs with hardware knob), low impedance mode
- **Hardware sync** — 10 Hz polling keeps the app in sync with physical controls
- **System integration** — Mute and HP volume sync bidirectionally with PipeWire/ALSA
- **Audio capture fix** — Background daemon (systemd or runit) prevents the firmware race condition where mic goes silent
- **System tray** — Runs in background with tray icon, mute from tray menu
- **First-run setup** — Configures udev permissions and audio service automatically

## How it works

Wave devices use USB Class control transfers on endpoint 0 for device configuration. On Linux, `snd-usb-audio` normally blocks these transfers because `wIndex=0x3300` routes through interface 0 (owned by the audio driver). OpenWave uses `wIndex=0x3303` instead — the firmware only checks the `0x33` prefix, while the kernel sees interface 3 (unclaimed) and lets the transfer through. No driver detach needed, audio is never interrupted.

Both devices speak the same vendor protocol (`bRequest` 0x85 read / 0x05 write) but with different config layouts: the Wave XLR uses a 34-byte block (gain uint16 @0, mute @4, HP volume int16 Q8.8 @9, knob mode @14, low-Z @33), the Wave:3 a 16-byte block (gain uint16 Q8.8 dB @0, mute @4, HP volume int16 Q8.8 @7, monitor mix uint16 Q8.8 percent @10, dial mode @12 — 1=gain, 2=headphones, 3=mix). Per-model constants live in `wavexlr/profiles.py`; `python3 -m wavexlr.probe` (`dump` / `watch` / `poke`) verifies a device against its profile and helps map new fields. The device services vendor transfers from only one process at a time, so quit OpenWave before probing.

## Install

One-liner — detects Arch, Debian/Ubuntu, Fedora, openSUSE, or Void; installs deps and OpenWave:

```bash
curl -fsSL https://raw.githubusercontent.com/rikkichy/openwave/main/install.sh | sh
```

Or from a checkout:

```bash
git clone https://github.com/rikkichy/openwave.git
cd openwave
./install.sh                  # default PREFIX=/usr/local
PREFIX=/usr ./install.sh      # for packaging-style layout
```

Uninstall:

```bash
sudo make -C /path/to/openwave uninstall PREFIX=/usr/local
```

### Requirements

- Python 3.10+
- GTK4, libadwaita
- PipeWire (for audio capture fix)
- libusb 1.0

## Usage

```bash
openwave            # if installed via install.sh / PKGBUILD
python3 -m wavexlr  # from a checkout, no install needed
```

On first launch, OpenWave will prompt to set up USB permissions (via polkit) and install the audio service.

### Init systems

OpenWave detects your init system at runtime:

- **systemd** — the GUI installs a user unit at `~/.config/systemd/user/openwave.service` and enables it. No root needed for install or status checks.
- **runit** (Artix, Void, Devuan-runit) — the GUI cannot install the system service itself (writing to `/etc/sv` requires root). Create a `wavexlr-audio` service directory at `/etc/sv/wavexlr-audio/` whose `run` script execs `python3 -m wavexlr.daemon` as your user (typically via `chpst -u`), then enable it with `ln -s /etc/sv/wavexlr-audio /var/service/`.

  Status detection from the non-root GUI uses `sv check`; on stock Void the supervise FIFO is mode 0700, so OpenWave falls back to scanning `/proc` for the daemon process.

- **other** (macOS, Windows, no init detected) — the capture-fix section is disabled.

### Start hidden in tray
```bash
python3 -m wavexlr --hide
```

### Start at login
```bash
cp /usr/share/openwave/openwave-autostart.desktop ~/.config/autostart/
```

### Desktop entry
Copy `wavexlr.desktop` to `~/.local/share/applications/` for app launcher integration.

## Architecture

```
wavexlr/
  device.py   — USB backend (raw libusb via ctypes, wIndex=0x3303 trick)
  profiles.py — per-model protocol constants and capabilities
  probe.py    — vendor protocol verification CLI (dump / watch / poke)
  app.py      — GTK4/Adwaita UI with 10Hz polling
  tray.py     — StatusNotifierItem tray icon via D-Bus
  audio.py    — PipeWire capture keepalive (fixes firmware race condition)
  daemon.py   — Systemd service entry point
  setup.py    — First-run udev + systemd setup, generated PipeWire config
  mixer.py    — The router: intake sinks, per-cell loopbacks, stream claiming
  mixes.py    — Mix definitions store (~/.config/openwave/mixdefs.json)
  sources.py  — Source definitions store (~/.config/openwave/sources.json)
  mixmatrix.py  — The sources x mixes grid widget
  mixdialog.py  — Create/rename a mix
  sourcedialog.py — Add or edit a source
  meter.py    — Level metering via pw-cat
  service.py  — systemd/runit unit management
  paths.py    — Install-prefix resolution
```

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains the routing model: why an
application's audio is moved rather than copied, how trim and send compose, and
why every stream gets exactly one owner.

## Development

Run from a checkout without installing:

```bash
python3 -m wavexlr
```

The tests cover the backend — matching, the stores, state migration, the
generated config and the device scaling. They import neither GTK nor a running
PipeWire, so they need no display, no audio server and no hardware:

```bash
python3 -m unittest discover -s tests -t .
```

The GUI, the USB protocol and the routing itself are not unit-tested; those are
verified against real hardware. `python3 -m wavexlr.probe dump` reads a
connected device and is the fastest way to check a profile — quit OpenWave
first, since the firmware serves one process at a time.

## Credits

USB protocol reverse-engineered from the macOS Wave Link application using Frida. Inspired by [GoXLR-on-Linux/goxlr-utility](https://github.com/GoXLR-on-Linux/goxlr-utility).

## License

MIT
