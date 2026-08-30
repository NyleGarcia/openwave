# Spec: Diagnostics export

Gap: openxlr ships one-click diagnostics export (`docs/comparison.md`,
"Quality of life"); OpenWave asks reporters to run probe by hand. This also
feeds the `00b6` hardware hunt (`docs/hardware-support.md` call-to-action).

## Deliverable

`python3 -m wavexlr.diag` → one timestamped plain-text file
(`openwave-diag-YYYYMMDD-HHMMSS.txt`), path printed; plus an "Export
diagnostics" button in the sidebar's service section writing the same
bundle via a save dialog.

## Sections

| Section | Source | Notes |
|---|---|---|
| Versions | OpenWave version, Python, GTK/Adwaita, PipeWire, distro | best-effort |
| Device | detected profile, vid:pid, fw/API/serial, config + devinfo hexdump | via the running GUI's handle when open, else direct connect (probe path) |
| USB | supported IDs present on the bus (`wave_present` logic, per-pid) | |
| udev | `udev_installed()` result + which rule file matched | |
| Service | init system, installed/running state, keepalive watchdog state | `service.py` |
| Journal | last ~100 lines of the user daemon unit (systemd only) | `journalctl --user -u openwave` |
| PipeWire | openwave nodes from `pw-dump` (names, states, links), `wpctl status` | filter to `openwave_*` + Elgato nodes |
| Config | which config files exist + sizes + parse-ok flag | contents only with `--full` (app names are personal) |

Every collector is isolated: a failed or absent source prints
`<section>: unavailable (<reason>)` — never a traceback, never a hang
(subprocess timeouts like the rest of the codebase, 3 s).

## Design decisions

- **Plain text, one file.** Attachable to a GitHub issue inline; no tarball
  until something binary needs shipping.
- **Privacy default-on**: no config contents, no full `pw-dump` (stream
  names reveal running apps) without `--full`. Serials stay — they are
  already how hardware reports are matched.
- **Reuse, don't duplicate**: hexdump from `probe.py`, presence from
  `device.wave_present`, service state from `service.py`, paths from
  `paths.py`. The module is assembly, not new probing.
- **GUI handle sharing**: firmware serves one process; when the GUI is open
  the CLI cannot read the device. CLI says so and continues; the in-app
  button uses the GUI's own handle, so it always gets device data. This is
  why the button exists and is the recommended path in README.

## Verification

- Unit: assemble bundle with all collectors faked (present/absent/raising);
  assert section headers, no exception escapes.
- Manual: run CLI with device attached + GUI closed, GUI open (device
  section says held), no device at all.
- Update README "Reporting problems" + hardware-support call-to-action to
  lead with the export.
