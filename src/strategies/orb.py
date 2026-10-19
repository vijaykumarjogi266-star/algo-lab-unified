"""Opening Range Breakout (ORB) Canonical Quantitative Strategy.

Models volatility expansion beyond the opening range of the Indian market session (09:15 IST).

Repairs and Invariants:
1. Cutoff Enforcement: Timezone-aware (Asia/Kolkata) cutoff (default 14:45 IST) strictly enforced.
2. Opening Range Eligibility: Range is constructed ONLY from eligible session bars (e.g. 09:15 to 09:30 IST).
3. Candidate vs Fill Decoupling: Generating a signal does NOT consume the daily trade limit;
   only confirmed order fills consume the limit via mark_order_filled().
4. Duplicate Prevention: Tracks processed bar timestamps to prevent duplicate signals after restarts.
5. Statutory Tick Quantization: Directional quantization applied to limit entry, stop loss, and target.
"""

from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set
import uuid
from zoneinfo import ZoneInfo

from src.brokergateway.states import OrderSide
from src.core.exceptions import InvalidStrategyParameterError
from src.core.tick_precision import quantize_order_price
from src.scanner.scanner import MarketBar
from src.strategies.base import BaseStrategy
from src.strategies.models import StrategySignal

IST = ZoneInfo("Asia/Kolkata")


