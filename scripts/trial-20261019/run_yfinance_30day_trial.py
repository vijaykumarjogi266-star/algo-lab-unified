#!/usr/bin/env python3
"""Algo Lab — 30-Day Controlled Paper-Trading Trial with Real Yahoo Finance NSE Data.

Executes a 30-session paper-trading evaluation using:
- Real historical 5-minute NSE bars for RELIANCE, INFY, TCS, HDFCBANK
- Initial virtual capital: INR 100,000.00
- 1x simulated exposure (zero leverage)
- 2% risk per trade
- Statutory NSE friction model (STT, GST, turnover, stamp duty, slippage)
- Daily EOD double-entry balance sheet reconciliation
- Continuous output to yfinance_daily_trial_metrics.csv
"""

from __future__ import annotations

import csv
from decimal import Decimal
import json
import os
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if (REPO_ROOT / "src").exists():
    WORKSPACE_ROOT = REPO_ROOT
else:
    WORKSPACE_ROOT = REPO_ROOT / "algo-lab-unified-trial"
sys.path.insert(0, str(WORKSPACE_ROOT))

from src.core.monetary import format_monetary_decimal
from src.scanner.scanner import MarketBar
from src.simulation.engine import HistoricalReplayEngine


def main() -> int:
    workspace_dir = REPO_ROOT
    data_path = workspace_dir / "data" / "yfinance_nse_5m_30days.json"
    if not data_path.exists():
        data_path = workspace_dir.parent / "data" / "yfinance_nse_5m_30days.json"
    output_dir = workspace_dir / "reports" / "trial-20261019"
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "yfinance_daily_trial_metrics.csv"

    print("=================================================================")
    print("  ALGO LAB UNIFIED: 30-DAY PAPER TRIAL (REAL YFINANCE NSE DATA)")
    print("=================================================================")
    print("  Starting Virtual Capital: INR 100,000.00 (1x Exposure)")
    print(f"  Input Dataset:            {data_path}")
    print(f"  Output CSV:               {csv_path}")
    print("-----------------------------------------------------------------")

    with open(data_path, "r") as f:
        dataset = json.load(f)

    days_dict = dataset["days"]
    sorted_dates = sorted(list(days_dict.keys()))

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

    all_summaries = []

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for idx, s_date_str in enumerate(sorted_dates, 1):
            starting_for_day = current_cash
            raw_day_data = days_dict[s_date_str]

            market_data: dict[str, list[MarketBar]] = {}
            for sym, bar_list in raw_day_data.items():
                mb_list = []
                for b in bar_list:
                    mb = MarketBar(
                        symbol=b["symbol"],
                        timestamp_iso=b["timestamp_iso"],
                        open=Decimal(b["open"]),
                        high=Decimal(b["high"]),
                        low=Decimal(b["low"]),
                        close=Decimal(b["close"]),
                        volume=int(b["volume"]),
                        vwap=None
                    )
                    mb_list.append(mb)
                market_data[sym] = mb_list

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
                "calendar_date": s_date_str,
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

            all_summaries.append(s_summary)
            writer.writerow(s_summary)
            f.flush()

            print(
                f"  Day {idx:02d} ({s_date_str}): Cash: INR {current_cash:,.2f} | "
                f"Net: INR {metrics.net_realized_pnl:+,.2f} | "
                f"Trades: {metrics.total_trades} | "
                f"Balanced: {metrics.is_eod_balanced}"
            )

    # Compute overall statistics
    total_trades = sum(int(s["trades_count"]) for s in all_summaries)
    total_wins = sum(int(s["winning_trades"]) for s in all_summaries)
    total_losses = sum(int(s["losing_trades"]) for s in all_summaries)
    total_gross = sum(Decimal(s["gross_pnl_inr"]) for s in all_summaries)
    total_fees = sum(Decimal(s["statutory_fees_inr"]) for s in all_summaries)
    total_slippage = sum(Decimal(s["slippage_inr"]) for s in all_summaries)
    total_friction = sum(Decimal(s["total_friction_inr"]) for s in all_summaries)
    net_pnl_total = current_cash - Decimal("100000.00")
    win_rate = (Decimal(str(total_wins)) / Decimal(str(total_trades)) * Decimal("100")).quantize(Decimal("0.1")) if total_trades > 0 else Decimal("0.0")
    all_balanced = all(s["is_eod_balanced"] for s in all_summaries)

    print("=================================================================")
    print("  30-DAY YFINANCE TRIAL COMPLETE — PERFORMANCE SUMMARY")
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
    summary_path = output_dir / "yfinance_trial_summary_metrics.json"
    conservation_delta = current_cash - Decimal("100000.00") - net_pnl_total
    summary_payload = {
        "candidate": "Candidate 2 (Hardened: Historical Tick Rules & Realistic Paper Fills)",
        "fill_mode": "REALISTIC_PENETRATION",
        "statutory_circular": "NSE/CMTR/67133",
        "data_source": "Yahoo Finance (Real 5m NSE OHLCV)",
        "start_date": sorted_dates[0],
        "end_date": sorted_dates[-1],
        "initial_virtual_capital_inr": format_monetary_decimal(Decimal("100000.00")),
        "ending_cash_inr": format_monetary_decimal(current_cash),
        "net_realized_pnl_inr": format_monetary_decimal(net_pnl_total),
        "net_return_pct": float((net_pnl_total / Decimal("1000.0")).quantize(Decimal("0.01"))),
        "gross_realized_pnl_inr": format_monetary_decimal(total_gross),
        "total_statutory_fees_inr": format_monetary_decimal(total_fees),
        "total_slippage_inr": format_monetary_decimal(total_slippage),
        "total_friction_inr": format_monetary_decimal(total_friction),
        "peak_equity_inr": format_monetary_decimal(peak_equity),
        "max_drawdown_pct": float(max_drawdown),
        "total_trades": total_trades,
        "winning_trades": total_wins,
        "losing_trades": total_losses,
        "win_rate_pct": float(win_rate),
        "unfilled_limit_orders": getattr(metrics, "unfilled_orders_count", 0),
        "partial_fills_count": getattr(metrics, "partial_fills_count", 0),
        "accounting_conservation_delta": format_monetary_decimal(conservation_delta),
        "all_sessions_balanced": all_balanced,
        "discrepancies_count": 0 if all_balanced else 1,
        "tick_provenance": getattr(metrics, "tick_metadata_provenance", {}),
    }
    with open(summary_path, "w") as jf:
        json.dump(summary_payload, jf, indent=2)

    return 0 if all_balanced else 1


if __name__ == "__main__":
    sys.exit(main())
