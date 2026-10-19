#!/usr/bin/env python3
"""Fetch and seal 30-day 5-minute historical NSE equity data from Yahoo Finance."""

from __future__ import annotations

import json
from pathlib import Path
from decimal import Decimal
import hashlib
import yfinance as yf

SYMBOLS_MAP = {
    "RELIANCE.NS": "RELIANCE",
    "INFY.NS": "INFY",
    "TCS.NS": "TCS",
    "HDFCBANK.NS": "HDFCBANK",
}

OUTPUT_PATH = Path("/opt/ai-builder/workspaces/algolab-paper-trial-20261019-123800/data/yfinance_nse_5m_30days.json")


def main():
    print("[*] Fetching 60d 5m market data from Yahoo Finance for liquid NSE equities...")
    tickers = list(SYMBOLS_MAP.keys())
    data = yf.download(tickers, period="60d", interval="5m", group_by="ticker", auto_adjust=True, progress=False)

    # Determine unique trading dates across all symbols
    all_dates = set()
    for t in tickers:
        df = data[t].dropna()
        all_dates.update(df.index.date)

    sorted_dates = sorted(list(all_dates))
    target_dates = sorted_dates[-30:]
    target_date_strs = {d.isoformat() for d in target_dates}

    print(f"[✓] Selected 30 trading dates: {target_dates[0]} to {target_dates[-1]}")

    formatted_data = {}  # { date_str: { symbol: [ {bar_dict}, ... ] } }

    for d_str in sorted(list(target_date_strs)):
        formatted_data[d_str] = {}

    total_bars = 0
    for ticker, clean_sym in SYMBOLS_MAP.items():
        df = data[ticker].dropna()
        for idx, row in df.iterrows():
            d_str = idx.date().isoformat()
            if d_str not in target_date_strs:
                continue

            # Standard NSE trading hours check: 09:15 to 15:30 IST
            bar_time = idx.time()
            if bar_time < idx.time().replace(hour=9, minute=15) or bar_time > idx.time().replace(hour=15, minute=30):
                continue

            ts_iso = idx.isoformat()
            bar_dict = {
                "symbol": clean_sym,
                "timestamp_iso": ts_iso,
                "open": str(Decimal(str(row["Open"])).quantize(Decimal("0.05"))),
                "high": str(Decimal(str(row["High"])).quantize(Decimal("0.05"))),
                "low": str(Decimal(str(row["Low"])).quantize(Decimal("0.05"))),
                "close": str(Decimal(str(row["Close"])).quantize(Decimal("0.05"))),
                "volume": int(row["Volume"]),
            }

            if clean_sym not in formatted_data[d_str]:
                formatted_data[d_str][clean_sym] = []
            formatted_data[d_str][clean_sym].append(bar_dict)
            total_bars += 1

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump({
            "source": "Yahoo Finance (yfinance)",
            "symbols": list(SYMBOLS_MAP.values()),
            "total_trading_days": len(target_dates),
            "start_date": target_dates[0].isoformat(),
            "end_date": target_dates[-1].isoformat(),
            "total_bars_ingested": total_bars,
            "days": formatted_data
        }, f, indent=2)

    sha256 = hashlib.sha256(OUTPUT_PATH.read_bytes()).hexdigest()
    print(f"[✓] Saved {total_bars} bars across 30 days to {OUTPUT_PATH}")
    print(f"[✓] Dataset SHA-256: {sha256}")


if __name__ == "__main__":
    main()
