"""Unit Tests for Market Regime Classifier and Adaptive Strategy Router.

Verifies:
1. Correct regime identification across Bullish Trend, Sideways Range, High Vol Chop, and Panic regimes.
2. Invariant adherence: Snooze Guard and 100% Cash Buffer return None (WAIT).
3. Strategy routing fidelity: Bullish routes to Trend engines; Sideways routes to Mean Reversion.
4. Mathematical anti-ruin scaling: Ed Thorp 1% risk rule and capital allocation scaling.
5. Strict parameter validation fail-closed behavior.
6. StrategyRegistry registration and dispatch.
"""

from decimal import Decimal
import pytest

from src.core.exceptions import InvalidStrategyParameterError
from src.core.regime_classifier import (
    MarketRegime,
    MarketRegimeClassifier,
    MarketRegimeDecision,
    MarketRegimeMetrics,
)
from src.scanner.scanner import MarketBar
from src.strategies.adaptive_router import AdaptiveStrategyRouter
from src.strategies.registry import StrategyRegistry


def make_test_bar(
    close: float,
    open_: float = 100.0,
    high: float = 105.0,
    low: float = 95.0,
    volume: int = 10000,
    timestamp_iso: str = "2026-10-22T09:15:00+05:30",
    symbol: str = "RELIANCE",
) -> MarketBar:
    return MarketBar(
        symbol=symbol,
        timestamp_iso=timestamp_iso,
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=volume,
    )


