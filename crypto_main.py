"""
CREATURE - 24/7 Crypto Main Loop
================================
Runs continuously. Scans on interval. Checks exits on interval.
Stops only on explicit shutdown signal.
"""

import sys
import time
import os
import json
import socket
import atexit
import signal
import traceback
from datetime import datetime, timezone, date

from core.constitution import load as load_constitution, get

try:
    load_constitution()
except (FileNotFoundError, ValueError, RuntimeError) as e:
    print(f"[Creature] FATAL: {e}")
    sys.exit(1)

from core.journal import (
    init_db, log_trade, log_skip,
    update_asset_score, update_hourly_performance,
    log_cycle_event, log_system_event,
)
from core.risk import RiskEngine
from core.health import DEAD
from metabolism import Metabolism
import crypto_scanner as scanner
import crypto_backtester as backtester
import crypto_executor as executor
import dashboard_state as dash
import web_server


SCAN_INTERVAL = get("risk.crypto.scan_interval_seconds", 300)
EXIT_INTERVAL = get("risk.crypto.exit_check_seconds", 30)
UNIVERSE = get("crypto.universe", [])
STARTING_BALANCE = float(get("risk.starting_balance", 500.0))
RUNTIME_LOCK_FILE = os.path.join(os.path.dirname(__file__), "journal_data", "creature.runtime.lock")
STOP_FLAG_FILE = os.path.join(os.path.dirname(__file__), "journal_data", "creature.stop")

