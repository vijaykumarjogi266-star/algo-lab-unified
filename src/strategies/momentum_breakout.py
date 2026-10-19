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
from typing import Any, Dict, List, Optional
import uuid

from src.brokergateway.states import OrderSide
from src.core.tick_precision import quantize_order_price
from src.scanner.scanner import MarketBar, ScanCandidate
from src.strategies.base import BaseStrategy
from src.strategies.models import StrategySignal


class MomentumBreakoutStrategy(BaseStrategy):
    """Production intraday momentum breakout signal generator."""

    STRATEGY_ID = "STRAT_MOMENTUM_BREAKOUT_V1"

    def __init__(
        self,
        strategy_id: str = "STRAT_MOMENTUM_BREAKOUT_V1",
        version: str = "2.0.0",
        risk_reward_ratio: Decimal = Decimal("2.0"),
        atr_stop_multiplier: Decimal = Decimal("1.0"),
        parameters: Optional[Dict[str, Any]] = None,
    ):
        params = dict(parameters or {})
        if "risk_reward_ratio" not in params:
            params["risk_reward_ratio"] = risk_reward_ratio
        if "atr_stop_multiplier" not in params:
            params["atr_stop_multiplier"] = atr_stop_multiplier

        super().__init__(strategy_id=strategy_id, version=version, parameters=params)
        self.risk_reward_ratio: Decimal = self.parameters["risk_reward_ratio"]
        self.atr_stop_multiplier: Decimal = self.parameters["atr_stop_multiplier"]

    def validate_and_sanitize_parameters(self, params: Dict[str, Any]) -> Dict[str, Any]:
        rr = params.get("risk_reward_ratio", Decimal("2.0"))
        self._validate_numeric_range("risk_reward_ratio", rr, min_val=1.0, max_val=5.0)

        atr_mult = params.get("atr_stop_multiplier", Decimal("1.0"))
        self._validate_numeric_range("atr_stop_multiplier", atr_mult, min_val=0.5, max_val=3.0)

        return {
            "risk_reward_ratio": Decimal(str(rr)),
            "atr_stop_multiplier": Decimal(str(atr_mult)),
        }

    def evaluate(
        self,
        current_bar: MarketBar,
        history: List[MarketBar],
        indicators: Optional[Dict[str, Any]] = None,
    ) -> Optional[StrategySignal]:
        """Direct bar evaluation against 20-period lookback."""
        if not history or len(history) < 20:
            return None

        recent_bars = history[-20:]
        highest_20 = max(b.high for b in recent_bars)
        lowest_20 = min(b.low for b in recent_bars)

        # RVOL
        recent_vols = [b.volume for b in recent_bars if b.volume > 0]
        avg_vol = Decimal(str(sum(recent_vols))) / Decimal(str(len(recent_vols))) if recent_vols else Decimal("1.0")
        rvol = Decimal(str(current_bar.volume)) / avg_vol if avg_vol > Decimal("0.0") else Decimal("1.0")

        if rvol < Decimal("1.5"):
            return None

        # True Range / ATR
        tr_list = []
        all_bars = history + [current_bar]
        for i in range(1, len(all_bars[-15:])):
            c, p = all_bars[i], all_bars[i - 1]
            tr = max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
            tr_list.append(tr)
        atr = sum(tr_list) / Decimal(str(len(tr_list))) if tr_list else (current_bar.close * Decimal("0.01"))

        if current_bar.close > highest_20:
            bias = "BULLISH"
        elif current_bar.close < lowest_20:
            bias = "BEARISH"
        else:
            return None

        candidate = ScanCandidate(
            symbol=current_bar.symbol,
            timestamp_iso=current_bar.timestamp_iso,
            current_price=current_bar.close,
            rvol=rvol,
            atr=atr,
            spread_pct=Decimal("0.0005"),
            bias=bias,
        )
        return self.evaluate_candidate(candidate)

    def evaluate_candidate(self, candidate: ScanCandidate) -> Optional[StrategySignal]:
        """Translates scanner candidate into an approved risk-defined strategy signal."""
        stop_distance = candidate.atr * self.atr_stop_multiplier
        profit_distance = stop_distance * self.risk_reward_ratio

        if candidate.bias == "BULLISH":
            side = OrderSide.BUY
            raw_stop = candidate.current_price - stop_distance
            raw_target = candidate.current_price + profit_distance
            stop_loss = quantize_order_price(
                raw_stop,
                candidate.symbol,
                side_is_buy=True,
                order_purpose="STOP_LOSS",
                trade_date=candidate.timestamp_iso,
                reference_price=candidate.current_price,
            )
            take_profit = quantize_order_price(
                raw_target,
                candidate.symbol,
                side_is_buy=True,
                order_purpose="TAKE_PROFIT",
                trade_date=candidate.timestamp_iso,
                reference_price=candidate.current_price,
            )
        elif candidate.bias == "BEARISH":
            side = OrderSide.SELL
            raw_stop = candidate.current_price + stop_distance
            raw_target = candidate.current_price - profit_distance
            stop_loss = quantize_order_price(
                raw_stop,
                candidate.symbol,
                side_is_buy=False,
                order_purpose="STOP_LOSS",
                trade_date=candidate.timestamp_iso,
                reference_price=candidate.current_price,
            )
            take_profit = quantize_order_price(
                raw_target,
                candidate.symbol,
                side_is_buy=False,
                order_purpose="TAKE_PROFIT",
                trade_date=candidate.timestamp_iso,
                reference_price=candidate.current_price,
            )
        else:
            return None

        entry_price = quantize_order_price(
            candidate.current_price,
            candidate.symbol,
            side_is_buy=(side == OrderSide.BUY),
            order_purpose="ENTRY",
            trade_date=candidate.timestamp_iso,
            reference_price=candidate.current_price,
        )

        signal_id = f"SIG-{uuid.uuid4().hex[:8].upper()}"
        confidence = min(Decimal("1.0"), candidate.rvol / Decimal("3.0"))

        return StrategySignal(
            signal_id=signal_id,
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            symbol=candidate.symbol,
            side=side,
            target_entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence_score=confidence,
            timestamp_iso=candidate.timestamp_iso,
            order_type="LIMIT",
            reason=f"Breakout {candidate.bias} with RVOL {candidate.rvol:.2f}",
            evidence={"rvol": str(candidate.rvol), "atr": str(candidate.atr)},
            data_provenance={"scanner_timestamp": candidate.timestamp_iso},
        )
