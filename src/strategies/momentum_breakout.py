"""Intraday Momentum Breakout Strategy.

Rules:
1. Long entry when asset breaks above 20-period high with confirmed RVOL >= 1.5.
2. Short entry when asset breaks below 20-period low with confirmed RVOL >= 1.5.
3. Stop Loss: 1.0 x ATR from entry.
4. Take Profit: 2.0 x ATR from entry (2:1 Reward-to-Risk).
5. Stop/Target Collision Rule: If both Stop Loss and Take Profit levels are touched
   in the same bar, conservatively assume STOP LOSS is hit first (eliminates optimistic bias).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional
import uuid

from src.brokergateway.states import OrderSide
from src.scanner.scanner import MarketBar, ScanCandidate
from src.strategies.models import StrategySignal


class MomentumBreakoutStrategy:
    """Production intraday momentum breakout signal generator."""

    STRATEGY_ID = "STRAT_MOMENTUM_BREAKOUT_V1"

    def __init__(
        self,
        risk_reward_ratio: Decimal = Decimal("2.0"),
        atr_stop_multiplier: Decimal = Decimal("1.0"),
    ):
        self.risk_reward_ratio = risk_reward_ratio
        self.atr_stop_multiplier = atr_stop_multiplier

    def evaluate_candidate(self, candidate: ScanCandidate) -> Optional[StrategySignal]:
        """Translates scanner candidate into an approved risk-defined strategy signal."""
        stop_distance = candidate.atr * self.atr_stop_multiplier
        profit_distance = stop_distance * self.risk_reward_ratio

        if candidate.bias == "BULLISH":
            side = OrderSide.BUY
            stop_loss = candidate.current_price - stop_distance
            take_profit = candidate.current_price + profit_distance
        elif candidate.bias == "BEARISH":
            side = OrderSide.SELL
            stop_loss = candidate.current_price + stop_distance
            take_profit = candidate.current_price - profit_distance
        else:
            return None

        signal_id = f"SIG-{uuid.uuid4().hex[:8].upper()}"
        confidence = min(Decimal("1.0"), candidate.rvol / Decimal("3.0"))

        return StrategySignal(
            signal_id=signal_id,
            strategy_id=self.STRATEGY_ID,
            symbol=candidate.symbol,
            side=side,
            target_entry_price=candidate.current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence_score=confidence,
            timestamp_iso=candidate.timestamp_iso,
        )