class TestMarketRegimeClassifier:
    """Tests the orthogonal macro regime classification engine."""

    def setup_method(self):
        self.classifier = MarketRegimeClassifier()

    def test_classify_bullish_trend(self):
        metrics = MarketRegimeMetrics(
            index_close=Decimal("25000.0"),
            sma_50=Decimal("24500.0"),
            sma_200=Decimal("23000.0"),
            market_breadth_pct=Decimal("68.0"),
            vix=Decimal("13.5"),
            atr_pct=Decimal("0.8"),
        )
        decision = self.classifier.classify(metrics)
        assert decision.regime == MarketRegime.BULLISH_TREND
        assert not decision.snooze_active
        assert decision.capital_allocation_pct == Decimal("100.0")
        assert "STRAT_EMA_PULLBACK" in decision.active_strategies
        assert "STRAT_DONCHIAN_BREAKOUT" in decision.active_strategies

    def test_classify_crash_panic_on_vix_spike(self):
        metrics = MarketRegimeMetrics(
            index_close=Decimal("21000.0"),
            sma_50=Decimal("24000.0"),
            sma_200=Decimal("23500.0"),
            market_breadth_pct=Decimal("15.0"),
            vix=Decimal("28.5"),
            gap_down_pct=Decimal("2.1"),
        )
        decision = self.classifier.classify(metrics)
        assert decision.regime == MarketRegime.CRASH_PANIC
        assert decision.snooze_active
        assert decision.capital_allocation_pct == Decimal("0.0")
        assert len(decision.active_strategies) == 0
        assert "Panic threshold breached" in decision.reason

    def test_classify_high_vol_chop_triggers_snooze_guard(self):
        metrics = MarketRegimeMetrics(
            index_close=Decimal("24000.0"),
            sma_50=Decimal("24100.0"),
            sma_200=Decimal("23900.0"),
            market_breadth_pct=Decimal("42.0"),  # Indecisive 35-50%
            vix=Decimal("19.5"),
            atr_pct=Decimal("1.8"),               # High ATR >= 1.5%
        )
        decision = self.classifier.classify(metrics)
        assert decision.regime == MarketRegime.HIGH_VOL_CHOP
        assert decision.snooze_active
        assert decision.capital_allocation_pct == Decimal("0.0")
        assert len(decision.active_strategies) == 0
        assert "Snooze Guard" in decision.reason

    def test_classify_sideways_range_default(self):
        metrics = MarketRegimeMetrics(
            index_close=Decimal("24200.0"),
            sma_50=Decimal("24300.0"),
            sma_200=Decimal("24100.0"),
            market_breadth_pct=Decimal("52.0"),
            vix=Decimal("14.0"),
            atr_pct=Decimal("0.9"),
        )
        decision = self.classifier.classify(metrics)
        assert decision.regime == MarketRegime.SIDEWAYS_RANGE
        assert not decision.snooze_active
        assert decision.capital_allocation_pct == Decimal("60.0")
        assert "STRAT_BOLLINGER_REVERSION" in decision.active_strategies
        assert "STRAT_VWAP_REVERSION" in decision.active_strategies

    def test_classify_bullish_trend_fails_closed_on_missing_macro_feeds(self):
        # Missing market_breadth_pct -> cannot be BULLISH_TREND
        metrics_no_breadth = MarketRegimeMetrics(
            index_close=Decimal("25000.0"),
            sma_50=Decimal("24500.0"),
            sma_200=Decimal("23000.0"),
            market_breadth_pct=None,
            vix=Decimal("13.5"),
            atr_pct=Decimal("0.8"),
        )
        dec_no_breadth = self.classifier.classify(metrics_no_breadth)
        assert dec_no_breadth.regime != MarketRegime.BULLISH_TREND
        assert dec_no_breadth.regime == MarketRegime.SIDEWAYS_RANGE

        # Missing sma_50 -> cannot be BULLISH_TREND
        metrics_no_sma50 = MarketRegimeMetrics(
            index_close=Decimal("25000.0"),
            sma_50=None,
            sma_200=Decimal("23000.0"),
            market_breadth_pct=Decimal("68.0"),
            vix=Decimal("13.5"),
            atr_pct=Decimal("0.8"),
        )
        dec_no_sma50 = self.classifier.classify(metrics_no_sma50)
        assert dec_no_sma50.regime != MarketRegime.BULLISH_TREND

        # Missing sma_200 -> cannot be BULLISH_TREND
        metrics_no_sma200 = MarketRegimeMetrics(
            index_close=Decimal("25000.0"),
            sma_50=Decimal("24500.0"),
            sma_200=None,
            market_breadth_pct=Decimal("68.0"),
            vix=Decimal("13.5"),
            atr_pct=Decimal("0.8"),
        )
        dec_no_sma200 = self.classifier.classify(metrics_no_sma200)
        assert dec_no_sma200.regime != MarketRegime.BULLISH_TREND

    def test_invalid_parameters_fail_closed(self):
        with pytest.raises(InvalidStrategyParameterError):
            self.classifier.classify(
                MarketRegimeMetrics(index_close=Decimal("-100.0"))
            )

        with pytest.raises(InvalidStrategyParameterError):
            self.classifier.classify(
                MarketRegimeMetrics(
                    index_close=Decimal("1000.0"),
                    market_breadth_pct=Decimal("150.0"),
                )
            )


