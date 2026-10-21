#!/usr/bin/env python3
"""Zerodha Kite Connect Historical Data Downloader.

Downloads historical candle data for NSE equities and indices,
supporting chunked date fetching, instrument token resolution,
and export to CSV and Algo Lab Unified replay JSON format.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SESSION_FILE = Path("/home/ubuntu/.zerodha_session.json")
INSTRUMENTS_FILE = Path("/home/ubuntu/data/kite_instruments.csv")
OUTPUT_DIR = Path("/home/ubuntu/data/backtest")

# Max days per request allowed by Kite Connect API
CHUNK_LIMITS = {
    "minute": 50,
    "3minute": 90,
    "5minute": 90,
    "10minute": 90,
    "15minute": 180,
    "30minute": 180,
    "60minute": 360,
    "day": 1800,
}


def load_session() -> Dict[str, str]:
    if not SESSION_FILE.exists():
        print(f"Error: Session file {SESSION_FILE} not found. Run authentication first.")
        sys.exit(1)
    with open(SESSION_FILE) as f:
        return json.load(f)


def load_instrument_tokens(symbols: List[str]) -> Dict[str, Tuple[int, str]]:
    """Map tradingsymbol to (instrument_token, exchange)."""
    if not INSTRUMENTS_FILE.exists():
        print(f"Downloading instrument master to {INSTRUMENTS_FILE}...")
        sess = load_session()
        headers = {
            "X-Kite-Version": "3",
            "Authorization": f"token {sess['api_key']}:{sess['access_token']}",
        }
        req = urllib.request.Request("https://api.kite.trade/instruments", headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            os.makedirs(INSTRUMENTS_FILE.parent, exist_ok=True)
            with open(INSTRUMENTS_FILE, "wb") as f:
                f.write(resp.read())

    norm_map = {s.strip().upper(): s.strip().upper() for s in symbols}
    resolved: Dict[str, Tuple[int, str]] = {}

    with open(INSTRUMENTS_FILE, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tsym = row["tradingsymbol"].upper()
            exch = row["exchange"].upper()
            if tsym in norm_map and exch in ("NSE", "INDICES"):
                resolved[tsym] = (int(row["instrument_token"]), exch)

    return resolved


def fetch_historical_chunk(
    api_key: str,
    access_token: str,
    instrument_token: int,
    interval: str,
    from_date: str,
    to_date: str,
) -> List[List]:
    url = f"https://api.kite.trade/instruments/historical/{instrument_token}/{interval}?from={from_date}&to={to_date}"
    headers = {
        "X-Kite-Version": "3",
        "Authorization": f"token {api_key}:{access_token}",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            return data.get("data", {}).get("candles", [])
    except urllib.error.HTTPError as e:
        print(f"  [!] HTTP {e.code} for {from_date} to {to_date}: {e.read().decode()[:120]}")
        return []
    except Exception as e:
        print(f"  [!] Network error: {e}")
        return []


def download_candles(
    api_key: str,
    access_token: str,
    symbol: str,
    instrument_token: int,
    interval: str,
    start_date: datetime,
    end_date: datetime,
) -> List[Dict]:
    max_chunk_days = CHUNK_LIMITS.get(interval, 60)
    current_start = start_date
    all_candles = []

    while current_start < end_date:
        current_end = min(current_start + timedelta(days=max_chunk_days), end_date)
        from_str = current_start.strftime("%Y-%m-%d")
        to_str = current_end.strftime("%Y-%m-%d")

        chunk = fetch_historical_chunk(api_key, access_token, instrument_token, interval, from_str, to_str)
        if chunk:
            all_candles.extend(chunk)

        current_start = current_end + timedelta(days=1)
        time.sleep(0.35)  # Rate limiting (max 3 req/sec for Kite historical)

    # Deduplicate by timestamp
    dedup = {}
    for c in all_candles:
        # candle structure: [timestamp_str, open, high, low, close, volume, oi (optional)]
        dedup[c[0]] = c

    sorted_candles = [dedup[k] for k in sorted(dedup.keys())]
    results = []
    for c in sorted_candles:
        results.append({
            "timestamp": c[0],
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "volume": int(c[5]) if len(c) > 5 else 0,
        })
    return results


def save_as_csv(symbol: str, interval: str, candles: List[Dict], out_dir: Path) -> Path:
    out_path = out_dir / f"{symbol}_{interval}.csv"
    with open(out_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(candles)
    return out_path


def save_as_algolab_json(interval: str, symbol_candles_map: Dict[str, List[Dict]], out_dir: Path) -> Path:
    """Format for Algo Lab Unified HistoricalReplayEngine."""
    formatted = {}  # { date_str: { symbol: [ {bar}, ... ] } }
    total_bars = 0

    for symbol, candles in symbol_candles_map.items():
        for c in candles:
            # timestamp format: '2026-10-21T09:15:00+0530'
            ts_str = c["timestamp"]
            date_str = ts_str[:10]

            if date_str not in formatted:
                formatted[date_str] = {}
            if symbol not in formatted[date_str]:
                formatted[date_str][symbol] = []

            # 5-paise tick quantization
            bar = {
                "symbol": symbol,
                "timestamp_iso": ts_str,
                "open": str(Decimal(str(c["open"])).quantize(Decimal("0.05"))),
                "high": str(Decimal(str(c["high"])).quantize(Decimal("0.05"))),
                "low": str(Decimal(str(c["low"])).quantize(Decimal("0.05"))),
                "close": str(Decimal(str(c["close"])).quantize(Decimal("0.05"))),
                "volume": c["volume"],
            }
            formatted[date_str][symbol].append(bar)
            total_bars += 1

    out_path = out_dir / f"zerodha_{interval}_dataset.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(formatted, f, indent=2)

    return out_path


def main():
    parser = argparse.ArgumentParser(description="Download backtest data from Zerodha Kite Connect.")
    parser.add_argument(
        "--symbols",
        default="RELIANCE,TCS,INFY,HDFCBANK,ICICIBANK,NIFTY 50",
        help="Comma-separated symbols (default: RELIANCE,TCS,INFY,HDFCBANK,ICICIBANK,NIFTY 50)",
    )
    parser.add_argument(
        "--interval",
        default="5minute",
        choices=["minute", "3minute", "5minute", "15minute", "30minute", "60minute", "day"],
        help="Candle interval (default: 5minute)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of past days to fetch (default: 30)",
    )
    parser.add_argument(
        "--outdir",
        default=str(OUTPUT_DIR),
        help="Output directory path (default: /home/ubuntu/data/backtest)",
    )

    args = parser.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    out_dir = Path(args.outdir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("==========================================================")
    print("  ZERODHA KITE CONNECT: HISTORICAL DATA DOWNLOADER")
    print("==========================================================")
    print(f"  Symbols:   {', '.join(symbols)}")
    print(f"  Interval:  {args.interval}")
    print(f"  Days:      {args.days}")
    print(f"  Output:    {out_dir}")
    print("----------------------------------------------------------")

    sess = load_session()
    print(f"  Active Session: {sess.get('user_name')} ({sess.get('user_id')})")

    token_map = load_instrument_tokens(symbols)
    missing = [s for s in symbols if s not in token_map]
    if missing:
        print(f"  [!] Warning: Could not resolve tokens for: {', '.join(missing)}")

    end_date = datetime.now()
    start_date = end_date - timedelta(days=args.days)

    all_downloaded: Dict[str, List[Dict]] = {}

    for sym in symbols:
        if sym not in token_map:
            continue
        token, exch = token_map[sym]
        print(f"[*] Downloading {sym} ({exch}:{token})...", end="", flush=True)
        candles = download_candles(sess["api_key"], sess["access_token"], sym, token, args.interval, start_date, end_date)
        if candles:
            all_downloaded[sym] = candles
            csv_path = save_as_csv(sym, args.interval, candles, out_dir)
            print(f" [✓] {len(candles):,} candles -> {csv_path.name}")
        else:
            print(" [✗] 0 candles returned.")

    if all_downloaded:
        json_path = save_as_algolab_json(args.interval, all_downloaded, out_dir)
        print("----------------------------------------------------------")
        print(f"[✓] Complete! Exported Algo Lab replay dataset: {json_path}")
        print(f"    Total symbols: {len(all_downloaded)}")
        print(f"    Total candles: {sum(len(v) for v in all_downloaded.values()):,}")
    print("==========================================================")


if __name__ == "__main__":
    main()
