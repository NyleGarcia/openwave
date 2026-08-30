# Contributing to OpenWave

## Running from a checkout

No build step, no install:

```bash
git clone https://github.com/NyleGarcia/openwave.git
cd openwave
python3 -m wavexlr
```

Dependencies: Python 3.10+, PyGObject (GTK4 + libadwaita), libusb 1.0,
PipeWire. Optional: `python-xlib` for friendlier app names in the Add Source
picker. Nothing is pip-installed; every distro ships these as system
packages (see `install.sh` for the per-distro lists).

No Elgato hardware is required for most work: the app runs fully without a
device (`tests/test_no_elgato.py` pins that), and the whole mixing matrix is
plain PipeWire.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

Plain `unittest`, no pytest, no test dependencies, no display, no audio
server, no hardware. CI runs the suite on Python 3.10 and 3.13 plus a
`compileall` pass (`.github/workflows/tests.yml`).

Two things about the suite worth knowing before writing a test:

- **`tests/__init__.py` redirects every config path into a throwaway
  directory at package import.** A test once built a bare mixer without
  `temp_config()` and wiped the user's real `~/.config/openwave/mixes.json`
  — replaced their whole matrix with test fixtures, at random, whenever the
  suite ran. `temp_config()` (from `tests/support.py`) is still the right
  tool inside a test; the package-level redirect is the seatbelt for the
  test that forgets it. Do not remove it.
- **The mixer is tested against a fake PipeWire.** `Mixer(pw=FakePipeWire())`
  (also from `tests/support.py`) turns the reconcile and spawn paths — where
  the worst regressions have lived: double-routed audio, loopbacks against
  dead links, faders driving nothing — into call-sequence assertions:
  configure what the fake graph holds, run one reconcile, read back what the
  mixer decided to do. New mixer behaviour should come with a reconcile test;
  `tests/test_mixer_reconcile.py` has the patterns.

The GUI, the USB protocol and the live routing are deliberately not
unit-tested; they are verified against real hardware. The fastest hardware
check is:

```bash
python3 -m wavexlr.probe dump    # quit OpenWave first, tray icon included
```

## Working on device support

Per-model protocol constants live in `wavexlr/profiles.py`; the transport is
`wavexlr/device.py`. `docs/protocol.md` documents the register maps and
`docs/hardware-support.md` the per-device status. Mapping a new field is a
`probe watch` session: move one physical control, read the per-offset diff.

## Commit messages

Prose subjects that say what changed and why — not Conventional Commits.
This is deliberate: the release flow versions from tags, not from commit
prefixes (see the header comment in `.github/workflows/release.yml`), so
subjects are written for humans reading `git log`.

## Cutting a release

The version is the tag:

```bash
git tag v1.2.3 && git push <remote> v1.2.3
```

`release.yml` gates on the test suite, then builds a source tarball, a
`.deb` and checksums, publishes a GitHub Release with generated notes,
points the PKGBUILD at the released tarball (pkgver + sha256, committed
back to the default branch), and publishes to AUR when the
`AUR_SSH_PRIVATE_KEY` secret is present. It is the only workflow that
creates Releases. A `workflow_dispatch` run of the same workflow exercises
the artifact steps without spending a version number (uploads workflow
artifacts, publishes no Release, touches no PKGBUILD).

Update `CHANGELOG.md` before tagging.

## AI assistance

AI-assisted contributions are fine (parts of this project were built that
way — see the README's AI disclosure) with one hard rule: hardware claims
must be verified on real hardware. A protocol offset, a support statement or
a hardware-support table row needs a `probe` session behind it, not a
model's inference.

## Documentation

User-facing behaviour changes belong in `README.md`; routing-model changes
in `docs/ARCHITECTURE.md`; protocol findings in `docs/protocol.md` and
`docs/hardware-support.md`. The README's feature list and repository-layout
tree are checked against the code by reviewers — keep them true.
