# Revision 2026-04-09-r3

## Title
Paper activity tune, UTC timestamp clarity, and reconcile precision fix

## Why
Creature was running but too often idle due to strict signal gates.
UI timestamps looked confusing versus local clock because mind log times were UTC without label.

## Executed changes
- Lowered paper-mode signal strictness in constitution:
  - `risk.crypto.min_score_to_backtest`: `3.0 -> 2.0`
  - `crypto.strategy.sub_strategies.momentum.z_score_entry_threshold`: `1.5 -> 1.2`
  - `crypto.strategy.sub_strategies.mean_reversion.rsi_oversold`: `35 -> 40`
  - `crypto.strategy.sub_strategies.volume.min_vol_ratio`: `1.2 -> 1.0`
  - Added `crypto.strategy.sub_strategies.mean_reversion.bb_entry_max: 30`
- Updated scanner and backtester to use configurable `bb_entry_max` instead of hardcoded `20`.
- Updated dashboard mind-log timestamps to include explicit `UTC` label.
- Patched orphan-close precision in `crypto_executor`:
  - round-down order quantity instead of round-to-nearest
  - treat sub-minimum dust positions as non-tradeable dust
  - ignore dust positions during startup reconcile so they do not trap Creature in SAFE_MODE

## Files touched
- `config/constitution.yaml`
- `crypto_scanner/__init__.py`
- `crypto_backtester/__init__.py`
- `dashboard_state.py`
- `crypto_executor/__init__.py`
- `updates/current_revision.json`
- `updates/2026-04-09-r3-paper-activity-tune-and-utc-label.md`
