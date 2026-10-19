"""Canonical Adapters for OMS Strategy Blueprints (Project STRAT-FACTORY).

Integrates the three high-speed OMS blueprint strategies into the unified platform:
1. STRAT_OMS_MEAN_REV: Bollinger / SMA mean-reversion with volatility envelope.
2. STRAT_OMS_TREND_FOLLOWING: Short/Long moving average crossover with trailing stop loss.
3. STRAT_OMS_HYBRID_ALPHA: Regime-switching strategy choosing between mean-reversion
   and trend-following based on realized volatility.

Guarantees:
- Emits canonical StrategySignal (routes through BrokerGateway, never directly dispatching orders).
- Strict parameter bounds and fail-closed validation.
- Directional statutory tick quantization on all order geometries.
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


class OMSMeanReversionStrategy(BaseStrategy):
    """OMS Blueprint Mean Reversion Strategy."""

    STRATEGY_ID = "STRAT_OMS_MEAN_REV"

    def __init__(
        self,
        strategy_id: str = "STRAT_OMS_MEAN_REV",
        version: str = "2.0.0",
        parameters: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(strategy_id=strategy_id, version=version, parameters=parameters)
        self.window: int = self.parameters["window"]
        self.threshold: Decimal = self.parameters["threshold"]
        self.stop_loss_pct: Decimal = self.parameters["stop_loss_pct"]

    def validate_and_sanitize_parameters(self, params: Dict[str, Any]) -> Dict[str, Any]:
        window = params.get("window", 20)
        self._validate_numeric_range("window", window, min_val=2, max_val=200, is_integer=True)

        thresh = params.get("threshold", Decimal("1.5"))
        self._validate_numeric_range("threshold", thresh, min_val=0.1, max_val=5.0)

        sl = params.get("stop_loss_pct", Decimal("0.02"))
        self._validate_numeric_range("stop_loss_pct", sl, min_val=0.005, max_val=0.20)

        return {
            "window": int(window),
            "threshold": Decimal(str(thresh)),
            "stop_loss_pct": Decimal(str(sl)),
        }

    def evaluate(
        self,
        current_bar: MarketBar,
        history: List[MarketBar],
        indicators: Optional[Dict[str, Any]] = None,
    ) -> Optional[StrategySignal]:
        bars = (history or []) + [current_bar]
        if len(bars) < self.window:
            return None

        closes = [b.close for b in bars[-self.window:]]
        sma = sum(closes) / Decimal(str(self.window))
        variance = sum((c - sma) ** Decimal("2.0") for c in closes) / Decimal(str(self.window))
        std = Decimal(str(math.sqrt(float(variance)))) if variance > Decimal("0.0") else Decimal("0.0")

        lower = sma - (self.threshold * std)
        upper = sma + (self.threshold * std)

        if current_bar.close <= lower:
            side = OrderSide.BUY
            raw_entry = current_bar.close
            raw_stop = raw_entry * (Decimal("1.0") - self.stop_loss_pct)
            raw_target = sma
        elif current_bar.close >= upper:
            side = OrderSide.SELL
            raw_entry = current_bar.close
            raw_stop = raw_entry * (Decimal("1.0") + self.stop_loss_pct)
            raw_target = sma
        else:
            return None

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

        return StrategySignal(
            signal_id=f"SIG-OMSM-{uuid.uuid4().hex[:8].upper()}",
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
            reason=f"OMS Mean Reversion: close {current_bar.close} breached envelope [{lower:.2f}, {upper:.2f}]",
            evidence={"sma": str(sma.quantize(Decimal("0.01"))), "std": str(std.quantize(Decimal("0.01")))},
            data_provenance={"blueprint": "oms_mean_reversion"},
        )


class OMSTrendFollowingStrategy(BaseStrategy):
    """OMS Blueprint Trend Following Strategy."""

    STRATEGY_ID = "STRAT_OMS_TREND_FOLLOWING"

    def __init__(
        self,
        strategy_id: str = "STRAT_OMS_TREND_FOLLOWING",
        version: str = "2.0.0",
        parameters: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(strategy_id=strategy_id, version=version, parameters=parameters)
        self.short_window: int = self.parameters["short_window"]
        self.long_window: int = self.parameters["long_window"]
        self.stop_loss_pct: Decimal = self.parameters["stop_loss_pct"]

    def validate_and_sanitize_parameters(self, params: Dict[str, Any]) -> Dict[str, Any]:
        short_w = params.get("short_window", 5)
        self._validate_numeric_range("short_window", short_w, min_val=2, max_val=50, is_integer=True)

        long_w = params.get("long_window", 20)
        self._validate_numeric_range("long_window", long_w, min_val=10, max_val=200, is_integer=True)

        if int(short_w) >= int(long_w):
            raise InvalidStrategyParameterError(f"short_window ({short_w}) must be < long_window ({long_w})")

        sl = params.get("stop_loss_pct", Decimal("0.02"))
        self._validate_numeric_range("stop_loss_pct", sl, min_val=0.005, max_val=0.20)

        return {
            "short_window": int(short_w),
            "long_window": int(long_w),
            "stop_loss_pct": Decimal(str(sl)),
        }

    def evaluate(
        self,
        current_bar: MarketBar,
        history: List[MarketBar],
        indicators: Optional[Dict[str, Any]] = None,
    ) -> Optional[StrategySignal]:
        bars = (history or []) + [current_bar]
        if len(bars) < self.long_window + 1:
            return None

        closes = [b.close for b in bars]
        fast_c = sum(closes[-self.short_window:]) / Decimal(str(self.short_window))
        fast_p = sum(closes[-self.short_window - 1:-1]) / Decimal(str(self.short_window))
        slow_c = sum(closes[-self.long_window:]) / Decimal(str(self.long_window))
        slow_p = sum(closes[-self.long_window - 1:-1]) / Decimal(str(self.long_window))

        # Crossover
        if not (fast_c > slow_c and fast_p <= slow_p):
            return None

        raw_entry = current_bar.close
        raw_stop = raw_entry * (Decimal("1.0") - self.stop_loss_pct)
        risk = raw_entry - raw_stop
        raw_target = raw_entry + (risk * Decimal("2.0"))

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
            signal_id=f"SIG-OMST-{uuid.uuid4().hex[:8].upper()}",
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
            reason=f"OMS Trend Crossover: SMA({self.short_window}) {fast_c:.2f} > SMA({self.long_window}) {slow_c:.2f}",
            evidence={"fast": str(fast_c.quantize(Decimal("0.01"))), "slow": str(slow_c.quantize(Decimal("0.01")))},
            data_provenance={"blueprint": "oms_trend_following"},
        )


class OMSHybridAlphaStrategy(BaseStrategy):
    """OMS Blueprint Regime-Switching Hybrid Alpha Strategy."""

    STRATEGY_ID = "STRAT_OMS_HYBRID_ALPHA"

    def __init__(
        self,
        strategy_id: str = "STRAT_OMS_HYBRID_ALPHA",
        version: str = "2.0.0",
        parameters: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(strategy_id=strategy_id, version=version, parameters=parameters)
        self.volatility_window: int = self.parameters["volatility_window"]
        self.volatility_threshold: Decimal = self.parameters["volatility_threshold"]

        # Delegate sub-strategies
        self.mean_rev = OMSMeanReversionStrategy(
            strategy_id=f"{strategy_id}_SUB_MR",
            parameters={
                "window": self.parameters["window"],
                "threshold": self.parameters["threshold"],
                "stop_loss_pct": self.parameters["stop_loss_pct"],
            },
        )
        self.trend_following = OMSTrendFollowingStrategy(
            strategy_id=f"{strategy_id}_SUB_TF",
            parameters={
                "short_window": self.parameters["short_window"],
                "long_window": self.parameters["long_window"],
                "stop_loss_pct": self.parameters["stop_loss_pct"],
            },
        )

    def validate_and_sanitize_parameters(self, params: Dict[str, Any]) -> Dict[str, Any]:
        vol_w = params.get("volatility_window", 10)
        self._validate_numeric_range("volatility_window", vol_w, min_val=5, max_val=50, is_integer=True)

        vol_t = params.get("volatility_threshold", Decimal("0.01"))
        self._validate_numeric_range("volatility_threshold", vol_t, min_val=0.001, max_val=0.05)

        w = params.get("window", 20)
        self._validate_numeric_range("window", w, min_val=2, max_val=100, is_integer=True)

        sw = params.get("short_window", 5)
        self._validate_numeric_range("short_window", sw, min_val=2, max_val=50, is_integer=True)

        lw = params.get("long_window", 20)
        self._validate_numeric_range("long_window", lw, min_val=10, max_val=100, is_integer=True)

        thresh = params.get("threshold", Decimal("1.5"))
        self._validate_numeric_range("threshold", thresh, min_val=0.1, max_val=5.0)

        sl = params.get("stop_loss_pct", Decimal("0.02"))
        self._validate_numeric_range("stop_loss_pct", sl, min_val=0.005, max_val=0.20)

        return {
            "volatility_window": int(vol_w),
            "volatility_threshold": Decimal(str(vol_t)),
            "window": int(w),
            "short_window": int(sw),
            "long_window": int(lw),
            "threshold": Decimal(str(thresh)),
            "stop_loss_pct": Decimal(str(sl)),
        }

    def evaluate(
        self,
        current_bar: MarketBar,
        history: List[MarketBar],
        indicators: Optional[Dict[str, Any]] = None,
    ) -> Optional[StrategySignal]:
        bars = (history or []) + [current_bar]
        if len(bars) < self.volatility_window + 1:
            return None

        # Calculate realized return volatility
        returns = []
        for i in range(1, len(bars[-self.volatility_window - 1:])):
            r = (bars[i].close - bars[i - 1].close) / bars[i - 1].close
            returns.append(r)
        mean_r = sum(returns) / Decimal(str(len(returns)))
        variance = sum((r - mean_r) ** Decimal("2.0") for r in returns) / Decimal(str(len(returns)))
        realized_vol = Decimal(str(math.sqrt(float(variance)))) if variance > Decimal("0.0") else Decimal("0.0")

        # Regime Selection: Low Volatility -> Mean Reversion, High Volatility -> Trend Following
        if realized_vol < self.volatility_threshold:
            signal = self.mean_rev.evaluate(current_bar, history, indicators)
            regime = "MEAN_REVERSION"
        else:
            signal = self.trend_following.evaluate(current_bar, history, indicators)
            regime = "TREND_FOLLOWING"

        if signal:
            signal.reason = f"OMS Hybrid Alpha ({regime}): {signal.reason}"
            signal.evidence["realized_vol"] = str(realized_vol.quantize(Decimal("0.0001")))
            signal.evidence["regime"] = regime
        return signal
