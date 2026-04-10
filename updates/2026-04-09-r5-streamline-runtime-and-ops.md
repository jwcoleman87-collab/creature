# 2026-04-09-r5 - Streamline Runtime and Ops

## Goal
Make overnight operation simpler and less fragile by removing duplicate-run ambiguity and improving runtime visibility.

## What changed
- Added a single-instance runtime lock in `crypto_main.py`:
  - Prevents duplicate Creature launches on the same machine.
  - Clears stale lock files when old PID is no longer active.
- Added graceful stop-file handling in `crypto_main.py`:
  - Runtime checks `journal_data/creature.stop` each cycle.
  - On stop request, Creature runs normal shutdown flow and force-close logic.
- Added cycle heartbeat updates (`dashboard_state.mark_heartbeat`):
  - Every cycle now updates runtime metadata and `last_updated`.
  - Dashboard can detect stale data reliably.
- Added runtime metadata to dashboard state:
  - Instance ID, source, host, PID, cycle count, last cycle phase/status.
- Hardened dashboard startup in `crypto_main.py`:
  - Web server startup failure is logged and does not crash trading loop.
- Improved dashboard UI in `web_server.py`:
  - Runtime identity line (instance/source/host/PID/cycles/phase).
  - Stale-data warning banner when heartbeat age exceeds threshold.
  - Footer refresh label aligned to actual 10-second polling.
- Upgraded guard script `run_creature_guard.ps1`:
  - Guard lock file to prevent duplicate guard instances.
  - Guard status JSON output (`journal_data/guard.status.json`).
  - Stop-file handling (`journal_data/guard.stop`) for clean guard shutdown.
- Added operator control scripts:
  - `creature_start.ps1`
  - `creature_stop.ps1`
  - `creature_status.ps1`

## Operator workflow (new standard)
1. Start: `.\creature_start.ps1`
2. Check: `.\creature_status.ps1`
3. Stop cleanly: `.\creature_stop.ps1`
4. Force stop only if needed: `.\creature_stop.ps1 -Force`

## Why this matters
- Removes split-brain risk from accidental duplicate local launches.
- Makes liveness obvious even during quiet/no-trade cycles.
- Gives one clean, repeatable way to start/monitor/stop overnight sessions.
