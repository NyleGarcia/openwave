---
title: Quit and keep audio running
tags: [spec, mixer, process, shutdown]
updated: 2026-09-29
status: draft
---

# Quit and keep audio running

Requested behaviour: a second quit choice, **Quit and keep audio running**, that closes the GUI but leaves OpenWave's mix/intake sinks, send/output/capture loopbacks, effect filters and stream placements in place. The next launch adopts them instead of reporting `Unproven existing sink` or `Existing unowned route`. Plain **Quit** stays unchanged and remains the default.

This is a design only. It is not implemented here, because a safe version is larger than one reviewable change (see [Size](#size)).

## Why this is not a small change

[ARCHITECTURE.md](../../ARCHITECTURE.md) makes two rules that this feature has to relax:

1. **Helper lifetime is the GUI's lifetime.** `OwnedChild::spawn_in` starts every `pw-loopback` and filter `pipewire` through `openwave-maintenance exec-child --parent-pid <gui>`. `process::exec_child` sets `PR_SET_PDEATHSIG(SIGTERM)` before `exec`. After exec, the helper's PDEATHSIG cannot be cleared from outside. When the GUI exits, every helper dies. No shutdown path can "detach" an already running helper. The only options are:
   - respawn every route at quit time without PDEATHSIG, which causes an audible gap and a race with teardown;
   - spawn every helper without GUI-death protection from the start, so a GUI crash orphans helpers (the current contract forbids this);
   - put a holder process between the GUI and the helpers (preferred, see [Design](#design)).
2. **Ownership is proven only by in-memory evidence.** `ensure_sink` accepts an existing node only in these cases:
   - it matches a live `OwnedSink { cookie, module, owner }` held by this `Reconciler`;
   - it matches a persistent `mixes.conf` definition (`definition_matches`: no `pulse.module.id`, plus the `openwave.mix-id` and `openwave.definition` markers).

   `route()` refuses any existing `<name>` or `<name>_cap` node. `restore_streams` refuses to move streams out of an intake it cannot prove it owns. On a new launch all of that evidence is gone. The owner UUIDs, module ids and the `moved` original-sink map live only in the old process.

Adoption therefore needs a **persisted, verifiable ownership record** and a **child type that can supervise a process this GUI did not fork**. Both touch the process boundary, the mixer reconciler, controller shutdown and the desktop UI.

## Design

### 1. Holder process (process.rs, openwave-maintenance)

- Add a new maintenance subcommand, `hold-children --parent-pid <gui> --control-fd <n>`, spawned lazily once per GUI.
  - It sets `PR_SET_CHILD_SUBREAPER`.
  - It sets its own PDEATHSIG to the GUI **only as a trigger**: on SIGTERM from the GUI's death it checks the detach flag. If the flag is unset, it terminates its children (today's behaviour). If the flag is set, it exits and leaves them running.
- Helpers are spawned by the holder, not by the GUI. `exec-child --parent-pid <holder>` keeps PDEATHSIG, now tied to the holder.
- The control socket carries these messages:
  - `spawn(program, args)` → `{pid, start_time}`
  - `terminate(pid)`
  - `detach`, which disarms the kill-on-GUI-death and then exits cleanly
- If the GUI crashes, it never sends `detach`, so the holder kills everything and the crash behaviour is unchanged.
- stderr: helpers cannot keep a pipe to the GUI. The holder sends helper stderr to a bounded per-helper log in `$XDG_RUNTIME_DIR/openwave/` or to `/dev/null` after detach, which avoids SIGPIPE/EPIPE on a closed reader.

### 2. Adoptable child (mixer.rs `RoutingChild`)

- Add `AdoptedChild { pidfd, pid, start_time, exe, owner }`, which implements `RoutingChild`.
  - `running()` uses `pidfd` poll/`waitid(P_PIDFD, WNOHANG)`. Before any signal, it re-reads `/proc/<pid>/stat` to confirm the start time.
  - `terminate()` uses `pidfd_send_signal` TERM, then KILL after `TERM_GRACE`.
  - The process is never reaped by us, because the subreaper or init owns it.
- Adoption requires an exact identity match:
  - pid;
  - `/proc/<pid>/stat` start time (field 22);
  - exe inode equal to the resolved `pw-loopback`/`pipewire`;
  - an argv that contains the recorded `openwave.owner` token.

  Anything short of all four is treated as foreign.

### 3. Detach record (new `mixer::handoff` module)

- Written atomically to `$XDG_RUNTIME_DIR/openwave/handoff.json` (mode 0600, private 0700 dir). It lives in the runtime dir, not `~/.config`, so a reboot or logout invalidates it naturally.
- Contents:
  - schema version;
  - installation identity;
  - PipeWire core cookie;
  - holder pid + start time;
  - desired-state revision hash;
  - `sinks: {name, module, owner}`;
  - `routes: {key, name, owner, spec, pid, start_time}`;
  - `effects: {source_id, name, owner, spec, pid, start_time, config_path}`;
  - `moved: {stream object_serial, original_sink}`.
- Effect configs are currently `NamedTempFile`s dropped with the `Effect`. In detach mode they are persisted beside the record and removed when the effect is dropped after adoption.
- The record is consumed once, by rename to `handoff.json.claimed`, before adoption starts, so two launches cannot both adopt.

### 4. Detach shutdown path

- Add `AppCommand::Shutdown { keep_audio: bool }`, or a separate `DetachShutdown`, and a `BackendCommand` equivalent.
- `ActiveWorkers::stop` drains health, calibration, devices and meters as today. It then calls `Mixer::detach()` instead of `Mixer::stop()`.
- `Mixer::detach()` sets `stopped` and joins the worker. The worker runs `Reconciler::handoff()` instead of `teardown()`:
  1. Take one fresh cleanup graph. If the cookie changed, or the graph is unknown, fall back to full `teardown()`: detaching nothing is the safe default.
  2. Serialise the record.
  3. Tell the holder `detach`.
  4. Forget the children without terminating them.

  No stream restoration and no sink unload happen.
- Any failure before the record is durably written, or before `detach` is acknowledged, falls back to ordinary teardown. The UI reports "Audio could not be kept running; restored normally", not success. A failure after `detach` is acknowledged leaves helpers running with a valid record. That state is recoverable on the next launch (see [Adoption on launch](#5-adoption-on-launch)).
- Leases: vendor and installation leases are released normally. The detached graph holds no USB and no installation authority, so an uninstall must still be able to run. Uninstall must first clean up the handoff; see the risks below.

### 5. Adoption on launch

- In `Reconciler::new` or on the first `cycle()`: if a record exists, and its installation identity **and** PipeWire cookie match the first snapshot, claim it and seed the following from the record:
  - `sinks` (only if `graph.owned(name, owner)` and `pulse.module.id` match);
  - `routes` (with `AdoptedChild`, only if the pid identity check passes);
  - `effects`;
  - `moved`.
- Unmatched entries are handled as follows:
  - Sinks are left alone and reported. They are never unloaded without proof.
  - Processes that pass the pid identity check but whose graph nodes are missing are terminated through the pidfd.
  - Processes that fail the identity check are never signalled.
- A cookie mismatch (audio server restarted) invalidates every recorded PipeWire/Pulse id. The record is discarded, and any helper processes that still pass the pid check are terminated. This matches `observe_cookie`.
- After seeding, normal reconciliation runs unchanged. Routes whose spec no longer matches desired state are replaced, and masters are re-applied to the same identities, so saved levels win.

### 6. UI

- The primary menu gets **Quit and keep audio running** (`app.quit-keep-audio`). The tray keeps plain **Quit** and optionally gets the same second item. The shortcut stays on plain Quit.
- The status line says that audio stays routed until OpenWave is started again, or until logout.
- A frozen "Shutdown incomplete" window offers only a normal retry, never a detach, because a failed teardown has already partly released ownership.

## Size

These are rough estimates against the current tree:

| Area | Lines |
|---|---|
| Holder subcommand + protocol + spawn indirection (process.rs, maintenance bin) | ~250 |
| `AdoptedChild` + pid identity (process.rs / mixer.rs) | ~120 |
| Handoff record, detach path, adoption in `Reconciler` | ~300 |
| Controller/NativeBackend command + shutdown variant | ~80 |
| Desktop menu/tray/status | ~50 |
| Tests (fixture backend adoption/refusal/cookie change; holder crash vs detach; record races) | ~350 |
| **Total** | **~1150** |

That is roughly twice the single-change budget (~600), and it changes the process-lifetime contract that the architecture treats as normative. It should land as a staged series, not as one change.

## Risks

- **Orphaned helpers.** If the user never relaunches, loopbacks and filters run until logout. They use CPU, keep devices awake, and can suspend or prevent suspend. Mitigation: the record and helpers live in the runtime dir and in the user session scope. Future option: a user systemd scope/slice for the holder, so `systemctl --user stop` is a documented kill switch.
- **Stale record.** A PipeWire restart, reboot or reinstall invalidates it. The cookie, installation identity and pid start-time checks make it inert, never a licence to delete by name.
- **Pid reuse.** Covered by pid + start time + exe inode + owner token in argv, and by using pidfds after the check.
- **Daemon interaction.** `openwave-daemon` holds capture pins and health observation independently. Detached loopbacks read the same captures, so pins still help, and there is no conflict because the daemon never touches `openwave_*` routes. However, the GUI's observe-only health monitor is gone while detached, so output/capture faults go unobserved unless the daemon is running.
- **Uninstall.** Removal must tear down a detached graph first. That requires adopting the record, or a record-driven cleanup in `openwave-maintenance`. Otherwise it deletes the binaries that live helpers are executing from.
- **Shutdown incomplete.** The detach path must never convert a failed teardown into "kept running". A detach attempt that fails before `detach` is acknowledged must fall back to full teardown and report that.
- **Streams in intakes.** Application streams stay in `openwave_src_*` intakes while detached. If the record is lost, they stay stranded until the audio session restarts. The next launch should still offer today's "unproven" message, not guess.

## Staged plan

1. **Holder indirection only**, with no behaviour change: helpers are spawned via the holder, and a GUI crash or exit still kills them. Tests: holder kill-on-parent-death, spawn/terminate round trip, and a refusal to run as root.
2. **`AdoptedChild` + pid identity checks**: add them with unit tests using `sleep` fixtures (start-time mismatch refused, foreign argv refused).
3. **Handoff record + adoption**, driven only from tests via `start_with_backend`: fixture-graph tests for these cases:
   - adopt exact sinks/routes;
   - refuse on cookie change;
   - refuse on owner/module mismatch;
   - one-shot claim;
   - fallback to teardown on a record write failure.
4. **Controller detach command + UI entry**: `DetachShutdown` through `NativeBackend`; ignored GTK test for the menu item; an ARCHITECTURE.md section on the new lifetime rule.
5. **Uninstall/maintenance cleanup of a detached record.**

Each stage is independently reviewable. Only stage 4 exposes the feature.
