"""Canonical Session Policy and Timezone Authority for Algo Lab Unified.

Resolves the 15:15 vs 15:25 EOD square-off discrepancy by establishing
one unambiguous institutional session policy for Indian Equities (NSE).

Timeline (Asia/Kolkata):
- 09:15: Market Open. First 5-minute candle starts.
- 09:15 - 09:30: Opening Range Breakout (ORB) calculation window (no trades).
- 09:30 - 14:45: Active Trade Execution Window.
- 14:45: Entry Cutoff. No new trade signals or orders may be opened.
- 15:15: Intraday MIS Square-Off Trigger.
         1. All outstanding unfilled and partially filled limit orders are cancelled.
         2. Position liquidation begins (limit/market exit orders submitted).
- 15:25: Session Final Candle (15:25 - 15:30).
         Hard square-off enforcement: Any remaining open position must be liquidated
         at this candle's close. Zero overnight carry is permitted for intraday paper accounts.
- 15:30: Market Close. Continuous trading terminates.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Optional, Union
from zoneinfo import ZoneInfo

from src.core.exceptions import AlgoLabSecurityError

# Authoritative Timezone
IST = ZoneInfo("Asia/Kolkata")

# Statutory NSE Trading Session Milestones
SESSION_OPEN_TIME = time(9, 15)
OPENING_RANGE_END_TIME = time(9, 30)
ENTRY_CUTOFF_TIME = time(14, 45)
SQUAREOFF_TRIGGER_TIME = time(15, 15)
SESSION_HARD_CLOSE_TIME = time(15, 25)
MARKET_CLOSE_TIME = time(15, 30)


class SessionPolicy:
    """Institutional Trading Session Lifecycle Policy."""

    @staticmethod
    def to_ist(dt: Union[str, datetime]) -> datetime:
        """Converts an ISO timestamp or datetime to timezone-aware IST."""
        if isinstance(dt, str):
            # Parse ISO string
            parsed = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        elif isinstance(dt, datetime):
            parsed = dt
        else:
            raise TypeError(f"Expected str or datetime, got {type(dt).__name__}")

        if parsed.tzinfo is None:
            # Assume UTC if naive, then convert to IST
            return parsed.replace(tzinfo=ZoneInfo("UTC")).astimezone(IST)
        return parsed.astimezone(IST)

    @classmethod
    def is_market_session(cls, dt: Union[str, datetime]) -> bool:
        """Returns True if timestamp falls within official market hours (09:15 to 15:30 IST)."""
        ist_dt = cls.to_ist(dt)
        # Saturday (5) or Sunday (6)
        if ist_dt.weekday() >= 5:
            return False
        t = ist_dt.time()
        return SESSION_OPEN_TIME <= t <= MARKET_CLOSE_TIME

    @classmethod
    def can_enter_new_trade(cls, dt: Union[str, datetime]) -> bool:
        """Returns True if market is within the valid entry window (09:30 to 14:45 IST)."""
        ist_dt = cls.to_ist(dt)
        if not cls.is_market_session(ist_dt):
            return False
        t = ist_dt.time()
        return OPENING_RANGE_END_TIME <= t < ENTRY_CUTOFF_TIME

    @classmethod
    def should_trigger_squareoff(cls, dt: Union[str, datetime]) -> bool:
        """Returns True if time is >= 15:15 IST (start of square-off sequence)."""
        ist_dt = cls.to_ist(dt)
        t = ist_dt.time()
        return t >= SQUAREOFF_TRIGGER_TIME

    @classmethod
    def is_hard_close_bar(cls, dt: Union[str, datetime]) -> bool:
        """Returns True if bar represents the terminal 15:25 candle (closing at 15:30)."""
        ist_dt = cls.to_ist(dt)
        t = ist_dt.time()
        return t >= SESSION_HARD_CLOSE_TIME

    @classmethod
    def validate_order_entry_time(cls, dt: Union[str, datetime], symbol: str = "") -> None:
        """Fail-closed validation preventing orders after the 14:45 IST entry cutoff."""
        ist_dt = cls.to_ist(dt)
        t = ist_dt.time()
        if t >= ENTRY_CUTOFF_TIME:
            raise AlgoLabSecurityError(
                f"Trade entry rejected for {symbol} at {t.strftime('%H:%M:%S')} IST: "
                f"Exceeds mandatory institutional entry cutoff of {ENTRY_CUTOFF_TIME.strftime('%H:%M')} IST."
            )
