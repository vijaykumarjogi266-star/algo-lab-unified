"""Canonical Dual SMA Crossover Benchmark Strategy.

Trades crossover between fast and slow moving averages:
- Golden Cross: Fast SMA crosses strictly above Slow SMA -> BUY signal.
- Fail-Closed: Validates fast_period < slow_period; eliminates look-ahead.
- Point-in-Time: Crossover evaluated across strictly closed consecutive intervals.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional
import uuid

from src.brokergateway.states import OrderSide
from src.core.exceptions import InvalidStrategyParameterError
from src.core.tick_precision import quantize_order_price
from src.scanner.scanner import MarketBar
from src.strategies.base import BaseStrategy
from src.strategies.models import StrategySignal


class SMACrossoverStrategy(BaseStrategy):
    """Canonical Dual Simple Moving Average Crossover Strategy."""

    STRATEGY_ID = "STRAT_SMA_CROSS"

    def __init__(
        self,
        strategy_id: str = "STRAT_SMA_CROSS",
        version: str = "2.0.0",
        parameters: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(strategy_id=strategy_id, version=version, parameters=parameters)
        self.fast_period: int = self.parameters["fast_period"]
        self.slow_period: int = self.parameters["slow_period"]
        self.stop_loss_atr_mult: Decimal = self.parameters["stop_loss_atr_mult"]
        self.target_atr_mult: Decimal = self.parameters["target_atr_mult"]

    def validate_and_sanitize_parameters(self, params: Dict[str, Any]) -> Dict[str, Any]:
        fast = params.get("fast_period", 9)
        self._validate_numeric_range("fast_period", fast, min_val=2, max_val=100, is_integer=True)

        slow = params.get("slow_period", 21)
        self._validate_numeric_range("slow_period", slow, min_val=3, max_val=300, is_integer=True)

        if int(fast) >= int(slow):
            raise InvalidStrategyParameterError(
                f"fast_period ({fast}) must be strictly less than slow_period ({slow})"
            )

        sl_atr = params.get("stop_loss_atr_mult", Decimal("1.5"))
        self._validate_numeric_range("stop_loss_atr_mult", sl_atr, min_val=0.5, max_val=5.0)

        tp_atr = params.get("target_atr_mult", Decimal("3.0"))
        self._validate_numeric_range("target_atr_mult", tp_atr, min_val=1.0, max_val=10.0)

        return {
            "fast_period": int(fast),
            "slow_period": int(slow),
            "stop_loss_atr_mult": Decimal(str(sl_atr)),
            "target_atr_mult": Decimal(str(tp_atr)),
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
        bars = (history or []) + [current_bar]
        if len(bars) < self.slow_period + 1:
            return None

        closes = [b.close for b in bars]
        fast_curr = sum(closes[-self.fast_period:]) / Decimal(str(self.fast_period))
        fast_prev = sum(closes[-self.fast_period - 1:-1]) / Decimal(str(self.fast_period))
        slow_curr = sum(closes[-self.slow_period:]) / Decimal(str(self.slow_period))
        slow_prev = sum(closes[-self.slow_period - 1:-1]) / Decimal(str(self.slow_period))

        golden_cross = (fast_curr > slow_curr) and (fast_prev <= slow_prev)
        death_cross = (fast_curr < slow_curr) and (fast_prev >= slow_prev)
        if not golden_cross and not death_cross:
            return None

        atr = self._calculate_atr(bars) or (current_bar.close * Decimal("0.015"))
        side = OrderSide.BUY if golden_cross else OrderSide.SELL
        raw_entry = current_bar.close
        if side == OrderSide.BUY:
            raw_stop = raw_entry - (atr * self.stop_loss_atr_mult)
            raw_target = raw_entry + (atr * self.target_atr_mult)
        else:
            raw_stop = raw_entry + (atr * self.stop_loss_atr_mult)
            raw_target = raw_entry - (atr * self.target_atr_mult)

        entry_p = quantize_order_price(
            raw_entry, current_bar.symbol, side_is_buy=(side == OrderSide.BUY), order_purpose="ENTRY",
            trade_date=current_bar.timestamp_iso, reference_price=current_bar.close,
        )
        stop_p = quantize_order_price(
            raw_stop, current_bar.symbol, side_is_buy=(side == OrderSide.BUY), order_purpose="STOP_LOSS",
            trade_date=current_bar.timestamp_iso, reference_price=current_bar.close,
        )
        target_p = quantize_order_price(
            raw_target, current_bar.symbol, side_is_buy=(side == OrderSide.BUY), order_purpose="TAKE_PROFIT",
            trade_date=current_bar.timestamp_iso, reference_price=current_bar.close,
        )

        reason = (
            f"Golden Cross: SMA({self.fast_period}) {fast_curr:.2f} > SMA({self.slow_period}) {slow_curr:.2f}"
            if golden_cross else
            f"Death Cross: SMA({self.fast_period}) {fast_curr:.2f} < SMA({self.slow_period}) {slow_curr:.2f}"
        )

        return StrategySignal(
            signal_id=f"SIG-SMA-{uuid.uuid4().hex[:8].upper()}",
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            symbol=current_bar.symbol,
            side=side,
            target_entry_price=entry_p,
            stop_loss=stop_p,
            take_profit=target_p,
            confidence_score=Decimal("0.75"),
            timestamp_iso=current_bar.timestamp_iso,
            order_type="LIMIT",
            reason=reason,
            evidence={
                "fast_curr": str(fast_curr.quantize(Decimal("0.01"))),
                "slow_curr": str(slow_curr.quantize(Decimal("0.01"))),
                "fast_prev": str(fast_prev.quantize(Decimal("0.01"))),
                "slow_prev": str(slow_prev.quantize(Decimal("0.01"))),
            },
            data_provenance={"warmup_bars": len(bars)},
        )
