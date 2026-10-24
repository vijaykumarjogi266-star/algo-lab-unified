"""Institutional Pre-Trade Risk Management System (RMS) for Algo Lab.

Enforces:
1. Daily Loss Limit (Kill Switch): Halts all new entries if intraday drawdown exceeds threshold (default 2.0%).
2. Position Sizing & Concentration:
   - Max single position exposure ceiling (default 30% of portfolio equity).
   - Max sector concentration (default 2 stocks per sector).
   - Max concurrent portfolio positions (default 4).
3. Fat-Finger & Outlier Protection:
   - Rejects limit orders deviating by more than 3.0% from current LTP.
4. Statutory NSE Session Compliance:
   - Rejects new entries after 14:45 IST (SessionPolicy entry cutoff).
5. High-Frequency Rate Limiting:
   - Restricts order throughput to 5 orders/second to mirror exchange compliance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import logging
import time
from typing import Optional, Tuple

from src.brokergateway.states import OrderSide, OrderType
from src.core.safety_invariants import assert_paper_only_invariants
from src.core.session_policy import SessionPolicy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RiskCheckResult:
    is_approved: bool
    rejection_reason: Optional[str] = None
    rule_name: Optional[str] = None


class PreTradeRiskManager:
    """Institutional Risk Management System evaluating order intents before submission."""

    def __init__(
        self,
        max_daily_drawdown_pct: Decimal = Decimal("2.5"),  # 2.5% max daily drawdown
        max_position_equity_pct: Decimal = Decimal("30.0"), # 30% max per position
        max_concurrent_positions: int = 4,
        max_sector_exposure: int = 2,
        max_fat_finger_deviation_pct: Decimal = Decimal("3.0"),
        rate_limit_orders_per_sec: int = 5,
        enforce_session_times: bool = True,
    ):
        assert_paper_only_invariants()
        self.max_daily_drawdown_pct = max_daily_drawdown_pct
        self.max_position_equity_pct = max_position_equity_pct
        self.max_concurrent_positions = max_concurrent_positions
        self.max_sector_exposure = max_sector_exposure
        self.max_fat_finger_deviation_pct = max_fat_finger_deviation_pct
        self.rate_limit_orders_per_sec = rate_limit_orders_per_sec
        self.enforce_session_times = enforce_session_times
        self._last_order_timestamps: list[float] = []
        self._kill_switch_active: bool = False

    def activate_kill_switch(self, reason: str) -> None:
        """Manually or automatically locks trading due to risk breach."""
        self._kill_switch_active = True
        logger.critical(f"RMS KILL SWITCH ACTIVATED: {reason}")

    def reset_kill_switch(self) -> None:
        self._kill_switch_active = False
        logger.info("RMS Kill Switch reset.")

    @property
    def is_kill_switch_active(self) -> bool:
        return self._kill_switch_active

    def evaluate_order(
        self,
        symbol: str,
        sector: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: int,
        price: Decimal,
        ltp: Decimal,
        portfolio_equity: Decimal,
        initial_capital: Decimal,
        current_positions: dict,
        current_time_iso: Optional[str] = None,
    ) -> RiskCheckResult:
        """Comprehensive multi-rule pre-trade risk inspection."""
        assert_paper_only_invariants()

        # 1. Kill Switch Check
        if self._kill_switch_active:
            return RiskCheckResult(False, "Kill switch is active. Trading halted.", "KILL_SWITCH")

        # 2. Intraday Daily Loss Limit (Drawdown)
        if initial_capital > Decimal("0"):
            drawdown_pct = ((initial_capital - portfolio_equity) / initial_capital) * Decimal("100")
            if drawdown_pct >= self.max_daily_drawdown_pct:
                self.activate_kill_switch(f"Daily drawdown {drawdown_pct:.2f}% breached threshold {self.max_daily_drawdown_pct}%")
                return RiskCheckResult(
                    False,
                    f"Daily loss limit breached ({drawdown_pct:.2f}% >= {self.max_daily_drawdown_pct}%). Kill switch triggered.",
                    "DAILY_LOSS_LIMIT",
                )

        # 3. Session Policy Time Check (Entries blocked after 14:45 IST)
        if self.enforce_session_times and side == OrderSide.BUY:
            now_iso = current_time_iso or datetime.now().astimezone().isoformat()
            try:
                SessionPolicy.validate_order_entry_time(now_iso)
            except Exception as e:
                return RiskCheckResult(False, f"Statutory session timing violation: {e}", "SESSION_ENTRY_CUTOFF")

        # 4. Rate Limiter (Max N orders per second)
        now = time.time()
        self._last_order_timestamps = [t for t in self._last_order_timestamps if now - t < 1.0]
        if len(self._last_order_timestamps) >= self.rate_limit_orders_per_sec:
            return RiskCheckResult(False, f"Order rate limit exceeded ({self.rate_limit_orders_per_sec} orders/sec)", "RATE_LIMITER")
        self._last_order_timestamps.append(now)

        # 5. Position Sizing & Concentration Checks (only for opening new orders)
        if side == OrderSide.BUY:
            if symbol in current_positions:
                return RiskCheckResult(False, f"Position already active for {symbol}", "DUPLICATE_POSITION")

            if len(current_positions) >= self.max_concurrent_positions:
                return RiskCheckResult(
                    False,
                    f"Max concurrent positions reached ({len(current_positions)} / {self.max_concurrent_positions})",
                    "MAX_CONCURRENT_POSITIONS",
                )

            sector_count = sum(1 for p in current_positions.values() if p.sector == sector)
            if sector_count >= self.max_sector_exposure:
                return RiskCheckResult(
                    False,
                    f"Max sector exposure reached for {sector} ({sector_count} / {self.max_sector_exposure})",
                    "MAX_SECTOR_EXPOSURE",
                )

            notional = price * Decimal(str(quantity))
            max_notional = portfolio_equity * (self.max_position_equity_pct / Decimal("100"))
            if notional > max_notional:
                return RiskCheckResult(
                    False,
                    f"Order notional ₹{notional:.2f} exceeds max position equity cap ₹{max_notional:.2f} ({self.max_position_equity_pct}%)",
                    "MAX_POSITION_EQUITY_CAP",
                )

        # 6. Fat-Finger & Price Outlier Protection
        if ltp > Decimal("0") and price > Decimal("0"):
            deviation_pct = (abs(price - ltp) / ltp) * Decimal("100")
            if deviation_pct > self.max_fat_finger_deviation_pct:
                return RiskCheckResult(
                    False,
                    f"Order price ₹{price:.2f} deviates by {deviation_pct:.2f}% from LTP ₹{ltp:.2f} (Threshold: {self.max_fat_finger_deviation_pct}%)",
                    "FAT_FINGER_PROTECTION",
                )

        return RiskCheckResult(True, None, None)