class OpeningRangeBreakoutStrategy(BaseStrategy):
    """Canonical Opening Range Breakout (ORB) Strategy."""

    STRATEGY_ID = "STRAT_ORB"

    def __init__(
        self,
        strategy_id: str = "STRAT_ORB",
        version: str = "2.0.0",
        parameters: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(strategy_id=strategy_id, version=version, parameters=parameters)

        self.range_minutes: int = self.parameters["range_minutes"]
        self.volume_multiplier: Decimal = self.parameters["volume_multiplier"]
        self.risk_reward_ratio: Decimal = self.parameters["risk_reward_ratio"]
        self.entry_cutoff_time: time = self.parameters["entry_cutoff_time"]
        self.session_start_time: time = self.parameters["session_start_time"]
        self.max_trades_per_session: int = self.parameters["max_trades_per_session"]

        # Session tracking state
        self.current_session_date: Optional[str] = None
        self.opening_high: Optional[Decimal] = None
        self.opening_low: Optional[Decimal] = None
        self.range_established: bool = False
        self.range_end_time: Optional[time] = None

        # Trade lifecycle state: distinguishes candidate generation from actual fills
        self.session_signals_generated: int = 0
        self.session_orders_filled: int = 0
        self.filled_sessions: Set[str] = set()
        self.processed_bars: Set[str] = set()

    def validate_and_sanitize_parameters(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Validates ORB parameters fail-closed."""
        range_min = params.get("range_minutes", params.get("breakout_window_minutes", 15))
        self._validate_numeric_range("range_minutes", range_min, min_val=5, max_val=60, is_integer=True)

        vol_mult = params.get("volume_multiplier", Decimal("1.2"))
        self._validate_numeric_range("volume_multiplier", vol_mult, min_val=1.0, max_val=5.0)

        rr_ratio = params.get("risk_reward_ratio", Decimal("2.0"))
        self._validate_numeric_range("risk_reward_ratio", rr_ratio, min_val=1.0, max_val=5.0)

        max_trades = params.get("max_trades_per_session", 1)
        self._validate_numeric_range("max_trades_per_session", max_trades, min_val=1, max_val=5, is_integer=True)

        # Parse times
        cutoff_raw = params.get("entry_cutoff_time", "14:45")
        if isinstance(cutoff_raw, str):
            try:
                parts = cutoff_raw.split(":")
                cutoff_t = time(int(parts[0]), int(parts[1]))
            except Exception as exc:
                raise InvalidStrategyParameterError(f"Invalid entry_cutoff_time format: {cutoff_raw}") from exc
        elif isinstance(cutoff_raw, time):
            cutoff_t = cutoff_raw
        else:
            raise InvalidStrategyParameterError(f"entry_cutoff_time must be str or time, got {type(cutoff_raw).__name__}")

        start_raw = params.get("session_start_time", "09:15")
        if isinstance(start_raw, str):
            try:
                parts = start_raw.split(":")
                start_t = time(int(parts[0]), int(parts[1]))
            except Exception as exc:
                raise InvalidStrategyParameterError(f"Invalid session_start_time format: {start_raw}") from exc
        elif isinstance(start_raw, time):
            start_t = start_raw
        else:
            raise InvalidStrategyParameterError(f"session_start_time must be str or time, got {type(start_raw).__name__}")

        if cutoff_t <= start_t:
            raise InvalidStrategyParameterError(f"entry_cutoff_time ({cutoff_t}) must be after session_start_time ({start_t})")

        return {
            "range_minutes": int(range_min),
            "volume_multiplier": Decimal(str(vol_mult)),
            "risk_reward_ratio": Decimal(str(rr_ratio)),
            "entry_cutoff_time": cutoff_t,
            "session_start_time": start_t,
            "max_trades_per_session": int(max_trades),
        }

    def _parse_ist_datetime(self, ts_iso: str) -> datetime:
        """Parses ISO timestamp and converts cleanly to Asia/Kolkata timezone."""
        dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        else:
            dt = dt.astimezone(IST)
        return dt

    def evaluate(
        self,
        current_bar: MarketBar,
        history: List[MarketBar],
        indicators: Optional[Dict[str, Any]] = None,
    ) -> Optional[StrategySignal]:
        """Evaluates point-in-time market bar for Opening Range Breakout."""
        bar_key = f"{current_bar.symbol}:{current_bar.timestamp_iso}"
        if bar_key in self.processed_bars:
            return None
        self.processed_bars.add(bar_key)

        dt_ist = self._parse_ist_datetime(current_bar.timestamp_iso)
        session_date = dt_ist.date().isoformat()
        bar_time = dt_ist.time()

        # Session Reset
        if session_date != self.current_session_date:
            self.current_session_date = session_date
            self.opening_high = None
            self.opening_low = None
            self.range_established = False
            self.session_signals_generated = 0
            self.session_orders_filled = 0
            # Calculate range end time
            total_minutes = self.session_start_time.hour * 60 + self.session_start_time.minute + self.range_minutes
            self.range_end_time = time(total_minutes // 60, total_minutes % 60)

        # Do not trade before market open
        if bar_time < self.session_start_time:
            return None

        # Phase 1: Range Construction from Eligible Session Bars
        if self.range_end_time and bar_time < self.range_end_time:
            if self.opening_high is None or current_bar.high > self.opening_high:
                self.opening_high = current_bar.high
            if self.opening_low is None or current_bar.low < self.opening_low:
                self.opening_low = current_bar.low
            return None  # Range still forming -> WAIT

        # Reconstruct opening range from historical session bars if not yet established incrementally
        if self.opening_high is None and history and self.range_end_time:
            opening_bars = []
            for b in history:
                b_dt = self._parse_ist_datetime(b.timestamp_iso)
                if b_dt.date().isoformat() == session_date and self.session_start_time <= b_dt.time() < self.range_end_time:
                    opening_bars.append(b)
            if opening_bars:
                self.opening_high = max(b.high for b in opening_bars)
                self.opening_low = min(b.low for b in opening_bars)

        self.range_established = True

        # Phase 2: Execution Restrictions
        # 1. Daily fill limit check (does NOT trigger on mere signal generation)
        if self.session_orders_filled >= self.max_trades_per_session or self.is_session_filled(session_date):
            return None

        # 2. Cutoff time check (Strict Asia/Kolkata enforcement)
        if bar_time >= self.entry_cutoff_time:
            return None

        if self.opening_high is None or self.opening_low is None:
            return None

        range_span = self.opening_high - self.opening_low
        if range_span <= Decimal("0.0"):
            return None

        # Volume Confirmation
        vol_surge = True
        if history and len(history) >= 5:
            recent_vols = [b.volume for b in history[-20:] if b.volume > 0]
            if recent_vols:
                avg_vol = Decimal(str(sum(recent_vols))) / Decimal(str(len(recent_vols)))
                if avg_vol > Decimal("0.0"):
                    vol_surge = Decimal(str(current_bar.volume)) >= (avg_vol * self.volume_multiplier)

        if not vol_surge:
            return None

        # Phase 3: Directional Breakout Evaluation
        # Bullish Breakout
        if current_bar.close > self.opening_high:
            raw_entry = current_bar.close
            raw_stop = max(self.opening_low, self.opening_high - (range_span * Decimal("0.50")))
            risk = raw_entry - raw_stop
            if risk <= Decimal("0.0"):
                return None
            raw_target = raw_entry + (risk * self.risk_reward_ratio)

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

            self.session_signals_generated += 1
            return StrategySignal(
                signal_id=f"SIG-ORB-{uuid.uuid4().hex[:8].upper()}",
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
                reason=f"ORB {self.range_minutes}m high breakout above {self.opening_high}",
                evidence={"opening_high": str(self.opening_high), "opening_low": str(self.opening_low), "bar_close": str(current_bar.close)},
                data_provenance={"session_date": session_date, "cutoff": str(self.entry_cutoff_time)},
            )

        # Bearish Breakdown
        elif current_bar.close < self.opening_low:
            raw_entry = current_bar.close
            raw_stop = min(self.opening_high, self.opening_low + (range_span * Decimal("0.50")))
            risk = raw_stop - raw_entry
            if risk <= Decimal("0.0"):
                return None
            raw_target = raw_entry - (risk * self.risk_reward_ratio)

            entry_p = quantize_order_price(
                raw_entry, current_bar.symbol, side_is_buy=False, order_purpose="ENTRY",
                trade_date=current_bar.timestamp_iso, reference_price=current_bar.close,
            )
            stop_p = quantize_order_price(
                raw_stop, current_bar.symbol, side_is_buy=False, order_purpose="STOP_LOSS",
                trade_date=current_bar.timestamp_iso, reference_price=current_bar.close,
            )
            target_p = quantize_order_price(
                raw_target, current_bar.symbol, side_is_buy=False, order_purpose="TAKE_PROFIT",
                trade_date=current_bar.timestamp_iso, reference_price=current_bar.close,
            )

            self.session_signals_generated += 1
            return StrategySignal(
                signal_id=f"SIG-ORB-{uuid.uuid4().hex[:8].upper()}",
                strategy_id=self.strategy_id,
                strategy_version=self.version,
                symbol=current_bar.symbol,
                side=OrderSide.SELL,
                target_entry_price=entry_p,
                stop_loss=stop_p,
                take_profit=target_p,
                confidence_score=Decimal("0.80"),
                timestamp_iso=current_bar.timestamp_iso,
                order_type="LIMIT",
                reason=f"ORB {self.range_minutes}m low breakdown below {self.opening_low}",
                evidence={"opening_high": str(self.opening_high), "opening_low": str(self.opening_low), "bar_close": str(current_bar.close)},
                data_provenance={"session_date": session_date, "cutoff": str(self.entry_cutoff_time)},
            )

        return None

    def mark_order_filled(self, session_date: str) -> None:
        """Explicitly consumes daily trade capacity ONLY upon confirmed order fill."""
        self.session_orders_filled += 1
        self.filled_sessions.add(session_date)

    def is_session_filled(self, session_date: str) -> bool:
        """Returns True if the session has already filled its quota."""
        return session_date in self.filled_sessions
