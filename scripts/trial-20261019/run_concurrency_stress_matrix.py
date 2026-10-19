"""Phase K Option B: Advanced Concurrency, Race Condition & Crash Recovery Harness.

Executes under strict OS-level container isolation:
1. Multi-threaded capital reservation contention.
2. Concurrent fill vs cancellation races.
3. Submission and cancellation timeout uncertainties and reconciliation.
4. Process crash recovery and state hydration from SQLite.
"""

from __future__ import annotations

import concurrent.futures
from decimal import Decimal
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
from typing import Dict, List, Tuple

from src.brokergateway.gateway import BrokerGateway
from src.brokergateway.models import ExecutionFill, ExecutionOrder
from src.brokergateway.states import (
    OrderSide,
    OrderState,
    OrderType,
    UncertaintyResolutionStatus,
)
from src.capitalreservation.models import ReservationOutcome, ReservationStatus
from src.capitalreservation.service import CapitalReservationService
from src.core.logging import get_logger
from src.eodreconciler.models import PositionBalance
from src.eodreconciler.reconciler import EODReconciler

logger = get_logger("phase_k_option_b")


def test_concurrent_capital_reservation(db_path: Path) -> Tuple[bool, str]:
    """Tests multi-threaded capital contention with atomic SQLite serialization."""
    print("\n--- [Scenario 1] Multi-Threaded Capital Reservation Contention ---")
    initial_pool = Decimal("100000.00")
    reservation_amount = Decimal("20000.00")
    total_threads = 10  # Total demand: 200,000 against 100,000 capacity

    # Shared SQLite database with WAL mode for concurrency
    conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    service = CapitalReservationService(db_conn=conn)

    results: List[Dict] = []
    results_lock = threading.Lock()

    def get_avail(account: str) -> Decimal:
        return initial_pool

    def worker(worker_id: int):
        t_conn = sqlite3.connect(str(db_path), timeout=30.0)
        t_svc = CapitalReservationService(db_conn=t_conn)
        res = t_svc.reserve_capital(
            reservation_id=f"RES-CONC-{worker_id:03d}",
            account_id="PAPER_ACC_CONC",
            idempotency_key=f"IDEMP-CONC-{worker_id:03d}",
            strategy_id="STRAT_CONC",
            symbol="INFY",
            quantity=10,
            price=Decimal("2000.00"),  # 10 * 2000 = 20,000
            get_available_capital=get_avail,
        )
        t_conn.close()
        with results_lock:
            results.append((worker_id, res))

    with concurrent.futures.ThreadPoolExecutor(max_workers=total_threads) as executor:
        futures = [executor.submit(worker, i) for i in range(total_threads)]
        concurrent.futures.wait(futures)

    success_count = sum(1 for _, r in results if r.get("outcome") == ReservationOutcome.RESERVED)
    insufficient_count = sum(1 for _, r in results if r.get("outcome") == ReservationOutcome.INSUFFICIENT_FUNDS)

    cursor = conn.cursor()
    cursor.execute(
        "SELECT SUM(CAST(reserved_cash AS REAL)) FROM capital_reservations WHERE account_id = 'PAPER_ACC_CONC' AND status = 'ACTIVE'"
    )
    total_active = Decimal(str(cursor.fetchone()[0] or "0.0"))

    print(f"  Attempted: {total_threads} threads (Total Demand: INR {total_threads * reservation_amount})")
    print(f"  Available Pool: INR {initial_pool}")
    print(f"  Accepted Reservations: {success_count} (INR {total_active})")
    print(f"  Rejected (Insufficient Funds): {insufficient_count}")

    if success_count == 5 and insufficient_count == 5 and total_active == initial_pool:
        return True, f"PERFECT: Exactly 5 accepted, 5 rejected. Total active reserved == INR {total_active}"
    else:
        return False, f"FAIL: Accepted {success_count}, Rejected {insufficient_count}, Total Active {total_active}"


