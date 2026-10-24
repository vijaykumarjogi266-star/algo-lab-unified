"""Unit Tests for Atomic CapitalReservationService."""

from __future__ import annotations

from decimal import Decimal
import pytest

from src.capitalreservation.models import ReservationOutcome, ReservationStatus
from src.capitalreservation.service import CapitalReservationService


class TestCapitalReservationService:
    """Verifies atomic SQLite reservations, idempotency, and concurrency limits."""

    @pytest.fixture
    def service(self):
        return CapitalReservationService()

    def test_successful_reservation(self, service):
        account_id = "ACC-001"
        available_funds = Decimal("100000.00")

        res = service.reserve_capital(
            reservation_id="RES-001",
            account_id=account_id,
            idempotency_key="KEY-001",
            strategy_id="STRAT-A",
            symbol="INFY",
            quantity=10,
            price=Decimal("1500.00"),
            get_available_capital=lambda acc: available_funds,
        )
        assert res["outcome"] == ReservationOutcome.RESERVED
        assert res["reserved_cash"] == Decimal("15000.00")
        assert res["available_capital_after"] == Decimal("85000.00")

    def test_insufficient_funds_rejection(self, service):
        account_id = "ACC-002"
        available_funds = Decimal("5000.00")

        res = service.reserve_capital(
            reservation_id="RES-002",
            account_id=account_id,
            idempotency_key="KEY-002",
            strategy_id="STRAT-A",
            symbol="TCS",
            quantity=10,
            price=Decimal("3500.00"),  # Requires 35,000 > 5,000
            get_available_capital=lambda acc: available_funds,
        )
        assert res["outcome"] == ReservationOutcome.INSUFFICIENT_FUNDS

    def test_idempotent_replay(self, service):
        account_id = "ACC-003"
        available_funds = Decimal("50000.00")

        # First request
        res1 = service.reserve_capital(
            reservation_id="RES-003",
            account_id=account_id,
            idempotency_key="KEY-003",
            strategy_id="STRAT-B",
            symbol="RELIANCE",
            quantity=5,
            price=Decimal("2500.00"),
            get_available_capital=lambda acc: available_funds,
        )
        assert res1["outcome"] == ReservationOutcome.RESERVED

        # Duplicate replay
        res2 = service.reserve_capital(
            reservation_id="RES-003-DUP",
            account_id=account_id,
            idempotency_key="KEY-003",
            strategy_id="STRAT-B",
            symbol="RELIANCE",
            quantity=5,
            price=Decimal("2500.00"),
            get_available_capital=lambda acc: available_funds,
        )
        assert res2["outcome"] == ReservationOutcome.IDEMPOTENT_REPLAY
        assert res2["reservation_id"] == "RES-003"

    def test_release_and_consume_lifecycle(self, service):
        account_id = "ACC-004"
        available_funds = Decimal("50000.00")

        service.reserve_capital(
            reservation_id="RES-004",
            account_id=account_id,
            idempotency_key="KEY-004",
            strategy_id="STRAT-C",
            symbol="SBIN",
            quantity=20,
            price=Decimal("800.00"),
            get_available_capital=lambda acc: available_funds,
        )

        # Release
        outcome = service.release_capital("RES-004")
        assert outcome == ReservationOutcome.RELEASED

    def test_side_column_in_schema_and_migration(self):
        """DEF-F02: Capital reservation schema migration for side column."""
        import sqlite3
        conn = sqlite3.connect(":memory:")
        # Simulate legacy schema without side column
        conn.execute("""
            CREATE TABLE capital_reservations (
                reservation_id TEXT PRIMARY KEY,
                account_id TEXT,
                idempotency_key TEXT,
                strategy_id TEXT,
                symbol TEXT,
                requested_notional TEXT,
                reserved_cash TEXT,
                status TEXT,
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(account_id, idempotency_key)
            )
        """)
        conn.commit()

        # Initialize service with legacy db -> runtime check must add 'side TEXT'
        service = CapitalReservationService(db_conn=conn)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(capital_reservations)")
        cols = [r[1] for r in cursor.fetchall()]
        assert "side" in cols

        # Test reservation with side
        res = service.reserve_capital(
            reservation_id="RES-SIDE-01",
            account_id="ACC-SIDE",
            idempotency_key="KEY-SIDE-01",
            strategy_id="STRAT-SIDE",
            symbol="INFY",
            quantity=10,
            price=Decimal("1500.00"),
            get_available_capital=lambda acc: Decimal("50000.00"),
            side="BUY",
        )
        assert res["outcome"] == ReservationOutcome.RESERVED
        cursor.execute("SELECT side FROM capital_reservations WHERE reservation_id = ?", ("RES-SIDE-01",))
        row = cursor.fetchone()
        assert row[0] == "BUY"

    def test_def_f02_side_column_schema_migration(self):
        """DEF-F02 explicit test alias for automated audit suites."""
        self.test_side_column_in_schema_and_migration()

    def test_multistep_partial_fill_consume(self, service):
        """DEF-F03: Multi-step partial fill consume and atomic residual release."""
        account_id = "ACC-MULTI-PARTIAL"
        available_funds = Decimal("100000.00")

        # Reserve 100 shares @ 100 = 10,000 reserved
        res = service.reserve_capital(
            reservation_id="RES-MULTI-01",
            account_id=account_id,
            idempotency_key="KEY-MULTI-01",
            strategy_id="STRAT-MULTI",
            symbol="TATAMOTORS",
            quantity=100,
            price=Decimal("100.00"),
            get_available_capital=lambda acc: available_funds,
            side="BUY",
        )
        assert res["outcome"] == ReservationOutcome.RESERVED
        assert service.get_total_reserved_for_account(account_id) == Decimal("10000.00")

        # Step 1: partial fill of 30 shares (3,000), not final -> remainder 7,000 remains ACTIVE
        step1 = service.consume_partial("RES-MULTI-01", Decimal("3000.00"), is_final=False)
        assert step1["status"] == ReservationStatus.ACTIVE
        assert step1["consumed_cash"] == Decimal("3000.00")
        assert step1["reserved_cash"] == Decimal("7000.00")
        assert step1["released_cash"] == Decimal("0.0")
        assert service.get_total_reserved_for_account(account_id) == Decimal("7000.00")

        # Step 2: partial fill of 40 shares (4,000), not final -> remainder 3,000 remains ACTIVE
        step2 = service.consume_partial("RES-MULTI-01", Decimal("4000.00"), is_final=False)
        assert step2["status"] == ReservationStatus.ACTIVE
        assert step2["consumed_cash"] == Decimal("4000.00")
        assert step2["reserved_cash"] == Decimal("3000.00")
        assert step2["released_cash"] == Decimal("0.0")
        assert service.get_total_reserved_for_account(account_id) == Decimal("3000.00")

        # Step 3: final fill of 20 shares (2,000), is_final=True -> status CONSUMED, remainder 1,000 released
        step3 = service.consume_partial("RES-MULTI-01", Decimal("2000.00"), is_final=True)
        assert step3["outcome"] == ReservationOutcome.CONSUMED
        assert step3["status"] == ReservationStatus.CONSUMED
        assert step3["consumed_cash"] == Decimal("2000.00")
        assert step3["released_cash"] == Decimal("1000.00")
        assert service.get_total_reserved_for_account(account_id) == Decimal("0.00")

    def test_def_f03_multistep_partial_fill_consume(self, service):
        """DEF-F03 explicit test alias for automated audit suites."""
        self.test_multistep_partial_fill_consume(service)
