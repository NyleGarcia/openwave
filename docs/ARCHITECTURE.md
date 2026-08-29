# How OpenWave routes audio

The device half of OpenWave is a USB control panel. The mixing half is a
router built out of ordinary PipeWire objects. This describes the second,
because it is the part that is not obvious from the code.

## The shape

```
  application ──move──▶ intake sink ──loopback──▶ mix sink ──loopback──▶ output device
                    openwave_src_<id>       openwave_<mix>_mix      (your headphones)
                                    ▲                        ▲
                              trim × send              per-mix output
```

Everything is a null sink and a `pw-loopback` child process. There is no
custom audio code, no filter graph and no PipeWire module: the whole router is
sinks that discard audio, and loopbacks that carry it between them.

## Sources are rows, mixes are columns

A **source** is something that produces audio. Two kinds:

- **application** — matched by name against live streams. Its audio is *moved*
  onto the source's own intake sink, `openwave_src_<id>`.
- **device** — a hardware capture node, such as a headset microphone. Nothing
  is moved; the loopback captures the node directly.

A **mix** is a destination: a null sink that several sources feed and that may
be sent to a real output device, or to nothing at all.

The matrix is sources × mixes. Each cell is one `pw-loopback` carrying that
source into that mix.

## Why applications are moved rather than copied

The obvious implementation captures an application's stream and leaves the
application playing where it was. That is wrong here, and the failure is
subtle: OpenWave's mixes are normally the system default sink, so the
application's own connection already lands in the mix. Capturing it as well
means the audio arrives twice, and the cell's fader only ever attenuates the
copy — pulling it to zero leaves the original at full volume, so the fader
appears broken.

Moving the stream makes the loopback the only path, which makes the fader
authoritative.

Consequences worth knowing:

- The intake sink must exist before anything is moved onto it, and it must be
  destroyed when the source stops being routed. Destroying it returns the
  parked streams to the default sink; leaving one behind would strand an
  application in a sink nothing drains.
- A source that routes nowhere is left alone entirely. Every cell starts at
  zero, so capturing an unrouted source would silence the application — the
  common case, not an edge one.
- Intake sinks are created with `object.linger=true`, which is mandatory: a
  sink created by `pw-cli` dies the instant `pw-cli` exits. They therefore
  outlive OpenWave, and are swept at startup if a crash left one behind.

### Intake sinks and the default-sink election

An intake sink is internal, but it is an ordinary sink as far as the session
manager is concerned, so it can be chosen as the system default. That has been
observed after a PipeWire restart, when the mix sinks were not yet present for
the election to consider.

The result is worse than an obvious failure: every application lands in one
source row, at that row's send level. Audio does not stop, it goes quiet and
arrives in the wrong place, and nothing on screen looks broken.

Intake sinks are therefore created with `priority.session=0`, which loses to
everything including every mix, and the default is moved back onto a mix at
startup if one has already won.

## Trim and send

Two levels apply to every source:

- **Trim** — the source row's own slider. That source's level everywhere.
- **Send** — the cell slider. How much of that source a given mix receives.

They multiply, and the product is written to the cell's loopback with `wpctl`.

The trim is deliberately *not* the intake sink's own volume. A null sink's
monitor is taken pre-volume, so a loopback reading that monitor never sees the
change — and the PulseAudio compatibility layer raises the stream to compensate
for a sink turned down, which inverted the control entirely: measured, a sink
at volume 0 produced a monitor at full scale. The loopback volume is the one
control that demonstrably attenuates.

## Claiming

Matching alone is not safe to route by. Two sources can match one stream — two
rows naming the same application, or one naming `Chromium` beside one naming
the binary `chromium`. Both would spawn loopbacks into the same mix, PipeWire
would sum them, and two sample-aligned copies is +6 dB. Each fader would
attenuate only its own copy, so pulling one to zero would leave the application
audible and slightly quieter: a broken-looking fader again.

`claim_streams` gives every stream exactly one owner, in the one place that
decides what gets spawned. Ownership is deterministic — most specific match
wins, ties broken on source id — so it cannot flip between polls and thrash the
loopbacks.

One source may be marked `catch_all`. It takes whatever no other source
claimed, so an application nobody has named still lands somewhere with a fader
instead of bypassing the matrix. An explicit name always wins.

## Outputs

Each mix resolves its own output device: an explicit choice, else the Wave's
own headphone jack, else the system default, else the highest-priority output.
A mix may also be **not monitored**, which is correct for one that exists only
to be captured — a mix feeding a voice application does not want to be in your
ears as well.

The default-sink step rarely fires: the monitoring mix is usually *itself* the
default sink, and mix sinks are never eligible as outputs, because feeding a
mix into itself would loop.

Output loopbacks are spawned **detached** — no `PR_SET_PDEATHSIG`, their own
session — so closing the window does not silence the machine. Cell loopbacks
are not: they are mixing state, and are rebuilt on the next start.

## Mixes as capture sources

Every mix is also published as an ordinary capture source, `<sink>_source`, so
a mix can be selected as a microphone in a voice application. Its monitor
already carries the same audio, but Discord and others filter monitor sources
out of their input lists entirely, so a mix chosen that way is unselectable.

The capture side is re-linked on every reconcile rather than once at creation.
Installing mixes destroys and recreates their sinks, and the new sink is a
different node: a loopback pinned to the old one keeps running against a dead
link, so the source still exists, is still selectable, and is silent. Nothing
else repairs that and nothing reports it, which is exactly the kind of failure
that looks like "Discord cannot hear me" and has no visible cause.

## Where state lives

| File | Written by | Holds |
|---|---|---|
| `~/.config/openwave/mixdefs.json` | `mixes.py` | mix identity: name, icon, sink, description |
| `~/.config/openwave/sources.json` | `sources.py` | source identity, bindings, trim |
| `~/.config/openwave/mixes.json` | `Mixer` | per-cell levels, plus a reserved `outputs` map |
| `~/.config/openwave/ui-state.json` | `app.py` | window geometry, gain lock |
| `~/.config/pipewire/pipewire.conf.d/52-openwave-mixes.conf` | generated | one null sink per mix |

Mix identity and per-cell levels are deliberately separate files: sharing one
would let a slider move clobber a definition.

`Mixer._state` is read once at construction and rewritten wholesale on save,
so an external process writing `mixes.json` while OpenWave runs will be
silently overwritten. Anything wanting to drive OpenWave from outside needs a
real interface, not a file.

## Naming

Nodes are addressed by `node.name`, never by id — ids are reassigned across
restarts.

| Pattern | What it is |
|---|---|
| `openwave_<mix>_mix` | a mix's null sink |
| `openwave_src_<source>` | an application source's intake sink |
| `openwave_loop_<source>_<mix>` | an application cell |
| `openwave_loop_dev_<source>_to_<mix>` | a capture-device cell |
| `openwave_loop_mic_to_<mix>` | the built-in microphone row |
| `openwave_loop_out_<mix>` | a mix's output, detached |
| `openwave_<mix>_mix_source` | a mix published as a capture source |

Startup sweeps orphaned loopbacks by matching `openwave_`, which covers both
the `openwave_loop_` cells and the mix capture sources named after their sink.