_ACTIVE_ORGANISM = None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _acquire_runtime_lock() -> bool:
    os.makedirs(os.path.dirname(RUNTIME_LOCK_FILE), exist_ok=True)

    if os.path.exists(RUNTIME_LOCK_FILE):
        existing_pid = 0
        try:
            with open(RUNTIME_LOCK_FILE, "r", encoding="utf-8") as f:
                payload = json.load(f)
            existing_pid = int(payload.get("pid", 0))
        except Exception:
            existing_pid = 0

        if existing_pid and _pid_alive(existing_pid):
            print(f"[Creature] FATAL: Another instance is already running (pid={existing_pid}).")
            return False

        try:
            os.remove(RUNTIME_LOCK_FILE)
        except OSError as e:
            print(f"[Creature] FATAL: Could not clear stale runtime lock: {e}")
            return False

    payload = {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "started_utc": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with open(RUNTIME_LOCK_FILE, "x", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except FileExistsError:
        print("[Creature] FATAL: Runtime lock already exists. Aborting duplicate launch.")
        return False
    except OSError as e:
        print(f"[Creature] FATAL: Could not create runtime lock: {e}")
        return False

    return True


def _release_runtime_lock():
    try:
        if not os.path.exists(RUNTIME_LOCK_FILE):
            return
        owner_pid = None
        with open(RUNTIME_LOCK_FILE, "r", encoding="utf-8") as f:
            owner_pid = int(json.load(f).get("pid", 0))
        if owner_pid and owner_pid != os.getpid():
            return
        os.remove(RUNTIME_LOCK_FILE)
    except Exception:
        pass


def _stop_requested() -> bool:
    return os.path.exists(STOP_FLAG_FILE)


def _clear_stop_request():
    try:
        if os.path.exists(STOP_FLAG_FILE):
            os.remove(STOP_FLAG_FILE)
    except OSError:
        pass


def _record_cycle(
    organism: Metabolism,
    phase: str,
    status: str,
    actions: list[str],
    reasons: list[str],
    candidates_count: int = 0,
    best_symbol: str | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    details: dict | None = None,
):
    log_cycle_event({
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        "status": status,
        "action": ",".join(actions) if actions else "idle",
        "reason": " | ".join(reasons) if reasons else None,
        "health_state": organism.health.state,
        "learning_phase": organism.state.get("learning_phase"),
        "current_balance": organism.state.get("current_balance"),
        "open_positions": len(executor.get_open_symbols()),
        "candidates_count": candidates_count,
        "best_symbol": best_symbol,
        "error_type": error_type,
        "error_message": error_message,
        "details": details or {},
    })


def _resolve_startup_reconciliation(organism: Metabolism):
    """
    Resolve startup state mismatches before entering runtime loop.
    If Alpaca has unknown live positions, enter safe mode and flatten them.
    """
    while True:
        report = executor.reconcile_with_alpaca()
        if not report.get("ok"):
            msg = f"Startup reconcile failed: {report.get('error', 'unknown_error')}"
            dash.update("status", "SAFE_MODE")
            dash.mark_heartbeat("startup_reconcile", "error")
            dash.think(msg, "error")
            print(f"[Main] {msg}")
            log_system_event("critical", "RECONCILE_ERROR", msg, report)
            _record_cycle(
                organism=organism,
                phase="startup",
                status="error",
                actions=["startup_reconcile_error"],
                reasons=[msg],
                error_type="reconcile_error",
                error_message=report.get("error"),
                details=report,
            )
            time.sleep(EXIT_INTERVAL)
            continue

        orphans = report.get("orphan_live_symbols", [])
        if not orphans:
            log_system_event("info", "RECONCILE_OK", "Startup reconcile clean.", report)
            return

        msg = f"Startup mismatch: untracked live positions on Alpaca: {', '.join(orphans)}"
        dash.update("status", "SAFE_MODE")
        dash.mark_heartbeat("startup_reconcile", "safe_mode")
        dash.think(msg, "error")
        print(f"[Main] {msg}")
        log_system_event("critical", "RECONCILE_ORPHAN_POSITIONS", msg, report)

        close_results = executor.force_close_live_symbols(orphans, reason="reconcile_orphan")
        log_system_event(
            "warn",
            "RECONCILE_CLOSE_ATTEMPT",
            "Attempted to close orphan live positions.",
            {"orphans": orphans, "results": close_results},
        )
        _record_cycle(
            organism=organism,
            phase="startup",
            status="safe_mode",
            actions=["startup_reconcile_orphan_close"],
            reasons=["orphan_live_positions"],
            details={"orphans": orphans, "close_results": close_results},
        )
        time.sleep(EXIT_INTERVAL)


def _shutdown(sig, frame):
    global _ACTIVE_ORGANISM
    print("\n[Creature] Shutdown signal received. Closing all positions...")
    _clear_stop_request()
    dash.think("Shutdown signal received - closing all positions.", "warn")
    dash.update("status", "STOPPING")

    open_syms = executor.get_open_symbols()
    latest = scanner.get_latest_bars(open_syms) if open_syms else {}
    exit_prices = {sym: bar["close"] for sym, bar in latest.items() if bar and "close" in bar}

    closed = executor.force_close_all("shutdown", exit_prices=exit_prices)
    for trade in closed:
        print(f"[Creature] Closed {trade['symbol']} | P&L=${trade['actual_pnl']:.6f}")
        dash.think(f"Force-closed {trade['symbol']} on shutdown | P&L ${trade['actual_pnl']:.4f}", "trade")
        if _ACTIVE_ORGANISM is not None:
            _handle_close(trade, _ACTIVE_ORGANISM)

    log_system_event(
        "warn",
        "SHUTDOWN",
        "Shutdown signal received and force-close attempted.",
        {"closed_symbols": [t.get("symbol") for t in closed]},
    )
    print("[Creature] All positions closed. Goodbye.")
    sys.exit(0)


signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT, _shutdown)


def _handle_close(trade: dict, organism: Metabolism):
    """Log a closed trade, update organism state, and update learning tables."""
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
        "trade_date": str(date.today()),
        "timestamp_entry": trade.get("timestamp_entry"),
        "timestamp_exit": trade.get("timestamp_exit"),
        "direction": "long",
        "entry_price": trade.get("entry_price"),
        "stop_price": trade.get("stop_price"),
        "target_price": trade.get("target_price"),
        "breakeven_price": trade.get("breakeven_price"),
        "exit_price": trade.get("exit_price"),
        "shares": trade.get("shares"),
        "dollar_risk": trade.get("dollar_risk"),
        "actual_pnl": trade.get("actual_pnl"),
        "actual_pnl_r": trade.get("actual_pnl_r"),
        "health_state": organism.health.state,
        "learning_phase": organism.state["learning_phase"],
        "setup_type": trade.get("setup_type"),
        "volume_confirmed": trade.get("volume_confirmed", False),
        "direction_filter_passed": True,
        "exit_reason": trade.get("exit_reason"),
        "slippage_breach": False,
        "symbol": trade.get("symbol"),
    })


