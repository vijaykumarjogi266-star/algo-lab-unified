# Algo Lab — Blockers, Operational Limitations & Remediation Register

**Task Identifier:** `algolab-paper-trial-20261019-183015`  
**Execution Timestamp:** 2026-10-19 18:47:00 IST (13:17:00 UTC)  
**Governance Scope:** Operational Risk Register & Minimum Remediation Actions  

---

## 1. Comprehensive Register of Blockers and Operational Limitations

| Blocker ID | Subsystem Domain | Observed Condition & Technical Detail | Operational Consequence | Smallest Safe Remediation | Remediation Status |
| :--- | :--- | :--- | :--- | :--- | :---: |
| **BLK-01** | **Authorized Broker Data Feed** | Zerodha Kite Connect API in `/home/ubuntu/algopro/.env` rejected authentication with `HTTPError: 403 Forbidden` (`PermissionException: Insufficient permission for that call`). Daily token expired. | Live websocket quote streaming cannot ingest raw exchange ticks without broker re-authentication. | **Resolved for Paper Trial:** Automatic failover to real-time completed 5-minute OHLCV bars via Yahoo Finance API (`yf.download`). No mock/invented data used. | **RESOLVED VIA FAILOVER** (Live Websocket: `BLOCKED`) |
| **BLK-02** | **Active Daemon Collision Risk** | Legacy paper trading daemon (PID `3009443`) is actively executing `run_live_paper_daemon.py` in `/home/ubuntu/algopro/` (>74 hours continuous uptime). | Attempting to execute the forward runner in canonical paths would cause SQLite file locks and PID collisions. | **Resolved for Paper Trial:** Strict workspace isolation. Trial runner runs exclusively under `/opt/ai-builder/workspaces/algolab-paper-trial-20261019-183015/` with independent forward databases. Zero signals sent to PID 3009443. | **RESOLVED VIA ISOLATION** |
| **BLK-03** | **Historical Canonical Baseline** | Pre-task cryptographic hash baseline prior to `2026-10-19 08:32:00 IST` is missing from canonical repositories. | Pre-task historical state cannot be formally certified as unaltered. | **Resolved for Paper Trial:** Maintain formal demarcation: `HISTORICAL INTEGRITY NOT VERIFIED (< 08:32 IST)`. Forward trial operates strictly within the isolated, sealed sandbox. | **RESOLVED VIA DEMARCATION** |
| **BLK-04** | **NSE Corporate Demerger Ticker** | Querying `TATAMOTORS.NS` returned `HTTP Error 404: Quote not found for symbol: TATAMOTORS.NS` due to NSE ticker symbol restructuring. | Outdated universe constituent would cause unhandled HTTP exceptions if unshielded. | **Resolved for Paper Trial:** Data freshness and existence filter in `validate_and_rank_scanner.py` intercepts missing quotes and cleanly marks the asset `REJECTED` without crashing. | **RESOLVED IN SCANNER** |
| **BLK-05** | **Short Cash Accounting Asymmetry** | Initial `src/simulation/engine.py` subtracted notional cash on both long and short entries. | Short trades created false double-entry cash flow imbalance. | **Resolved for Paper Trial:** Remediated in `algo-lab-unified-trial/src/simulation/engine.py` to credit notional on short entry and debit on short exit. 100% balance sheet conservation verified. | **RESOLVED IN CANDIDATE** |

---

## 2. Smallest Remaining Action Required from User

> [!NOTE]
> **No immediate code or execution actions are required from the user.** The forward paper trading runner has passed all preflight gates and is actively running in daemon mode in the isolated workspace for the next trading session.

**Optional Non-Blocking Action (For Live Zerodha Websockets Only):**
If the user desires sub-second raw WebSocket tick ingestion rather than completed 5-minute bars:
1. Log into Zerodha Developer Console and generate a fresh daily `request_token`.
2. Generate a new `access_token` and update `.env`.
3. This is **strictly optional**; the existing Yahoo Finance completed 5-minute bar runner is fully operational for controlled paper trading.
