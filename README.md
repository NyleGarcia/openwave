# OpenWave

Linux control application for **Elgato Wave** audio devices — the **Wave XLR** microphone interface and the **Wave:3** microphone. A reverse-engineered replacement for Elgato Wave Link, built with GTK4 + Adwaita.

![OpenWave](docs/screenshot.png)

Sources are rows, mixes are columns, and each cell is how much of that source
the mix receives. Above: an XLR Dock and an Arctis headset microphone grouped
so only one is live at a time — the muted one is the red row — feeding a
Personal Mix monitored on the headset, a Chat Mix published as a capture source
for voice apps, and a Record Mix routed nowhere but still recordable.

## Supported devices

| Device | USB ID | Controls |
|---|---|---|
| Wave XLR | `0fd9:007d` | Gain, mute, headphone volume, low impedance mode, **48 V phantom power** |
| Wave XLR MK.2 | `0fd9:00a6` | as the Wave XLR — it enumerates as "Elgato XLR Dock" and speaks the same vendor protocol |
| Wave:3 | `0fd9:0070` | Gain, mute, headphone volume, monitor mix |

Phantom power lives at offset 6 of the Wave XLR config block (`0x01` on,
`0x00` off), found by diffing the block across a toggle and confirmed against
the device's own +48V indicator. The Dock has no front-panel button for it at
all, so on that hardware the app is the only way to switch it.

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
- **Microphone rows appear by themselves** — every Elgato capture input gets a
  row named after its device ("XLR Dock", "Wave XLR"), so two interfaces
  connected at once are told apart instead of contending for a single
  "microphone" row. They cannot be deleted, only muted.
- **Microphone groups** — drag one microphone row onto another to group them.
  Only one member of a group is live at a time and a single button hands the
  group over to the next, which is what two microphones on one speaker
  actually want; microphones in another group are untouched, so a second
  speaker's microphone stays open. Two mics on one person and one on another
  is two groups.
- **Sensible defaults** — System, Game, Music, Browser and Voice rows ship
  pre-matched to the usual applications, with System as the catch-all.
- **Remote control** — mixes, source trims and microphone groups are drivable
  from outside the window over the session bus. See
  [Remote control](#remote-control).
- **Microphone controls** — Gain, mute (syncs with hardware button), 48 V
  phantom power
- **Headphone controls** — Volume (syncs with hardware knob), low impedance mode
- **Hardware sync** — 10 Hz polling keeps the app in sync with physical controls
- **System integration** — Mute and HP volume sync bidirectionally with PipeWire/ALSA
- **Audio capture fix** — Background daemon (systemd or runit) prevents the firmware race condition where mic goes silent
- **System tray** — Runs in background with tray icon, mute from tray menu
- **First-run setup** — Configures udev permissions and audio service automatically

## How it works

Wave devices use USB Class control transfers on endpoint 0 for device configuration. On Linux, `snd-usb-audio` normally blocks these transfers because `wIndex=0x3300` routes through interface 0 (owned by the audio driver). OpenWave uses `wIndex=0x3303` instead — the firmware only checks the `0x33` prefix, while the kernel sees interface 3 (unclaimed) and lets the transfer through. No driver detach needed, audio is never interrupted.

Both devices speak the same vendor protocol (`bRequest` 0x85 read / 0x05 write) but with different config layouts: the Wave XLR uses a 34-byte block (gain uint16 @0, mute @4, HP volume int16 Q8.8 @9, knob mode @14, low-Z @33), the Wave:3 a 16-byte block (gain uint16 Q8.8 dB @0, mute @4, HP volume int16 Q8.8 @7, monitor mix uint16 Q8.8 percent @10, dial mode @12 — 1=gain, 2=headphones, 3=mix). Per-model constants live in `wavexlr/profiles.py`; `python3 -m wavexlr.probe` (`dump` / `watch` / `poke`) verifies a device against its profile and helps map new fields. The device services vendor transfers from only one process at a time, so quit OpenWave before probing.

## Remote control

OpenWave exports a small set of actions on the session bus, so a control
surface can drive the parts of it that PipeWire alone cannot reach — the
window owns the mixer state, and the GUI holds the only USB handle the
firmware will serve.

There is no protocol of its own: `GApplication` already exports
`org.gtk.Actions` on `com.github.openwave`.

```console
$ gdbus call --session --dest com.github.openwave \
    --object-path /com/github/openwave --method org.gtk.Actions.List
(['switch-group', 'set-source-level', 'toggle-source-mute',
  'source-groups', 'snapshot'],)
```

| Action | Parameter | Does |
|---|---|---|
| `switch-group` | `s` group name | Hands a microphone group to its next member |
| `set-source-level` | `(sd)` id, 0–1 | Sets a source's trim |
| `toggle-source-mute` | `s` id | Flips a source's mute, group rules included |
| `source-groups` | — | State: group names worth switching between |
| `snapshot` | — | State: every source's name, level, mute, group and kind, as JSON |

The two read-only actions publish their answer as action *state* rather than
returning it: `Activate` has no reply, but `Describe` reads state and `Changed`
fires when it moves, so a reader can both poll and subscribe. Activate first to
refresh, then describe.

`snapshot` is one action rather than one per field because a remote control
draws all of it on a single button, and reading it piecemeal would let the
parts disagree mid-read.

Everything goes through the window rather than the config files. `Mixer` holds
the same dict the window holds and rewrites `sources.json` whole on every save,
so a caller writing that file directly is overwritten the next time a fader
moves.

[**openwave-streamdeck**](https://github.com/NyleGarcia/openwave-streamdeck) is
a Stream Deck plugin built on this.

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
  mixmatrix.py  — The sources x mixes grid widget (drag to reorder or group)
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
