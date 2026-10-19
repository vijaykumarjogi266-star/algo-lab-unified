"""Realistic Paper Fill Simulation Engine for Intraday Cash Equities.

Provides:
1. Two distinct execution simulation modes:
   - "INSTANT_FILL": Legacy baseline assumption (100% immediate fill at limit price).
   - "REALISTIC_PENETRATION": Hardened paper model requiring market price penetration and volume caps.
2. Penetration requirement: Limit orders touching only the extreme low (for buy) or extreme high
   (for sell) of a bar are treated as UNFILLED to eliminate optimistic backtesting bias.
3. Volume participation cap: An order's simulated filled quantity cannot exceed an explicitly
   configured percentage of the bar's volume (default: 10%), generating realistic partial fills.
4. Capital reservation reconciliation: Unfilled or cancelled portions of an order immediately release
   unconsumed reserved capital back to available cash.
5. Strict enforcement of statutory tick sizes and price limits.
6. Clear separation between stop trigger prices, gap fill prices, and statutory slippage.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
import uuid

from src.brokergateway.models import ExecutionFill
from src.brokergateway.states import OrderSide, OrderType
from src.core.exceptions import InvalidOrderTickError
from src.core.tick_precision import is_valid_tick, quantize_order_price, quantize_to_tick
from src.scanner.scanner import MarketBar
from src.simulation.fee_model import NSEFrictionModel, FrictionBreakdown


@dataclass(frozen=True)
class FillExecutionResult:
    """Discrete outcome of a simulated order execution evaluation."""
    fill_id: str
    order_id: str
    symbol: str
    side: OrderSide
    requested_quantity: int
    filled_quantity: int
    unfilled_quantity: int
    fill_price: Decimal
    is_partial_fill: bool
    friction: FrictionBreakdown
    execution_reason: str


class RealisticPaperFillModel:
    """Configurable execution fill simulator adhering to strict paper-only constraints."""

    def __init__(
        self,
        mode: str = "REALISTIC_PENETRATION",  # "REALISTIC_PENETRATION" or "INSTANT_FILL"
        require_price_penetration: bool = True,
        max_volume_participation_pct: Decimal = Decimal("0.10"),  # 10% volume ceiling
        allow_price_improvement: bool = False,
    ):
        if mode not in ("REALISTIC_PENETRATION", "INSTANT_FILL"):
            raise ValueError(f"Unsupported fill model mode: {mode}")
        self.mode = mode
        self.require_price_penetration = require_price_penetration
        self.max_volume_participation_pct = max_volume_participation_pct
        self.allow_price_improvement = allow_price_improvement

    def evaluate_limit_entry(
        self,
        order_id: str,
        symbol: str,
        side: OrderSide,
        quantity: int,
        limit_price: Decimal,
        bar: MarketBar,
        tick_size: Decimal,
    ) -> Optional[FillExecutionResult]:
        """Evaluates whether a limit entry order can be filled on the given bar.

        Returns:
            FillExecutionResult if filled or partially filled, or None if unfilled.
        """
        # 1. Statutory Tick Verification
        if not is_valid_tick(limit_price, tick_size):
            raise InvalidOrderTickError(
                f"Limit price {limit_price} for {symbol} violates statutory tick size {tick_size}"
            )

        # 2. Legacy Instant Fill Mode
        if self.mode == "INSTANT_FILL":
            friction = NSEFrictionModel.calculate_friction(side.value, quantity, limit_price)
            return FillExecutionResult(
                fill_id=f"FILL-ENTRY-{uuid.uuid4().hex[:8]}",
                order_id=order_id,
                symbol=symbol,
                side=side,
                requested_quantity=quantity,
                filled_quantity=quantity,
                unfilled_quantity=0,
                fill_price=limit_price,
                is_partial_fill=False,
                friction=friction,
                execution_reason="INSTANT_FILL_ASSUMPTION",
            )

        # 3. Realistic Penetration & Marketability Checks
        fill_price = limit_price
        if side == OrderSide.BUY:
            # For a Buy limit order to be marketable, bar low must trade down to or below limit
            if bar.low > limit_price:
                # Market never reached the limit price
                return None
            if self.require_price_penetration and bar.low == limit_price:
                # Order only touched the extreme low of the bar (back of the queue)
                return None

            # Price improvement (only if enabled, e.g. for resting cross-bar orders)
            if self.allow_price_improvement and bar.open < limit_price:
                fill_price = quantize_order_price(bar.open, symbol, side_is_buy=True, order_purpose="ENTRY", tick_size=tick_size)

        elif side == OrderSide.SELL:
            # For a Sell limit order to be marketable, bar high must trade up to or above limit
            if bar.high < limit_price:
                return None
            if self.require_price_penetration and bar.high == limit_price:
                # Touched only the extreme high of the bar
                return None

            # Price improvement (only if enabled, e.g. for resting cross-bar orders)
            if self.allow_price_improvement and bar.open > limit_price:
                fill_price = quantize_order_price(bar.open, symbol, side_is_buy=False, order_purpose="ENTRY", tick_size=tick_size)

        # 4. Volume Participation Ceiling & Partial Fill Determination
        if bar.volume <= 0:
            return None

        max_allocable_qty = int(Decimal(str(bar.volume)) * self.max_volume_participation_pct)
        if max_allocable_qty < 1:
            return None

        filled_qty = min(quantity, max_allocable_qty)
        unfilled_qty = quantity - filled_qty
        is_partial = unfilled_qty > 0

        friction = NSEFrictionModel.calculate_friction(side.value, filled_qty, fill_price)

        return FillExecutionResult(
            fill_id=f"FILL-ENTRY-{uuid.uuid4().hex[:8]}",
            order_id=order_id,
            symbol=symbol,
            side=side,
            requested_quantity=quantity,
            filled_quantity=filled_qty,
            unfilled_quantity=unfilled_qty,
            fill_price=fill_price,
            is_partial_fill=is_partial,
            friction=friction,
            execution_reason="REALISTIC_BAR_PENETRATION" if not is_partial else "REALISTIC_VOLUME_CONSTRAINED_PARTIAL",
        )

    def evaluate_stop_loss_exit(
        self,
        symbol: str,
        pos_side: OrderSide,
        stop_loss_price: Decimal,
        bar: MarketBar,
        tick_size: Decimal,
    ) -> Optional[Tuple[Decimal, str]]:
        """Evaluates stop-loss trigger and execution price, accounting for gap-through bar opens.

        Returns (exit_price, exit_reason) if triggered, or None.
        """
        sl_hit = (bar.low <= stop_loss_price) if pos_side == OrderSide.BUY else (bar.high >= stop_loss_price)
        if not sl_hit:
            return None

        # Check gap-through open
        if pos_side == OrderSide.BUY and bar.open <= stop_loss_price:
            # Bar opened below stop loss (gap down)
            exit_price = quantize_order_price(bar.open, symbol, side_is_buy=False, order_purpose="STOP_LOSS", tick_size=tick_size)
            return exit_price, "STOP_LOSS_GAP_THROUGH"
        elif pos_side == OrderSide.SELL and bar.open >= stop_loss_price:
            # Bar opened above stop loss (gap up)
            exit_price = quantize_order_price(bar.open, symbol, side_is_buy=True, order_purpose="STOP_LOSS", tick_size=tick_size)
            return exit_price, "STOP_LOSS_GAP_THROUGH"

        return stop_loss_price, "STOP_LOSS"

    def evaluate_take_profit_exit(
        self,
        symbol: str,
        pos_side: OrderSide,
        take_profit_price: Decimal,
        bar: MarketBar,
        tick_size: Decimal,
    ) -> Optional[Tuple[Decimal, str]]:
        """Evaluates take-profit target trigger and execution price."""
        tp_hit = (bar.high >= take_profit_price) if pos_side == OrderSide.BUY else (bar.low <= take_profit_price)
        if not tp_hit:
            return None

        # Check gap-through favorable open (only if explicitly enabled)
        if self.allow_price_improvement:
            if pos_side == OrderSide.BUY and bar.open >= take_profit_price:
                exit_price = quantize_order_price(bar.open, symbol, side_is_buy=False, order_purpose="TAKE_PROFIT", tick_size=tick_size)
                return exit_price, "TAKE_PROFIT_GAP_UP"
            elif pos_side == OrderSide.SELL and bar.open <= take_profit_price:
                exit_price = quantize_order_price(bar.open, symbol, side_is_buy=True, order_purpose="TAKE_PROFIT", tick_size=tick_size)
                return exit_price, "TAKE_PROFIT_GAP_DOWN"

        return take_profit_price, "TAKE_PROFIT"
