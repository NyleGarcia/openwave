# Spec: Profiles / scenes

Gap: openxlr has named scenes recallable from UI or API (`docs/comparison.md`,
"Scenes, control surfaces, API"); OpenWave has one persistent state.

## What a scene is

A named snapshot the user recalls as one gesture — "Streaming",
"Recording", "Late night". v1 payload:

```json
{
  "scenes": {
    "streaming": {
      "name": "Streaming",
      "sources": {"<id>": {"trim": 0.8, "muted": false}},
      "cells":   {"<source>/<mix>": {"send": 0.5, "muted": false}},
      "outputs": {"<mix>": "alsa_output...."},
      "volumes": {"openwave_personal_mix": 0.65},
      "hardware": {"wave_xlr": {"gain_raw": 20480, "mute": false,
                    "phantom": true, "low_z": false, "hp_db": -12.0}}
    }
  }
}
```

Deliberately **not** in v1: mix/source *definitions* (creating or deleting
rows/columns on scene switch). A scene sets levels on the matrix that
exists; it does not restructure it. Restructuring scenes = later horizon,
only if wanted after v1 use.

## Design decisions

- **Module name `scenes.py`**, store `~/.config/openwave/scenes.json`.
  `profiles.py` is taken by device protocol profiles — do not overload the
  word "profile" in code; UI copy may still say "profile" if it reads
  better (decide at UI task).
- **Apply goes through the window's existing paths** (`Mixer.set_cell`,
  source trim setters, device setters), never by writing config files —
  same rule as the D-Bus surface and for the same reason: reconcile
  re-applies `send × trim`, and the GUI holds the only USB handle.
- **Partial apply is normal, not an error.** A scene naming a source/mix
  that no longer exists skips those entries and reports what it skipped
  (toast in UI, log line from D-Bus). A scene's hardware section applies
  only when a device with that profile key is connected.
- **Gain lock wins.** A locked gain slider rejects the scene's gain the
  same way it rejects a drag; everything else in the scene still applies.
- **Capture reads live state**, not stored state — same principle as mix
  master persistence: whatever moved a fader, that is the value the scene
  should hold.
- **Store shape follows `mixes.py`**: seeded empty, corrupt file preserved
  as `.corrupt` and replaced, whole-file rewrite on save.

## Remote surface

Three new `org.gtk.Actions`, same conventions as the existing seven:

| Action | Parameter | Does |
|---|---|---|
| `apply-scene` | `s` scene id | Applies a scene (partial-apply rules above) |
| `save-scene` | `s` scene id/name | Captures current state into that scene |
| `scenes` | — | State: scene ids + names, activate-then-describe |

`snapshot` already exposes everything a scene holds, so an external tool
can diff scene-vs-live without new actions.

## Open questions

- Does a scene switch belong on the tray menu? (Probably yes, after v1.)
- Should `apply-scene` report skipped entries over the bus, or is the log
  enough? (v1: log; revisit if openwave-streamdeck wants feedback.)

## Verification

- Reconcile tests with FakePipeWire: apply produces exactly the expected
  set-volume/mute call sequence; skipped entries produce none.
- Round-trip: save scene → restart app → apply → `snapshot` matches saved
  payload (minus skipped hardware when absent).
- On hardware: phantom/gain/HP recalled; gain-lock case.
