# Changelog

Notable changes per release. Format follows [Keep a Changelog](https://keepachangelog.com/);
versions are git tags (see [Releases](../../releases)).

## [Unreleased]

### Added
- **Multiple Wave devices at once**: every connected Wave — two of the
  same model included — is opened, polled at 10 Hz and ALSA-synced; a
  Device dropdown in the sidebar picks which one the controls drive, a
  sysfs watch notices units appearing or vanishing while others stay
  connected, the capture-fix daemon keeps one keepalive pin per device,
  scenes record hardware state per serial number, and the tray reports
  muted when any device's hardware mute is down.
- **Mix master sliders and output meters**: every mix column header now
  carries its master volume slider (throttled, and following external
  moves — pavucontrol, media keys, scenes — within a couple of seconds)
  and a live level bar tapping the mix sink's monitor. The bar displays
  amplitude on the same cubic taper the faders use (a 30% fader is 2.7%
  linear amplitude; meter and fader now speak one language) with
  peak-hold ballistics (~140 ms decay half-life).
- **Row mute and hardware mute are one mute**: muting an Elgato capture
  row flips the device's own mute — from a click, the session bus, a
  scene, or a group hand-over, whose losing microphone now goes dark on
  its on-air LED too — and the reverse holds: the physical mute button or
  a system-side mute reaches the matrix row within a second or two. Rows
  pair with USB handles by serial (node-stem fallback when a serial will
  not read), so two units of one model each follow their own row, and
  only state *changes* propagate, so the pair cannot loop.
- **Bazzite / Fedora Atomic install guide** (`docs/install-bazzite.md`):
  checkout-first, what layering is actually needed for, why the sandbox
  and the immutable `/usr` change nothing for udev or the user service,
  and the manual udev step for the Flatpak path.
- **An rpm with every release**: noarch, built by the release workflow,
  module under /usr/share/openwave with PYTHONPATH launchers so one
  package serves every Fedora python. Validated end-to-end by a CI
  dry-run.
- **Flatpak manifest** (`packaging/flatpak/`), built and smoke-tested:
  GNOME 49 runtime, bundling libusb, alsa-lib/utils, the PipeWire tools,
  Lua and WirePlumber's wpctl; the app boots sandboxed with its full
  D-Bus surface. First-run setup knows it is sandboxed and points at the
  host udev step instead of crashing into a pkexec that is not there;
  the capture-fix daemon remains native-only.
- **Devices are discovered while running**: a Wave plugged in
  mid-session — or plugged back in after its row was removed — gets its
  row within seconds instead of on the next launch.
- **Unplugged device rows can be removed**: a connected Elgato row stays
  protected, but once its device is unplugged the row grows a remove
  button. Removing it forgets the auto-offer memory for that device, so
  plugging it back in brings the row back by itself.
- **Scenes**: every trim, send, mute, output, master and device setting
  saved under a name and recalled as one gesture — from a header-bar menu
  or four new session-bus actions (`apply-scene`, `save-scene`,
  `delete-scene`, `scenes`). Partial recall is normal: entries naming
  removed sources or mixes are skipped and reported, and the gain lock
  wins over a scene's gain.
- **Diagnostics export**: one file for bug reports — versions, device
  state, udev/service status, journal tail, OpenWave's PipeWire nodes —
  via an in-app button or `python3 -m wavexlr.diag`. Config contents and
  app names stay out unless `--full`.

### Changed
- The Arch package (PKGBUILD) now builds from this fork's release tarballs
  via `make install`, so it ships the icons and the daemon launcher it had
  drifted away from.
- One tag push now does everything: `release.yml` publishes the Release,
  points the PKGBUILD at the released tarball (pkgver + checksum) and
  pushes to AUR; the overlapping manual `build.yml` flow is gone.
- Documentation overhaul: hardware support matrix, protocol reference,
  contributing guide, this changelog.

### Changed (performance)
- Hardware polling no longer forks two `amixer` processes per device ten
  times a second: the ALSA read-back runs every fifth poll (still well
  under a second of latency for a pavucontrol move), cutting forty
  subprocess spawns per second to eight on a two-device setup.
- Meters integrate 64 ms windows at ~15 Hz instead of 16 ms at 60 Hz —
  over 400 main-loop wakeups a second across seven meters became ~100,
  with no transient a peak meter would show lost. Steady-state CPU with
  two devices and seven meters dropped from ~7% of a core to under 1%.

### Fixed
- The mix meters actually meter the mixes: a record stream targeting a
  sink is silently linked to the default *source* by the session manager,
  so every mix bar was showing the default microphone. The meter streams
  now set `stream.capture.sink`, landing on each mix's own monitor.
- The window no longer opens cramped on first run: with no saved geometry
  it opened at the 820×480 minimum; the default is now 1280×720, and the
  matrix gained the bottom margin its other three sides already had.
- Unplugging a Wave while the app runs no longer crashes it: disconnect
  could slot between two transfers of one poll and hand libusb a NULL
  handle — a segfault, since libusb does not check. The transfer path now
  fails cleanly as "device disconnected", poll ticks no longer stack
  workers against a dying device, overlapping reconnects cannot stack or
  leak USB handles, and closing a handle waits for any in-flight
  transfer. The dead unit is dropped, a remaining device takes over the
  sidebar, and a replug is reopened automatically within seconds
  (verified against a bouncing cable).
- The capture-fix daemon now pins the Wave XLR MK.2 / XLR Dock: its node
  name ("Elgato_XLR_Dock_…") never matched the old "Elgato_Wave_" stem,
  so the Dock silently ran with no keepalive at all.
- The udev rules and the installed-check both derive from the device
  profile list, so a supported device can no longer be missing from either
  (an MK.2/Dock-only machine re-ran first-run setup forever).
- The generated app-drawer entry and the packaged one agree on name,
  tagline, icon and categories; the icon falls back to a stock one on a
  checkout with no installed icons.

## [1.1.0] — 2026-08-30

The mixing matrix release: OpenWave grows from a device control panel into a
sources × mixes router, and takes the name OpenWave.

### Added
- **Mixing matrix**: user-defined mixes as columns, sources as rows,
  per-cell send and mute, per-source trim; drag to reorder or group.
- **Sources**: app rows matched by name (several names per row), hardware
  capture rows, a catch-all row; System/Game/Music/Browser/Voice seeded on
  first run; icon picker; bind an app that is not yet running.
- **Per-mix outputs**: every mix picks its own device (or none); output
  loopbacks survive the window closing; every mix also published as a
  capture source for voice apps, and kept linked across sink recreation.
- **Auto-discovered microphone rows**: every Elgato input gets its own row
  named after the device; **microphone groups** with exclusive-live
  semantics and one-press hand-over.
- **48 V phantom power** control (the only way to switch it on an XLR Dock).
- **Wave XLR MK.2 / XLR Dock** (`0fd9:00a6`) support, verified on hardware.
- **Remote control**: seven `org.gtk.Actions` on the session bus — levels,
  mutes, group switching, and a JSON snapshot — the surface
  [openwave-streamdeck](https://github.com/NyleGarcia/openwave-streamdeck)
  builds on.
- **Per-source level meters**, empty-mix indicator, muted-row marking.
- Gain shown in dB; gain lock; hardware tracks a slider drag live.
- Mix master volumes remembered and restored across reboots.
- Stalled-capture recovery: a replugged Wave that enumerates but delivers no
  frames is detected and reopened.
- Hotplug: reconnect to a Wave that appears after launch.
- App drawer entry, start-at-login and start-in-tray switches; own tray
  icons with a live/muted/attention state.
- ALSA controls discovered by name instead of hardcoded numids; ALSA card
  matched by USB id so two Elgato devices are told apart.
- Unit suite (19 files) + CI; mixer reconcile paths tested against a fake
  PipeWire; suite is sandboxed so it can never touch real user config.
- Tag-driven releases: `.deb`, source tarball and checksums per tag.

### Fixed
- Intake sinks can no longer win the default-sink election.
- A hidden window no longer overwrites the remembered geometry.
- Source-row sliders actually attenuate (loopback volume, not sink volume).
- An application's audio is moved into its source rather than copied, so a
  fader at zero is actually silent.
- Icon theme and install prefix that are not the default both survive.

## [1.0.0] — 2026-05-25

### Added
- First cut of the mix infrastructure: Personal / Chat / Record mix sinks,
  per-cell mixing via `pw-loopback`, user-defined app sources, mix matrix UI.
- Device pane moved into a collapsible sidebar.
- Live source level meters; full −128 dB headphone range.
- runit support alongside systemd; WirePlumber suspend-disable rule;
  byte-flow watchdog for a wedged keepalive.
- Multi-distro `install.sh` and Makefile.

## [0.1.5] — 2026-04-14

### Fixed
- `--hide` keeps the app alive when the tray is registered.

## [0.1.4] — 2026-04-14

### Fixed
- `--hide` registered as a proper GApplication option.

## [0.1.3] — 2026-04-14

### Fixed
- PKGBUILD referenced deleted docs.

## [0.1.2] — 2026-04-14

### Fixed
- udev detection for the old rule filename.

## [0.1.1] — 2026-04-14

Initial release: Wave XLR control (gain, mute, headphone volume, low
impedance), capture-fix daemon with uninstall, first-run setup, PKGBUILD and
release workflow.

[Unreleased]: https://github.com/NyleGarcia/openwave/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/NyleGarcia/openwave/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/NyleGarcia/openwave/compare/v0.1.5...v1.0.0
[0.1.5]: https://github.com/NyleGarcia/openwave/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/NyleGarcia/openwave/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/NyleGarcia/openwave/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/NyleGarcia/openwave/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/NyleGarcia/openwave/releases/tag/v0.1.1
