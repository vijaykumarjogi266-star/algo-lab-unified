"""Unit tests for Professional Realistic Paper Trading Engine components."""

from decimal import Decimal
import pytest

from src.brokergateway.states import OrderSide, OrderType
from src.paper.analytics import QuantAnalyticsEngine
from src.paper.order_book_fill import DepthLevel, ExecutionResult, OrderBookDepth, RealisticOrderBookExecutionEngine
from src.paper.pending_order_book import PendingOrder, PendingOrderBook
from src.paper.portfolio_tracker import PaperClosedTrade, PaperPortfolio
from src.paper.risk_manager import PreTradeRiskManager


def test_order_book_depth_parsing_and_spread():
    raw_quote = {
        "last_price": 1200.0,
        "lower_circuit_limit": 1080.0,
        "upper_circuit_limit": 1320.0,
        "volume": 500000,
        "average_price": 1201.5,
        "depth": {
            "buy": [
                {"price": 1199.9, "quantity": 100, "orders": 2},
                {"price": 1199.8, "quantity": 250, "orders": 5},
            ],
            "sell": [
                {"price": 1200.1, "quantity": 50, "orders": 1},
                {"price": 1200.2, "quantity": 150, "orders": 3},
            ],
        },
    }
    depth = OrderBookDepth.from_kite_quote("TEST", raw_quote)
    assert depth.best_bid == Decimal("1199.9")
    assert depth.best_ask == Decimal("1200.1")
    assert depth.spread == Decimal("0.2")
    assert depth.total_bid_depth == 350
    assert depth.total_ask_depth == 200


def test_market_buy_sweeps_depth_levels_with_vwap():
    depth = OrderBookDepth(
        symbol="TCS",
        ltp=Decimal("3000.0"),
        bids=[DepthLevel(Decimal("2999.0"), 100, 1)],
        asks=[
            DepthLevel(Decimal("3000.1"), 10, 1),
            DepthLevel(Decimal("3000.5"), 20, 2),
        ],
        upper_circuit=Decimal("3300.0"),
        lower_circuit=Decimal("2700.0"),
    )
    engine = RealisticOrderBookExecutionEngine()

    # Buy 15 shares -> Should consume 10 @ 3000.10 and 5 @ 3000.50
    # Total cost = (10 * 3000.10) + (5 * 3000.50) = 30001 + 15002.50 = 45003.50
    # VWAP = 45003.50 / 15 = 3000.2333 -> quantized to tick
    res = engine.execute_market_order("ORD-1", "TCS", OrderSide.BUY, 15, depth)

    assert res.filled_quantity == 15
    assert res.depth_levels_consumed == 2
    assert res.fill_price > Decimal("3000.10")
    assert res.fill_price <= Decimal("3000.50")
    assert res.friction.total_friction > Decimal("0")


def test_circuit_breaker_blocks_execution():
    depth = OrderBookDepth(
        symbol="RELIANCE",
        ltp=Decimal("1300.0"),
        bids=[],
        asks=[],
        upper_circuit=Decimal("1300.0"),
        lower_circuit=Decimal("1100.0"),
    )
    engine = RealisticOrderBookExecutionEngine()

    # Stock is locked at upper circuit -> BUY must be rejected
    with pytest.raises(ValueError, match="Upper circuit limit hit"):
        engine.execute_market_order("ORD-UC", "RELIANCE", OrderSide.BUY, 10, depth)


def test_limit_order_marketability_and_resting():
    depth = OrderBookDepth(
        symbol="INFY",
        ltp=Decimal("1500.0"),
        bids=[DepthLevel(Decimal("1499.5"), 100, 1)],
        asks=[DepthLevel(Decimal("1500.5"), 100, 1)],
    )
    engine = RealisticOrderBookExecutionEngine()

    # 1. Non-marketable limit buy at 1499.00 (below best ask 1500.50) -> Returns None (resting)
    res_resting = engine.evaluate_limit_order("ORD-REST", "INFY", OrderSide.BUY, 10, Decimal("1499.00"), depth)
    assert res_resting is None

    # 2. Marketable limit buy at 1501.00 (above best ask 1500.50) -> Executes immediately
    res_mkt = engine.evaluate_limit_order("ORD-CROSS", "INFY", OrderSide.BUY, 10, Decimal("1501.00"), depth)
    assert res_mkt is not None
    assert res_mkt.filled_quantity == 10
    assert res_mkt.fill_price == Decimal("1500.50")


def test_rms_daily_loss_kill_switch():
    rms = PreTradeRiskManager(max_daily_drawdown_pct=Decimal("2.0"), enforce_session_times=False)
    
    # Starting with 10,000 capital, current equity dropped to 9,700 (-3.0% loss)
    res = rms.evaluate_order(
        symbol="SBIN",
        sector="BANKING",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=1,
        price=Decimal("800.0"),
        ltp=Decimal("800.0"),
        portfolio_equity=Decimal("9700.0"),
        initial_capital=Decimal("10000.0"),
        current_positions={},
    )
    assert not res.is_approved
    assert res.rule_name == "DAILY_LOSS_LIMIT"
    assert rms.is_kill_switch_active


def test_rms_max_position_equity_cap():
    rms = PreTradeRiskManager(max_position_equity_pct=Decimal("25.0"), enforce_session_times=False)

    # 10,000 equity -> Max single position allowed is 2,500.
    # Trying to order 1 share @ 3,500 should be rejected!
    res = rms.evaluate_order(
        symbol="LT",
        sector="INFRA",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=1,
        price=Decimal("3500.0"),
        ltp=Decimal("3500.0"),
        portfolio_equity=Decimal("10000.0"),
        initial_capital=Decimal("10000.0"),
        current_positions={},
    )
    assert not res.is_approved
    assert res.rule_name == "MAX_POSITION_EQUITY_CAP"


def test_rms_fat_finger_protection():
    rms = PreTradeRiskManager(max_fat_finger_deviation_pct=Decimal("3.0"), enforce_session_times=False)

    # LTP is 1000. Limit price is 1100 (+10% outlier deviation) -> Reject
    res = rms.evaluate_order(
        symbol="ITC",
        sector="FMCG",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=1,
        price=Decimal("1100.0"),
        ltp=Decimal("1000.0"),
        portfolio_equity=Decimal("10000.0"),
        initial_capital=Decimal("10000.0"),
        current_positions={},
    )
    assert not res.is_approved
    assert res.rule_name == "FAT_FINGER_PROTECTION"


def test_quant_analytics_metrics_calculation():
    port = PaperPortfolio(initial_capital=Decimal("10000.00"))
    # Add mock closed trades
    port.closed_trades = [
        PaperClosedTrade("T1", "P1", "SBIN", "BANKING", "BUY", 10, Decimal("800"), Decimal("820"), Decimal("1"), Decimal("1"), Decimal("200"), Decimal("198"), Decimal("2.5"), "iso1", "iso2", "TAKE_PROFIT"),
        PaperClosedTrade("T2", "P2", "TCS", "IT", "BUY", 1, Decimal("3000"), Decimal("2950"), Decimal("1"), Decimal("1"), Decimal("-50"), Decimal("-52"), Decimal("-1.67"), "iso1", "iso2", "STOP_LOSS"),
    ]
    port.cash_balance = Decimal("10146.00")

    metrics = QuantAnalyticsEngine.compute_metrics(port)
    assert metrics.total_trades == 2
    assert metrics.winning_trades == 1
    assert metrics.losing_trades == 1
    assert metrics.win_rate_pct == Decimal("50.0")
    assert metrics.profit_factor > Decimal("3.0")
    assert metrics.realized_pnl == Decimal("146.00")
