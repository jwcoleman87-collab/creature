"""
crypto_executor
===============
Executes crypto trades on Alpaca paper account (long-only — spot crypto).
Persists open positions to disk so a Railway restart doesn't orphan a trade.

One position at a time (enforced by has_open_position() gate in main loop).
Exit conditions: stop hit, target hit, 8h time-based exit, breakeven trail.
"""

import os
import json
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from core.constitution import get

_trading_client = TradingClient(
    api_key=os.getenv("ALPACA_API_KEY"),
    secret_key=os.getenv("ALPACA_SECRET_KEY"),
    paper=True,
)

_POSITIONS_FILE = os.path.join(
    os.path.dirname(__file__), "..", "journal_data", "crypto_positions.json"
)

_open_positions: dict = {}   # { "BTC/USD": position_dict }


# ── Symbol normalisation ───────────────────────────────────────────────────────

def _normalise(sym: str) -> str:
    """Ensure symbol is in 'BTC/USD' format (Alpaca returns 'BTCUSD' for positions)."""
    if "/" in sym:
        return sym
    # Only transform if it ends in a known quote currency
    for quote in ("USD", "EUR", "GBP", "USDT", "USDC"):
        if sym.upper().endswith(quote):
            base = sym[: -len(quote)]
            return f"{base}/{quote}"
    return sym   # Return as-is if we can't determine the format


# ── Persistence ────────────────────────────────────────────────────────────────

def _save():
    os.makedirs(os.path.dirname(_POSITIONS_FILE), exist_ok=True)
    with open(_POSITIONS_FILE, "w") as f:
        json.dump(_open_positions, f, indent=2)


def _load():
    global _open_positions
    if os.path.exists(_POSITIONS_FILE):
        with open(_POSITIONS_FILE, "r") as f:
            _open_positions = json.load(f)


# ── Startup reconciliation ─────────────────────────────────────────────────────

def reconcile_with_alpaca():
    """
    On startup, cross-check our tracked positions with Alpaca's live state.
    Removes stale entries where Alpaca no longer holds the position.
    """
    _load()
    try:
        live         = _trading_client.get_all_positions()
        live_symbols = {_normalise(p.symbol) for p in live}

        stale = [s for s in list(_open_positions.keys()) if s not in live_symbols]
        for s in stale:
            print(f"[Executor] Reconcile: removing stale position {s}")
            del _open_positions[s]

        _save()
        print(f"[Executor] Reconciled. Tracking {len(_open_positions)} open position(s).")
    except Exception as e:
        print(f"[Executor] Reconcile warning (continuing): {e}")


# ── Public position queries ────────────────────────────────────────────────────

def has_open_position(symbol: str = None) -> bool:
    """True if ANY position open (symbol=None) or a specific symbol is open."""
    if symbol:
        return _normalise(symbol) in _open_positions
    return len(_open_positions) > 0


def get_open_position(symbol: str) -> dict | None:
    return _open_positions.get(_normalise(symbol))


def get_open_symbols() -> list:
    """Return list of symbols with open positions. Use this instead of accessing _open_positions directly."""
    return list(_open_positions.keys())


def get_open_position_summary() -> dict | None:
    """Return a dashboard-friendly summary of the first open position, or None."""
    if not _open_positions:
        return None
    sym, pos = next(iter(_open_positions.items()))
    try:
        entry_ts   = datetime.fromisoformat(pos["timestamp_entry"])
        hours_held = round((datetime.now(timezone.utc) - entry_ts).total_seconds() / 3600, 1)
    except Exception:
        hours_held = 0.0
    return {
        "symbol":       sym,
        "direction":    pos.get("direction", "long"),
        "entry_price":  pos.get("entry_price"),
        "stop_price":   pos.get("stop_price"),
        "target_price": pos.get("target_price"),
        "shares":       pos.get("shares"),
        "dollar_risk":  pos.get("dollar_risk"),
        "setup_type":   pos.get("setup_type"),
        "hours_held":   hours_held,
        "breakeven_moved": pos.get("stop_moved_to_be", False),
    }


# ── Entry ──────────────────────────────────────────────────────────────────────

