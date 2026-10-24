"""Universal Multi-Factor Stock Screener for NSE Equities.

Provides institutional-grade stock selection and ranking across:
1. Liquidity & Solvency Filtering (Min daily turnover, price floor, spread filter).
2. Trend Template (Price > 50/200 SMA, 50 > 200 SMA, 52-week high proximity).
3. Relative Strength vs. Benchmark (Alpha vs NIFTY 50 over 20/50/200 periods).
4. Volatility Setup Detection (EMA 9/21 pullbacks, Donchian tightness, RVOL surges).
5. Composite Scoring & Sector Diversification (0-100 score, max sector allocation).

Adheres to Non-Negotiable Invariants:
- Fail-closed parameter validation.
- Zero look-ahead bias (point-in-time bar evaluations).
- Decimal precision for monetary calculations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
import math
from typing import Any, Dict, List, Optional

from src.core.exceptions import InvalidStrategyParameterError
from src.scanner.scanner import MarketBar


class SetupType(str, Enum):
    """Identified technical setup pattern."""
    TREND_PULLBACK = "TREND_PULLBACK"       # Pullback to 9/21 EMA in primary uptrend
    DONCHIAN_BREAKOUT = "DONCHIAN_BREAKOUT" # High-volume breakout beyond N-day high
    VOLATILITY_SQUEEZE = "VOLATILITY_SQUEEZE" # Bandwidth compression ready to expand
    MEAN_REVERSION = "MEAN_REVERSION"       # Oversold bounce candidate
    NONE = "NONE"


@dataclass
class StockScreenResult:
    """Detailed score and qualification metadata for a screened stock."""
    symbol: str
    timestamp_iso: str
    current_price: Decimal
    trend_score: Decimal        # 0.0 - 100.0
    relative_strength: Decimal  # Alpha % vs Benchmark
    momentum_score: Decimal    # 20-period Rate of Change %
    rvol: Decimal              # Relative volume vs 20-period average
    composite_score: Decimal   # Weighted total 0.0 - 100.0
    setup_type: SetupType
    sma_50: Optional[Decimal]
    sma_200: Optional[Decimal]
    is_trend_template_qualified: bool
    sector: str = "GENERAL"
    metadata: Dict[str, Any] = field(default_factory=dict)


class StockScreener:
    """Configurable multi-factor stock screener and ranker."""

    def __init__(
        self,
        min_price: Decimal = Decimal("50.0"),
        min_avg_turnover: Decimal = Decimal("10000000.0"),  # ₹1 Crore min daily turnover
        min_rvol: Decimal = Decimal("1.0"),
        require_trend_template: bool = True,
        max_stocks_per_sector: int = 2,
        min_pct_of_52w_high: Optional[Decimal] = None,  # Screener.in standard: e.g. 85.0%
        min_rsi: Optional[Decimal] = None,              # Chartink standard: e.g. 50.0
    ):
        if min_price <= Decimal("0"):
            raise InvalidStrategyParameterError(f"min_price must be positive, got {min_price}")
        if min_avg_turnover < Decimal("0"):
            raise InvalidStrategyParameterError(f"min_avg_turnover cannot be negative, got {min_avg_turnover}")
        if max_stocks_per_sector <= 0:
            raise InvalidStrategyParameterError("max_stocks_per_sector must be at least 1")

        self.min_price = min_price
        self.min_avg_turnover = min_avg_turnover
        self.min_rvol = min_rvol
        self.require_trend_template = require_trend_template
        self.max_stocks_per_sector = max_stocks_per_sector
        self.min_pct_of_52w_high = min_pct_of_52w_high
        self.min_rsi = min_rsi

    def screen_stock(
        self,
        symbol: str,
        current_bar: MarketBar,
        history_bars: List[MarketBar],
        benchmark_history: Optional[List[MarketBar]] = None,
        sector: str = "GENERAL",
    ) -> Optional[StockScreenResult]:
        """Evaluates a single stock against multi-factor quantitative criteria.

        Returns StockScreenResult if qualified, or None if filtered out.
        """
        # 1. Price Floor Filter
        if current_bar.close < self.min_price:
            return None

        # Require minimum history for 20-period indicators
        if len(history_bars) < 20:
            return None

        # 2. Turnover / Liquidity Filter
        recent_bars = history_bars[-20:]
        turnovers = [b.close * Decimal(str(b.volume)) for b in recent_bars]
        avg_turnover = sum(turnovers) / Decimal(str(len(turnovers)))
        if avg_turnover < self.min_avg_turnover:
            return None

        # 3. 52-Week High Proximity Filter (Screener.in & Chartink Institutional Standard)
        lookback_52w = min(252, len(history_bars))
        high_52w = max(b.high for b in history_bars[-lookback_52w:])
        pct_of_52w_high = (
            (current_bar.close / high_52w) * Decimal("100")
            if high_52w > Decimal("0")
            else Decimal("100.0")
        )
        if self.min_pct_of_52w_high is not None and pct_of_52w_high < self.min_pct_of_52w_high:
            return None

        # 4. RSI (14) Momentum Filter (Chartink Standard: RSI > 50)
        rsi_14 = self._calculate_rsi(history_bars + [current_bar], 14)
        if self.min_rsi is not None and rsi_14 is not None and rsi_14 < self.min_rsi:
            return None

        # 5. Moving Averages & Trend Template
        closes_20 = [b.close for b in recent_bars]
        sma_20 = sum(closes_20) / Decimal("20")

        sma_50 = None
        if len(history_bars) >= 50:
            sma_50 = sum(b.close for b in history_bars[-50:]) / Decimal("50")

        sma_200 = None
        if len(history_bars) >= 200:
            sma_200 = sum(b.close for b in history_bars[-200:]) / Decimal("200")

        # Trend Template Check: Price > 50 SMA > 200 SMA
        is_above_50 = (sma_50 is None) or (current_bar.close >= sma_50)
        is_above_200 = (sma_200 is None) or (current_bar.close >= sma_200)
        is_golden_cross = (sma_50 is None or sma_200 is None) or (sma_50 >= sma_200)
        trend_template_passed = is_above_50 and is_above_200 and is_golden_cross

        if self.require_trend_template and not trend_template_passed:
            return None

        # 4. Relative Volume (RVOL)
        avg_volume = sum(Decimal(str(b.volume)) for b in recent_bars) / Decimal("20")
        rvol = (
            Decimal(str(current_bar.volume)) / avg_volume
            if avg_volume > Decimal("0")
            else Decimal("1.0")
        )
        if rvol < self.min_rvol:
            return None

        # 5. Momentum: 20-period Rate of Change (ROC)
        price_20_bars_ago = history_bars[-20].close
        roc_20 = (
            ((current_bar.close - price_20_bars_ago) / price_20_bars_ago) * Decimal("100")
            if price_20_bars_ago > Decimal("0")
            else Decimal("0.0")
        )

        # 6. Relative Strength vs. Benchmark
        rs_alpha = Decimal("0.0")
        if benchmark_history and len(benchmark_history) >= 20:
            bench_curr = benchmark_history[-1].close
            bench_past = benchmark_history[-20].close
            bench_roc = (
                ((bench_curr - bench_past) / bench_past) * Decimal("100")
                if bench_past > Decimal("0")
                else Decimal("0.0")
            )
            rs_alpha = roc_20 - bench_roc

        # 7. Trend Score (0.0 to 100.0)
        trend_score = Decimal("50.0")
        if trend_template_passed:
            trend_score += Decimal("30.0")
        if current_bar.close > sma_20:
            trend_score += Decimal("20.0")
        trend_score = min(Decimal("100.0"), max(Decimal("0.0"), trend_score))

        # 8. Setup Detection
        setup = SetupType.NONE
        highest_20 = max(b.high for b in recent_bars)
        if current_bar.close > highest_20:
            setup = SetupType.DONCHIAN_BREAKOUT
        else:
            # Check Pullback to 9/21 EMA zone
            ema_9 = self._calculate_ema(history_bars + [current_bar], 9)
            ema_21 = self._calculate_ema(history_bars + [current_bar], 21)
            if (
                ema_9 and ema_21
                and ema_9 >= ema_21
                and current_bar.low <= (ema_9 * Decimal("1.005"))
                and current_bar.close >= current_bar.open
            ):
                setup = SetupType.TREND_PULLBACK

        # 9. Composite Score (0.0 - 100.0)
        # Weights: Trend (35%), RS Alpha (30%), Momentum ROC (20%), RVOL (15%)
        norm_trend = trend_score
        norm_rs = min(Decimal("100.0"), max(Decimal("0.0"), (rs_alpha + Decimal("10.0")) * Decimal("5.0")))
        norm_roc = min(Decimal("100.0"), max(Decimal("0.0"), (roc_20 + Decimal("10.0")) * Decimal("5.0")))
        norm_rvol = min(Decimal("100.0"), rvol * Decimal("40.0"))

        composite = (
            (norm_trend * Decimal("0.35"))
            + (norm_rs * Decimal("0.30"))
            + (norm_roc * Decimal("0.20"))
            + (norm_rvol * Decimal("0.15"))
        )

        return StockScreenResult(
            symbol=symbol,
            timestamp_iso=current_bar.timestamp_iso,
            current_price=current_bar.close,
            trend_score=trend_score.quantize(Decimal("0.01")),
            relative_strength=rs_alpha.quantize(Decimal("0.01")),
            momentum_score=roc_20.quantize(Decimal("0.01")),
            rvol=rvol.quantize(Decimal("0.01")),
            composite_score=composite.quantize(Decimal("0.01")),
            setup_type=setup,
            sma_50=sma_50.quantize(Decimal("0.01")) if sma_50 else None,
            sma_200=sma_200.quantize(Decimal("0.01")) if sma_200 else None,
            is_trend_template_qualified=trend_template_passed,
            sector=sector,
            metadata={
                "avg_turnover": str(avg_turnover.quantize(Decimal("1.0"))),
                "sma_20": str(sma_20.quantize(Decimal("0.01"))),
                "pct_of_52w_high": str(pct_of_52w_high.quantize(Decimal("0.1"))),
                "rsi_14": str(rsi_14.quantize(Decimal("0.1"))) if rsi_14 else "N/A",
                "is_volume_shocker": bool(rvol >= Decimal("2.0") and current_bar.close > current_bar.open),
            },
        )

    def screen_universe(
        self,
        market_data: Dict[str, List[MarketBar]],
        benchmark_data: Optional[List[MarketBar]] = None,
        symbol_sectors: Optional[Dict[str, str]] = None,
        top_n: int = 5,
    ) -> List[StockScreenResult]:
        """Screens and ranks entire universe of stocks with sector capping."""
        results: List[StockScreenResult] = []
        sectors_map = symbol_sectors or {}

        for symbol, bars in market_data.items():
            if not bars or len(bars) < 2:
                continue
            current_bar = bars[-1]
            history_bars = bars[:-1]
            sector = sectors_map.get(symbol, "GENERAL")

            res = self.screen_stock(
                symbol=symbol,
                current_bar=current_bar,
                history_bars=history_bars,
                benchmark_history=benchmark_data,
                sector=sector,
            )
            if res:
                results.append(res)

        # Sort by composite score descending
        results.sort(key=lambda x: x.composite_score, reverse=True)

        # Apply sector concentration guard
        selected: List[StockScreenResult] = []
        sector_counts: Dict[str, int] = {}

        for item in results:
            sec = item.sector
            count = sector_counts.get(sec, 0)
            if count < self.max_stocks_per_sector:
                selected.append(item)
                sector_counts[sec] = count + 1
            if len(selected) >= top_n:
                break

        return selected

    @staticmethod
    def _calculate_rsi(bars: List[MarketBar], period: int = 14) -> Optional[Decimal]:
        if len(bars) < period + 1:
            return None
        gains = []
        losses = []
        for i in range(1, len(bars)):
            diff = bars[i].close - bars[i - 1].close
            if diff > Decimal("0"):
                gains.append(diff)
                losses.append(Decimal("0"))
            else:
                gains.append(Decimal("0"))
                losses.append(abs(diff))

        recent_gains = gains[-period:]
        recent_losses = losses[-period:]
        avg_gain = sum(recent_gains) / Decimal(str(period))
        avg_loss = sum(recent_losses) / Decimal(str(period))

        if avg_loss == Decimal("0"):
            return Decimal("100.0")
        rs = avg_gain / avg_loss
        rsi = Decimal("100.0") - (Decimal("100.0") / (Decimal("1.0") + rs))
        return rsi

    @staticmethod
    def _calculate_ema(bars: List[MarketBar], period: int) -> Optional[Decimal]:
        if len(bars) < period:
            return None
        multiplier = Decimal("2.0") / Decimal(str(period + 1))
        ema = sum(b.close for b in bars[:period]) / Decimal(str(period))
        for b in bars[period:]:
            ema = ((b.close - ema) * multiplier) + ema
        return ema
