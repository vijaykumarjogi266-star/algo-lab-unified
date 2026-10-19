#!/usr/bin/env python3
"""Algo Lab — 30-Day Controlled Paper-Trading Trial Runner.

Executes a 30-session paper-trading evaluation with:
- Initial virtual capital: INR 100,000.00
- 1x simulated exposure (zero leverage)
- 4 Liquid NSE equities: RELIANCE, INFY, TCS, HDFCBANK
- Daily EOD double-entry balance sheet reconciliation
- Continuous output to daily_trial_metrics.csv
"""

from __future__ import annotations

import csv
from decimal import Decimal
import json
import os
from pathlib import Path
import random
import sys
from datetime import datetime, date, timedelta

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent / "algo-lab-unified-trial"
sys.path.insert(0, str(WORKSPACE_ROOT))

from src.scanner.scanner import MarketBar
from src.simulation.engine import HistoricalReplayEngine


def generate_session_market_bars(day_index: int, session_date: date) -> dict[str, list[MarketBar]]:
    """Generates deterministic intraday 5-min bars for 4 liquid NSE equities."""
    symbols = ["RELIANCE", "INFY", "TCS", "HDFCBANK"]
    base_prices = {
        "RELIANCE": Decimal("2850.00"),
        "INFY": Decimal("1480.00"),
        "TCS": Decimal("3820.00"),
        "HDFCBANK": Decimal("1640.00"),
    }
    
    # Use deterministic seed tied to day index
    random.seed(day_index * 1337 + 42)
    market_data = {}

    for sym in symbols:
        bars = []
        p = base_prices[sym] * (Decimal("1.0") + Decimal(str(random.uniform(-0.02, 0.02))))
        p = p.quantize(Decimal("0.05"))
        
        # Determine breakout bar index
        breakout_bar = random.choice([15, 25, 35, 45])
        breakout_direction = random.choice([1, -1])

        for bar_idx in range(60):
            hour = 9 + (15 + bar_idx * 5) // 60
            minute = (15 + bar_idx * 5) % 60
            ts_iso = f"{session_date.isoformat()}T{hour:02d}:{minute:02d}:00+05:30"

            drift = Decimal(str(random.uniform(-0.003, 0.003)))
            vol = random.randint(12000, 35000)

            if bar_idx == breakout_bar:
                vol = random.randint(85000, 140000)
                drift = Decimal("0.014") * breakout_direction

            open_p = p
            close_p = (open_p * (Decimal("1.0") + drift)).quantize(Decimal("0.05"))
            high_p = (max(open_p, close_p) * Decimal("1.002")).quantize(Decimal("0.05"))
            low_p = (min(open_p, close_p) * Decimal("0.998")).quantize(Decimal("0.05"))
            p = close_p

            bars.append(MarketBar(
                symbol=sym,
                timestamp_iso=ts_iso,
                open=open_p,
                high=high_p,
                low=low_p,
                close=close_p,
                volume=vol,
            ))
        market_data[sym] = bars

    return market_data


