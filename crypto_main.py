"""
CREATURE — 24/7 Crypto Main Loop
==================================
Runs forever. Scans every 5 minutes. Checks exits every 30 seconds.
Stops only when you press the STOP button (Ctrl+C or Railway stop).

Decision cycle every scan:
  1. SENSE   — fetch Fear & Greed Index (contrarian sentiment)
  2. SCAN    — score all crypto pairs (MMR hybrid strategy)
  3. TEST    — backtest best signal vs last 7 days of price history
  4. LEARN   — update asset scores from closed trades
  5. ACT     — enter if every gate passes

Usage:
    python crypto_main.py
"""

import sys
import time
import signal
from datetime import datetime, timezone, date

# ── Load constitution first — if this fails, nothing runs ─────────────────────
from core.constitution import load as load_constitution, get
try:
    load_constitution()
except (FileNotFoundError, ValueError, RuntimeError) as e:
    print(f"[Creature] FATAL: {e}")
    sys.exit(1)

# ── All imports at top — no imports inside functions ───────────────────────────
from core.journal  import (
    init_db, log_trade, log_skip,
    update_asset_score, update_hourly_performance,
)
from core.risk     import RiskEngine
from core.health   import DEAD
from metabolism    import Metabolism
import crypto_scanner    as scanner
import crypto_backtester as backtester
import crypto_executor   as executor
import dashboard_state   as dash
import web_server

# ── Constants from constitution (using dot-notation getter, not dict chaining) ─
SCAN_INTERVAL = get("risk.crypto.scan_interval_seconds", 300)
EXIT_INTERVAL = get("risk.crypto.exit_check_seconds",    30)
UNIVERSE      = get("crypto.universe", [])


# ── Graceful shutdown ──────────────────────────────────────────────────────────
def _shutdown(sig, frame):
    print("\n[Creature] Shutdown signal received. Closing all positions...")
    dash.think("Shutdown signal received — closing all positions.", "warn")
    dash.update("status", "STOPPING")
    closed = executor.force_close_all("shutdown")
    for t in closed:
        print(f"[Creature] Closed {t['symbol']} | P&L=${t['actual_pnl']:.6f}")
        dash.think(f"Force-closed {t['symbol']} on shutdown | P&L ${t['actual_pnl']:.4f}", "trade")
    print("[Creature] All positions closed. Goodbye.")
    sys.exit(0)

signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT,  _shutdown)


# ── Trade close handler ────────────────────────────────────────────────────────
def _handle_close(trade: dict, organism: Metabolism):
    """Log a closed trade, update organism state, and update learning brain."""
    pnl = trade.get("actual_pnl", 0.0)
    organism.record_trade_outcome(pnl)
    dash.sync_from_organism(organism)

    update_asset_score(trade["symbol"], pnl > 0, trade.get("actual_pnl_r", 0.0))
    update_hourly_performance(
        trade["symbol"],
        datetime.now(timezone.utc).hour,
        pnl > 0,
    )

    log_trade({
        "trade_date":              str(date.today()),
        "timestamp_entry":         trade.get("timestamp_entry"),
        "timestamp_exit":          trade.get("timestamp_exit"),
        "direction":               "long",
        "entry_price":             trade.get("entry_price"),
        "stop_price":              trade.get("stop_price"),
        "target_price":            trade.get("target_price"),
        "breakeven_price":         trade.get("breakeven_price"),
        "exit_price":              trade.get("exit_price"),
        "shares":                  trade.get("shares"),
        "dollar_risk":             trade.get("dollar_risk"),
        "actual_pnl":              trade.get("actual_pnl"),
        "actual_pnl_r":            trade.get("actual_pnl_r"),
        "health_state":            organism.health.state,
        "learning_phase":          organism.state["learning_phase"],
        "setup_type":              trade.get("setup_type"),
        "volume_confirmed":        trade.get("volume_confirmed", False),
        "direction_filter_passed": True,
        "exit_reason":             trade.get("exit_reason"),
        "slippage_breach":         False,
        "symbol":                  trade.get("symbol"),
    })


