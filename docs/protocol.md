# The Elgato Wave vendor protocol

What OpenWave knows about the USB protocol the Wave devices speak, as
implemented in [`wavexlr/device.py`](../wavexlr/device.py) and parameterised
per model in [`wavexlr/profiles.py`](../wavexlr/profiles.py). Per-device
support status lives in [hardware-support.md](hardware-support.md).

Provenance: reverse-engineered from the macOS Wave Link application using
Frida, then verified byte-for-byte against live hardware with
`python3 -m wavexlr.probe`. Nothing here is from vendor documentation.

## Transport

Everything is USB Class control transfers on endpoint 0:

| Field | Read | Write |
|---|---|---|
| `bmRequestType` | `0xA1` (class, interface, IN) | `0x21` (class, interface, OUT) |
| `bRequest` | `0x85` | `0x05` |
| `wValue` | selects the block (below) | selects the block |
| `wIndex` | `0x3303` | `0x3303` |

### Why `wIndex=0x3303`

Wave Link uses `wIndex=0x3300`, whose low byte routes the transfer through
interface 0 — owned by `snd-usb-audio` on Linux, which blocks it. The
firmware only checks the `0x33` prefix, so OpenWave sends `0x3303`: the
kernel sees interface 3 (unclaimed) and lets it through. No driver detach,
audio never interrupted.

### One process at a time

The firmware services vendor transfers from a single process. A second
reader gets `-EIO`; quit OpenWave (tray icon included) before probing.

### Writes are read-modify-write

There is no per-field write. OpenWave reads the whole config block, patches
the field, and writes the whole block back.

## Blocks

Three `wValue`-selected blocks, same on every model (lengths differ):

| Block | `wValue` | Wave XLR / Dock | Wave:3 |
|---|---|---|---|
| config | `0x0000` | 34 bytes | 16 bytes |
| meter | `0x0001` | 10 bytes | 8 bytes |
| devinfo | `0x000A` | 51 bytes | 64 bytes |

The meter block starts with two little-endian uint32 levels (left, right).

## Config block — Wave XLR and Wave XLR MK.2 / XLR Dock

`0fd9:007d` and `0fd9:00a6` share this layout byte for byte (the MK.2/Dock
was verified against live hardware). 34 bytes.

| Offset | Size / type | Field | Encoding |
|---|---|---|---|
| 0 | uint16 LE | Mic gain | 256 raw units per dB; max `0x5000` = 80 dB. Measured against the ALSA `Mic Capture Volume` control at 20/40/60/75 dB: `0x1400`/`0x2800`/`0x3C00`/`0x4B00`, exactly 256.00 raw/dB at every point |
| 4 | byte | Mute | `0x01` muted, `0x00` live |
| 6 | byte | 48 V phantom power | `0x01` on, `0x00` off. Found by watching the block while the dial was held: byte 6 flipped with the 48V LED and nothing else moved |
| 9 | int16 LE | Headphone volume | Q8.8 dB (raw / 256), 0 = unity, negative = attenuation |
| 14 | byte | Knob mode | `0x02` = knob drives headphone volume |
| 33 | byte | Low impedance mode | `0x01` on, `0x00` off |

Devinfo (51 bytes): API version at bytes 0–1 (`major.minor`), firmware at
6–8 (`x.y.z`), serial as ASCII at 27–46.

## Config block — Wave:3

`0fd9:0070`. 16 bytes.

| Offset | Size / type | Field | Encoding |
|---|---|---|---|
| 0 | uint16 LE | Mic gain | 256 raw/dB; max `0x2800` = 40 dB |
| 4 | byte | Mute | `0x01` muted |
| 7 | int16 LE | Headphone volume | Q8.8 dB |
| 10 | uint16 LE | Monitor mix | Q8.8 percent, max `0x6400` = 100 — the mic/PC crossfade |
| 12 | byte | Dial mode | `0x01` = gain, `0x02` = headphones, `0x03` = monitor mix |

Devinfo (64 bytes): API at 0–1, firmware at 21–23, serial at 36–47.

## Probing a device

`python3 -m wavexlr.probe` is the tool everything above was verified with:

```bash
python3 -m wavexlr.probe dump                    # config/meter/devinfo, hexdumped,
                                                 # with expected-vs-actual lengths
python3 -m wavexlr.probe dump --wvalue 0x2 --len 512   # explore an unknown block
python3 -m wavexlr.probe watch                   # poll config, print per-offset
                                                 # diffs while you move controls
python3 -m wavexlr.probe poke --noop             # write the block back unchanged
                                                 # (proves writes are accepted)
python3 -m wavexlr.probe poke --offset 6 --byte 0x01   # flip one byte (confirms)
```

The method that mapped every field above: `watch`, move exactly one physical
control, read which offset moved. Then `poke` the offset and confirm the
hardware reacts. `poke --noop` first — a device that rejects a full-block
write-back is telling you the layout is wrong before you change anything.

Mapping a new device is: add a `DeviceProfile` to `profiles.py` (copy the
closest existing one), `dump` to check the block lengths, `watch` to map
offsets, `poke` to confirm. See
[hardware-support.md](hardware-support.md#wave-xlr-mk2-00b6-revision) for
the device we are currently looking for.
