"""Canonical Bollinger Bands Mean Reversion Strategy.

Trades mean reversion when price stretches below the lower Bollinger Band.

Repairs and Invariants:
1. No Lookahead: Bands computed strictly from prior closed bars (history[-period:]); excludes current bar.
2. Parameter Overrides Bound: Supplied parameters directly govern indicator windows and multipliers.
3. Mean Reversion Target: Target anchored to middle band (SMA), stop loss protected by dynamic ATR.
4. Statutory Tick Quantization: Directional quantization applied to all order prices.
"""

from __future__ import annotations

from decimal import Decimal
import math
from typing import Any, Dict, List, Optional
import uuid

from src.brokergateway.states import OrderSide
from src.core.exceptions import InvalidStrategyParameterError
from src.core.tick_precision import quantize_order_price
from src.scanner.scanner import MarketBar
from src.strategies.base import BaseStrategy
from src.strategies.models import StrategySignal


class BollingerReversionStrategy(BaseStrategy):
    """Canonical Bollinger Bands Mean Reversion Strategy."""

    STRATEGY_ID = "STRAT_BOLLINGER_REVERSION"

    def __init__(
        self,
        strategy_id: str = "STRAT_BOLLINGER_REVERSION",
        version: str = "2.0.0",
        parameters: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(strategy_id=strategy_id, version=version, parameters=parameters)
        self.period: int = self.parameters["period"]
        self.num_std: Decimal = self.parameters["num_std"]
        self.stop_loss_atr: Decimal = self.parameters["stop_loss_atr"]
        self.target_atr: Decimal = self.parameters["target_atr"]

    def validate_and_sanitize_parameters(self, params: Dict[str, Any]) -> Dict[str, Any]:
        period = params.get("period", 20)
        self._validate_numeric_range("period", period, min_val=5, max_val=100, is_integer=True)

        std = params.get("num_std", Decimal("2.0"))
        self._validate_numeric_range("num_std", std, min_val=1.0, max_val=4.0)

        sl_atr = params.get("stop_loss_atr", Decimal("1.5"))
        self._validate_numeric_range("stop_loss_atr", sl_atr, min_val=0.5, max_val=5.0)

        tp_atr = params.get("target_atr", Decimal("3.0"))
        self._validate_numeric_range("target_atr", tp_atr, min_val=1.0, max_val=10.0)

        return {
            "period": int(period),
            "num_std": Decimal(str(std)),
            "stop_loss_atr": Decimal(str(sl_atr)),
            "target_atr": Decimal(str(tp_atr)),
        }

    def _calculate_atr(self, bars: List[MarketBar], period: int = 14) -> Optional[Decimal]:
        if len(bars) < period + 1:
            return None
        tr_list = []
        for i in range(1, len(bars)):
            c, p = bars[i], bars[i - 1]
            tr = max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
            tr_list.append(tr)
        recent = tr_list[-period:]
        return sum(recent) / Decimal(str(len(recent)))

    def evaluate(
        self,
        current_bar: MarketBar,
        history: List[MarketBar],
        indicators: Optional[Dict[str, Any]] = None,
    ) -> Optional[StrategySignal]:
        if not history or len(history) < self.period:
            return None

        # Calculate Bollinger Bands strictly from prior closed bars
        prior_closes = [b.close for b in history[-self.period:]]
        sma = sum(prior_closes) / Decimal(str(self.period))
        variance = sum((c - sma) ** Decimal("2.0") for c in prior_closes) / Decimal(str(self.period))
        std = Decimal(str(math.sqrt(float(variance)))) if variance > Decimal("0.0") else Decimal("0.0")

        lower_bb = sma - (self.num_std * std)
        upper_bb = sma + (self.num_std * std)

        # Oversold Mean Reversion condition
        if current_bar.close >= lower_bb:
            return None

        all_bars = history + [current_bar]
        atr = self._calculate_atr(all_bars) or (current_bar.close * Decimal("0.015"))

        raw_entry = current_bar.close
        raw_target = sma  # Mean reversion to middle band
        raw_stop = raw_entry - (atr * self.stop_loss_atr)

        if raw_target <= raw_entry:
            return None

        entry_p = quantize_order_price(
            raw_entry, current_bar.symbol, side_is_buy=True, order_purpose="ENTRY",
            trade_date=current_bar.timestamp_iso, reference_price=current_bar.close,
        )
        stop_p = quantize_order_price(
            raw_stop, current_bar.symbol, side_is_buy=True, order_purpose="STOP_LOSS",
            trade_date=current_bar.timestamp_iso, reference_price=current_bar.close,
        )
        target_p = quantize_order_price(
            raw_target, current_bar.symbol, side_is_buy=True, order_purpose="TAKE_PROFIT",
            trade_date=current_bar.timestamp_iso, reference_price=current_bar.close,
        )

        return StrategySignal(
            signal_id=f"SIG-BB-{uuid.uuid4().hex[:8].upper()}",
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            symbol=current_bar.symbol,
            side=OrderSide.BUY,
            target_entry_price=entry_p,
            stop_loss=stop_p,
            take_profit=target_p,
            confidence_score=Decimal("0.75"),
            timestamp_iso=current_bar.timestamp_iso,
            order_type="LIMIT",
            reason=f"Bollinger oversold: close {current_bar.close} < lower band {lower_bb:.2f}",
            evidence={
                "sma": str(sma.quantize(Decimal("0.01"))),
                "lower_bb": str(lower_bb.quantize(Decimal("0.01"))),
                "std": str(std.quantize(Decimal("0.01"))),
                "atr": str(atr.quantize(Decimal("0.01"))),
            },
            data_provenance={"period": self.period, "num_std": str(self.num_std)},
        )
