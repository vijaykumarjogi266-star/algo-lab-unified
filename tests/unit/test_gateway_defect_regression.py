"""Regression tests for three confirmed gateway defects fixed in this task.

BUG-IDEM-01: Idempotency key reuse with conflicting payload silently dropped second order.
BUG-FILL-01: Duplicate fill ID checked AFTER overfill bounds — duplicate fills raised false ValueError.
GAP-GW-02:   request_cancellation() bypassed OrderDispatchSafetyGuard.
"""

from decimal import Decimal

import pytest

from src.brokergateway.gateway import BrokerGateway
from src.brokergateway.models import ExecutionFill
from src.brokergateway.states import OrderSide, OrderState, OrderType
from src.core.exceptions import IdempotencyConflictError


def _make_gateway():
    return BrokerGateway()


def _create_open_order(gw, order_id="ORD-1", idem="IDEM-1", symbol="RELIANCE", qty=100,
                       price=Decimal("2500.00")):
    """Helper: create an order, reserve risk, and submit it."""
    gw.create_order(
        order_id=order_id,
        idempotency_key=idem,
        strategy_id="STRAT_TEST",
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=qty,
        price=price,
        venue="PAPER",
        is_risk_approved=True,
    )
    gw.transition_state(order_id, OrderState.RISK_RESERVED, "Risk approved")
    gw.transition_state(order_id, OrderState.SUBMITTED, "Submitted to venue")
    gw.transition_state(order_id, OrderState.OPEN, "Acknowledged by venue")
    return gw.orders[order_id]


def _make_fill(fill_id, order_id, symbol, qty, price=Decimal("2500.00")):
    return ExecutionFill(
        fill_id=fill_id,
        order_id=order_id,
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=qty,
        price=price,
        transaction_cost=Decimal("5.00"),
        slippage_cost=Decimal("1.00"),
    )


# ---------------------------------------------------------------------------
# BUG-IDEM-01: Idempotency payload conflict must raise IdempotencyConflictError
# ---------------------------------------------------------------------------

class TestIdempotencyPayloadConflict:
    """BUG-IDEM-01 regression: silent drop of conflicting idempotency replay."""

    def test_exact_replay_returns_existing_order(self):
        """Identical payload replay is idempotent and returns existing order."""
        gw = _make_gateway()
        order1 = gw.create_order(
            order_id="ORD-A",
            idempotency_key="IDEM-A",
            strategy_id="STRAT_1",
            symbol="TCS",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=50,
            price=Decimal("3500.00"),
            venue="PAPER",
            is_risk_approved=True,
        )
        order2 = gw.create_order(
            order_id="ORD-A",
            idempotency_key="IDEM-A",
            strategy_id="STRAT_1",
            symbol="TCS",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=50,
            price=Decimal("3500.00"),
            venue="PAPER",
            is_risk_approved=True,
        )
        assert order1 is order2, "Exact replay must return the same object"

    def test_conflicting_symbol_raises_idempotency_conflict_error(self):
        """Different symbol on same key must raise IdempotencyConflictError, not silently drop."""
        gw = _make_gateway()
        gw.create_order(
            order_id="ORD-B",
            idempotency_key="IDEM-B",
            strategy_id="STRAT_1",
            symbol="TCS",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=50,
            price=Decimal("3500.00"),
            venue="PAPER",
            is_risk_approved=True,
        )
        with pytest.raises(IdempotencyConflictError, match="Idempotency conflict"):
            gw.create_order(
                order_id="ORD-B2",
                idempotency_key="IDEM-B",
                strategy_id="STRAT_1",
                symbol="INFY",  # Different symbol — conflict
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=50,
                price=Decimal("3500.00"),
                venue="PAPER",
                is_risk_approved=True,
            )

    def test_conflicting_quantity_raises_idempotency_conflict_error(self):
        """Different quantity on same key raises IdempotencyConflictError."""
        gw = _make_gateway()
        gw.create_order(
            order_id="ORD-C",
            idempotency_key="IDEM-C",
            strategy_id="STRAT_1",
            symbol="INFY",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=100,
            price=Decimal("1500.00"),
            venue="PAPER",
            is_risk_approved=True,
        )
        with pytest.raises(IdempotencyConflictError):
            gw.create_order(
                order_id="ORD-C2",
                idempotency_key="IDEM-C",
                strategy_id="STRAT_1",
                symbol="INFY",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=200,  # Different quantity — conflict
                price=Decimal("1500.00"),
                venue="PAPER",
                is_risk_approved=True,
            )

    def test_conflicting_price_raises_idempotency_conflict_error(self):
        """Different price on same key raises IdempotencyConflictError."""
        gw = _make_gateway()
        gw.create_order(
            order_id="ORD-D",
            idempotency_key="IDEM-D",
            strategy_id="STRAT_1",
            symbol="WIPRO",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=100,
            price=Decimal("500.00"),
            venue="PAPER",
            is_risk_approved=True,
        )
        with pytest.raises(IdempotencyConflictError):
            gw.create_order(
                order_id="ORD-D2",
                idempotency_key="IDEM-D",
                strategy_id="STRAT_1",
                symbol="WIPRO",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=100,
                price=Decimal("505.00"),  # Different price — conflict
                venue="PAPER",
                is_risk_approved=True,
            )


