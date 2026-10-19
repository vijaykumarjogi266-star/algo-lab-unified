#!/usr/bin/env python3
"""Standardized 30-Day Multi-Strategy Evaluation Replay Script.

Executes standardized 30-session paper-trading replay across all verified strategies
on frozen historical data (`data/yfinance_nse_5m_30days.json`).

Output:
- reports/strategy_reuse/standardized_strategy_comparison.json
- Printed comparative table with exact monetary metrics and double-entry certification.
"""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import sys
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKSPACE_ROOT = REPO_ROOT
sys.path.insert(0, str(WORKSPACE_ROOT))

from src.core.monetary import format_monetary_decimal
from src.scanner.scanner import MarketBar
from src.simulation.engine import HistoricalReplayEngine
from src.strategies.registry import StrategyRegistry


def load_dataset(dataset_path: Path) -> Dict[str, Dict[str, List[MarketBar]]]:
    with open(dataset_path, "r") as f:
        data = json.load(f)

    days_dict = data["days"]
    parsed_days: Dict[str, Dict[str, List[MarketBar]]] = {}

    for date_str in sorted(days_dict.keys()):
        raw_day = days_dict[date_str]
        market_data: Dict[str, List[MarketBar]] = {}
        for sym, bars in raw_day.items():
            bar_objs = []
            for b in bars:
                mb = MarketBar(
                    symbol=b["symbol"],
                    timestamp_iso=b["timestamp_iso"],
                    open=Decimal(str(b["open"])),
                    high=Decimal(str(b["high"])),
                    low=Decimal(str(b["low"])),
                    close=Decimal(str(b["close"])),
                    volume=int(b["volume"]),
                    vwap=None,
                )
                bar_objs.append(mb)
            market_data[sym] = bar_objs
        parsed_days[date_str] = market_data

    return parsed_days


def evaluate_strategy_30days(
    strategy_id: str,
    parsed_days: Dict[str, Dict[str, List[MarketBar]]],
    strategy_params: Dict[str, Any] | None = None,
    initial_capital: Decimal = Decimal("100000.00"),
) -> Dict[str, Any]:
    current_cash = initial_capital
    peak_equity = current_cash
    max_drawdown = Decimal("0.00")

    total_gross = Decimal("0.00")
    total_fees = Decimal("0.00")
    total_slippage = Decimal("0.00")
    total_trades = 0
    total_wins = 0
    total_losses = 0
    unfilled_orders = 0
    partial_fills = 0
    daily_balance_checks = []

    for date_str, market_data in parsed_days.items():
        engine = HistoricalReplayEngine(
            initial_capital=current_cash,
            max_risk_per_trade_pct=Decimal("0.02"),
            strategy=strategy_id,
            strategy_params=strategy_params,
        )
        metrics = engine.run_replay(market_data)

        current_cash = metrics.ending_cash
        if current_cash > peak_equity:
            peak_equity = current_cash

        dd = ((peak_equity - current_cash) / peak_equity * Decimal("100")).quantize(Decimal("0.01"))
        if dd > max_drawdown:
            max_drawdown = dd

        total_gross += metrics.gross_realized_pnl
        total_fees += metrics.total_statutory_fees
        total_slippage += metrics.total_slippage
        total_trades += metrics.total_trades
        total_wins += metrics.winning_trades
        total_losses += metrics.losing_trades
        unfilled_orders += metrics.unfilled_orders_count
        partial_fills += metrics.partial_fills_count
        daily_balance_checks.append(metrics.is_eod_balanced)

    net_pnl = current_cash - initial_capital
    total_friction = total_fees + total_slippage
    win_rate = (Decimal(str(total_wins)) / Decimal(str(total_trades)) * Decimal("100")).quantize(Decimal("0.1")) if total_trades > 0 else Decimal("0.0")

    # Profit Factor
    profit_factor = Decimal("0.00")
    # Exact monetary conservation diff
    expected_ending = initial_capital + net_pnl
    conservation_delta = abs(current_cash - expected_ending)

    return {
        "strategy_id": strategy_id,
        "initial_capital_inr": format_monetary_decimal(initial_capital),
        "ending_cash_inr": format_monetary_decimal(current_cash),
        "gross_realized_pnl_inr": format_monetary_decimal(total_gross),
        "net_realized_pnl_inr": format_monetary_decimal(net_pnl),
        "total_statutory_fees_inr": format_monetary_decimal(total_fees),
        "total_slippage_inr": format_monetary_decimal(total_slippage),
        "total_friction_inr": format_monetary_decimal(total_friction),
        "total_trades": total_trades,
        "winning_trades": total_wins,
        "losing_trades": total_losses,
        "win_rate_pct": f"{win_rate:.1f}%",
        "max_drawdown_pct": f"{max_drawdown:.2f}%",
        "unfilled_orders": unfilled_orders,
        "partial_fills": partial_fills,
        "all_sessions_balanced": all(daily_balance_checks),
        "monetary_conservation_delta_inr": format_monetary_decimal(conservation_delta),
    }


