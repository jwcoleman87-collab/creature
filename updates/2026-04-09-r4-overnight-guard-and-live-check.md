# Revision 2026-04-09-r4

## Title
Overnight guard runner and live stability confirmation

## Executed changes
- Added `run_creature_guard.ps1` watchdog runner that restarts `crypto_main.py` if it exits.
- Launched Creature under the guard process.
- Confirmed live API health and timestamp movement across checks:
  - status remained `ONLINE`
  - `last_updated` advanced between polls
  - active position remained managed (`AAVE/USD`)

## Files touched
- `run_creature_guard.ps1`
- `updates/current_revision.json`
- `updates/2026-04-09-r4-overnight-guard-and-live-check.md`

## Operator note
Machine sleep and hibernate are already disabled (`AC/DC = 0`) on this host.
