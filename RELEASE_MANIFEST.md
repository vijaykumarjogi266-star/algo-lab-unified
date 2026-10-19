# Release Manifest: Algo Lab Unified (v1.0.0)

**Release Identifier:** `algo-lab-unified-v1.0.0-paper-only`  
**Build Date:** 2026-10-19 09:52:00 IST  
**Task Identifier:** `algolab-unification-20261019-083200`  
**License:** MIT (Copyright (c) 2026 Algo Lab Core Team)  
**Checksums File:** [`CHECKSUMS.sha256`](file:///opt/ai-builder/workspaces/algolab-unification-20261019-083200/algo-lab-unified/CHECKSUMS.sha256)

---

## 1. Release Inventory

| Subsystem / Path | Module Count | Responsibilities |
| :--- | :--- | :--- |
| `src/core/` | 4 modules | Invariants, exception taxonomy, path containment, structured logging |
| `src/brokergateway/` | 4 modules | OMS lifecycle, uncertainty state transitions, fill deduplication |
| `src/capitalreservation/` | 3 modules | Atomic SQLite `BEGIN EXCLUSIVE` margin locking & idempotency |
| `src/eodreconciler/` | 3 modules | 28-digit `Decimal` double-entry balance sheet conservation |
| `src/scanner/` | 2 modules | Technical intraday market scanner (RVOL $\ge 1.5$, ATR expansion) |
| `src/strategies/` | 3 modules | Decoupled intraday momentum breakout strategy with conservative SL/TP collision |
| `src/simulation/` | 3 modules | Event-driven historical replay engine & official NSE statutory fee schedule |
| `scripts/` | 3 scripts | Preflight safety auditor, safe rollback script, backtest runner |
| `tests/unit/` | 4 suites | 28 automated unit tests (100% green pass rate in 0.07s) |

---

## 2. Hardcoded Safety Invariants

```python
ALLOW_LIVE_TRADING = False
ALLOW_REAL_BROKER_EXECUTION = False
REAL_MONEY_RISK_PAISE = 0
PAPER_BROKER_ONLY = True
```

---

## 3. Cryptographic Checksum Sample

Please inspect [`CHECKSUMS.sha256`](file:///opt/ai-builder/workspaces/algolab-unification-20261019-083200/algo-lab-unified/CHECKSUMS.sha256) for the full 39-file cryptographic verification table.
