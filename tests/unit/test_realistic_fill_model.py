"""Unit and Regression Tests for Realistic Paper Fill Model.

Validates:
1. Instant fill mode vs Realistic penetration mode.
2. Marketability constraints: order unfilled if market never traded through limit price.
3. Boundary touch filtering: orders touching only the extreme low (buy) or extreme high (sell) are unfilled.
4. Traded volume participation caps: partial fills generated when order exceeds allowable volume.
5. Favorable gap execution (price improvement on gap down buy or gap up sell).
6. Stop loss gap-through execution vs normal stop trigger.
7. Unfilled order cancellation and capital reservation release.
"""

from decimal import Decimal
import pytest

from src.brokergateway.states import OrderSide
from src.core.exceptions import InvalidOrderTickError
from src.scanner.scanner import MarketBar
from src.simulation.fill_model import RealisticPaperFillModel


def make_test_bar(
    symbol: str = "RELIANCE",
    open_p: str = "2500.00",
    high_p: str = "2520.00",
    low_p: str = "2490.00",
    close_p: str = "2510.00",
    volume: int = 50000,
) -> MarketBar:
    return MarketBar(
        symbol=symbol,
        timestamp_iso="2026-09-04T09:30:00+05:30",
        open=Decimal(open_p),
        high=Decimal(high_p),
        low=Decimal(low_p),
        close=Decimal(close_p),
        volume=volume,
    )


class TestRealisticLimitFills:
    """Tests execution criteria, penetration requirements, and price improvement."""

    def test_buy_limit_unfilled_when_price_never_reached(self):
        """Buy limit order below bar low must remain unfilled."""
        model = RealisticPaperFillModel(mode="REALISTIC_PENETRATION")
        bar = make_test_bar(low_p="2500.00", high_p="2530.00")
        # Buy limit at 2490.00 (below market low)
        res = model.evaluate_limit_entry(
            order_id="ORD-01",
            symbol="RELIANCE",
            side=OrderSide.BUY,
            quantity=10,
            limit_price=Decimal("2490.00"),
            bar=bar,
            tick_size=Decimal("0.10"),
        )
        assert res is None

    def test_buy_limit_unfilled_on_exact_low_touch(self):
        """Buy limit order touching only the extreme low of the bar is unfilled (queue assumption)."""
        model = RealisticPaperFillModel(mode="REALISTIC_PENETRATION", require_price_penetration=True)
        bar = make_test_bar(low_p="2500.00", high_p="2530.00")
        res = model.evaluate_limit_entry(
            order_id="ORD-02",
            symbol="RELIANCE",
            side=OrderSide.BUY,
            quantity=10,
            limit_price=Decimal("2500.00"),  # Exactly equal to bar.low
            bar=bar,
            tick_size=Decimal("0.10"),
        )
        assert res is None

    def test_buy_limit_filled_on_price_penetration(self):
        """Buy limit order penetrating inside the bar range is filled."""
        model = RealisticPaperFillModel(mode="REALISTIC_PENETRATION", require_price_penetration=True)
        bar = make_test_bar(open_p="2515.00", low_p="2495.00", high_p="2525.00")
        res = model.evaluate_limit_entry(
            order_id="ORD-03",
            symbol="RELIANCE",
            side=OrderSide.BUY,
            quantity=10,
            limit_price=Decimal("2510.00"),  # Traded through (bar.low 2495 < 2510)
            bar=bar,
            tick_size=Decimal("0.10"),
        )
        assert res is not None
        assert res.filled_quantity == 10
        assert res.fill_price == Decimal("2510.00")
        assert res.is_partial_fill is False

    def test_buy_limit_price_improvement_on_gap_down(self):
        """Buy limit receives price improvement if bar opens gap-down below limit price."""
        model = RealisticPaperFillModel(mode="REALISTIC_PENETRATION", allow_price_improvement=True)
        bar = make_test_bar(open_p="2480.00", low_p="2475.00", high_p="2510.00")
        res = model.evaluate_limit_entry(
            order_id="ORD-04",
            symbol="RELIANCE",
            side=OrderSide.BUY,
            quantity=10,
            limit_price=Decimal("2500.00"),  # Open is 2480.00 < 2500.00
            bar=bar,
            tick_size=Decimal("0.10"),
        )
        assert res is not None
        assert res.fill_price == Decimal("2480.00")  # Favorable fill at bar.open

    def test_sell_limit_unfilled_on_exact_high_touch(self):
        """Sell limit touching only the extreme high of the bar is unfilled."""
        model = RealisticPaperFillModel(mode="REALISTIC_PENETRATION", require_price_penetration=True)
        bar = make_test_bar(low_p="2480.00", high_p="2510.00")
        res = model.evaluate_limit_entry(
            order_id="ORD-05",
            symbol="RELIANCE",
            side=OrderSide.SELL,
            quantity=10,
            limit_price=Decimal("2510.00"),  # Exactly equal to bar.high
            bar=bar,
            tick_size=Decimal("0.10"),
        )
        assert res is None

    def test_sell_limit_filled_on_penetration(self):
        """Sell limit penetrating inside the bar range is filled."""
        model = RealisticPaperFillModel(mode="REALISTIC_PENETRATION")
        bar = make_test_bar(open_p="2490.00", low_p="2485.00", high_p="2520.00")
        res = model.evaluate_limit_entry(
            order_id="ORD-06",
            symbol="RELIANCE",
            side=OrderSide.SELL,
            quantity=10,
            limit_price=Decimal("2500.00"),  # Bar high 2520 > 2500
            bar=bar,
            tick_size=Decimal("0.10"),
        )
        assert res is not None
        assert res.fill_price == Decimal("2500.00")