def test_concurrent_fill_cancel_race(db_path: Path) -> Tuple[bool, str]:
    """Tests race conditions between simultaneous cancellation requests and broker fills."""
    print("\n--- [Scenario 2] Concurrent Fill vs Cancellation Race ---")
    conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    gateway = BrokerGateway(db_conn=conn)

    order_count = 20
    orders: List[ExecutionOrder] = []

    for i in range(order_count):
        o = gateway.create_order(
            order_id=f"ORD-RACE-{i:03d}",
            idempotency_key=f"IDEMP-RACE-{i:03d}",
            strategy_id="STRAT_RACE",
            symbol="TCS",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10,
            price=Decimal("3500.00"),
            venue="PAPER",
            is_risk_approved=True,
        )
        gateway.transition_state(o.order_id, OrderState.RISK_RESERVED, "Risk approved")
        gateway.transition_state(o.order_id, OrderState.SUBMITTED, "Submitted to simulator")
        gateway.transition_state(o.order_id, OrderState.OPEN, "Working at venue")
        orders.append(o)

    race_lock = threading.Lock()
    final_states: Dict[str, str] = {}

    def cancel_worker(order_id: str):
        try:
            # Transition to CANCEL_REQUESTED
            gateway.transition_state(order_id, OrderState.CANCEL_REQUESTED, "Client requested cancellation")
            # If still CANCEL_REQUESTED, move to CANCELLED
            if gateway.orders[order_id].state == OrderState.CANCEL_REQUESTED:
                gateway.transition_state(order_id, OrderState.CANCELLED, "Venue confirmed cancellation")
        except ValueError:
            pass  # Order may already be filled (legal terminal state)

    def fill_worker(order_id: str):
        try:
            fill = ExecutionFill(
                fill_id=f"FILL-RACE-{order_id}",
                order_id=order_id,
                symbol="TCS",
                side=OrderSide.BUY,
                quantity=10,
                price=Decimal("3500.00"),
                transaction_cost=Decimal("15.50"),
                slippage_cost=Decimal("5.00"),
            )
            gateway.process_fill(fill)
        except ValueError:
            pass  # Order may already be cancelled

    with concurrent.futures.ThreadPoolExecutor(max_workers=order_count * 2) as executor:
        futures = []
        for o in orders:
            futures.append(executor.submit(cancel_worker, o.order_id))
            futures.append(executor.submit(fill_worker, o.order_id))
        concurrent.futures.wait(futures)

    filled_count = sum(1 for o in gateway.orders.values() if o.state == OrderState.FILLED)
    cancelled_count = sum(1 for o in gateway.orders.values() if o.state == OrderState.CANCELLED)
    other_count = sum(1 for o in gateway.orders.values() if o.state not in (OrderState.FILLED, OrderState.CANCELLED))

    print(f"  Orders Raced: {order_count}")
    print(f"  Resolved to FILLED: {filled_count}")
    print(f"  Resolved to CANCELLED: {cancelled_count}")
    print(f"  Orders Trapped in Non-Terminal State: {other_count}")

    if other_count == 0 and (filled_count + cancelled_count == order_count):
        return True, f"PERFECT: All {order_count} orders resolved cleanly to terminal states ({filled_count} FILLED, {cancelled_count} CANCELLED)."
    else:
        return False, f"FAIL: {other_count} orders remained in indeterminate state."


