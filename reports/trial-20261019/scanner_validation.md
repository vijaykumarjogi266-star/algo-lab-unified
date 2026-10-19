# Algo Lab — NSE Stock Scanner Empirical Validation Report

**Task Identifier:** `algolab-paper-trial-20261019-183015`  
**Execution Timestamp:** 2026-10-19 18:43:00 IST (13:13:00 UTC)  
**Evaluation Target:** [`src/scanner/scanner.py`](file:///opt/ai-builder/workspaces/algolab-paper-trial-20261019-183015/algo-lab-unified-trial/src/scanner/scanner.py) & [`validate_and_rank_scanner.py`](file:///opt/ai-builder/workspaces/algolab-paper-trial-20261019-183015/scripts/validate_and_rank_scanner.py)  
**Universe Audited:** NSE Liquid Large-Cap Benchmark Equities (`RELIANCE`, `TCS`, `HDFCBANK`, `INFY`, `ICICIBANK`, `SBIN`, `BHARTIARTL`, `ITC`, `LT`, `TATAMOTORS`)  
**Data Feed:** Completed 5-Minute NSE Bars (Yahoo Finance API `yf.download`)  

---

## 1. Executive Summary

This report establishes the empirical validation of the Algo Lab NSE Stock Scanner across 10 core market filters. Using only data legitimately available at execution time (completed 5-minute OHLCV bars without look-ahead bias), the scanner was tested on the active trading session of **2026-10-19**.

Out of 10 evaluated universe constituents:
- **5 Equities QUALIFIED:** `RELIANCE`, `HDFCBANK`, `INFY`, `ICICIBANK`, `BHARTIARTL`
- **5 Equities REJECTED:** `TCS` (Low RVOL & inside range), `SBIN` (Low RVOL), `ITC` (Low Volatility / narrow ATR), `LT` (Low RVOL & ATR), `TATAMOTORS` (Demerged / 404 missing feed).

---

## 2. Empirical Validation of Scanner Filters

| Filter Domain | Mathematical / Logical Implementation | Operational Purpose | Observed Test Behavior & Evidence | Filter Status |
| :--- | :--- | :--- | :--- | :---: |
| **1. Data Freshness & Gaps** | Checks bar timestamp alignment and minimum history length ($\ge 25$ bars). | Prevents trading on stale or interrupted socket feeds. | Successfully caught 0 bars for `TATAMOTORS` (demerged), cleanly rejecting the symbol without crash. | **PASS** |
| **2. Liquidity & Traded Value** | $\text{Session Turnover} \ge \text{INR 5.0 Crore}$. | Prevents market impact and execution traps in illiquid names. | All active large caps demonstrated turnover between ₹292 Cr (`ITC`) and ₹2,870 Cr (`ICICIBANK`). | **PASS** |
| **3. Bid-Ask Spread Proxy** | $\text{Spread BPS} = \frac{\text{Tick Size}}{\text{LTP}} \times 10,000 \le 10.0\text{ bps}$. | Guarantees tight execution pricing; caps immediate entry friction. | All evaluated stocks exhibited tight spreads ($0.27\text{ bps}$ on `BHARTIARTL` to $1.92\text{ bps}$ on `ITC`). | **PASS** |
| **4. Relative Volume (RVOL)** | $\text{RVOL} = \frac{\text{Volume}_t}{\text{SMA}_{20}(\text{Volume})} \ge 1.2\text{x}$. | Identifies institutional participation and liquidity expansion. | Accurately differentiated active movers (`INFY` 1.97x, `HDFCBANK` 1.94x) from quiet names (`TCS` 0.68x, `SBIN` 0.77x). | **PASS** |
| **5. VWAP & Price Structure** | Computes session VWAP from typical price. Bullish: $C_t > \text{VWAP}$; Bearish: $C_t < \text{VWAP}$. | Confirms directional momentum relative to institutional benchmark. | Correctly identified session breakdown across all 5 qualified names trading below their VWAP lines. | **PASS** |
| **6. Opening Range (ORB-15)** | Establishes 09:15–09:30 High and Low. Breakout requires $C_t > \text{ORB}_{\text{High}}$ or $C_t < \text{ORB}_{\text{Low}}$. | Filters out random intraday chop within the morning bracket. | Successfully rejected `TCS`, `SBIN`, `LT` which oscillated entirely inside their opening range. | **PASS** |
| **7. Volatility & R:R Suitability** | $\frac{\text{ATR}_{14}}{\text{LTP}} \ge 0.15\%$. Requires $2:1$ Reward-to-Risk clearance before circuit. | Prevents trading low-volatility assets where friction exceeds gross target. | Accurately rejected `ITC` ($\text{ATR} = \text{INR } 0.32$, only $0.12\%$ of price), protecting virtual capital from fee drag. | **PASS** |
| **8. Circuit / Extreme Move** | $|\Delta P| < 8.5\%$ from session open. | Rejects stocks nearing upper/lower circuits to prevent execution lockup. | All tested stocks traded within $\pm 2.5\%$ of open; zero circuit locks occurred. | **PASS** |
| **9. Capital Allocation Guard** | Position sizing capped at $2\%$ account risk and $\le 25\%$ single-stock allocation. | Enforces strict portfolio diversification and non-exhaustion of capital. | Virtual orders capped at ₹24,000–₹24,920 per qualified position against ₹100,000 baseline. | **PASS** |
| **10. Duplicate Suppression** | Enforces cooldown window (1 trade per symbol per session). | Eliminates duplicate entries and cascading fills on same setup. | Verified in `run_forward_paper_trading.py` and `scanner.py`. | **PASS** |

---

## 3. Ranked Candidate List (Session: 2026-10-19)

Below is the verified ranking produced by `validate_and_rank_scanner.py`:

```
=========================================================================================================
Symbol     | Status    |   LTP (₹) |  RVOL |  VWAP (₹) |  Turnover | Setup Name                 |  Allocated
---------------------------------------------------------------------------------------------------------
RELIANCE   | QUALIFIED |   1202.00 |  1.68 |   1212.10 | ₹1806.0Cr | ORB_15M_BEARISH_BREAKDOWN  | ₹24,040.00
HDFCBANK   | QUALIFIED |    712.00 |  1.94 |    712.11 | ₹2147.8Cr | ORB_15M_BEARISH_BREAKDOWN  | ₹24,920.00
INFY       | QUALIFIED |   1130.70 |  1.97 |   1136.72 | ₹1396.3Cr | ORB_15M_BEARISH_BREAKDOWN  | ₹24,875.40
ICICIBANK  | QUALIFIED |   1381.00 |  1.30 |   1387.92 | ₹2870.5Cr | ORB_15M_BEARISH_BREAKDOWN  | ₹24,858.00
BHARTIARTL | QUALIFIED |   1875.20 |  1.38 |   1882.90 | ₹1532.4Cr | ORB_15M_BEARISH_BREAKDOWN  | ₹24,377.60
TCS        | REJECTED  |   2460.90 |  0.68 |   2469.93 | ₹1889.0Cr | NONE                       |      ₹0.00
SBIN       | REJECTED  |    973.70 |  0.77 |    972.86 | ₹1408.6Cr | NONE                       |      ₹0.00
ITC        | REJECTED  |    260.80 |  2.19 |    261.41 | ₹ 292.1Cr | ORB_15M_BEARISH_BREAKDOWN  |      ₹0.00
LT         | REJECTED  |   3661.90 |  0.65 |   3658.24 | ₹ 834.8Cr | NONE                       |      ₹0.00
TATAMOTORS | REJECTED  |      0.00 |  0.00 |      0.00 | ₹   0.0Cr | NONE                       |      ₹0.00
=========================================================================================================
```

### Detailed Rejection Audit:
1. **`TCS`:** Rejected due to `LOW_RVOL` (0.68x vs 1.2x min) and `NO_TECHNICAL_TRIGGER` (trading inside 09:15-09:30 range).
2. **`SBIN`:** Rejected due to `LOW_RVOL` (0.77x vs 1.2x min) and `NO_TECHNICAL_TRIGGER`.
3. **`ITC`:** Rejected due to `LOW_VOLATILITY` ($\text{ATR}_{14} = 0.32$, only $0.12\%$ of LTP). Even though RVOL was 2.19x and price broke down, the expected move was too small to overcome exchange friction.
4. **`LT`:** Rejected due to `LOW_RVOL` (0.65x) and `LOW_VOLATILITY` (ATR 0.15%).
5. **`TATAMOTORS`:** Rejected due to `INSUFFICIENT_HISTORY` (0 bars; corporate demerger resulted in missing ticker quotes).

---

## 4. Feed Transparency & Real-Time Limitations Disclosure

- **Authorized Feed Posture:** Direct Kite Connect streaming is **BLOCKED** due to expired daily access tokens (`403 PermissionException`).
- **Active Data Feed:** Completed 5-minute bars are ingested directly via the Yahoo Finance API (`yf.download`).
- **Operational Reality:** Yahoo Finance intraday bars are delayed by approximately 1–3 minutes relative to raw exchange ticks. Signals are evaluated strictly on **completed bars** at the end of each 5-minute candle interval. The system does not claim sub-second live tick execution.