# ── Main loop ──────────────────────────────────────────────────────────────────
def main():
    print("\n" + "=" * 60)
    print("  CREATURE — 24/7 CRYPTO MODE")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Universe: {len(UNIVERSE)} pairs")
    print(f"  Scan every {SCAN_INTERVAL}s | Exit check every {EXIT_INTERVAL}s")
    print("=" * 60 + "\n")

    init_db()
    organism = Metabolism()
    organism.start_of_day()
    risk     = RiskEngine()
    executor.reconcile_with_alpaca()

    # ── Start web dashboard ────────────────────────────────────────────────────
    web_server.start()
    dash.update("status", "ONLINE")
    dash.sync_from_organism(organism)
    dash.think("Creature awakens. Initialising systems...", "info")
    dash.think(f"Universe: {len(UNIVERSE)} pairs | Scan: {SCAN_INTERVAL}s", "info")
    dash.think(f"Balance: ${organism.state['current_balance']:.2f} | Phase: {organism.state['learning_phase']}", "info")

    print(f"[Main] {organism.summary()}\n")
    print("[Main] Running 24/7. Press Ctrl+C or click STOP to halt.\n")

    last_scan_ts = 0.0

    while True:
        now = time.time()

        # Daily counter reset — checks if calendar date changed, resets if so
        organism.start_of_day()

        # ── Sync dashboard open position ───────────────────────────────────────
        open_syms = executor.get_open_symbols()
        if open_syms:
            # push current open position summary (executor stores full position dict)
            dash.update("open_position", executor.get_open_position_summary())
        else:
            dash.update("open_position", None)

        # ── Exit monitoring (every EXIT_INTERVAL seconds) ──────────────────────
        if executor.has_open_position():
            current_bars = scanner.get_latest_bars(open_syms)

            if not current_bars:
                print("[Main] WARNING: No bar data for exit check — skipping this cycle.")
            else:
                closed = executor.check_exits(current_bars)
                for trade in closed:
                    _handle_close(trade, organism)
                    dash.sync_from_organism(organism)
                    pnl = trade.get("actual_pnl", 0.0)
                    emoji = "WIN" if pnl >= 0 else "LOSS"
                    dash.think(
                        f"{emoji} | {trade['symbol']} closed | "
                        f"P&L ${pnl:.4f} | reason: {trade.get('exit_reason', '?')}",
                        "trade",
                    )
                    # Update recent trades list in dashboard
                    recent = dash.get_state().get("recent_trades", [])
                    recent.insert(0, {
                        "symbol":    trade["symbol"],
                        "direction": "long",
                        "pnl":       round(pnl, 4),
                        "pnl_r":     round(trade.get("actual_pnl_r", 0.0), 2),
                        "exit":      trade.get("exit_reason", "?"),
                        "time":      datetime.now(timezone.utc).strftime("%H:%M UTC"),
                    })
                    dash.update("recent_trades", recent[:20])

        # ── Hard stop: organism is dead ────────────────────────────────────────
        if organism.health.state == DEAD:
            dash.update("status", "DEAD")
            dash.think("Creature health state: DEAD. Pausing 1 hour.", "error")
            print("[Main] Creature is DEAD. Pausing 1 hour before checking again.")
            time.sleep(3600)
            continue

        # ── Scan cycle (every SCAN_INTERVAL seconds) ──────────────────────────
        if now - last_scan_ts >= SCAN_INTERVAL:
            last_scan_ts = now
            ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
            print(f"\n[Main] ── Scan cycle @ {ts} ─────────────────────────────")

            # Fetch bars once — reused by scanner AND backtester (no double fetch)
            bars_data  = scanner.get_all_bars(UNIVERSE, limit=200)
            candidates = scanner.scan(bars_data=bars_data)

            # ── Push live prices into dashboard (for ticker) ───────────────────
            prices = {}
            for sym, bars in bars_data.items():
                if bars and len(bars) >= 2:
                    latest = float(bars[-1].close)
                    prev   = float(bars[-2].close)
                    chg    = ((latest - prev) / prev * 100) if prev > 0 else 0.0
                    prices[sym] = {"price": round(latest, 4), "change_pct": round(chg, 2)}
            if prices:
                dash.update("prices", prices)

            # ── Push sentiment into dashboard ──────────────────────────────────
            sentiment_info = scanner.get_sentiment_info()
            dash.update("sentiment", sentiment_info)
            if sentiment_info.get("fear_greed") is not None:
                dash.think(
                    f"Fear & Greed: {sentiment_info['fear_greed']} "
                    f"({sentiment_info['label']}) adj={sentiment_info['adj']:+.2f}",
                    "info",
                )

            # ── Update asset intelligence table in dashboard ───────────────────
            dash.update("asset_scores", [
                {
                    "symbol":     c.symbol,
                    "score":      round(c.final_score, 2),
                    "setup":      c.setup_type,
                    "regime":     c.regime,
                    "sentiment":  round(getattr(c, "sentiment_adj", 0.0), 2),
                }
                for c in candidates[:10]
            ])
            dash.update("last_scan", {
                "timestamp":  ts,
                "candidates": [c.symbol for c in candidates[:5]],
                "action":     "scanning",
            })
            dash.think(f"Scan complete. {len(candidates)} signals found.", "info")

            if not candidates:
                dash.think("No signals above threshold this cycle.", "info")
                print("[Main] No signals above threshold this cycle.")
            else:
                best = candidates[0]
                print(
                    f"[Main] Best signal: {best.symbol} | "
                    f"score={best.final_score:.2f} | {best.setup_type} | "
                    f"regime={best.regime}"
                )
                dash.think(
                    f"Best signal: {best.symbol} score={best.final_score:.2f} "
                    f"[{best.setup_type} / {best.regime}]",
                    "signal",
                )

                # ── Validate bars exist for this symbol before backtesting ──────
                if best.symbol not in bars_data or not bars_data[best.symbol]:
                    print(f"[Main] No bar data for {best.symbol} — skipping.")
                    dash.think(f"No bar data for {best.symbol} — skipping.", "warn")
                    log_skip(best.direction, "no_bar_data", {"symbol": best.symbol})

                else:
                    # ── Backtest gate ──────────────────────────────────────────
                    bt = backtester.run(
                        best.symbol,
                        best.setup_type,
                        bars_data[best.symbol],
                    )
                    print(f"[Main] Backtest: {bt.reason}")
                    dash.think(f"Backtest {best.symbol}: {bt.reason}", "info")

                    if not bt.passed:
                        dash.think(f"Backtest FAILED for {best.symbol} — skipping entry.", "warn")
                        log_skip(
                            best.direction,
                            f"backtest_failed: {bt.reason}",
                            {"symbol": best.symbol, "score": best.final_score},
                        )

                    else:
                        # ── Health / fitness gate ──────────────────────────────
                        fit, reason = organism.is_fit_to_trade(best.direction)

                        if not fit:
                            log_skip(best.direction, reason, {"symbol": best.symbol})
                            dash.think(f"Health gate blocked entry: {reason}", "warn")
                            print(f"[Main] Not fit to trade: {reason}")

                        elif executor.has_open_position():
                            dash.think("Already in a position — waiting for exit.", "info")
                            print("[Main] Already in a position — max 1 concurrent.")

                        else:
                            # ── Size the trade ─────────────────────────────────
                            sizing = risk.calculate(
                                current_balance=organism.state["current_balance"],
                                entry_price=best.entry_price,
                                stop_price=best.stop_price,
                                health_multiplier=organism.health.risk_multiplier(),
                                risk_pct=organism.get_risk_pct(),
                            )

                            if not sizing["valid"]:
                                dash.think(f"Risk sizing invalid: {sizing.get('reason')}", "warn")
                                log_skip(
                                    best.direction,
                                    f"risk_invalid: {sizing.get('reason')}",
                                    {"symbol": best.symbol},
                                )
                            else:
                                # ── Enter ──────────────────────────────────────
                                dash.think(
                                    f"ENTERING {best.symbol} | entry={best.entry_price:.4f} "
                                    f"stop={best.stop_price:.4f} | "
                                    f"risk=${sizing.get('dollar_risk', 0):.2f}",
                                    "trade",
                                )
                                position = executor.submit_entry(best, sizing)
                                if not position:
                                    dash.think(f"Entry order FAILED for {best.symbol}.", "error")
                                    print(f"[Main] Entry failed for {best.symbol}.")
                                else:
                                    dash.think(f"Position OPEN: {best.symbol}", "trade")
                                    dash.update("open_position", executor.get_open_position_summary())
                                # NOTE: No use_slot() here — we trade as often as
                                # health allows. Daily loss limit in metabolism
                                # will block via slot flags if stop is hit.

        time.sleep(EXIT_INTERVAL)


if __name__ == "__main__":
    main()