def test_submission_and_cancellation_uncertainty(db_path: Path) -> Tuple[bool, str]:
    """Tests timeout handling and authoritative status reconciliation."""
    print("\n--- [Scenario 3] Submission & Cancellation Uncertainty Resolution ---")
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    gateway = BrokerGateway(db_conn=conn)

    # 1. Order 1: Submission timeout -> CONFIRMED_AT_VENUE -> OPEN
    o1 = gateway.create_order(
        order_id="ORD-UNC-001",
        idempotency_key="IDEMP-UNC-001",
        strategy_id="STRAT_UNC",
        symbol="RELIANCE",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=5,
        price=Decimal("2500.00"),
        venue="PAPER",
        is_risk_approved=True,
    )
    gateway.transition_state(o1.order_id, OrderState.RISK_RESERVED, "Risk OK")
    gateway.handle_submission_timeout(o1.order_id, "HTTP 504 Gateway Timeout")
    assert gateway.orders[o1.order_id].state == OrderState.SUBMISSION_UNKNOWN
    gateway.resolve_uncertain_order(o1.order_id, UncertaintyResolutionStatus.CONFIRMED_AT_VENUE)
    assert gateway.orders[o1.order_id].state == OrderState.OPEN

    # 2. Order 2: Submission timeout -> ABSENT_AT_VENUE -> REJECTED
    o2 = gateway.create_order(
        order_id="ORD-UNC-002",
        idempotency_key="IDEMP-UNC-002",
        strategy_id="STRAT_UNC",
        symbol="RELIANCE",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=5,
        price=Decimal("2500.00"),
        venue="PAPER",
        is_risk_approved=True,
    )
    gateway.transition_state(o2.order_id, OrderState.RISK_RESERVED, "Risk OK")
    gateway.handle_submission_timeout(o2.order_id, "Socket reset")
    gateway.resolve_uncertain_order(o2.order_id, UncertaintyResolutionStatus.ABSENT_AT_VENUE)
    assert gateway.orders[o2.order_id].state == OrderState.REJECTED

    # 3. Order 3: Cancellation timeout -> CONFIRMED_AT_VENUE -> CANCELLED
    o3 = gateway.create_order(
        order_id="ORD-UNC-003",
        idempotency_key="IDEMP-UNC-003",
        strategy_id="STRAT_UNC",
        symbol="RELIANCE",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=5,
        price=Decimal("2500.00"),
        venue="PAPER",
        is_risk_approved=True,
    )
    gateway.transition_state(o3.order_id, OrderState.RISK_RESERVED, "Risk OK")
    gateway.transition_state(o3.order_id, OrderState.SUBMITTED, "Dispatched")
    gateway.transition_state(o3.order_id, OrderState.OPEN, "Open at venue")
    gateway.transition_state(o3.order_id, OrderState.CANCEL_REQUESTED, "Cancel sent")
    gateway.handle_cancellation_timeout(o3.order_id, "Cancel ack dropped")
    assert gateway.orders[o3.order_id].state == OrderState.CANCEL_UNKNOWN
    gateway.resolve_uncertain_order(o3.order_id, UncertaintyResolutionStatus.CONFIRMED_AT_VENUE)
    assert gateway.orders[o3.order_id].state == OrderState.CANCELLED

    # 4. Order 4: Cancellation timeout -> ABSENT_AT_VENUE -> OPEN
    o4 = gateway.create_order(
        order_id="ORD-UNC-004",
        idempotency_key="IDEMP-UNC-004",
        strategy_id="STRAT_UNC",
        symbol="RELIANCE",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=5,
        price=Decimal("2500.00"),
        venue="PAPER",
        is_risk_approved=True,
    )
    gateway.transition_state(o4.order_id, OrderState.RISK_RESERVED, "Risk OK")
    gateway.transition_state(o4.order_id, OrderState.SUBMITTED, "Dispatched")
    gateway.transition_state(o4.order_id, OrderState.OPEN, "Open at venue")
    gateway.transition_state(o4.order_id, OrderState.CANCEL_REQUESTED, "Cancel sent")
    gateway.handle_cancellation_timeout(o4.order_id, "Cancel ack dropped")
    gateway.resolve_uncertain_order(o4.order_id, UncertaintyResolutionStatus.ABSENT_AT_VENUE)
    assert gateway.orders[o4.order_id].state == OrderState.OPEN

    print("  [✓] SUBMISSION_UNKNOWN -> CONFIRMED_AT_VENUE -> OPEN verified.")
    print("  [✓] SUBMISSION_UNKNOWN -> ABSENT_AT_VENUE    -> REJECTED verified.")
    print("  [✓] CANCEL_UNKNOWN     -> CONFIRMED_AT_VENUE -> CANCELLED verified.")
    print("  [✓] CANCEL_UNKNOWN     -> ABSENT_AT_VENUE    -> OPEN verified.")
    return True, "PERFECT: All timeout and uncertainty transitions resolved deterministically."


