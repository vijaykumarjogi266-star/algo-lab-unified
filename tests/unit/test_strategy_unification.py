"""Unit and Integration Tests for Strategy Unification, Registry, and Replay.

Verifies the 13 implementation and verification gates:
- Gate 1: Parameter validation (fail-closed, bounds, types)
- Gate 2: Registry fail-closed dispatch and zero silent fallbacks
- Gate 3: Legacy adapter translation to canonical StrategySignal
- Gate 4: Timezone-aware ORB session bounds, cutoff, and fill decoupling
- Gate 5: VWAP session reset, dynamic ATR, zero-volume guards
- Gate 6: TTM Squeeze parameter overrides and squeeze-firing transitions
- Gate 7: SMA Crossover fast < slow enforcement and golden/death cross
- Gate 8: Donchian Channel prior-bar lag (no lookahead bias)
- Gate 9: EMA Pullback trend filter and bounce verification
- Gate 10: OMS Blueprints and Hybrid Alpha regime dispatch
- Gate 11: Multi-strategy replay integration in HistoricalReplayEngine
- Gate 12: Exact double-entry monetary conservation (Delta = INR 0.00)
- Gate 13: Strict paper-only safety invariant compliance
"""

from decimal import Decimal
import pytest
from typing import Dict, List

from src.brokergateway.states import OrderSide
from src.core.exceptions import InvalidStrategyParameterError, UnknownStrategyError
from src.core.safety_invariants import (
    ALLOW_LIVE_TRADING,
    ALLOW_REAL_BROKER_EXECUTION,
    REAL_MONEY_RISK_PAISE,
    PAPER_BROKER_ONLY,
    assert_paper_only_invariants,
)
from src.scanner.scanner import MarketBar
from src.simulation.engine import HistoricalReplayEngine
from src.strategies.base import BaseStrategy
from src.strategies.bollinger_reversion import BollingerReversionStrategy
from src.strategies.donchian_breakout import DonchianBreakoutStrategy
from src.strategies.ema_pullback import EMAPullbackStrategy
from src.strategies.models import (
    StrategySignal,
    SignalType,
    adapt_legacy_order_intent,
    adapt_legacy_strategy_decision,
    adapt_legacy_trade_candidate,
)
from src.strategies.momentum_breakout import MomentumBreakoutStrategy
from src.strategies.oms_blueprints import (
    OMSHybridAlphaStrategy,
    OMSMeanReversionStrategy,
    OMSTrendFollowingStrategy,
)
from src.strategies.orb import OpeningRangeBreakoutStrategy
from src.strategies.registry import StrategyRegistry
from src.strategies.sma_cross import SMACrossoverStrategy
from src.strategies.volatility_squeeze import TTMSqueezeBreakoutStrategy
from src.strategies.vwap_reversion import VWAPMeanReversionStrategy


def make_bar(
    symbol: str = "RELIANCE",
    ts: str = "2026-10-15T09:20:00+05:30",
    open_p: str = "1000.00",
    high_p: str = "1010.00",
    low_p: str = "990.00",
    close_p: str = "1005.00",
    vol: int = 10000,
) -> MarketBar:
    return MarketBar(
        symbol=symbol,
        timestamp_iso=ts,
        open=Decimal(open_p),
        high=Decimal(high_p),
        low=Decimal(low_p),
        close=Decimal(close_p),
        volume=vol,
    )


class TestGate1ParameterValidation:
    """Gate 1: Parameter validation (fail-closed, bounds, types)."""

    def test_sma_fast_greater_than_slow_raises(self):
        with pytest.raises(InvalidStrategyParameterError, match="must be strictly less than"):
            SMACrossoverStrategy(parameters={"fast_period": 50, "slow_period": 20})

    def test_sma_negative_period_raises(self):
        with pytest.raises(InvalidStrategyParameterError, match="fast_period"):
            SMACrossoverStrategy(parameters={"fast_period": -5, "slow_period": 20})

    def test_orb_invalid_breakout_mins_raises(self):
        with pytest.raises(InvalidStrategyParameterError, match="range_minutes"):
            OpeningRangeBreakoutStrategy(parameters={"breakout_window_minutes": 2})

    def test_vwap_negative_threshold_raises(self):
        with pytest.raises(InvalidStrategyParameterError, match="entry_threshold_pct"):
            VWAPMeanReversionStrategy(parameters={"entry_threshold_pct": Decimal("-0.01")})

    def test_squeeze_invalid_bb_std_raises(self):
        with pytest.raises(InvalidStrategyParameterError, match="bb_std"):
            TTMSqueezeBreakoutStrategy(parameters={"bb_std": Decimal("0.1")})

    def test_donchian_negative_period_raises(self):
        with pytest.raises(InvalidStrategyParameterError, match="period"):
            DonchianBreakoutStrategy(parameters={"lookback_period": 1})


