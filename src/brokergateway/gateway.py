"""Hardened Broker Gateway and Uncertainty Recovery Engine.

Provides:
1. Idempotent order creation and duplicate submission handling.
2. Validated state transition machine enforcing legal lifecycle steps.
3. Network timeout representation as SUBMISSION_UNKNOWN and CANCEL_UNKNOWN.
4. Authoritative uncertainty resolution via resolve_uncertain_order().
5. Exact fill deduplication protecting against double-fills and duplicate fee debits.
6. Late fill accounting handling fills post-cancellation or post-timeout.
"""

from __future__ import annotations

from decimal import Decimal
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Set

from src.brokergateway.models import ExecutionFill, ExecutionOrder, StateTransitionRecord, utc_now_iso
from src.brokergateway.safety_guard import OrderDispatchSafetyGuard
from src.brokergateway.states import OrderSide, OrderState, OrderType, UncertaintyResolutionStatus
from src.core.exceptions import AlgoLabSecurityError, BrokerExecutionBlockedError, InvalidOrderTickError
from src.core.logging import get_logger
from src.core.tick_precision import InstrumentTickRegistry, is_valid_tick

logger = get_logger(__name__)

# Valid state transition graph
LEGAL_TRANSITIONS: Dict[OrderState, Set[OrderState]] = {
    OrderState.CREATED: {OrderState.RISK_RESERVED, OrderState.REJECTED},
    OrderState.RISK_RESERVED: {OrderState.SUBMITTED, OrderState.SUBMISSION_UNKNOWN, OrderState.REJECTED},
    OrderState.SUBMITTED: {OrderState.OPEN, OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.SUBMISSION_UNKNOWN, OrderState.REJECTED, OrderState.CANCELLED},
    OrderState.SUBMISSION_UNKNOWN: {OrderState.OPEN, OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.REJECTED, OrderState.CANCELLED},
    OrderState.OPEN: {OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.CANCEL_REQUESTED, OrderState.CANCELLED, OrderState.EXPIRED},
    OrderState.PARTIALLY_FILLED: {OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.CANCEL_REQUESTED, OrderState.CANCELLED},
    OrderState.CANCEL_REQUESTED: {OrderState.CANCELLED, OrderState.CANCEL_UNKNOWN, OrderState.PARTIALLY_FILLED, OrderState.FILLED},
    OrderState.CANCEL_UNKNOWN: {OrderState.OPEN, OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.CANCELLED},
    OrderState.FILLED: set(),      # Terminal state
    OrderState.CANCELLED: {OrderState.FILLED, OrderState.PARTIALLY_FILLED},   # Terminal state (reopened if late fills arrive via venue execution report)
    OrderState.REJECTED: set(),    # Terminal state
    OrderState.EXPIRED: set(),     # Terminal state
}


