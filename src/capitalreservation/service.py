"""Atomic Capital Reservation Service with SQLite Serializable Locking.

Guarantees:
1. Atomic margin locking using SQLite BEGIN EXCLUSIVE transactions.
2. Idempotent reservation creation preventing duplicate capital commitments.
3. Concurrent request protection with automatic retry backoff.
4. Clear lifecycle semantics: RESERVE -> CONSUME (on fill) or RELEASE (on cancel/reject).
"""

from __future__ import annotations

from decimal import Decimal
import sqlite3
import time
from typing import Any, Callable, Dict, Optional

from src.capitalreservation.models import CapitalReservation, ReservationOutcome, ReservationStatus, utc_now_iso
from src.core.logging import get_logger

logger = get_logger(__name__)


class CapitalReservationService:
    """Atomic capital reservation engine for concurrent multi-strategy execution."""

    def __init__(self, db_conn: Optional[sqlite3.Connection] = None):
        self.conn = db_conn or sqlite3.connect(":memory:", timeout=10.0)
        self._init_db()

    def _init_db(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS capital_reservations (
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
        self.conn.commit()

    def reserve_capital(
        self,
        reservation_id: str,
        account_id: str,
        idempotency_key: str,
        strategy_id: str,
        symbol: str,
        quantity: int,
        price: Decimal,
        get_available_capital: Callable[[str], Decimal],
    ) -> Dict[str, Any]:
        """Reserves capital atomically using SQLite BEGIN EXCLUSIVE transaction."""
        if quantity <= 0:
            logger.warning("Reservation rejected: quantity %d must be greater than zero", quantity)
            return {
                "outcome": ReservationOutcome.REJECTED,
                "reason": f"Invalid quantity {quantity}: quantity must be greater than zero",
            }
        if price <= Decimal("0.0"):
            logger.warning("Reservation rejected: price %s must be greater than zero", price)
            return {
                "outcome": ReservationOutcome.REJECTED,
                "reason": f"Invalid price {price}: price must be greater than zero",
            }

        requested_notional = Decimal(str(quantity)) * price
        reserved_cash = requested_notional  # Full cash collateral assumption for paper intraday

        max_retries = 5
        base_delay = 0.05

        for attempt in range(max_retries):
            cursor = self.conn.cursor()
            try:
                cursor.execute("BEGIN EXCLUSIVE")

                # 1. Idempotency Check
                cursor.execute(
                    "SELECT reservation_id, requested_notional, reserved_cash, status FROM capital_reservations WHERE account_id = ? AND idempotency_key = ?",
                    (account_id, idempotency_key),
                )
                existing = cursor.fetchone()

                if existing:
                    cursor.execute("ROLLBACK")
                    old_notional = Decimal(str(existing[1]))
                    if abs(old_notional - requested_notional) > Decimal("0.001"):
                        return {
                            "outcome": ReservationOutcome.CONFLICT,
                            "reason": "Idempotency conflict: requested notional differs from existing record",
                        }
                    return {
                        "outcome": ReservationOutcome.IDEMPOTENT_REPLAY,
                        "reservation_id": existing[0],
                        "reserved_cash": Decimal(str(existing[2])),
                        "status": ReservationStatus(existing[3]),
                    }

                # 2. Check Available Capital
                base_capital = get_available_capital(account_id)
                cursor.execute(
                    "SELECT COALESCE(SUM(CAST(reserved_cash AS REAL)), 0.0) FROM capital_reservations WHERE account_id = ? AND status = ?",
                    (account_id, ReservationStatus.ACTIVE.value),
                )
                active_sum_row = cursor.fetchone()
                active_reserved = Decimal(str(active_sum_row[0])) if active_sum_row else Decimal("0.0")
                available_capital = base_capital - active_reserved

                if requested_notional > available_capital:
                    cursor.execute("ROLLBACK")
                    logger.warning(
                        "INSUFFICIENT FUNDS: Account %s requested %s, available %s (base %s - active %s)",
                        account_id, requested_notional, available_capital, base_capital, active_reserved,
                    )
                    return {
                        "outcome": ReservationOutcome.INSUFFICIENT_FUNDS,
                        "available_capital": available_capital,
                        "requested_notional": requested_notional,
                    }

                # 3. Create Reservation
                now_utc = utc_now_iso()
                cursor.execute(
                    "INSERT INTO capital_reservations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        reservation_id,
                        account_id,
                        idempotency_key,
                        strategy_id,
                        symbol,
                        str(requested_notional),
                        str(reserved_cash),
                        ReservationStatus.ACTIVE.value,
                        now_utc,
                        now_utc,
                    ),
                )
                self.conn.commit()
                logger.info(
                    "Capital reserved: reservation_id %s, account %s, amount %s, available after %s",
                    reservation_id, account_id, reserved_cash, (available_capital - reserved_cash),
                )
                return {
                    "outcome": ReservationOutcome.RESERVED,
                    "reservation_id": reservation_id,
                    "reserved_cash": reserved_cash,
                    "available_capital_after": (available_capital - reserved_cash),
                }

            except sqlite3.OperationalError as e:
                cursor.execute("ROLLBACK")
                if "locked" in str(e).lower() and attempt < max_retries - 1:
                    time.sleep(base_delay * (2 ** attempt))
                    continue
                raise

    def release_capital(self, reservation_id: str) -> ReservationOutcome:
        """Atomically releases a reservation back to available capital."""
        cursor = self.conn.cursor()
        cursor.execute("BEGIN EXCLUSIVE")
        cursor.execute(
            "SELECT status FROM capital_reservations WHERE reservation_id = ?",
            (reservation_id,),
        )
        row = cursor.fetchone()
        if not row:
            cursor.execute("ROLLBACK")
            return ReservationOutcome.NOT_FOUND

        current_status = ReservationStatus(row[0])
        if current_status == ReservationStatus.ACTIVE:
            now_utc = utc_now_iso()
            cursor.execute(
                "UPDATE capital_reservations SET status = ?, updated_at = ? WHERE reservation_id = ?",
                (ReservationStatus.RELEASED.value, now_utc, reservation_id),
            )
            self.conn.commit()
            logger.info("Reservation %s RELEASED back to available cash", reservation_id)
            return ReservationOutcome.RELEASED
        else:
            cursor.execute("ROLLBACK")
            return ReservationOutcome.IDEMPOTENT_REPLAY

    def consume_capital(self, reservation_id: str) -> ReservationOutcome:
        """Atomically transitions reservation to CONSUMED upon order fill."""
        cursor = self.conn.cursor()
        cursor.execute("BEGIN EXCLUSIVE")
        cursor.execute(
            "SELECT status FROM capital_reservations WHERE reservation_id = ?",
            (reservation_id,),
        )
        row = cursor.fetchone()
        if not row:
            cursor.execute("ROLLBACK")
            return ReservationOutcome.NOT_FOUND

        current_status = ReservationStatus(row[0])
        if current_status == ReservationStatus.ACTIVE:
            now_utc = utc_now_iso()
            cursor.execute(
                "UPDATE capital_reservations SET status = ?, updated_at = ? WHERE reservation_id = ?",
                (ReservationStatus.CONSUMED.value, now_utc, reservation_id),
            )
            self.conn.commit()
            logger.info("Reservation %s CONSUMED into filled position", reservation_id)
            return ReservationOutcome.CONSUMED
        else:
            cursor.execute("ROLLBACK")
            return ReservationOutcome.IDEMPOTENT_REPLAY

    def consume_partial(self, reservation_id: str, filled_notional: Decimal) -> Dict[str, Any]:
        """Atomically consumes the exact filled portion of a reservation and releases the remainder.

        Prevents margin lockup on partial fills while ensuring exact financial reconciliation.
        """
        if filled_notional < Decimal("0.0"):
            raise ValueError(f"Invalid filled_notional {filled_notional}: cannot be negative")

        cursor = self.conn.cursor()
        cursor.execute("BEGIN EXCLUSIVE")
        cursor.execute(
            "SELECT reserved_cash, status FROM capital_reservations WHERE reservation_id = ?",
            (reservation_id,),
        )
        row = cursor.fetchone()
        if not row:
            cursor.execute("ROLLBACK")
            return {"outcome": ReservationOutcome.NOT_FOUND}

        original_reserved = Decimal(str(row[0]))
        current_status = ReservationStatus(row[1])

        if current_status != ReservationStatus.ACTIVE:
            cursor.execute("ROLLBACK")
            return {
                "outcome": ReservationOutcome.IDEMPOTENT_REPLAY,
                "status": current_status,
                "consumed_cash": original_reserved,
                "released_cash": Decimal("0.0"),
            }

        consumed_cash = min(filled_notional, original_reserved)
        released_cash = max(Decimal("0.0"), original_reserved - consumed_cash)
        now_utc = utc_now_iso()

        cursor.execute(
            "UPDATE capital_reservations SET reserved_cash = ?, status = ?, updated_at = ? WHERE reservation_id = ?",
            (str(consumed_cash), ReservationStatus.CONSUMED.value, now_utc, reservation_id),
        )
        self.conn.commit()
        logger.info(
            "Reservation %s PARTIALLY CONSUMED: %s consumed, %s released back to cash",
            reservation_id, consumed_cash, released_cash,
        )
        return {
            "outcome": ReservationOutcome.CONSUMED,
            "reservation_id": reservation_id,
            "consumed_cash": consumed_cash,
            "released_cash": released_cash,
        }
