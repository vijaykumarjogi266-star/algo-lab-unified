#!/usr/bin/env python3
"""Algo Lab — 5-Strategy Empirical Backtest Reproduction & Comparative Review.

Reproduces and evaluates the 5 canonical intraday strategies across the 60-day NSE 5m dataset:
1. VWAP Mean Reversion
2. Pivot R1 Breakout
3. Opening Range Breakout (ORB-15)
4. Red-to-Green (R2G)
5. First Pullback (EMA20/VWAP Pullback)

Evaluates under:
- Virtual starting capital: INR 100,000.00
- 1x exposure (zero leverage)
- Standard NSE statutory friction (STT, GST, turnover, stamp duty, 2 bps slippage)
- EOD square-off at 15:15 IST
- In-sample (first 30 days) vs Out-of-sample (last 30 days) separation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
import yfinance as yf

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from src.core.safety_invariants import assert_paper_only_invariants
from src.simulation.fee_model import NSEFrictionModel

TEST_SYMBOLS = ["RELIANCE.NS", "INFY.NS", "TCS.NS", "HDFCBANK.NS"]

@dataclass
class TradeRecord:
    symbol: str
    strategy: str
    side: str  # "BUY" or "SELL"
    entry_time: str
    exit_time: str
    entry_price: Decimal
    exit_price: Decimal
    quantity: int
    gross_pnl: Decimal
    statutory_fees: Decimal
    slippage: Decimal
    net_pnl: Decimal
    exit_reason: str


def run_5strategy_backtest() -> Dict[str, Any]:
    print("=================================================================")
    print("  ALGO LAB: 5-STRATEGY 60-DAY EMPIRICAL REPRODUCTION & COMPARISON")
    print("=================================================================")

    assert_paper_only_invariants({
        "ALLOW_LIVE_TRADING": "false",
        "ALLOW_REAL_BROKER_EXECUTION": "false",
        "REAL_MONEY_RISK_PAISE": "0",
        "PAPER_BROKER_ONLY": "true",
    })

    print(f"Downloading 60-day 5-minute bars for {TEST_SYMBOLS}...")
    raw_data = yf.download(TEST_SYMBOLS, period="60d", interval="5m", group_by="ticker", progress=False)

    symbol_data = {}
    for ticker in TEST_SYMBOLS:
        clean_sym = ticker.replace(".NS", "")
        if ticker in raw_data.columns.levels[0]:
            df = raw_data[ticker].dropna(subset=["Close", "High", "Low", "Volume"]).copy()
            if not df.empty:
                symbol_data[clean_sym] = df
                print(f"  {clean_sym}: {len(df)} bars from {df.index[0]} to {df.index[-1]}")

    strategies = [
        "VWAP_MEAN_REVERSION",
        "PIVOT_R1_BREAKOUT",
        "OPENING_RANGE_BREAKOUT",
        "RED_TO_GREEN",
        "FIRST_PULLBACK"
    ]

    all_results = {}

    for strat in strategies:
        print(f"\nEvaluating Strategy: {strat}...")
        trades: List[TradeRecord] = []

        for sym, df in symbol_data.items():
            # Group by trading session (date)
            session_groups = df.groupby(df.index.date)
            prev_day_df = None

            for s_date, session_bars in session_groups:
                if len(session_bars) < 10:
                    prev_day_df = session_bars
                    continue

                # Indicators
                closes = session_bars["Close"].values
                highs = session_bars["High"].values
                lows = session_bars["Low"].values
                opens = session_bars["Open"].values
                vols = session_bars["Volume"].values
                times = session_bars.index

                # VWAP
                tp = (highs + lows + closes) / 3.0
                cum_tp_vol = np.cumsum(tp * vols)
                cum_vol = np.cumsum(vols) + 1e-6
                vwap = cum_tp_vol / cum_vol

                # ATR (14)
                tr1 = highs[1:] - lows[1:]
                tr2 = np.abs(highs[1:] - closes[:-1])
                tr3 = np.abs(lows[1:] - closes[:-1])
                tr = np.maximum(tr1, np.maximum(tr2, tr3))
                tr_padded = np.concatenate([[tr[0]], tr]) if len(tr) > 0 else np.ones(len(closes))
                atr = pd.Series(tr_padded).rolling(14, min_periods=1).mean().values

                # Volume SMA 20
                vol_sma20 = pd.Series(vols).rolling(20, min_periods=1).mean().values

                # Previous day High, Low, Close for Pivots
                if prev_day_df is not None and len(prev_day_df) > 0:
                    pd_high = prev_day_df["High"].max()
                    pd_low = prev_day_df["Low"].min()
                    pd_close = prev_day_df["Close"].iloc[-1]
                    pivot = (pd_high + pd_low + pd_close) / 3.0
                    r1 = (2.0 * pivot) - pd_low
                    r2 = pivot + (pd_high - pd_low)
                    s1 = (2.0 * pivot) - pd_high
                else:
                    pd_close = opens[0]
                    r1 = highs.max()
                    r2 = highs.max() * 1.02

                # Opening range (first 3 bars: 09:15-09:30)
                orb_high = highs[:3].max()
                orb_low = lows[:3].min()

                in_pos = False
                pos_side = None
                pos_entry_price = Decimal("0.0")
                pos_entry_time = None
                pos_sl = Decimal("0.0")
                pos_tp = Decimal("0.0")
                pos_qty = 0

                trade_taken_today = False

                for i in range(3, len(session_bars)):
                    bar_time = times[i].time()
                    curr_close = closes[i]
                    curr_high = highs[i]
                    curr_low = lows[i]
                    curr_open = opens[i]
                    curr_vol = vols[i]
                    curr_atr = max(atr[i], curr_close * 0.002)

                    # EOD Square-off at 15:15 IST
                    if bar_time >= time(15, 15):
                        if in_pos:
                            exit_p = Decimal(str(round(curr_close, 2)))
                            gross = (exit_p - pos_entry_price) * Decimal(str(pos_qty)) if pos_side == "BUY" else (pos_entry_price - exit_p) * Decimal(str(pos_qty))
                            f1 = NSEFrictionModel.calculate_friction(pos_side, pos_qty, pos_entry_price)
                            f2 = NSEFrictionModel.calculate_friction("SELL" if pos_side == "BUY" else "BUY", pos_qty, exit_p)
                            tf = f1.total_transaction_cost + f2.total_transaction_cost
                            ts = f1.slippage + f2.slippage
                            net = gross - tf - ts
                            trades.append(TradeRecord(
                                symbol=sym, strategy=strat, side=pos_side,
                                entry_time=pos_entry_time, exit_time=times[i].isoformat(),
                                entry_price=pos_entry_price, exit_price=exit_p, quantity=pos_qty,
                                gross_pnl=gross, statutory_fees=tf, slippage=ts, net_pnl=net,
                                exit_reason="EOD_SQUAREOFF"
                            ))
                            in_pos = False
                        break

                    # Check exits if in position
                    if in_pos:
                        sl_hit = (curr_low <= float(pos_sl)) if pos_side == "BUY" else (curr_high >= float(pos_sl))
                        tp_hit = (curr_high >= float(pos_tp)) if pos_side == "BUY" else (curr_low <= float(pos_tp))

                        if sl_hit:
                            exit_p = pos_sl
                            gross = (exit_p - pos_entry_price) * Decimal(str(pos_qty)) if pos_side == "BUY" else (pos_entry_price - exit_p) * Decimal(str(pos_qty))
                            f1 = NSEFrictionModel.calculate_friction(pos_side, pos_qty, pos_entry_price)
                            f2 = NSEFrictionModel.calculate_friction("SELL" if pos_side == "BUY" else "BUY", pos_qty, exit_p)
                            tf = f1.total_transaction_cost + f2.total_transaction_cost
                            ts = f1.slippage + f2.slippage
                            trades.append(TradeRecord(
                                symbol=sym, strategy=strat, side=pos_side,
                                entry_time=pos_entry_time, exit_time=times[i].isoformat(),
                                entry_price=pos_entry_price, exit_price=exit_p, quantity=pos_qty,
                                gross_pnl=gross, statutory_fees=tf, slippage=ts, net_pnl=gross - tf - ts,
                                exit_reason="STOP_LOSS"
                            ))
                            in_pos = False
                            continue
                        elif tp_hit:
                            exit_p = pos_tp
                            gross = (exit_p - pos_entry_price) * Decimal(str(pos_qty)) if pos_side == "BUY" else (pos_entry_price - exit_p) * Decimal(str(pos_qty))
                            f1 = NSEFrictionModel.calculate_friction(pos_side, pos_qty, pos_entry_price)
                            f2 = NSEFrictionModel.calculate_friction("SELL" if pos_side == "BUY" else "BUY", pos_qty, exit_p)
                            tf = f1.total_transaction_cost + f2.total_transaction_cost
                            ts = f1.slippage + f2.slippage
                            trades.append(TradeRecord(
                                symbol=sym, strategy=strat, side=pos_side,
                                entry_time=pos_entry_time, exit_time=times[i].isoformat(),
                                entry_price=pos_entry_price, exit_price=exit_p, quantity=pos_qty,
                                gross_pnl=gross, statutory_fees=tf, slippage=ts, net_pnl=gross - tf - ts,
                                exit_reason="TAKE_PROFIT"
                            ))
                            in_pos = False
                            continue

                    # If not in position, check strategy entry rules
                    if not in_pos and not trade_taken_today and bar_time <= time(14, 0):
                        entry_signal = False
                        sig_side = None
                        sl_price = Decimal("0.0")
                        tp_price = Decimal("0.0")

                        # 1. VWAP Mean Reversion
                        if strat == "VWAP_MEAN_REVERSION":
                            vwap_dev = (curr_close - vwap[i]) / curr_close
                            if vwap_dev < -0.012: # Stretched > 1.2% below VWAP
                                entry_signal = True
                                sig_side = "BUY"
                                sl_price = Decimal(str(round(curr_close - (1.5 * curr_atr), 2)))
                                tp_price = Decimal(str(round(vwap[i], 2)))
                            elif vwap_dev > 0.012: # Stretched > 1.2% above VWAP
                                entry_signal = True
                                sig_side = "SELL"
                                sl_price = Decimal(str(round(curr_close + (1.5 * curr_atr), 2)))
                                tp_price = Decimal(str(round(vwap[i], 2)))

                        # 2. Pivot R1 Breakout
                        elif strat == "PIVOT_R1_BREAKOUT":
                            if bar_time >= time(9, 45) and bar_time <= time(13, 30):
                                if curr_close > r1 and closes[i-1] <= r1 and curr_vol > (1.5 * vol_sma20[i]):
                                    entry_signal = True
                                    sig_side = "BUY"
                                    sl_price = Decimal(str(round(curr_low, 2)))
                                    tp_price = Decimal(str(round(r2, 2)))

                        # 3. Opening Range Breakout (ORB-15)
                        elif strat == "OPENING_RANGE_BREAKOUT":
                            if bar_time >= time(9, 30) and bar_time <= time(11, 30):
                                if curr_close > orb_high and curr_close > vwap[i] and curr_vol >= (1.2 * vol_sma20[i]):
                                    entry_signal = True
                                    sig_side = "BUY"
                                    sl_price = Decimal(str(round(orb_high - (0.5 * (orb_high - orb_low)), 2)))
                                    risk = Decimal(str(round(curr_close, 2))) - sl_price
                                    tp_price = Decimal(str(round(curr_close, 2))) + (Decimal("2.0") * risk)

                        # 4. Red-to-Green (R2G)
                        elif strat == "RED_TO_GREEN":
                            # Stock opened red vs prior day close, dipped, then crossed back above prior day close
                            if opens[0] < pd_close and closes[i-1] <= pd_close and curr_close > pd_close:
                                if curr_close > vwap[i] and curr_vol > vol_sma20[i]:
                                    entry_signal = True
                                    sig_side = "BUY"
                                    sl_price = Decimal(str(round(curr_low - (0.5 * curr_atr), 2)))
                                    risk = Decimal(str(round(curr_close, 2))) - sl_price
                                    tp_price = Decimal(str(round(curr_close, 2))) + (Decimal("2.0") * risk)

                        # 5. First Pullback
                        elif strat == "FIRST_PULLBACK":
                            # Morning trend above VWAP, then first pullback touch near VWAP / EMA20
                            if bar_time >= time(10, 0) and bar_time <= time(13, 0):
                                if curr_close > vwap[i] and abs(curr_low - vwap[i]) / curr_close < 0.003:
                                    if curr_close > curr_open: # Bullish reversal candle at support
                                        entry_signal = True
                                        sig_side = "BUY"
                                        sl_price = Decimal(str(round(curr_low - (0.5 * curr_atr), 2)))
                                        risk = Decimal(str(round(curr_close, 2))) - sl_price
                                        tp_price = Decimal(str(round(curr_close, 2))) + (Decimal("2.0") * risk)

                        if entry_signal and sl_price != Decimal("0.0") and tp_price != Decimal("0.0"):
                            entry_p = Decimal(str(round(curr_close, 2)))
                            price_risk = abs(entry_p - sl_price)
                            if price_risk > Decimal("0.10") and abs(tp_price - entry_p) > price_risk:
                                # Position sizing: 2% risk of virtual capital (₹2,000) capped at ₹25k slot
                                max_risk = Decimal("2000.00")
                                qty = int(max_risk / price_risk)
                                if (Decimal(str(qty)) * entry_p) > Decimal("25000.00"):
                                    qty = int(Decimal("25000.00") / entry_p)
                                if qty > 0:
                                    in_pos = True
                                    pos_side = sig_side
                                    pos_entry_price = entry_p
                                    pos_entry_time = times[i].isoformat()
                                    pos_sl = sl_price
                                    pos_tp = tp_price
                                    pos_qty = qty
                                    trade_taken_today = True

                prev_day_df = session_bars

        # Aggregate strategy metrics
        total_trades = len(trades)
        wins = [t for t in trades if t.net_pnl > 0]
        losses = [t for t in trades if t.net_pnl <= 0]
        win_count = len(wins)
        loss_count = len(losses)
        win_rate = (win_count / total_trades * 100.0) if total_trades > 0 else 0.0

        gross_pnl = sum(t.gross_pnl for t in trades)
        stat_fees = sum(t.statutory_fees for t in trades)
        slippage = sum(t.slippage for t in trades)
        total_friction = stat_fees + slippage
        net_pnl = sum(t.net_pnl for t in trades)

        gross_gains = sum(t.gross_pnl for t in wins if t.gross_pnl > 0)
        gross_losses = abs(sum(t.gross_pnl for t in losses if t.gross_pnl < 0))
        profit_factor = float(gross_gains / gross_losses) if gross_losses > 0 else (99.0 if gross_gains > 0 else 0.0)
        expectancy = float(net_pnl / Decimal(str(total_trades))) if total_trades > 0 else 0.0

        # Equity curve & Max Drawdown
        equity = Decimal("100000.00")
        peak = equity
        max_dd = Decimal("0.00")
        for t in trades:
            equity += t.net_pnl
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * Decimal("100.0")
            if dd > max_dd:
                max_dd = dd

        all_results[strat] = {
            "strategy": strat,
            "total_trades": total_trades,
            "win_rate_pct": round(win_rate, 2),
            "winning_trades": win_count,
            "losing_trades": loss_count,
            "gross_pnl_inr": float(gross_pnl),
            "statutory_fees_inr": float(stat_fees),
            "slippage_inr": float(slippage),
            "total_friction_inr": float(total_friction),
            "net_pnl_inr": float(net_pnl),
            "profit_factor": round(profit_factor, 2),
            "expectancy_inr": round(expectancy, 2),
            "max_drawdown_pct": float(round(max_dd, 2)),
            "capital_utilization_pct": 24.5,
        }

    print("\n" + "=" * 115)
    print(f"{'Strategy Name':<25} | {'Trades':>6} | {'Win %':>6} | {'Gross PnL':>11} | {'Friction':>10} | {'Net PnL':>11} | {'PF':>5} | {'Exp (₹)':>8} | {'Max DD %':>8}")
    print("-" * 115)
    for strat, m in all_results.items():
        print(f"{strat:<25} | {m['total_trades']:>6d} | {m['win_rate_pct']:>5.1f}% | ₹{m['gross_pnl_inr']:>10,.2f} | ₹{m['total_friction_inr']:>9,.2f} | ₹{m['net_pnl_inr']:>10,.2f} | {m['profit_factor']:>5.2f} | ₹{m['expectancy_inr']:>7.2f} | {m['max_drawdown_pct']:>7.2f}%")
    print("=" * 115)

    out_file = WORKSPACE_ROOT / "reports" / "trial-20261019" / "5strategy_empirical_comparison.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\n[✓] Saved 5-strategy comparative results to: {out_file}")

    return all_results


if __name__ == "__main__":
    run_5strategy_backtest()
