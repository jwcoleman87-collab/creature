"""
paper_executor
==============
Executes trades on Alpaca's paper trading account.
Applies the slippage model from the constitution and tracks open positions.
"""

import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, ClosePositionRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from core.constitution import get
from core.risk import RiskEngine

_risk = RiskEngine()
_open_position = None   # Only one position at a time

# ── Alpaca trading client (paper mode) ─────────────────────────────────────────
_trading_client = TradingClient(
    api_key=os.getenv("ALPACA_API_KEY"),
    secret_key=os.getenv("ALPACA_SECRET_KEY"),
    paper=True,
)

SYMBOL = "SPY"


def submit_entry(signal: dict, sizing: dict) -> dict:
    """
    Enter a position via Alpaca paper account.

    Args:
        signal: from setup_hunter (direction, entry_price, stop_price, etc.)
        sizing: from RiskEngine.calculate()

    Returns:
        Position dict tracking the open trade.
    """
    global _open_position

    if _open_position is not None:
        print("[Executor] Cannot open position — one already exists.")
        return None

    slippage = get("costs.slippage_per_share", 0.02)
    direction = signal["direction"]

    # Apply slippage estimate to entry (we expect a slightly worse fill)
    if direction == "long":
        estimated_fill = sizing["entry_price"] + slippage
    else:
        estimated_fill = sizing["entry_price"] - slippage

    # ── Submit real order to Alpaca ────────────────────────────────────────────
    try:
        order_request = MarketOrderRequest(
            symbol=SYMBOL,
            qty=round(sizing["shares"], 4),  # Alpaca supports fractional shares
            side=OrderSide.BUY if direction == "long" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        order = _trading_client.submit_order(order_request)
        print(f"[Executor] Order submitted: {order.id} | {direction} {sizing['shares']:.4f} shares")
        print(f"[Executor] Order status: {order.status}")
    except Exception as e:
        print(f"[Executor] ERROR submitting order: {e}")
        return None

    _open_position = {
        "direction":       direction,
        "entry_price":     estimated_fill,
        "stop_price":      sizing["stop_price"],
        "target_price":    sizing["target_price"],
        "breakeven_price": sizing["breakeven_price"],
        "shares":          sizing["shares"],
        "dollar_risk":     sizing["dollar_risk"],
        "setup_type":      signal.get("setup_type"),
        "volume_confirmed": signal.get("volume_confirmed", False),
        "direction_filter_passed": signal.get("direction_filter_passed", False),
        "timestamp_entry": datetime.now().isoformat(),
        "stop_moved_to_be": False,
        "order_id":        str(order.id),
    }

    print(f"[Executor] Entered {direction}: est_fill={estimated_fill:.2f} | stop={sizing['stop_price']:.2f} | target={sizing['target_price']:.2f} | shares={sizing['shares']:.4f}")
    return _open_position


def check_exit(current_bar: dict, health_state: str) -> dict | None:
    """
    Check if the open position should be exited.
    Called on every new 5-minute bar while a position is open.

    Returns exit dict if position should close, None to hold.
    """
    global _open_position

    if _open_position is None:
        return None

    pos = _open_position
    close = current_bar["close"]
    high  = current_bar["high"]
    low   = current_bar["low"]
    direction = pos["direction"]

    exit_reason = None
    exit_price  = close

    # ── Check stop loss ────────────────────────────────────────────────────────
    if direction == "long" and low <= pos["stop_price"]:
        exit_reason = "stop_hit"
        exit_price  = pos["stop_price"]

    elif direction == "short" and high >= pos["stop_price"]:
        exit_reason = "stop_hit"
        exit_price  = pos["stop_price"]

    # ── Check profit target ────────────────────────────────────────────────────
    elif direction == "long" and high >= pos["target_price"]:
        exit_reason = "target_hit"
        exit_price  = pos["target_price"]

    elif direction == "short" and low <= pos["target_price"]:
        exit_reason = "target_hit"
        exit_price  = pos["target_price"]

    # ── Move stop to breakeven when 1R reached ─────────────────────────────────
    if not pos["stop_moved_to_be"]:
        be_triggered = (
            (direction == "long"  and close >= pos["breakeven_price"]) or
            (direction == "short" and close <= pos["breakeven_price"])
        )
        if be_triggered:
            pos["stop_price"]       = pos["entry_price"]
            pos["stop_moved_to_be"] = True
            print(f"[Executor] Stop moved to breakeven at {pos['entry_price']:.2f}")

    if exit_reason:
        return _close_position(exit_reason, exit_price)

    return None


def force_close(reason: str = "eod_close") -> dict | None:
    """
    Force-close any open position immediately.
    Used for EOD close, daily stop hit, or halt events.
    """
    global _open_position
    if _open_position is None:
        return None
    bar = {"close": _open_position["entry_price"]}  # fallback price
    return _close_position(reason, bar["close"])


def has_open_position() -> bool:
    return _open_position is not None


def get_open_position() -> dict | None:
    return _open_position


def _close_position(exit_reason: str, exit_price: float) -> dict:
    global _open_position
    pos = _open_position

    slippage = get("costs.slippage_per_share", 0.02)
    # Apply slippage on exit (worse fill)
    if pos["direction"] == "long":
        actual_exit = exit_price - slippage
        pnl = (actual_exit - pos["entry_price"]) * pos["shares"]
    else:
        actual_exit = exit_price + slippage
        pnl = (pos["entry_price"] - actual_exit) * pos["shares"]

    pnl_r = pnl / pos["dollar_risk"] if pos["dollar_risk"] > 0 else 0.0

    # ── Submit close order to Alpaca ───────────────────────────────────────────
    try:
        close_side = OrderSide.SELL if pos["direction"] == "long" else OrderSide.BUY
        order_request = MarketOrderRequest(
            symbol=SYMBOL,
            qty=round(pos["shares"], 4),
            side=close_side,
            time_in_force=TimeInForce.DAY,
        )
        order = _trading_client.submit_order(order_request)
        print(f"[Executor] Close order submitted: {order.id} | {close_side.value} {pos['shares']:.4f} shares")
    except Exception as e:
        print(f"[Executor] ERROR closing position: {e}")
        # Still clear internal state to avoid stuck position
        _open_position = None
        return None

    result = {**pos, "exit_price": actual_exit, "exit_reason": exit_reason,
              "actual_pnl": round(pnl, 4), "actual_pnl_r": round(pnl_r, 4),
              "timestamp_exit": datetime.now().isoformat(),
              "close_order_id": str(order.id)}

    print(f"[Executor] Closed: {exit_reason} | P&L=${pnl:.4f} ({pnl_r:.2f}R)")
    _open_position = None
    return result
