# Release Manifest: Algo Lab Unified (v1.0.1)

**Release Identifier:** `algo-lab-unified-v1.0.1-paper-only`  
**Build Date:** 2026-10-19 10:42:00 IST  
**Task Identifier:** `algolab-unification-20261019-083200`  
**License:** Algo Lab Internal Proprietary Evaluation License (`REDISTRIBUTION STATUS: BLOCKED`)  
**Checksums File:** [`CHECKSUMS.sha256`](file:///opt/ai-builder/workspaces/algolab-unification-20261019-083200/algo-lab-unified-remediation/CHECKSUMS.sha256)

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
| `tests/unit/` | 5 suites | 36 automated unit tests (100% green pass rate) |

---

## 2. Hardcoded Safety Invariants

```python
ALLOW_LIVE_TRADING = False
ALLOW_REAL_BROKER_EXECUTION = False
REAL_MONEY_RISK_PAISE = 0
PAPER_BROKER_ONLY = True
```

---

## 3. Cryptographic Checksum Architecture

The cryptographic hash table [`CHECKSUMS.sha256`](file:///opt/ai-builder/workspaces/algolab-unification-20261019-083200/algo-lab-unified-remediation/CHECKSUMS.sha256) is generated deterministically from all Git-tracked release files.

- **Total Tracked Files in Repository:** 42 files.
- **Total Hashes in Checksums File:** 41 entries.
- **Sole Excluded File:** `CHECKSUMS.sha256` is mathematically excluded from its own digest list to prevent circular hashing.
- **Zero Runtime Debris:** All untracked logs (`*.log`), caches (`.pytest_cache/`, `__pycache__/`), and local databases are strictly excluded.
