"""Strategy Signals and Parameters Data Models."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from src.brokergateway.states import OrderSide


@dataclass
class StrategySignal:
    """Actionable trade signal emitted by a quant strategy."""
    signal_id: str
    strategy_id: str
    symbol: str
    side: OrderSide
    target_entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    confidence_score: Decimal
    timestamp_iso: str