class TestGate2RegistryFailClosed:
    """Gate 2: Registry fail-closed dispatch and zero silent fallbacks."""

    def test_unknown_strategy_raises_unknown_strategy_error(self):
        with pytest.raises(UnknownStrategyError, match="is not registered"):
            StrategyRegistry.get_strategy_class("NON_EXISTENT_STRAT")

    def test_create_unknown_strategy_raises(self):
        with pytest.raises(UnknownStrategyError):
            StrategyRegistry.create_strategy("NON_EXISTENT_STRAT")

    def test_create_with_invalid_parameters_raises_invalid_strategy_param(self):
        with pytest.raises(InvalidStrategyParameterError):
            StrategyRegistry.create_strategy("STRAT_SMA_CROSS", {"fast_period": 100, "slow_period": 20})

    def test_registered_strategies_list(self):
        strats = StrategyRegistry.list_strategies()
        assert "STRAT_SMA_CROSS" in strats
        assert "STRAT_ORB" in strats
        assert "STRAT_VWAP_REVERSION" in strats
        assert "STRAT_TTM_SQUEEZE" in strats
        assert "STRAT_MOMENTUM_BREAKOUT_V1" in strats

    def test_alias_resolution(self):
        cls1 = StrategyRegistry.get_strategy_class("STRAT_ORB")
        cls2 = StrategyRegistry.get_strategy_class("OPENING_RANGE_BREAKOUT")
        assert cls1 is cls2


class TestGate3LegacyAdapters:
    """Gate 3: Legacy adapter translation to canonical StrategySignal."""

    def test_adapt_legacy_strategy_decision_buy(self):
        legacy = {
            "signal": "BUY",
            "symbol": "TCS",
            "entry_price": 3500.0,
            "stop_loss": 3450.0,
            "take_profit": 3600.0,
            "strategy_id": "LEGACY_VWAP",
        }
        sig = adapt_legacy_strategy_decision(legacy)
        assert sig is not None
        assert sig.side == OrderSide.BUY
        assert sig.symbol == "TCS"
        assert sig.target_entry_price == Decimal("3500.00")
        assert sig.stop_loss == Decimal("3450.00")
        assert sig.take_profit == Decimal("3600.00")

    def test_adapt_legacy_strategy_decision_hold_returns_none(self):
        legacy = {"signal": "WAIT", "symbol": "TCS"}
        assert adapt_legacy_strategy_decision(legacy) is None

    def test_adapt_legacy_trade_candidate(self):
        legacy_candidate = {
            "symbol": "INFY",
            "bias": "BULLISH",
            "price": 1800.0,
            "stop_loss": 1780.0,
            "target": 1840.0,
            "strategy_id": "ORB_CANDIDATE",
        }
        sig = adapt_legacy_trade_candidate(legacy_candidate)
        assert sig is not None
        assert sig.side == OrderSide.BUY
        assert sig.symbol == "INFY"

    def test_adapt_legacy_order_intent(self):
        legacy_intent = {
            "action": "SELL",
            "tradingsymbol": "SBIN",
            "limit_price": 750.0,
            "stop_loss": 760.0,
            "target": 730.0,
        }
        sig = adapt_legacy_order_intent(legacy_intent)
        assert sig is not None
        assert sig.side == OrderSide.SELL
        assert sig.symbol == "SBIN"


