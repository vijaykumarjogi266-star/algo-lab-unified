"""Strategies package for Algo Lab Unified.

Exports canonical strategy models, base interfaces, and the central StrategyRegistry.
"""

from src.strategies.base import BaseStrategy
from src.strategies.bollinger_reversion import BollingerReversionStrategy
from src.strategies.donchian_breakout import DonchianBreakoutStrategy
from src.strategies.ema_pullback import EMAPullbackStrategy
from src.strategies.models import (
    LegacyOrderIntent,
    LegacyStrategyDecision,
    LegacyTradeCandidate,
    SignalType,
    StrategySignal,
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

# Register Canonical Strategies with StrategyRegistry
StrategyRegistry.register(
    MomentumBreakoutStrategy,
    aliases=["MOMENTUM_BREAKOUT", "STRAT_MOMENTUM_BREAKOUT", "BREAKOUT_V1"],
    description="Intraday 20-period breakout with RVOL >= 1.5x and 2:1 RR.",
)
StrategyRegistry.register(
    OpeningRangeBreakoutStrategy,
    aliases=["ORB", "OPENING_RANGE_BREAKOUT", "ORB_STRATEGY"],
    description="Opening Range Breakout beyond 15m opening range with 14:45 IST cutoff.",
)
StrategyRegistry.register(
    VWAPMeanReversionStrategy,
    aliases=["VWAP_REVERSION", "VWAP_MEAN_REVERSION", "VWAPREVERSION"],
    description="VWAP Mean Reversion with session-reset cumulative VWAP and 2.0σ bands.",
)
StrategyRegistry.register(
    SMACrossoverStrategy,
    aliases=["SMA_CROSS", "SMA_CROSSOVER", "SMACROSS", "STRAT_SMA"],
    description="Dual Simple Moving Average Crossover (fast crosses slow) benchmark.",
)
StrategyRegistry.register(
    DonchianBreakoutStrategy,
    aliases=["DONCHIAN_BREAKOUT", "DONCHIAN", "DONCHIANBREAKOUT"],
    description="Lagged 20-bar Donchian channel breakout.",
)
StrategyRegistry.register(
    BollingerReversionStrategy,
    aliases=["BOLLINGER_REVERSION", "BOLLINGER", "BOLLINGERREVERSION", "BOLLINGER_MEAN_REVERSION"],
    description="Bollinger Bands 2.0σ oversold mean-reversion.",
)
StrategyRegistry.register(
    TTMSqueezeBreakoutStrategy,
    aliases=["TTM_SQUEEZE", "VOLATILITY_SQUEEZE", "TTMSQUEEZE", "STRAT_VOLATILITY_SQUEEZE"],
    description="TTM Volatility Squeeze (Bollinger within Keltner Channels) breakout.",
)
StrategyRegistry.register(
    EMAPullbackStrategy,
    aliases=["EMA_PULLBACK", "EMA_PULLBACK_SWING", "STRAT_EMA_SWING"],
    description="9/21 EMA pullback swing strategy in primary uptrend.",
)
StrategyRegistry.register(
    OMSMeanReversionStrategy,
    aliases=["OMS_MEAN_REV", "OMS_MEAN_REVERSION"],
    description="OMS Blueprint: SMA with volatility envelope reversion.",
)
StrategyRegistry.register(
    OMSTrendFollowingStrategy,
    aliases=["OMS_TREND_FOLLOWING", "OMS_TREND"],
    description="OMS Blueprint: Short/Long moving average crossover.",
)
StrategyRegistry.register(
    OMSHybridAlphaStrategy,
    aliases=["OMS_HYBRID_ALPHA", "HYBRID_ALPHA"],
    description="OMS Blueprint: Volatility-based regime switching hybrid alpha.",
)

__all__ = [
    "BaseStrategy",
    "StrategySignal",
    "SignalType",
    "LegacyStrategyDecision",
    "LegacyTradeCandidate",
    "LegacyOrderIntent",
    "adapt_legacy_strategy_decision",
    "adapt_legacy_trade_candidate",
    "adapt_legacy_order_intent",
    "StrategyRegistry",
    "MomentumBreakoutStrategy",
    "OpeningRangeBreakoutStrategy",
    "VWAPMeanReversionStrategy",
    "SMACrossoverStrategy",
    "DonchianBreakoutStrategy",
    "BollingerReversionStrategy",
    "TTMSqueezeBreakoutStrategy",
    "EMAPullbackStrategy",
    "OMSMeanReversionStrategy",
    "OMSTrendFollowingStrategy",
    "OMSHybridAlphaStrategy",
]
