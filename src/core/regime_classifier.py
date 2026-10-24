"""Market Regime Classifier for Algo Lab Unified.

Provides authoritative, multi-dimensional market regime classification
to dynamically navigate market cycles, prevent account ruin during market
crashes, and eliminate statutory fee bleed during volatile sideways chop.

Regimes:
- BULLISH_TREND: Index > 200 SMA, Breadth >= 55%, low/moderate volatility.
                 Dispatches trend-following strategies (EMA Pullback, Donchian).
- SIDEWAYS_RANGE: Low volatility consolidation (σ <= 1.2%), mixed breadth.
                  Dispatches mean-reversion strategies (Bollinger, VWAP Reversion).
- HIGH_VOL_CHOP: High intraday volatility without directional alignment.
                 Engages Symmetry Snooze Guard (0% capital risk, 0 trades).
- CRASH_PANIC: VIX spike, extreme gap-down, or market breadth collapse.
               Enforces 100% Defensive Cash Solvency Buffer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from src.core.exceptions import InvalidStrategyParameterError


class MarketRegime(str, Enum):
    """Authoritative market regime classification."""
    BULLISH_TREND = "BULLISH_TREND"
    SIDEWAYS_RANGE = "SIDEWAYS_RANGE"
    HIGH_VOL_CHOP = "HIGH_VOL_CHOP"
    CRASH_PANIC = "CRASH_PANIC"


@dataclass
class MarketRegimeMetrics:
    """Quantitative metrics describing macro market health and volatility."""
    index_close: Decimal
    sma_50: Optional[Decimal] = None
    sma_200: Optional[Decimal] = None
    market_breadth_pct: Optional[Decimal] = None  # % of universe above 50 SMA (0-100)
    vix: Optional[Decimal] = None                 # India VIX level
    atr_pct: Optional[Decimal] = None             # Index ATR as % of price
    gap_down_pct: Optional[Decimal] = None         # Session open gap down %
    timestamp_iso: str = ""
    extra_indicators: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MarketRegimeDecision:
    """Decision emitted by the MarketRegimeClassifier."""
    regime: MarketRegime
    confidence: Decimal
    active_strategies: List[str]
    capital_allocation_pct: Decimal
    snooze_active: bool
    reason: str
    metrics: MarketRegimeMetrics


class MarketRegimeClassifier:
    """Classifies market regime and dictates strategic capital allocation."""

    def __init__(
        self,
        panic_vix_threshold: Decimal = Decimal("24.0"),
        panic_breadth_threshold: Decimal = Decimal("30.0"),
        panic_gap_down_threshold: Decimal = Decimal("1.5"),
        trend_breadth_threshold: Decimal = Decimal("55.0"),
        chop_atr_pct_threshold: Decimal = Decimal("1.5"),
        range_max_atr_pct: Decimal = Decimal("1.2"),
    ):
        self.panic_vix_threshold = panic_vix_threshold
        self.panic_breadth_threshold = panic_breadth_threshold
        self.panic_gap_down_threshold = panic_gap_down_threshold
        self.trend_breadth_threshold = trend_breadth_threshold
        self.chop_atr_pct_threshold = chop_atr_pct_threshold
        self.range_max_atr_pct = range_max_atr_pct

    def classify(self, metrics: MarketRegimeMetrics) -> MarketRegimeDecision:
        """Classifies the market regime using orthogonal macro indicators."""
        # Validate inputs
        if metrics.index_close <= Decimal("0"):
            raise InvalidStrategyParameterError(f"index_close must be positive, got {metrics.index_close}")
        if metrics.market_breadth_pct is not None:
            if not (Decimal("0") <= metrics.market_breadth_pct <= Decimal("100")):
                raise InvalidStrategyParameterError(
                    f"market_breadth_pct must be between 0 and 100, got {metrics.market_breadth_pct}"
                )

        # 1. Check CRASH / PANIC Regime (Highest Priority - Anti-Ruin Protection)
        is_panic_vix = metrics.vix is not None and metrics.vix >= self.panic_vix_threshold
        is_panic_breadth = (
            metrics.market_breadth_pct is not None
            and metrics.market_breadth_pct < self.panic_breadth_threshold
        )
        is_panic_gap = (
            metrics.gap_down_pct is not None
            and metrics.gap_down_pct >= self.panic_gap_down_threshold
        )
        is_deep_breakdown = (
            metrics.sma_200 is not None
            and metrics.index_close < (metrics.sma_200 * Decimal("0.97"))  # 3% below 200 SMA
        )

        if (is_panic_vix and (is_panic_breadth or is_panic_gap)) or (is_panic_breadth and is_deep_breakdown):
            return MarketRegimeDecision(
                regime=MarketRegime.CRASH_PANIC,
                confidence=Decimal("0.95"),
                active_strategies=[],
                capital_allocation_pct=Decimal("0.0"),
                snooze_active=True,
                reason=(
                    f"Panic threshold breached: VIX={metrics.vix}, Breadth={metrics.market_breadth_pct}%, "
                    f"GapDown={metrics.gap_down_pct}%. Mandating 100% defensive cash solvency buffer."
                ),
                metrics=metrics,
            )

        # 2. Check HIGH_VOL_CHOP Regime (Fee Bleed Prevention / Symmetry Snooze Guard)
        is_high_atr = (
            metrics.atr_pct is not None
            and metrics.atr_pct >= self.chop_atr_pct_threshold
        )
        is_indecisive_breadth = (
            metrics.market_breadth_pct is not None
            and Decimal("35.0") <= metrics.market_breadth_pct <= Decimal("50.0")
        )
        is_elevated_vix = metrics.vix is not None and Decimal("18.0") <= metrics.vix < self.panic_vix_threshold

        if is_high_atr and (is_indecisive_breadth or is_elevated_vix):
            return MarketRegimeDecision(
                regime=MarketRegime.HIGH_VOL_CHOP,
                confidence=Decimal("0.85"),
                active_strategies=[],
                capital_allocation_pct=Decimal("0.0"),
                snooze_active=True,
                reason=(
                    f"High volatility consolidation detected (ATR%={metrics.atr_pct}%, VIX={metrics.vix}). "
                    f"Engaging Symmetry Snooze Guard to prevent statutory fee leakage."
                ),
                metrics=metrics,
            )

        # 3. Check BULLISH_TREND Regime (Fail-closed on missing macro feeds)
        if (
            metrics.sma_50 is not None
            and metrics.sma_200 is not None
            and metrics.market_breadth_pct is not None
        ):
            index_above_50 = metrics.index_close >= metrics.sma_50
            index_above_200 = metrics.index_close >= metrics.sma_200
            golden_cross_aligned = metrics.sma_50 >= metrics.sma_200
            healthy_breadth = metrics.market_breadth_pct >= self.trend_breadth_threshold

            if index_above_50 and index_above_200 and golden_cross_aligned and healthy_breadth:
                return MarketRegimeDecision(
                    regime=MarketRegime.BULLISH_TREND,
                    confidence=Decimal("0.90"),
                    active_strategies=[
                        "STRAT_EMA_PULLBACK",
                        "STRAT_DONCHIAN_BREAKOUT",
                    ],
                    capital_allocation_pct=Decimal("100.0"),
                    snooze_active=False,
                    reason=(
                        f"Confirmed bullish trend: Index above SMAs with healthy breadth ({metrics.market_breadth_pct}%). "
                        f"Deploying EMA Pullback & Donchian Breakout engines with 3.5x payoff targets."
                    ),
                    metrics=metrics,
                )

        # 4. Default to SIDEWAYS_RANGE (Controlled Mean Reversion)
        breadth_str = f"{metrics.market_breadth_pct}%" if metrics.market_breadth_pct is not None else "None"
        return MarketRegimeDecision(
            regime=MarketRegime.SIDEWAYS_RANGE,
            confidence=Decimal("0.80"),
            active_strategies=[
                "STRAT_BOLLINGER_REVERSION",
                "STRAT_VWAP_REVERSION",
            ],
            capital_allocation_pct=Decimal("60.0"),
            snooze_active=False,
            reason=(
                f"Rangebound consolidation regime: Breadth={breadth_str}, "
                f"Index close={metrics.index_close}. Allocating to 60m Bollinger & VWAP Reversion."
            ),
            metrics=metrics,
        )
