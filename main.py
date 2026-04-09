"""
CREATURE — Main Entry Point v1.0
==================================
This is where the organism wakes up each morning and hunts.

Run this file once per trading day after 09:30 ET.
It will watch for an ORB breakout, manage the trade, and shut down by 15:45.

Usage:
    python main.py

PLACEHOLDER note: All Alpaca API calls inside market_watcher and paper_executor
are currently stubbed. The organism will print its decisions but won't place
real orders until the API key is active and placeholders are replaced.
"""

import sys
import time
from datetime import datetime, time as dtime, date

# ── Load constitution first — if this fails, nothing runs ─────────────────────
from core.constitution import load as load_constitution
try:
    constitution = load_constitution()
except (FileNotFoundError, ValueError, RuntimeError) as e:
    print(f"[Creature] FATAL: {e}")
    sys.exit(1)

# ── Core modules ───────────────────────────────────────────────────────────────
from core.journal    import init_db, log_trade, log_skip
from core.risk       import RiskEngine
from metabolism      import Metabolism
import market_watcher as mw
import setup_hunter   as sh
import paper_executor as pe

# ── Constants from constitution ────────────────────────────────────────────────
EOD_CLOSE_BY   = dtime(15, 45)
ORB_READY_TIME = dtime(10,  0)
POLL_SECONDS   = 60   # Check for signals every 60 seconds


def is_market_hours() -> bool:
    now = datetime.now().time()
    return dtime(9, 30) <= now <= dtime(16, 0)


def past_eod() -> bool:
    return datetime.now().time() >= EOD_CLOSE_BY


def orb_window_closed() -> bool:
    return datetime.now().time() >= ORB_READY_TIME


def main():
    print("\n" + "="*60)
    print("  CREATURE AWAKENS")
    print(f"  {date.today()} | Mode: {constitution['creature']['mode']}")
    print("="*60 + "\n")

    # ── Initialise ─────────────────────────────────────────────────────────────
    init_db()
    risk     = RiskEngine()
    organism = Metabolism()
    organism.start_of_day()

    print(f"[Main] Status: {organism.summary()}\n")

    # ── Wait for ORB window to close ───────────────────────────────────────────
    if not orb_window_closed():
        print(f"[Main] Waiting for Opening Range to form (closes at 10:00 ET)...")
        while not orb_window_closed():
            time.sleep(POLL_SECONDS)

    # ── Fetch opening range ────────────────────────────────────────────────────
    orb = mw.get_opening_range(trade_date=date.today())
    if not orb["ready"]:
        print("[Main] Opening range not available (PLACEHOLDER mode). Creature will observe only today.")
        # In placeholder mode we continue but won't place trades
    else:
        print(f"[Main] Opening Range: HIGH={orb['high']:.2f} | LOW={orb['low']:.2f}")

    # ── Main trading loop ──────────────────────────────────────────────────────
    print("[Main] Entering trading loop. Scanning every 60 seconds...\n")

    while not past_eod():

        if not is_market_hours():
            print("[Main] Outside market hours. Waiting...")
            time.sleep(POLL_SECONDS)
            continue

        # ── EOD force-close check ──────────────────────────────────────────────
        if pe.has_open_position():
            if past_eod():
                print("[Main] EOD reached. Force-closing position.")
                result = pe.force_close(reason="eod_close")
                if result:
                    _handle_close(result, organism)
                break

        # ── Check exits on open position ───────────────────────────────────────
        if pe.has_open_position():
            bar = mw.get_latest_bar()
            result = pe.check_exit(bar, organism.health.state)
            if result:
                _handle_close(result, organism)

        # ── Scan for new signals (only if no open position) ────────────────────
        elif orb.get("ready"):
            # Check fitness before even scanning
            for direction in ("long", "short"):
                fit, reason = organism.is_fit_to_trade(direction)
                if not fit:
                    if reason not in ("long_slot_already_used_today", "short_slot_already_used_today"):
                        print(f"[Main] Not fit to trade {direction}: {reason}")

            signal = sh.scan(orb, organism.state["current_balance"])

            if signal:
                direction = signal["direction"]
                fit, reason = organism.is_fit_to_trade(direction)

                if fit:
                    # Size the trade
                    sizing = risk.calculate(
                        current_balance=organism.state["current_balance"],
                        entry_price=signal["entry_price"],
                        stop_price=signal["stop_price"],
                        health_multiplier=organism.health.risk_multiplier(),
                        risk_pct=organism.get_risk_pct(),
                    )

                    if sizing["valid"]:
                        position = pe.submit_entry(signal, sizing)
                        if position:
                            organism.use_slot(direction)
                    else:
                        log_skip(direction, f"risk_invalid: {sizing.get('reason')}", signal)
                else:
                    log_skip(direction, reason, signal)

        time.sleep(POLL_SECONDS)

    # ── End of day ─────────────────────────────────────────────────────────────
    print("\n[Main] Market closed. End of day.\n")

    # Force-close anything still open (safety net)
    if pe.has_open_position():
        result = pe.force_close(reason="eod_close")
        if result:
            _handle_close(result, organism)

    organism.record_losing_day()

    print(f"\n[Main] Final status:")
    summary = organism.summary()
    print(f"  Balance:   ${summary['current_balance']:.2f}")
    print(f"  Health:    {summary['health']['state']}")
    print(f"  Phase:     {summary['learning_phase']}")
    print(f"  Trades:    {summary['today_trades']} today | {summary['total_trades']} total")
    print("\n  Creature rests. See you tomorrow.\n")


def _handle_close(result: dict, organism: Metabolism):
    """Log a closed trade and update organism state."""
    pnl = result.get("actual_pnl", 0)
    organism.record_trade_outcome(pnl)

    log_trade({
        "trade_date":              str(date.today()),
        "timestamp_entry":         result.get("timestamp_entry"),
        "timestamp_exit":          result.get("timestamp_exit"),
        "direction":               result.get("direction"),
        "entry_price":             result.get("entry_price"),
        "stop_price":              result.get("stop_price"),
        "target_price":            result.get("target_price"),
        "breakeven_price":         result.get("breakeven_price"),
        "exit_price":              result.get("exit_price"),
        "shares":                  result.get("shares"),
        "dollar_risk":             result.get("dollar_risk"),
        "actual_pnl":              result.get("actual_pnl"),
        "actual_pnl_r":            result.get("actual_pnl_r"),
        "health_state":            result.get("health_state", organism.health.state),
        "learning_phase":          organism.state["learning_phase"],
        "setup_type":              result.get("setup_type"),
        "volume_confirmed":        result.get("volume_confirmed", False),
        "direction_filter_passed": result.get("direction_filter_passed", False),
        "exit_reason":             result.get("exit_reason"),
        "slippage_breach":         False,   # PLACEHOLDER: check real slippage once API live
    })


if __name__ == "__main__":
    main()
