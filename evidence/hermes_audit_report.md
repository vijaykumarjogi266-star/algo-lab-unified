# Hermes Algo Lab Verification & Monitoring Report
**Generated:** 2026-10-19T17:20:05.584081+00:00  
**Overall Status:** **`VERIFICATION_FAILED`**  
**Promotion Decision:** **`HOLD (PROMOTION NOT AUTHORIZED)`**  

---
## 1. Canonical Repository Integrity

### Repository: `all-in-one`
- **Path:** `/home/ubuntu/all-in-one`
- **Git Root:** `/home/ubuntu/all-in-one`
- **Branch:** `main`
- **HEAD Commit:** `8a23393c36c091880ad38284ab8468072857482d`
- **Commit Drift:** `NONE`
- **Working Tree Status:** 6 changed items
```text
 M data/decisions/agent_consensus_ledger.json
 M data/reports/hot_state_live_snapshot.json
?? data/intraday_ledger.db-shm
?? data/intraday_ledger.db-wal
?? data/preflight/
?? data/reports/live_execution_stream.jsonl
```

### Repository: `algolab`
- **Path:** `/home/ubuntu/algolab`
- **Git Root:** `/home/ubuntu/algolab`
- **Branch:** `main`
- **HEAD Commit:** `2389387ea351c733909f39245adbeca0c935a12a`
- **Commit Drift:** `NONE`
- **Working Tree Status:** 1 changed items
```text
 M data/live_portfolio_state.json
```

---
## 2. Candidate Patch & Isolation

*(No candidate patch evaluated)*

---
## 3. Test Evidence & Safety Matrix

| Safety Domain | Result | Test Entry Point | Timestamp | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `paper_only_enforcement` | **NOT_TESTED** | `N/A` | N/A | No test evidence provided in manifest |
| `broker_path_denial` | **NOT_TESTED** | `N/A` | N/A | No test evidence provided in manifest |
| `order_lifecycle` | **NOT_TESTED** | `N/A` | N/A | No test evidence provided in manifest |
| `partial_and_late_fills` | **NOT_TESTED** | `N/A` | N/A | No test evidence provided in manifest |
| `cancellation_uncertainty` | **NOT_TESTED** | `N/A` | N/A | No test evidence provided in manifest |
| `idempotency` | **NOT_TESTED** | `N/A` | N/A | No test evidence provided in manifest |
| `crash_recovery` | **NOT_TESTED** | `N/A` | N/A | No test evidence provided in manifest |
| `concurrent_risk_reservations` | **NOT_TESTED** | `N/A` | N/A | No test evidence provided in manifest |
| `reconciliation` | **NOT_TESTED** | `N/A` | N/A | No test evidence provided in manifest |
| `pnl_statutory_truth` | **NOT_TESTED** | `N/A` | N/A | No test evidence provided in manifest |
| `access_control_rbac` | **NOT_TESTED** | `N/A` | N/A | No test evidence provided in manifest |
| `regression_unit_suite` | **NOT_TESTED** | `N/A` | N/A | No test evidence provided in manifest |

---
## 4. Paper-Only Safety Proof

*(No manifest provided)*

---
## 5. Identified Safety Gaps & Blockers

- ❌ **Gap:** Repo all-in-one: Working tree status drift! Expected 4 entries, found 6
- ❌ **Gap:** Repo algolab: Working tree status drift! Expected 0 entries, found 1
- ❌ **Gap:** 12 mandatory safety domains are NOT TESTED!

---
## 6. Action Required
Human Review Required. Do NOT promote automatically.

> [!IMPORTANT]
> All modifications must remain unpromoted until formal human sign-off is completed.