# ---------------------------------------------------------------------------
# BUG-FILL-01: Duplicate fill detected before overfill check
# ---------------------------------------------------------------------------

class TestFillDeduplicationBeforeOverfillCheck:
    """BUG-FILL-01 regression: duplicate fill on fully-filled order raises false ValueError."""

    def test_duplicate_fill_on_fully_filled_order_is_silently_ignored(self):
        """A duplicate fill arriving after the order is fully filled must be ignored, not raise ValueError."""
        gw = _make_gateway()
        _create_open_order(gw, order_id="ORD-FILL-1", qty=10)

        fill = _make_fill("FILL-1", "ORD-FILL-1", "RELIANCE", qty=10)
        gw.process_fill(fill)

        # Verify order is now fully filled
        assert gw.orders["ORD-FILL-1"].state == OrderState.FILLED
        assert gw.orders["ORD-FILL-1"].filled_quantity == 10

        # Re-deliver the same fill — must be silently ignored, not raise ValueError
        duplicate = _make_fill("FILL-1", "ORD-FILL-1", "RELIANCE", qty=10)
        result = gw.process_fill(duplicate)

        # State and quantities must be unchanged
        assert result.state == OrderState.FILLED
        assert result.filled_quantity == 10
        assert len(result.fills) == 1, "Duplicate fill must not be appended to fills list"

    def test_overfill_rejected_fill_id_not_consumed_allowing_corrected_retry(self):
        """A rejected overfill must not consume the fill_id slot; a corrected retry must succeed."""
        gw = _make_gateway()
        _create_open_order(gw, order_id="ORD-FILL-2", qty=10)

        # First attempt: overfill quantity
        bad_fill = _make_fill("FILL-X", "ORD-FILL-2", "RELIANCE", qty=15)
        with pytest.raises(ValueError, match="overfill"):
            gw.process_fill(bad_fill)

        # Corrected retry with same fill_id, correct quantity — must succeed
        good_fill = _make_fill("FILL-X", "ORD-FILL-2", "RELIANCE", qty=10)
        result = gw.process_fill(good_fill)

        assert result.filled_quantity == 10, (
            f"Corrected fill was wrongly swallowed: filled_qty={result.filled_quantity}"
        )

    def test_genuine_duplicate_partial_fill_is_ignored_not_double_counted(self):
        """A duplicate partial fill must be ignored; quantities must not be double-counted."""
        gw = _make_gateway()
        _create_open_order(gw, order_id="ORD-FILL-3", qty=20)

        fill = _make_fill("FILL-P1", "ORD-FILL-3", "RELIANCE", qty=10)
        gw.process_fill(fill)

        assert gw.orders["ORD-FILL-3"].filled_quantity == 10
        assert gw.orders["ORD-FILL-3"].state == OrderState.PARTIALLY_FILLED

        # Duplicate delivery of the same partial fill
        duplicate = _make_fill("FILL-P1", "ORD-FILL-3", "RELIANCE", qty=10)
        result = gw.process_fill(duplicate)

        assert result.filled_quantity == 10, "Duplicate partial fill must not double-count"
        assert result.state == OrderState.PARTIALLY_FILLED


# ---------------------------------------------------------------------------
# GAP-GW-02: request_cancellation passes through OrderDispatchSafetyGuard
# ---------------------------------------------------------------------------

class TestCancellationSafetyGuard:
    """GAP-GW-02 regression: request_cancellation bypassed OrderDispatchSafetyGuard."""

    def test_request_cancellation_succeeds_for_paper_venue(self):
        """Cancellation of a PAPER order must complete without error."""
        gw = _make_gateway()
        _create_open_order(gw, order_id="ORD-CAN-1")
        order = gw.request_cancellation("ORD-CAN-1", "Test cancellation")
        assert order.state == OrderState.CANCEL_REQUESTED

    def test_request_cancellation_for_unknown_order_raises_value_error(self):
        """Cancellation request for a non-existent order must raise ValueError."""
        gw = _make_gateway()
        with pytest.raises(ValueError, match="not found"):
            gw.request_cancellation("NON-EXISTENT-ORDER")

    def test_request_cancellation_transitions_to_cancel_requested(self):
        """After request_cancellation, order state must be CANCEL_REQUESTED."""
        gw = _make_gateway()
        _create_open_order(gw, order_id="ORD-CAN-2")
        gw.request_cancellation("ORD-CAN-2")
        assert gw.orders["ORD-CAN-2"].state == OrderState.CANCEL_REQUESTED
