# Spec: port CryoByte33/openwave logic into the fork

*Written 2026-08-30. Tracked in [plans/now/todo.md](../now/todo.md).*

## Context

`CryoByte33/openwave` forked `rikkichy/openwave` at `19ba53a` ("Bump version to
v1.0.0"), diverged for ~5 days, last pushed 2026-06-29. Our tree
(`rikkichy/main` + `wave-xlr-mk2-and-output-routing`) ran to 2026-08-30 and is
far ahead on the mixer: dynamic mix store, per-mix output, exclusive source
groups, phantom power, session-bus remote control, stalled-capture recovery,
mix-master persistence, an 11-file test suite and CI.

Cryo still has genuine work we lack. This plan takes the parts that are ahead
and explicitly rejects the parts where adopting them would regress us.

Every `cryo/...` path and bare commit sha below refers to that fork. To follow
them, clone it somewhere scratch and point it at this repo so the merge base
resolves:

```bash
git clone https://github.com/CryoByte33/openwave.git /tmp/cryo && cd /tmp/cryo
git remote add local /path/to/this/repo && git fetch local
git merge-base HEAD local/main    # -> 19ba53a
```

**Scope decided: safe wins only.** Hardware on hand is the XLR Dock (`0x00A6`)
and the original Wave XLR (`0x007D`) — no `0x00B6`, so Cryo's MK.2 backend is
deferred rather than blind-ported (see *Deferred*).

## Verdict table

| Cryo work | Verdict |
|---|---|
| Friendly app display names (`wmnames.py` + generic-name rules) | **Take** — §1 |
| Shared `Throttler` / `Scheduler` seam | **Take** — §2 |
| ALSA numid discovery by control-name suffix | **Take** — §3 |
| Device hotplug auto-reconnect | **Take** — §4 |
| `c89880f` duplicate-source picker guard | **Take, reduced** — §5 |
| MK.2 `0x00B6` UAC2 backend, voice effects, crossfade | **Defer** — no hardware to verify |
| `SubprocessPipeWire` adapter seam | **Defer** — big refactor, real value; see *Deferred* |
| `routing.py` / `pwnames.py` / `mixercontroller.py` | **Reject** — fixed 3-mix model, regresses dynamic mixes |
| `07579a1` cell-loopback volume fix | **Reject** — immune by construction |
| `0c9feff` glob, `1f763a9` `do_shutdown`, `4a2aecb` config refresh | **Reject** — already fixed here, more thoroughly |
| Tray / `desktop.py` / autostart / CI | **Reject** — Cryo is behind us |
| Rename `wavexlr` → `openwave` | **Reject for now** — cosmetic, costlier for us |

---

## 1. Friendly app display names

The Add Source picker titles rows with raw `application.name`
(`wavexlr/sourcedialog.py:322`), so a Java app reads "ALSA plug-in [java]" and
an Electron app reads "Chromium".

Cryo splits this cleanly: `app_name` stays the stable **match key**,
`display_name` is a **label** resolved from the process binary, and failing
that from the owning X11 window.

- **New `wavexlr/wmnames.py`** — copy `cryo/openwave/wmnames.py` verbatim
  (change the logger name to `wavexlr.wmnames`). Fully self-contained: imports
  nothing from its own package, `python-xlib` imported lazily *inside*
  `pid_names()`, every failure path returns `{}`. Maps `_NET_CLIENT_LIST` →
  `_NET_WM_PID` → name, preferring a clean `WM_CLASS` over the volatile window
  title but falling back to the title for reverse-DNS/dashed classes
  ("net-runelite-client-RuneLite" → "RuneLite").
- **`wavexlr/mixer.py`** — port `_GENERIC_PREFIXES`, `_GENERIC_NAMES`,
  `_RUNTIME_BINARIES`, `_is_generic()`, `_binary_name()`
  (`cryo/openwave/pipewire.py:38-82`) and add a `display_name` key to
  `list_audio_streams()` (`mixer.py:395`). That function **already returns
  `binary`** (`mixer.py:436`), so only the pid capture and the enrichment pass
  are new. Keep Cryo's laziness: X11 is consulted only when a stream is generic
  *and* has no usable binary, so the common path never touches Xlib.
  Keep our existing extra filters (`playback.*` nodes, virtual-node skip,
  `mixer.py:417-426`) — Cryo has neither.
- **`wavexlr/sourcedialog.py:322`** — title the row with `display_name`, leave
  `row._app_name` (`sourcedialog.py:327`) as the match key untouched.
  Do **not** touch `_stream_identities()` (`mixer.py:453`) or `claim_streams()`
  (`mixer.py:504`) — matching must stay exact equality.
- **Dependency** — `python-xlib`, strictly optional. Note it as optional in the
  README requirements, `PKGBUILD` and `flake.nix`, the way Cryo's README does.
- **Tests** — `_is_generic`/`_binary_name`/`_pick_name` are pure. Add
  `tests/test_names.py` mirroring `cryo/tests/test_names.py`. Cover the
  regression Cryo hit twice (`ab07051`): a name that already matches its binary
  (Zen/zen, Discord/discord) is **not** generic and must not go through the
  window lookup — a Flatpak's namespaced PID can collide with another sandbox's
  window and mislabel it.

## 2. Shared `Throttler` + `Scheduler`

Three sliders are debounced with three copy-pasted trailing-only timers:
`_on_gain_changed`/`_send_gain` (`app.py:667`, `self._gain_timeout`),
`_on_hp_changed`/`_send_hp` (`app.py:682`), `_on_mix_changed`/`_send_mix`
(`app.py:885`) — each `GLib.timeout_add(200, ...)`, each firing only *after*
the drag stops.

- **New `wavexlr/scheduler.py`** — copy `GLibScheduler` + `Throttler` from
  `cryo/openwave/scheduler.py`. `Throttler` is pure Python (no GLib import),
  keyed by control name, and does leading + periodic + trailing at 80 ms, so a
  drag tracks live instead of landing 200 ms late.
- **`wavexlr/app.py`** — replace the three ad-hoc timers with
  `self._throttle.push("gain", value, setter)` etc. Keep our existing
  `_usb_async` (`app.py:544`) — it is already the same shape as
  `Scheduler.run_async`; adopting the class for it is optional, not required
  here.
- **Tests** — `tests/test_throttler.py` with a fake controllable-clock
  scheduler, mirroring `cryo/tests/test_throttler.py`. No GTK, no main loop.

## 3. ALSA numid discovery by control-name suffix

`wavexlr/device.py` hardcodes `numid=5` for mute, `numid=4` for HP volume and
`numid=6` for gain (`device.py:114-158`). Those numbers are not stable across
firmware revisions or models.

Port Cryo's `_MK2_ROLE_BY_SUFFIX` + `_discover()`
(`cryo/openwave/device.py:432-437`, `589-610`): parse `amixer contents`, match
the control-name **suffix** (`Capture Switch`, `Capture Volume`,
`Playback Switch`, `Playback Volume`) — the product-string prefix varies between
units and firmware, the suffix does not — and cache the resulting numid plus max
per card. Fold in our existing per-control max cache (`_alsa_ctl_max`,
`device.py:136`).

Keep the current hardcoded numids as the fallback when discovery finds nothing,
so nothing that works today regresses. Both devices on hand exercise this.

## 4. Device hotplug auto-reconnect

We reconnect only when the user clicks Refresh (`app.py:217`). Nothing polls for
a device that appears later, so plugging a Wave in after launch leaves the
sidebar dead until clicked.

Port the reconnect loop only — **not** `DeviceController` wholesale:
`_start_reconnect`/`_reconnect_tick` from `cryo/openwave/devicecontroller.py`
(2 s tick, polls for the device, hands off to the normal connect path). Start it
on connect failure and from `_on_poll_error` (`app.py:605`); stop it once
connected. Our `_start_polling`/`_stop_polling` (`app.py:581-590`) is the model.

**Also investigate while here** (found during comparison, not a Cryo port):
`find_wave_xlr_alsa()` is called exactly once, in `Mixer.__init__`
(`mixer.py:575` — `self.mic, self.hp = find_wave_xlr_alsa()`). There is no
re-detect path. `recovery.py` covers *stalled-but-present* (node exists,
delivers zero frames), not *vanished-and-returned*. If a physical replug changes
the ALSA node names, `self.mic`/`self.hp` go stale with nothing to correct them.
Confirm against real hardware before deciding whether this needs a fix — it may
not be reachable if our node names are stable across replug.

Note Cryo's related bug for context: their replug wait broke on `mic or hp`
where it needed `mic and hp` (`24684d2`) — mic and headphones are the same
device, so acting on whichever registers first drops the other's loopback. Worth
keeping in mind if a re-detect path does get added.

## 5. Duplicate-source picker guard (reduced port)

`app.py:_on_add_source_clicked` (`app.py:1093`) excludes already-bound **capture
nodes** via `_bound_capture_nodes()` (`app.py:1099`) but not already-bound **app
names**. `sourcedialog.py:_populate_apps` (`sourcedialog.py:309`) lists every
running app with no exclusion, and `_on_source_confirmed` (`app.py:1113`) adds
unconditionally — so a second row can be bound to an app another row already
claims.

The audio consequence is already handled: `claim_streams()` (`mixer.py:504`)
gives every stream exactly one owner (most-specific match wins, ties broken on
source id, catch-all last), so the duplicate cannot double-route or double-
amplify. This is **strictly better than Cryo's UI-only fix** and must stay.

What remains is purely cosmetic: the duplicate row is a silently inert fader,
which is confusing. So port only the picker filter, not Cryo's routing
assumptions:

- Add a bound-app-names helper next to `_bound_capture_nodes()` in `app.py`,
  built from `match_app_names` (`sources.py:138`) so it covers our multi-name
  rows, which Cryo's single `match_app_name` model did not have.
- Pass it as `exclude_apps` into the dialog and filter in `_populate_apps`,
  mirroring `git show c89880f -- wavexlr/sourcedialog.py`.
- Update the empty-state text the way Cryo did ("No new apps playing audio").
- Do **not** port Cryo's `_on_source_confirmed` hard-reject — a catch-all row
  and a specific row legitimately overlap in our model.

---

## Rejected, and why

- **`routing.py` / `pwnames.py` / `mixercontroller.py`** — `MIX_SINKS` is a
  hardcoded three-entry dict (`personal`/`chat`/`record`) and `plan()` iterates
  it; `MixerController` imports both. We have a user-defined mix store with
  per-mix output devices, device-vs-app sources, source trim, and exclusive
  group mute. Adopting these is a regression, not a refactor.
- **`07579a1`** (re-apply cell loopback volume after a retried spawn) — Cryo
  caches `_cell_state` and skips re-applying, so a failed spawn strands the
  retry at unity. We keep no such cache: `_reconcile_capture_cell` and
  `_reconcile_app_cell` (`mixer.py:1446`, `mixer.py:1509`) re-apply volume and
  mute unconditionally every pass. Immune by construction. (We do pay a
  `_node_id_by_name` lookup plus two `wpctl` calls per cell per pass where Cryo
  pays none — a real cost, but correctness-by-construction is worth more.)
- **`0c9feff`** — `Makefile:18` already uses `$(wildcard wavexlr/*.py)`;
  `PKGBUILD:19` globs.
- **`1f763a9`** — our `do_shutdown` (`app.py:1788`) is already single and covers
  meter + mixer teardown, plus polling stop, UI-state save and USB disconnect,
  which Cryo's does not.
- **`4a2aecb`** — our `service.needs_refresh()` compares just the `ExecStart=`
  line rather than the whole unit file, avoiding false positives between a dev
  checkout and a site-packages install. Cryo's whole-file compare is cruder.
- **`_set_pdeathsig`** — already ours (`mixer.py:137`, shared by `meter.py:22`).
- **Tray / `desktop.py` / autostart / `tests.yml`** — Cryo has none of these and
  still has the hide-into-a-nonexistent-tray bug we fixed in `cc3cfb0`.
- **Rename `wavexlr` → `openwave`** — real cost here is higher than Cryo's was:
  `probe.py`, `paths.py`, `desktop.py`, `service.py` (`RUNIT_SERVICE`,
  `_daemon_command`, the `/proc` cmdline match), `flake.nix`'s hardcoded
  site-packages subpath, `.github/workflows/tests.yml`, and all 11 test files
  import the package by name. `setup.py:17` already carries a `UDEV_PATH_OLD`
  migration hook, so groundwork exists. Its own branch, if ever.

## Deferred

**MK.2 `0x00B6` backend.** Additive, not a correction — our `WAVE_XLR_MK2`
(`profiles.py:139`) is PID `0x00A6`, enumerates as "Elgato XLR Dock", speaks the
original Wave XLR vendor protocol, and was probe-verified against hardware.
Cryo's `PID_MK2 = 0x00B6` (`cryo/openwave/device.py:27`) is a different chip
generation: UAC2, driven by a second vendor transport
(`bmRequestType 0xC1`/`0x41`, `bRequest 0x01`, `wIndex 0x0203`, numbered blocks)
that our single-flat-config-block `DeviceProfile` has no field for. `0x00B6`
appears nowhere in our code or history.

Worth having if that hardware ever turns up — it is the only place Cryo is
meaningfully ahead on features. Decoded map, for whenever:

```
block 0x0001 (6B)   byte0  = crossfade 0..200, 100 = centre
block 0x0004 (38B)  byte0  = gain 0..80
                    byte1  = bit0 mute, bit4 low-cut, bit5 expander,
                             bit6 voice tune
                    byte10 = voice-tune strength 0..100
block 0x0005 (2B)   byte0  = hp volume, 0 loudest..240 quietest, dB = -b/4
                    byte1  = bit1 low impedance
```

Plus `_MK2_HP_DETENTS` (`device.py:422`), 49 captured wheel steps. UI in
`git show 0446abb -- openwave/app.py`. Our UI seam is ready for it either way:
`_apply_profile()` (`app.py:611`) keys row visibility off profile capability
flags and `_apply_state()` (`app.py:634`) keys values off state-dict keys.
Caveat if revisited: Cryo decoded these from a Wave Link USB capture, not a
datasheet.

**`SubprocessPipeWire` adapter seam.** Unlike `routing.py`, the adapter itself
is name-agnostic and not tied to the 3-mix model, so it *is* portable. We
currently have ~30 scattered `subprocess.run`/`Popen` sites in `mixer.py`
(`_pactl_short` `mixer.py:160`, `_wpctl` `mixer.py:246`, `_ports`
`mixer.py:256`, three independent `pw-dump` invocations at `mixer.py:285`,
`339`, `399`, `_spawn_loopback` `mixer.py:750`, `_rescue_default_sink`
`mixer.py:1197`, `_sweep_stale_loopbacks` `mixer.py:1331`).

The payoff is testability: nothing today exercises `_reconcile_cell` /
`_reconcile_capture_cell` / `_reconcile_app_cell` / `_spawn_loopback`. Our
`bare_mixer()` (`tests/support.py:40`) gets object construction without
hardware, and `test_mixer_state.py:148` monkeypatches `subprocess.run` for the
one default-sink-rescue case, but call-sequence assertions against the spawn and
volume logic are not writable — which is exactly the layer where Cryo's real
regressions lived. Cryo's `FakePipeWire` + `Mixer(pw=fake, start_worker=False)`
makes them one-liners.

Mechanical but large, and touching the mixer is how the worst bugs get in. Its
own branch, after the five items above land.

---

## Suggested order

1. §1 friendly names — self-contained, immediate visible win.
2. §2 `Throttler` — small, testable.
3. §5 dup-row guard — small, sits next to §1 in the same dialog.
4. §3 numid discovery — pure robustness, both devices on hand exercise it.
5. §4 hotplug reconnect — largest of the five, plus the replug investigation.

## Verification

- `python -m unittest discover -s tests -t . -v` after each step — the existing
  suite is the guard against regressing the mixer; CI runs it on 3.10 and 3.13.
- `python -m compileall -q wavexlr tests`, matching
  `.github/workflows/tests.yml`.
- **§1** — run the app, open **Add Source** with a Java or Electron app playing;
  the row should show its real name. Then make `python-xlib` unimportable (or
  run a native-Wayland app) and confirm it degrades to the raw PipeWire name
  rather than erroring. Critically: bind a source by its friendly label and
  confirm it still captures audio — the match key must be unchanged.
- **§2** — drag the gain and HP sliders on the Wave XLR; the device should track
  during the drag, not only on release. Confirm the hardware LED ring follows.
- **§3** — run `amixer -c <card> contents` for both the XLR Dock and the Wave
  XLR, confirm the discovered numids match the currently hardcoded 4/5/6 on that
  hardware before trusting discovery over the fallback.
- **§4** — launch with the Wave unplugged, plug it in, confirm the sidebar comes
  alive within a couple of seconds with no Refresh click. Then replug while
  running and check whether `self.mic`/`self.hp` are still correct (the
  investigation above) — compare `pw-dump` node names before and after.
- **§5** — add a source for a running app, reopen the picker, confirm that app
  is gone from the list and the empty state reads sensibly when everything
  playing is already bound.
