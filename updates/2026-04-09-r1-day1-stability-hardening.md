# Revision 2026-04-09-r1

## Title
Day 1 stability hardening

## Executed changes
- Added per-cycle runtime logging (`cycle_events`) in SQLite.
- Added structured system event logging (`system_events`) in SQLite.
- Wrapped main loop cycles with exception capture and continuation.
- Added startup reconciliation safe-mode flow for orphan Alpaca positions.
- Added force-close support for orphan live symbols during startup reconcile.

## Files touched
- `crypto_main.py`
- `core/journal.py`
- `crypto_executor/__init__.py`

## Intent
Prioritize survival and observability over strategy expansion.
