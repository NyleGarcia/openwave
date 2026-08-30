# Later — ice box

Ideas acknowledged, not committed. Promote to `next/` deliberately.

- **`0fd9:00b6` Wave XLR MK.2 revision support** — blocked on hardware.
  CryoByte33/openwave decoded it (UAC2, different control scheme). Unblocks
  via a community `probe dump`/`watch` capture; docs/hardware-support.md
  already asks for it. Diagnostics export (now-sprint) lowers the bar.
- **Wave XLR Pro support** — blocked on hardware; openxlr has the protocol
  documented (`docs/wave-xlr-pro-protocol.md` in their tree) — a port
  candidate with credit, same as the CryoByte33 borrowings.
- **Scene switching from the tray menu** — after profiles/scenes v1.
- **Restructuring scenes** — scenes that add/remove mixes and sources, not
  just set levels. Only if v1 usage shows the need.
- **Audio flow visualization** — openxlr has live flow viz; OpenWave's
  matrix arguably *is* the visualization. Revisit only on user ask.
- **Compressor/expander DSP** — second wave of the DSP chain (`plans/next/dsp-chain.md`).
- **Flatpak** — metainfo.xml exists now; a manifest is the missing piece.
  Sandboxed USB + pkexec setup are real obstacles; investigate before
  promising.
