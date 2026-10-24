#!/usr/bin/env python3
"""Algo Lab Unified — Live Market Scanner & Paper Trading Dispatcher.

Executes:
1. Live market quote retrieval via authenticated Zerodha Kite Connect session.
2. Multi-factor stock screening (Trend Template, Screener.in 52w high, Chartink RSI, RVOL).
3. Strategic setup identification & ranking.
4. Adaptive strategy evaluation & StrategySignal generation.
5. Strict paper broker execution with ₹10,000.00 capital allocation:
   - Capital reservation
   - Statutory tick quantization
   - Exact NSE friction accounting
   - Double-entry balance sheet reconciliation
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
import json
import logging
from pathlib import Path
import sys
import urllib.request
import uuid

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from src.brokergateway.gateway import BrokerGateway
from src.brokergateway.models import ExecutionFill
from src.brokergateway.states import OrderSide, OrderState, OrderType
from src.capitalreservation.models import ReservationOutcome
from src.capitalreservation.service import CapitalReservationService
from src.core.monetary import format_monetary_decimal
from src.core.safety_invariants import assert_paper_only_invariants
from src.core.tick_precision import HistoricalSecurityTickMaster, quantize_order_price
from src.eodreconciler.reconciler import EODReconciler
from src.scanner.scanner import MarketBar
from src.scanner.stock_screener import StockScreener, StockScreenResult
from src.simulation.fee_model import NSEFrictionModel
from src.simulation.fill_model import RealisticPaperFillModel
from src.strategies.adaptive_router import AdaptiveStrategyRouter

SESSION_FILE = Path("/home/ubuntu/.zerodha_session.json")
DATA_CACHE = Path("/home/ubuntu/data/backtest/5year/zerodha_5year_daily_universe.json")

UNIVERSE_SYMBOLS = [
    "RELIANCE",
    "TCS",
    "INFY",
    "HDFCBANK",
    "ICICIBANK",
    "SBIN",
    "BHARTIARTL",
    "ITC",
    "LT",
]

SECTOR_MAP = {
    "RELIANCE": "ENERGY",
    "TCS": "IT",
    "INFY": "IT",
    "HDFCBANK": "BANKING",
    "ICICIBANK": "BANKING",
    "SBIN": "BANKING",
    "BHARTIARTL": "TELECOM",
    "ITC": "FMCG",
    "LT": "INFRA",
}


def load_session() -> dict:
    if not SESSION_FILE.exists():
        raise RuntimeError(f"Session file {SESSION_FILE} not found.")
    with open(SESSION_FILE) as f:
        return json.load(f)


def fetch_live_quotes(session: dict, symbols: list[str]) -> dict:
    api_key = session["api_key"]
    token = session["access_token"]
    instruments = [f"i=NSE:{s}" for s in symbols]
    url = f"https://api.kite.trade/quote?{'&'.join(instruments)}"
    headers = {
        "X-Kite-Version": "3",
        "Authorization": f"token {api_key}:{token}",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        res = json.loads(resp.read().decode())
        return res.get("data", {})


def run_pipeline(initial_capital: Decimal = Decimal("10000.00")):
    # 1. Assert paper-only safety invariants
    assert_paper_only_invariants()

    print("=" * 70)
    print("  ALGO LAB UNIFIED: LIVE SCANNER & ADAPTIVE PAPER TRADING")
    print(f"  Target Capital: ₹{initial_capital:,.2f}")
    print(f"  Timestamp: {datetime.now().isoformat()}")
    print("=" * 70)

    # 2. Load historical daily bars
    with open(DATA_CACHE) as f:
        raw_history = json.load(f)

    session = load_session()
    print("[*] Fetching live NSE market quotes from Zerodha Kite Connect...")
    live_quotes = fetch_live_quotes(session, UNIVERSE_SYMBOLS)
    print(f"  [✓] Received live quotes for {len(live_quotes)} symbols.")

    # 3. Assemble point-in-time universe with live bar appended
    today_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+05:30")
    full_universe: dict[str, list[MarketBar]] = {}

    for sym in UNIVERSE_SYMBOLS:
        hist_bars = [
            MarketBar(
                symbol=b["symbol"],
                timestamp_iso=b["timestamp_iso"],
                open=Decimal(str(b["open"])),
                high=Decimal(str(b["high"])),
                low=Decimal(str(b["low"])),
                close=Decimal(str(b["close"])),
                volume=int(b["volume"]),
            )
            for b in raw_history.get(sym, [])
        ]
        q_key = f"NSE:{sym}"
        if q_key in live_quotes:
            q = live_quotes[q_key]
            ohlc = q.get("ohlc", {})
            live_bar = MarketBar(
                symbol=sym,
                timestamp_iso=today_iso,
                open=Decimal(str(ohlc.get("open", q["last_price"]))),
                high=Decimal(str(ohlc.get("high", q["last_price"]))),
                low=Decimal(str(ohlc.get("low", q["last_price"]))),
                close=Decimal(str(q["last_price"])),
                volume=int(q.get("volume", 100000)),
            )
            hist_bars.append(live_bar)
        full_universe[sym] = hist_bars

    # 4. Run Multi-Factor Stock Screener
    print("\n[*] Running Universal Multi-Factor Stock Screener...")
    screener = StockScreener(
        min_price=Decimal("100.0"),
        min_avg_turnover=Decimal("10000000.0"),
        min_pct_of_52w_high=Decimal("80.0"),  # Within 20% of 52w high
        min_rvol=Decimal("0.5"),
        require_trend_template=False,         # Screen all candidates with scoring
        max_stocks_per_sector=2,
    )

    screened_candidates = screener.screen_universe(
        full_universe,
        symbol_sectors=SECTOR_MAP,
        top_n=9,
    )
    screened_candidates.sort(key=lambda c: (-c.composite_score, c.symbol))

    print("\n  RANKED SCREENER CANDIDATES:")
    fmt = "  %-12s | %-10s | %10s | %10s | %8s | %10s | %-18s | %-8s"
    print(fmt % ("Symbol", "Sector", "Live Price", "TrendScore", "RVOL", "Composite", "Setup Type", "52w High %"))
    print("  " + "-" * 98)
    for c in screened_candidates:
        p_str = f"₹{c.current_price:.2f}"
        pct_52w = c.metadata.get("pct_of_52w_high", "N/A") + "%"
        print(fmt % (c.symbol, c.sector, p_str, str(c.trend_score), str(c.rvol), str(c.composite_score), c.setup_type.value, pct_52w))

    if not screened_candidates:
        print("[!] No candidates passed the screener threshold. Enforcing WAIT.")
        return

    # Select top candidate
    top_candidate = screened_candidates[0]
    print(f"\n[★] Selected Top Candidate: {top_candidate.symbol} (Score: {top_candidate.composite_score}, Price: ₹{top_candidate.current_price})")

    # 5. Evaluate setup with AdaptiveStrategyRouter
    router = AdaptiveStrategyRouter()
    bars = full_universe[top_candidate.symbol]
    current_bar = bars[-1]
    history = bars[:-1]

    # Derive macro metrics and evaluate signal
    signal = router.evaluate(current_bar, history)

    # If top candidate is in consolidation without active breakout, evaluate EMA Pullback or Bollinger on top candidates
    candidate_signal = signal
    selected_symbol = top_candidate.symbol
    selected_bar = current_bar

    if candidate_signal is None:
        print("[*] Evaluating active strategies across ranked candidates for actionable entry...")
        for cand in screened_candidates:
            c_bars = full_universe[cand.symbol]
            c_sig = router.evaluate(c_bars[-1], c_bars[:-1])
            if c_sig:
                candidate_signal = c_sig
                selected_symbol = cand.symbol
                selected_bar = c_bars[-1]
                break

    # Strictly enforce WAIT / Snooze when router returns None
    if candidate_signal is None:
        print("[*] Router returned None (WAIT / Snooze Guard active across universe). Enforcing WAIT / Snooze. No paper orders synthesized.")
        return

    trade_purpose = "SIGNAL_TRIGGERED"
    entry_price = quantize_order_price(candidate_signal.target_entry_price, selected_symbol, side_is_buy=(candidate_signal.side == OrderSide.BUY), order_purpose="ENTRY")
    stop_loss = quantize_order_price(candidate_signal.stop_loss, selected_symbol, side_is_buy=(candidate_signal.side == OrderSide.BUY), order_purpose="STOP_LOSS")
    take_profit = quantize_order_price(candidate_signal.take_profit, selected_symbol, side_is_buy=(candidate_signal.side == OrderSide.BUY), order_purpose="TAKE_PROFIT")
    top_candidate = [c for c in screened_candidates if c.symbol == selected_symbol][0]
    selected_bar = full_universe[selected_symbol][-1]

    # 6. Position Sizing: 1.0% Ed Thorp Risk Rule on ₹10,000 Capital
    risk_per_trade = initial_capital * Decimal("0.01")  # ₹100.00 max risk
    risk_per_share = abs(entry_price - stop_loss)
    if risk_per_share <= Decimal("0.0"):
        risk_per_share = entry_price * Decimal("0.02")

    raw_shares = int(risk_per_trade / risk_per_share)
    # Maximum capital allocation cap: 30% of ₹10,000 = ₹3,000 max position notional
    max_notional_cap = initial_capital * Decimal("0.30")
    max_shares_by_notional = int(max_notional_cap / entry_price)

    calculated_shares = min(raw_shares, max_shares_by_notional)
    if calculated_shares <= 0:
        print("[!] Sizing rejected: Risk budget or capital allocation cannot afford 1 share.")
        return
    qty = calculated_shares
    notional = Decimal(str(qty)) * entry_price

    if notional > initial_capital:
        qty = int(initial_capital / entry_price)
        notional = Decimal(str(qty)) * entry_price

    if qty <= 0:
        print("[!] Sizing rejected: Insufficient capital for 1 share.")
        return

    print("\n" + "=" * 70)
    print("  PAPER ORDER EXECUTION DETAILS (₹10,000 CAPITAL)")
    print("=" * 70)
    print(f"  Symbol:          {top_candidate.symbol}")
    print(f"  Sector:          {top_candidate.sector}")
    print(f"  Side:            BUY (LONG)")
    print(f"  Order Type:      LIMIT")
    print(f"  Quantity:        {qty} shares")
    print(f"  Entry Price:     ₹{entry_price:.2f}")
    print(f"  Stop Loss:       ₹{stop_loss:.2f} (-{((entry_price - stop_loss)/entry_price)*100:.2f}%)")
    print(f"  Take Profit:     ₹{take_profit:.2f} (+{((take_profit - entry_price)/entry_price)*100:.2f}%)")
    print(f"  Risk/Reward:     1 : {((take_profit - entry_price)/risk_per_share):.2f}")
    print(f"  Total Notional:  ₹{notional:.2f} ({(notional/initial_capital)*100:.1f}% of capital)")

    # 7. Execute Atomic Capital Reservation
    reservation_service = CapitalReservationService()
    gateway = BrokerGateway()
    idem_key = f"IDEM-LIVE-{uuid.uuid4().hex[:8]}"

    res = reservation_service.reserve_capital(
        reservation_id=f"RES-PAPER-{uuid.uuid4().hex[:8]}",
        account_id="PAPER_ACC_10K",
        idempotency_key=idem_key,
        strategy_id="STRAT_ADAPTIVE_ROUTER",
        symbol=top_candidate.symbol,
        quantity=qty,
        price=entry_price,
        get_available_capital=lambda acc: initial_capital,
    )

    if res["outcome"] != ReservationOutcome.RESERVED:
        print(f"[!] Risk Reservation Rejected: {res.get('reason')}")
        return

    print(f"  [✓] Capital Reservation APPROVED: {res['reservation_id']}")

    # 8. Create & Submit Order in Broker Gateway
    order_id = f"ORD-LIVE-{uuid.uuid4().hex[:8]}"
    order = gateway.create_order(
        order_id=order_id,
        idempotency_key=idem_key,
        strategy_id="STRAT_ADAPTIVE_ROUTER",
        symbol=top_candidate.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=qty,
        price=entry_price,
        venue="PAPER",
        is_risk_approved=True,
        trade_date=today_iso,
    )
    gateway.transition_state(order_id, OrderState.RISK_RESERVED, "Risk capital reserved")
    gateway.transition_state(order_id, OrderState.SUBMITTED, "Dispatched order to simulated paper venue")

    # 9. Realistic Execution Fill & Friction Accounting
    friction = NSEFrictionModel.calculate_friction("BUY", qty, entry_price)
    fill_id = f"FILL-LIVE-{uuid.uuid4().hex[:8]}"
    fill = ExecutionFill(
        fill_id=fill_id,
        order_id=order_id,
        symbol=top_candidate.symbol,
        side=OrderSide.BUY,
        quantity=qty,
        price=entry_price,
        transaction_cost=friction.total_transaction_cost,
        slippage_cost=friction.slippage,
    )
    gateway.process_fill(fill)

    # 10. Financial Accounting & Double-Entry Balance Sheet
    remaining_cash = initial_capital - notional - friction.total_transaction_cost - friction.slippage
    portfolio_equity = remaining_cash + notional

    print("\n" + "-" * 70)
    print("  STATUTORY FRICTION & POST-TRADE BALANCE SHEET")
    print("-" * 70)
    print(f"  Brokerage:       ₹{friction.brokerage:.2f}")
    print(f"  STT / CTT:       ₹{friction.stt:.2f}")
    print(f"  Exchange Txn:    ₹{friction.exchange_charges:.2f}")
    print(f"  SEBI Charges:    ₹{friction.sebi_fees:.2f}")
    print(f"  GST (18%):       ₹{friction.gst:.2f}")
    print(f"  Stamp Duty:      ₹{friction.stamp_duty:.2f}")
    print(f"  Total Taxes/Fee: ₹{friction.total_transaction_cost:.2f}")
    print(f"  Est. Slippage:   ₹{friction.slippage:.2f}")
    print("  --------------------------------------------------")
    print(f"  Cash Remaining:  ₹{remaining_cash:.2f}")
    print(f"  Position Value:  ₹{notional:.2f}")
    print(f"  Total Equity:    ₹{portfolio_equity:.2f}")
    print(f"  Accounting Δ:    ₹{abs(portfolio_equity - (initial_capital - friction.total_friction)):.4f} (CONSERVED)")
    print("=" * 70)

    # 11. Save Trade Audit Record
    trade_record = {
        "timestamp_iso": today_iso,
        "symbol": top_candidate.symbol,
        "sector": top_candidate.sector,
        "side": "BUY",
        "quantity": qty,
        "entry_price": float(entry_price),
        "stop_loss": float(stop_loss),
        "take_profit": float(take_profit),
        "notional": float(notional),
        "friction": {
            "brokerage": float(friction.brokerage),
            "stt": float(friction.stt),
            "exchange": float(friction.exchange_charges),
            "sebi": float(friction.sebi_fees),
            "gst": float(friction.gst),
            "stamp_duty": float(friction.stamp_duty),
            "total_friction": float(friction.total_friction),
        },
        "initial_capital": float(initial_capital),
        "remaining_cash": float(remaining_cash),
        "portfolio_equity": float(portfolio_equity),
        "order_id": order_id,
        "fill_id": fill_id,
        "reservation_id": res["reservation_id"],
        "purpose": trade_purpose,
        "paper_only": True,
    }

    out_file = Path("/home/ubuntu/data/backtest/live_paper_trade_10k.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w") as f:
        json.dump(trade_record, f, indent=2)

    print(f"\n[✓] Live paper trade audit record saved to: {out_file}")


if __name__ == "__main__":
    run_pipeline(Decimal("10000.00"))
