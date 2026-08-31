# OpenWave

[![Tests](https://github.com/NyleGarcia/openwave/actions/workflows/tests.yml/badge.svg)](https://github.com/NyleGarcia/openwave/actions/workflows/tests.yml)
[![Release](https://github.com/NyleGarcia/openwave/actions/workflows/release.yml/badge.svg)](https://github.com/NyleGarcia/openwave/actions/workflows/release.yml)
[![AUR version](https://img.shields.io/aur/version/openwave)](https://aur.archlinux.org/packages/openwave)

**The audio mixing matrix for Linux.** Per-app mixes with per-mix outputs, plus native control of **Elgato Wave** hardware — the **Wave XLR** interface (original and MK.2/XLR Dock) and the **Wave:3** microphone. A reverse-engineered replacement for Elgato Wave Link, built with GTK4 + Adwaita.

![OpenWave](docs/screenshot.png)

Sources are rows, mixes are columns, and each cell is how much of that source
the mix receives. Above: an XLR Dock and an Arctis headset microphone grouped
so only one is live at a time — the muted one is the red row — feeding a
Personal Mix monitored on the headset, a Chat Mix published as a capture source
for voice apps, and a Record Mix routed nowhere but still recordable.

## Supported devices

| Device | USB ID | Status | Controls |
|---|---|---|---|
| Wave XLR | `0fd9:007d` | 🟢 supported | Gain, mute, headphone volume, low impedance mode, **48 V phantom power**, knob-mode readout |
| Wave XLR MK.2 / XLR Dock | `0fd9:00a6` | 🟢 supported | as the Wave XLR — it enumerates as "Elgato XLR Dock" and speaks the same vendor protocol, verified on hardware |
| Wave:3 | `0fd9:0070` | 🟢 supported | Gain, mute, headphone volume, monitor mix, 3-way dial mode |
| Wave XLR MK.2 (`00b6` revision) | `0fd9:00b6` | ⚪ not yet | a different MK.2 revision — UAC2, different control scheme, decoded by [CryoByte33/openwave](https://github.com/CryoByte33/openwave); deferred for lack of hardware |

Details, per-control status and protocol notes:
[docs/hardware-support.md](docs/hardware-support.md). Have an untested device?
See [Reporting problems](#reporting-problems).

Phantom power lives at offset 6 of the Wave XLR config block (`0x01` on,
`0x00` off), found by diffing the block across a toggle and confirmed against
the device's own +48V indicator. The Dock has no front-panel button for it at
all, so on that hardware the app is the only way to switch it.

## Features

### Mixing matrix

- **Sources × mixes grid** — user-defined mixes as columns, sources as rows.
  Each cell is how much of that source the mix receives; each source row
  carries a trim applying everywhere, with a per-cell mute on every send.
  See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- **Sources** — an application matched by name (several names per row, so one
  fader can cover every game or two music players), or a hardware capture
  device such as a headset microphone. One row may be the catch-all for
  anything unmatched. Every source and mix gets a pickable icon.
- **Live level meters** — every source row meters its own audio, every mix
  header meters what the mix carries, and a row waiting for its application
  says so instead of sitting silent.
- **Mix master sliders** — each column header carries its mix's master
  volume. The slider follows outside movers too: whoever turns a master —
  pavucontrol, a media key, a scene — the header shows it within seconds.
- **Per-mix output** — every mix chooses its own output device from a menu:
  *Automatic* (labelled with the device it resolved to), any live sink, or
  *Not monitored* for a mix that exists only to be captured. A remembered
  device that is currently absent stays selectable, marked "(unavailable)".
  A mix keeps playing when the window is closed.
- **Every mix is a microphone** — each mix is also published as a capture
  source, so a voice app or OBS can select it as an input.
- **Levels survive a reboot** — PipeWire recreates the mix sinks at unity on
  every start and WirePlumber does not restore them. OpenWave remembers the
  masters itself and puts them back. See [Mix levels and reboots](#mix-levels-and-reboots).
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
  pre-matched to the usual applications, with System as the catch-all. An
  empty mix says it carries nothing rather than looking broken.
- **Scenes** — every trim, send, mute, output, master and device setting
  saved under a name and recalled as one gesture, from the header-bar menu
  or the session bus. A scene sets levels on the matrix that exists — it
  never creates or deletes rows or columns, and one that names things since
  removed applies what still matches. The gain lock wins over a scene's
  gain.
- **Remote control** — mixes, source trims, microphone groups and scenes
  are drivable from outside the window over the session bus. See
  [Remote control](#remote-control).

### Device control

- **Microphone** — gain in dB with a **gain lock** (lock the slider so a stray
  drag cannot blow out a dialled-in level), mute synced with the hardware
  button, 48 V phantom power.
- **Headphones** — volume synced with the hardware knob, low impedance mode,
  and on a Wave:3 a **monitor mix** slider (mic/PC crossfade).
- **Knob readout** — shows what the physical dial currently controls.
- **Device info** — firmware version, protocol API version and serial number,
  read from the device itself.
- **Hardware sync** — 10 Hz polling keeps the app in sync with physical
  controls; slider drags are throttled so the hardware tracks the drag
  instead of hearing about it after.
- **System integration** — mute and volumes sync bidirectionally with
  PipeWire/ALSA, with ALSA controls discovered by name so a firmware revision
  that renumbers them cannot break it.
- **Per-microphone effects** — each capture row carries a DSP popover:
  low cut (80/120 Hz), three-band tone EQ, alignment delay (sync your
  mic to desktop audio in a recording), and forced mono. Built from
  PipeWire's own filter-chain — nothing to install, no process running
  while everything is neutral. Gate, compressor and AI noise removal are
  on the roadmap as optional plugins.
- **Hotplug** — a Wave plugged in after launch is picked up automatically.
- **Multiple devices** — every connected Wave is opened, polled and
  ALSA-synced at once, two of the same model included (told apart by USB
  bus address and serial). A Device dropdown appears in the sidebar when
  more than one is connected; the capture-fix daemon pins each device's
  stream; scenes record hardware per serial; and the tray reports muted if
  any device's hardware mute is down.

### Reliability

- **Audio capture fix** — a background daemon (systemd or runit) prevents the
  firmware race where the microphone goes silent, with a byte-flow watchdog
  for a keepalive that wedged without dying. The sidebar warns when the
  service is missing and can install — or uninstall — it in place.
- **Stalled capture recovery** — a Wave replugged while the system runs can
  come back claiming to be healthy while delivering no frames; OpenWave
  detects that and reopens it. See [Stalled capture](#stalled-capture).
- **Corrupt config survival** — an unreadable mix store is preserved as
  `mixdefs.json.corrupt` and replaced with the defaults, so a bad write never
  leaves the app with no mixes at all.
- **Icon-theme resilience** — icon names Breeze lacks are substituted at draw
  time, so the UI survives a non-default theme without rewriting your config.

### Desktop integration

- **System tray** — StatusNotifier icon with mute from the menu; the tooltip
  distinguishes hardware mute, matrix mute, and both. On a desktop with no
  tray host (stock GNOME), OpenWave shows its window instead of hiding into
  nothing.
- **App drawer, start at login, start in the tray** — all handled by switches
  in the app; no files to copy. See
  [App drawer, starting at login, starting in the tray](#app-drawer-starting-at-login-starting-in-the-tray).
- **Responsive layout** — the device pane is a collapsible sidebar; the window
  remembers its geometry (and a hidden window cannot clobber it).
- **First-run setup** — configures udev permissions and the audio service
  automatically, via polkit.

## How OpenWave compares

Two other projects live in the same space: [openxlr](https://github.com/emaspa/openxlr),
a C#/.NET control suite for Elgato XLR interfaces on Linux, and Elgato's own
**Wave Link** on Windows/macOS. Roughly: openxlr covers more XLR hardware
variants (the Wave XLR Pro, the `00b6` MK.2) and adds host-side DSP and an
OpenDeck plugin; Wave Link has the deepest effects stack and no Linux
version; OpenWave covers the Wave:3, models mixing as one sources × mixes
matrix with scenes and microphone groups, and runs on plain Python +
PyGObject with no runtime to install. Pick openxlr for its hardware and
DSP; pick OpenWave for the matrix.

## How it works

Wave devices use USB Class control transfers on endpoint 0 for device configuration. On Linux, `snd-usb-audio` normally blocks these transfers because `wIndex=0x3300` routes through interface 0 (owned by the audio driver). OpenWave uses `wIndex=0x3303` instead — the firmware only checks the `0x33` prefix, while the kernel sees interface 3 (unclaimed) and lets the transfer through. No driver detach needed, audio is never interrupted.

All supported devices speak the same vendor protocol (`bRequest` 0x85 read / 0x05 write) but with different config layouts; per-model constants live in `wavexlr/profiles.py`, and the full register maps are documented in [docs/protocol.md](docs/protocol.md). `python3 -m wavexlr.probe` (`dump` / `watch` / `poke`) verifies a device against its profile and helps map new fields. The device services vendor transfers from only one process at a time, so quit OpenWave before probing.

The mixing half is a router built from ordinary PipeWire objects — null sinks
and `pw-loopback` children, no custom audio code.
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains the routing model: why an
application's audio is moved rather than copied, how trim and send compose, and
why every stream gets exactly one owner.

## Install

Tagged releases on the [Releases page](../../releases) carry ready-made
objects: a `.deb` for Debian/Ubuntu (`sudo apt install ./openwave_*.deb`),
a source tarball, and checksums.

### One-liner

Detects Arch, Debian/Ubuntu, Fedora, openSUSE, or Void; installs deps and OpenWave:

```bash
curl -fsSL https://raw.githubusercontent.com/NyleGarcia/openwave/main/install.sh | sh
```

### Arch Linux

```bash
yay -S openwave   # AUR
```

### From a checkout

```bash
git clone https://github.com/NyleGarcia/openwave.git
cd openwave
./install.sh                  # default PREFIX=/usr/local
PREFIX=/usr ./install.sh      # for packaging-style layout
```

### Nix

The repo is a flake exposing `packages.<system>.openwave` (also `default`)
for `x86_64-linux` and `aarch64-linux`:

```bash
nix run github:NyleGarcia/openwave
nix profile install github:NyleGarcia/openwave
```

On NixOS, the package ships the udev rules the first-run setup would
otherwise write (pkexec cannot write to the read-only store), so consume
them declaratively:

```nix
services.udev.packages = [ openwave ];
```

### Bazzite / Fedora Atomic

Immutable images change what "install" means — see
[docs/install-bazzite.md](docs/install-bazzite.md). Short version: run
from a checkout (no build step, first-run setup works as-is since `/etc`
is writable and the service is a user unit), layering only PyGObject if
the image lacks it.

### Flatpak (experimental)

A manifest lives at
[`packaging/flatpak/com.github.openwave.yml`](packaging/flatpak/com.github.openwave.yml):

```bash
flatpak install --user flathub org.flatpak.Builder org.gnome.Platform//48 org.gnome.Sdk//48
flatpak run org.flatpak.Builder --user --install --force-clean build-dir \
    packaging/flatpak/com.github.openwave.yml
```

Know the limits before choosing it: the sandbox cannot install udev rules
(grant USB access once from a native install or by hand — see
[docs/hardware-support.md](docs/hardware-support.md)) and cannot run the
first-run setup or the capture-fix daemon, so those stay native. The
manifest bundles the `pw-*`/`wpctl`/`amixer` tools the mixer shells out
to and drives the host PipeWire through its socket. Prefer a native
package where one exists.

### Uninstall

```bash
sudo make -C /path/to/openwave uninstall PREFIX=/usr/local
```

### Requirements

- Python 3.10+
- GTK4, libadwaita
- PipeWire (for audio capture fix)
- libusb 1.0
- python-xlib *(optional)* — friendlier app names in the Add Source picker
  for X11/XWayland apps that report a generic PipeWire name ("ALSA plug-in
  [java]"); without it those rows fall back to the raw name

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

### App drawer, starting at login, starting in the tray

All three are handled by the app; none needs a file copied by hand.

The **app drawer entry** is written on launch to
`~/.local/share/applications/openwave.desktop`, and rewritten if it goes
stale — the `Exec` line records where OpenWave was found, so an entry written
from a checkout that has since been installed properly would otherwise keep
launching a path that no longer exists.

**Start at login** and **Start in the tray** are switches in the sidebar,
under *Startup*. Starting in the tray needs a tray: GNOME ships no
StatusNotifier host, so without an AppIndicator extension OpenWave shows its
window instead of hiding into nothing, and closing the window quits rather
than making it disappear. They write `~/.config/autostart/openwave.desktop`, adding
`--hide` for the tray-only case. Turning autostart off deletes that file;
the drawer entry is a separate file and is left alone.

Both are user-level files needing no privileges, which is why neither is part
of the first-run setup that asks for a password. An entry a desktop
environment has disabled in place (GNOME Tweaks does this rather than
deleting it) reads back as off, so the switch cannot claim a login behaviour
that will not happen.

`--hide` still works on its own for a one-off:

```bash
python3 -m wavexlr --hide
```

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
  'set-cell-level', 'toggle-cell-mute', 'source-groups', 'snapshot',
  'apply-scene', 'save-scene', 'delete-scene', 'scenes'],)
```

| Action | Parameter | Does |
|---|---|---|
| `switch-group` | `s` group name | Hands a microphone group to its next member |
| `set-source-level` | `(sd)` id, 0–1 | Sets a source's trim |
| `toggle-source-mute` | `s` id | Flips a source's mute, group rules included |
| `set-cell-level` | `(ssd)` source, mix, 0–1 | Sets one send — how much of a source a single mix receives |
| `toggle-cell-mute` | `(ss)` source, mix | Flips one cell's mute |
| `apply-scene` | `s` scene id | Recalls a scene; entries naming things that are gone are skipped |
| `save-scene` | `s` name | Captures the current levels under that name |
| `delete-scene` | `s` scene id | Removes a scene |
| `source-groups` | — | State: group names worth switching between |
| `scenes` | — | State: `{scene id: name}` as JSON |
| `snapshot` | — | State: every source, mix and cell, as JSON |

The two read-only actions publish their answer as action *state* rather than
returning it: `Activate` has no reply, but `Describe` reads state and `Changed`
fires when it moves, so a reader can both poll and subscribe. Activate first to
refresh, then describe.

`snapshot` is one action rather than one per field because a remote control
draws all of it on a single button, and reading it piecemeal would let the
parts disagree mid-read. It reports **every** cell, including the ones at zero:
a caller cannot otherwise tell a send that is down from one that does not
exist.

Everything goes through the window rather than the config files. `Mixer` holds
the same dict the window holds and rewrites `sources.json` whole on every save,
so a caller writing that file directly is overwritten the next time a fader
moves — and a cell written straight to `mixes.json` is undone even faster,
because `send × trim` is re-applied on every reconcile.

[**openwave-streamdeck**](https://github.com/NyleGarcia/openwave-streamdeck) is
a Stream Deck plugin built on this.

## Mix levels and reboots

A mix master is a plain PipeWire sink volume, and the mix sinks are
`context.objects` in PipeWire's configuration — recreated by the daemon on
every start, at unity, with no memory. WirePlumber does not restore them
either, because they are neither streams nor devices it manages. Left alone,
every mix master silently resets to 100% at each boot, including anything set
from a control surface.

OpenWave remembers them in `mixes.json` under `volumes` and applies them once
the sinks exist. It records what the master is actually set to rather than
only what its own window did, because anything may move it — a Stream Deck,
`pavucontrol`, a media key — and whoever moved it, that is the value that
should come back.

Observation is gated on the restore having happened, and that gate is the
point rather than an optimisation. At boot the sinks exist at unity before
OpenWave does; an observation landing first would persist that unity and
destroy the value it exists to protect — silently, exactly once per boot,
which is indistinguishable from never having saved anything.

## Stalled capture

A Wave replugged while the system is running enumerates, gets its ALSA card
and its PipeWire node, reports itself unmuted at full gain with phantom power
on — and produces nothing. Not quiet audio: no frames.

The distinction that makes it detectable is **silence versus no data**. A live
analogue input always delivers a noise floor; a stalled one delivers nothing,
so a meter reading it blocks forever on its first read. That is the signal
OpenWave watches, and it is why a level threshold would be the wrong test — a
muted microphone in a quiet room is legitimately near zero and must not be
"recovered".

The remedy is to make ALSA close and reopen the device, which cycling the
card's profile through `off` and back does. Restarting the capture keepalive
does not: it exists to *prevent* the race and cannot clear one that has
already happened.

Three things it deliberately will not do. It will not act on a device that is
simply absent — unplugged is not broken, and cycling a card for a device
someone has just removed fights the person who removed it. It will not act on
silence reported by a dead meter subprocess, whose silence says something
about `pw-cat` and nothing about the hardware. And it gives up after two
attempts, because cycling a card is disruptive and a device that is genuinely
broken should be left alone to be noticed rather than reopened every minute
forever. Unplugging resets that budget, since replugging is how the stall
arises in the first place.

## Configuration files

| File | Holds |
|---|---|
| `~/.config/openwave/mixdefs.json` | mix identity: name, icon, sink, description |
| `~/.config/openwave/sources.json` | source identity, bindings, trim |
| `~/.config/openwave/mixes.json` | per-cell levels, per-mix outputs, mix master volumes |
| `~/.config/openwave/ui-state.json` | window geometry, gain lock |
| `~/.config/pipewire/pipewire.conf.d/52-openwave-mixes.conf` | generated: one null sink per mix |
| `~/.config/wireplumber/wireplumber.conf.d/51-openwave-wave-xlr.conf` | generated: keeps the Wave from being suspended |

These are OpenWave's own state, not an interface: values poked into them from
outside are overwritten on the next save or reconcile. Use
[Remote control](#remote-control) instead.

## Reporting problems

Open an issue on the [issue tracker](../../issues) and attach a diagnostics
bundle: **Export diagnostics** in the sidebar, or

```bash
python3 -m wavexlr.diag
```

The bundle carries versions, device state, service and PipeWire status —
and no config contents or app names unless you pass `--full`. Prefer the
in-app button when OpenWave is running: the firmware serves vendor
transfers to one process at a time, so the CLI cannot read a device the
app holds open. For deeper protocol digging there is
`python3 -m wavexlr.probe dump` (quit OpenWave first, tray icon included). If you have a Wave device that is not in
the [supported table](#supported-devices) — the `0fd9:00b6` MK.2 revision
especially — a `probe dump`, plus `probe watch` output while you move each
physical control, is exactly what adding support needs.

## Repository layout

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
  recovery.py — Stalled-capture detection and card-profile cycling
  scheduler.py — Slider-write throttling seam
  icons.py    — Draw-time icon substitution for themes missing names
  desktop.py  — App drawer and autostart entries
  wmnames.py  — Friendly app names via X11/XWayland (optional)
  service.py  — systemd/runit unit management
  paths.py    — Install-prefix resolution
docs/         — architecture, hardware support, protocol, comparison
tests/        — unit suite (no GTK, no PipeWire, no hardware needed)
```

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full picture. The short
version — run from a checkout without installing:

```bash
python3 -m wavexlr
```

The tests cover the backend — matching, the stores, state migration, the
generated config, the device scaling, the mixer's reconcile decisions
(against a fake PipeWire), stall recovery, the tray, the desktop entries and
the throttler. They import neither GTK nor a running PipeWire, so they need
no display, no audio server and no hardware:

```bash
python3 -m unittest discover -s tests -t .
```

The GUI, the USB protocol and the routing itself are not unit-tested; those are
verified against real hardware. `python3 -m wavexlr.probe dump` reads a
connected device and is the fastest way to check a profile — quit OpenWave
first, since the firmware serves one process at a time.

## Credits

USB protocol reverse-engineered from the macOS Wave Link application using Frida. Inspired by [GoXLR-on-Linux/goxlr-utility](https://github.com/GoXLR-on-Linux/goxlr-utility).

The shape of this documentation — the hardware-support matrix, the protocol
reference, the per-distro install sections, the AI disclosure — is modeled on
[emaspa/openxlr](https://github.com/emaspa/openxlr), the sibling project for
Elgato's XLR interfaces, whose README sets the bar for this niche.

Several ideas and two modules are ported from
[CryoByte33/openwave](https://github.com/CryoByte33/openwave), a sibling fork:
the friendly-app-name resolution (`wmnames.py` and the generic-name rules),
the slider `Throttler` and its scheduler seam, ALSA control discovery by
name suffix, the hotplug reconnect loop, and the duplicate-source picker
guard. cryobyte33's fork also decoded the `0fd9:00b6` Wave XLR MK.2 revision —
a UAC2 device with a different control scheme from the `0fd9:00a6` XLR Dock
this tree supports — which this tree defers only for lack of that hardware.

## AI disclosure

Parts of this project — code and documentation — were developed with AI
assistance. Everything that touches hardware is verified by a human against
real devices: the protocol findings in [docs/protocol.md](docs/protocol.md)
come from `probe` sessions on live hardware, not from a model's guess, and
the support claims in [docs/hardware-support.md](docs/hardware-support.md)
state explicitly what has been verified on hardware and what has not.

## License

MIT
