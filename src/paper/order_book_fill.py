"""Realistic Order Book Depth Execution Engine.

Simulates real-world market microstructure:
1. Bid/Ask Spread Traversal:
   - Market BUY orders sweep the Ask book (depth.sell) using VWAP pricing.
   - Market SELL orders sweep the Bid book (depth.buy) using VWAP pricing.
2. Market Depth & Partial Fills:
   - Consumes available liquidity level-by-level.
   - If order size exceeds depth, simulates partial fills or realistic price impact.
3. Statutory Exchange Circuit Breaker Checks:
   - Blocks BUY orders at Upper Circuit.
   - Blocks SELL orders at Lower Circuit.
4. Limit Order Marketability & Resting Simulation:
   - Marketable limit orders execute immediately against opposing book.
   - Non-marketable limit orders become resting orders.
5. Exact Statutory Tick Quantization (quantize_order_price).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import logging
from typing import Any, Dict, List, Optional, Tuple
import uuid

from src.brokergateway.states import OrderSide, OrderType
from src.core.safety_invariants import assert_paper_only_invariants
from src.core.tick_precision import quantize_order_price
from src.simulation.fee_model import NSEFrictionModel, FrictionBreakdown

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DepthLevel:
    price: Decimal
    quantity: int
    orders: int


@dataclass(frozen=True)
class OrderBookDepth:
    """Represents Level-2 Order Book Depth (top 5 bids and asks)."""
    symbol: str
    ltp: Decimal
    bids: list[DepthLevel]
    asks: list[DepthLevel]
    lower_circuit: Optional[Decimal] = None
    upper_circuit: Optional[Decimal] = None
    volume: int = 0
    vwap: Optional[Decimal] = None

    @property
    def best_bid(self) -> Optional[Decimal]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[Decimal]:
        return self.asks[0].price if self.asks else None

    @property
    def spread(self) -> Decimal:
        if self.best_bid is not None and self.best_ask is not None:
            return max(Decimal("0.0"), self.best_ask - self.best_bid)
        return Decimal("0.05")

    @property
    def spread_bps(self) -> Decimal:
        if self.best_bid and self.best_bid > Decimal("0"):
            return (self.spread / self.best_bid) * Decimal("10000")
        return Decimal("0.0")

    @property
    def total_bid_depth(self) -> int:
        return sum(b.quantity for b in self.bids)

    @property
    def total_ask_depth(self) -> int:
        return sum(a.quantity for a in self.asks)

    @classmethod
    def from_kite_quote(cls, symbol: str, quote_data: dict) -> OrderBookDepth:
        """Parses Kite Connect quote payload into structured OrderBookDepth."""
        ltp = Decimal(str(quote_data.get("last_price", 0.0)))
        depth = quote_data.get("depth", {})
        
        bids = [
            DepthLevel(
                price=Decimal(str(d["price"])),
                quantity=int(d["quantity"]),
                orders=int(d.get("orders", 1)),
            )
            for d in depth.get("buy", [])
            if d.get("price", 0) > 0
        ]
        
        asks = [
            DepthLevel(
                price=Decimal(str(d["price"])),
                quantity=int(d["quantity"]),
                orders=int(d.get("orders", 1)),
            )
            for d in depth.get("sell", [])
            if d.get("price", 0) > 0
        ]

        lc = Decimal(str(quote_data["lower_circuit_limit"])) if "lower_circuit_limit" in quote_data else None
        uc = Decimal(str(quote_data["upper_circuit_limit"])) if "upper_circuit_limit" in quote_data else None
        vol = int(quote_data.get("volume", 0))
        vwap = Decimal(str(quote_data["average_price"])) if quote_data.get("average_price") else None

        return cls(
            symbol=symbol,
            ltp=ltp,
            bids=bids,
            asks=asks,
            lower_circuit=lc,
            upper_circuit=uc,
            volume=vol,
            vwap=vwap,
        )


@dataclass(frozen=True)
class ExecutionResult:
    """Discrete outcome of an order execution simulation."""
    order_id: str
    fill_id: str
    symbol: str
    side: OrderSide
    requested_quantity: int
    filled_quantity: int
    unfilled_quantity: int
    fill_price: Decimal
    is_partial: bool
    friction: FrictionBreakdown
    execution_reason: str
    depth_levels_consumed: int
    effective_spread_paid: Decimal


class RealisticOrderBookExecutionEngine:
    """Institutional realistic execution simulator operating on live order book depth."""

    def __init__(self, default_tick_size: Decimal = Decimal("0.05")):
        assert_paper_only_invariants()
        self.default_tick_size = default_tick_size

    def check_circuit_breakers(
        self,
        depth: OrderBookDepth,
        side: OrderSide,
        price: Decimal,
    ) -> Tuple[bool, str]:
        """Validates that execution does not violate statutory circuit breaker limits."""
        if depth.upper_circuit is not None and side == OrderSide.BUY:
            if depth.ltp >= depth.upper_circuit or price >= depth.upper_circuit:
                return False, f"Upper circuit limit hit ({depth.upper_circuit}). Buy orders frozen."

        if depth.lower_circuit is not None and side == OrderSide.SELL:
            if depth.ltp <= depth.lower_circuit or price <= depth.lower_circuit:
                return False, f"Lower circuit limit hit ({depth.lower_circuit}). Sell orders frozen."

        return True, "Circuits normal"

    def execute_market_order(
        self,
        order_id: str,
        symbol: str,
        side: OrderSide,
        quantity: int,
        depth: OrderBookDepth,
    ) -> ExecutionResult:
        """Executes a market order by sweeping the live order book depth.
        
        - BUY sweeps the Ask levels.
        - SELL sweeps the Bid levels.
        - Computes exact volume-weighted average price (VWAP).
        """
        assert_paper_only_invariants()
        if quantity <= 0:
            raise ValueError(f"Quantity must be positive, got {quantity}")

        # Circuit check
        ok, reason = self.check_circuit_breakers(depth, side, depth.ltp)
        if not ok:
            raise ValueError(f"Order rejected by circuit breaker: {reason}")

        book = depth.asks if side == OrderSide.BUY else depth.bids
        if not book:
            # Fallback if depth missing: use LTP with half-spread impact
            half_spread = depth.spread / Decimal("2.0")
            raw_price = depth.ltp + half_spread if side == OrderSide.BUY else depth.ltp - half_spread
            fill_price = quantize_order_price(raw_price, symbol, side_is_buy=(side == OrderSide.BUY), order_purpose="ENTRY")
            friction = NSEFrictionModel.calculate_friction(side.value, quantity, fill_price)
            return ExecutionResult(
                order_id=order_id,
                fill_id=f"FILL-MKT-{uuid.uuid4().hex[:8]}",
                symbol=symbol,
                side=side,
                requested_quantity=quantity,
                filled_quantity=quantity,
                unfilled_quantity=0,
                fill_price=fill_price,
                is_partial=False,
                friction=friction,
                execution_reason="MARKET_LTP_FALLBACK_WITH_SPREAD",
                depth_levels_consumed=1,
                effective_spread_paid=half_spread,
            )

        # Sweep order book levels
        rem_qty = quantity
        total_cost = Decimal("0.0")
        levels_consumed = 0
        filled_qty = 0

        for level in book:
            if rem_qty <= 0:
                break
            levels_consumed += 1
            take_qty = min(rem_qty, level.quantity)
            total_cost += level.price * Decimal(str(take_qty))
            rem_qty -= take_qty
            filled_qty += take_qty

        # If order size exceeds 5 depth levels, fill remainder at last depth level with impact
        if rem_qty > 0:
            deepest_price = book[-1].price
            impact_pct = Decimal("0.001") * Decimal(str(levels_consumed))  # 10 bps impact per depth level
            if side == OrderSide.BUY:
                impacted_price = deepest_price * (Decimal("1.0") + impact_pct)
            else:
                impacted_price = deepest_price * (Decimal("1.0") - impact_pct)
            
            total_cost += impacted_price * Decimal(str(rem_qty))
            filled_qty += rem_qty
            rem_qty = 0

        avg_price = total_cost / Decimal(str(filled_qty))
        fill_price = quantize_order_price(avg_price, symbol, side_is_buy=(side == OrderSide.BUY), order_purpose="ENTRY")
        friction = NSEFrictionModel.calculate_friction(side.value, filled_qty, fill_price)
        spread_impact = abs(fill_price - depth.ltp)

        return ExecutionResult(
            order_id=order_id,
            fill_id=f"FILL-DEPTH-{uuid.uuid4().hex[:8]}",
            symbol=symbol,
            side=side,
            requested_quantity=quantity,
            filled_quantity=filled_qty,
            unfilled_quantity=rem_qty,
            fill_price=fill_price,
            is_partial=(rem_qty > 0),
            friction=friction,
            execution_reason=f"DEPTH_SWEEP_{levels_consumed}_LEVELS",
            depth_levels_consumed=levels_consumed,
            effective_spread_paid=spread_impact,
        )

    def evaluate_limit_order(
        self,
        order_id: str,
        symbol: str,
        side: OrderSide,
        quantity: int,
        limit_price: Decimal,
        depth: OrderBookDepth,
    ) -> Optional[ExecutionResult]:
        """Evaluates whether a limit order is marketable against current depth.
        
        - If Buy Limit >= Best Ask: Marketable limit order, executes immediately.
        - If Sell Limit <= Best Bid: Marketable limit order, executes immediately.
        - Otherwise returns None (Order rests as PENDING in the order book).
        """
        assert_paper_only_invariants()
        ok, reason = self.check_circuit_breakers(depth, side, limit_price)
        if not ok:
            raise ValueError(f"Limit order rejected by circuit breaker: {reason}")

        if side == OrderSide.BUY:
            if depth.best_ask is not None and limit_price >= depth.best_ask:
                # Marketable! Sweep asks up to limit_price
                book = [a for a in depth.asks if a.price <= limit_price]
                if not book:
                    return None

                rem_qty = quantity
                total_cost = Decimal("0.0")
                levels_consumed = 0
                filled_qty = 0

                for level in book:
                    if rem_qty <= 0:
                        break
                    levels_consumed += 1
                    take_qty = min(rem_qty, level.quantity)
                    total_cost += level.price * Decimal(str(take_qty))
                    rem_qty -= take_qty
                    filled_qty += take_qty

                if filled_qty == 0:
                    return None

                avg_price = total_cost / Decimal(str(filled_qty))
                fill_price = quantize_order_price(avg_price, symbol, side_is_buy=True, order_purpose="ENTRY")
                friction = NSEFrictionModel.calculate_friction(side.value, filled_qty, fill_price)

                return ExecutionResult(
                    order_id=order_id,
                    fill_id=f"FILL-LIMIT-{uuid.uuid4().hex[:8]}",
                    symbol=symbol,
                    side=side,
                    requested_quantity=quantity,
                    filled_quantity=filled_qty,
                    unfilled_quantity=rem_qty,
                    fill_price=fill_price,
                    is_partial=(rem_qty > 0),
                    friction=friction,
                    execution_reason="MARKETABLE_LIMIT_ORDER_CROSS",
                    depth_levels_consumed=levels_consumed,
                    effective_spread_paid=abs(fill_price - depth.best_ask),
                )
            else:
                # Non-marketable, rests in the order book
                return None

        elif side == OrderSide.SELL:
            if depth.best_bid is not None and limit_price <= depth.best_bid:
                # Marketable! Sweep bids down to limit_price
                book = [b for b in depth.bids if b.price >= limit_price]
                if not book:
                    return None

                rem_qty = quantity
                total_cost = Decimal("0.0")
                levels_consumed = 0
                filled_qty = 0

                for level in book:
                    if rem_qty <= 0:
                        break
                    levels_consumed += 1
                    take_qty = min(rem_qty, level.quantity)
                    total_cost += level.price * Decimal(str(take_qty))
                    rem_qty -= take_qty
                    filled_qty += take_qty

                if filled_qty == 0:
                    return None

                avg_price = total_cost / Decimal(str(filled_qty))
                fill_price = quantize_order_price(avg_price, symbol, side_is_buy=False, order_purpose="ENTRY")
                friction = NSEFrictionModel.calculate_friction(side.value, filled_qty, fill_price)

                return ExecutionResult(
                    order_id=order_id,
                    fill_id=f"FILL-LIMIT-{uuid.uuid4().hex[:8]}",
                    symbol=symbol,
                    side=side,
                    requested_quantity=quantity,
                    filled_quantity=filled_qty,
                    unfilled_quantity=rem_qty,
                    fill_price=fill_price,
                    is_partial=(rem_qty > 0),
                    friction=friction,
                    execution_reason="MARKETABLE_LIMIT_ORDER_CROSS",
                    depth_levels_consumed=levels_consumed,
                    effective_spread_paid=abs(fill_price - depth.best_bid),
                )
            else:
                return None

        return None