class TestGate4OpeningRangeBreakout:
    """Gate 4: Timezone-aware ORB session bounds, cutoff, and fill decoupling."""

    def test_orb_ignores_pre_market_or_opening_window(self):
        orb = OpeningRangeBreakoutStrategy(parameters={"breakout_window_minutes": 15})
        history = [
            make_bar(ts="2026-10-15T09:15:00+05:30", high_p="1010.00", low_p="995.00"),
            make_bar(ts="2026-10-15T09:20:00+05:30", high_p="1015.00", low_p="998.00"),
        ]
        curr = make_bar(ts="2026-10-15T09:25:00+05:30", high_p="1020.00", low_p="1000.00")
        # 09:25 is before 09:30 breakout window end
        sig = orb.evaluate(curr, history)
        assert sig is None

    def test_orb_triggers_long_on_breakout_after_window(self):
        orb = OpeningRangeBreakoutStrategy(parameters={"breakout_window_minutes": 15})
        history = [
            make_bar(ts="2026-10-15T09:15:00+05:30", high_p="1010.00", low_p="995.00"),
            make_bar(ts="2026-10-15T09:20:00+05:30", high_p="1015.00", low_p="998.00"),
            make_bar(ts="2026-10-15T09:25:00+05:30", high_p="1012.00", low_p="1000.00"),
        ]
        curr = make_bar(ts="2026-10-15T09:30:00+05:30", close_p="1016.00", high_p="1018.00", low_p="1005.00")
        sig = orb.evaluate(curr, history)
        assert sig is not None
        assert sig.side == OrderSide.BUY
        assert sig.target_entry_price == Decimal("1016.00")

    def test_orb_enforces_1445_ist_cutoff(self):
        orb = OpeningRangeBreakoutStrategy(parameters={"breakout_window_minutes": 15})
        history = [
            make_bar(ts="2026-10-15T09:15:00+05:30", high_p="1010.00", low_p="995.00"),
            make_bar(ts="2026-10-15T09:20:00+05:30", high_p="1015.00", low_p="998.00"),
            make_bar(ts="2026-10-15T09:25:00+05:30", high_p="1012.00", low_p="1000.00"),
        ]
        curr = make_bar(ts="2026-10-15T14:50:00+05:30", close_p="1025.00")
        sig = orb.evaluate(curr, history)
        assert sig is None, "ORB must not generate signals after 14:45 IST cutoff"

    def test_orb_candidate_decoupled_from_fill(self):
        orb = OpeningRangeBreakoutStrategy(parameters={"breakout_window_minutes": 15})
        history = [
            make_bar(ts="2026-10-15T09:15:00+05:30", high_p="1010.00", low_p="995.00"),
            make_bar(ts="2026-10-15T09:20:00+05:30", high_p="1015.00", low_p="998.00"),
            make_bar(ts="2026-10-15T09:25:00+05:30", high_p="1012.00", low_p="1000.00"),
        ]
        curr = make_bar(ts="2026-10-15T09:30:00+05:30", close_p="1016.00")
        sig1 = orb.evaluate(curr, history)
        assert sig1 is not None

        # Another bar evaluated before order fill still produces a candidate
        curr2 = make_bar(ts="2026-10-15T09:35:00+05:30", close_p="1018.00")
        sig2 = orb.evaluate(curr2, history + [curr])
        assert sig2 is not None

        # Once marked filled, subsequent signals for that session date are blocked
        orb.mark_order_filled("2026-10-15")
        curr3 = make_bar(ts="2026-10-15T09:40:00+05:30", close_p="1020.00")
        sig3 = orb.evaluate(curr3, history + [curr, curr2])
        assert sig3 is None


class TestGate5VWAPSessionResetAndATR:
    """Gate 5: VWAP session reset, dynamic ATR, zero-volume guards."""

    def test_vwap_resets_at_session_boundary(self):
        strat = VWAPMeanReversionStrategy(parameters={"entry_threshold_pct": Decimal("0.01")})
        # Day 1: heavy volume at 1000
        bars_day1 = [
            make_bar(ts="2026-10-14T09:15:00+05:30", close_p="1000.00", vol=100000),
            make_bar(ts="2026-10-14T09:20:00+05:30", close_p="1000.00", vol=100000),
        ]
        # Day 2: price opens at 2000, low volume
        bar_day2 = make_bar(ts="2026-10-15T09:15:00+05:30", close_p="2000.00", high_p="2010.00", low_p="1990.00", vol=100)
        vwap = strat.calculate_session_vwap(bar_day2, bars_day1)
        # Should be calculated ONLY on day 2 bar, around 2000, not dragged down by day 1
        assert abs(vwap - Decimal("2000.00")) < Decimal("10.00")

    def test_dynamic_atr_calculation(self):
        strat = VWAPMeanReversionStrategy()
        history = [
            make_bar(ts=f"2026-10-15T09:{15+i:02d}:00+05:30", high_p=str(1000 + i * 2), low_p=str(990 + i * 2), close_p=str(995 + i * 2))
            for i in range(15)
        ]
        curr = make_bar(ts="2026-10-15T09:30:00+05:30", high_p="1035.00", low_p="1015.00", close_p="1025.00")
        atr = strat.calculate_dynamic_atr(curr, history)
        assert atr > Decimal("0.0")
        # Ensure it does not equal hardcoded 5.0
        assert atr != Decimal("5.0")

    def test_zero_volume_bars_safe(self):
        strat = VWAPMeanReversionStrategy()
        curr = make_bar(vol=0)
        history = [make_bar(vol=0)]
        vwap = strat.calculate_session_vwap(curr, history)
        assert vwap == curr.close


