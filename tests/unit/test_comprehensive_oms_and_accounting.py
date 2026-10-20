"""Comprehensive Lifecycle, Accounting, and Session Policy Test Suite.

Mandatory Gates Covered:
- Gate 2: Lifecycle and Reservation Accounting (Workstream B & C)
- Gate 3: EOD Timing and Strategy Correctness (Workstream D & E)
"""

from decimal import Decimal
import pytest

from src.brokergateway.gateway import BrokerGateway
from src.brokergateway.models import ExecutionFill
from src.brokergateway.states import OrderSide, OrderState, OrderType, UncertaintyResolutionStatus
from src.capitalreservation.models import ReservationOutcome, ReservationStatus
from src.capitalreservation.service import CapitalReservationService
from src.core.exceptions import AlgoLabSecurityError
from src.core.session_policy import (
    ENTRY_CUTOFF_TIME,
    SESSION_HARD_CLOSE_TIME,
    SESSION_OPEN_TIME,
    SQUAREOFF_TRIGGER_TIME,
    SessionPolicy,
)
from src.eodreconciler.models import PositionBalance
from src.eodreconciler.reconciler import EODReconciler


class TestWorkstreamBOMSComprehensiveLifecycle:
    """Rigorous tests covering all states: submitted, open, partial-fill,
    cancel-requested, cancel-unknown, confirmed cancellation, rejection,
    duplicate events, and late fills."""

    @pytest.fixture
    def gateway(self):
        return BrokerGateway()

    def test_lifecycle_submitted_to_open_to_filled(self, gateway):
        order = gateway.create_order(
            order_id="ORD-LIFECYCLE-01",
            idempotency_key="IDEM-LIFECYCLE-01",
            strategy_id="STRAT_ORB",
            symbol="INFY",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=50,
            price=Decimal("1500.00"),
            venue="PAPER",
            is_risk_approved=True,
        )
        assert order.state == OrderState.RISK_RESERVED

        # SUBMITTED
        gateway.transition_state(order.order_id, OrderState.SUBMITTED, "Sent to simulator")
        assert order.state == OrderState.SUBMITTED

        # OPEN
        gateway.transition_state(order.order_id, OrderState.OPEN, "Working on simulator book")
        assert order.state == OrderState.OPEN

        # FILLED
        fill = ExecutionFill(
            fill_id="FILL-LIFECYCLE-01",
            order_id=order.order_id,
            symbol="INFY",
            side=OrderSide.BUY,
            quantity=50,
            price=Decimal("1500.00"),
        )
        gateway.process_fill(fill)
        assert order.state == OrderState.FILLED
        assert order.filled_quantity == 50
        assert order.remaining_quantity == 0

    def test_cancel_requested_is_not_confused_with_cancelled(self, gateway):
        """Mandatory verification: Cancel requested is a pending intent, NOT confirmed cancellation."""
        order = gateway.create_order(
            order_id="ORD-CANCEL-REQ-01",
            idempotency_key="IDEM-CANCEL-REQ-01",
            strategy_id="STRAT_ORB",
            symbol="TCS",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=20,
            price=Decimal("3500.00"),
            venue="PAPER",
            is_risk_approved=True,
        )
        gateway.transition_state(order.order_id, OrderState.SUBMITTED, "Dispatched")
        gateway.transition_state(order.order_id, OrderState.OPEN, "Open on book")

        # Client requests cancellation
        gateway.request_cancellation(order.order_id, "User requested cancellation")
        assert order.state == OrderState.CANCEL_REQUESTED
        # Order is NOT confirmed cancelled yet!
        assert order.state != OrderState.CANCELLED

        # A fill can still arrive while cancellation is pending at venue
        fill = ExecutionFill(
            fill_id="FILL-RACE-01",
            order_id=order.order_id,
            symbol="TCS",
            side=OrderSide.BUY,
            quantity=10,
            price=Decimal("3500.00"),
        )
        gateway.process_fill(fill)
        # Successfully transitioned to PARTIALLY_FILLED despite CANCEL_REQUESTED
        assert order.state == OrderState.PARTIALLY_FILLED
        assert order.filled_quantity == 10
        assert order.remaining_quantity == 10

        # Now venue confirms cancellation of the remaining 10 shares
        gateway.confirm_cancellation(order.order_id, "Venue confirmed cancellation of remaining balance")
        assert order.state == OrderState.CANCELLED
        assert order.filled_quantity == 10
        assert order.remaining_quantity == 10

    def test_cancel_unknown_authoritative_resolution(self, gateway):
        """Verifies network drops during cancellation and resolution via reconciliation."""
        order = gateway.create_order(
            order_id="ORD-TIMEOUT-01",
            idempotency_key="IDEM-TIMEOUT-01",
            strategy_id="STRAT_VWAP",
            symbol="RELIANCE",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10,
            price=Decimal("2800.00"),
            venue="PAPER",
            is_risk_approved=True,
        )
        gateway.transition_state(order.order_id, OrderState.SUBMITTED, "Dispatched")
        gateway.transition_state(order.order_id, OrderState.OPEN, "Active")
        gateway.request_cancellation(order.order_id, "Cancel requested")
        gateway.handle_cancellation_timeout(order.order_id, "Connection reset by peer")
        assert order.state == OrderState.CANCEL_UNKNOWN

        # Reconcile: venue confirmed cancelled
        gateway.resolve_uncertain_order(order.order_id, UncertaintyResolutionStatus.CONFIRMED_AT_VENUE)
        assert order.state == OrderState.CANCELLED

    def test_rejection_from_submitted(self, gateway):
        order = gateway.create_order(
            order_id="ORD-REJECT-01",
            idempotency_key="IDEM-REJECT-01",
            strategy_id="STRAT_SMA",
            symbol="INFY",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10,
            price=Decimal("1500.00"),
            venue="PAPER",
            is_risk_approved=True,
        )
        gateway.transition_state(order.order_id, OrderState.SUBMITTED, "Dispatched")
        gateway.transition_state(order.order_id, OrderState.REJECTED, "Circuit limit reached")
        assert order.state == OrderState.REJECTED

    def test_duplicate_submission_and_fill_events(self, gateway):
        """Verifies strict idempotency for duplicate order creations and duplicate fill messages."""
        order1 = gateway.create_order(
            order_id="ORD-IDEM-01",
            idempotency_key="KEY-IDEM-UNIQUE",
            strategy_id="STRAT_MOMENTUM",
            symbol="HDFCBANK",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10,
            price=Decimal("1600.00"),
            venue="PAPER",
            is_risk_approved=True,
        )
        # Duplicate order intent
        order2 = gateway.create_order(
            order_id="ORD-IDEM-01-DUP",
            idempotency_key="KEY-IDEM-UNIQUE",
            strategy_id="STRAT_MOMENTUM",
            symbol="HDFCBANK",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10,
            price=Decimal("1600.00"),
            venue="PAPER",
            is_risk_approved=True,
        )
        assert order1.order_id == order2.order_id
        assert len(gateway.orders) == 1

        gateway.transition_state(order1.order_id, OrderState.SUBMITTED, "Dispatched")
        gateway.transition_state(order1.order_id, OrderState.OPEN, "Open")

        fill = ExecutionFill(
            fill_id="FILL-DUP-01",
            order_id=order1.order_id,
            symbol="HDFCBANK",
            side=OrderSide.BUY,
            quantity=5,
            price=Decimal("1600.00"),
        )
        # First fill processing
        gateway.process_fill(fill)
        assert order1.filled_quantity == 5
        assert order1.state == OrderState.PARTIALLY_FILLED

        # Duplicate fill processing
        gateway.process_fill(fill)
        # Must not double-count
        assert order1.filled_quantity == 5
        assert len(order1.fills) == 1

    def test_late_fill_after_cancellation(self, gateway):
        """Verifies that late fills arriving after cancellation are accepted into ledger with warning."""
        order = gateway.create_order(
            order_id="ORD-LATE-01",
            idempotency_key="IDEM-LATE-01",
            strategy_id="STRAT_ORB",
            symbol="SBIN",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10,
            price=Decimal("800.00"),
            venue="PAPER",
            is_risk_approved=True,
        )
        gateway.transition_state(order.order_id, OrderState.SUBMITTED, "Dispatched")
        gateway.transition_state(order.order_id, OrderState.OPEN, "Open")
        gateway.confirm_cancellation(order.order_id, "Cancelled by user")
        assert order.state == OrderState.CANCELLED

        # Late fill arrives after cancel
        late_fill = ExecutionFill(
            fill_id="FILL-LATE-01",
            order_id=order.order_id,
            symbol="SBIN",
            side=OrderSide.BUY,
            quantity=10,
            price=Decimal("800.00"),
        )
        gateway.process_fill(late_fill)
        # Fills list records the fill for ledger audit
        assert len(order.fills) == 1
        assert order.filled_quantity == 10
        assert order.state == OrderState.FILLED


