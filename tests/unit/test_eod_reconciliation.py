"""Unit Tests for EOD Double-Entry Balance Sheet Reconciler."""

from __future__ import annotations

from decimal import Decimal
import pytest

from src.brokergateway.models import ExecutionFill
from src.brokergateway.states import OrderSide
from src.eodreconciler.models import PositionBalance
from src.eodreconciler.reconciler import EODReconciler


class TestEODReconciler:
    """Verifies double-entry accounting conservation, fee summation, and position symmetry."""

    @pytest.fixture
    def reconciler(self):
        return EODReconciler()

    def test_perfectly_balanced_session(self, reconciler):
        # Initial Capital: 100,000
        # Buy 10 INFY @ 1,500 = Cost Basis 15,000
        # Fees: 20, Slippage: 5 -> Total Friction: 25
        # Ending Cash: 84,975
        # Gross Realized P&L: 0
        # Equation: Ending Cash (84,975) + Cost Basis (15,000) + Friction (25) == Initial Capital (100,000) + Gross PnL (0)
        # Left (100,000) == Right (100,000) -> BALANCED!
        initial_capital = Decimal("100000.00")
        ending_cash = Decimal("84975.00")
        gross_pnl = Decimal("0.00")
        fees = Decimal("20.00")
        slippage = Decimal("5.00")

        fill = ExecutionFill(
            fill_id="F-1",
            order_id="O-1",
            symbol="INFY",
            side=OrderSide.BUY,
            quantity=10,
            price=Decimal("1500.00"),
            transaction_cost=fees,
            slippage_cost=slippage,
        )

        positions = {
            "INFY": PositionBalance(symbol="INFY", quantity=10, cost_basis=Decimal("15000.00")),
        }

        report = reconciler.reconcile_session(
            session_id="SESS-001",
            initial_capital=initial_capital,
            ending_cash=ending_cash,
            orders=[],
            fills=[fill],
            positions=positions,
            gross_realized_pnl=gross_pnl,
            total_fees_paid=fees,
            total_slippage_paid=slippage,
        )

        assert report.is_balanced is True
        assert len(report.discrepancies) == 0
        assert report.imbalance_amount == Decimal("0.00")
        assert report.net_realized_pnl == Decimal("-25.00")

    def test_detect_balance_sheet_imbalance(self, reconciler):
        # Artificially alter ending cash by 100 INR
        initial_capital = Decimal("100000.00")
        ending_cash = Decimal("84875.00")  # Missing 100 INR
        fees = Decimal("20.00")
        slippage = Decimal("5.00")

        fill = ExecutionFill(
            fill_id="F-2",
            order_id="O-2",
            symbol="INFY",
            side=OrderSide.BUY,
            quantity=10,
            price=Decimal("1500.00"),
            transaction_cost=fees,
            slippage_cost=slippage,
        )

        positions = {
            "INFY": PositionBalance(symbol="INFY", quantity=10, cost_basis=Decimal("15000.00")),
        }

        report = reconciler.reconcile_session(
            session_id="SESS-002",
            initial_capital=initial_capital,
            ending_cash=ending_cash,
            orders=[],
            fills=[fill],
            positions=positions,
            gross_realized_pnl=Decimal("0.00"),
            total_fees_paid=fees,
            total_slippage_paid=slippage,
        )

        assert report.is_balanced is False
        assert len(report.discrepancies) == 1
        assert "Balance sheet conservation violation" in report.discrepancies[0]
        assert report.imbalance_amount == Decimal("100.00")
