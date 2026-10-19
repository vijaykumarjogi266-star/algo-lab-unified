"""Canonical EMA 9/21 Pullback Swing Strategy.

Extracted from the 5-year verified swing trading research suite in Algo Lab.
Trades pullbacks to the 9/21 EMA zone in the direction of the primary uptrend.

Rules:
1. Primary Trend Filter: Price above trend SMA (e.g. 50 or 200 SMA) and EMA 9 > EMA 21.
2. Pullback Confirmation: Bar low dips into the 9/21 EMA zone (low <= 9 EMA * 1.005)
   and close bounces above 9 EMA with a bullish candle body (Close > Open).
3. Risk Protection: Dynamic Chandelier/ATR stop loss with statutory tick quantization.
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


class EMAPullbackStrategy(BaseStrategy):
    """Canonical 9/21 EMA Pullback Swing Strategy."""

    STRATEGY_ID = "STRAT_EMA_PULLBACK"

    def __init__(
        self,
        strategy_id: str = "STRAT_EMA_PULLBACK",
        version: str = "2.0.0",
        parameters: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(strategy_id=strategy_id, version=version, parameters=parameters)
        self.ema_fast: int = self.parameters["ema_fast"]
        self.ema_slow: int = self.parameters["ema_slow"]
        self.sma_trend: int = self.parameters["sma_trend"]
        self.stop_loss_atr: Decimal = self.parameters["stop_loss_atr"]
        self.target_reward_ratio: Decimal = self.parameters["target_reward_ratio"]

    def validate_and_sanitize_parameters(self, params: Dict[str, Any]) -> Dict[str, Any]:
        fast = params.get("ema_fast", 9)
        self._validate_numeric_range("ema_fast", fast, min_val=2, max_val=50, is_integer=True)

        slow = params.get("ema_slow", 21)
        self._validate_numeric_range("ema_slow", slow, min_val=5, max_val=100, is_integer=True)

        if int(fast) >= int(slow):
            raise InvalidStrategyParameterError(f"ema_fast ({fast}) must be strictly less than ema_slow ({slow})")

        trend = params.get("sma_trend", 50)
        self._validate_numeric_range("sma_trend", trend, min_val=10, max_val=300, is_integer=True)

        sl_atr = params.get("stop_loss_atr", Decimal("2.0"))
        self._validate_numeric_range("stop_loss_atr", sl_atr, min_val=0.5, max_val=5.0)

        rr = params.get("target_reward_ratio", Decimal("3.0"))
        self._validate_numeric_range("target_reward_ratio", rr, min_val=1.0, max_val=6.0)

        return {
            "ema_fast": int(fast),
            "ema_slow": int(slow),
            "sma_trend": int(trend),
            "stop_loss_atr": Decimal(str(sl_atr)),
            "target_reward_ratio": Decimal(str(rr)),
        }

    def _calculate_ema(self, series: List[Decimal], period: int) -> Decimal:
        """Calculates point-in-time Exponential Moving Average."""
        k = Decimal("2.0") / Decimal(str(period + 1))
        ema = series[0]
        for val in series[1:]:
            ema = (val * k) + (ema * (Decimal("1.0") - k))
        return ema

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
        min_bars = max(self.sma_trend, self.ema_slow) + 5
        if len(bars) < min_bars:
            return None

        closes = [b.close for b in bars]
        # Trend SMA
        sma_val = sum(closes[-self.sma_trend:]) / Decimal(str(self.sma_trend))
        # EMAs
        ema_f = self._calculate_ema(closes[-self.ema_fast * 3:], self.ema_fast)
        ema_s = self._calculate_ema(closes[-self.ema_slow * 3:], self.ema_slow)

        # 1. Primary Uptrend Filter
        in_uptrend = (current_bar.close > sma_val) and (ema_f > ema_s)
        if not in_uptrend:
            return None

        # 2. Pullback and Bounce Confirmation
        bounced = (
            (current_bar.low <= ema_f * Decimal("1.005"))
            and (current_bar.close > ema_f)
            and (current_bar.close > current_bar.open)
        )
        if not bounced:
            return None

        atr = self._calculate_atr(bars) or (current_bar.close * Decimal("0.015"))
        raw_entry = current_bar.close
        stop_dist = max(atr * self.stop_loss_atr, raw_entry * Decimal("0.02"))
        raw_stop = min(ema_s * Decimal("0.99"), raw_entry - stop_dist)
        actual_risk = raw_entry - raw_stop
        if actual_risk <= Decimal("0.0"):
            return None
        raw_target = raw_entry + (actual_risk * self.target_reward_ratio)

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
            signal_id=f"SIG-EMAPB-{uuid.uuid4().hex[:8].upper()}",
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
            reason=f"EMA Pullback: bounced off EMA {self.ema_fast} ({ema_f:.2f}) in uptrend above SMA {self.sma_trend}",
            evidence={
                "ema_fast": str(ema_f.quantize(Decimal("0.01"))),
                "ema_slow": str(ema_s.quantize(Decimal("0.01"))),
                "sma_trend": str(sma_val.quantize(Decimal("0.01"))),
                "atr": str(atr.quantize(Decimal("0.01"))),
            },
            data_provenance={"trend_period": self.sma_trend},
        )