class BrokerGateway:
    """Production OMS Gateway managing order lifecycles and recovery."""

    def __init__(self, db_conn: Optional[sqlite3.Connection] = None):
        self._lock = threading.RLock()
        self.orders: Dict[str, ExecutionOrder] = {}
        self.idempotency_map: Dict[str, str] = {}  # idempotency_key -> order_id
        self.processed_fill_ids: Set[str] = set()
        self.conn = db_conn or sqlite3.connect(":memory:", check_same_thread=False)
        self._init_db()

    def _init_db(self) -> None:
        """Initializes order persistence tables."""
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                idempotency_key TEXT UNIQUE,
                strategy_id TEXT,
                symbol TEXT,
                side TEXT,
                order_type TEXT,
                quantity INTEGER,
                price TEXT,
                venue TEXT,
                state TEXT,
                filled_quantity INTEGER,
                remaining_quantity INTEGER,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fills (
                fill_id TEXT PRIMARY KEY,
                order_id TEXT,
                symbol TEXT,
                side TEXT,
                quantity INTEGER,
                price TEXT,
                transaction_cost TEXT,
                slippage_cost TEXT,
                timestamp_utc TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS state_transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                from_state TEXT,
                to_state TEXT,
                reason TEXT,
                timestamp_utc TEXT
            )
        """)
        self.conn.commit()

    def create_order(
        self,
        order_id: str,
        idempotency_key: str,
        strategy_id: str,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: int,
        price: Optional[Decimal] = None,
        venue: str = "PAPER",
        is_risk_approved: bool = False,
    ) -> ExecutionOrder:
        """Idempotently creates a new order after passing security and risk gates."""
        with self._lock:
            # 1. Idempotency Check
            if idempotency_key in self.idempotency_map:
                existing_id = self.idempotency_map[idempotency_key]
                logger.info("Idempotent replay for key %s -> order %s", idempotency_key, existing_id)
                return self.orders[existing_id]

            # 2. Intercept through Security Guard
            intent_payload = {
                "order_id": order_id,
                "idempotency_key": idempotency_key,
                "strategy_id": strategy_id,
                "symbol": symbol,
                "side": side.value,
                "venue": venue,
                "is_risk_approved": is_risk_approved,
                "real_money": False,
            }
            OrderDispatchSafetyGuard.intercept_order_intent(intent_payload)

            # 2b. Authoritative Exchange Tick Precision Validation
            if price is not None:
                tick_size = InstrumentTickRegistry.get_tick_size(symbol, price)
                if not is_valid_tick(price, tick_size):
                    raise InvalidOrderTickError(
                        f"Off-tick order price rejected: order {order_id} price {price} "
                        f"for {symbol} is not a valid multiple of statutory tick size {tick_size}."
                    )

            # 3. Instantiate Order
            order = ExecutionOrder(
                order_id=order_id,
                idempotency_key=idempotency_key,
                strategy_id=strategy_id,
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                venue=venue,
                state=OrderState.CREATED,
            )

            self.orders[order_id] = order
            self.idempotency_map[idempotency_key] = order_id

            # Persist
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    order.order_id,
                    order.idempotency_key,
                    order.strategy_id,
                    order.symbol,
                    order.side.value,
                    order.order_type.value,
                    order.quantity,
                    str(order.price) if order.price else None,
                    order.venue,
                    order.state.value,
                    order.filled_quantity,
                    order.remaining_quantity,
                    order.created_at_utc,
                    order.updated_at_utc,
                ),
            )
            self.conn.commit()

            # Advance to RISK_RESERVED
            self.transition_state(order_id, OrderState.RISK_RESERVED, "Risk capital reserved")
            return order

    def transition_state(self, order_id: str, target_state: OrderState, reason: str) -> ExecutionOrder:
        """Transitions order state according to strict legal state machine."""
        with self._lock:
            order = self.orders.get(order_id)
            if not order:
                raise ValueError(f"Order '{order_id}' not found.")

            current = order.state
            if target_state != current:
                legal = LEGAL_TRANSITIONS.get(current, set())
                if target_state not in legal:
                    raise ValueError(
                        f"Illegal order state transition for {order_id}: {current.value} -> {target_state.value}. "
                        f"Legal targets: {[s.value for s in legal]}"
                    )

            order.state = target_state
            order.updated_at_utc = utc_now_iso()
            rec = StateTransitionRecord(from_state=current, to_state=target_state, reason=reason)
            order.state_history.append(rec)

            cursor = self.conn.cursor()
            cursor.execute(
                "UPDATE orders SET state = ?, updated_at = ? WHERE order_id = ?",
                (target_state.value, order.updated_at_utc, order_id),
            )
            cursor.execute(
                "INSERT INTO state_transitions (order_id, from_state, to_state, reason, timestamp_utc) VALUES (?, ?, ?, ?, ?)",
                (order_id, current.value, target_state.value, reason, rec.timestamp_utc),
            )
            self.conn.commit()
            logger.info("Order %s state changed: %s -> %s (%s)", order_id, current.value, target_state.value, reason)

        return order

    def handle_submission_timeout(self, order_id: str, error_detail: str) -> ExecutionOrder:
        """Transitions order to SUBMISSION_UNKNOWN upon transmission timeout."""
        return self.transition_state(
            order_id,
            OrderState.SUBMISSION_UNKNOWN,
            f"Network timeout during submission: {error_detail}",
        )

    def request_cancellation(self, order_id: str, reason: str = "Client cancellation requested") -> ExecutionOrder:
        """Transitions order to CANCEL_REQUESTED.
        
        Note: Cancellation requests must not be confused with confirmed cancellations.
        The order remains active until venue confirmation or timeout occurs.
        """
        return self.transition_state(order_id, OrderState.CANCEL_REQUESTED, reason)

    def confirm_cancellation(self, order_id: str, reason: str = "Venue confirmed cancellation") -> ExecutionOrder:
        """Transitions order to confirmed CANCELLED state upon authoritative venue confirmation."""
        return self.transition_state(order_id, OrderState.CANCELLED, reason)

    def handle_cancellation_timeout(self, order_id: str, error_detail: str) -> ExecutionOrder:
        """Transitions order to CANCEL_UNKNOWN upon cancellation timeout."""
        return self.transition_state(
            order_id,
            OrderState.CANCEL_UNKNOWN,
            f"Network timeout during cancellation: {error_detail}",
        )

    def resolve_uncertain_order(
        self,
        order_id: str,
        resolution_status: UncertaintyResolutionStatus,
        venue_evidence: Optional[Dict[str, Any]] = None,
    ) -> ExecutionOrder:
        """Authoritatively resolves orders in SUBMISSION_UNKNOWN or CANCEL_UNKNOWN states."""
        with self._lock:
            order = self.orders.get(order_id)
            if not order:
                raise ValueError(f"Order '{order_id}' not found.")

            if order.state not in (OrderState.SUBMISSION_UNKNOWN, OrderState.CANCEL_UNKNOWN):
                logger.warning("Order %s is not in uncertain state (current: %s)", order_id, order.state.value)
                return order

            evidence = venue_evidence or {}
            if resolution_status == UncertaintyResolutionStatus.CONFIRMED_AT_VENUE:
                # Venue confirmed order was received
                if order.state == OrderState.SUBMISSION_UNKNOWN:
                    self.transition_state(order_id, OrderState.OPEN, "Confirmed active at venue via reconciliation")
                elif order.state == OrderState.CANCEL_UNKNOWN:
                    self.transition_state(order_id, OrderState.CANCELLED, "Confirmed cancelled at venue via reconciliation")
            elif resolution_status == UncertaintyResolutionStatus.ABSENT_AT_VENUE:
                # Venue confirmed order was never received
                if order.state == OrderState.SUBMISSION_UNKNOWN:
                    self.transition_state(order_id, OrderState.REJECTED, "Confirmed absent at venue; order rejected")
                elif order.state == OrderState.CANCEL_UNKNOWN:
                    if order.filled_quantity >= order.quantity and order.quantity > 0:
                        self.transition_state(order_id, OrderState.FILLED, "Cancellation absent at venue; order already filled")
                    elif order.filled_quantity > 0:
                        self.transition_state(order_id, OrderState.PARTIALLY_FILLED, "Cancellation absent at venue; order remains PARTIALLY_FILLED")
                    else:
                        self.transition_state(order_id, OrderState.OPEN, "Cancellation absent at venue; order remains OPEN")
            elif resolution_status == UncertaintyResolutionStatus.MANUAL_ESCALATION:
                logger.critical("Order %s requires MANUAL OPERATOR INTERVENTION: %s", order_id, evidence)

            return order

    def process_fill(self, fill: ExecutionFill) -> ExecutionOrder:
        """Ingests a discrete fill event with exact deduplication and late-fill handling."""
        with self._lock:
            order = self.orders.get(fill.order_id)
            if not order:
                raise ValueError(f"Fill references unknown order ID: '{fill.order_id}'")

            # 1. Fill Deduplication Check
            if fill.fill_id in self.processed_fill_ids:
                logger.warning("DUPLICATE FILL IGNORED: fill_id %s already processed for order %s", fill.fill_id, fill.order_id)
                return order

            self.processed_fill_ids.add(fill.fill_id)

            # 2. Check Late Fill
            is_late_fill = order.state in (OrderState.CANCEL_REQUESTED, OrderState.CANCEL_UNKNOWN, OrderState.CANCELLED)
            if is_late_fill:
                logger.warning("LATE FILL RECEIVED for order %s in state %s: fill_id %s, qty %d", order.order_id, order.state.value, fill.fill_id, fill.quantity)

            # 3. Update Order Quantities and Average Price
            new_filled = order.filled_quantity + fill.quantity
            if new_filled > order.quantity:
                raise ValueError(
                    f"Fill overfills order {order.order_id}: existing filled {order.filled_quantity} + "
                    f"fill qty {fill.quantity} > total order qty {order.quantity}"
                )

            # Weighted average calculation
            total_value = (order.average_fill_price * Decimal(order.filled_quantity)) + (fill.price * Decimal(fill.quantity))
            order.filled_quantity = new_filled
            order.remaining_quantity = order.quantity - new_filled
            order.average_fill_price = total_value / Decimal(order.filled_quantity)
            order.fills.append(fill)

            # 4. State Transition
            if order.filled_quantity == order.quantity:
                if order.state != OrderState.FILLED:
                    self.transition_state(order.order_id, OrderState.FILLED, f"Filled completely by fill {fill.fill_id}")
            else:
                if order.state in (OrderState.OPEN, OrderState.SUBMITTED, OrderState.SUBMISSION_UNKNOWN, OrderState.CANCEL_UNKNOWN, OrderState.CANCEL_REQUESTED):
                    self.transition_state(order.order_id, OrderState.PARTIALLY_FILLED, f"Partially filled by fill {fill.fill_id}")

            # Persist fill
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO fills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    fill.fill_id,
                    fill.order_id,
                    fill.symbol,
                    fill.side.value,
                    fill.quantity,
                    str(fill.price),
                    str(fill.transaction_cost),
                    str(fill.slippage_cost),
                    fill.timestamp_utc,
                ),
            )
            self.conn.commit()

            return order
