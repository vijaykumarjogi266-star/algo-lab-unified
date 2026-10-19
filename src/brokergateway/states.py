"""Order States and Lifecycle Definitions for Algo Lab Unified.

Provides explicit, validated state enumerations supporting network drop
and uncertainty recovery (SUBMISSION_UNKNOWN, CANCEL_UNKNOWN).
"""

from __future__ import annotations

from enum import Enum


class OrderState(str, Enum):
    """Rigorous state machine enumeration for all order instances."""
    CREATED = "CREATED"
    RISK_RESERVED = "RISK_RESERVED"
    SUBMITTED = "SUBMITTED"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCEL_UNKNOWN = "CANCEL_UNKNOWN"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class UncertaintyResolutionStatus(str, Enum):
    """Resolution outcomes for orders in SUBMISSION_UNKNOWN or CANCEL_UNKNOWN states."""
    PENDING = "PENDING"
    CONFIRMED_AT_VENUE = "CONFIRMED_AT_VENUE"
    ABSENT_AT_VENUE = "ABSENT_AT_VENUE"
    RESOLVED_LOCALLY = "RESOLVED_LOCALLY"
    MANUAL_ESCALATION = "MANUAL_ESCALATION"


class OrderSide(str, Enum):
    """Order side specification."""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Order type specification."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"
    SL_M = "SL-M"
