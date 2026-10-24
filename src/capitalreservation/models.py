"""Capital Reservation Data Models and Schemas."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReservationStatus(str, Enum):
    """Lifecycle status of a capital reservation."""
    ACTIVE = "ACTIVE"
    CONSUMED = "CONSUMED"
    RELEASED = "RELEASED"


class ReservationOutcome(str, Enum):
    """Result outcome of a capital reservation request."""
    RESERVED = "RESERVED"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
    CONFLICT = "CONFLICT"
    RELEASED = "RELEASED"
    CONSUMED = "CONSUMED"
    NOT_FOUND = "NOT_FOUND"
    REJECTED = "REJECTED"


@dataclass
class CapitalReservation:
    """Represents an atomic margin/capital reservation."""
    reservation_id: str
    account_id: str
    idempotency_key: str
    strategy_id: str
    symbol: str
    requested_notional: Decimal
    reserved_cash: Decimal
    side: Optional[str] = None
    status: ReservationStatus = ReservationStatus.ACTIVE
    created_at_utc: str = field(default_factory=utc_now_iso)
    updated_at_utc: str = field(default_factory=utc_now_iso)
