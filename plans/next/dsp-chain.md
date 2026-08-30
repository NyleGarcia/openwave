# Next: Host-side DSP chain

Gap: openxlr offers host-side DSP (high-pass 80/120 Hz, ClipGuard-style
limiter, compressor/expander via LADSPA) for devices without onboard
effects; Wave Link has the full VST/AU stack. OpenWave has none
(`docs/comparison.md`, "Hardware control").

Parked in `next/` because it is the largest item and Phase 1–3 of
`plans/now/todo.md` should land first. Promote by moving this file's task
list into `plans/now/todo.md` and expanding into a full spec in
`plans/specs/`.

## Shape (to be spec'd before promotion)

- **Mechanism**: `libpipewire-module-filter-chain` node inserted between a
  microphone capture row and its cells — fits the existing architecture
  (ordinary PipeWire objects, no custom audio code). LADSPA `swh-plugins`
  as optional dependency, exactly openxlr's approach; builtin filter-chain
  plugins (`bq_highpass` etc.) cover the high-pass with zero new deps.
- **v1 scope**: per-microphone-row toggle set — high-pass (80/120 Hz),
  hard limiter at −3 dB ("clip guard"). Compressor/expander later.
- **Where it lives**: filter-chain config generated like the mix sinks
  (`setup.py` render path), node named `openwave_fx_<source>`, reconciled
  by `Mixer` like any other node; per-row FX popover in the matrix UI.
- **Persistence**: per-source FX settings in `sources.json` (they are
  source identity, like trim).
- **Testing**: reconcile decisions against FakePipeWire (spawn/despawn/
  relink when FX toggles); the audible result is hardware-verified like
  the rest of the routing.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Latency added in the mic path | High | builtin biquads first; measure with `pw-top` before/after; FX off by default |
| filter-chain node caught by default-sink election / claiming | Med | same `priority.session=0` + naming-sweep treatment as intake sinks |
| swh-plugins missing at runtime | Low | builtin-only v1; LADSPA features degrade with a visible "plugin missing" state |
