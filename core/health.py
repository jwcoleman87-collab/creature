"""
core/health.py
==============
Tracks Creature's health state based on drawdown from peak equity.
States: HEALTHY → WOUNDED → SURVIVAL → LOCKOUT → DEAD

Health state is checked before EVERY trade decision.
No trade is placed without first passing the health gate.
"""

from core.constitution import get


# ── State constants ────────────────────────────────────────────────────────────
HEALTHY  = "HEALTHY"
WOUNDED  = "WOUNDED"
SURVIVAL = "SURVIVAL"
LOCKOUT  = "LOCKOUT"
DEAD     = "DEAD"

# States that allow trading (with restrictions)
TRADEABLE_STATES = {HEALTHY, WOUNDED, SURVIVAL}


class HealthMonitor:
    def __init__(self, starting_balance: float):
        self.peak_equity    = starting_balance
        self.current_equity = starting_balance
        self.state          = HEALTHY

        # Load thresholds from constitution
        self.wounded_pct  = get("risk.wounded_threshold")   # 0.03
        self.survival_pct = get("risk.survival_threshold")  # 0.05
        self.lockout_pct  = get("risk.lockout_threshold")   # 0.075
        self.death_pct    = get("risk.death_threshold")      # 0.10
        self.min_balance  = get("risk.minimum_tradeable_balance")  # 400.00

    def update(self, current_equity: float) -> str:
        """
        Update equity and recalculate health state.
        Returns the new state string.
        """
        self.current_equity = current_equity

        # Update peak equity (only goes up, never down)
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

        drawdown = self._drawdown_from_peak()
        previous_state = self.state

        # Determine new state
        if drawdown >= self.death_pct or current_equity <= self.min_balance:
            self.state = DEAD
        elif drawdown >= self.lockout_pct:
            self.state = LOCKOUT
        elif drawdown >= self.survival_pct:
            self.state = SURVIVAL
        elif drawdown >= self.wounded_pct:
            self.state = WOUNDED
        else:
            # ── Exit conditions (conservative — must reach NEW equity high) ──
            # Only return to HEALTHY from WOUNDED once equity exceeds peak
            if previous_state in (WOUNDED, SURVIVAL, LOCKOUT):
                if current_equity >= self.peak_equity:
                    self.state = HEALTHY
                # else: stay in the previous cautious state until truly recovered
            else:
                self.state = HEALTHY

        if self.state != previous_state:
            print(
                f"[Health] State changed: {previous_state} → {self.state} "
                f"(equity=${current_equity:.2f}, drawdown={drawdown*100:.2f}%)"
            )

        return self.state

    def can_trade(self) -> bool:
        """Returns True if Creature is allowed to open new positions."""
        return self.state in TRADEABLE_STATES

    def risk_multiplier(self) -> float:
        """
        Returns a multiplier (0.0–1.0) applied to position sizing.
        Healthy = full size | Wounded = 50% | Survival = 25% | Lockout/Dead = 0%
        """
        multipliers = {
            HEALTHY:  1.0,
            WOUNDED:  0.5,
            SURVIVAL: 0.25,
            LOCKOUT:  0.0,
            DEAD:     0.0,
        }
        return multipliers.get(self.state, 0.0)

    def _drawdown_from_peak(self) -> float:
        """Fractional drawdown from peak equity."""
        if self.peak_equity <= 0:
            return 0.0
        return (self.peak_equity - self.current_equity) / self.peak_equity

    def summary(self) -> dict:
        return {
            "state":          self.state,
            "current_equity": round(self.current_equity, 2),
            "peak_equity":    round(self.peak_equity, 2),
            "drawdown_pct":   round(self._drawdown_from_peak() * 100, 2),
            "can_trade":      self.can_trade(),
            "risk_multiplier": self.risk_multiplier(),
        }