def submit_entry(candidate, sizing: dict) -> dict | None:
    """Place a BUY order and begin tracking the position."""
    symbol = _normalise(candidate.symbol)

    if has_open_position():
        print(f"[Executor] Already in a position — skipping {symbol}.")
        return None

    shares = sizing["shares"]
    if shares < 0.000001:
        print(f"[Executor] Position size too small for {symbol} ({shares}).")
        return None

    try:
        order = _trading_client.submit_order(MarketOrderRequest(
            symbol=symbol,
            qty=round(shares, 6),
            side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC,
        ))
        # Validate Alpaca accepted the order
        if str(order.status) in ("rejected", "canceled", "expired"):
            print(f"[Executor] Order {order.status.upper()} for {symbol}: {order}")
            return None
        print(f"[Executor] BUY {shares:.6f} {symbol} | order={order.id} | status={order.status}")
    except Exception as e:
        print(f"[Executor] Order submission failed for {symbol}: {e}")
        return None

    position = {
        "symbol":           symbol,
        "direction":        "long",
        "entry_price":      candidate.entry_price,
        "stop_price":       candidate.stop_price,
        "target_price":     candidate.target_price,
        "breakeven_price":  candidate.breakeven_price,
        "shares":           shares,
        "dollar_risk":      sizing["dollar_risk"],
        "setup_type":       candidate.setup_type,
        "volume_confirmed": candidate.volume_confirmed,
        "timestamp_entry":  datetime.now(timezone.utc).isoformat(),
        "order_id":         str(order.id),
        "stop_moved_to_be": False,
    }

    _open_positions[symbol] = position
    _save()
    print(
        f"[Executor] Entered {symbol}: "
        f"entry={candidate.entry_price:.6f} "
        f"stop={candidate.stop_price:.6f} "
        f"target={candidate.target_price:.6f} "
        f"shares={shares:.6f}"
    )
    return position


# ── Exit monitoring ────────────────────────────────────────────────────────────

def check_exits(current_bars: dict) -> list:
    """
    Check all open positions against current bar data.
    Returns list of closed trade result dicts.
    """
    max_hold_h = get("risk.crypto.max_hold_hours", 8)
    closed     = []

    for symbol in list(_open_positions.keys()):
        bar = current_bars.get(symbol)
        if not bar:
            continue

        pos   = _open_positions[symbol]
        close = bar["close"]
        high  = bar["high"]
        low   = bar["low"]

        exit_reason = None
        exit_price  = close

        # ── Time-based exit ────────────────────────────────────────────────────
        try:
            entry_ts   = datetime.fromisoformat(pos["timestamp_entry"])
            hours_held = (datetime.now(timezone.utc) - entry_ts).total_seconds() / 3600
            if hours_held >= max_hold_h:
                exit_reason = "time_exit"
                exit_price  = close
        except Exception:
            pass

        # ── Stop loss ─────────────────────────────────────────────────────────
        if not exit_reason and low <= pos["stop_price"]:
            exit_reason = "stop_hit"
            exit_price  = pos["stop_price"]

        # ── Profit target ─────────────────────────────────────────────────────
        if not exit_reason and high >= pos["target_price"]:
            exit_reason = "target_hit"
            exit_price  = pos["target_price"]

        # ── Move stop to breakeven at +1R ─────────────────────────────────────
        if not pos["stop_moved_to_be"] and close >= pos["breakeven_price"]:
            pos["stop_price"]       = pos["entry_price"]
            pos["stop_moved_to_be"] = True
            print(f"[Executor] {symbol} stop moved to breakeven at {pos['entry_price']:.6f}")
            _save()

        if exit_reason:
            result = _close_position(symbol, exit_reason, exit_price)
            if result:
                closed.append(result)

    return closed


def _close_position(symbol: str, exit_reason: str, exit_price: float) -> dict | None:
    pos = _open_positions.get(symbol)
    if not pos:
        return None

    try:
        order = _trading_client.submit_order(MarketOrderRequest(
            symbol=symbol,
            qty=round(pos["shares"], 6),
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
        ))
        if str(order.status) in ("rejected", "canceled", "expired"):
            print(f"[Executor] Close order {order.status.upper()} for {symbol}")
            # Still clear the position to avoid being stuck
        else:
            print(f"[Executor] SELL {pos['shares']:.6f} {symbol} | order={order.id} | reason={exit_reason}")
    except Exception as e:
        print(f"[Executor] Close order failed for {symbol}: {e}")

    # Always clear position from tracking (prevent stuck state)
    pnl   = (exit_price - pos["entry_price"]) * pos["shares"]
    pnl_r = pnl / pos["dollar_risk"] if pos["dollar_risk"] > 0 else 0.0

    result = {
        **pos,
        "exit_price":     exit_price,
        "exit_reason":    exit_reason,
        "actual_pnl":     round(pnl, 8),
        "actual_pnl_r":   round(pnl_r, 4),
        "timestamp_exit": datetime.now(timezone.utc).isoformat(),
    }

    del _open_positions[symbol]
    _save()
    print(f"[Executor] Closed {symbol}: {exit_reason} | P&L=${pnl:.6f} ({pnl_r:.2f}R)")
    return result


# ── Forced close (shutdown / health lockout) ───────────────────────────────────

def force_close_all(reason: str = "shutdown") -> list:
    """Force-close all open positions. Called on SIGTERM or organism death."""
    closed = []
    for symbol in list(_open_positions.keys()):
        pos    = _open_positions[symbol]
        result = _close_position(symbol, reason, pos["entry_price"])
        if result:
            closed.append(result)
    return closed