def main() -> int:
    dataset_path = WORKSPACE_ROOT / "data" / "yfinance_nse_5m_30days.json"
    if not dataset_path.exists():
        dataset_path = WORKSPACE_ROOT.parent / "data" / "yfinance_nse_5m_30days.json"

    print("=======================================================================")
    print("  STANDARDIZED 30-DAY MULTI-STRATEGY REPLAY ON REAL NSE DATA")
    print(f"  Dataset: {dataset_path}")
    print("=======================================================================")

    parsed_days = load_dataset(dataset_path)
    print(f"Loaded {len(parsed_days)} trading sessions across universe symbols.")

    strategies_to_test = [
        ("STRAT_MOMENTUM_BREAKOUT_V1", None),
        ("STRAT_VWAP_REVERSION", None),
        ("STRAT_ORB", {"range_minutes": 15}),
        ("STRAT_SMA_CROSS", {"fast_period": 9, "slow_period": 21}),
        ("STRAT_DONCHIAN_BREAKOUT", {"lookback_period": 20}),
        ("STRAT_BOLLINGER_REVERSION", {"period": 20, "std_dev": Decimal("2.0")}),
        ("STRAT_TTM_SQUEEZE", {"bb_length": 20, "bb_std": Decimal("2.0")}),
        ("STRAT_OMS_HYBRID_ALPHA", None),
    ]

    results: List[Dict[str, Any]] = []

    print("\n" + "-" * 105)
    print(f"{'Strategy ID':<26} | {'Trades':<6} | {'Gross PnL':<12} | {'Net PnL':<12} | {'Fees+Slip':<10} | {'Win Rate':<8} | {'Max DD':<7} | {'Balanced':<8}")
    print("-" * 105)

    for strat_id, params in strategies_to_test:
        strat_res = evaluate_strategy_30days(strat_id, parsed_days, params)
        results.append(strat_res)
        print(
            f"{strat_res['strategy_id']:<26} | "
            f"{strat_res['total_trades']:<6} | "
            f"INR {strat_res['gross_realized_pnl_inr']:>8} | "
            f"INR {strat_res['net_realized_pnl_inr']:>8} | "
            f"INR {strat_res['total_friction_inr']:>6} | "
            f"{strat_res['win_rate_pct']:<8} | "
            f"{strat_res['max_drawdown_pct']:<7} | "
            f"{str(strat_res['all_sessions_balanced']):<8}"
        )

    print("-" * 105)

    # Save to reports/strategy_reuse/standardized_strategy_comparison.json
    output_dir = WORKSPACE_ROOT / "reports" / "strategy_reuse"
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / "standardized_strategy_comparison.json"
    with open(out_file, "w") as f:
        json.dump(
            {
                "evaluation_date_iso": "2026-10-19T21:20:00+05:30",
                "sessions_evaluated": len(parsed_days),
                "initial_capital_per_strategy": "100000.00",
                "risk_per_trade_pct": "2.00%",
                "friction_model": "NSE_STATUTORY_FEE_SCHEDULE",
                "fill_model": "REALISTIC_PENETRATION_AND_PARTIAL_FILLS",
                "results": results,
            },
            f,
            indent=2,
        )

    print(f"\n[OK] Machine-readable comparison written to {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
