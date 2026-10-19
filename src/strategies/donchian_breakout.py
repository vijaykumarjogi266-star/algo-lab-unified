"""Canonical Donchian Channel Breakout Strategy.

Models breakout beyond the highest high of the past N bars (strictly excluding current bar).
Distinguishes intraday (5m) and swing (daily) variants via explicit strategy IDs and configuration.

Repairs and Invariants:
1. Strict Point-in-Time: Breakout boundary uses prior closed bars only (bars[:-1]); excludes current bar.
2. Dynamic Volatility Stops: Stop loss and targets calibrated via calculated ATR.
3. Statutory Tick Quantization: Directional quantization applied to order price, stop, and target.
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


class DonchianBreakoutStrategy(BaseStrategy):
    """Canonical Donchian Channel Breakout Strategy."""

    STRATEGY_ID = "STRAT_DONCHIAN_BREAKOUT"

    def __init__(
        self,
        strategy_id: str = "STRAT_DONCHIAN_BREAKOUT",
        version: str = "2.0.0",
        parameters: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(strategy_id=strategy_id, version=version, parameters=parameters)
        self.period: int = self.parameters["period"]
        self.stop_loss_atr: Decimal = self.parameters["stop_loss_atr"]
        self.target_atr: Decimal = self.parameters["target_atr"]
        self.variant: str = self.parameters["variant"]

    def validate_and_sanitize_parameters(self, params: Dict[str, Any]) -> Dict[str, Any]:
        period = params.get("lookback_period", params.get("period", 20))
        self._validate_numeric_range("period", period, min_val=2, max_val=100, is_integer=True)

        sl_atr = params.get("stop_loss_atr", Decimal("1.5"))
        self._validate_numeric_range("stop_loss_atr", sl_atr, min_val=0.5, max_val=5.0)

        tp_atr = params.get("target_atr", Decimal("3.0"))
        self._validate_numeric_range("target_atr", tp_atr, min_val=1.0, max_val=10.0)

        variant = params.get("variant", "INTRADAY")
        if variant not in ("INTRADAY", "SWING"):
            raise InvalidStrategyParameterError(f"variant must be 'INTRADAY' or 'SWING', got {variant!r}")

        return {
            "period": int(period),
            "stop_loss_atr": Decimal(str(sl_atr)),
            "target_atr": Decimal(str(tp_atr)),
            "variant": variant,
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
            return None  # Insufficient history for lagged Donchian channel

        # Strictly point-in-time: reference high is maximum of preceding N closed bars
        preceding_bars = history[-self.period:]
        channel_high = max(b.high for b in preceding_bars)

        # Breakout condition: current bar close penetrates strictly above channel high
        if current_bar.close <= channel_high:
            return None

        all_bars = history + [current_bar]
        atr = self._calculate_atr(all_bars) or (current_bar.close * Decimal("0.015"))

        raw_entry = current_bar.close
        raw_stop = raw_entry - (atr * self.stop_loss_atr)
        raw_target = raw_entry + (atr * self.target_atr)

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
            signal_id=f"SIG-DONCH-{uuid.uuid4().hex[:8].upper()}",
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            symbol=current_bar.symbol,
            side=OrderSide.BUY,
            target_entry_price=entry_p,
            stop_loss=stop_p,
            take_profit=target_p,
            confidence_score=Decimal("0.80"),
            timestamp_iso=current_bar.timestamp_iso,
            order_type="LIMIT",
            reason=f"Donchian breakout: close {current_bar.close} > {self.period}-bar high {channel_high}",
            evidence={
                "period": self.period,
                "channel_high": str(channel_high),
                "variant": self.variant,
                "atr": str(atr.quantize(Decimal("0.01"))),
            },
            data_provenance={"history_len": len(history)},
        )
