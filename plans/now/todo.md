# Now — active sprint

> Status 2026-08-30: Tasks 1–8 implemented, suite green (282 tests).
> Verified live: checkout app running (systemd unit `openwave-checkout`),
> 11 actions on the bus, full scene save/move/recall/delete round-trip over
> gdbus with the Dock attached — PASS. Remaining: scene recall after a real
> reboot, click Export diagnostics once, release.yml's AUR job proves
> itself on the next tag.

Source: `docs/comparison.md` gap analysis vs openxlr / Wave Link (2026-08-30).
Specs live in `plans/specs/`. Done items get checked, re-linked to `docs/`,
then removed from this file.

## Phase 1: Hygiene (quirk-fix batch)

### Task 1: Derive udev_installed() from PROFILES

**Description:** `wavexlr/setup.py udev_installed()` checks only `007d` and
`0070`, so an MK.2/Dock (`00a6`) owner re-runs first-run setup forever.
Identical bug class already happened once for `0070` (documented in
`flake.nix` postInstall comment). Derive both `UDEV_RULES` and the check
from `profiles.PROFILES` so a new device can never miss either.

**Acceptance criteria:**
- [x] `udev_installed()` requires every `PROFILES` pid in the rule file
- [x] `UDEV_RULES` generated from `PROFILES` (single source of truth)
- [x] Unit test: every profile pid appears in rules and in the check

**Verification:** `python3 -m unittest discover -s tests -t .`
**Dependencies:** None
**Files:** `wavexlr/setup.py`, `tests/test_config_render.py` (or new test file)
**Scope:** S

### Task 2: Unify desktop-entry identity

**Description:** `wavexlr/desktop.py:17-21` generates the pre-rename tagline
("Elgato Wave control for Linux") and generic icon; shipped
`wavexlr.desktop` says "The audio mixing matrix for Linux" with
`Icon=openwave`. Whichever entry the user gets depends on install path.
Make `desktop.py` the single source: same name/comment/categories as the
shipped file, `Icon=openwave` when the themed icon resolves, generic
fallback otherwise.

**Acceptance criteria:**
- [x] Generated entry and `wavexlr.desktop` agree on Name/Comment/Categories
- [x] Icon falls back cleanly on a checkout with no installed icons
- [x] `tests/test_desktop.py` pins the generated content

**Verification:** suite + launch from checkout, check drawer entry
**Dependencies:** None
**Files:** `wavexlr/desktop.py`, `wavexlr.desktop`, `tests/test_desktop.py`
**Scope:** S

### Task 3: Single owner for GitHub Releases

**Description:** `build.yml` (manual) pushes a `v*` tag — which triggers
`release.yml` — *and* runs `gh release create --draft` on the same tag.
Nothing coordinates them. Make `release.yml` the only workflow that creates
Releases; `build.yml` keeps the PKGBUILD bump + AUR publish and stops
creating releases.

**Acceptance criteria:**
- [x] `build.yml` no longer calls `gh release create`
- [x] One tag push → exactly one Release
- [x] CONTRIBUTING.md "known quirk" paragraph updated to describe the fixed flow

**Verification:** `workflow_dispatch` dry-run of release.yml (publishes no Release); next real tag
**Dependencies:** None
**Files:** `.github/workflows/build.yml`, `CONTRIBUTING.md`
**Scope:** S

### Checkpoint: Hygiene
- [x] Suite green, `compileall` clean
- [ ] First-run setup no longer re-prompts on an MK.2-only machine (unit-tested; confirm once on the real machine)

## Phase 2: Diagnostics export

### Task 4: `wavexlr/diag.py` collector

Spec: `plans/specs/diagnostics-export.md`

**Description:** One command gathers everything a device bug report needs:
versions, detected profile, config/devinfo dump (via the GUI's own handle or
probe), `pw-dump` excerpt of openwave nodes, `wpctl status`, service state,
udev check, recent daemon journal. Plain-text bundle, secrets-free.

**Acceptance criteria:**
- [x] `python3 -m wavexlr.diag` writes one timestamped `.txt` and prints its path
- [x] Runs without hardware and without the daemon (sections say "absent", never traceback)
- [x] No serial-number redaction needed beyond what README already publishes; no config-file contents with user app names unless `--full`

**Verification:** run with and without device; unit test on section assembly with faked collectors
**Dependencies:** None
**Files:** `wavexlr/diag.py` (new), `tests/test_diag.py` (new)
**Scope:** M

### Task 5: Export button + docs

