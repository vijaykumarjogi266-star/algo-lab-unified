"""Reproduction Tests for Remediation Mission 001.

Validates and documents the 8 reported defect areas before implementing fixes:
1. Short-sale accounting & EOD reconciliation
2. Order persistence and restart recovery
3. Fill atomicity & retry consumption
4. Boundary validation (negative/zero qty, price, symbol/side mismatch)
5. Independent reconciliation (orphan fills, duplicate fills, cumulative discrepancies)
6. Portfolio risk & mark-to-market unrealized drawdown
7. Fail-closed configuration
8. CLI entry points & default path integrity
"""

import importlib
from decimal import Decimal
from pathlib import Path
import sqlite3
import pytest

from src.brokergateway.gateway import BrokerGateway
from src.brokergateway.models import ExecutionFill, ExecutionOrder
from src.brokergateway.states import OrderSide, OrderState, OrderType
from src.capitalreservation.models import ReservationOutcome
from src.capitalreservation.service import CapitalReservationService
from src.core.exceptions import SecurityInvariantViolation
from src.core.safety_invariants import assert_paper_only_invariants
from src.eodreconciler.models import PositionBalance
from src.eodreconciler.reconciler import EODReconciler


class TestDefect1ShortSaleAccounting:
    def test_short_position_eod_reconciliation_symmetry(self):
        """Reproduce: EODReconciler fails symmetry check on an open short position."""
        reconciler = EODReconciler()
        
        # 1 sell order for 10 shares
        order = ExecutionOrder(
            order_id="ORD-S1",
            idempotency_key="IDEM-S1",
            strategy_id="STRAT_TEST",
            symbol="INFY",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=10,
            price=Decimal("1500.00"),
            state=OrderState.FILLED,
            filled_quantity=10,
            remaining_quantity=0,
        )
        fill = ExecutionFill(
            fill_id="FILL-S1",
            order_id="ORD-S1",
            symbol="INFY",
            side=OrderSide.SELL,
            quantity=10,
            price=Decimal("1500.00"),
            transaction_cost=Decimal("15.00"),
            slippage_cost=Decimal("5.00"),
        )
        
        # Short position open: net delta from fills is -10
        # PositionBalance definition in models.py: symbol, quantity, entry_price, cost_basis
        positions = {
            "INFY": PositionBalance(
                symbol="INFY",
                quantity=-10,
                cost_basis=Decimal("15000.00"),
            )
        }
        
        report = reconciler.reconcile_session(
            session_id="SESS-001",
            initial_capital=Decimal("100000.00"),
            ending_cash=Decimal("114980.00"),  # Received 15000 - 20 fees
            orders=[order],
            fills=[fill],
            positions=positions,
            gross_realized_pnl=Decimal("0.00"),
            total_fees_paid=Decimal("15.00"),
            total_slippage_paid=Decimal("5.00"),
        )
        
        # Should be balanced without false discrepancy
        assert report.is_balanced, f"Short position reconciliation failed: {report.discrepancies}"


