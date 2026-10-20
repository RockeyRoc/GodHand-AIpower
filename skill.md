---
name: godhand
description: Use GodHand's single MCP entrypoint to manage authorized AWD targets, deploy AoiAWD probes and start the bundled Windows monitoring services.
---

# GodHand

Use `godhand(action, arguments)` for the existing GodHand MCP service. Query `targets` and `capabilities` before choosing target IDs or operation parameters. Target credentials come from the local encrypted target store; never put them in reports or public source.

## AoiAWD deployment

- Core, the monitoring Web page and MongoDB run natively on Windows inside GodHand. Do not install them on the targets or require Docker. Use `defense_aoi_start`, `defense_aoi_status` and `defense_aoi_stop` with target `local`.
- Use `defense_aoi_probe` on a saved, authorized Linux target, then `defense_aoi_deploy`. File and process monitoring use RoundWorm. Set `web=true` for PHP traffic; targets without PHP explicitly skip that component. `watch_paths` is a list of absolute target directories.
- `elf_path` optionally produces a separate `<original>.guardianed` ELF for PWN monitoring. The original executable is preserved; changing a running challenge's launch configuration is a separate action.
- `defense_aoi_open` returns a loopback monitoring URL for the chosen target. GodHand owns a separate Core/database namespace per target and forwards the target's loopback probe port through the saved SSH connection.
- Every `run` returns a task ID. Read `task` and `logs` until terminal status; a submitted task is not proof of successful deployment or detection.
- `defense_aoi_remove` stops the owned probe and restores the recorded PHP changes. Concurrent edits are preserved when the exact inserted block can be removed; conflicting changes retain backups and require review.
- If GodHand or SSH stops, capture pauses. PHP monitoring preserves the application response and fails open on connection failure; missed traffic is not replayed later.

The deployment GUI exposes only setup and lifecycle actions. Existing Fail2Ban, Watchbird, Lynis, YARA and AIDE operations remain available through MCP.

## Verification and publishing

Verify file events, a controlled process, PHP requests/responses and a temporary test ELF against actual AoiAWD records. Distinguish unsupported components from successful tests. Restore test files and stop temporary services when verification finishes.

Keep the local Word documents in `docx/`. Exclude that folder from Git and portable packages. Keep the existing `docs/` documentation in both source and portable releases; do not exclude files elsewhere merely because their extension is `.docx`. Never publish saved targets, credentials, task databases or monitoring data.
