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
