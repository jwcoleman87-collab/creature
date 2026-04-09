"""
core/risk.py
============
Position sizing and stop/target calculations.
Implements fixed fractional sizing against CURRENT balance (not starting balance).

Formula:
    dollar_risk   = current_balance × risk_pct × health_multiplier
    position_size = dollar_risk / (entry_price − stop_price)

Hard cap: position_value (shares × entry_price) never exceeds max_position_value_pct.
"""

from core.constitution import get


class RiskEngine:
    def __init__(self):
        self.risk_min       = get("risk.risk_per_trade_min")          # 0.0025
        self.risk_max       = get("risk.risk_per_trade_max")          # 0.005
        self.risk_default   = get("risk.default_risk_per_trade")      # 0.0025
        self.max_pos_pct    = get("risk.max_position_value_pct")      # 0.05
        self.reward_risk    = get("strategy.exit_rules.reward_risk_ratio")  # 2.0
        self.be_trigger_r   = get("strategy.exit_rules.breakeven_trigger_r")  # 1.0
        self.slippage_per_share = get("costs.slippage_per_share")     # 0.02
        self.max_slippage   = get("risk.max_acceptable_slippage_per_share")  # 0.15

    def calculate(
        self,
        current_balance: float,
        entry_price: float,
        stop_price: float,
        health_multiplier: float = 1.0,
        risk_pct: float = None,
    ) -> dict:
        """
        Calculate full position sizing for a trade.

        Returns a dict with:
            shares          — fractional shares to buy/sell
            dollar_risk     — dollars at risk (after health multiplier)
            stop_price      — confirmed stop level
            target_price    — profit target (2R)
            breakeven_price — price at which stop moves to entry (1R)
            position_value  — total notional value of position
            valid           — False if the trade should be skipped (size too small)
        """
        if risk_pct is None:
            risk_pct = self.risk_default

        # Clamp risk to constitution limits
        risk_pct = max(self.risk_min, min(self.risk_max, risk_pct))

        # Apply health multiplier (wounded = 50%, survival = 25%)
        effective_risk_pct = risk_pct * health_multiplier
        dollar_risk = current_balance * effective_risk_pct

        # Distance from entry to stop
        stop_distance = abs(entry_price - stop_price)
        if stop_distance <= 0:
            return self._invalid("Stop distance is zero — cannot size position.")

        # Raw position size from risk formula
        raw_shares = dollar_risk / stop_distance

        # Hard cap: never hold more than max_position_value_pct of account
        max_notional = current_balance * self.max_pos_pct
        capped_shares = min(raw_shares, max_notional / entry_price)

        if capped_shares < 0.001:
            return self._invalid(f"Position size too small ({capped_shares:.4f} shares). Skipping.")

        # Recalculate actual dollar risk after cap
        actual_dollar_risk = capped_shares * stop_distance

        # Target and breakeven prices
        r_distance = stop_distance  # 1R = distance to stop
        is_long = entry_price > stop_price

        if is_long:
            target_price    = entry_price + (r_distance * self.reward_risk)
            breakeven_price = entry_price + (r_distance * self.be_trigger_r)
        else:
            target_price    = entry_price - (r_distance * self.reward_risk)
            breakeven_price = entry_price - (r_distance * self.be_trigger_r)

        return {
            "valid":           True,
            "shares":          round(capped_shares, 4),
            "dollar_risk":     round(actual_dollar_risk, 4),
            "entry_price":     entry_price,
            "stop_price":      stop_price,
            "target_price":    round(target_price, 4),
            "breakeven_price": round(breakeven_price, 4),
            "position_value":  round(capped_shares * entry_price, 4),
            "r_distance":      round(r_distance, 4),
            "was_capped":      capped_shares < raw_shares,
        }

    def check_slippage(self, intended_price: float, actual_fill: float) -> dict:
        """
        Compare intended stop price to actual fill.
        Returns whether slippage was within acceptable limits.
        """
        slippage = abs(actual_fill - intended_price)
        breach = slippage > self.max_slippage
        return {
            "intended_price": intended_price,
            "actual_fill":    actual_fill,
            "slippage":       round(slippage, 4),
            "breach":         breach,
            "max_allowed":    self.max_slippage,
        }

    def _invalid(self, reason: str) -> dict:
        print(f"[Risk] Skipping trade: {reason}")
        return {"valid": False, "reason": reason}
