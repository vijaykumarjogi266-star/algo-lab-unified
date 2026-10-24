"""Institutional Quantitative Analytics & Performance Engine for Paper Trading.

Calculates:
1. Double-entry friction breakdown (Brokerage, STT, Exchange, SEBI, GST, Stamp Duty, Slippage).
2. Win Rate, Profit Factor, Expectancy, Payoff Ratio.
3. Maximum Drawdown (Peak-to-Trough in INR and percentage).
4. Risk-Adjusted Returns (Sharpe Ratio, Sortino Ratio).
5. Comprehensive audit metrics dictionary.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math
from typing import Any, Dict, List, Optional

from src.core.safety_invariants import assert_paper_only_invariants
from src.paper.portfolio_tracker import PaperClosedTrade, PaperPortfolio


@dataclass(frozen=True)
class InstitutionalMetrics:
    initial_capital: Decimal
    current_cash: Decimal
    invested_value: Decimal
    total_equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    net_total_pnl: Decimal
    total_return_pct: Decimal
    peak_equity: Decimal
    max_drawdown_inr: Decimal
    max_drawdown_pct: Decimal
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: Decimal
    profit_factor: Decimal
    expectancy_inr: Decimal
    avg_win_inr: Decimal
    avg_loss_inr: Decimal
    payoff_ratio: Decimal
    sharpe_ratio: Decimal
    sortino_ratio: Decimal
    total_friction_paid: Decimal

    def to_dict(self) -> dict:
        return {
            "initial_capital": float(self.initial_capital),
            "current_cash": float(self.current_cash),
            "invested_value": float(self.invested_value),
            "total_equity": float(self.total_equity),
            "realized_pnl": float(self.realized_pnl),
            "unrealized_pnl": float(self.unrealized_pnl),
            "net_total_pnl": float(self.net_total_pnl),
            "total_return_pct": float(self.total_return_pct),
            "peak_equity": float(self.peak_equity),
            "max_drawdown_inr": float(self.max_drawdown_inr),
            "max_drawdown_pct": float(self.max_drawdown_pct),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate_pct": float(self.win_rate_pct),
            "profit_factor": float(self.profit_factor),
            "expectancy_inr": float(self.expectancy_inr),
            "avg_win_inr": float(self.avg_win_inr),
            "avg_loss_inr": float(self.avg_loss_inr),
            "payoff_ratio": float(self.payoff_ratio),
            "sharpe_ratio": float(self.sharpe_ratio),
            "sortino_ratio": float(self.sortino_ratio),
            "total_friction_paid": float(self.total_friction_paid),
        }


class QuantAnalyticsEngine:
    """Computes rigorous institutional risk and performance metrics."""

    @staticmethod
    def compute_metrics(
        portfolio: PaperPortfolio,
        risk_free_rate_pct: Decimal = Decimal("6.5"),  # RBI Repo Rate baseline
    ) -> InstitutionalMetrics:
        assert_paper_only_invariants()
        init_cap = portfolio.initial_capital
        cash = portfolio.cash_balance
        invested = portfolio.invested_value
        equity = portfolio.total_equity
        unrealized = portfolio.unrealized_pnl
        realized = portfolio.realized_pnl
        net_pnl = equity - init_cap
        ret_pct = ((equity - init_cap) / init_cap * Decimal("100")) if init_cap > 0 else Decimal("0")

        # Closed Trades Analysis
        trades = portfolio.closed_trades
        n_trades = len(trades)
        wins = [t.net_pnl for t in trades if t.net_pnl > Decimal("0")]
        losses = [abs(t.net_pnl) for t in trades if t.net_pnl < Decimal("0")]

        n_wins = len(wins)
        n_losses = len(losses)
        win_rate = (Decimal(str(n_wins)) / Decimal(str(n_trades)) * Decimal("100")) if n_trades > 0 else Decimal("0")

        gross_profit = sum(wins, Decimal("0.0"))
        gross_loss = sum(losses, Decimal("0.0"))
        profit_factor = (gross_profit / gross_loss) if gross_loss > Decimal("0") else (Decimal("99.9") if gross_profit > 0 else Decimal("0.0"))

        avg_win = (gross_profit / Decimal(str(n_wins))) if n_wins > 0 else Decimal("0.0")
        avg_loss = (gross_loss / Decimal(str(n_losses))) if n_losses > 0 else Decimal("0.0")
        payoff = (avg_win / avg_loss) if avg_loss > Decimal("0") else Decimal("0.0")

        # Expectancy = (Win% * AvgWin) - (Loss% * AvgLoss)
        p_win = Decimal(str(n_wins)) / Decimal(str(n_trades)) if n_trades > 0 else Decimal("0")
        p_loss = Decimal(str(n_losses)) / Decimal(str(n_trades)) if n_trades > 0 else Decimal("0")
        expectancy = (p_win * avg_win) - (p_loss * avg_loss)

        # Total Friction
        total_friction = sum((t.entry_friction + t.exit_friction for t in trades), Decimal("0.0"))
        total_friction += sum((p.entry_friction for p in portfolio.positions.values()), Decimal("0.0"))

        # Max Drawdown Calculation across closed trades equity curve
        peak = init_cap
        running_equity = init_cap
        max_dd_inr = Decimal("0.0")
        max_dd_pct = Decimal("0.0")

        for t in trades:
            running_equity += t.net_pnl
            if running_equity > peak:
                peak = running_equity
            dd_inr = peak - running_equity
            if dd_inr > max_dd_inr:
                max_dd_inr = dd_inr
            dd_pct = (dd_inr / peak * Decimal("100")) if peak > 0 else Decimal("0")
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct

        # Check current drawdown including open positions
        if equity > peak:
            peak = equity
        curr_dd_inr = peak - equity
        if curr_dd_inr > max_dd_inr:
            max_dd_inr = curr_dd_inr
        curr_dd_pct = (curr_dd_inr / peak * Decimal("100")) if peak > 0 else Decimal("0")
        if curr_dd_pct > max_dd_pct:
            max_dd_pct = curr_dd_pct

        # Sharpe & Sortino (per-trade basis annualized over 250 trading days)
        if n_trades >= 3:
            returns = [float(t.return_pct) for t in trades]
            mean_ret = sum(returns) / len(returns)
            variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
            std_dev = math.sqrt(variance) if variance > 0 else 0.0001
            
            # Downside deviation for Sortino
            downside_sq = sum((min(0.0, r) ** 2) for r in returns) / len(returns)
            downside_dev = math.sqrt(downside_sq) if downside_sq > 0 else 0.0001

            rf_per_trade = float(risk_free_rate_pct) / 250.0
            sharpe = Decimal(str(round((mean_ret - rf_per_trade) / std_dev * math.sqrt(250), 2)))
            sortino = Decimal(str(round((mean_ret - rf_per_trade) / downside_dev * math.sqrt(250), 2)))
        else:
            sharpe = Decimal("0.0")
            sortino = Decimal("0.0")

        return InstitutionalMetrics(
            initial_capital=init_cap,
            current_cash=cash,
            invested_value=invested,
            total_equity=equity,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            net_total_pnl=net_pnl,
            total_return_pct=ret_pct,
            peak_equity=peak,
            max_drawdown_inr=max_dd_inr,
            max_drawdown_pct=max_dd_pct,
            total_trades=n_trades,
            winning_trades=n_wins,
            losing_trades=n_losses,
            win_rate_pct=win_rate,
            profit_factor=profit_factor,
            expectancy_inr=expectancy,
            avg_win_inr=avg_win,
            avg_loss_inr=avg_loss,
            payoff_ratio=payoff,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            total_friction_paid=total_friction,
        )