class TestWorkstreamCCapitalAccounting:
    """Verifies capital reservations, releases, partial consumption, and balance reconciliation."""

    @pytest.fixture
    def service(self):
        return CapitalReservationService()

    def test_partial_fill_adjusts_reservation_and_frees_cash(self, service):
        """Tests that when an order partially fills, only the filled amount is consumed,
        and the unfulfilled reserved cash is freed immediately."""
        account_id = "ACC-PARTIAL-01"
        available_funds = Decimal("100000.00")

        # Request 100 shares @ 500 = 50,000 reserved
        res = service.reserve_capital(
            reservation_id="RES-PARTIAL-01",
            account_id=account_id,
            idempotency_key="KEY-PARTIAL-01",
            strategy_id="STRAT_ORB",
            symbol="TATAMOTORS",
            quantity=100,
            price=Decimal("500.00"),
            get_available_capital=lambda acc: available_funds,
        )
        assert res["outcome"] == ReservationOutcome.RESERVED
        assert res["reserved_cash"] == Decimal("50000.00")

        # 40 shares fill @ 500 = 20,000 consumed
        actual_consumed = Decimal("20000.00")
        consume_res = service.consume_partial("RES-PARTIAL-01", actual_consumed)

        assert consume_res["outcome"] == ReservationOutcome.CONSUMED
        assert consume_res["consumed_cash"] == Decimal("20000.00")
        assert consume_res["released_cash"] == Decimal("30000.00")

    def test_full_fill_consume_and_reconciliation(self, service):
        account_id = "ACC-FULL-01"
        available_funds = Decimal("100000.00")
        res = service.reserve_capital(
            reservation_id="RES-FULL-01",
            account_id=account_id,
            idempotency_key="KEY-FULL-01",
            strategy_id="STRAT_SMA",
            symbol="INFY",
            quantity=10,
            price=Decimal("1500.00"),
            get_available_capital=lambda acc: available_funds,
        )
        assert res["outcome"] == ReservationOutcome.RESERVED
        assert res["reserved_cash"] == Decimal("15000.00")

        outcome = service.consume_capital("RES-FULL-01")
        assert outcome == ReservationOutcome.CONSUMED

        # Duplicate consume replay is idempotent
        outcome_dup = service.consume_capital("RES-FULL-01")
        assert outcome_dup == ReservationOutcome.IDEMPOTENT_REPLAY

    def test_cancellation_and_rejection_release_without_leaks(self, service):
        account_id = "ACC-CANCEL-01"
        available_funds = Decimal("50000.00")
        res = service.reserve_capital(
            reservation_id="RES-CANCEL-01",
            account_id=account_id,
            idempotency_key="KEY-CANCEL-01",
            strategy_id="STRAT_VWAP",
            symbol="TCS",
            quantity=5,
            price=Decimal("3500.00"),
            get_available_capital=lambda acc: available_funds,
        )
        assert res["outcome"] == ReservationOutcome.RESERVED
        assert res["reserved_cash"] == Decimal("17500.00")

        # Order rejected or cancelled at venue -> release capital
        outcome = service.release_capital("RES-CANCEL-01")
        assert outcome == ReservationOutcome.RELEASED

        # Duplicate release is idempotent, preventing double release
        outcome_dup = service.release_capital("RES-CANCEL-01")
        assert outcome_dup == ReservationOutcome.IDEMPOTENT_REPLAY

    def test_duplicate_reservation_idempotency_and_conflict(self, service):
        account_id = "ACC-IDEM-01"
        available_funds = Decimal("50000.00")
        res1 = service.reserve_capital(
            reservation_id="RES-IDEM-01",
            account_id=account_id,
            idempotency_key="KEY-IDEM-MATCH",
            strategy_id="STRAT_ORB",
            symbol="RELIANCE",
            quantity=10,
            price=Decimal("2500.00"),
            get_available_capital=lambda acc: available_funds,
        )
        assert res1["outcome"] == ReservationOutcome.RESERVED

        # Replay with same parameters -> IDEMPOTENT_REPLAY
        res2 = service.reserve_capital(
            reservation_id="RES-IDEM-01-DUP",
            account_id=account_id,
            idempotency_key="KEY-IDEM-MATCH",
            strategy_id="STRAT_ORB",
            symbol="RELIANCE",
            quantity=10,
            price=Decimal("2500.00"),
            get_available_capital=lambda acc: available_funds,
        )
        assert res2["outcome"] == ReservationOutcome.IDEMPOTENT_REPLAY

        # Conflict: same idempotency key with conflicting notional
        res3 = service.reserve_capital(
            reservation_id="RES-IDEM-01-CONFLICT",
            account_id=account_id,
            idempotency_key="KEY-IDEM-MATCH",
            strategy_id="STRAT_ORB",
            symbol="RELIANCE",
            quantity=20,
            price=Decimal("2500.00"),
            get_available_capital=lambda acc: available_funds,
        )
        assert res3["outcome"] == ReservationOutcome.CONFLICT

    def test_double_entry_reconciliation_with_partial_fill(self):
        """Verifies exact conservation of cash, position cost basis, fees, and PnL."""
        reconciler = EODReconciler()
        initial_capital = Decimal("100000.00")

        # Buy 50 shares @ 1000. Total cost = 50,000
        # Friction = 50.00 fees + 10.00 slippage = 60.00
        # Cash left = 100,000 - 50,000 - 60 = 49,940.00
        # Open position: 50 shares @ 1000 = 50,000 cost basis
        # Realized PnL = 0
        positions = {
            "INFY": PositionBalance(symbol="INFY", quantity=50, cost_basis=Decimal("50000.00"))
        }
        fills = [
            ExecutionFill(
                fill_id="F1", order_id="O1", symbol="INFY", side=OrderSide.BUY,
                quantity=50, price=Decimal("1000.00"), transaction_cost=Decimal("50.00"), slippage_cost=Decimal("10.00")
            )
        ]

        report = reconciler.reconcile_session(
            session_id="SESS-01",
            initial_capital=initial_capital,
            ending_cash=Decimal("49940.00"),
            orders=[],
            fills=fills,
            positions=positions,
            gross_realized_pnl=Decimal("0.0"),
            total_fees_paid=Decimal("50.00"),
            total_slippage_paid=Decimal("10.00"),
        )
        assert report.is_balanced is True
        assert len(report.discrepancies) == 0


