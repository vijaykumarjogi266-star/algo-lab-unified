"""Order and Execution Models for Algo Lab Unified.

Provides immutable execution structures, fill records, and state transition histories.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from src.brokergateway.states import OrderSide, OrderState, OrderType


def utc_now_iso() -> str:
    """Returns current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ExecutionFill:
    """Represents a discrete execution fill event from the venue."""
    fill_id: str
    order_id: str
    symbol: str
    side: OrderSide
    quantity: int
    price: Decimal
    transaction_cost: Decimal = Decimal("0.0")
    slippage_cost: Decimal = Decimal("0.0")
    timestamp_utc: str = field(default_factory=utc_now_iso)


@dataclass
class StateTransitionRecord:
    """Audit record capturing an explicit state transition."""
    from_state: OrderState
    to_state: OrderState
    reason: str
    timestamp_utc: str = field(default_factory=utc_now_iso)


@dataclass
class ExecutionOrder:
    """Authoritative representation of an order instance."""
    order_id: str
    idempotency_key: str
    strategy_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: int
    price: Optional[Decimal] = None
    venue: str = "PAPER"
    state: OrderState = OrderState.CREATED
    filled_quantity: int = 0
    remaining_quantity: int = 0
    average_fill_price: Decimal = Decimal("0.0")
    fills: List[ExecutionFill] = field(default_factory=list)
    state_history: List[StateTransitionRecord] = field(default_factory=list)
    created_at_utc: str = field(default_factory=utc_now_iso)
    updated_at_utc: str = field(default_factory=utc_now_iso)

    def __post_init__(self):
        if self.remaining_quantity == 0 and self.filled_quantity == 0:
            self.remaining_quantity = self.quantity
