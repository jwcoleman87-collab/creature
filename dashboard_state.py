"""
dashboard_state.py
==================
Shared in-memory state between crypto_main.py and the web dashboard.
Thread-safe. crypto_main writes here; web_server reads from here.
"""

import os
import json
import threading
import socket
import uuid
from datetime import datetime, timezone
from core.constitution import get

STARTING_BALANCE = float(get("risk.starting_balance", 500.0))
UPDATES_DIR = os.path.join(os.path.dirname(__file__), "updates")
REVISION_FILE = os.path.join(UPDATES_DIR, "current_revision.json")


def _load_revision_meta() -> dict:
    default = {
        "id": "dev-local",
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "title": "Local Development",
        "notes_file": "updates/README.md",
    }
    try:
        if not os.path.exists(REVISION_FILE):
            return default
        with open(REVISION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return default
        return {
            "id": data.get("id", default["id"]),
            "date": data.get("date", default["date"]),
            "title": data.get("title", default["title"]),
            "notes_file": data.get("notes_file", default["notes_file"]),
        }
    except Exception:
        return default


REVISION_META = _load_revision_meta()
INSTANCE_ID = os.environ.get("CREATURE_INSTANCE_ID", f"local-{uuid.uuid4().hex[:8]}")
RUNTIME_SOURCE = os.environ.get("CREATURE_RUNTIME_SOURCE", "local")

_lock  = threading.Lock()
_state = {
    "status":         "STARTING",
    "revision":       REVISION_META,
    "uptime_start":   datetime.now(timezone.utc).isoformat(),
    "last_updated":   None,
    "runtime": {
        "instance_id": INSTANCE_ID,
        "source": RUNTIME_SOURCE,
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "cycle_count": 0,
        "last_cycle_phase": "startup",
        "last_cycle_status": "starting",
        "last_cycle_at": None,
    },
    "balance": {
        "current":  STARTING_BALANCE,
        "peak":     STARTING_BALANCE,
        "starting": STARTING_BALANCE,
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
            "time":    datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
            "message": message,
            "level":   level,   # info | signal | trade | warn | error
        }
        _state["thinking"].append(entry)
        _state["thinking"] = _state["thinking"][-60:]  # keep last 60 lines


def get_state() -> dict:
    with _lock:
        import copy
        snapshot = copy.deepcopy(_state)
        snapshot["runtime"]["pid"] = os.getpid()
        return snapshot


def mark_heartbeat(phase: str, status: str = "ok"):
    """Mark each runtime cycle so UI can detect freshness."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with _lock:
        runtime = _state.get("runtime", {})
        runtime["cycle_count"] = int(runtime.get("cycle_count", 0)) + 1
        runtime["last_cycle_phase"] = phase
        runtime["last_cycle_status"] = status
        runtime["last_cycle_at"] = now_iso
        _state["runtime"] = runtime
        _state["last_updated"] = now_iso


def sync_from_organism(organism):
    """Pull latest organism state into dashboard."""
    s = organism.state
    h = organism.health.summary()
    starting = STARTING_BALANCE

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
