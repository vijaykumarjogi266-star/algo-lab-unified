"""Canonical TTM Volatility Squeeze Breakout Strategy.

Identifies volatility compression when Bollinger Bands contract inside Keltner Channels.
Fires a directional trade when bands expand outside channels with volume confirmation.

Repairs and Invariants:
1. Parameter Overrides Bound: Parameters (bb_std, kc_atr_mult, volume_multiplier) directly
   govern calculations, eliminating hardcoded or unlinked attributes.
2. Two-Bar Transition Logic: Confirms transition from squeeze (t-1) to breakout (t).
3. Point-in-Time: Excludes future bars; rolling metrics derived from strictly available history.
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


class TTMSqueezeBreakoutStrategy(BaseStrategy):
    """Canonical TTM Volatility Squeeze Breakout Strategy."""

    STRATEGY_ID = "STRAT_TTM_SQUEEZE"

    def __init__(
        self,
        strategy_id: str = "STRAT_TTM_SQUEEZE",
        version: str = "2.0.0",
        parameters: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(strategy_id=strategy_id, version=version, parameters=parameters)
        self.bb_period: int = self.parameters["bb_period"]
        self.bb_std: Decimal = self.parameters["bb_std"]
        self.kc_atr_mult: Decimal = self.parameters["kc_atr_mult"]
        self.volume_multiplier: Decimal = self.parameters["volume_multiplier"]
        self.stop_loss_atr: Decimal = self.parameters["stop_loss_atr"]
        self.target_atr: Decimal = self.parameters["target_atr"]

    def validate_and_sanitize_parameters(self, params: Dict[str, Any]) -> Dict[str, Any]:
        bb_p = params.get("bb_period", 20)
        self._validate_numeric_range("bb_period", bb_p, min_val=5, max_val=100, is_integer=True)

        bb_std = params.get("bb_std", Decimal("2.0"))
        self._validate_numeric_range("bb_std", bb_std, min_val=1.0, max_val=4.0)

        kc_mult = params.get("kc_atr_mult", Decimal("1.5"))
        self._validate_numeric_range("kc_atr_mult", kc_mult, min_val=0.5, max_val=4.0)

        vol_mult = params.get("volume_multiplier", Decimal("1.15"))
        self._validate_numeric_range("volume_multiplier", vol_mult, min_val=1.0, max_val=5.0)

        sl_atr = params.get("stop_loss_atr", Decimal("1.5"))
        self._validate_numeric_range("stop_loss_atr", sl_atr, min_val=0.5, max_val=5.0)

        tp_atr = params.get("target_atr", Decimal("3.0"))
        self._validate_numeric_range("target_atr", tp_atr, min_val=1.0, max_val=10.0)

        return {
            "bb_period": int(bb_p),
            "bb_std": Decimal(str(bb_std)),
            "kc_atr_mult": Decimal(str(kc_mult)),
            "volume_multiplier": Decimal(str(vol_mult)),
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
        all_bars = (history or []) + [current_bar]
        if len(all_bars) < self.bb_period + 5:
            return None

        atr = self._calculate_atr(all_bars)
        if atr is None or atr <= Decimal("0.0"):
            return None

        closes = [b.close for b in all_bars]
        volumes = [b.volume for b in all_bars]

        # 1. Current Bar BB and KC
        curr_closes = closes[-self.bb_period:]
        curr_sma = sum(curr_closes) / Decimal(str(self.bb_period))
        curr_var = sum((c - curr_sma) ** Decimal("2.0") for c in curr_closes) / Decimal(str(self.bb_period))
        curr_std = Decimal(str(math.sqrt(float(curr_var)))) if curr_var > Decimal("0.0") else Decimal("0.0")

        curr_bb_upper = curr_sma + (self.bb_std * curr_std)
        curr_bb_lower = curr_sma - (self.bb_std * curr_std)
        curr_kc_upper = curr_sma + (self.kc_atr_mult * atr)
        curr_kc_lower = curr_sma - (self.kc_atr_mult * atr)

        # 2. Previous Bar BB and KC (for transition check)
        prev_closes = closes[-self.bb_period - 1:-1]
        prev_sma = sum(prev_closes) / Decimal(str(self.bb_period))
        prev_var = sum((c - prev_sma) ** Decimal("2.0") for c in prev_closes) / Decimal(str(self.bb_period))
        prev_std = Decimal(str(math.sqrt(float(prev_var)))) if prev_var > Decimal("0.0") else Decimal("0.0")

        prev_bb_upper = prev_sma + (self.bb_std * prev_std)
        prev_bb_lower = prev_sma - (self.bb_std * prev_std)
        prev_kc_upper = prev_sma + (self.kc_atr_mult * atr)
        prev_kc_lower = prev_sma - (self.kc_atr_mult * atr)

        # 3. Squeeze Transition Detection
        # Was in squeeze at t-1: Bollinger Bands inside Keltner Channels
        was_in_squeeze = (prev_bb_lower > prev_kc_lower) and (prev_bb_upper < prev_kc_upper)
        # Fired breakout at t: Bollinger Bands expanded outside Keltner Channels
        fired_breakout = (curr_bb_lower <= curr_kc_lower) or (curr_bb_upper >= curr_kc_upper)

        if not (was_in_squeeze and fired_breakout):
            return None

        # 4. Volume Surge Confirmation
        recent_vols = [Decimal(str(v)) for v in volumes[-self.bb_period:] if v > 0]
        avg_vol = sum(recent_vols) / Decimal(str(len(recent_vols))) if recent_vols else Decimal("1.0")
        vol_surge = Decimal(str(current_bar.volume)) >= (avg_vol * self.volume_multiplier)
        if not vol_surge:
            return None

        # 5. Directional Momentum
        mom = current_bar.close - curr_sma
        if mom == Decimal("0.0"):
            return None

        is_bullish = mom > Decimal("0.0") and current_bar.close > curr_sma
        is_bearish = mom < Decimal("0.0") and current_bar.close < curr_sma

        if is_bullish:
            side = OrderSide.BUY
            raw_entry = current_bar.close
            raw_stop = raw_entry - (atr * self.stop_loss_atr)
            raw_target = raw_entry + (atr * self.target_atr)
        elif is_bearish:
            side = OrderSide.SELL
            raw_entry = current_bar.close
            raw_stop = raw_entry + (atr * self.stop_loss_atr)
            raw_target = raw_entry - (atr * self.target_atr)
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
            signal_id=f"SIG-TTM-{uuid.uuid4().hex[:8].upper()}",
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            symbol=current_bar.symbol,
            side=side,
            target_entry_price=entry_p,
            stop_loss=stop_p,
            take_profit=target_p,
            confidence_score=Decimal("0.80"),
            timestamp_iso=current_bar.timestamp_iso,
            order_type="LIMIT",
            reason=f"TTM Squeeze Fired ({'Bullish' if is_bullish else 'Bearish'}): BB expanded outside KC with volume surge",
            evidence={
                "momentum": str(mom.quantize(Decimal("0.01"))),
                "bb_upper": str(curr_bb_upper.quantize(Decimal("0.01"))),
                "kc_upper": str(curr_kc_upper.quantize(Decimal("0.01"))),
                "atr": str(atr.quantize(Decimal("0.01"))),
            },
            data_provenance={"bb_std": str(self.bb_std), "kc_atr_mult": str(self.kc_atr_mult)},
        )
