"""Unit Tests for IntradayMarketScanner and MomentumBreakoutStrategy.

Proves:
1. Scanner enforces minimum history bar threshold (>= 20 bars).
2. Scanner rejects illiquid assets with wide bid-ask spread.
3. Scanner rejects bars with insufficient relative volume (RVOL < 1.5).
4. Scanner correctly identifies bullish and bearish breakout candidates with ATR.
5. Strategy converts bullish candidate into a BUY signal with exact 2:1 Reward-to-Risk.
6. Strategy converts bearish candidate into a SELL signal with exact 2:1 Reward-to-Risk.
7. Strategy rejects candidates lacking directional bias.
8. Signal confidence scales with RVOL and caps conservatively at 1.0.
"""

from decimal import Decimal
import pytest

from src.brokergateway.states import OrderSide
from src.scanner.scanner import IntradayMarketScanner, MarketBar, ScanCandidate
from src.strategies.momentum_breakout import MomentumBreakoutStrategy


def _create_history_bars(count: int, base_price: Decimal = Decimal("100.0")) -> list[MarketBar]:
    """Generates synthetic historical bars for testing."""
    bars = []
    for i in range(count):
        p = base_price + Decimal(str(i * 0.1))
        bars.append(
            MarketBar(
                symbol="RELIANCE",
                timestamp_iso=f"2026-10-19T09:{i+15:02d}:00Z",
                open=p,
                high=p + Decimal("1.0"),
                low=p - Decimal("1.0"),
                close=p + Decimal("0.2"),
                volume=1000,
            )
        )
    return bars


class TestIntradayMarketScanner:
    """Verifies technical market scanner filters and breakout detection."""

    def test_scanner_requires_minimum_history_bars(self):
        scanner = IntradayMarketScanner(min_rvol=Decimal("1.5"))
        short_history = _create_history_bars(15)  # Less than 20 required
        current_bar = MarketBar(
            symbol="RELIANCE",
            timestamp_iso="2026-10-19T09:35:00Z",
            open=Decimal("105.0"),
            high=Decimal("108.0"),
            low=Decimal("104.5"),
            close=Decimal("107.5"),
            volume=3000,
        )
        assert scanner.scan_symbol("RELIANCE", current_bar, short_history) is None

    def test_scanner_rejects_wide_spread(self):
        scanner = IntradayMarketScanner(min_rvol=Decimal("1.5"), max_spread_pct=Decimal("0.001"))
        history = _create_history_bars(25)
        current_bar = MarketBar(
            symbol="RELIANCE",
            timestamp_iso="2026-10-19T09:40:00Z",
            open=Decimal("105.0"),
            high=Decimal("108.0"),
            low=Decimal("104.5"),
            close=Decimal("107.5"),
            volume=3000,
        )
        # Spread = 0.50 on 107.5 close -> 0.465% > 0.10% threshold
        candidate = scanner.scan_symbol(
            "RELIANCE", current_bar, history, bid_ask_spread=Decimal("0.50")
        )
        assert candidate is None

    def test_scanner_rejects_low_rvol(self):
        scanner = IntradayMarketScanner(min_rvol=Decimal("1.5"))
        history = _create_history_bars(25)  # Average volume = 1000
        current_bar = MarketBar(
            symbol="RELIANCE",
            timestamp_iso="2026-10-19T09:40:00Z",
            open=Decimal("105.0"),
            high=Decimal("108.0"),
            low=Decimal("104.5"),
            close=Decimal("107.5"),
            volume=1200,  # RVOL = 1.2x < 1.5x minimum
        )
        candidate = scanner.scan_symbol(
            "RELIANCE", current_bar, history, bid_ask_spread=Decimal("0.05")
        )
        assert candidate is None

    def test_scanner_detects_bullish_breakout(self):
        scanner = IntradayMarketScanner(min_rvol=Decimal("1.5"))
        history = _create_history_bars(25)
        last_high = history[-1].high  # Breakout level
        current_bar = MarketBar(
            symbol="RELIANCE",
            timestamp_iso="2026-10-19T09:40:00Z",
            open=last_high,
            high=last_high + Decimal("3.0"),
            low=last_high - Decimal("0.5"),
            close=last_high + Decimal("2.5"),  # Clean breakout above previous high
            volume=2500,  # RVOL = 2.5x > 1.5x
        )
        candidate = scanner.scan_symbol(
            "RELIANCE", current_bar, history, bid_ask_spread=Decimal("0.05")
        )
        assert candidate is not None
        assert candidate.symbol == "RELIANCE"
        assert candidate.bias == "BULLISH"
        assert candidate.rvol == Decimal("2.5")
        assert candidate.current_price == current_bar.close
        assert candidate.atr > Decimal("0.0")

    def test_scanner_detects_bearish_breakout(self):
        scanner = IntradayMarketScanner(min_rvol=Decimal("1.5"))
        history = _create_history_bars(25)
        last_low = history[-1].low  # Breakdown level
        current_bar = MarketBar(
            symbol="RELIANCE",
            timestamp_iso="2026-10-19T09:40:00Z",
            open=last_low,
            high=last_low + Decimal("0.5"),
            low=last_low - Decimal("3.0"),
            close=last_low - Decimal("2.5"),  # Breakdown below previous low
            volume=2000,  # RVOL = 2.0x > 1.5x
        )
        candidate = scanner.scan_symbol(
            "RELIANCE", current_bar, history, bid_ask_spread=Decimal("0.05")
        )
        assert candidate is not None
        assert candidate.bias == "BEARISH"
        assert candidate.rvol == Decimal("2.0")