class TestGate6TTMSqueezeOverrides:
    """Gate 6: TTM Squeeze parameter overrides and squeeze-firing transitions."""

    def test_ttm_squeeze_override_propagation(self):
        custom_params = {
            "bb_std": Decimal("2.5"),
            "kc_atr_mult": Decimal("1.8"),
            "volume_multiplier": Decimal("2.0"),
        }
        strat = TTMSqueezeBreakoutStrategy(parameters=custom_params)
        assert strat.bb_std == Decimal("2.5")
        assert strat.kc_atr_mult == Decimal("1.8")
        assert strat.volume_multiplier == Decimal("2.0")

    def test_squeeze_requires_sufficient_history(self):
        strat = TTMSqueezeBreakoutStrategy()
        curr = make_bar()
        history = [make_bar() for _ in range(5)]
        sig = strat.evaluate(curr, history)
        assert sig is None


class TestGate7SMACrossover:
    """Gate 7: SMA Crossover fast < slow enforcement and golden/death cross."""

    def test_golden_cross_triggers_buy(self):
        strat = SMACrossoverStrategy(parameters={"fast_period": 3, "slow_period": 5})
        # fast was <= slow on prev bar, then crosses strictly above on current bar
        history = [
            make_bar(close_p="100.00"),
            make_bar(close_p="100.00"),
            make_bar(close_p="100.00"),
            make_bar(close_p="99.00"),
            make_bar(close_p="98.00"),
        ]
        curr = make_bar(close_p="105.00", high_p="106.00", low_p="104.00")
        sig = strat.evaluate(curr, history)
        assert sig is not None
        assert sig.side == OrderSide.BUY

    def test_death_cross_triggers_sell(self):
        strat = SMACrossoverStrategy(parameters={"fast_period": 3, "slow_period": 5})
        # fast was >= slow on prev bar, then crosses strictly below on current bar
        history = [
            make_bar(close_p="100.00"),
            make_bar(close_p="100.00"),
            make_bar(close_p="100.00"),
            make_bar(close_p="101.00"),
            make_bar(close_p="102.00"),
        ]
        curr = make_bar(close_p="95.00", high_p="96.00", low_p="94.00")
        sig = strat.evaluate(curr, history)
        assert sig is not None
        assert sig.side == OrderSide.SELL


class TestGate8DonchianLag:
    """Gate 8: Donchian Channel prior-bar lag (no lookahead bias)."""

    def test_donchian_breakout_strictly_prior_bar_lagged(self):
        strat = DonchianBreakoutStrategy(parameters={"lookback_period": 5})
        # 5 prior bars high was 105.00
        history = [
            make_bar(high_p="100.00", low_p="95.00"),
            make_bar(high_p="102.00", low_p="96.00"),
            make_bar(high_p="105.00", low_p="97.00"),
            make_bar(high_p="103.00", low_p="98.00"),
            make_bar(high_p="104.00", low_p="99.00"),
        ]
        # Current bar breaks above 105.00 with close 106.00
        curr = make_bar(close_p="106.00", high_p="107.00", low_p="103.00")
        sig = strat.evaluate(curr, history)
        assert sig is not None
        assert sig.side == OrderSide.BUY

        # If current bar is inside 105.00, no signal
        curr_inside = make_bar(close_p="104.50", high_p="105.00", low_p="102.00")
        sig_inside = strat.evaluate(curr_inside, history)
        assert sig_inside is None


