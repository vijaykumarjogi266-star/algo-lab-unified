"""Unit Tests for BrokerGateway Lifecycle and Uncertainty Recovery."""

from __future__ import annotations

from decimal import Decimal
import pytest

from src.brokergateway.gateway import BrokerGateway
from src.brokergateway.models import ExecutionFill
from src.brokergateway.states import OrderSide, OrderState, OrderType, UncertaintyResolutionStatus


class TestBrokerGatewayLifecycle:
    """Verifies state machine transitions, idempotency, and timeout recovery."""

    @pytest.fixture
    def gateway(self):
        return BrokerGateway()

    def test_idempotent_order_creation(self, gateway):
        order1 = gateway.create_order(
            order_id="ORD-101",
            idempotency_key="IDEM-001",
            strategy_id="STRAT-ALPHA",
            symbol="INFY",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=100,
            price=Decimal("1500.00"),
            venue="PAPER",
            is_risk_approved=True,
        )
        assert order1.state == OrderState.RISK_RESERVED

        # Duplicate replay with same idempotency key
        order2 = gateway.create_order(
            order_id="ORD-101-DUP",
            idempotency_key="IDEM-001",
            strategy_id="STRAT-ALPHA",
            symbol="INFY",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=100,
            price=Decimal("1500.00"),
            venue="PAPER",
            is_risk_approved=True,
        )
        assert order2.order_id == "ORD-101"
        assert len(gateway.orders) == 1

    def test_submission_timeout_and_uncertainty_resolution(self, gateway):
        order = gateway.create_order(
            order_id="ORD-102",
            idempotency_key="IDEM-002",
            strategy_id="STRAT-BETA",
            symbol="TCS",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=50,
            venue="PAPER",
            is_risk_approved=True,
        )
        gateway.transition_state(order.order_id, OrderState.SUBMITTED, "Dispatched to paper simulator")

        # Network timeout occurs
        gateway.handle_submission_timeout(order.order_id, "ReadTimeout after 5000ms")
        assert order.state == OrderState.SUBMISSION_UNKNOWN

        # Reconcile: venue confirms order exists
        gateway.resolve_uncertain_order(order.order_id, UncertaintyResolutionStatus.CONFIRMED_AT_VENUE)
        assert order.state == OrderState.OPEN

    def test_fill_deduplication(self, gateway):
        order = gateway.create_order(
            order_id="ORD-103",
            idempotency_key="IDEM-003",
            strategy_id="STRAT-GAMMA",
            symbol="RELIANCE",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10,
            price=Decimal("2800.00"),
            venue="PAPER",
            is_risk_approved=True,
        )
        gateway.transition_state(order.order_id, OrderState.SUBMITTED, "Dispatched")
        gateway.transition_state(order.order_id, OrderState.OPEN, "Acknowledged")

        fill = ExecutionFill(
            fill_id="FILL-001",
            order_id=order.order_id,
            symbol="RELIANCE",
            side=OrderSide.BUY,
            quantity=5,
            price=Decimal("2800.00"),
            transaction_cost=Decimal("15.50"),
            slippage_cost=Decimal("2.00"),
        )

        # Ingest fill once
        gateway.process_fill(fill)
        assert order.filled_quantity == 5
        assert order.remaining_quantity == 5
        assert order.state == OrderState.PARTIALLY_FILLED

        # Ingest duplicate fill event
        gateway.process_fill(fill)
        # Quantities must NOT double
        assert order.filled_quantity == 5
        assert order.remaining_quantity == 5
        assert len(order.fills) == 1

    def test_complete_fill_transition(self, gateway):
        order = gateway.create_order(
            order_id="ORD-104",
            idempotency_key="IDEM-004",
            strategy_id="STRAT-GAMMA",
            symbol="HDFCBANK",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10,
            price=Decimal("1600.00"),
            venue="PAPER",
            is_risk_approved=True,
        )
        gateway.transition_state(order.order_id, OrderState.SUBMITTED, "Dispatched")

        fill = ExecutionFill(
            fill_id="FILL-002",
            order_id=order.order_id,
            symbol="HDFCBANK",
            side=OrderSide.BUY,
            quantity=10,
            price=Decimal("1600.00"),
        )
        gateway.process_fill(fill)
        assert order.state == OrderState.FILLED
        assert order.filled_quantity == 10
        assert order.remaining_quantity == 0
