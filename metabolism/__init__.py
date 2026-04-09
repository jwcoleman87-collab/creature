"""
metabolism
==========
Tracks Creature's daily state: consecutive losing days, daily stop,
and bridges the learning phase system with the health monitor.
This is the organism's nervous system — it integrates signals from
all other modules and decides whether Creature is fit to trade today.
"""

import os
import json
from datetime import date
from core.constitution import get
from core.health import HealthMonitor, DEAD


_STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "journal_data", "metabolism_state.json")


def _load_state() -> dict:
    """Load persistent daily state from disk."""
    os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
    if os.path.exists(_STATE_FILE):
        with open(_STATE_FILE, "r") as f:
            return json.load(f)
    return _default_state()


def _save_state(state: dict):
    os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
    with open(_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _default_state() -> dict:
    return {
        "current_balance":        get("risk.starting_balance"),
        "peak_equity":            get("risk.starting_balance"),
        "today_loss":             0.0,
        "today_trades":           0,
        "long_slot_used":         False,
        "short_slot_used":        False,
        "consecutive_losing_days": 0,
        "paused_for_review":      False,
        "learning_phase":         "newborn",
        "total_trades":           0,
        "last_updated":           str(date.today()),
    }


class Metabolism:
    def __init__(self):
        self.state   = _load_state()
        self.health  = HealthMonitor(self.state["current_balance"])
        current      = self.state["current_balance"]
        peak         = self.state.get("peak_equity", current)
        # Sanity: peak can never be less than current (protects against corrupted state)
        self.health.peak_equity    = max(peak, current)
        self.health.current_equity = current
        self.health.update(current)

        self._daily_stop_pct      = get("risk.daily_stop_pct")          # 0.01
        self._max_consec_days     = get("risk.max_consecutive_losing_days")  # 3
        self._min_balance         = get("risk.minimum_tradeable_balance") # 400.0

        print(f"[Metabolism] Woke up. Balance=${self.state['current_balance']:.2f} | Health={self.health.state} | Phase={self.state['learning_phase']}")

    # ── Daily reset ────────────────────────────────────────────────────────────

    def start_of_day(self):
        """Call this at 09:30 each morning to reset daily counters."""
        today = str(date.today())
        if self.state.get("last_updated") != today:
            print(f"[Metabolism] New day: {today}. Resetting daily counters.")
            self.state["today_loss"]       = 0.0
            self.state["today_trades"]     = 0
            self.state["long_slot_used"]   = False
            self.state["short_slot_used"]  = False
            self.state["last_updated"]     = today
            _save_state(self.state)

    # ── Trade slot management ──────────────────────────────────────────────────

    def slot_available(self, direction: str) -> bool:
        """Check if the long or short slot is available today."""
        if direction == "long":
            return not self.state["long_slot_used"]
        elif direction == "short":
            return not self.state["short_slot_used"]
        return False

    def use_slot(self, direction: str):
        """Mark a directional slot as used for the day."""
        if direction == "long":
            self.state["long_slot_used"] = True
        elif direction == "short":
            self.state["short_slot_used"] = True
        _save_state(self.state)

    # ── Trade outcome recording ────────────────────────────────────────────────

    def record_trade_outcome(self, pnl: float):
        """Call this after every trade closes."""
        self.state["today_loss"]   += min(pnl, 0)   # Only count losses
        self.state["today_trades"] += 1
        self.state["total_trades"] += 1
        self.state["current_balance"] = round(self.state["current_balance"] + pnl, 4)

        # Update health
        health_state = self.health.update(self.state["current_balance"])
        self.state["peak_equity"] = self.health.peak_equity

        # Check daily stop
        daily_stop_dollar = self.state["current_balance"] * self._daily_stop_pct
        if abs(self.state["today_loss"]) >= daily_stop_dollar:
            print(f"[Metabolism] Daily stop hit. Loss today=${abs(self.state['today_loss']):.2f}. No more trades today.")
            self.state["long_slot_used"]  = True
            self.state["short_slot_used"] = True

        _save_state(self.state)
        self._update_learning_phase()
        return health_state

    def record_losing_day(self):
        """Call at EOD if today_loss > 0."""
        if self.state["today_loss"] < 0:
            self.state["consecutive_losing_days"] += 1
            print(f"[Metabolism] Losing day #{self.state['consecutive_losing_days']}")
            if self.state["consecutive_losing_days"] >= self._max_consec_days:
                self.state["paused_for_review"] = True
                print(f"[Metabolism] *** {self._max_consec_days} consecutive losing days. Pausing for owner review. ***")
        else:
            self.state["consecutive_losing_days"] = 0
        _save_state(self.state)

    # ── Fitness check ──────────────────────────────────────────────────────────

    def is_fit_to_trade(self, direction: str) -> tuple[bool, str]:
        """
        Master gate. Returns (True, "ok") or (False, reason).
        Call this before acting on any signal.
        """
        if self.state["paused_for_review"]:
            return False, "paused_for_owner_review"

        if self.health.state == DEAD:
            return False, "creature_is_dead"

        if self.state["current_balance"] < self._min_balance:
            return False, f"balance_below_minimum (${self.state['current_balance']:.2f})"

        if not self.health.can_trade():
            return False, f"health_state_blocks_trading ({self.health.state})"

        if not self.slot_available(direction):
            return False, f"{direction}_slot_already_used_today"

        return True, "ok"

    # ── Learning phase ─────────────────────────────────────────────────────────

    def _update_learning_phase(self):
        """Determine which learning phase Creature is in based on trade count + confidence."""
        total = self.state["total_trades"]
        current_phase = self.state["learning_phase"]

        try:
            from core.journal import get_daily_stats
            stats = get_daily_stats(20)
            win_rate   = stats["win_rate"]
            expectancy = stats["expectancy_r"]
        except Exception:
            win_rate, expectancy = 0.0, 0.0

        if total <= 20:
            new_phase = "newborn"
        elif total <= 100:
            new_phase = "developing" if (win_rate > 0.45 and expectancy > 0) else "newborn"
        else:
            new_phase = "mature" if (win_rate > 0.50 and expectancy > 0.5) else "developing"

        if new_phase != current_phase:
            print(f"[Metabolism] Learning phase: {current_phase} → {new_phase} (trades={total}, WR={win_rate:.0%}, E={expectancy:.2f}R)")
            self.state["learning_phase"] = new_phase
            _save_state(self.state)

    def get_risk_pct(self) -> float:
        """Return the current risk % based on learning phase and health."""
        base = get("risk.default_risk_per_trade")    # 0.0025
        max_r = get("risk.risk_per_trade_max")        # 0.005

        phase = self.state["learning_phase"]
        total = self.state["total_trades"]

        if phase == "newborn":
            risk = base
        elif phase == "developing":
            # Scale linearly between min and max based on progress through phase
            progress = min((total - 20) / 80, 1.0)   # 0.0 at trade 20, 1.0 at trade 100
            risk = base + (max_r - base) * progress
        else:  # mature
            risk = max_r

        # Apply health multiplier
        return risk * self.health.risk_multiplier()

    def summary(self) -> dict:
        return {
            **self.state,
            "health": self.health.summary(),
            "risk_pct": round(self.get_risk_pct(), 6),
        }