def main():
    global _ACTIVE_ORGANISM
    if not _acquire_runtime_lock():
        sys.exit(2)
    atexit.register(_release_runtime_lock)
    _clear_stop_request()

    print("\n" + "=" * 60)
    print("  CREATURE - 24/7 CRYPTO MODE")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Universe: {len(UNIVERSE)} pairs")
    print(f"  Scan every {SCAN_INTERVAL}s | Exit check every {EXIT_INTERVAL}s")
    print("=" * 60 + "\n")

    init_db()
    organism = Metabolism()
    _ACTIVE_ORGANISM = organism
    organism.start_of_day()
    risk = RiskEngine()

    runtime = dash.get_state().get("runtime", {})
    runtime["scan_interval_s"] = SCAN_INTERVAL
    runtime["exit_interval_s"] = EXIT_INTERVAL
    runtime["universe_size"] = len(UNIVERSE)
    dash.update("runtime", runtime)

    try:
        web_server.start()
    except Exception as e:
        err = f"Dashboard server failed to start: {type(e).__name__}: {e}"
        print(f"[Main] {err}")
        dash.think(err, "error")
        log_system_event("error", "DASHBOARD_START_FAILED", err, {"error": str(e)})

    dash.update("status", "STARTING")
    dash.sync_from_organism(organism)
    dash.think("Creature awakens. Initialising systems...", "info")
    dash.think(f"Universe: {len(UNIVERSE)} pairs | Scan: {SCAN_INTERVAL}s", "info")
    dash.think(f"Balance: ${organism.state['current_balance']:.2f} | Phase: {organism.state['learning_phase']}", "info")
    log_system_event(
        "info",
        "STARTUP",
        "Creature startup initialised.",
        {"scan_interval": SCAN_INTERVAL, "exit_interval": EXIT_INTERVAL, "universe_size": len(UNIVERSE)},
    )

    _resolve_startup_reconciliation(organism)
    dash.update("status", "ONLINE")

    print(f"[Main] {organism.summary()}\n")
    print("[Main] Running 24/7. Press Ctrl+C or click STOP to halt.\n")

    last_scan_ts = 0.0

    while True:
        sleep_seconds = EXIT_INTERVAL
        phase = "heartbeat"
        status = "ok"
        actions = []
        reasons = []
        candidates_count = 0
        best_symbol = None
        error_type = None
        error_message = None
        details = {}

        try:
            now = time.time()
            organism.start_of_day()
            if _stop_requested():
                dash.think("Stop file detected. Performing graceful shutdown.", "warn")
                _shutdown(None, None)

            open_syms = executor.get_open_symbols()
            details["open_symbols"] = open_syms
            if open_syms:
                dash.update("open_position", executor.get_open_position_summary())
            else:
                dash.update("open_position", None)

            if executor.has_open_position():
                phase = "exit_monitor"
                current_bars = scanner.get_latest_bars(open_syms)

                if not current_bars:
                    print("[Main] WARNING: No bar data for exit check - skipping this cycle.")
                    actions.append("exit_check_skipped")
                    reasons.append("no_bar_data_for_exit_check")
                else:
                    closed = executor.check_exits(current_bars)
                    details["closed_count"] = len(closed)
                    if closed:
                        actions.append("exit_close")
                    else:
                        actions.append("exit_monitor_idle")

                    for trade in closed:
                        _handle_close(trade, organism)
                        dash.sync_from_organism(organism)
                        pnl = trade.get("actual_pnl", 0.0)
                        label = "WIN" if pnl >= 0 else "LOSS"
                        dash.think(
                            f"{label} | {trade['symbol']} closed | "
                            f"P&L ${pnl:.4f} | reason: {trade.get('exit_reason', '?')}",
                            "trade",
                        )
                        recent = dash.get_state().get("recent_trades", [])
                        recent.insert(0, {
                            "symbol": trade["symbol"],
                            "direction": "long",
                            "pnl": round(pnl, 4),
                            "pnl_r": round(trade.get("actual_pnl_r", 0.0), 2),
                            "exit": trade.get("exit_reason", "?"),
                            "time": datetime.now(timezone.utc).strftime("%H:%M UTC"),
                        })
                        dash.update("recent_trades", recent[:20])

            if organism.health.state == DEAD:
                phase = "dead_state"
                dash.update("status", "DEAD")
                actions.append("dead_pause")
                reasons.append("health_dead")

                if executor.has_open_position():
                    open_syms = executor.get_open_symbols()
                    latest = scanner.get_latest_bars(open_syms) if open_syms else {}
                    exit_prices = {sym: bar["close"] for sym, bar in latest.items() if bar and "close" in bar}
                    closed = executor.force_close_all("health_dead", exit_prices=exit_prices)
                    details["dead_forced_closes"] = [t.get("symbol") for t in closed]
                    for trade in closed:
                        _handle_close(trade, organism)
                        dash.think(
                            f"Force-closed {trade['symbol']} due to DEAD health | "
                            f"P&L ${trade.get('actual_pnl', 0.0):.4f}",
                            "error",
                        )

                dash.think("Creature health state: DEAD. All trading halted for 1 hour.", "error")
                print("[Main] Creature is DEAD. All trading halted for 1 hour.")
                sleep_seconds = 3600
                continue

            if now - last_scan_ts < SCAN_INTERVAL:
                if not actions:
                    actions.append("idle")
                    reasons.append("scan_interval_not_reached")
                continue

            phase = "scan"
            last_scan_ts = now
            ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
            print(f"\n[Main] Scan cycle @ {ts}")
            actions.append("scan_start")

            acct = executor.get_account_balance()
            if acct:
                equity = acct["equity"]
                organism.state["current_balance"] = equity
                dash.update("balance", {
                    "current": equity,
                    "peak": max(organism.state["peak_equity"], equity),
                    "starting": STARTING_BALANCE,
                    "pnl": round(equity - STARTING_BALANCE, 2),
                    "pnl_pct": round((equity - STARTING_BALANCE) / STARTING_BALANCE * 100, 2),
                    "cash": acct["cash"],
                    "buying_power": acct["buying_power"],
                })
                details["equity"] = equity

            bars_data = scanner.get_all_bars(UNIVERSE, limit=200)
            candidates = scanner.scan(bars_data=bars_data)
            candidates_count = len(candidates)
            details["candidates_count"] = candidates_count

            prices = {}
            for sym, bars in bars_data.items():
                if bars and len(bars) >= 2:
                    latest = float(bars[-1].close)
                    prev = float(bars[-2].close)
                    chg = ((latest - prev) / prev * 100) if prev > 0 else 0.0
                    prices[sym] = {"price": round(latest, 4), "change_pct": round(chg, 2)}
            if prices:
                dash.update("prices", prices)

            sentiment_info = scanner.get_sentiment_info()
            dash.update("sentiment", sentiment_info)
            if sentiment_info.get("fear_greed") is not None:
                dash.think(
                    f"Fear & Greed: {sentiment_info['fear_greed']} "
                    f"({sentiment_info['label']}) adj={sentiment_info['adj']:+.2f}",
                    "info",
                )

            dash.update("asset_scores", [
                {
                    "symbol": c.symbol,
                    "score": round(c.final_score, 2),
                    "setup": c.setup_type,
                    "regime": c.regime,
                    "sentiment": round(getattr(c, "sentiment_adj", 0.0), 2),
                }
                for c in candidates[:10]
            ])
            dash.update("last_scan", {
                "timestamp": ts,
                "candidates": [c.symbol for c in candidates[:5]],
                "action": "scanning",
            })
            dash.think(f"Scan complete. {len(candidates)} signals found.", "info")

            if not candidates:
                actions.append("scan_no_signal")
                reasons.append("no_candidates")
                dash.think("No signals above threshold this cycle.", "info")
                print("[Main] No signals above threshold this cycle.")
                continue

            best = candidates[0]
            best_symbol = best.symbol
            details["best_setup"] = best.setup_type
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

            if best.symbol not in bars_data or not bars_data[best.symbol]:
                actions.append("skip")
                reasons.append("no_bar_data")
                print(f"[Main] No bar data for {best.symbol} - skipping.")
                dash.think(f"No bar data for {best.symbol} - skipping.", "warn")
                log_skip(best.direction, "no_bar_data", {"symbol": best.symbol})
                continue

            bt = backtester.run(best.symbol, best.setup_type, bars_data[best.symbol])
            details["backtest"] = {
                "passed": bt.passed,
                "reason": bt.reason,
                "win_rate": bt.win_rate,
                "expectancy_r": bt.expectancy_r,
                "trade_count": bt.trade_count,
            }
            print(f"[Main] Backtest: {bt.reason}")
            dash.think(f"Backtest {best.symbol}: {bt.reason}", "info")

            if not bt.passed:
                actions.append("skip")
                reasons.append("backtest_failed")
                dash.think(f"Backtest FAILED for {best.symbol} - skipping entry.", "warn")
                log_skip(
                    best.direction,
                    f"backtest_failed: {bt.reason}",
                    {"symbol": best.symbol, "score": best.final_score},
                )
                continue

            fit, reason = organism.is_fit_to_trade(best.direction)
            if not fit:
                actions.append("skip")
                reasons.append(f"health_gate:{reason}")
                log_skip(best.direction, reason, {"symbol": best.symbol})
                dash.think(f"Health gate blocked entry: {reason}", "warn")
                print(f"[Main] Not fit to trade: {reason}")
                continue

            if executor.has_open_position():
                actions.append("skip")
                reasons.append("already_in_position")
                dash.think("Already in a position - waiting for exit.", "info")
                print("[Main] Already in a position - max 1 concurrent.")
                continue

            sizing = risk.calculate(
                current_balance=organism.state["current_balance"],
                entry_price=best.entry_price,
                stop_price=best.stop_price,
                health_multiplier=organism.health.risk_multiplier(),
                risk_pct=organism.get_risk_pct(),
            )
            details["sizing"] = sizing

            if not sizing["valid"]:
                actions.append("skip")
                reasons.append("risk_invalid")
                dash.think(f"Risk sizing invalid: {sizing.get('reason')}", "warn")
                log_skip(
                    best.direction,
                    f"risk_invalid: {sizing.get('reason')}",
                    {"symbol": best.symbol},
                )
                continue

            dash.think(
                f"ENTERING {best.symbol} | entry={best.entry_price:.4f} "
                f"stop={best.stop_price:.4f} | "
                f"risk=${sizing.get('dollar_risk', 0):.2f}",
                "trade",
            )
            position = executor.submit_entry(best, sizing)
            if not position:
                actions.append("entry_failed")
                reasons.append("submit_entry_failed")
                dash.think(f"Entry order FAILED for {best.symbol}.", "error")
                print(f"[Main] Entry failed for {best.symbol}.")
                continue

            actions.append("entry_submitted")
            reasons.append(best.symbol)
            dash.think(f"Position OPEN: {best.symbol}", "trade")
            dash.update("open_position", executor.get_open_position_summary())
            dash.sync_from_organism(organism)

        except Exception as e:
            status = "error"
            error_type = type(e).__name__
            error_message = str(e)
            tb = traceback.format_exc()
            actions.append("cycle_exception")
            reasons.append(error_type)
            details["traceback"] = tb
            dash.think(f"Cycle exception: {error_type} - {error_message}", "error")
            print(f"[Main] ERROR: {error_type}: {error_message}")
            log_system_event(
                "error",
                "MAIN_LOOP_EXCEPTION",
                f"{error_type}: {error_message}",
                {"traceback": tb},
            )
        finally:
            if not actions:
                actions.append("idle")
            try:
                _record_cycle(
                    organism=organism,
                    phase=phase,
                    status=status,
                    actions=actions,
                    reasons=reasons,
                    candidates_count=candidates_count,
                    best_symbol=best_symbol,
                    error_type=error_type,
                    error_message=error_message,
                    details=details,
                )
            except Exception as log_err:
                print(f"[Main] WARNING: cycle logging failed: {log_err}")
            dash.mark_heartbeat(phase=phase, status=status)
            time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()