**Description:** "Export diagnostics" button in the sidebar service section;
saves via file dialog. README "Reporting problems" section points at it
first, probe second.

**Acceptance criteria:**
- [x] Button produces the same bundle as the CLI
- [x] README + docs/hardware-support.md reference it

**Verification:** manual click; suite
**Dependencies:** Task 4
**Files:** `wavexlr/app.py`, `README.md`, `docs/hardware-support.md`
**Scope:** S

## Phase 3: Profiles / scenes (v1)

Spec: `plans/specs/profiles-scenes.md` — read it before starting.

### Task 6: Scene store + capture/apply in Mixer

**Description:** `wavexlr/scenes.py` (NOT `profiles.py` — that name is taken
by device protocol profiles): named snapshots of trims, source mutes, cell
sends/mutes, mix outputs, mix master volumes. Capture from live state;
apply through the existing setter paths so reconcile stays authoritative.

**Acceptance criteria:**
- [x] `~/.config/openwave/scenes.json`, corrupt-file recovery same as mixes.py
- [x] Apply tolerates a scene naming a source/mix that no longer exists (skips, reports)
- [x] Reconcile tests cover apply (FakePipeWire call-sequence assertions)

**Verification:** suite; manual save/recall across restart
**Dependencies:** None (parallel-safe with Phase 2)
**Files:** `wavexlr/scenes.py` (new), `wavexlr/mixer.py`, `tests/test_scenes.py` (new)
**Scope:** M

### Task 7: Hardware state in scenes

**Description:** Extend scene payload with device state (gain, mute,
phantom, low-Z, HP volume) keyed by profile key; applied only when that
device is connected, respecting gain lock.

**Acceptance criteria:**
- [x] Scene with hardware section applies on matching device, silently skips otherwise
- [x] Gain lock wins over a scene's gain value
- [x] No-hardware test stays green

**Verification:** suite + on-hardware check
**Dependencies:** Task 6
**Files:** `wavexlr/scenes.py`, `wavexlr/app.py`
**Scope:** S

### Task 8: Scene UI + D-Bus

**Description:** Header-bar scene menu (save current as…, apply, delete) and
two remote actions: `apply-scene` (`s`), `save-scene` (`s`), plus a `scenes`
state action — same activate/describe pattern as `source-groups`.

**Acceptance criteria:**
- [x] Scenes drivable from `gdbus` (Stream Deck-ready)
- [x] README Remote control table + docs/comparison.md updated (profiles row goes 🟢)

**Verification:** `gdbus call` round-trip; suite
**Dependencies:** Task 6 (Task 7 optional)
**Files:** `wavexlr/app.py`, `README.md`, `docs/comparison.md`
**Scope:** M

## Phase 4: Multiple simultaneous devices (added mid-sprint, implemented)

### Task 9: Multi-device support end to end

**Description:** Every connected Wave opened at once — same-model pairs
included. `device.scan()` walks the bus (libusb device list), targeted
`connect(profile, bus, addr)` opens a specific unit and pins its ALSA card
via `usbbus`; app holds `_devs` list with a sidebar Device dropdown
(visible only with 2+), polls and ALSA-syncs all units, sysfs watch
(`present_units()`) catches hotplug while others stay connected; daemon
refactored to one `_Pin` per source with worst-state aggregation; scenes
hardware keyed `profile:serial` with legacy fallback; tray muted = any
device muted; diag dumps every unit.

**Acceptance criteria:**
- [x] `scan()` reports two identical models separately (`tests/test_device_scan.py`)
- [x] Daemon pins every Wave incl. XLR Dock (whose node name the old match missed) — `tests/test_audio_pins.py`; verified live: two pins on the real Wave XLR + Dock
- [x] Scene hardware entries keyed by serial, legacy scenes still apply (`tests/test_scenes.py`)
- [x] App verified live holding both devices (diag showed both handles held, cards 4 and 3)
- [x] Start-in-tray verified live: `--hide` stays tray-only, one StatusNotifierItem, window summonable

**Files:** `wavexlr/device.py`, `wavexlr/app.py`, `wavexlr/audio.py`, `wavexlr/scenes.py`, `wavexlr/diag.py`, tests
**Scope:** L (user-requested mid-sprint)

### Checkpoint: Sprint complete
- [ ] Suite green on 3.10 + 3.13 (3.14 local ✓; CI on push)
- [ ] Scene saved → reboot → recalled, hardware included (manual)
- [ ] Diagnostics bundle attached to a test issue reads clean (manual)
- [x] `docs/` updated; done items re-linked and removed from this file
