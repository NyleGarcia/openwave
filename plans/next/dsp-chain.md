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

### Tier 1b — more builtins (still zero dependencies)
- **Presence EQ**: three bands from builtin biquads (`bq_lowshelf`,
  `bq_peaking`, `bq_highshelf`) — broadcast-voice tone shaping.
- **Mono downmix toggle**: a stereo capture forced to centered mono
  (channel-mix in the FX node) — the fix for one-sided interfaces.
- **Per-source delay**: millisecond alignment so mic and desktop audio
  hit the Record/Stream mix in sync (builtin delay). Lives on any
  source, not just microphones.

### Tier 2b — more LADSPA classics
- **De-esser** (TAP `tap_deesser`) — candidate plugin, verify at build.
- **Auto-leveler / AGC** — slow-attack leveling so nobody rides gain.
  Candidate: sc4 with leveler settings or TAP AGC; pick by ear at build
  time, and say which plugin the toggle needs.

### Mix-side chain (separate insertion point: before a mix's output
loopback, or app-driven)
- **Headphone EQ per mix**: biquad EQ on the *output* path (Personal Mix
  → Arctis), AutoEq-style curves importable later. Same filter-chain
  mechanics, different insertion point.
- **Music ducking**: music dips when the microphone is live. Two
  implementation shapes, decided at build: (a) LADSPA sidechain
  compressor — blocked on filter-chain's single-capture-stream model,
  probably a dead end; (b) **app-driven**: the per-source meters already
  produce a 15 Hz voice envelope, and cell volumes are already written
  through the throttler — ducking is a small control loop over machinery
  that exists (watch mic meter, ease the Music cell down/up). (b) is the
  recommendation: no DSP at all, scene-aware, and the release curve is
  a Python constant instead of a plugin parameter.
- **Loudness meter (LUFS)**: EBU R128 readout on the Record/Stream mix
  so streams land near −14/−16 LUFS. Metering only. Needs a 48 kHz tap
  (the 8 kHz level meters cannot carry K-weighting); spawn it only while
  the readout is visible. K-weighting is two fixed biquads — pure
  Python over the existing meter-reader pattern, `libebur128` optional
  if the numbers disagree with OBS.

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

## Suggested build order across it all

1. Tier 1 low cut (proves the insertion end to end)
2. Tier 1b EQ + mono + delay (same node, zero deps, big visible win)
3. Tier 2 gate/comp/limiter, then 2b de-esser/AGC
4. Tier 3 RNNoise
5. Ducking (app-driven) and LUFS meter — independent of the FX node,
   can land any time after the meters exist (they do)
6. Headphone EQ per mix
7. Tier 4 NVIDIA — only past its questions

## Promotion checklist (next → now)

- [ ] Tier 1 spec'd into tasks (filter-chain render, mixer insertion,
      popover, persistence, reconcile tests against FakePipeWire)
- [ ] swh-plugins + rnnoise plugin packaging notes per distro (incl.
      the Flatpak manifest additions — both are LADSPA .so files the
      sandbox must bundle)
- [ ] Latency measured on real hardware before defaults are chosen
