# Now

## Port CryoByte33/openwave logic into the fork

Spec: [plans/specs/cryo-port.md](../specs/cryo-port.md) — includes the full
verdict table and the *rejected, and why* list. Read that before re-opening the
comparison; most of Cryo's tree is behind us and the spec records which parts
and for what reason.

Scope is **safe wins only** — five items, no missing hardware, each landing on
its own. In order:

- [ ] **Friendly app display names.** New `wavexlr/wmnames.py` (X11
      `_NET_WM_PID` → window name); port the generic-name rules into
      `mixer.py:list_audio_streams()` as a new `display_name` key; use it for
      the row title in `sourcedialog.py:322` only. `app_name` stays the match
      key — do not touch `_stream_identities()` or `claim_streams()`.
      Optional `python-xlib` dep. Add `tests/test_names.py`.
- [ ] **Shared `Throttler`.** New `wavexlr/scheduler.py`; replace the three
      copy-pasted trailing-only 200 ms debounces in `app.py` (`_send_gain`,
      `_send_hp`, `_send_mix`) with one keyed 80 ms leading+periodic+trailing
      throttle. Add `tests/test_throttler.py`.
- [ ] **Duplicate-source picker guard.** `app.py` has `_bound_capture_nodes()`
      for device rows but no equivalent for app names, so the picker can make a
      silently inert duplicate row. Add a bound-app-names helper built from
      `match_app_names`, pass it as `exclude_apps` to the dialog. Cosmetic only
      — `claim_streams()` already prevents double-routing.
- [ ] **ALSA numid discovery by control-name suffix.** `device.py` hardcodes
      `numid=4/5/6`; discover them from `amixer contents` by name suffix
      instead, keeping the hardcoded values as fallback. Verify against both
      the XLR Dock and the Wave XLR before trusting discovery.
- [ ] **Device hotplug auto-reconnect.** Only a manual Refresh button today
      (`app.py:217`). Add a 2 s reconnect tick started on connect failure and
      from `_on_poll_error`. While here, investigate whether
      `find_wave_xlr_alsa()` running once in `Mixer.__init__` (`mixer.py:575`)
      leaves `self.mic`/`self.hp` stale after a physical replug — `recovery.py`
      covers stalled-but-present, not vanished-and-returned.

Verification steps per item are in the spec. Run
`python -m unittest discover -s tests -t . -v` after each.

### Deferred (not this sprint)

- MK.2 `0x00B6` backend — voice effects and self-monitoring crossfade. Cryo is
  genuinely ahead here, but that is a different chip from our `0x00A6` XLR Dock
  and we have no `0x00B6` to verify against. Decoded block map is preserved in
  the spec.
- `SubprocessPipeWire` adapter seam — portable (unlike Cryo's `routing.py`) and
  would finally make the reconcile/spawn paths testable with a fake, but it is
  ~30 call sites in a 1582-line `mixer.py`. Own branch, after the five above.
