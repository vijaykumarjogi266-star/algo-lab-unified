#!/usr/bin/env python3
"""Algo Lab Unified — 5-Year Empirical Multi-Strategy Historical Backtest Runner.

Executes a full 5-year continuous backtest across core NSE liquid equities
using authentic historical data from Zerodha Kite Connect (2021-10-22 to 2026-10-21).

Models:
- Realistic paper execution & conservative SL/TP collision
- Exact NSE statutory friction (STT, GST, turnover, stamp duty, brokerage cap)
- Double-entry balance sheet conservation
- Benchmark comparison against NIFTY 50 Buy-and-Hold
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from src.core.monetary import format_monetary_decimal
from src.core.tick_precision import HistoricalSecurityTickMaster, quantize_order_price
from src.scanner.scanner import MarketBar
from src.simulation.engine import HistoricalReplayEngine, SimulationMetrics
from src.simulation.fee_model import NSEFrictionModel
from src.strategies.registry import StrategyRegistry

SESSION_FILE = Path("/home/ubuntu/.zerodha_session.json")
DATA_DIR = Path("/home/ubuntu/data/backtest/5year")

UNIVERSE = {
    "RELIANCE": 738561,
    "TCS": 2953217,
    "INFY": 408065,
    "HDFCBANK": 341249,
    "ICICIBANK": 1270529,
    "SBIN": 779521,
    "BHARTIARTL": 2714625,
    "ITC": 424961,
    "LT": 2939649,
}

BENCHMARK_TOKEN = 256265  # NIFTY 50

STRATEGIES_TO_TEST = [
    "STRAT_SMA_CROSS",
    "STRAT_DONCHIAN_BREAKOUT",
    "STRAT_BOLLINGER_REVERSION",
    "STRAT_EMA_PULLBACK",
    "STRAT_TTM_SQUEEZE",
    "STRAT_MOMENTUM_BREAKOUT_V1",
]


def load_session() -> Dict[str, str]:
    if not SESSION_FILE.exists():
        raise RuntimeError(f"Session file {SESSION_FILE} not found.")
    with open(SESSION_FILE) as f:
        return json.load(f)


def fetch_or_load_5year_data(symbols_tokens: Dict[str, int]) -> Dict[str, List[MarketBar]]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = DATA_DIR / "zerodha_5year_daily_universe.json"

    if cache_file.exists():
        print(f"[*] Loading 5-year dataset from local cache: {cache_file}")
        with open(cache_file) as f:
            raw = json.load(f)
        universe_bars = {}
        for sym, bars in raw.items():
            universe_bars[sym] = [
                MarketBar(
                    symbol=b["symbol"],
                    timestamp_iso=b["timestamp_iso"],
                    open=Decimal(str(b["open"])),
                    high=Decimal(str(b["high"])),
                    low=Decimal(str(b["low"])),
                    close=Decimal(str(b["close"])),
                    volume=int(b["volume"]),
                )
                for b in bars
            ]
        return universe_bars

    print(f"[*] Downloading 5-year daily data (2021-10-22 to 2026-10-21) from Zerodha Kite Connect...")
    sess = load_session()
    headers = {
        "X-Kite-Version": "3",
        "Authorization": f"token {sess['api_key']}:{sess['access_token']}",
    }

    from_date = (datetime.now() - timedelta(days=1825)).strftime("%Y-%m-%d")
    to_date = datetime.now().strftime("%Y-%m-%d")

    universe_bars: Dict[str, List[MarketBar]] = {}
    serializable_dict: Dict[str, List[Dict[str, Any]]] = {}

    for sym, token in symbols_tokens.items():
        url = f"https://api.kite.trade/instruments/historical/{token}/day?from={from_date}&to={to_date}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            candles = data.get("data", {}).get("candles", [])

        bar_list = []
        ser_list = []
        for c in candles:
            # candle format: [timestamp, open, high, low, close, volume]
            bar = MarketBar(
                symbol=sym,
                timestamp_iso=c[0],
                open=Decimal(str(c[1])).quantize(Decimal("0.05")),
                high=Decimal(str(c[2])).quantize(Decimal("0.05")),
                low=Decimal(str(c[3])).quantize(Decimal("0.05")),
                close=Decimal(str(c[4])).quantize(Decimal("0.05")),
                volume=int(c[5]) if len(c) > 5 else 0,
            )
            bar_list.append(bar)
            ser_list.append({
                "symbol": sym,
                "timestamp_iso": c[0],
                "open": str(bar.open),
                "high": str(bar.high),
                "low": str(bar.low),
                "close": str(bar.close),
                "volume": bar.volume,
            })

        universe_bars[sym] = bar_list
        serializable_dict[sym] = ser_list
        print(f"  [✓] {sym}: {len(bar_list)} daily bars downloaded.")
        time.sleep(0.3)

    with open(cache_file, "w") as f:
        json.dump(serializable_dict, f, indent=2)
    print(f"[✓] Cached 5-year dataset to {cache_file}")

    return universe_bars


def compute_benchmark_return(symbols_tokens: Dict[str, int]) -> Dict[str, Any]:
    """Fetch NIFTY 50 5-year return as benchmark."""
    sess = load_session()
    headers = {
        "X-Kite-Version": "3",
        "Authorization": f"token {sess['api_key']}:{sess['access_token']}",
    }
    from_date = (datetime.now() - timedelta(days=1825)).strftime("%Y-%m-%d")
    to_date = datetime.now().strftime("%Y-%m-%d")
    url = f"https://api.kite.trade/instruments/historical/{BENCHMARK_TOKEN}/day?from={from_date}&to={to_date}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        candles = json.loads(resp.read().decode()).get("data", {}).get("candles", [])

    if not candles:
        return {"return_pct": Decimal("0.0"), "cagr_pct": Decimal("0.0")}

    first_close = Decimal(str(candles[0][4]))
    last_close = Decimal(str(candles[-1][4]))
    total_return_pct = ((last_close - first_close) / first_close) * Decimal("100.0")
    # CAGR = (last/first)^(1/5) - 1
    years = Decimal("5.0")
    ratio = float(last_close / first_close)
    cagr_pct = Decimal(str((ratio ** (1.0 / 5.0) - 1.0) * 100.0)).quantize(Decimal("0.01"))

    return {
        "symbol": "NIFTY 50 (Benchmark)",
        "start_price": float(first_close),
        "end_price": float(last_close),
        "total_return_pct": float(total_return_pct.quantize(Decimal("0.01"))),
        "cagr_pct": float(cagr_pct),
    }


def run_strategy_backtest(
    strategy_id: str,
    market_data: Dict[str, List[MarketBar]],
    initial_capital: Decimal = Decimal("1000000.00"),  # 10 Lakh INR
) -> Dict[str, Any]:
    print(f"[*] Running 5-Year Replay: {strategy_id}...", end="", flush=True)
    t0 = time.time()

    engine = HistoricalReplayEngine(
        initial_capital=initial_capital,
        max_risk_per_trade_pct=Decimal("0.02"),
        strategy=strategy_id,
    )

    metrics: SimulationMetrics = engine.run_replay(market_data)
    duration_s = time.time() - t0

    years = Decimal("5.0")
    final_equity = metrics.ending_cash
    total_net_pnl = metrics.net_realized_pnl
    total_return_pct = ((final_equity - initial_capital) / initial_capital) * Decimal("100.0")

    ratio = float(final_equity / initial_capital)
    cagr_pct = Decimal(str((ratio ** (1.0 / 5.0) - 1.0) * 100.0)).quantize(Decimal("0.01")) if ratio > 0 else Decimal("-100.00")

    res = {
        "strategy_id": strategy_id,
        "initial_capital": float(initial_capital),
        "final_equity": float(final_equity),
        "net_pnl": float(total_net_pnl),
        "return_pct": float(total_return_pct),
        "cagr_pct": float(cagr_pct),
        "total_trades": metrics.total_trades,
        "win_rate_pct": float(metrics.win_rate_pct),
        "profit_factor": float(metrics.profit_factor),
        "max_drawdown_pct": float(metrics.max_drawdown_pct),
        "total_fees": float(metrics.total_statutory_fees),
        "total_slippage": float(metrics.total_slippage),
        "is_eod_balanced": metrics.is_eod_balanced,
        "duration_s": round(duration_s, 2),
    }
    print(f" [✓] Done in {duration_s:.2f}s | Net PnL: ₹{float(total_net_pnl):,.2f} | CAGR: {float(cagr_pct):.2f}%")
    return res


def main():
    print("=================================================================")
    print("  ALGO LAB UNIFIED: 5-YEAR MULTI-STRATEGY EMPIRICAL BACKTEST")
    print("  Period: 2021-10-22 to 2026-10-21 (5 Years Continuous)")
    print("  Universe: 9 Liquid NSE Large-Caps + NIFTY 50 Benchmark")
    print("  Starting Capital: INR 1,000,000.00 (10 Lakhs)")
    print("=================================================================")

    # 1. Fetch / Load Data
    market_data = fetch_or_load_5year_data(UNIVERSE)

    # 2. Benchmark Performance
    print("[*] Fetching NIFTY 50 Benchmark Performance...")
    benchmark = compute_benchmark_return(UNIVERSE)
    print(f"  [✓] NIFTY 50 (5-Yr Buy & Hold): Start: ₹{benchmark['start_price']} | End: ₹{benchmark['end_price']} | Return: {benchmark['total_return_pct']}% | CAGR: {benchmark['cagr_pct']}%")

    print("\n-----------------------------------------------------------------")
    print(f"  EVALUATING {len(STRATEGIES_TO_TEST)} QUANTITATIVE STRATEGIES")
    print("-----------------------------------------------------------------")

    results = []
    for strat_id in STRATEGIES_TO_TEST:
        try:
            res = run_strategy_backtest(strat_id, market_data)
            results.append(res)
        except Exception as e:
            print(f"  [✗] Failed to backtest {strat_id}: {e}")

    # Save summary report
    report_path = Path("/home/ubuntu/data/backtest/5year/five_year_backtest_report.json")
    full_report = {
        "execution_timestamp": datetime.now().isoformat(),
        "period": "2021-10-22 to 2026-10-21",
        "benchmark": benchmark,
        "results": results,
    }
    with open(report_path, "w") as f:
        json.dump(full_report, f, indent=2)

    print("\n=================================================================")
    print("  5-YEAR STRATEGY PERFORMANCE SUMMARY MATRIX")
    print("=================================================================")
    print(f"{'Strategy Name':<28} | {'Final Equity (₹)':<16} | {'Return %':<10} | {'CAGR %':<8} | {'Win %':<7} | {'PF':<6} | {'MaxDD %':<8} | {'Trades':<6}")
    print("-" * 105)

    # Print benchmark line
    print(f"{'NIFTY 50 (Buy & Hold)':<28} | {'-':<16} | {str(benchmark['total_return_pct']) + '%':<10} | {str(benchmark['cagr_pct']) + '%':<8} | {'-':<7} | {'-':<6} | {'-':<8} | {'-':<6}")
    print("-" * 105)

    for r in results:
        strat_display = r['strategy_id'].replace("STRAT_", "")
        print(f"{strat_display:<28} | ₹{r['final_equity']:<15,.2f} | {r['return_pct']:>8.2f}% | {r['cagr_pct']:>6.2f}% | {r['win_rate_pct']:>5.1f}% | {r['profit_factor']:>5.2f} | {r['max_drawdown_pct']:>6.2f}% | {r['total_trades']:<6}")

    print("=================================================================")
    print(f"[✓] Full audit artifact saved to: {report_path}")


if __name__ == "__main__":
    main()
