"""
setup_hunter
============
Evaluates whether a valid ORB (Opening Range Breakout) setup exists.
Runs all three gates from the constitution: regime_match, setup_validity, execution_viability.
Returns a signal dict or None if no valid setup found.
"""

import market_watcher as mw
from core.constitution import get


def scan(orb: dict, current_equity: float) -> dict | None:
    """
    Scan for a valid ORB breakout signal.

    Args:
        orb:            dict from market_watcher.get_opening_range()
        current_equity: current account balance (for execution viability check)

    Returns:
        Signal dict if a valid setup found, None otherwise.
        Signal dict: { direction, entry_price, stop_price, reason }
    """
    if not orb.get("ready"):
        print("[SetupHunter] Opening range not ready yet. Waiting.")
        return None

    bar = mw.get_latest_bar()
    avg_volume = mw.get_volume_average(lookback_bars=get("strategy.volume_lookback_candles", 20))
    ma_20 = mw.get_twenty_day_ma()
    spy_close = bar["close"]

    # ── Gate 1: Direction filter (regime_match) ────────────────────────────────
    above_ma = spy_close > ma_20 if ma_20 > 0 else None
    # above_ma = None means we can't determine MA yet (placeholder)

    # ── Gate 2: Setup validity ─────────────────────────────────────────────────
    broke_high = bar["close"] > orb["high"]
    broke_low  = bar["close"] < orb["low"]

    # ── Gate 2b: Volume confirmation ───────────────────────────────────────────
    volume_ok = bar["volume"] > avg_volume if avg_volume > 0 else False

    # ── Gate 3: Execution viability ────────────────────────────────────────────
    # PLACEHOLDER: In a real implementation, check bid/ask spread here.
    # For now, assume execution is viable if we have a valid signal.
    spread_ok = True  # PLACEHOLDER — replace with real spread check

    # ── Evaluate long signal ───────────────────────────────────────────────────
    if broke_high and volume_ok and spread_ok:
        if above_ma is not False:   # Either above MA or MA unknown (placeholder)
            print(f"[SetupHunter] LONG signal: close {spy_close:.2f} > ORB high {orb['high']:.2f} on volume {bar['volume']}")
            return {
                "direction":   "long",
                "entry_price": bar["close"],
                "stop_price":  orb["low"],      # Stop at the other side of the range
                "setup_type":  "trend_continuation",
                "volume_confirmed":        volume_ok,
                "direction_filter_passed": above_ma is not False,
            }
        else:
            _log_skip("long", "direction_filter_failed: SPY below 20-day MA", bar)

    # ── Evaluate short signal ──────────────────────────────────────────────────
    elif broke_low and volume_ok and spread_ok:
        if above_ma is not True:    # Either below MA or MA unknown (placeholder)
            print(f"[SetupHunter] SHORT signal: close {spy_close:.2f} < ORB low {orb['low']:.2f} on volume {bar['volume']}")
            return {
                "direction":   "short",
                "entry_price": bar["close"],
                "stop_price":  orb["high"],     # Stop at the other side of the range
                "setup_type":  "trend_continuation",
                "volume_confirmed":        volume_ok,
                "direction_filter_passed": above_ma is not True,
            }
        else:
            _log_skip("short", "direction_filter_failed: SPY above 20-day MA", bar)

    elif (broke_high or broke_low) and not volume_ok:
        direction = "long" if broke_high else "short"
        _log_skip(direction, f"volume_below_average: bar={bar['volume']} avg={avg_volume:.0f}", bar)

    return None


def _log_skip(direction: str, reason: str, bar: dict):
    """Internal helper to log a skipped signal."""
    try:
        from core.journal import log_skip
        log_skip(direction, reason, bar)
    except Exception:
        print(f"[SetupHunter] Skip: {direction} — {reason}")
