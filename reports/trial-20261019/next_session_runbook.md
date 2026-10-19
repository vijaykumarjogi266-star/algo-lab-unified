# Algo Lab — Next-Session Forward Paper Trading Runbook

**Task Identifier:** `algolab-paper-trial-20261019-183015`  
**Target Trading Session:** Tuesday, October 20, 2026 (09:00 to 15:30 IST)  
**Execution Environment:** Isolated Workspace `/opt/ai-builder/workspaces/algolab-paper-trial-20261019-183015/`  
**Active Runner Binary:** [`scripts/run_forward_paper_trading.py`](file:///opt/ai-builder/workspaces/algolab-paper-trial-20261019-183015/scripts/run_forward_paper_trading.py) (Daemon PID `3185633`)  
**Preservation Invariant:** Canonical PID `3009443` in `/home/ubuntu/algopro` must remain completely untouched.

---

## 1. Configurable Session Schedule (IST)

```
09:00 IST ───► Pre-Market Preflight Check & Database Hydration
09:15 IST ───► NSE Cash Market Opens (Normal Trading Session)
09:15-09:30 ─► Opening Range Formation (ORB-15 High/Low established)
09:30-11:15 ─► Phase 1 Active Scanning (ORB-15 Breakouts & Volume Surges)
11:15-14:30 ─► Phase 2 Active Scanning (VWAP Mean Reversion & Support Bounces)
14:30 IST ───► Entry Cutoff Window (Zero new positions permitted)
15:15 IST ───► Mandatory Intraday Square-Off Trigger
15:20 IST ───► Hard Terminal Market Square-Off (All open paper orders cancelled)
15:30 IST ───► Cash Market Close & EOD Balance Sheet Reconciliation
15:35 IST ───► Generation of Daily Performance & Audit Report
```

### Detailed Phase Specifications:
1. **09:00 IST — Pre-Market Readiness Check:**
   - Verify SQLite WAL files (`data/forward_gateway.db`, `data/forward_reservations.db`).
   - Confirm virtual cash baseline (INR 100,000.00).
   - Verify that paper safety invariants are strictly asserted (`ALLOW_LIVE_TRADING=false`).
   - Probe market data endpoint availability.
2. **09:15 IST — Market Open & Indicator Warm-up:**
   - First 5-minute bar forms (09:15–09:20 IST).
   - No entries permitted until minimum 3 completed bars form (09:30 IST) to establish the Opening Range High and Low.
3. **09:30 to 14:30 IST — Periodic Scanning on Completed Bars:**
   - Scanner executes on every 5-minute bar completion.
   - Evaluates RVOL ($\ge 1.5$), ATR volatility expansion, and VWAP directional alignment.
   - If a valid setup triggers, emits `StrategySignal`.
4. **Portfolio Risk & Margin Gate:**
   - `CapitalReservationService.reserve_capital()` performs an atomic check.
   - If free capital is insufficient, signal is cleanly rejected (`ReservationOutcome.INSUFFICIENT_FUNDS`).
   - Sizing capped at $2.0\%$ portfolio risk and $\le 25\%$ single-stock capital allocation.
5. **14:30 IST — New Entry Cutoff:**
   - Runner stops generating new entry orders. Existing open positions continue trailing stops.
6. **15:15 to 15:20 IST — Intraday Square-Off:**
   - Any remaining open position is closed via market order.
   - Pending working orders are immediately cancelled.
7. **15:30 IST — End-of-Day Reconciliation:**
   - `EODReconciler` verifies double-entry balance:
     $$\text{Cash}_{\text{EOD}} = \text{Cash}_{\text{Start}} + \text{Net Realized P\&L}$$
   - Any discrepancy $> \text{INR } 0.00$ triggers a reconciliation alert.

---

## 2. Operational Commands & Real-Time Monitoring

### 2.1 Checking Runner Process Health
```bash
# Check running daemon status
ps aux | grep "run_forward_paper_trading.py" | grep -v grep

# Verify legacy daemon preservation (PID 3009443 must remain running)
ps aux | grep 3009443 | grep -v grep
```

### 2.2 Live Telemetry Inspection
```bash
# View continuous streaming log
tail -f /opt/ai-builder/workspaces/algolab-paper-trial-20261019-183015/logs/forward_daemon.log

# Inspect latest hot-state JSON snapshot
cat /opt/ai-builder/workspaces/algolab-paper-trial-20261019-183015/reports/forward_trading_live_snapshot.json | jq .
```

### 2.3 Single Cycle Verification Probe
```bash
# Run isolated probe without interrupting daemon
python3 /opt/ai-builder/workspaces/algolab-paper-trial-20261019-183015/scripts/run_forward_paper_trading.py --single-cycle
```

---

## 3. Emergency Stop & Fail-Closed Procedures

If an operational anomaly, data feed corruption, or unexpected market event occurs:
```bash
# 1. Send graceful SIGTERM to isolated forward paper daemon
kill -TERM $(pgrep -f "run_forward_paper_trading.py")

# 2. Verify all open positions are squared off in SQLite ledger
sqlite3 /opt/ai-builder/workspaces/algolab-paper-trial-20261019-183015/data/forward_gateway.db \
  "SELECT order_id, symbol, state, quantity, price FROM orders WHERE state NOT IN ('FILLED', 'CANCELLED');"

# 3. DO NOT signal PID 3009443 under any circumstance.
```
