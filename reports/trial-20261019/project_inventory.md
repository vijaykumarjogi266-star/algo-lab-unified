# Algo Lab — Comprehensive Project Inventory & Provenance Report

**Task Identifier:** `algolab-paper-trial-20261019-183015`  
**Execution Timestamp:** 2026-10-19 18:41:00 IST (13:11:00 UTC)  
**Evaluated Repositories:**
1. Canonical AlgoPro: `/home/ubuntu/algopro`
2. Canonical All-in-One: `/home/ubuntu/all-in-one`
3. Canonical AlgoLab: `/home/ubuntu/algolab`
4. Unified Release Candidate: `/opt/ai-builder/workspaces/algolab-unification-20261019-083200/algo-lab-unified`
5. Isolated Trial Candidate: `/opt/ai-builder/workspaces/algolab-paper-trial-20261019-183015/algo-lab-unified-trial`

---

## 1. Executive Summary

This inventory documents the source commit IDs, dirty-worktree status, file provenance, report dates, and evidence freshness across all candidate repositories in the Algo Lab ecosystem. In strict adherence to institutional preservation invariants, canonical repositories ([`/home/ubuntu/algolab`](file:///home/ubuntu/algolab), [`/home/ubuntu/algopro`](file:///home/ubuntu/algopro), [`/home/ubuntu/all-in-one`](file:///home/ubuntu/all-in-one)) were accessed exclusively in a read-only manner. Zero files were modified, committed, or pushed to canonical trees, and legacy paper trading daemon PID `3009443` remained completely undisturbed.

The unified release candidate `algo-lab-unified` (`v1.0.2-paper-only` at commit `d775a57afc92a2143d7b86c3b3878e8dd324cc6b`) was selected as the authoritative codebase due to verified order state-machine remediation, hardcoded fail-closed execution guards, atomic capital reservation, and verified double-entry accounting reconciliation.

---

## 2. Canonical & Candidate Repository Provenance

| Repository Path | Branch | Commit SHA | Git Status / Dirty Tree | Report Dates & Provenance Freshness | Selection Verdict |
| :--- | :---: | :---: | :--- | :--- | :---: |
| [`/home/ubuntu/algopro`](file:///home/ubuntu/algopro) | `main` | `e7a02fa6e933c19d6bfa4c6a4cf30ede703ab67f` | 6 untracked preflight logs in `data/preflight/` | 2026-10-18 to 2026-10-19 08:30 IST; legacy monolithic gateway | **REJECTED** (Unremediated timeouts, PID 3009443 running) |
| [`/home/ubuntu/all-in-one`](file:///home/ubuntu/all-in-one) | `main` | `8a23393c36c091880ad38284ab8468072857482d` | 2 modified files (`agent_consensus_ledger.json`, `hot_state_live_snapshot.json`) | 2026-10-17 to 2026-10-18; fragmented scanner and in-memory reservations | **REJECTED** (Missing double-entry reconciler, timeout race defects) |
| [`/home/ubuntu/algolab`](file:///home/ubuntu/algolab) | `main` | `2389387ea351c733909f39245adbeca0c935a12a` | 1 modified file (`data/live_portfolio_state.json`) | 2026-09-23 to 2026-10-10; unintegrated staging branches | **REJECTED** (Lacks unified state machine and safety guards) |
| [`algo-lab-unified`](file:///opt/ai-builder/workspaces/algolab-unification-20261019-083200/algo-lab-unified) | `main` | `d775a57afc92a2143d7b86c3b3878e8dd324cc6b` | Clean (`v1.0.2-paper-only` tag) | 2026-10-19 12:30 IST; verified with 40 unit tests & 4 stress scenarios | **BASE SOURCE** |
| [`algo-lab-unified-trial`](file:///opt/ai-builder/workspaces/algolab-paper-trial-20261019-183015/algo-lab-unified-trial) | `main` | `d775a57afc92a2143d7b86c3b3878e8dd324cc6b` | Remediation patch applied to `engine.py` (Short Cash Accounting) | Freshly cloned & verified in isolated workspace `algolab-paper-trial-20261019-183015` | **SELECTED & QUALIFIED** |

---

## 3. Subsystem Inventory and Best-Supported Component Selection

| Subsystem Domain | Existing Implementations Found | Selected Best-Supported Implementation | Selection Rationale |
| :--- | :--- | :--- | :--- |
| **Market Data Ingestion** | Kite API (`BatchQuoteProvider`), Synthetic Replay, Yahoo Finance API (`yf.download`) | **`yf.download` (5m bars) + Replay Engine** | Kite API daily token expired (`403 PermissionException`). Yahoo Finance provides real, completed 5m OHLCV bars without authentication failure. |
| **Stock Scanner** | `IntradayMarketScanner` (`all-in-one`), `run_live_intraday_scanner.py`, `master_strategy_screener.py` | **`IntradayMarketScanner` (`src/scanner/scanner.py`)** | Strict decoupling, 20-bar warmup, RVOL >= 1.5, ATR filter, spread check (< 10 bps). Modular and tested in isolation. |
| **Strategy Logic** | ORB, VWAP Reversion, Pivot R1, Bollinger, SMACross, MomentumBreakout | **`MomentumBreakoutStrategy` (`src/strategies/`) & ORB-15 / VWAP Reversion** | 2:1 RR dynamic brackets, clean signal emitting without broker side-effects, verified in unit tests. |
| **Order Gateway & OMS** | Legacy OMS (`algolab`, `algopro`), Remediated Gateway (`algo-lab-unified`) | **`BrokerGateway` (`src/brokergateway/gateway.py`)** | Remediated state machine handling `CANCEL_UNKNOWN -> OPEN/PARTIALLY_FILLED/FILLED`, terminal state immutability, late fill absorption. |
| **Capital & Margin Guard** | Flat checks, in-memory allocations, atomic SQLite WAL reservations | **`CapitalReservationService` (`src/capitalreservation/service.py`)** | Atomic SQLite WAL reservations, verified under 10-thread concurrency contention, prevents margin over-allocation. |
| **Safety Invariants** | Config flags, soft checks | **`OrderDispatchSafetyGuard` & `safety_invariants.py`** | Hardcoded fail-closed denial of live broker venues (`guard_broker_dispatch`), AST import block for prohibited broker SDKs (`kiteconnect`, etc.). |
| **Ledger & Accounting** | JSON dumps, manual balance calculations | **`EODReconciler` (`src/eodreconciler/reconciler.py`)** | Double-entry conservation check: $\text{Cash} + \text{Margin} + \text{Net P&L} = \text{Balance}$. Reconciles session down to ₹0.00 error. |
| **Friction & Slippage** | Flat brokerage, estimated % | **`NSEFrictionModel` (`src/simulation/fee_model.py`)** | Explicit statutory NSE breakdown (Brokerage, STT, Exchange turnover, SEBI turnover, Stamp duty, 18% GST) + 2.0 bps slippage. |

---

## 4. Preservation & Safety Controls Audit

1. **Safety Flags Hardcoded:**
   - `ALLOW_LIVE_TRADING = False`
   - `ALLOW_REAL_BROKER_EXECUTION = False`
   - `REAL_MONEY_RISK_PAISE = 0`
   - `PAPER_BROKER_ONLY = True`
2. **Prohibited SDKs:** `kiteconnect`, `smartapi`, `upstox_client`, `fyers_api`, `angelone`, `alice_blue`, `dhan` blocked at runtime.
3. **Execution Venues:** Only `PAPER`, `SIMULATED`, `VIRTUAL`, `MOCK`, `BACKTEST` permitted. Any attempt to pass `ZERODHA` or `NSE_LIVE` triggers immediate `BrokerExecutionBlockedError`.
4. **Canonical Preservation:** Repositories `/home/ubuntu/algolab`, `/home/ubuntu/algopro`, and `/home/ubuntu/all-in-one` remain 100% unaltered.
5. **Background Paper Daemon (PID 3009443):** Maintained uninterrupted execution (>74 hours continuous uptime) in `/home/ubuntu/algopro`.
