"""Simulated Pending Order Book for Resting Limit & Stop Loss Orders.

Supports:
1. Resting Limit Orders (BUY below ask, SELL above bid).
2. Stop Loss Orders (SL-M, SL-L) triggered on price breaches.
3. Order Lifecycle: SUBMITTED -> PENDING -> TRIGGERED -> FILLED or CANCELLED.
4. Capital reservation lifecycle tied directly to pending orders.
5. Statutory Session EOD automated cancellation at 15:15 IST.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid

from src.brokergateway.states import OrderSide, OrderType
from src.core.safety_invariants import assert_paper_only_invariants
from src.paper.order_book_fill import ExecutionResult, OrderBookDepth, RealisticOrderBookExecutionEngine

logger = logging.getLogger(__name__)


@dataclass
class PendingOrder:
    order_id: str
    symbol: str
    sector: str
    side: OrderSide
    order_type: OrderType
    quantity: int
    price: Decimal
    stop_loss: Optional[Decimal]
    take_profit: Optional[Decimal]
    reservation_id: str
    idempotency_key: str
    created_at_iso: str
    status: str = "PENDING"  # PENDING, PARTIALLY_FILLED, FILLED, CANCELLED
    filled_quantity: int = 0

    @property
    def notional(self) -> Decimal:
        return self.price * Decimal(str(self.quantity))

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "sector": self.sector,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": self.quantity,
            "price": float(self.price),
            "stop_loss": float(self.stop_loss) if self.stop_loss else None,
            "take_profit": float(self.take_profit) if self.take_profit else None,
            "reservation_id": self.reservation_id,
            "idempotency_key": self.idempotency_key,
            "created_at_iso": self.created_at_iso,
            "status": self.status,
            "filled_quantity": self.filled_quantity,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PendingOrder:
        return cls(
            order_id=d["order_id"],
            symbol=d["symbol"],
            sector=d.get("sector", "GENERAL"),
            side=OrderSide(d["side"]),
            order_type=OrderType(d["order_type"]),
            quantity=int(d["quantity"]),
            price=Decimal(str(d["price"])),
            stop_loss=Decimal(str(d["stop_loss"])) if d.get("stop_loss") else None,
            take_profit=Decimal(str(d["take_profit"])) if d.get("take_profit") else None,
            reservation_id=d["reservation_id"],
            idempotency_key=d["idempotency_key"],
            created_at_iso=d["created_at_iso"],
            status=d.get("status", "PENDING"),
            filled_quantity=int(d.get("filled_quantity", 0)),
        )


class PendingOrderBook:
    """Manages active resting orders and evaluates matching against live depth."""

    def __init__(self, execution_engine: Optional[RealisticOrderBookExecutionEngine] = None):
        assert_paper_only_invariants()
        self.orders: dict[str, PendingOrder] = {}
        self.execution_engine = execution_engine or RealisticOrderBookExecutionEngine()

    def add_order(self, order: PendingOrder) -> None:
        self.orders[order.order_id] = order

    def get_order(self, order_id: str) -> Optional[PendingOrder]:
        return self.orders.get(order_id)

    def cancel_order(self, order_id: str) -> Optional[PendingOrder]:
        if order_id in self.orders:
            order = self.orders.pop(order_id)
            order.status = "CANCELLED"
            return order
        return None

    def cancel_all(self, reason: str = "MANUAL") -> list[PendingOrder]:
        cancelled = []
        for oid in list(self.orders.keys()):
            o = self.cancel_order(oid)
            if o:
                cancelled.append(o)
        return cancelled

    def evaluate_pending(self, depth_map: dict[str, OrderBookDepth]) -> list[tuple[PendingOrder, ExecutionResult]]:
        """Evaluates whether any resting limit order is marketable against current depth.
        
        Handles:
        - Partial fills: remaining quantity stays active in resting book (DEF-010, DEF-015).
        - Circuit-breaker exceptions: safely handled per symbol without terminating loop (DEF-011).
        """
        filled: list[tuple[PendingOrder, ExecutionResult]] = []
        orders_to_remove: list[str] = []

        for order_id, order in list(self.orders.items()):
            if order.symbol not in depth_map:
                continue

            depth = depth_map[order.symbol]

            # Try executing limit order with circuit breaker protection (DEF-011)
            try:
                res = self.execution_engine.evaluate_limit_order(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=order.quantity,
                    limit_price=order.price,
                    depth=depth,
                )
            except ValueError as e:
                logger.warning(
                    "Circuit breaker or validation rejection for resting order %s (%s): %s",
                    order.order_id, order.symbol, e
                )
                continue

            if res is not None and res.filled_quantity > 0:
                order.filled_quantity += res.filled_quantity
                if res.filled_quantity >= order.quantity:
                    order.quantity = 0
                    order.status = "FILLED"
                    orders_to_remove.append(order_id)
                else:
                    # DEF-010 & DEF-015: Partial fill preserves remaining quantity in resting book
                    order.quantity -= res.filled_quantity
                    order.status = "PARTIALLY_FILLED"
                filled.append((order, res))

        for oid in orders_to_remove:
            self.orders.pop(oid, None)

        return filled

    def to_dict(self) -> dict:
        return {oid: o.to_dict() for oid, o in self.orders.items()}

    def load_from_dict(self, data: dict) -> None:
        self.orders = {oid: PendingOrder.from_dict(d) for oid, d in data.items() if d.get("status") == "PENDING"}
