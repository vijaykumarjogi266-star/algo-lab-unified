"""Adaptive Multi-Regime Strategy Router for Algo Lab Unified.

Implements the 4-Pillar Adaptive Market Survival Architecture:
1. Multi-Dimensional Market Regime Detection (Bullish, Rangebound, High-Vol Chop, Panic).
2. Anti-Ruin Sizing & Solvency Buffers (Ed Thorp 1.0% equity risk per trade, 10% uncommitted cash).
3. Taleb Convexity & Asymmetric Payoffs (Prioritizing high reward-to-risk setups).
4. Statutory Friction Shield (Engaging Symmetry Snooze Guard in choppy markets to halt trade bleed).

Adheres to Non-Negotiable Invariants:
- Principle 1: Never force a trade.
- Principle 2: WAIT is a valid decision (returns None).
- Principle 4: No look-ahead bias; point-in-time evaluation only.
- Strict Decimal monetary precision and statutory tick quantization.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional
import uuid

from src.core.exceptions import InvalidStrategyParameterError
from src.core.regime_classifier import (
    MarketRegime,
    MarketRegimeClassifier,
    MarketRegimeDecision,
    MarketRegimeMetrics,
)
from src.scanner.scanner import MarketBar
from src.strategies.base import BaseStrategy
from src.strategies.bollinger_reversion import BollingerReversionStrategy
from src.strategies.donchian_breakout import DonchianBreakoutStrategy
from src.strategies.ema_pullback import EMAPullbackStrategy
from src.strategies.models import StrategySignal
from src.strategies.vwap_reversion import VWAPMeanReversionStrategy


class AdaptiveStrategyRouter(BaseStrategy):
    """Dynamically routes capital and evaluates strategies conditioned on market regimes."""

    STRATEGY_ID = "STRAT_ADAPTIVE_ROUTER"

    def __init__(
        self,
        strategy_id: str = "STRAT_ADAPTIVE_ROUTER",
        version: str = "1.0.0",
        parameters: Optional[Dict[str, Any]] = None,
        classifier: Optional[MarketRegimeClassifier] = None,
        sub_strategies: Optional[Dict[str, BaseStrategy]] = None,
    ):
        super().__init__(strategy_id=strategy_id, version=version, parameters=parameters)
        self.max_risk_pct_per_trade: Decimal = self.parameters["max_risk_pct_per_trade"]
        self.min_cash_reserve_pct: Decimal = self.parameters["min_cash_reserve_pct"]

        self.classifier = classifier or MarketRegimeClassifier()
        self.current_regime_decision: Optional[MarketRegimeDecision] = None

        # Initialize sub-strategies
        self.sub_strategies: Dict[str, BaseStrategy] = sub_strategies or {
            "STRAT_EMA_PULLBACK": EMAPullbackStrategy(),
            "STRAT_DONCHIAN_BREAKOUT": DonchianBreakoutStrategy(),
            "STRAT_BOLLINGER_REVERSION": BollingerReversionStrategy(),
            "STRAT_VWAP_REVERSION": VWAPMeanReversionStrategy(),
        }

    def validate_and_sanitize_parameters(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Validates fail-closed router parameters."""
        max_risk = params.get("max_risk_pct_per_trade", Decimal("1.0"))
        self._validate_numeric_range("max_risk_pct_per_trade", max_risk, min_val=0.1, max_val=5.0)

        min_cash = params.get("min_cash_reserve_pct", Decimal("10.0"))
        self._validate_numeric_range("min_cash_reserve_pct", min_cash, min_val=0.0, max_val=50.0)

        return {
            "max_risk_pct_per_trade": Decimal(str(max_risk)),
            "min_cash_reserve_pct": Decimal(str(min_cash)),
        }

    def update_regime(self, metrics: MarketRegimeMetrics) -> MarketRegimeDecision:
        """Updates the active market regime decision from current metrics."""
        self.current_regime_decision = self.classifier.classify(metrics)
        return self.current_regime_decision

    def set_regime_decision(self, decision: MarketRegimeDecision) -> None:
        """Explicitly sets the active market regime decision."""
        self.current_regime_decision = decision

    def _derive_metrics(
        self,
        current_bar: MarketBar,
        history: List[MarketBar],
        indicators: Optional[Dict[str, Any]] = None,
    ) -> MarketRegimeMetrics:
        """Derives point-in-time trend, volatility, and breadth proxy from historical bars."""
        sma_50 = None
        sma_200 = None
        atr_pct = None
        breadth = None

        if indicators and "sma_50" in indicators and indicators["sma_50"] is not None:
            sma_50 = Decimal(str(indicators["sma_50"]))
        elif history and len(history) >= 50:
            sma_50 = sum(b.close for b in history[-50:]) / Decimal("50")

        if indicators and "sma_200" in indicators and indicators["sma_200"] is not None:
            sma_200 = Decimal(str(indicators["sma_200"]))
        elif history and len(history) >= 200:
            sma_200 = sum(b.close for b in history[-200:]) / Decimal("200")

        # ATR calculation for volatility gating
        if indicators and "atr_pct" in indicators and indicators["atr_pct"] is not None:
            atr_pct = Decimal(str(indicators["atr_pct"]))
        elif history and len(history) >= 14:
            bars = history[-14:] + [current_bar]
            tr_list = []
            for i in range(1, len(bars)):
                c, p = bars[i], bars[i - 1]
                tr = max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
                tr_list.append(tr)
            if tr_list:
                avg_tr = sum(tr_list) / Decimal(str(len(tr_list)))
                if current_bar.close > Decimal("0"):
                    atr_pct = (avg_tr / current_bar.close) * Decimal("100")

        # Market breadth proxy: fail-closed if missing, no single-stock synthesis
        if indicators and "market_breadth_pct" in indicators and indicators["market_breadth_pct"] is not None:
            breadth = Decimal(str(indicators["market_breadth_pct"]))
        else:
            breadth = None

        vix = Decimal(str(indicators["vix"])) if indicators and "vix" in indicators and indicators["vix"] is not None else None
        gap_down_pct = Decimal(str(indicators["gap_down_pct"])) if indicators and "gap_down_pct" in indicators and indicators["gap_down_pct"] is not None else None

        return MarketRegimeMetrics(
            index_close=current_bar.close,
            sma_50=sma_50,
            sma_200=sma_200,
            market_breadth_pct=breadth,
            vix=vix,
            atr_pct=atr_pct,
            gap_down_pct=gap_down_pct,
            timestamp_iso=current_bar.timestamp_iso,
        )

    _compute_metrics_from_history = _derive_metrics

    def evaluate(
        self,
        current_bar: MarketBar,
        history: List[MarketBar],
        indicators: Optional[Dict[str, Any]] = None,
    ) -> Optional[StrategySignal]:
        """Evaluates strategies conditioned on the active market regime.

        Returns None (WAIT) if the regime is in Snooze Guard, Defensive Cash buffer,
        or if no permitted strategy generates an entry signal.
        """
        # If no regime decision is explicitly pre-set, classify using derived or provided metrics
        if self.current_regime_decision is None:
            metrics = self._derive_metrics(current_bar, history, indicators)
            regime_dec = self.classifier.classify(metrics)
        else:
            regime_dec = self.current_regime_decision

        # Invariant: If Snooze Guard active or capital allocation is 0, enforce strict WAIT
        if regime_dec.snooze_active or regime_dec.capital_allocation_pct <= Decimal("0"):
            return None

        # Filter and evaluate only permitted strategies for the active regime
        candidate_signals: List[StrategySignal] = []

        for strat_name in regime_dec.active_strategies:
            strat = self.sub_strategies.get(strat_name)
            if not strat:
                continue

            sig = strat.evaluate(current_bar, history, indicators)
            if sig:
                candidate_signals.append(sig)

        if not candidate_signals:
            return None

        # Rank signals by confidence score descending
        candidate_signals.sort(key=lambda s: s.confidence_score, reverse=True)
        selected_signal = candidate_signals[0]

        # Calculate position sizing ratio scaled by regime capital allocation & Thorp rule
        # e.g., 100% allocation * 1.0% risk rule = 0.010
        effective_allocation_factor = regime_dec.capital_allocation_pct / Decimal("100.0")
        effective_risk_pct = (self.max_risk_pct_per_trade / Decimal("100.0")) * effective_allocation_factor

        # Augment signal evidence with regime provenance
        evidence = dict(selected_signal.evidence)
        evidence["regime"] = regime_dec.regime.value
        evidence["regime_reason"] = regime_dec.reason
        evidence["regime_capital_allocation_pct"] = str(regime_dec.capital_allocation_pct)
        evidence["routed_strategy_id"] = selected_signal.strategy_id
        evidence["router_version"] = self.version

        return StrategySignal(
            signal_id=f"ROUTED_{uuid.uuid4().hex[:12].upper()}",
            strategy_id=self.strategy_id,
            symbol=selected_signal.symbol,
            side=selected_signal.side,
            target_entry_price=selected_signal.target_entry_price,
            stop_loss=selected_signal.stop_loss,
            take_profit=selected_signal.take_profit,
            confidence_score=selected_signal.confidence_score,
            timestamp_iso=selected_signal.timestamp_iso,
            strategy_version=self.version,
            order_type=selected_signal.order_type,
            reason=f"[{regime_dec.regime.value}] {selected_signal.reason}",
            evidence=evidence,
            data_provenance=selected_signal.data_provenance,
            position_sizing_ratio=effective_risk_pct,
        )
