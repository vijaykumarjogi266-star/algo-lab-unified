# Algo Lab Unified (v1.0.0)

**High-Fidelity Intraday Paper-Trading & Quantitative Research Operating System for Indian Markets**

---

## 1. Project Mission & Safety Invariants

Algo Lab Unified consolidates the verified production components from **AlgoPro** and **All-in-One** into a clean, modular, paper-trading system designed specifically for the National Stock Exchange of India (NSE).

### Non-Negotiable Paper-Only Safety Invariants
The system is hard-locked in simulation mode through multi-layer software and AST gatekeepers:
```python
ALLOW_LIVE_TRADING = False
ALLOW_REAL_BROKER_EXECUTION = False
REAL_MONEY_RISK_PAISE = 0
PAPER_BROKER_ONLY = True
```
- **AST Prohibited Imports:** Direct imports of broker SDKs (`kiteconnect`, `smartapi`, `upstox_client`, `fyers_api`, etc.) are actively detected and blocked by compilation gatekeepers.
- **Fail-Closed Design:** Any missing, ambiguous, or contradictory configuration immediately raises an unhandled `SecurityInvariantViolation`.
- **Path Containment:** File I/O operations are strictly confined within the workspace boundary; attempts to access canonical host directories trigger `WorkspaceIsolationViolation`.

---

## 2. Architecture & Subsystems

```text
algo-lab-unified/
├── src/
│   ├── core/                  # AST security filters, path assertion guards, structured logging
│   ├── brokergateway/         # OMS lifecycle, SUBMISSION_UNKNOWN recovery, fill deduplication
│   ├── capitalreservation/    # Atomic SQLite BEGIN EXCLUSIVE margin allocation & idempotency
│   ├── eodreconciler/         # Double-entry balance sheet conservation engine (28-digit Decimal)
│   ├── scanner/               # Technical market scanner (RVOL >= 1.5, ATR expansion, spread filter)
│   ├── strategies/            # Decoupled momentum breakout strategy with conservative SL/TP collision
│   └── simulation/            # Event-driven historical replay engine & official NSE statutory fees
├── config/                    # Static market hours, statutory fee rates, risk limits
├── data/                      # Local ephemeral SQLite fixtures (git-ignored)
├── logs/                      # Local audit logs (git-ignored)
├── scripts/                   # Operational tooling (preflight_check.py, rollback.sh, run_offline_backtest.py)
├── tests/                     # Isolated offline test suites (unit & integration)
├── requirements.lock          # Baseline 64-package pinned manifest
└── pyproject.toml             # Standard Python packaging specification
```

---

## 3. Operational Runbooks

### 3.1 Pre-Session Preflight Verification
Before opening any paper-trading session, execute the preflight safety audit:
```bash
python3 scripts/preflight_check.py
```
**Expected Output:**
```text
=================================================================
  ALGO LAB UNIFIED: PREFLIGHT SAFETY & INVARIANT AUDIT
=================================================================
  [✓] All 4 paper-only invariants verified (PASS)
  [✓] Zero .db or active .env files in workspace (PASS)
  [✓] AST scan: Zero prohibited broker SDKs in src/ (PASS)

[✓] PREFLIGHT AUDIT PASSED — SYSTEM CERTIFIED FOR PAPER OPERATION.
```

### 3.2 Running the Offline Unit Test Suite
To verify state transitions, atomic margin locks, and accounting equations:
```bash
PYTHONPATH=. pytest tests/unit/ -v --tb=short
```
*Expected: 28 tests collected, 28 passed in < 0.10s.*

### 3.3 Running the Offline Historical Replay Simulation
To execute a 60-bar replay across liquid NSE equities with full statutory fee modeling:
```bash
python3 scripts/run_offline_backtest.py
```
*Outputs JSON metrics to `reports/phase-8/backtest_metrics.json`.*

### 3.4 Safe Rollback Procedure
If workspace state needs to be completely reset to clean baseline:
```bash
./scripts/rollback.sh
```
*Validates directory path, archives existing artifacts to `evidence/`, and cleans ephemeral state.*

---

## 4. Double-Entry Accounting Law

At session close, `EODReconciler` verifies the fundamental conservation identity:

$$\mathbf{Ending\ Cash} + \sum \mathbf{Position\ Cost\ Basis} + \mathbf{Total\ Friction} \equiv \mathbf{Initial\ Capital} + \mathbf{Gross\ Realized\ P\&L}$$

Where:
- **Total Friction:** Brokerage (capped at ₹20) + STT (0.025% on Sell) + Exchange turnover (0.00345%) + SEBI fees (0.0001%) + Stamp duty (0.003% on Buy) + 18% GST + Slippage (2.0 bps).
- **Tolerance:** $\epsilon \le 0.01\text{ INR}$. Imbalances trigger session audit failures and halt subsequent startup.

---

## 5. Licensing & Provenance

- **Authors:** Algo Lab Core Team
- **License:** MIT License (see [`LICENSE`](file:///opt/ai-builder/workspaces/algolab-unification-20261019-083200/algo-lab-unified/LICENSE))
- **IP Clearance:** All components originate from internal Algo Lab repositories with zero third-party copyleft restrictions.