class TestDefect2OrderPersistenceAndRestartRecovery:
    def test_broker_gateway_recovers_state_on_restart(self, tmp_path):
        """Reproduce: BrokerGateway does not hydrate orders, idempotency, or fills from existing SQLite DB on restart."""
        db_path = str(tmp_path / "gateway_restart_test.db")
        conn1 = sqlite3.connect(db_path)
        gw1 = BrokerGateway(db_conn=conn1)
        
        # Create and fill order
        order = gw1.create_order(
            order_id="ORD-R1",
            idempotency_key="IDEM-R1",
            strategy_id="STRAT_1",
            symbol="RELIANCE",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=100,
            price=Decimal("2500.00"),
            venue="PAPER",
            is_risk_approved=True,
        )
        gw1.transition_state("ORD-R1", OrderState.SUBMITTED, "Submitted")
        gw1.process_fill(ExecutionFill(
            fill_id="FILL-R1",
            order_id="ORD-R1",
            symbol="RELIANCE",
            side=OrderSide.BUY,
            quantity=50,
            price=Decimal("2500.00"),
            transaction_cost=Decimal("10.00"),
            slippage_cost=Decimal("2.00"),
        ))
        conn1.close()

        # Simulate process restart by creating new gateway instance connecting to same DB
        conn2 = sqlite3.connect(db_path)
        gw2 = BrokerGateway(db_conn=conn2)
        
        # In unpatched code, gw2 has empty in-memory state:
        # 1. Orders should be recovered
        assert "ORD-R1" in gw2.orders, "Failed to recover order from persistent storage on restart"
        assert gw2.orders["ORD-R1"].filled_quantity == 50
        assert gw2.orders["ORD-R1"].remaining_quantity == 50
        
        # 2. Idempotency map should be recovered
        assert "IDEM-R1" in gw2.idempotency_map
        replay = gw2.create_order(
            order_id="ORD-R1-NEW",
            idempotency_key="IDEM-R1",
            strategy_id="STRAT_1",
            symbol="RELIANCE",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=100,
            price=Decimal("2500.00"),
            venue="PAPER",
            is_risk_approved=True,
        )
        assert replay.order_id == "ORD-R1", "Idempotent replay failed across restart"
        
        # 3. Processed fill IDs should be recovered
        assert "FILL-R1" in gw2.processed_fill_ids
        conn2.close()


class TestDefect3FillAtomicityAndRetryConsumption:
    def test_rejected_overfill_does_not_consume_fill_id(self):
        """Reproduce: An invalid overfill registers the fill_id in processed_fill_ids before failing, blocking corrected retry."""
        gw = BrokerGateway()
        gw.create_order(
            order_id="ORD-A1",
            idempotency_key="IDEM-A1",
            strategy_id="STRAT_1",
            symbol="TCS",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=10,
            price=Decimal("3500.00"),
            venue="PAPER",
            is_risk_approved=True,
        )
        gw.transition_state("ORD-A1", OrderState.SUBMITTED, "Submitted")
        
        # Erroneous overfill with fill_id "FILL-X"
        bad_fill = ExecutionFill(
            fill_id="FILL-X",
            order_id="ORD-A1",
            symbol="TCS",
            side=OrderSide.BUY,
            quantity=15,  # Overfills order of qty 10
            price=Decimal("3500.00"),
            transaction_cost=Decimal("10.00"),
            slippage_cost=Decimal("0.00"),
        )
        with pytest.raises(ValueError, match="overfill"):
            gw.process_fill(bad_fill)
            
        # Corrected retry with same fill_id and correct quantity
        corrected_fill = ExecutionFill(
            fill_id="FILL-X",
            order_id="ORD-A1",
            symbol="TCS",
            side=OrderSide.BUY,
            quantity=10,
            price=Decimal("3500.00"),
            transaction_cost=Decimal("10.00"),
            slippage_cost=Decimal("0.00"),
        )
        order = gw.process_fill(corrected_fill)
        assert order.filled_quantity == 10, f"Corrected fill was swallowed or failed: filled_qty={order.filled_quantity}"


