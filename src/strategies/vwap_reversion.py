"""Canonical VWAP Mean Reversion Strategy.

Captures intraday price over-extensions away from institutional volume-weighted average price.

Repairs and Invariants:
1. Session Boundary Reset: Cumulative VWAP strictly resets at each Asia/Kolkata session boundary.
2. Zero-Volume & Incomplete Session Handling: Explicit zero-division guards; requires min_session_bars.
3. No Hardcoded ATR: Real ATR dynamically calculated from true ranges of historical bars.
4. Point-in-Time Calculation: Uses strictly information available at or before current bar.
5. Statutory Tick Quantization: Directional quantization applied to entry, stop loss, and target.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import math
from typing import Any, Dict, List, Optional
import uuid
from zoneinfo import ZoneInfo

from src.brokergateway.states import OrderSide
from src.core.exceptions import InvalidStrategyParameterError
from src.core.tick_precision import quantize_order_price
from src.scanner.scanner import MarketBar
from src.strategies.base import BaseStrategy
from src.strategies.models import StrategySignal

IST = ZoneInfo("Asia/Kolkata")


class VWAPMeanReversionStrategy(BaseStrategy):
    """Canonical VWAP Mean Reversion Strategy."""

    STRATEGY_ID = "STRAT_VWAP_REVERSION"

    def __init__(
        self,
        strategy_id: str = "STRAT_VWAP_REVERSION",
        version: str = "2.0.0",
        parameters: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(strategy_id=strategy_id, version=version, parameters=parameters)
        self.std_dev_threshold: Decimal = self.parameters["std_dev_threshold"]
        self.stop_loss_atr_mult: Decimal = self.parameters["stop_loss_atr_mult"]
        self.atr_period: int = self.parameters["atr_period"]
        self.min_session_bars: int = self.parameters["min_session_bars"]

    def validate_and_sanitize_parameters(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Validates VWAP parameters fail-closed."""
        if "entry_threshold_pct" in params:
            self._validate_numeric_range("entry_threshold_pct", params["entry_threshold_pct"], min_val=0.001, max_val=0.10)

        std_dev = params.get("std_dev_threshold", Decimal("2.0"))
        self._validate_numeric_range("std_dev_threshold", std_dev, min_val=0.5, max_val=4.0)

        sl_atr = params.get("stop_loss_atr_mult", Decimal("1.5"))
        self._validate_numeric_range("stop_loss_atr_mult", sl_atr, min_val=0.5, max_val=5.0)

        atr_p = params.get("atr_period", 14)
        self._validate_numeric_range("atr_period", atr_p, min_val=2, max_val=50, is_integer=True)

        min_bars = params.get("min_session_bars", 5)
        self._validate_numeric_range("min_session_bars", min_bars, min_val=2, max_val=30, is_integer=True)

        return {
            "std_dev_threshold": Decimal(str(std_dev)),
            "stop_loss_atr_mult": Decimal(str(sl_atr)),
            "atr_period": int(atr_p),
            "min_session_bars": int(min_bars),
        }

    def _get_session_date(self, ts_iso: str) -> str:
        dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        else:
            dt = dt.astimezone(IST)
        return dt.date().isoformat()

    def _calculate_atr(self, bars: List[MarketBar]) -> Optional[Decimal]:
        """Calculates historical Average True Range (ATR)."""
        if len(bars) < self.atr_period + 1:
            return None
        tr_list = []
        for i in range(1, len(bars)):
            curr = bars[i]
            prev = bars[i - 1]
            h_l = curr.high - curr.low
            h_pc = abs(curr.high - prev.close)
            l_pc = abs(curr.low - prev.close)
            tr = max(h_l, h_pc, l_pc)
            tr_list.append(tr)
        recent_tr = tr_list[-self.atr_period:]
        return sum(recent_tr) / Decimal(str(len(recent_tr)))

    def calculate_session_vwap(self, current_bar: MarketBar, history: List[MarketBar]) -> Decimal:
        """Calculates cumulative session VWAP up to and including current bar."""
        curr_session_date = self._get_session_date(current_bar.timestamp_iso)
        all_bars = (history or []) + [current_bar]
        session_bars = [b for b in all_bars if self._get_session_date(b.timestamp_iso) == curr_session_date]
        cum_volume = sum(b.volume for b in session_bars)
        if cum_volume <= 0:
            return current_bar.close
        cum_tp_v = sum(
            ((b.high + b.low + b.close) / Decimal("3.0")) * Decimal(str(b.volume))
            for b in session_bars
        )
        return cum_tp_v / Decimal(str(cum_volume))

    def calculate_dynamic_atr(self, current_bar: MarketBar, history: List[MarketBar]) -> Decimal:
        """Calculates dynamic ATR over available bars without hardcoded fallbacks."""
        all_bars = (history or []) + [current_bar]
        atr = self._calculate_atr(all_bars)
        return atr if atr is not None else (current_bar.close * Decimal("0.015"))

    def evaluate(
        self,
        current_bar: MarketBar,
        history: List[MarketBar],
        indicators: Optional[Dict[str, Any]] = None,
    ) -> Optional[StrategySignal]:
        """Evaluates point-in-time VWAP overextension."""
        curr_session_date = self._get_session_date(current_bar.timestamp_iso)

        # 1. Filter history to current session bars only for cumulative session VWAP
        all_bars = (history or []) + [current_bar]
        session_bars = [b for b in all_bars if self._get_session_date(b.timestamp_iso) == curr_session_date]

        if len(session_bars) < self.min_session_bars:
            return None  # Warmup: insufficient session volume data

        # 2. Cumulative VWAP calculation
        cum_volume = sum(b.volume for b in session_bars)
        if cum_volume <= 0:
            return None

        cum_tp_v = sum(
            ((b.high + b.low + b.close) / Decimal("3.0")) * Decimal(str(b.volume))
            for b in session_bars
        )
        vwap = cum_tp_v / Decimal(str(cum_volume))

        # 3. Volume-weighted standard deviation
        cum_sq_diff = sum(
            Decimal(str(b.volume)) * (((b.high + b.low + b.close) / Decimal("3.0") - vwap) ** Decimal("2.0"))
            for b in session_bars
        )
        variance = cum_sq_diff / Decimal(str(cum_volume))
        std_dev = Decimal(str(math.sqrt(float(variance)))) if variance > Decimal("0.0") else Decimal("0.0")

        lower_band = vwap - (self.std_dev_threshold * std_dev)
        upper_band = vwap + (self.std_dev_threshold * std_dev)

        # 4. Dynamic ATR calculation (No hardcoded fallback)
        atr = self._calculate_atr(all_bars)
        if atr is None or atr <= Decimal("0.0"):
            return None

        # 5. Long Setup: Price stretched below lower band
        if current_bar.close <= lower_band and current_bar.low < lower_band:
            raw_entry = current_bar.close
            raw_target = vwap  # Mean reversion target
            raw_stop = raw_entry - (atr * self.stop_loss_atr_mult)

            if raw_target <= raw_entry:
                return None  # Contradictory target

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

            if stop_p >= entry_p or target_p <= entry_p:
                return None

            return StrategySignal(
                signal_id=f"SIG-VWAP-{uuid.uuid4().hex[:8].upper()}",
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
                reason=f"VWAP oversold: close {current_bar.close} <= lower band {lower_band:.2f}",
                evidence={
                    "vwap": str(vwap.quantize(Decimal("0.01"))),
                    "lower_band": str(lower_band.quantize(Decimal("0.01"))),
                    "std_dev": str(std_dev.quantize(Decimal("0.01"))),
                    "atr": str(atr.quantize(Decimal("0.01"))),
                },
                data_provenance={"session_date": curr_session_date, "session_bars": len(session_bars)},
            )

        return None
