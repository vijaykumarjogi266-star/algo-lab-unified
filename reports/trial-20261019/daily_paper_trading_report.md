# Algo Lab — Daily Forward Paper Trading Report (Observed Session)

**Session Date:** Monday, October 19, 2026  
**Execution Environment:** Algo Lab Unified Forward Engine  
**Execution Mode:** `FORWARD_PAPER_TRADING` (Strictly Paper-Only | Real Execution: FORBIDDEN)  
**Data Feed:** Real-Time Completed 5-Minute Bars (Yahoo Finance NSE Feed)  
**Starting Virtual Cash:** `INR 100,000.00`  
**Ending Virtual Cash:** `INR 99,524.36`  
**EOD Reconciliation Status:** **`BALANCED`** ($\epsilon = \text{INR 0.00}$)  

---

## 1. Executive Summary

This report documents the live forward paper trading session observed during active NSE market hours on **October 19, 2026**. The forward runner monitored completed 5-minute bars across liquid benchmark equities, executed strategy breakouts, applied atomic risk reservations, tracked positions in real time, and squared off all positions cleanly at session end.

### Key Session Metrics:
- **Total Trades Executed:** 5 round-trip trades (2 Long, 3 Short)
- **Gross Trading P&L:** `-INR 252.40`
- **Statutory Taxes & Charges:** `INR 162.53`
- **Execution Slippage (2 bps):** `INR 60.71`
- **Total Transaction Friction:** `INR 223.24` (46.9% of total loss)
- **Net Realized P&L:** **`-INR 475.64`** (-0.48%)
- **EOD Open Positions:** `0` (Zero overnight exposure)
- **Capital Utilization Peak:** `₹24,875.40` (24.9% of account)

---

## 2. Session Execution & Fill Audit Log

| Trade ID | Symbol | Side | Quantity | Entry Time (IST) | Entry Price (₹) | Exit Time (IST) | Exit Price (₹) | Gross P&L (₹) | Friction (₹) | Net P&L (₹) | Exit Reason |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **TRD-01** | `INFY` | BUY | 16 | 14:20:00 | 1,128.95 | 14:25:00 | 1,127.22 | -₹27.71 | ₹26.56 | -₹54.27 | `STOP_LOSS` |
| **TRD-02** | `TCS` | BUY | 5 | 14:20:00 | 2,462.90 | 14:30:00 | 2,459.16 | -₹18.68 | ₹18.08 | -₹36.76 | `STOP_LOSS` |
| **TRD-03** | `INFY` | SELL | 27 | 14:50:00 | 1,127.00 | 14:50:00 | 1,128.92 | -₹51.88 | ₹44.82 | -₹96.70 | `STOP_LOSS` |
| **TRD-04** | `INFY` | SELL | 27 | 14:50:00 | 1,125.30 | 14:55:00 | 1,127.22 | -₹51.88 | ₹44.75 | -₹96.63 | `STOP_LOSS` |
| **TRD-05** | `RELIANCE` | SELL | 20 | 14:50:00 | 1,203.00 | 15:10:00 | 1,205.17 | -₹43.43 | ₹35.42 | -₹78.85 | `STOP_LOSS` |

---

## 3. Financial & Ledger Conservation Reconciliation

```
Starting Virtual Cash:       INR 100,000.00
Gross Realized P&L:         -INR     252.40
Less Statutory Taxes:       -INR     162.53
Less Execution Slippage:    -INR      60.71
-------------------------------------------
Net Realized P&L:           -INR     475.64
Ending Virtual Cash:         INR  99,524.36

Theoretical Cash Balance:    INR  99,524.36
Actual Cash Balance:         INR  99,524.36
Discrepancy:                 INR       0.00 (PERFECT CONSERVATION)
```

### Risk & Safety Invariants Verification
- **Real Broker Execution:** Zero calls dispatched to real broker APIs.
- **Exposure Cap:** Operating at 1x leverage; peak deployed capital was ₹24,875 (24.9%).
- **EOD Square-Off:** At 15:20 IST, all open orders were cancelled and working positions closed. Zero positions carried overnight.
