# Next: Per-microphone DSP chain (low cut, gate, noise removal)

Gap: openxlr offers host-side DSP for devices without onboard effects;
Wave Link has the full VST/AU stack; OpenWave has none. User ask on top:
AI noise removal — "NVIDIA Broadcast"-class — plus the classics (low cut,
gate).

## Architecture (fixed)

One `libpipewire-module-filter-chain` node per microphone row, inserted
between the capture node and its cells — the same shape as everything
else in the router: ordinary PipeWire objects, no custom audio code.
Node named `openwave_fx_<source>`, `priority.session=0` and the naming
sweep like intake sinks, reconciled by `Mixer`, settings persisted on the
source record in `sources.json` (they are source identity, like trim).
Cells capture the FX node instead of the raw device when any effect is
on; the replug self-heal applies to the FX node the same way.

## Effect tiers, in build order

### Tier 1 — builtin biquads (zero new dependencies)
- **Low cut / high-pass** at 80 or 120 Hz (`bq_highpass`).
- Ships first: proves the insertion, the UI, the persistence and the
  reconcile with nothing to install.

### Tier 2 — LADSPA classics (optional dependency: swh-plugins)
- **Gate** (swh), **compressor** (swh sc4), **hard limiter** at −3 dB
  (ClipGuard-alike). Degrade visibly when the plugin library is absent —
  a toggle that says "install swh-plugins", never a silent no-op.

### Tier 3 — AI noise removal (optional dependency: RNNoise plugin)
- [werman/noise-suppression-for-voice](https://github.com/werman/noise-suppression-for-voice):
  `librnnoise_ladspa.so`, `noise_suppressor_mono` — VERIFIED: PipeWire's
  own docs carry a filter-chain config for exactly this since 0.3.45,
  GPL-3.0, 48 kHz native which is what the graph runs. CPU-only, no GPU
  requirement, packaged in most distros (AUR/Fedora/Debian). This is the
  default "noise removal" toggle.
- The VAD grace-period knob is the one setting worth exposing (word
  onsets vs latency).

### Tier 4 — NVIDIA Maxine/Broadcast AFX (research track, promoted only
if it earns it)
Not scheduled; open questions to answer before any code:
1. Current Linux availability of the Audio Effects SDK and its license —
   the public page gates behind a 90-day trial and says nothing about
   redistribution. OpenWave could at most dlopen a user-installed SDK,
   never ship it.
2. RTX-only + TensorRT runtime: a hard hardware wall RNNoise does not
   have.
3. No PipeWire story exists: integration means writing a filter-chain
   plugin (filter-chain loads LADSPA — a LADSPA shim around the SDK's
   streaming API is the plausible shape) or a standalone
   consume/produce node. Real project either way.
4. Whether its denoise/dereverb beats RNNoise enough, on this hardware,
   to justify 1–3. Decide with recordings, not marketing.

Verdict for now: Tier 3 gives the "Broadcast" experience with none of
the walls; Tier 4 stays parked until someone measures a quality gap.

## UI

Per-microphone-row FX popover (next to the mute): toggles for low cut
(80/120), gate, compressor, limiter, noise removal; a "plugin missing"
state that names the package. Scenes capture FX settings with the rest
of the source record — free, since they live on it.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Latency in the mic path | High | builtin biquads near-zero; RNNoise adds ~10 ms frame + optional VAD grace — show it, default modest; FX off by default |
| FX node caught by default-sink election / claiming | Med | same priority.session=0 + sweep treatment as intake sinks |
| Missing plugin libraries | Low | visible degraded state naming the package; Tier 1 always works |
| RNNoise misclassifies poor mics | Low | it is a toggle; meters make the effect audible AND visible |

## Promotion checklist (next → now)

- [ ] Tier 1 spec'd into tasks (filter-chain render, mixer insertion,
      popover, persistence, reconcile tests against FakePipeWire)
- [ ] swh-plugins + rnnoise plugin packaging notes per distro (incl.
      the Flatpak manifest additions — both are LADSPA .so files the
      sandbox must bundle)
- [ ] Latency measured on real hardware before defaults are chosen
