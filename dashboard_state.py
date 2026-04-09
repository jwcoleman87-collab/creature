"""
dashboard_state.py
==================
Shared in-memory state between crypto_main.py and the web dashboard.
Thread-safe. crypto_main writes here; web_server reads from here.
"""

import threading
from datetime import datetime, timezone

_lock  = threading.Lock()
_state = {
    "status":         "STARTING",
    "uptime_start":   datetime.now(timezone.utc).isoformat(),
    "last_updated":   None,
    "balance": {
        "current":  500.0,
        "peak":     500.0,
        "starting": 500.0,
        "pnl":      0.0,
        "pnl_pct":  0.0,
    },
    "health": {
        "state":          "HEALTHY",
        "drawdown_pct":   0.0,
        "can_trade":      True,
        "risk_multiplier": 1.0,
    },
    "learning": {
        "phase":       "newborn",
        "total_trades": 0,
        "win_rate":    0.0,
        "expectancy_r": 0.0,
        "confidence":  0.0,
    },
    "today": {
        "trades": 0,
        "pnl":    0.0,
    },
    "sentiment": {
        "fear_greed":  None,
        "label":       "Unknown",
        "adj":         0.0,
    },
    "open_position":  None,
    "recent_trades":  [],
    "asset_scores":   [],
    "last_scan": {
        "timestamp":   None,
        "candidates":  [],
        "action":      "waiting",
    },
    "thinking":       [],   # ring buffer of last 60 thoughts
    "prices":         {},   # { "BTC/USD": {"price": 84234.5, "change_pct": -2.3} }
}


def update(key: str, value):
    with _lock:
        _state[key] = value
        _state["last_updated"] = datetime.now(timezone.utc).isoformat()


def think(message: str, level: str = "info"):
    """Add a line to the creature's thinking log."""
    with _lock:
        entry = {
            "time":    datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "message": message,
            "level":   level,   # info | signal | trade | warn | error
        }
        _state["thinking"].append(entry)
        _state["thinking"] = _state["thinking"][-60:]  # keep last 60 lines


def get_state() -> dict:
    with _lock:
        import copy
        return copy.deepcopy(_state)


def sync_from_organism(organism):
    """Pull latest organism state into dashboard."""
    s = organism.state
    h = organism.health.summary()
    starting = 500.0

    with _lock:
        _state["balance"] = {
            "current":  round(s["current_balance"], 2),
            "peak":     round(s["peak_equity"], 2),
            "starting": starting,
            "pnl":      round(s["current_balance"] - starting, 2),
            "pnl_pct":  round((s["current_balance"] - starting) / starting * 100, 2),
        }
        _state["health"] = {
            "state":           h["state"],
            "drawdown_pct":    h["drawdown_pct"],
            "can_trade":       h["can_trade"],
            "risk_multiplier": h["risk_multiplier"],
        }
        _state["learning"] = {
            "phase":        s["learning_phase"],
            "total_trades": s["total_trades"],
            "win_rate":     0.0,
            "expectancy_r": 0.0,
            "confidence":   0.0,
        }
        _state["today"] = {
            "trades": s["today_trades"],
            "pnl":    round(s.get("today_loss", 0.0), 2),
        }
        _state["last_updated"] = datetime.now(timezone.utc).isoformat()
