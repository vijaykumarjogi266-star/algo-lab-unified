"""High-Precision Financial Math and Double-Entry Equations.

Uses Python Decimal with 28-digit precision to eliminate floating-point drift.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, getcontext
from typing import Tuple

# Set global decimal context precision to 28 digits
getcontext().prec = 28


def round_currency(val: Decimal) -> Decimal:
    """Rounds currency to 2 decimal places (paise precision)."""
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def verify_conservation_equation(
    initial_capital: Decimal,
    ending_cash: Decimal,
    total_cost_basis: Decimal,
    total_friction: Decimal,
    gross_realized_pnl: Decimal,
    tolerance: Decimal = Decimal("0.01"),
) -> Tuple[bool, Decimal]:
    """Verifies double-entry balance sheet conservation:

    Ending Cash + Cost Basis + Total Friction == Initial Capital + Gross Realized P&L

    For short positions, total_cost_basis represents net asset cost basis minus short sale proceeds.
    Returns (is_balanced, imbalance_amount).
    """
    left_hand_side = ending_cash + total_cost_basis + total_friction
    right_hand_side = initial_capital + gross_realized_pnl
    imbalance = abs(left_hand_side - right_hand_side)
    is_balanced = imbalance <= tolerance
    return is_balanced, round_currency(imbalance)
