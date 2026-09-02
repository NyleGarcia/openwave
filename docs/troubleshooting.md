# Troubleshooting audio faults

Field notes from faults observed on real hardware, with the diagnosis
method that found them. All of them share a property: every ordinary
check passes — nodes report `running`, bytes flow, volumes read fine —
and the audio is still wrong.

## Measuring xruns (the method)

PipeWire's per-node xrun counter is exported only by `pw-top`, and it is
**cumulative** over the node's lifetime — a big absolute number is
history, not a fault. Always diff two samples:

```sh
pw-top -b -n 3 | awk '{print $2,$9,$NF}' > /tmp/a; sleep 10
pw-top -b -n 3 | awk '{print $2,$9,$NF}' > /tmp/b
# compare ERR per node id between the files
```

At least 3 iterations are required: the first two print placeholder
zeros while the profiler warms up. A healthy node's delta is zero or a
handful at stream start; sustained accumulation is audible.

## Robotic / granular microphone

**Signature:** the Wave's capture node accumulates xruns continuously
(~23/s observed); recording sounds granular and robotic. Every
byte-level check passes.

**Cause:** the Wave lost the graph-driver election and follows a clock
its DLL cannot track. All ALSA nodes default `priority.driver = 2100`
and a tie falls to the lowest object id — observed handing the graph
clock to a wireless headset dongle whose jittery delivery the Wave
resynced against forever.

**Fix:** the shipped WirePlumber conf pins `priority.driver = 2500` on
Wave nodes so their wired isochronous clock drives. Verify with
`pw-dump`: other nodes' `driver-id` should point at the Wave capture
node.

## Crackles / pops on playback

**Signature:** every follower device xruns; the sinks pop a few times a
second. Worst while a WebRTC app (Discord) runs.

**Cause:** apps request small quanta (WebRTC asks for 360) and
full-speed USB followers miss deadlines below ~512 once they no longer
drive the clock. Measured: a headset dongle capture at 188 xruns/s and
~2 pops/s on its sink at quantum 360; zero xruns on every live node at
1024.

**Fix (user machine, not shipped):** floor the quantum —

```
# ~/.config/pipewire/pipewire.conf.d/90-min-quantum.conf
context.properties = {
  default.clock.min-quantum = 1024
}
```

1024 @ 48 kHz is 21.3 ms — fine for voice chat, required for clean
multi-device mixing. Apply live with
`pw-metadata -n settings 0 clock.min-quantum 1024`. For a stubborn
batch device, `api.alsa.headroom = 1024` in a WirePlumber rule adds
device-side slack.

## Silent output while everything reports running

**Signature:** the sink node runs, the graph delivers samples, volume
and mute read fine — and the hardware plays silence. Observed after a
WirePlumber restart recreated device nodes.

**Cause:** the ALSA PCM behind the sink stopped consuming; only the
kernel shows it, in `/proc/asound/cardN/pcmNp/subN/status` — `hw_ptr`
frozen (or `state: XRUN`) while the stream claims to run.

**Fix:** close and reopen the PCM: `pactl suspend-sink <name> 1`, then
`0`. The daemon's stall watchdog does this automatically, rate-limited.

## A source that xruns once per graph cycle, forever

**Signature:** one capture node's xrun delta exactly matches the graph
cycle rate (23/s at quantum 2048, 47/s at 1024) and never varies.

**Cause:** the source is muted at the ALSA level (`pactl list sources`
shows `Mute: yes`, or the card's `Capture Switch` is off) — a headset's
own mute button, or a stale state restore. It delivers digital silence;
the xruns are inaudible bookkeeping. `Status: Stop` in the card's
`/proc/asound` stream file with the node running is the same family:
reopen with `pactl suspend-source <name> 1` then `0`.

**Note:** the daemon's glitch watchdog ignores muted captures for this
reason, and the mixer syncs device-level mutes with their matrix rows,
so a mute engaged outside OpenWave shows in the window instead of
reading as a dead microphone.

## Watchdog behavior

Both daemon watchdogs (`wavexlr/health.py`) act at most twice per
incident, 60 s apart, then leave the device alone to be noticed — a
remedy that did not stick must not become a loop of audible pops. The
budget re-arms only after a sustained quiet stretch (5 min for the
glitch watch, 1 min of movement for the stall watch). Every remedy and
give-up is logged under `wavexlr.health` with the measured numbers;
`journalctl --user -u openwave.service` shows them.