class TestDefect4BoundaryValidation:
    def test_create_order_rejects_negative_or_zero_quantity(self):
        """Reproduce: create_order does not validate quantity > 0."""
        gw = BrokerGateway()
        with pytest.raises(ValueError, match="quantity.*greater than zero"):
            gw.create_order(
                order_id="ORD-NEG-1",
                idempotency_key="IDEM-NEG-1",
                strategy_id="STRAT_1",
                symbol="INFY",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=-5,
                price=Decimal("1500.00"),
                venue="PAPER",
                is_risk_approved=True,
            )

    def test_reserve_capital_rejects_negative_quantity_or_price(self):
        """Reproduce: reserve_capital does not validate quantity > 0 or price > 0, allowing negative reservations."""
        srv = CapitalReservationService()
        res = srv.reserve_capital(
            reservation_id="RES-NEG",
            account_id="ACC-1",
            idempotency_key="IDEM-NEG",
            strategy_id="STRAT_1",
            symbol="INFY",
            quantity=-10,
            price=Decimal("100.00"),
            get_available_capital=lambda acc: Decimal("10000.00"),
        )
        assert res["outcome"] == ReservationOutcome.REJECTED

    def test_process_fill_rejects_symbol_or_side_mismatch(self):
        """Reproduce: process_fill does not validate that fill.symbol and fill.side match the order."""
        gw = BrokerGateway()
        gw.create_order(
            order_id="ORD-SYM-1",
            idempotency_key="IDEM-SYM-1",
            strategy_id="STRAT_1",
            symbol="INFY",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=10,
            price=Decimal("1500.00"),
            venue="PAPER",
            is_risk_approved=True,
        )
        gw.transition_state("ORD-SYM-1", OrderState.SUBMITTED, "Submitted")
        
        # Mismatched symbol
        fill_wrong_sym = ExecutionFill(
            fill_id="FILL-WRONG-SYM",
            order_id="ORD-SYM-1",
            symbol="TCS",  # Mismatched symbol!
            side=OrderSide.BUY,
            quantity=10,
            price=Decimal("1500.00"),
            transaction_cost=Decimal("10.00"),
            slippage_cost=Decimal("0.00"),
        )
        with pytest.raises(ValueError, match="Symbol mismatch"):
            gw.process_fill(fill_wrong_sym)


class TestDefect5IndependentReconciliation:
    def test_eod_reconciler_detects_orphan_fill(self):
        """Reproduce: EODReconciler fails to detect an orphan fill with an unknown order_id."""
        reconciler = EODReconciler()
        order = ExecutionOrder(
            order_id="ORD-1",
            idempotency_key="IDEM-1",
            strategy_id="STRAT_1",
            symbol="INFY",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=10,
            price=Decimal("1500.00"),
            state=OrderState.FILLED,
            filled_quantity=10,
            remaining_quantity=0,
        )
        fill1 = ExecutionFill(
            fill_id="FILL-1",
            order_id="ORD-1",
            symbol="INFY",
            side=OrderSide.BUY,
            quantity=10,
            price=Decimal("1500.00"),
            transaction_cost=Decimal("10.00"),
            slippage_cost=Decimal("0.00"),
        )
        # Orphan fill:
        orphan_fill = ExecutionFill(
            fill_id="FILL-ORPHAN",
            order_id="ORD-NON-EXISTENT",
            symbol="INFY",
            side=OrderSide.BUY,
            quantity=5,
            price=Decimal("1500.00"),
            transaction_cost=Decimal("5.00"),
            slippage_cost=Decimal("0.00"),
        )
        report = reconciler.reconcile_session(
            session_id="SESS-ORPHAN",
            initial_capital=Decimal("100000.00"),
            ending_cash=Decimal("77485.00"),
            orders=[order],
            fills=[fill1, orphan_fill],
            positions={"INFY": PositionBalance("INFY", 15, Decimal("1500.00"), Decimal("22500.00"))},
            gross_realized_pnl=Decimal("0.00"),
            total_fees_paid=Decimal("15.00"),
            total_slippage_paid=Decimal("0.00"),
        )
        assert not report.is_balanced
        assert any("Orphan fill" in d or "unknown order" in d for d in report.discrepancies)

    def test_eod_reconciler_detects_duplicate_fill_id(self):
        """Reproduce: EODReconciler does not detect duplicate fill IDs."""
        reconciler = EODReconciler()
        order = ExecutionOrder(
            order_id="ORD-DUP",
            idempotency_key="IDEM-DUP",
            strategy_id="STRAT_1",
            symbol="INFY",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=10,
            price=Decimal("1500.00"),
            state=OrderState.FILLED,
            filled_quantity=10,
            remaining_quantity=0,
        )
        fill = ExecutionFill(
            fill_id="FILL-DUP-1",
            order_id="ORD-DUP",
            symbol="INFY",
            side=OrderSide.BUY,
            quantity=5,
            price=Decimal("1500.00"),
            transaction_cost=Decimal("5.00"),
            slippage_cost=Decimal("0.00"),
        )
        report = reconciler.reconcile_session(
            session_id="SESS-DUP",
            initial_capital=Decimal("100000.00"),
            ending_cash=Decimal("84990.00"),
            orders=[order],
            fills=[fill, fill],  # Duplicate fill injected
            positions={"INFY": PositionBalance("INFY", 10, Decimal("1500.00"), Decimal("15000.00"))},
            gross_realized_pnl=Decimal("0.00"),
            total_fees_paid=Decimal("10.00"),
            total_slippage_paid=Decimal("0.00"),
        )
        assert not report.is_balanced
        assert any("Duplicate fill" in d for d in report.discrepancies)


