"""End-of-Day Double-Entry Financial Reconciler.

Audits:
1. Order and fill count conservation.
2. Friction and fee summation (STT, NSE turnover, GST, slippage).
3. Position quantity symmetry (Sum of Buy fills - Sum of Sell fills == Position qty).
4. Balance sheet conservation equation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List

from src.brokergateway.models import ExecutionFill, ExecutionOrder
from src.eodreconciler.math import round_currency, verify_conservation_equation
from src.eodreconciler.models import EODReconciliationReport, PositionBalance
from src.core.logging import get_logger

logger = get_logger(__name__)


class EODReconciler:
    """Rigorous double-entry balance sheet reconciler."""

    def reconcile_session(
        self,
        session_id: str,
        initial_capital: Decimal,
        ending_cash: Decimal,
        orders: List[ExecutionOrder],
        fills: List[ExecutionFill],
        positions: Dict[str, PositionBalance],
        gross_realized_pnl: Decimal,
        total_fees_paid: Decimal,
        total_slippage_paid: Decimal,
    ) -> EODReconciliationReport:
        """Executes full mathematical reconciliation across cash, fills, and positions."""
        discrepancies: List[str] = []

        # 1. Order & Fill Count Verification and Fill Integrity
        order_count = len(orders)
        fill_count = len(fills)

        seen_fill_ids: set[str] = set()
        for f in fills:
            if f.fill_id in seen_fill_ids:
                discrepancies.append(f"Duplicate fill ID detected: {f.fill_id}")
            seen_fill_ids.add(f.fill_id)

        if orders:
            valid_order_ids = {o.order_id for o in orders}
            for f in fills:
                if f.order_id not in valid_order_ids:
                    discrepancies.append(
                        f"Orphan fill detected: Fill {f.fill_id} references unknown order {f.order_id}"
                    )

            # 1b. Order Cumulative Filled & Remaining Quantity Verification
            fills_by_order: Dict[str, int] = {}
            for f in fills:
                fills_by_order[f.order_id] = fills_by_order.get(f.order_id, 0) + f.quantity

            for order in orders:
                actual_fill_sum = fills_by_order.get(order.order_id, 0)
                if order.filled_quantity != actual_fill_sum:
                    discrepancies.append(
                        f"Cumulative quantity mismatch for order {order.order_id}: "
                        f"Order states {order.filled_quantity} != Sum of fills {actual_fill_sum}"
                    )
                expected_remaining = order.quantity - actual_fill_sum
                if order.remaining_quantity != expected_remaining:
                    discrepancies.append(
                        f"Remaining quantity mismatch for order {order.order_id}: "
                        f"Order states {order.remaining_quantity} != expected {expected_remaining}"
                    )

        # 2. Fee & Friction Verification
        summed_fees = sum((f.transaction_cost for f in fills), Decimal("0.0"))
        summed_slippage = sum((f.slippage_cost for f in fills), Decimal("0.0"))

        if abs(total_fees_paid - summed_fees) > Decimal("0.01"):
            discrepancies.append(
                f"Fee mismatch: Reported fees {total_fees_paid} != Sum of fill fees {summed_fees}"
            )

        if abs(total_slippage_paid - summed_slippage) > Decimal("0.01"):
            discrepancies.append(
                f"Slippage mismatch: Reported slippage {total_slippage_paid} != Sum of fill slippage {summed_slippage}"
            )

        total_friction = total_fees_paid + total_slippage_paid

        # 3. Position Quantity Symmetry Check
        fill_qty_by_symbol: Dict[str, int] = {}
        for f in fills:
            delta = f.quantity if f.side.value.upper() == "BUY" else -f.quantity
            fill_qty_by_symbol[f.symbol] = fill_qty_by_symbol.get(f.symbol, 0) + delta

        for symbol, pos in positions.items():
            expected_qty = fill_qty_by_symbol.get(symbol, 0)
            if pos.quantity != expected_qty:
                discrepancies.append(
                    f"Position quantity mismatch for {symbol}: Ledger qty {pos.quantity} != Net fills qty {expected_qty}"
                )

        for symbol, net_qty in fill_qty_by_symbol.items():
            if net_qty != 0 and symbol not in positions:
                discrepancies.append(
                    f"Missing position record for symbol {symbol}: Net fills qty {net_qty} but position not recorded"
                )

        # 4. Double-Entry Balance Sheet Conservation Equation
        # For long positions (quantity > 0): cash was spent (positive asset cost basis).
        # For short positions (quantity < 0): cash was received (short liability offset).
        total_cost_basis = sum(
            (p.cost_basis if p.quantity >= 0 else -p.cost_basis for p in positions.values()),
            Decimal("0.0"),
        )
        is_balanced, imbalance = verify_conservation_equation(
            initial_capital=initial_capital,
            ending_cash=ending_cash,
            total_cost_basis=total_cost_basis,
            total_friction=total_friction,
            gross_realized_pnl=gross_realized_pnl,
        )

        if not is_balanced:
            discrepancies.append(
                f"Balance sheet conservation violation: Imbalance of INR {imbalance} detected. "
                f"(Ending Cash {ending_cash} + Cost Basis {total_cost_basis} + Friction {total_friction} "
                f"!= Initial Capital {initial_capital} + Gross PnL {gross_realized_pnl})"
            )

        net_realized_pnl = gross_realized_pnl - total_friction

        report = EODReconciliationReport(
            session_id=session_id,
            reconciliation_timestamp_utc=datetime.now(timezone.utc).isoformat(),
            is_balanced=is_balanced and (len(discrepancies) == 0),
            initial_capital=initial_capital,
            ending_cash=ending_cash,
            total_cost_basis=total_cost_basis,
            total_fees_paid=total_fees_paid,
            total_slippage_paid=total_slippage_paid,
            gross_realized_pnl=gross_realized_pnl,
            net_realized_pnl=net_realized_pnl,
            imbalance_amount=imbalance,
            order_count=order_count,
            fill_count=fill_count,
            discrepancies=discrepancies,
        )

        if report.is_balanced:
            logger.info("Session %s EOD Reconciliation BALANCED. Net PnL: %s", session_id, net_realized_pnl)
        else:
            logger.error("Session %s EOD Reconciliation FAILED: %s", session_id, discrepancies)

        return report
