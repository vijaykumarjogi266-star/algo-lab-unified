#!/usr/bin/env python3
"""Offline Historical Replay and Strategy Verification Runner.

Simulates a 60-bar intraday trading session across liquid NSE equities,
measuring scanner selectivity, momentum breakouts, friction attribution,
and double-entry accounting conservation.
"""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import sys

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from src.scanner.scanner import MarketBar
from src.simulation.engine import HistoricalReplayEngine


def generate_synthetic_session_bars() -> dict[str, list[MarketBar]]:
    """Generates synthetic 5-minute intraday bars (09:15 to 14:15 IST) for 4 symbols."""
    symbols = ["RELIANCE", "INFY", "TCS", "HDFCBANK"]
    base_prices = {
        "RELIANCE": Decimal("2850.00"),
        "INFY": Decimal("1480.00"),
        "TCS": Decimal("3820.00"),
        "HDFCBANK": Decimal("1640.00"),
    }
    market_data = {}

    import random
    random.seed(42)  # Deterministic seed for reproducible backtest

    for sym in symbols:
        bars = []
        p = base_prices[sym]
        for bar_idx in range(60):
            hour = 9 + (15 + bar_idx * 5) // 60
            minute = (15 + bar_idx * 5) % 60
            ts_iso = f"2026-10-19T{hour:02d}:{minute:02d}:00+05:30"

            # Simulate price movement
            drift = Decimal(str(random.uniform(-0.004, 0.005)))
            # Add volume surge at bar 25 for breakout trigger
            vol = random.randint(15000, 30000)
            if bar_idx == 25:
                vol = 120000
                drift = Decimal("0.012")  # 1.2% surge

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
    print("=================================================================")
    print("  ALGO LAB UNIFIED: OFFLINE HISTORICAL REPLAY BACKTEST")
    print("=================================================================")

    market_data = generate_synthetic_session_bars()
    engine = HistoricalReplayEngine(
        initial_capital=Decimal("500000.00"),  # 5 Lakh INR
        max_risk_per_trade_pct=Decimal("0.02"),
    )

    metrics = engine.run_replay(market_data)

    print(f"  Initial Capital:        INR {metrics.initial_capital:,.2f}")
    print(f"  Ending Cash:            INR {metrics.ending_cash:,.2f}")
    print(f"  Gross Realized P&L:     INR {metrics.gross_realized_pnl:,.2f}")
    print(f"  Net Realized P&L:       INR {metrics.net_realized_pnl:,.2f}")
    print(f"  Total Statutory Fees:   INR {metrics.total_statutory_fees:,.2f}")
    print(f"  Total Slippage:         INR {metrics.total_slippage:,.2f}")
    print(f"  Total Friction:         INR {metrics.total_friction:,.2f}")
    print(f"  Total Trades:           {metrics.total_trades}")
    print(f"  Winning Trades:         {metrics.winning_trades}")
    print(f"  Losing Trades:          {metrics.losing_trades}")
    print(f"  Win Rate:               {metrics.win_rate_pct}%")
    print(f"  Profit Factor:          {metrics.profit_factor}")
    print(f"  Max Drawdown:           {metrics.max_drawdown_pct}%")
    print(f"  Rejected Signals:       {metrics.rejected_signals_count}")
    print(f"  Double-Entry Balanced:  {'YES (BALANCED)' if metrics.is_eod_balanced else 'NO (IMBALANCED)'}")

    # Output JSON report
    report_dict = {
        "initial_capital_inr": float(metrics.initial_capital),
        "ending_cash_inr": float(metrics.ending_cash),
        "gross_realized_pnl_inr": float(metrics.gross_realized_pnl),
        "net_realized_pnl_inr": float(metrics.net_realized_pnl),
        "total_statutory_fees_inr": float(metrics.total_statutory_fees),
        "total_slippage_inr": float(metrics.total_slippage),
        "total_friction_inr": float(metrics.total_friction),
        "total_trades": metrics.total_trades,
        "winning_trades": metrics.winning_trades,
        "losing_trades": metrics.losing_trades,
        "win_rate_pct": float(metrics.win_rate_pct),
        "profit_factor": float(metrics.profit_factor),
        "max_drawdown_pct": float(metrics.max_drawdown_pct),
        "rejected_signals_count": metrics.rejected_signals_count,
        "is_eod_balanced": metrics.is_eod_balanced,
        "reconciliation_discrepancies": metrics.reconciliation_discrepancies,
    }

    report_path = Path("/opt/ai-builder/workspaces/algolab-unification-20261019-083200/reports/phase-8/backtest_metrics.json")
    with open(report_path, "w") as f:
        json.dump(report_dict, f, indent=2)

    print(f"\n[✓] Backtest report written to {report_path}")
    return 0 if metrics.is_eod_balanced else 1


if __name__ == "__main__":
    sys.exit(main())