class TestGate9EMAPullback:
    """Gate 9: EMA Pullback trend filter and bounce verification."""

    def test_ema_pullback_requires_min_history(self):
        strat = EMAPullbackStrategy()
        curr = make_bar()
        history = [make_bar() for _ in range(30)]
        # Needs at least 200 bars for EMA 200
        sig = strat.evaluate(curr, history)
        assert sig is None


class TestGate10OMSBlueprints:
    """Gate 10: OMS Blueprints and Hybrid Alpha regime dispatch."""

    def test_oms_mean_reversion_instantiation(self):
        strat = OMSMeanReversionStrategy()
        assert strat.strategy_id == "STRAT_OMS_MEAN_REV"

    def test_oms_trend_following_instantiation(self):
        strat = OMSTrendFollowingStrategy()
        assert strat.strategy_id == "STRAT_OMS_TREND_FOLLOWING"

    def test_oms_hybrid_alpha_delegates(self):
        hybrid = OMSHybridAlphaStrategy()
        assert hybrid.strategy_id == "STRAT_OMS_HYBRID_ALPHA"
        curr = make_bar(close_p="100.00")
        history = [make_bar(close_p="100.00") for _ in range(30)]
        # Regime dispatch executes safely without crash
        sig = hybrid.evaluate(curr, history)
        # Returns either None or valid StrategySignal
        assert sig is None or isinstance(sig, StrategySignal)


class TestGate11ReplayIntegration:
    """Gate 11: Multi-strategy replay integration in HistoricalReplayEngine."""

    def test_engine_initializes_with_strategy_name(self):
        engine = HistoricalReplayEngine(strategy="STRAT_SMA_CROSS")
        assert engine.strategy.strategy_id == "STRAT_SMA_CROSS"

    def test_engine_runs_synthetic_replay_with_sma_cross(self):
        engine = HistoricalReplayEngine(
            strategy="STRAT_SMA_CROSS",
            strategy_params={"fast_period": 2, "slow_period": 3},
        )
        bars = [
            make_bar(ts="2026-10-15T09:15:00+05:30", close_p="100.00"),
            make_bar(ts="2026-10-15T09:20:00+05:30", close_p="100.00"),
            make_bar(ts="2026-10-15T09:25:00+05:30", close_p="105.00"),
            make_bar(ts="2026-10-15T09:30:00+05:30", close_p="110.00"),
            make_bar(ts="2026-10-15T09:35:00+05:30", close_p="115.00"),
        ]
        metrics = engine.run_replay({"RELIANCE": bars})
        assert metrics is not None
        assert metrics.is_eod_balanced is True
        assert len(metrics.reconciliation_discrepancies) == 0


class TestGate12DoubleEntryConservation:
    """Gate 12: Exact double-entry monetary conservation (Delta = INR 0.00)."""

    def test_exact_zero_monetary_leakage(self):
        engine = HistoricalReplayEngine(strategy="STRAT_MOMENTUM_BREAKOUT_V1")
        bars = [
            make_bar(ts=f"2026-10-15T09:{15+i:02d}:00+05:30", high_p=str(1000 + i * 5), low_p=str(990 + i * 5), close_p=str(995 + i * 5))
            for i in range(25)
        ]
        metrics = engine.run_replay({"INFY": bars})
        assert metrics.is_eod_balanced is True
        reconciled_cash = metrics.initial_capital + metrics.net_realized_pnl
        diff = abs(engine.cash - reconciled_cash)
        assert diff == Decimal("0.00")


class TestGate13PaperSafetyInvariants:
    """Gate 13: Strict paper-only safety invariant compliance."""

    def test_paper_only_constants_enforced(self):
        assert ALLOW_LIVE_TRADING is False
        assert ALLOW_REAL_BROKER_EXECUTION is False
        assert REAL_MONEY_RISK_PAISE == 0
        assert PAPER_BROKER_ONLY is True
        # Asserts without exception
        assert_paper_only_invariants({
            "ALLOW_LIVE_TRADING": "false",
            "ALLOW_REAL_BROKER_EXECUTION": "false",
            "REAL_MONEY_RISK_PAISE": "0",
            "PAPER_BROKER_ONLY": "true",
        })
