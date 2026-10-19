"""EOD Reconciliation Data Models and Report Schemas."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional


@dataclass
class PositionBalance:
    """Position balance for double-entry reconciliation."""
    symbol: str
    quantity: int
    cost_basis: Decimal
    current_market_price: Decimal = Decimal("0.0")


@dataclass
class EODReconciliationReport:
    """Authoritative double-entry session reconciliation certification."""
    session_id: str
    reconciliation_timestamp_utc: str
    is_balanced: bool
    initial_capital: Decimal
    ending_cash: Decimal
    total_cost_basis: Decimal
    total_fees_paid: Decimal
    total_slippage_paid: Decimal
    gross_realized_pnl: Decimal
    net_realized_pnl: Decimal
    imbalance_amount: Decimal
    order_count: int
    fill_count: int
    discrepancies: List[str] = field(default_factory=list)
