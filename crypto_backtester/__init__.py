"""
crypto_backtester
=================
Walk-forward quick backtest. Replays the last 7 days of 1h bars using the
SAME signal logic as the scanner — no optimisation, no overfitting.

Purpose: binary gate. Did this signal type work on this coin recently?
  - Yes (passed)  → proceed to trade
  - No  (failed)  → skip this cycle, log reason
  - Newborn phase → always accept (insufficient history to veto)
"""

from dataclasses import dataclass
from core.constitution import get
from crypto_scanner import (
    _compute_rsi, _compute_bb_position,
    _compute_z_score_4h, _compute_atr,
)


@dataclass
class BacktestResult:
    symbol:       str
    setup_type:   str
    trade_count:  int
    win_rate:     float
    expectancy_r: float
    passed:       bool
    reason:       str


def run(symbol: str, setup_type: str, bars: list) -> BacktestResult:
    """
    Walk forward through bars. On each bar where the signal fires,
    simulate entry + exits (stop/target/time). Count win rate + expectancy.
    """
    min_wr     = get("crypto.backtest.min_win_rate",       0.45)
    min_exp    = get("crypto.backtest.min_expectancy_r",   0.30)
    min_sample = get("crypto.backtest.min_sample_size",    5)
    max_hold   = get("risk.crypto.max_hold_hours",         8)
    z_thresh   = get("crypto.strategy.sub_strategies.momentum.z_score_entry_threshold", 1.5)
    rsi_os     = get("crypto.strategy.sub_strategies.mean_reversion.rsi_oversold",      35)
    bb_entry_max = get("crypto.strategy.sub_strategies.mean_reversion.bb_entry_max",     20)
    atr_mult   = get("risk.crypto.stop_loss_atr_multiplier", 1.5)
    floor_pct  = get("risk.crypto.stop_loss_pct",            0.01)

    if not bars or len(bars) < 80:
        return BacktestResult(
            symbol=symbol, setup_type=setup_type,
            trade_count=0, win_rate=0.0, expectancy_r=0.0,
            passed=True, reason="insufficient_history"
        )

    outcomes = []
    i = 60  # need at least 60 bars of lookback before scanning

    while i < len(bars) - max_hold:
        window = bars[:i + 1]
        closes = [b.close for b in window]

        if setup_type == "momentum_long":
            signal = _compute_z_score_4h(window) >= z_thresh
        elif setup_type == "mean_reversion_long":
            signal = (_compute_bb_position(closes) <= bb_entry_max and _compute_rsi(closes) <= rsi_os)
        else:
            signal = False

        if signal:
            entry = bars[i].close
            atr   = _compute_atr(window)
            if atr <= 0 or entry <= 0:
                i += 1
                continue

            stop_dist    = max(atr * atr_mult, entry * floor_pct)
            stop_price   = entry - stop_dist
            target_price = entry + stop_dist * 2.0

            outcome = 0.0  # default: time exit (neutral)
            for j in range(i + 1, min(i + max_hold + 1, len(bars))):
                if bars[j].low <= stop_price:
                    outcome = -1.0   # stopped out = -1R
                    break
                if bars[j].high >= target_price:
                    outcome = 2.0    # target hit = +2R
                    break

            outcomes.append(outcome)
            i += max_hold  # skip ahead to avoid overlapping trades
        else:
            i += 1

    if not outcomes:
        return BacktestResult(
            symbol=symbol, setup_type=setup_type,
            trade_count=0, win_rate=0.0, expectancy_r=0.0,
            passed=True, reason="no_historical_signals"
        )

    wins       = sum(1 for o in outcomes if o > 0)
    win_rate   = wins / len(outcomes)
    expectancy = sum(outcomes) / len(outcomes)

    # Newborn phase bypass — not enough live trades to trust veto
    try:
        from core.journal import get_daily_stats
        stats      = get_daily_stats(200) or {}
        total_live = stats.get("total_trades", 0)
        is_newborn = total_live < 20
    except Exception:
        is_newborn = True

    if len(outcomes) < min_sample:
        passed = True
        reason = f"small_sample ({len(outcomes)} trades) — accepting"
    elif is_newborn:
        passed = True
        reason = f"newborn_bypass — WR={win_rate:.0%} exp={expectancy:.2f}R (n={len(outcomes)})"
    else:
        passed = (win_rate >= min_wr) and (expectancy >= min_exp)
        reason = (
            f"passed — WR={win_rate:.0%} exp={expectancy:.2f}R (n={len(outcomes)})"
            if passed else
            f"failed — WR={win_rate:.0%} exp={expectancy:.2f}R (n={len(outcomes)})"
        )

    return BacktestResult(
        symbol=symbol, setup_type=setup_type,
        trade_count=len(outcomes),
        win_rate=round(win_rate, 4),
        expectancy_r=round(expectancy, 4),
        passed=passed, reason=reason,
    )