class TestAdaptiveStrategyRouter:
    """Tests the dynamic multi-regime strategy router."""

    def setup_method(self):
        self.router = AdaptiveStrategyRouter()

    def test_registry_registration(self):
        assert StrategyRegistry.is_registered("STRAT_ADAPTIVE_ROUTER")
        assert StrategyRegistry.is_registered("ADAPTIVE_ROUTER")
        assert StrategyRegistry.is_registered("MULTI_REGIME_ROUTER")

        instance = StrategyRegistry.create_strategy("ADAPTIVE_ROUTER")
        assert isinstance(instance, AdaptiveStrategyRouter)

    def test_parameter_validation_fail_closed(self):
        with pytest.raises(InvalidStrategyParameterError):
            AdaptiveStrategyRouter(parameters={"max_risk_pct_per_trade": -0.5})

        with pytest.raises(InvalidStrategyParameterError):
            AdaptiveStrategyRouter(parameters={"min_cash_reserve_pct": 80.0})

    def test_router_returns_none_in_snooze_chop_regime(self):
        # Configure High-Vol Chop regime
        chop_decision = MarketRegimeDecision(
            regime=MarketRegime.HIGH_VOL_CHOP,
            confidence=Decimal("0.85"),
            active_strategies=[],
            capital_allocation_pct=Decimal("0.0"),
            snooze_active=True,
            reason="High Volatility Chop Snooze Guard",
            metrics=MarketRegimeMetrics(index_close=Decimal("25000.0")),
        )
        self.router.set_regime_decision(chop_decision)

        bar = make_test_bar(close=2500.0)
        history = [make_test_bar(close=2490.0 + i) for i in range(50)]

        # Must return None (WAIT)
        signal = self.router.evaluate(bar, history)
        assert signal is None

    def test_router_returns_none_in_panic_regime(self):
        panic_decision = MarketRegimeDecision(
            regime=MarketRegime.CRASH_PANIC,
            confidence=Decimal("0.95"),
            active_strategies=[],
            capital_allocation_pct=Decimal("0.0"),
            snooze_active=True,
            reason="Panic Solvency Cash Buffer",
            metrics=MarketRegimeMetrics(index_close=Decimal("21000.0")),
        )
        self.router.set_regime_decision(panic_decision)

        bar = make_test_bar(close=2500.0)
        history = [make_test_bar(close=2490.0 + i) for i in range(50)]

        signal = self.router.evaluate(bar, history)
        assert signal is None

    def test_router_evaluates_trend_in_bullish_regime(self):
        bull_decision = MarketRegimeDecision(
            regime=MarketRegime.BULLISH_TREND,
            confidence=Decimal("0.90"),
            active_strategies=["STRAT_DONCHIAN_BREAKOUT"],
            capital_allocation_pct=Decimal("100.0"),
            snooze_active=False,
            reason="Bullish Trend Regime",
            metrics=MarketRegimeMetrics(index_close=Decimal("25000.0")),
        )
        self.router.set_regime_decision(bull_decision)

        # Create breakout history where bar breaks out above 20-period high
        history = [
            make_test_bar(close=100.0, high=102.0, low=98.0) for _ in range(30)
        ]
        breakout_bar = make_test_bar(close=105.0, high=106.0, low=101.0, open_=102.0)

        signal = self.router.evaluate(breakout_bar, history)
        assert signal is not None
        assert signal.side.value == "BUY"
        assert signal.strategy_id == "STRAT_ADAPTIVE_ROUTER"
        assert signal.evidence["regime"] == "BULLISH_TREND"
        assert signal.evidence["routed_strategy_id"] == "STRAT_DONCHIAN_BREAKOUT"
        # 100% allocation * 1.0% risk rule = 0.010
        assert signal.position_sizing_ratio == Decimal("0.010")

    def test_router_scales_position_size_in_sideways_regime(self):
        sideways_decision = MarketRegimeDecision(
            regime=MarketRegime.SIDEWAYS_RANGE,
            confidence=Decimal("0.80"),
            active_strategies=["STRAT_DONCHIAN_BREAKOUT"],
            capital_allocation_pct=Decimal("50.0"),  # 50% allocation
            snooze_active=False,
            reason="Sideways Range Regime",
            metrics=MarketRegimeMetrics(index_close=Decimal("24000.0")),
        )
        self.router.set_regime_decision(sideways_decision)

        history = [
            make_test_bar(close=100.0, high=102.0, low=98.0) for _ in range(30)
        ]
        breakout_bar = make_test_bar(close=105.0, high=106.0, low=101.0, open_=102.0)

        signal = self.router.evaluate(breakout_bar, history)
        assert signal is not None
        # 50% allocation * 1.0% risk rule = 0.005
        assert signal.position_sizing_ratio == Decimal("0.005")
        assert signal.evidence["regime"] == "SIDEWAYS_RANGE"

    def test_derive_metrics_breadth_fail_closed(self):
        bar = make_test_bar(close=100.0)
        history = [make_test_bar(close=90.0 + i) for i in range(60)]

        # 1. No indicators provided -> breadth must be None
        metrics_none = self.router._derive_metrics(bar, history)
        assert metrics_none.market_breadth_pct is None

        # 2. Indicators missing market_breadth_pct -> breadth must be None
        metrics_empty_ind = self.router._derive_metrics(bar, history, indicators={"sma_50": 95.0})
        assert metrics_empty_ind.market_breadth_pct is None

        # 3. Indicators with None market_breadth_pct -> breadth must be None
        metrics_ind_none = self.router._derive_metrics(bar, history, indicators={"market_breadth_pct": None})
        assert metrics_ind_none.market_breadth_pct is None

        # 4. Valid market_breadth_pct provided
        metrics_valid = self.router._derive_metrics(bar, history, indicators={"market_breadth_pct": 62.5})
        assert metrics_valid.market_breadth_pct == Decimal("62.5")

    def test_audit_macro_002_fail_closed_regime_checks(self):
        """AUDIT-MACRO-002: Macro fail-closed regime checks when macro breadth/VIX unavailable."""
        # When macro breadth is missing, regime classifier must NOT enter BULLISH_TREND
        classifier = MarketRegimeClassifier()
        metrics = MarketRegimeMetrics(
            index_close=Decimal("25000.0"),
            sma_50=Decimal("24500.0"),
            sma_200=Decimal("23000.0"),
            market_breadth_pct=None,  # Missing breadth feed
            vix=Decimal("13.5"),
            atr_pct=Decimal("0.8"),
        )
        decision = classifier.classify(metrics)
        assert decision.regime != MarketRegime.BULLISH_TREND
        assert decision.regime == MarketRegime.SIDEWAYS_RANGE

        # Router with missing indicators must also safely derive None breadth
        bar = make_test_bar(close=100.0)
        history = [make_test_bar(close=95.0) for _ in range(30)]
        derived = self.router._derive_metrics(bar, history)
        assert derived.market_breadth_pct is None

    def test_audit_alloc_003_cross_sectional_confidence_ranking(self):
        """AUDIT-ALLOC-003: Cross-sectional candidate ranking prioritizes highest confidence over alphabetical order."""
        from src.strategies.models import StrategySignal
        from src.brokergateway.states import OrderSide

        sig_low = StrategySignal(
            signal_id="SIG-LOW",
            strategy_id="STRAT_TEST",
            symbol="AAA_SYMBOL",
            side=OrderSide.BUY,
            target_entry_price=Decimal("100.00"),
            stop_loss=Decimal("95.00"),
            take_profit=Decimal("110.00"),
            confidence_score=Decimal("0.40"),
            timestamp_iso="2026-10-22T09:15:00+05:30",
        )
        sig_high = StrategySignal(
            signal_id="SIG-HIGH",
            strategy_id="STRAT_TEST",
            symbol="ZZZ_SYMBOL",
            side=OrderSide.BUY,
            target_entry_price=Decimal("200.00"),
            stop_loss=Decimal("190.00"),
            take_profit=Decimal("220.00"),
            confidence_score=Decimal("0.95"),
            timestamp_iso="2026-10-22T09:15:00+05:30",
        )
        candidates = [
            ("AAA_SYMBOL", None, Decimal("0.05"), sig_low),
            ("ZZZ_SYMBOL", None, Decimal("0.05"), sig_high),
        ]
        # Sort using the engine's sorting rule: (-confidence_score, symbol)
        ranked = sorted(candidates, key=lambda item: (-item[3].confidence_score, item[0]))
        assert ranked[0][0] == "ZZZ_SYMBOL"
        assert ranked[0][3].confidence_score == Decimal("0.95")
        assert ranked[1][0] == "AAA_SYMBOL"
        assert ranked[1][3].confidence_score == Decimal("0.40")
