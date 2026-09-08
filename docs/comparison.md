# How this fork differs from upstream

Upstream is [rikkichy/openwave](https://github.com/rikkichy/openwave); this
tree is [NyleGarcia/openwave](https://github.com/NyleGarcia/openwave). A third
sibling, [CryoByte33/openwave](https://github.com/CryoByte33/openwave), is
covered in the README's credits and not compared here.

Snapshot taken 2026-09-08 against `upstream/main` at
`f654103` ("Add safe uninstall flow and configurable supplied tray icons").
Re-run it with:

```bash
git remote add upstream https://github.com/rikkichy/openwave
git fetch upstream main
git diff --stat upstream/main HEAD
```

## The histories are unrelated

`git merge-base HEAD upstream/main` returns nothing. This tree was re-rooted
with fresh history — 51 commits from 2026-08-30 — while upstream carries 113
commits back to 2026-04-14. Nothing can be cherry-picked by SHA and nothing
can be merged; a port from either direction is a port by hand, file by file.

That also means "ahead/behind" says nothing. The only honest comparison is a
tree diff, which reads **105 files changed, +13,293 / −9,571**.

Of the 80 files present in both trees, seven are byte-identical: `LICENSE`,
`flake.lock`, `tests/test_audio_pins.py`, `wavexlr/__init__.py`,
`wavexlr/mixdialog.py`, `wavexlr/probe.py` and `wavexlr/style.css`. Every
other shared file has been rewritten on one side or the other.

| | this tree | upstream |
|---|---|---|
| application code | 13,215 lines across 28 modules | 12,040 lines across 32 modules |
| tests | 4,187 lines across 26 files | 2,997 lines across 15 files |
| version | 1.2.1, from `CHANGELOG.md` and `PKGBUILD` | 1.0.0, from a `VERSION` file |
| last commit | 2026-09-03 | 2026-09-08 |

## Only in this tree

**Modules.** `wavexlr/wmnames.py` — friendly application-name resolution and
the generic-name rules, ported from CryoByte33/openwave.

**Documentation and planning.** `CHANGELOG.md` (Keep a Changelog, ten releases
back to 0.1.1), `CONTRIBUTING.md`, `docs/screenshot.png`, and the whole
`plans/` tree: `now/todo.md`, `next/dsp-chain.md`, `later/ice-box.md`, and the
two specs under `plans/specs/`.

**Icons.** The three symbolic tray icons —
`openwave-symbolic.svg`, `openwave-muted-symbolic.svg` and
`openwave-attention-symbolic.svg` — which follow the icon theme's colour.
Upstream ships fixed-colour `openwave-black/red/white.svg` instead and makes
the choice a preference.

**Tests.** Nineteen test files upstream has no equivalent of, plus
`tests/support.py` with the `FakePipeWire` harness and `bare_mixer`/
`temp_config` helpers the newer suites are built on: `test_mixer_reconcile`,
`test_mixer_state`, `test_mix_volumes`, `test_stores`, `test_fx`,
`test_hw_mute_sync`, `test_no_elgato`, `test_throttler`,
`test_numid_discovery`, `test_matching`, `test_names`, `test_paths`,
`test_icons`, `test_tray`, `test_desktop`, `test_config_render`,
`test_device_scaling`, `test_setup_udev`, `test_sink_creation`.

## Only upstream

**A safe uninstall and installation-management subsystem** — the largest
capability this tree does not have, and upstream's most recent work:

- `wavexlr/installation.py` (493 lines) — tracks what an install put on disk.
- `wavexlr/uninstall.py` (696 lines) — removes it again.
- `wavexlr/uninstall_dialog.py` (153 lines) — the GTK front end.
- `tests/test_installation.py`, `tests/test_uninstall.py`.

This tree can install its capture-fix service and undo that, but has no
answer for removing the application itself.

**Other modules.** `wavexlr/effects.py` (the DSP settings and config
rendering this tree keeps inside `sources.py`) and `wavexlr/child.py`.

**Release plumbing.** A `VERSION` file with `packaging/version.py`,
`packaging/build-release.sh`, `packaging/smoke-install.sh` and
`packaging/asset-attribution.txt`.

## Same problem, different answer

Both trees grew most of the same feature set independently. Where they
overlap, they overlap in intent, not in code.

### The DSP chain

Upstream extracted it into `effects.py`, which renders PipeWire configuration
as a pure function and lets the mixer own the processes. Here it lives inside
`sources.py` and `mixer.py`, and a settings change respawns the
`pipewire -c <generated conf>` child rather than patching it.

The two `DEFAULT_FX` dictionaries have nevertheless converged on identical
keys and identical defaults — `lowcut`, `gate`, `gate_thresh`, `comp`,
`comp_thresh`, `comp_ratio`, `eq_low`, `eq_mid`, `eq_high`, `delay_ms`,
`mono` — and both drive the SWH LADSPA gate and SC4 compressor. A port in
either direction would be mostly plumbing.

### Health monitoring

Upstream's `health.py` is framed around consent: observation by default, no
graph-driver changes or profile preferences imposed, disruptive remedies
behind an explicit opt-in, and `stop()` waits for any started transaction to
be restored.

This tree's `health.py` is framed around two specific faults observed on real
hardware — sustained xruns from losing the graph-driver election, and a
keepalive that wedged without dying — and watches for those directly.

That difference reaches the audio server config.
`wireplumber/51-openwave-wave-xlr.conf` here sets `priority.driver = 2500` so
the Wave's wired iso clock wins the election outright; upstream has no such
line. Conversely, upstream's `pipewire/52-openwave-mixes.conf` carries
`state.restore-props = false` and `priority.session = 0` on every mix sink,
where this tree drops both and remembers master levels in the application
instead — and treats the shipped file as a static fallback, generating the
real one from the user's mix definitions during first-run setup.

### Scenes

Both stores hold levels and refuse to hold structure, and both deliberately
exclude phantom power. Upstream's store is `{"scenes": {id: scene}}` and
leaves unreadable storage untouched, reporting to the caller. This tree's
store keys hardware state by device profile and follows the same durability
rule as its other stores: a corrupt file is preserved as `scenes.json.corrupt`
and replaced with defaults.

### Weight

`mixer.py` is 2,370 lines here against upstream's 980; `app.py` is 3,135
against 2,302; `sources.py` 365 against 181; `setup.py` 508 against 241.
Upstream is smaller in these and larger in module count, having split
installation, uninstallation and effects out into files of their own.

### Documentation

Two entirely different documents, 593 lines of README apart. Upstream leads
with a centred logo and device control. This tree leads with the mixing
matrix and models its structure on
[emaspa/openxlr](https://github.com/emaspa/openxlr).

## Known drift in this tree

Artefacts of the fork that have not been cleaned up:

- `install.sh` still clones and curls from `rikkichy/openwave` (lines 4 and
  9), and `flake.nix:90` and `.github/workflows/release.yml:92` still name it
  as the homepage. `PKGBUILD` and the README correctly point here.
- `LICENSE` is still `Copyright (c) 2025 rikkichy`.
- `README.md:107` calls the gate and compressor roadmap items, but
  `sources.py:270` implements both.
