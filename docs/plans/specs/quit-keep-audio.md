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

The current code enforces two rules that this feature has to relax:

1. **Helper lifetime is the GUI's lifetime.** This rule lives in the code, not in [ARCHITECTURE.md](../../ARCHITECTURE.md). `OwnedChild::spawn_in` (`process.rs`) starts every `pw-loopback` and filter `pipewire` through `openwave-maintenance exec-child --parent-pid <gui>`. `process::exec_child` calls `set_parent_process_death_signal(Some(Signal::TERM))` before `exec`, and PDEATHSIG survives `exec`. After exec, the helper's PDEATHSIG cannot be cleared from outside, and it cannot be armed toward a process that is not the parent. When the GUI exits, every helper dies. No shutdown path can "detach" an already running helper. The only options are:
   - respawn every route at quit time without PDEATHSIG, which causes an audible gap and a race with teardown;
   - spawn every helper without GUI-death protection from the start, so a GUI crash orphans helpers (today's code never allows this);
   - put a long-lived holder process between the GUI and the helpers, so the helpers' PDEATHSIG is bound to the holder instead of the GUI (preferred, see [Design](#design)).
2. **Ownership is proven only by in-memory evidence.** `ensure_sink` accepts an existing node only in these cases:
   - it matches a live `OwnedSink { cookie, module, owner }` held by this `Reconciler`;
   - it matches a persistent `mixes.conf` definition (`definition_matches`: no `pulse.module.id`, plus the `openwave.mix-id` and `openwave.definition` markers).

   `route()` refuses any existing `<name>` or `<name>_cap` node. `restore_streams` refuses to move streams out of an intake it cannot prove it owns. On a new launch all of that evidence is gone. The owner UUIDs, module ids and the `moved` original-sink map live only in the old process.

Adoption therefore needs a **persisted, verifiable ownership record** and a **supervisor that outlives the GUI and that a new GUI can reattach to**. Both touch the process boundary, the mixer reconciler, controller shutdown and the desktop UI.

## Design

The model is: **the holder is the only parent of every helper, and it stays alive for as long as any helper does.** Helpers keep PDEATHSIG, bound to the holder. The holder watches the GUI through a pidfd, never through PDEATHSIG, so a relaunched GUI can take over the watch.

### 1. Holder process (process.rs, openwave-maintenance)

- Add a new maintenance subcommand, `hold-children --gui-pid <gui>`, spawned lazily once per GUI.
  - The GUI spawns it **without** PDEATHSIG. It is the only process OpenWave starts that way.
  - It opens a pidfd for the GUI (`pidfd_open`), after checking that `/proc/<gui>/stat` start time matches the value the GUI passed, and polls it.
  - It listens on `$XDG_RUNTIME_DIR/openwave/holder.sock` (mode 0600 inside the private 0700 dir) and accepts only peers whose `SO_PEERCRED` uid is its own uid. It refuses to run as root.
- Helpers are forked by the holder, not by the GUI. The holder execs `openwave-maintenance exec-child --parent-pid <holder> -- …`, so each helper's PDEATHSIG is bound to the holder. Helpers are direct children of the holder, so the holder reaps them and no `PR_SET_CHILD_SUBREAPER` is needed.
- Control messages:
  - `spawn(program, args)` → `{pid, start_time}`
  - `terminate(pid)`: TERM, then KILL after `TERM_GRACE`; the holder reaps.
  - `list` → the live children with `{pid, start_time}`
  - `detach`: disarm kill-on-GUI-death for the current GUI. The holder **keeps running** and keeps its children.
  - `attach(gui_pid, gui_start_time)`: re-point the GUI watch to a new GUI pidfd and re-arm kill-on-GUI-death.
- GUI-death handling, when the GUI pidfd fires:
  - not detached (GUI crash or normal Quit that did not finish teardown): TERM every child, reap, remove the socket, exit. Crash behaviour is unchanged from today.
  - detached: do nothing. Keep supervising the children, and wait for an `attach` or for the last child to exit.
- The holder exits when it has no children left and no attached GUI, for example after the next GUI adopts and later tears everything down with a normal Quit.
- Holder death: if the holder itself dies (crash, `SIGKILL`, `systemctl --user stop`), the kernel sends every helper SIGTERM through PDEATHSIG. A holder crash therefore never orphans helpers; it drops the audio instead. While a GUI is attached, the GUI sees its routes disappear and reports them through the normal reconcile path.
- stderr: helpers cannot keep a pipe to a GUI that may leave. The holder owns the helpers' stderr and copies it to a bounded per-helper log in `$XDG_RUNTIME_DIR/openwave/`. An attached GUI reads recent lines through the control socket. That avoids SIGPIPE/EPIPE on a closed reader.
- `ps` / `systemctl --user` view: helpers always have the holder as parent. After detach the holder's parent is gone, so it is reparented to the nearest subreaper, normally the user's `systemd --user` manager, and it stays inside the GUI's original session scope/cgroup. Stage 5 may move it into its own transient user scope (for example `openwave-holder.scope`) so `systemctl --user stop openwave-holder.scope` is a documented kill switch.

### 2. Adopted child (mixer.rs `RoutingChild`)

- Add `HeldChild { holder, pid, start_time, pidfd }`, which implements `RoutingChild`. It is used for every helper in holder mode, whether the current GUI spawned it or adopted it.
  - `running()` polls a pidfd that the GUI opens itself (`pidfd_open` works on non-children for readiness polling). Before trusting it, the GUI re-reads `/proc/<pid>/stat` to confirm the start time.
  - `terminate()` sends `terminate(pid)` to the holder. The GUI never signals or reaps helpers directly, because they are not its children.
- Adoption goes through the holder, never around it. The new GUI connects to the recorded socket and checks:
  - the holder's pid and `/proc/<pid>/stat` start time (field 22) match the record;
  - the socket peer's `SO_PEERCRED` pid equals that holder pid.

  Then it sends `attach`, which re-arms crash protection for the new GUI **before** any adoption. Then it calls `list`. A recorded helper is adopted only if all of these match:
  - it appears in the holder's `list` with the recorded pid and start time;
  - `/proc/<pid>/exe` equals the resolved target of the `pw-loopback`/`pipewire` binary (on distros where `pw-loopback` is a symlink or wrapper to `pipewire`, `exe` resolves to the target, so the comparison is against the resolved target, not the name);
  - its argv contains the recorded `openwave.owner` token.

  Anything short of all of these is treated as foreign. After adoption, every helper again has the same protection it has today: its PDEATHSIG is bound to the holder, and the holder kills it if the new GUI crashes.

### 3. Detach record (new `mixer::handoff` module)

- Written atomically to `$XDG_RUNTIME_DIR/openwave/handoff.json` (mode 0600, private 0700 dir). It lives in the runtime dir, not `~/.config`, so a reboot or logout invalidates it naturally.
- Contents:
  - schema version;
  - installation identity;
  - PipeWire core cookie (global; it is also the `server_cookie` half of every `NodeIdentity` below);
  - holder pid + start time + socket path;
  - desired-state revision hash;
  - `sinks: {name, module, owner}`;
  - `routes: {key, name, owner, spec, pid, start_time}`;
  - `effects: {source_id, name, owner, spec, pid, start_time, config_path}`;
  - `moved: {identity: NodeIdentity { server_cookie, object_serial }, original_sink}`, matching the key of `Reconciler.moved`.
- Effect configs are currently `NamedTempFile`s dropped with the `Effect`. In detach mode they are persisted beside the record and removed when the effect is dropped after adoption.
- The record is consumed once, by rename to `handoff.json.claimed`, before adoption starts, so two launches cannot both adopt. The holder also accepts only one attached GUI at a time.

### 4. Detach shutdown path

- Add `AppCommand::Shutdown { keep_audio: bool }`, or a separate `DetachShutdown`, and a `BackendCommand` equivalent.
- `ActiveWorkers::stop` drains health, calibration, devices and meters as today. It then calls `Mixer::detach()` instead of `Mixer::stop()`.
- `Mixer::detach()` sets `stopped` and joins the worker. The worker runs `Reconciler::handoff()` instead of `teardown()`:
  1. Take one fresh cleanup graph. If the cookie changed, or the graph is unknown, fall back to full `teardown()`: detaching nothing is the safe default.
  2. Serialise the record.
  3. Tell the holder `detach` and wait for its acknowledgement.
  4. Forget the `HeldChild` handles without terminating them.

  No stream restoration and no sink unload happen.
- Any failure before the record is durably written, or before `detach` is acknowledged, falls back to ordinary teardown. The UI reports "Audio could not be kept running; restored normally", not success. A failure after `detach` is acknowledged leaves helpers running under the holder with a valid record. That state is recoverable on the next launch (see [Adoption on launch](#5-adoption-on-launch)).
- Leases: `NativeBackend::shutdown` today releases the vendor and installation leases only after successful joins and owned-graph cleanup (`controller/native.rs`, "Successful joins and owned graph cleanup precede lease release"). A detach does not run `cleanup_sinks`, so the detach path must explicitly count an acknowledged `detach` with a durable record as "owned cleanup succeeded" for lease purposes. Otherwise shutdown stays frozen. The detached graph holds no USB and no installation authority, so an uninstall must still be able to run. Uninstall must first clean up the handoff; see the risks below.

### 5. Adoption on launch

- In `Reconciler::new` or on the first `cycle()`: if a record exists, and its installation identity **and** PipeWire cookie match the first snapshot, claim it. Then connect to the holder and `attach` as in section 2. Only after `attach` succeeds, seed the following from the record:
  - `sinks` (only if `graph.owned(name, owner)` and `pulse.module.id` match);
  - `routes` (as `HeldChild`, only if the holder and pid identity checks pass);
  - `effects`;
  - `moved`.
- If the holder is gone, or fails its identity check, its helpers are already dead (PDEATHSIG) or not ours. The record is discarded, no process is signalled, and the launch continues with today's behaviour.
- Unmatched entries are handled as follows:
  - Sinks are left alone and reported. They are never unloaded without proof.
  - Holder children that pass the pid identity check but whose graph nodes are missing are terminated through the holder.
  - Processes that fail the identity check are never signalled.
- A cookie mismatch (audio server restarted) invalidates every recorded PipeWire/Pulse id. The record is discarded. If the holder passes its identity check, the GUI attaches and asks it to terminate every child, which also lets it exit. This matches `observe_cookie`.
- After seeding, normal reconciliation runs unchanged. Routes whose spec no longer matches desired state are replaced through the holder, and masters are re-applied to the same identities, so saved levels win.

### 6. UI

- The primary menu gets **Quit and keep audio running** (`app.quit-keep-audio`). The tray keeps plain **Quit** and optionally gets the same second item. The shortcut stays on plain Quit.
- The status line says that audio stays routed until OpenWave is started again, or until logout.
- A frozen "Shutdown incomplete" window offers only a normal retry, never a detach, because a failed teardown has already partly released ownership.

## Size

These are rough estimates against the current tree:

| Area | Lines |
|---|---|
| Holder subcommand + socket protocol + attach/detach + spawn indirection (process.rs, maintenance bin) | ~350 |
| `HeldChild` + holder and pid identity checks (process.rs / mixer.rs) | ~100 |
| Handoff record, detach path, adoption in `Reconciler` | ~300 |
| Controller/NativeBackend command + shutdown variant + lease rule | ~80 |
| Desktop menu/tray/status | ~50 |
| Tests (fixture backend adoption/refusal/cookie change; holder GUI crash vs detach vs reattach; holder death kills helpers; record races) | ~400 |
| **Total** | **~1280** |

That is about twice the single-change budget (~600), and it changes the process-lifetime rule that the code enforces today. It should land as a staged series, not as one change.

## Risks

- **Orphaned audio after detach.** If the user never relaunches, the holder and its loopbacks and filters run until logout. They use CPU, keep devices awake, and can suspend or prevent suspend. Mitigation: everything lives in the runtime dir and in the user session. Killing the holder (`kill <holder>` or, after stage 5, `systemctl --user stop openwave-holder.scope`) takes every helper down with it through PDEATHSIG.
- **Holder as a single point of protection.** Between detach and relaunch, and after adoption, helpers are protected only by the holder: its PDEATHSIG kills them if it dies, and its GUI pidfd watch kills them if an attached GUI crashes. A holder that is alive but wedged (deadlocked, stopped with SIGSTOP) protects nothing: helpers keep running after a GUI crash until the holder is killed. The holder must therefore stay small and single-threaded, with no audio work, and the GUI should report an unresponsive holder socket as a fault. This is new compared with today, where the kernel alone ties helpers to the GUI. The stage 4 ARCHITECTURE.md section must state this rule.
- **Holder death drops audio.** A holder crash kills all helpers, including while a GUI is attached. That is the chosen failure mode (no orphans), but it is a new way to lose audio mid-session. The attached GUI's reconcile loop recreates the routes through a new holder.
- **Stale record.** A PipeWire restart, reboot or reinstall invalidates it. The cookie, installation identity, holder identity and pid start-time checks make it inert, never a licence to delete by name.
- **Pid reuse.** Covered by holder pid + start time + `SO_PEERCRED`, helper pid + start time in the holder's own child list, resolved exe, and owner token in argv, and by using pidfds after the check.
- **Daemon interaction.** `openwave-daemon` holds capture pins and health observation independently. Detached loopbacks read the same captures, so pins still help, and there is no conflict because the daemon never touches `openwave_*` routes. However, the GUI's observe-only health monitor is gone while detached, so output/capture faults go unobserved unless the daemon is running.
- **Uninstall.** Removal must tear down a detached graph first. That requires attaching to the holder and adopting the record, or a record-driven cleanup in `openwave-maintenance`. Otherwise it deletes the binaries that the holder and live helpers are executing from.
- **Shutdown incomplete.** The detach path must never convert a failed teardown into "kept running". A detach attempt that fails before `detach` is acknowledged must fall back to full teardown and report that.
- **Streams in intakes.** Application streams stay in `openwave_src_*` intakes while detached. If the record is lost, they stay stranded until the audio session restarts. The next launch should still offer today's "unproven" message, not guess.

## Staged plan

1. **Holder indirection only**, with no behaviour change: helpers are spawned via the holder, `detach` does not exist yet, and a GUI crash or exit still kills them through the holder's GUI pidfd. Tests: GUI death kills children, holder death kills children, spawn/terminate round trip, and a refusal to run as root.
2. **`attach`/`detach` + `HeldChild` identity checks**: unit tests using `sleep` fixtures (start-time mismatch refused, foreign argv refused, wrong peer refused, detached holder survives GUI death, reattached holder kills on second GUI death).
3. **Handoff record + adoption**, driven only from tests via `start_with_backend`: fixture-graph tests for these cases:
   - adopt exact sinks/routes;
   - refuse on cookie change;
   - refuse on owner/module mismatch;
   - refuse when the holder is gone or fails its identity check;
   - one-shot claim;
   - fallback to teardown on a record write failure.
4. **Controller detach command + UI entry**: `DetachShutdown` through `NativeBackend`, including the lease rule; ignored GTK test for the menu item; an ARCHITECTURE.md section on the new lifetime rule (holder-bound helpers, holder-only protection, holder reparenting).
5. **Uninstall/maintenance cleanup of a detached record**, and optionally a dedicated user scope for the holder.

Each stage is independently reviewable. Only stage 4 exposes the feature.