class TestDefect6PortfolioRisk:
    def test_equity_curve_and_drawdown_includes_unrealized_pnl(self):
        """Reproduce: HistoricalReplayEngine calculates equity curve using cash alone without open-position mark-to-market."""
        from src.simulation.engine import HistoricalReplayEngine
        from src.scanner.scanner import MarketBar
        from src.strategies.models import StrategySignal
        from src.strategies.base import BaseStrategy

        class DummyStrategy(BaseStrategy):
            def __init__(self):
                super().__init__("DUMMY")
            def evaluate(self, bar, history=None):
                if bar.timestamp_iso.endswith("09:15:00"):
                    return StrategySignal("SIG-1", "DUMMY", bar.symbol, OrderSide.BUY, Decimal("100.00"), Decimal("10.00"), Decimal("150.00"), Decimal("1.0"), bar.timestamp_iso)
                return None
            def validate_and_sanitize_parameters(self, params):
                return params

        engine = HistoricalReplayEngine(
            strategy=DummyStrategy(),
            initial_capital=Decimal("10000.00"),
            max_risk_per_trade_pct=Decimal("0.50"),  # 50% capital
        )
        # Bar 1: Entry at 100
        b1 = MarketBar("SBIN", "2026-10-19T09:15:00", Decimal("100.00"), Decimal("101.00"), Decimal("99.00"), Decimal("100.00"), 10000)
        # Bar 2: Huge crash to 50 while position is open
        b2 = MarketBar("SBIN", "2026-10-19T10:00:00", Decimal("50.00"), Decimal("51.00"), Decimal("49.00"), Decimal("50.00"), 10000)
        # Bar 3: Rebound to 100 and exit at 100
        b3 = MarketBar("SBIN", "2026-10-19T15:25:00", Decimal("100.00"), Decimal("100.00"), Decimal("99.00"), Decimal("100.00"), 10000)

        metrics = engine.run_replay({"SBIN": [b1, b2, b3]})
        # With MTM equity curve, intra-trade 50% drop on 20% allocated position produces ~10.0% drawdown,
        # even though it rebounded and exited at breakeven gross PnL.
        assert metrics.max_drawdown_pct >= Decimal("9.5"), f"Unrealized drawdown ignored! Reported max_dd={metrics.max_drawdown_pct}%"


class TestDefect7FailClosedConfiguration:
    def test_preflight_fails_closed_when_live_flag_set(self):
        """Verify: assert_paper_only_invariants raises SecurityInvariantViolation when ALLOW_LIVE_TRADING is True."""
        with pytest.raises(SecurityInvariantViolation):
            assert_paper_only_invariants({"ALLOW_LIVE_TRADING": "true"})


class TestDefect8CLIIntegrity:
    def test_cli_entry_points_resolve_to_existing_modules(self):
        """Reproduce: pyproject.toml defines algo-paper = src.cli.daemon:main which does not exist."""
        try:
            importlib.import_module("src.cli.daemon")
        except ModuleNotFoundError as e:
            pytest.fail(f"Declared CLI entry point 'src.cli.daemon' is missing: {e}")