def test_crash_recovery_and_eod_reconciliation(db_path: Path) -> Tuple[bool, str]:
    """Tests persisting trading hot state to SQLite, simulating crash, and hydrating for EOD reconciliation."""
    print("\n--- [Scenario 4] SQLite Hot-State Hydration & Crash Recovery ---")
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    gateway = BrokerGateway(db_conn=conn)

    initial_capital = Decimal("200000.00")

    # Order 1: Buy 10 INFY @ 1500 -> FILLED
    o1 = gateway.create_order(
        order_id="ORD-CRASH-001",
        idempotency_key="IDEMP-CRASH-001",
        strategy_id="STRAT_CRASH",
        symbol="INFY",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=10,
        price=Decimal("1500.00"),
        venue="PAPER",
        is_risk_approved=True,
    )
    gateway.transition_state(o1.order_id, OrderState.RISK_RESERVED, "Risk OK")
    gateway.transition_state(o1.order_id, OrderState.SUBMITTED, "Dispatched")
    fill1 = ExecutionFill(
        fill_id="FILL-CRASH-001",
        order_id=o1.order_id,
        symbol="INFY",
        side=OrderSide.BUY,
        quantity=10,
        price=Decimal("1500.00"),
        transaction_cost=Decimal("7.50"),
        slippage_cost=Decimal("2.50"),
    )
    gateway.process_fill(fill1)

    # Order 2: Sell 10 INFY @ 1550 -> FILLED (Gross profit: 10 * 50 = +500)
    o2 = gateway.create_order(
        order_id="ORD-CRASH-002",
        idempotency_key="IDEMP-CRASH-002",
        strategy_id="STRAT_CRASH",
        symbol="INFY",
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        quantity=10,
        price=Decimal("1550.00"),
        venue="PAPER",
        is_risk_approved=True,
    )
    gateway.transition_state(o2.order_id, OrderState.RISK_RESERVED, "Risk OK")
    gateway.transition_state(o2.order_id, OrderState.SUBMITTED, "Dispatched")
    fill2 = ExecutionFill(
        fill_id="FILL-CRASH-002",
        order_id=o2.order_id,
        symbol="INFY",
        side=OrderSide.SELL,
        quantity=10,
        price=Decimal("1550.00"),
        transaction_cost=Decimal("7.75"),
        slippage_cost=Decimal("2.25"),
    )
    gateway.process_fill(fill2)

    # Calculate financial parameters
    total_fees = Decimal("7.50") + Decimal("7.75")       # 15.25
    total_slippage = Decimal("2.50") + Decimal("2.25")   # 4.75
    gross_pnl = Decimal("500.00")
    net_pnl = gross_pnl - total_fees - total_slippage    # 500 - 20 = +480.00
    ending_cash = initial_capital + net_pnl              # 200,480.00

    # SIMULATE ABRUPT CRASH: Discard in-memory gateway & close connection
    conn.close()
    del gateway
    print("  [!] Simulated Process Crash: In-memory gateway instance discarded.")

    # RECOVERY PHASE: Reconnect to SQLite and rehydrate records
    recovery_conn = sqlite3.connect(str(db_path))
    cursor = recovery_conn.cursor()

    cursor.execute("SELECT order_id, symbol, side, quantity, state FROM orders")
    hydrated_orders_rows = cursor.fetchall()
    assert len(hydrated_orders_rows) == 2, f"Expected 2 orders, got {len(hydrated_orders_rows)}"

    cursor.execute("SELECT fill_id, order_id, symbol, side, quantity, price, transaction_cost, slippage_cost FROM fills")
    hydrated_fills_rows = cursor.fetchall()
    assert len(hydrated_fills_rows) == 2, f"Expected 2 fills, got {len(hydrated_fills_rows)}"

    recovered_fills = [
        ExecutionFill(
            fill_id=r[0],
            order_id=r[1],
            symbol=r[2],
            side=OrderSide(r[3]),
            quantity=r[4],
            price=Decimal(str(r[5])),
            transaction_cost=Decimal(str(r[6])),
            slippage_cost=Decimal(str(r[7])),
        )
        for r in hydrated_fills_rows
    ]

    # Double-entry balance sheet audit on recovered state
    reconciler = EODReconciler()
    report = reconciler.reconcile_session(
        session_id="CRASH-RECOVERY-SESS-01",
        initial_capital=initial_capital,
        ending_cash=ending_cash,
        orders=[],  # Reconciler verifies fills against balance sheet
        fills=recovered_fills,
        positions={},  # Intraday flat
        gross_realized_pnl=gross_pnl,
        total_fees_paid=total_fees,
        total_slippage_paid=total_slippage,
    )

    print(f"  Recovered Orders: {len(hydrated_orders_rows)}/2")
    print(f"  Recovered Fills:  {len(recovered_fills)}/2")
    print(f"  Double-Entry Conservation: {report.is_balanced}")
    print(f"  Discrepancies: {report.discrepancies}")

    if report.is_balanced and len(report.discrepancies) == 0:
        return True, "PERFECT: Full order & fill state recovered from SQLite. Balance sheet perfectly balanced."
    else:
        return False, f"FAIL: Discrepancies detected: {report.discrepancies}"


def main():
    print("=" * 70)
    print("ALGO LAB UNIFIED: PHASE K OPTION B SIMULATION HARNESS")
    print("Container: Docker --network none | Target: Concurrency, Races & Recovery")
    print("=" * 70)

    db_dir = Path("/tmp/option_b_test_dbs")
    db_dir.mkdir(parents=True, exist_ok=True)

    scenarios = [
        ("Scenario 1: Capital Contention", test_concurrent_capital_reservation, db_dir / "conc.db"),
        ("Scenario 2: Fill vs Cancel Race", test_concurrent_fill_cancel_race, db_dir / "race.db"),
        ("Scenario 3: Timeout Uncertainties", test_submission_and_cancellation_uncertainty, db_dir / "unc.db"),
        ("Scenario 4: Crash & Recovery", test_crash_recovery_and_eod_reconciliation, db_dir / "crash.db"),
    ]

    all_passed = True
    summary = []

    for name, test_func, db_path in scenarios:
        if db_path.exists():
            db_path.unlink()
        try:
            ok, msg = test_func(db_path)
            if ok:
                summary.append((name, "PASS", msg))
                print(f"[✓ PASS] {name}: {msg}")
            else:
                summary.append((name, "FAIL", msg))
                print(f"[✗ FAIL] {name}: {msg}")
                all_passed = False
        except Exception as e:
            summary.append((name, "ERROR", str(e)))
            print(f"[! ERROR] {name}: {e}")
            all_passed = False

    print("\n" + "=" * 70)
    print("OPTION B SIMULATION RESULTS SUMMARY")
    print("=" * 70)
    for name, status, msg in summary:
        print(f"  [{status}] {name}")
        print(f"         {msg}")
    print("=" * 70)

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
