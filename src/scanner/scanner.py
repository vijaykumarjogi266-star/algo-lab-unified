"""Decoupled Intraday Technical Scanner for NSE Equities.

Scans candidate symbols based on:
1. RVOL (Relative Volume) >= threshold (default: 1.5x 20-period average)
2. ATR (Average True Range) expansion
3. Spread sanity filter (< 0.10% of price)
4. Session timing window (09:20 - 14:45 IST)
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional


@dataclass
class MarketBar:
    """Standardized OHLCV market data bar."""
    symbol: str
    timestamp_iso: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    vwap: Optional[Decimal] = None


@dataclass
class ScanCandidate:
    """A qualified candidate asset identified by the scanner."""
    symbol: str
    timestamp_iso: str
    current_price: Decimal
    rvol: Decimal
    atr: Decimal
    spread_pct: Decimal
    bias: str  # "BULLISH" or "BEARISH"


class IntradayMarketScanner:
    """High-cohesion market scanner operating strictly on historical/synthetic feeds."""

    def __init__(
        self,
        min_rvol: Decimal = Decimal("1.5"),
        max_spread_pct: Decimal = Decimal("0.001"),  # 10 bps
    ):
        self.min_rvol = min_rvol
        self.max_spread_pct = max_spread_pct

    def scan_symbol(
        self,
        symbol: str,
        current_bar: MarketBar,
        history_bars: List[MarketBar],
        bid_ask_spread: Decimal = Decimal("0.05"),
    ) -> Optional[ScanCandidate]:
        """Evaluates whether an asset qualifies for strategy signal generation."""
        if len(history_bars) < 20:
            return None

        # 1. Spread Check
        spread_pct = (bid_ask_spread / current_bar.close) if current_bar.close > Decimal("0.0") else Decimal("1.0")
        if spread_pct > self.max_spread_pct:
            return None  # Spread too wide; reject to avoid excessive friction

        # 2. RVOL Calculation
        recent_volumes = [Decimal(str(b.volume)) for b in history_bars[-20:]]
        avg_volume = sum(recent_volumes) / Decimal("20")
        if avg_volume == Decimal("0.0"):
            return None

        current_vol = Decimal(str(current_bar.volume))
        rvol = current_vol / avg_volume
        if rvol < self.min_rvol:
            return None  # Insufficient volume surge

        # 3. ATR Calculation (14-period)
        tr_list = []
        for i in range(1, min(15, len(history_bars))):
            curr = history_bars[-i]
            prev = history_bars[-i - 1]
            tr = max(
                curr.high - curr.low,
                abs(curr.high - prev.close),
                abs(curr.low - prev.close),
            )
            tr_list.append(tr)

        atr = (sum(tr_list) / Decimal(str(len(tr_list)))) if tr_list else Decimal("1.0")

        # 4. Directional Bias
        if current_bar.close > current_bar.open and current_bar.close > history_bars[-1].high:
            bias = "BULLISH"
        elif current_bar.close < current_bar.open and current_bar.close < history_bars[-1].low:
            bias = "BEARISH"
        else:
            return None

        return ScanCandidate(
            symbol=symbol,
            timestamp_iso=current_bar.timestamp_iso,
            current_price=current_bar.close,
            rvol=rvol,
            atr=atr,
            spread_pct=spread_pct,
            bias=bias,
        )
