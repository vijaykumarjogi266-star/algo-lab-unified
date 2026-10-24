"""Unit Tests for Multi-Factor Stock Screener and Stock Selection.

Verifies:
1. Strict price and turnover liquidity filtering.
2. Minervini trend template enforcement.
3. Relative Strength (RS Alpha) vs benchmark.
4. Setup pattern detection (EMA Pullback, Donchian Breakout).
5. Composite scoring and sector concentration capping.
6. Fail-closed parameter validation.
"""

from decimal import Decimal
import pytest

from src.core.exceptions import InvalidStrategyParameterError
from src.scanner.scanner import MarketBar
from src.scanner.stock_screener import SetupType, StockScreener


def make_bar_series(
    symbol: str,
    base_price: float,
    trend_step: float = 0.5,
    count: int = 60,
    volume: int = 100000,
) -> list[MarketBar]:
    bars = []
    p = base_price
    for i in range(count):
        p += trend_step
        bars.append(
            MarketBar(
                symbol=symbol,
                timestamp_iso=f"2026-10-22T09:{i % 60:02d}:00+05:30",
                open=Decimal(str(p - 0.2)),
                high=Decimal(str(p + 0.5)),
                low=Decimal(str(p - 0.5)),
                close=Decimal(str(p)),
                volume=volume,
            )
        )
    return bars


class TestStockScreener:
    """Tests the institutional multi-factor stock screener."""

    def setup_method(self):
        self.screener = StockScreener(
            min_price=Decimal("100.0"),
            min_avg_turnover=Decimal("1000000.0"),  # 10 Lakhs
            min_rvol=Decimal("1.0"),
            require_trend_template=True,
            max_stocks_per_sector=1,
        )

    def test_rejection_below_price_floor(self):
        bars = make_bar_series("PENNY", base_price=20.0, trend_step=0.1, count=30)
        res = self.screener.screen_stock("PENNY", bars[-1], bars[:-1])
        assert res is None

    def test_rejection_below_turnover_threshold(self):
        # High price but near zero volume -> low turnover
        bars = make_bar_series("ILLIQUID", base_price=500.0, trend_step=0.1, count=30, volume=10)
        res = self.screener.screen_stock("ILLIQUID", bars[-1], bars[:-1])
        assert res is None

    def test_trend_template_enforcement(self):
        # Strong uptrend stock passes
        bars = make_bar_series("LEADER", base_price=500.0, trend_step=2.0, count=60, volume=50000)
        res = self.screener.screen_stock("LEADER", bars[-1], bars[:-1])
        assert res is not None
        assert res.symbol == "LEADER"
        assert res.is_trend_template_qualified
        assert res.trend_score >= Decimal("80.0")

        # Downtrend stock fails when require_trend_template=True
        down_bars = make_bar_series("LAGGARD", base_price=500.0, trend_step=-2.0, count=60, volume=50000)
        down_res = self.screener.screen_stock("LAGGARD", down_bars[-1], down_bars[:-1])
        assert down_res is None

    def test_relative_strength_calculation(self):
        stock_bars = make_bar_series("OUTPERFORMER", base_price=100.0, trend_step=1.0, count=30)
        bench_bars = make_bar_series("NIFTY50", base_price=20000.0, trend_step=5.0, count=30)

        res = self.screener.screen_stock(
            "OUTPERFORMER",
            stock_bars[-1],
            stock_bars[:-1],
            benchmark_history=bench_bars,
        )
        assert res is not None
        assert res.relative_strength > Decimal("0.0")  # Outperforming benchmark

    def test_setup_detection_donchian_breakout(self):
        # 30 bars ranging around 200, last bar breaking out to 220 with surge volume
        bars = make_bar_series("BREAKOUT_STOCK", base_price=200.0, trend_step=0.0, count=30)
        breakout_bar = MarketBar(
            symbol="BREAKOUT_STOCK",
            timestamp_iso="2026-10-22T15:00:00+05:30",
            open=Decimal("201.0"),
            high=Decimal("220.0"),
            low=Decimal("200.0"),
            close=Decimal("218.0"),
            volume=300000,
        )
        res = self.screener.screen_stock("BREAKOUT_STOCK", breakout_bar, bars)
        assert res is not None
        assert res.setup_type == SetupType.DONCHIAN_BREAKOUT

    def test_screen_universe_with_sector_capping(self):
        # Two IT stocks and one BANK stock
        it1 = make_bar_series("IT_LEADER_1", base_price=1000.0, trend_step=5.0, count=60)
        it2 = make_bar_series("IT_LEADER_2", base_price=1000.0, trend_step=4.0, count=60)
        bank = make_bar_series("BANK_LEADER", base_price=1000.0, trend_step=3.0, count=60)

        universe = {
            "IT_LEADER_1": it1,
            "IT_LEADER_2": it2,
            "BANK_LEADER": bank,
        }
        sectors = {
            "IT_LEADER_1": "IT",
            "IT_LEADER_2": "IT",
            "BANK_LEADER": "BANKING",
        }

        # Screener has max_stocks_per_sector=1
        selected = self.screener.screen_universe(
            universe,
            symbol_sectors=sectors,
            top_n=5,
        )

        assert len(selected) == 2
        symbols = [s.symbol for s in selected]
        assert "IT_LEADER_1" in symbols
        assert "IT_LEADER_2" not in symbols  # Excluded due to sector cap of 1
        assert "BANK_LEADER" in symbols

    def test_browser_chartink_and_screenerin_criteria(self):
        # Configure screener with Chartink & Screener.in standard filters
        browser_screener = StockScreener(
            min_price=Decimal("100.0"),
            min_avg_turnover=Decimal("1000000.0"),
            min_pct_of_52w_high=Decimal("85.0"),  # Screener.in 52w high proximity
            min_rsi=Decimal("50.0"),              # Chartink RSI momentum floor
        )

        # Leader stock in strong uptrend near highs with RSI > 50
        strong_stock = make_bar_series("CHARTINK_LEADER", base_price=500.0, trend_step=2.0, count=60)
        res = browser_screener.screen_stock("CHARTINK_LEADER", strong_stock[-1], strong_stock[:-1])
        assert res is not None
        assert float(res.metadata["pct_of_52w_high"]) >= 85.0
        assert float(res.metadata["rsi_14"]) >= 50.0

        # Beaten-down stock > 20% off highs should be filtered out by 52w high proximity
        falling_stock = make_bar_series("DOWNTREND", base_price=500.0, trend_step=-3.0, count=60)
        res_falling = browser_screener.screen_stock("DOWNTREND", falling_stock[-1], falling_stock[:-1])
        assert res_falling is None

    def test_invalid_parameters_fail_closed(self):
        with pytest.raises(InvalidStrategyParameterError):
            StockScreener(min_price=Decimal("-10.0"))

        with pytest.raises(InvalidStrategyParameterError):
            StockScreener(max_stocks_per_sector=0)
