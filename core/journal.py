"""
core/journal.py
===============
Logs every trade AND every skipped trade to a daily SQLite database.
The journal is Creature's memory. Without it, learning is impossible.

Every record includes the required fields from the constitution.
"""

import os
import json
import sqlite3
from datetime import datetime, date


_DB_DIR  = os.path.join(os.path.dirname(__file__), "..", "journal_data")
_DB_NAME = "creature_journal.db"


def _get_db_path() -> str:
    os.makedirs(_DB_DIR, exist_ok=True)
    return os.path.join(_DB_DIR, _DB_NAME)


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist. Safe to call on every startup."""
    conn = _get_connection()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date              TEXT NOT NULL,
            timestamp_entry         TEXT,
            timestamp_exit          TEXT,
            direction               TEXT,      -- 'long' or 'short'
            entry_price             REAL,
            stop_price              REAL,
            target_price            REAL,
            breakeven_price         REAL,
            exit_price              REAL,
            shares                  REAL,
            dollar_risk             REAL,
            actual_pnl              REAL,
            actual_pnl_r            REAL,      -- P&L expressed in R multiples
            health_state            TEXT,
            learning_phase          TEXT,
            setup_type              TEXT,
            volume_confirmed        INTEGER,   -- 1 = true, 0 = false
            direction_filter_passed INTEGER,
            exit_reason             TEXT,      -- target_hit | stop_hit | breakeven_stop | eod_close | halt_exit | daily_stop
            slippage_breach         INTEGER,   -- 1 = true, 0 = false
            notes                   TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS skipped_trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date      TEXT NOT NULL,
            timestamp       TEXT,
            direction       TEXT,
            skip_reason     TEXT NOT NULL,
            signal_details  TEXT           -- JSON blob of what was seen
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_summary (
            summary_date        TEXT PRIMARY KEY,
            trades_taken        INTEGER DEFAULT 0,
            trades_skipped      INTEGER DEFAULT 0,
            gross_pnl           REAL DEFAULT 0.0,
            daily_stop_hit      INTEGER DEFAULT 0,
            health_state_eod    TEXT,
            learning_phase_eod  TEXT,
            closing_balance     REAL,
            notes               TEXT
        )
    """)

    # ── Crypto learning tables ─────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS asset_scores (
            symbol           TEXT PRIMARY KEY,
            total_trades     INTEGER DEFAULT 0,
            wins             INTEGER DEFAULT 0,
            losses           INTEGER DEFAULT 0,
            total_pnl        REAL DEFAULT 0.0,
            total_pnl_r      REAL DEFAULT 0.0,
            win_rate         REAL DEFAULT 0.0,
            expectancy_r     REAL DEFAULT 0.0,
            hard_blocked     INTEGER DEFAULT 0,
            last_updated     TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS hourly_performance (
            symbol       TEXT NOT NULL,
            hour_utc     INTEGER NOT NULL,
            wins         INTEGER DEFAULT 0,
            losses       INTEGER DEFAULT 0,
            win_rate     REAL DEFAULT 0.0,
            last_updated TEXT,
            PRIMARY KEY (symbol, hour_utc)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS cycle_events (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_utc    TEXT NOT NULL,
            phase            TEXT,
            status           TEXT NOT NULL,  -- ok | warning | error | safe_mode
            action           TEXT NOT NULL,
            reason           TEXT,
            health_state     TEXT,
            learning_phase   TEXT,
            current_balance  REAL,
            open_positions   INTEGER,
            candidates_count INTEGER,
            best_symbol      TEXT,
            error_type       TEXT,
            error_message    TEXT,
            details_json     TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS system_events (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_utc  TEXT NOT NULL,
            level          TEXT NOT NULL,  -- info | warn | error | critical
            code           TEXT NOT NULL,
            message        TEXT NOT NULL,
            details_json   TEXT
        )
    """)

    # Add symbol column to trades if it doesn't exist yet (safe migration)
    try:
        c.execute("ALTER TABLE trades ADD COLUMN symbol TEXT DEFAULT 'SPY'")
        conn.commit()
    except Exception:
        pass  # Column already exists — that's fine

    conn.commit()
    conn.close()
    print("[Journal] Database initialised.")


def log_trade(trade: dict):
    """
    Log a completed trade. Pass a dict with all required fields.
    Missing fields are stored as NULL — but a warning is printed.
    """
    required = [
        "direction", "entry_price", "stop_price", "target_price",
        "exit_price", "shares", "dollar_risk", "actual_pnl",
        "health_state", "learning_phase", "exit_reason"
    ]
    for field in required:
        if field not in trade:
            print(f"[Journal] WARNING: missing field '{field}' in trade log.")

    conn = _get_connection()
    conn.execute("""
        INSERT INTO trades (
            trade_date, timestamp_entry, timestamp_exit,
            direction, entry_price, stop_price, target_price, breakeven_price,
            exit_price, shares, dollar_risk, actual_pnl, actual_pnl_r,
            health_state, learning_phase, setup_type,
            volume_confirmed, direction_filter_passed,
            exit_reason, slippage_breach, symbol, notes
        ) VALUES (
            :trade_date, :timestamp_entry, :timestamp_exit,
            :direction, :entry_price, :stop_price, :target_price, :breakeven_price,
            :exit_price, :shares, :dollar_risk, :actual_pnl, :actual_pnl_r,
            :health_state, :learning_phase, :setup_type,
            :volume_confirmed, :direction_filter_passed,
            :exit_reason, :slippage_breach, :symbol, :notes
        )
    """, {
        "trade_date":             trade.get("trade_date", str(date.today())),
        "timestamp_entry":        trade.get("timestamp_entry"),
        "timestamp_exit":         trade.get("timestamp_exit"),
        "direction":              trade.get("direction"),
        "entry_price":            trade.get("entry_price"),
        "stop_price":             trade.get("stop_price"),
        "target_price":           trade.get("target_price"),
        "breakeven_price":        trade.get("breakeven_price"),
        "exit_price":             trade.get("exit_price"),
        "shares":                 trade.get("shares"),
        "dollar_risk":            trade.get("dollar_risk"),
        "actual_pnl":             trade.get("actual_pnl"),
        "actual_pnl_r":           trade.get("actual_pnl_r"),
        "health_state":           trade.get("health_state"),
        "learning_phase":         trade.get("learning_phase"),
        "setup_type":             trade.get("setup_type"),
        "volume_confirmed":       int(trade.get("volume_confirmed", False)),
        "direction_filter_passed": int(trade.get("direction_filter_passed", False)),
        "exit_reason":            trade.get("exit_reason"),
        "slippage_breach":        int(trade.get("slippage_breach", False)),
        "symbol":                 trade.get("symbol", "SPY"),
        "notes":                  trade.get("notes"),
    })
    conn.commit()
    conn.close()
    print(f"[Journal] Trade logged: {trade.get('direction')} | P&L: ${trade.get('actual_pnl', 0):.2f} | Exit: {trade.get('exit_reason')}")


def log_skip(direction: str, reason: str, signal_details: dict = None):
    """Log a trade that was considered but not taken."""
    conn = _get_connection()
    conn.execute("""
        INSERT INTO skipped_trades (trade_date, timestamp, direction, skip_reason, signal_details)
        VALUES (?, ?, ?, ?, ?)
    """, (
        str(date.today()),
        datetime.now().isoformat(),
        direction,
        reason,
        json.dumps(signal_details) if signal_details else None,
    ))
    conn.commit()
    conn.close()
    print(f"[Journal] Trade skipped: {direction} — {reason}")


def get_recent_trades(n: int = 20) -> list:
    """Return the last N completed trades as a list of dicts."""
    conn = _get_connection()
    rows = conn.execute(
        "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (n,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_asset_score(symbol: str) -> dict | None:
    """Return asset score row for a symbol, or None if not found."""
    conn = _get_connection()
    row = conn.execute(
        "SELECT * FROM asset_scores WHERE symbol = ?", (symbol,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_asset_score(symbol: str, win: bool, pnl_r: float):
    """Update running stats for a symbol after a trade closes."""
    conn = _get_connection()
    existing = conn.execute(
        "SELECT * FROM asset_scores WHERE symbol = ?", (symbol,)
    ).fetchone()

    if existing:
        row = dict(existing)
        row["total_trades"] += 1
        row["wins"]    += 1 if win else 0
        row["losses"]  += 0 if win else 1
        row["total_pnl_r"] += pnl_r
        row["win_rate"]     = row["wins"] / row["total_trades"]
        row["expectancy_r"] = row["total_pnl_r"] / row["total_trades"]
        row["last_updated"] = datetime.now().isoformat()
        # Hard block: < 25% win rate after 5+ trades
        if row["total_trades"] >= 5 and row["win_rate"] < 0.25:
            row["hard_blocked"] = 1
            print(f"[Journal] {symbol} HARD BLOCKED: WR={row['win_rate']:.0%} over {row['total_trades']} trades")
        conn.execute("""
            UPDATE asset_scores
            SET total_trades=:total_trades, wins=:wins, losses=:losses,
                total_pnl_r=:total_pnl_r, win_rate=:win_rate,
                expectancy_r=:expectancy_r, hard_blocked=:hard_blocked,
                last_updated=:last_updated
            WHERE symbol=:symbol
        """, row)
    else:
        conn.execute("""
            INSERT INTO asset_scores
            (symbol, total_trades, wins, losses, total_pnl_r, win_rate, expectancy_r, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol, 1, 1 if win else 0, 0 if win else 1,
            pnl_r, 1.0 if win else 0.0, pnl_r,
            datetime.now().isoformat()
        ))

    conn.commit()
    conn.close()


def update_hourly_performance(symbol: str, hour_utc: int, win: bool):
    """Track win/loss per symbol per UTC hour."""
    conn = _get_connection()
    existing = conn.execute(
        "SELECT * FROM hourly_performance WHERE symbol=? AND hour_utc=?",
        (symbol, hour_utc)
    ).fetchone()

    if existing:
        row = dict(existing)
        row["wins"]   += 1 if win else 0
        row["losses"] += 0 if win else 1
        total = row["wins"] + row["losses"]
        row["win_rate"]     = row["wins"] / total if total > 0 else 0.0
        row["last_updated"] = datetime.now().isoformat()
        conn.execute("""
            UPDATE hourly_performance
            SET wins=:wins, losses=:losses, win_rate=:win_rate, last_updated=:last_updated
            WHERE symbol=:symbol AND hour_utc=:hour_utc
        """, row)
    else:
        conn.execute("""
            INSERT INTO hourly_performance (symbol, hour_utc, wins, losses, win_rate, last_updated)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (symbol, hour_utc, 1 if win else 0, 0 if win else 1,
              1.0 if win else 0.0, datetime.now().isoformat()))

    conn.commit()
    conn.close()


def get_daily_stats(n_days: int = 20) -> dict:
    """
    Calculate win rate and expectancy over the last N trades.
    Used by the learning module to determine phase transitions.
    """
    trades = get_recent_trades(n_days)
    if not trades:
        return {"win_rate": 0.0, "expectancy_r": 0.0, "total_trades": 0}

    winners = [t for t in trades if (t["actual_pnl"] or 0) > 0]
    win_rate = len(winners) / len(trades)

    r_values = [t["actual_pnl_r"] for t in trades if t["actual_pnl_r"] is not None]
    expectancy = sum(r_values) / len(r_values) if r_values else 0.0

    return {
        "win_rate":     round(win_rate, 4),
        "expectancy_r": round(expectancy, 4),
        "total_trades": len(trades),
    }


def log_cycle_event(event: dict):
    """
    Persist a single runtime cycle heartbeat.
    This is intentionally lightweight and called every loop iteration.
    """
    conn = _get_connection()
    conn.execute("""
        INSERT INTO cycle_events (
            timestamp_utc, phase, status, action, reason,
            health_state, learning_phase, current_balance,
            open_positions, candidates_count, best_symbol,
            error_type, error_message, details_json
        ) VALUES (
            :timestamp_utc, :phase, :status, :action, :reason,
            :health_state, :learning_phase, :current_balance,
            :open_positions, :candidates_count, :best_symbol,
            :error_type, :error_message, :details_json
        )
    """, {
        "timestamp_utc":    event.get("timestamp_utc", datetime.utcnow().isoformat()),
        "phase":            event.get("phase"),
        "status":           event.get("status", "ok"),
        "action":           event.get("action", "idle"),
        "reason":           event.get("reason"),
        "health_state":     event.get("health_state"),
        "learning_phase":   event.get("learning_phase"),
        "current_balance":  event.get("current_balance"),
        "open_positions":   event.get("open_positions"),
        "candidates_count": event.get("candidates_count"),
        "best_symbol":      event.get("best_symbol"),
        "error_type":       event.get("error_type"),
        "error_message":    event.get("error_message"),
        "details_json":     json.dumps(event.get("details", {})),
    })
    conn.commit()
    conn.close()


def log_system_event(level: str, code: str, message: str, details: dict | None = None):
    """Persist structured system-level events such as reconcile failures and exceptions."""
    conn = _get_connection()
    conn.execute("""
        INSERT INTO system_events (timestamp_utc, level, code, message, details_json)
        VALUES (?, ?, ?, ?, ?)
    """, (
        datetime.utcnow().isoformat(),
        level,
        code,
        message,
        json.dumps(details or {}),
    ))
    conn.commit()
    conn.close()