def main() -> int:
    output_dir = Path(__file__).resolve().parent.parent / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "daily_trial_metrics.csv"

    print("=================================================================")
    print("  ALGO LAB UNIFIED: 30-DAY CONTROLLED PAPER-TRADING TRIAL")
    print("=================================================================")
    print("  Starting Virtual Capital: INR 100,000.00 (1x Exposure)")
    print("  Target Sessions:          30 Daily Sessions")
    print(f"  Output CSV:               {csv_path}")
    print("-----------------------------------------------------------------")

    fieldnames = [
        "session_day", "calendar_date", "starting_cash_inr", "ending_cash_inr",
        "gross_pnl_inr", "statutory_fees_inr", "slippage_inr", "total_friction_inr",
        "net_pnl_inr", "trades_count", "winning_trades", "losing_trades",
        "signals_generated", "signals_rejected", "peak_equity_inr",
        "max_drawdown_pct", "is_eod_balanced"
    ]

    current_cash = Decimal("100000.00")
    peak_equity = current_cash
    max_drawdown = Decimal("0.00")

    start_date = date(2026, 9, 1)
    business_days = []
    d = start_date
    while len(business_days) < 30:
        if d.weekday() < 5:  # Monday to Friday
            business_days.append(d)
        d += timedelta(days=1)

    all_summaries = []

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for idx, s_date in enumerate(business_days, 1):
            starting_for_day = current_cash
            market_data = generate_session_market_bars(idx, s_date)
            
            # Execute session via HistoricalReplayEngine
            engine = HistoricalReplayEngine(
                initial_capital=starting_for_day,
                max_risk_per_trade_pct=Decimal("0.02")
            )
            metrics = engine.run_replay(market_data)

            current_cash = metrics.ending_cash
            if current_cash > peak_equity:
                peak_equity = current_cash
            
            dd = ((peak_equity - current_cash) / peak_equity * Decimal("100")).quantize(Decimal("0.01"))
            if dd > max_drawdown:
                max_drawdown = dd

            s_summary = {
                "session_day": idx,
                "calendar_date": s_date.isoformat(),
                "starting_cash_inr": f"{starting_for_day:.2f}",
                "ending_cash_inr": f"{current_cash:.2f}",
                "gross_pnl_inr": f"{metrics.gross_realized_pnl:.2f}",
                "statutory_fees_inr": f"{metrics.total_statutory_fees:.2f}",
                "slippage_inr": f"{metrics.total_slippage:.2f}",
                "total_friction_inr": f"{metrics.total_friction:.2f}",
                "net_pnl_inr": f"{metrics.net_realized_pnl:.2f}",
                "trades_count": metrics.total_trades,
                "winning_trades": metrics.winning_trades,
                "losing_trades": metrics.losing_trades,
                "signals_generated": metrics.total_trades + metrics.rejected_signals_count,
                "signals_rejected": metrics.rejected_signals_count,
                "peak_equity_inr": f"{peak_equity:.2f}",
                "max_drawdown_pct": f"{max_drawdown:.2f}",
                "is_eod_balanced": metrics.is_eod_balanced,
            }

            writer.writerow(s_summary)
            f.flush()
            all_summaries.append(s_summary)

            print(f"  Day {idx:02d} ({s_date}): Cash: INR {current_cash:,.2f} | Net: INR {metrics.net_realized_pnl:+,.2f} | Trades: {metrics.total_trades} | Balanced: {metrics.is_eod_balanced}")

    # Final summary calculations
    net_pnl_total = current_cash - Decimal("100000.00")
    total_trades = sum(s["trades_count"] for s in all_summaries)
    total_wins = sum(s["winning_trades"] for s in all_summaries)
    total_losses = sum(s["losing_trades"] for s in all_summaries)
    win_rate = (Decimal(total_wins) / Decimal(total_trades) * 100).quantize(Decimal("0.1")) if total_trades > 0 else Decimal("0.0")

    total_gross = sum(Decimal(s["gross_pnl_inr"]) for s in all_summaries)
    total_friction = sum(Decimal(s["total_friction_inr"]) for s in all_summaries)
    total_fees = sum(Decimal(s["statutory_fees_inr"]) for s in all_summaries)
    total_slippage = sum(Decimal(s["slippage_inr"]) for s in all_summaries)

    all_balanced = all(s["is_eod_balanced"] for s in all_summaries)

    print("=================================================================")
    print("  30-DAY TRIAL COMPLETE — PERFORMANCE SUMMARY")
    print("=================================================================")
    print("  Initial Virtual Capital:  INR 100,000.00")
    print(f"  Final Ending Cash:        INR {current_cash:,.2f}")
    print(f"  Gross Realized P&L:       INR {total_gross:,.2f}")
    print(f"  Total Statutory Fees:     INR {total_fees:,.2f}")
    print(f"  Total Slippage:           INR {total_slippage:,.2f}")
    print(f"  Total Friction:           INR {total_friction:,.2f}")
    print(f"  Net Realized P&L:         INR {net_pnl_total:,.2f} ({net_pnl_total/1000:.2f}%)")
    print(f"  Peak Equity:              INR {peak_equity:,.2f}")
    print(f"  Maximum Drawdown:         {max_drawdown}%")
    print(f"  Total Trades:             {total_trades}")
    print(f"  Winning Trades:           {total_wins} ({win_rate}%)")
    print(f"  Losing Trades:            {total_losses}")
    print(f"  All Days Balanced:        {'YES (100% BALANCED)' if all_balanced else 'NO (IMBALANCED)'}")
    print("=================================================================")

    # Write summary JSON for machine audit
    summary_path = output_dir / "paper_trial_summary_metrics.json"
    with open(summary_path, "w") as jf:
        json.dump({
            "initial_virtual_capital_inr": 100000.0,
            "ending_cash_inr": float(current_cash),
            "net_realized_pnl_inr": float(net_pnl_total),
            "net_return_pct": float(net_pnl_total / Decimal("1000.0")),
            "gross_realized_pnl_inr": float(total_gross),
            "total_statutory_fees_inr": float(total_fees),
            "total_slippage_inr": float(total_slippage),
            "total_friction_inr": float(total_friction),
            "peak_equity_inr": float(peak_equity),
            "max_drawdown_pct": float(max_drawdown),
            "total_trades": total_trades,
            "winning_trades": total_wins,
            "losing_trades": total_losses,
            "win_rate_pct": float(win_rate),
            "all_sessions_balanced": all_balanced,
            "discrepancies_count": 0 if all_balanced else 1,
        }, jf, indent=2)

    return 0 if all_balanced else 1


if __name__ == "__main__":
    sys.exit(main())