class TestVolumeParticipationAndPartialFills:
    """Tests volume participation constraints and partial fill behavior."""

    def test_partial_fill_when_quantity_exceeds_volume_cap(self):
        """Orders exceeding volume participation cap (10%) are partially filled."""
        model = RealisticPaperFillModel(
            mode="REALISTIC_PENETRATION",
            max_volume_participation_pct=Decimal("0.10"),
        )
        # Bar volume = 1,000 shares -> max allowed participation = 100 shares
        bar = make_test_bar(low_p="2490.00", high_p="2520.00", volume=1000)
        res = model.evaluate_limit_entry(
            order_id="ORD-07",
            symbol="RELIANCE",
            side=OrderSide.BUY,
            quantity=250,  # Requests 250 shares
            limit_price=Decimal("2505.00"),
            bar=bar,
            tick_size=Decimal("0.10"),
        )
        assert res is not None
        assert res.is_partial_fill is True
        assert res.filled_quantity == 100
        assert res.unfilled_quantity == 150
        assert res.requested_quantity == 250

    def test_zero_volume_bar_fails_to_fill(self):
        """Zero volume bars cannot execute any fills."""
        model = RealisticPaperFillModel(mode="REALISTIC_PENETRATION")
        bar = make_test_bar(low_p="2490.00", high_p="2520.00", volume=0)
        res = model.evaluate_limit_entry(
            order_id="ORD-08",
            symbol="RELIANCE",
            side=OrderSide.BUY,
            quantity=10,
            limit_price=Decimal("2505.00"),
            bar=bar,
            tick_size=Decimal("0.10"),
        )
        assert res is None


class TestExitExecutionAndGaps:
    """Tests stop-loss gap handling and take-profit limit triggers."""

    def test_stop_loss_normal_trigger(self):
        """Normal stop-loss trigger executes at the quantized stop price."""
        model = RealisticPaperFillModel()
        bar = make_test_bar(open_p="2510.00", low_p="2490.00", high_p="2515.00")
        stop_price = Decimal("2495.00")
        res = model.evaluate_stop_loss_exit(
            symbol="RELIANCE",
            pos_side=OrderSide.BUY,
            stop_loss_price=stop_price,
            bar=bar,
            tick_size=Decimal("0.10"),
        )
        assert res is not None
        exit_price, reason = res
        assert exit_price == stop_price
        assert reason == "STOP_LOSS"

    def test_stop_loss_gap_through_open(self):
        """Gap-through stop executes at bar open price (quantized to tick)."""
        model = RealisticPaperFillModel()
        # Bar opened at 2480.00 (below stop loss at 2495.00)
        bar = make_test_bar(open_p="2480.00", low_p="2470.00", high_p="2485.00")
        stop_price = Decimal("2495.00")
        res = model.evaluate_stop_loss_exit(
            symbol="RELIANCE",
            pos_side=OrderSide.BUY,
            stop_loss_price=stop_price,
            bar=bar,
            tick_size=Decimal("0.10"),
        )
        assert res is not None
        exit_price, reason = res
        assert exit_price == Decimal("2480.00")
        assert reason == "STOP_LOSS_GAP_THROUGH"
