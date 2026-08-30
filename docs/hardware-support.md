# Hardware support

Format modeled on [openxlr's hardware-support page](https://github.com/emaspa/openxlr/blob/main/docs/hardware-support.md).

Per-device status for everything OpenWave knows about. The USB protocol
itself — transport, register maps, encodings — is in
[protocol.md](protocol.md); per-model constants live in
[`wavexlr/profiles.py`](../wavexlr/profiles.py).

Legend: 🟢 verified on hardware · 🟡 decoded, needs real-hardware testing ·
⚪ not supported / unknown.

| Device | USB ID | Status |
|---|---|---|
| [Wave XLR](#wave-xlr) | `0fd9:007d` | 🟢 |
| [Wave XLR MK.2 / XLR Dock](#wave-xlr-mk2--xlr-dock) | `0fd9:00a6` | 🟢 |
| [Wave:3](#wave3) | `0fd9:0070` | 🟢 |
| [Wave XLR MK.2 (`00b6` revision)](#wave-xlr-mk2-00b6-revision) | `0fd9:00b6` | ⚪ |

## Wave XLR

`0fd9:007d` — the original XLR interface. 34-byte config block.

| Control | Status | Notes |
|---|---|---|
| Mic gain | 🟢 | uint16 @0, 256 raw/dB, max `0x5000` = 80 dB |
| Mute | 🟢 | byte @4, syncs with the hardware mute pad and ALSA |
| 48 V phantom power | 🟢 | byte @6; found by diffing the block across a toggle, confirmed against the +48V LED |
| Headphone volume | 🟢 | int16 Q8.8 @9, syncs with the knob and ALSA |
| Knob mode readout | 🟢 | byte @14, `0x02` = knob drives headphones |
| Low impedance mode | 🟢 | byte @33 |
| Device info | 🟢 | firmware, API version, serial |
| Meters | 🟢 | 10-byte block, two uint32 levels |
| Monitor mix | ⚪ | no such field on this device |

## Wave XLR MK.2 / XLR Dock

`0fd9:00a6` — enumerates as "Elgato XLR Dock" but speaks the original Wave
XLR's vendor protocol byte for byte: a probe dump against hardware decodes
gain @0, mute @4, HP volume @9 and low-Z @33 exactly as the original, and the
serial at offset 27 matches the ALSA card serial. Everything in the Wave XLR
table above applies, verified on a live Dock.

One difference matters: the Dock has **no front-panel phantom button at
all**, so OpenWave's switch is the only way to toggle 48 V on this hardware.

Not to be confused with the `0fd9:00b6` revision below, which is a different
device.

## Wave:3

`0fd9:0070` — the USB microphone. 16-byte config block.

| Control | Status | Notes |
|---|---|---|
| Mic gain | 🟢 | uint16 @0, 256 raw/dB, max `0x2800` = 40 dB; mirrored into ALSA |
| Mute | 🟢 | byte @4, syncs with the capacitive mute and ALSA |
| Headphone volume | 🟢 | int16 Q8.8 @7 |
| Monitor mix | 🟢 | uint16 Q8.8 percent @10, max `0x6400`; the mic/PC crossfade, exposed as a sidebar slider |
| Dial mode readout | 🟢 | byte @12: 1 = gain, 2 = headphones, 3 = mix |
| Device info | 🟢 | firmware, API version, serial |
| Meters | 🟢 | 8-byte block |
| Low impedance / phantom | ⚪ | no XLR input, no such fields |

## Wave XLR MK.2 (`00b6` revision)

`0fd9:00b6` — a different MK.2 revision: a USB Audio Class 2 device with a
control scheme unlike the `00a6` Dock's.
[CryoByte33/openwave](https://github.com/CryoByte33/openwave) decoded its
vendor protocol; this tree defers it only for lack of that hardware.

**Have one?** That is exactly the missing piece. Start with **Export
diagnostics** in the sidebar (or `python3 -m wavexlr.diag`) for the
overview, then quit OpenWave (including the tray icon — the firmware serves
one process at a time) and capture:

```bash
python3 -m wavexlr.probe dump            # config / meter / devinfo blocks
python3 -m wavexlr.probe watch           # per-offset diffs while you move each control
```

Open an issue with the output and which physical control you moved for each
diff. See [protocol.md](protocol.md) for how the probe maps fields.

## Implementation notes

- **`wIndex=0x3303`** — vendor transfers officially route through
  `wIndex=0x3300` (interface 0), which `snd-usb-audio` owns and blocks. The
  firmware only checks the `0x33` prefix, so OpenWave uses `0x3303`: the
  kernel sees unclaimed interface 3 and lets it through. No driver detach,
  audio never interrupted.
- **One process at a time** — the firmware services vendor transfers from a
  single process; a second reader gets `-EIO`.
- **ALSA controls found by name suffix, not numid** — the numids 4/5/6 hold
  on the hardware in hand but are not promised across firmware revisions;
  the control names vary only in their product-string prefix, so the suffix
  ("Capture Switch", "Capture Volume", "Playback Volume") is the stable
  handle. Ported from CryoByte33/openwave, verified on a live `00a6` Dock.
- **ALSA card matched by `usbid`, not name** — every profile's name match
  ends in "Elgato", so with two Elgato devices connected, name matching
  resolved them all to whichever card came first. `/proc/asound/card*/usbid`
  disambiguates by vid:pid, and `usbbus` splits two of the same model.
- **Control ranges read from the driver** — ALSA maxima differ per device
  and kernel; they are read from `amixer` and cached rather than assumed.