class TestWorkstreamESessionPolicyAndDiscrepancyResolution:
    """Verifies that the 15:15 vs 15:25 exit discrepancy is cleanly resolved with one institutional policy."""

    def test_milestones_chronology(self):
        assert SESSION_OPEN_TIME < ENTRY_CUTOFF_TIME
        assert ENTRY_CUTOFF_TIME < SQUAREOFF_TRIGGER_TIME
        assert SQUAREOFF_TRIGGER_TIME < SESSION_HARD_CLOSE_TIME

    def test_entry_cutoff_blocks_new_orders_after_1445(self):
        # 14:40 IST is allowed
        assert SessionPolicy.can_enter_new_trade("2026-10-19T14:40:00+05:30") is True

        # 14:45 IST is blocked for new entries
        assert SessionPolicy.can_enter_new_trade("2026-10-19T14:45:00+05:30") is False

        with pytest.raises(AlgoLabSecurityError, match="entry cutoff"):
            SessionPolicy.validate_order_entry_time("2026-10-19T14:50:00+05:30", symbol="RELIANCE")

    def test_squareoff_trigger_at_1515(self):
        # 15:14 IST is before square-off
        assert SessionPolicy.should_trigger_squareoff("2026-10-19T15:14:00+05:30") is False

        # 15:15 IST triggers square-off sequence
        assert SessionPolicy.should_trigger_squareoff("2026-10-19T15:15:00+05:30") is True

    def test_hard_close_at_1525_candle(self):
        # 15:20 IST is during square-off sequence
        assert SessionPolicy.is_hard_close_bar("2026-10-19T15:20:00+05:30") is False

        # 15:25 IST is the terminal 5-min candle (15:25 - 15:30)
        assert SessionPolicy.is_hard_close_bar("2026-10-19T15:25:00+05:30") is True

    def test_utc_to_ist_conversion(self):
        # 03:45 UTC is 09:15 IST
        dt_ist = SessionPolicy.to_ist("2026-10-19T03:45:00Z")
        assert dt_ist.hour == 9
        assert dt_ist.minute == 15