class TestMomentumBreakoutStrategy:
    """Verifies strategy signal generation and 2:1 risk/reward geometry."""

    def test_strategy_generates_long_signal_with_2_to_1_rr(self):
        strategy = MomentumBreakoutStrategy(risk_reward_ratio=Decimal("2.0"))
        candidate = ScanCandidate(
            symbol="TCS",
            timestamp_iso="2026-10-19T09:45:00Z",
            current_price=Decimal("3500.00"),
            rvol=Decimal("2.1"),
            atr=Decimal("25.00"),
            spread_pct=Decimal("0.0005"),
            bias="BULLISH",
        )
        signal = strategy.evaluate_candidate(candidate)
        assert signal is not None
        assert signal.symbol == "TCS"
        assert signal.side == OrderSide.BUY
        assert signal.target_entry_price == Decimal("3500.00")
        assert signal.stop_loss == Decimal("3475.00")  # 3500 - 25
        assert signal.take_profit == Decimal("3550.00")  # 3500 + 50 (2:1 RR)
        assert signal.confidence_score == Decimal("0.7")  # 2.1 / 3.0

    def test_strategy_generates_short_signal_with_2_to_1_rr(self):
        strategy = MomentumBreakoutStrategy(risk_reward_ratio=Decimal("2.0"))
        candidate = ScanCandidate(
            symbol="INFY",
            timestamp_iso="2026-10-19T09:45:00Z",
            current_price=Decimal("1800.00"),
            rvol=Decimal("3.3"),
            atr=Decimal("15.00"),
            spread_pct=Decimal("0.0004"),
            bias="BEARISH",
        )
        signal = strategy.evaluate_candidate(candidate)
        assert signal is not None
        assert signal.symbol == "INFY"
        assert signal.side == OrderSide.SELL
        assert signal.target_entry_price == Decimal("1800.00")
        assert signal.stop_loss == Decimal("1815.00")  # 1800 + 15
        assert signal.take_profit == Decimal("1770.00")  # 1800 - 30 (2:1 RR)
        assert signal.confidence_score == Decimal("1.0")  # Capped at 1.0

    def test_strategy_rejects_neutral_bias(self):
        strategy = MomentumBreakoutStrategy()
        candidate = ScanCandidate(
            symbol="WIPRO",
            timestamp_iso="2026-10-19T09:45:00Z",
            current_price=Decimal("500.00"),
            rvol=Decimal("2.0"),
            atr=Decimal("5.00"),
            spread_pct=Decimal("0.0002"),
            bias="NEUTRAL",
        )
        assert strategy.evaluate_candidate(candidate) is None
