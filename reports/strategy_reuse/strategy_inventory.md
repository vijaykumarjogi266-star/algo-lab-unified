# Algo Lab — Quantitative Strategy Inventory and Integration Matrix

**Document ID:** `ALGO-LAB-STRAT-INVENTORY-20261019`  
**Workspace:** `/opt/ai-builder/workspaces/algolab-strategy-reuse-20261019-210500/algo-lab-unified/`  
**Branch:** `integration/strategy-reuse-and-unification-20261019`  
**Base Commit:** `0e9d2aab4357646f8731358cc74e2ce1212a5eea`  
**Status:** COMPLETE AUDIT INVENTORY (PHASE 1)  

---

## 1. Executive Summary

This inventory audits all candidate quantitative strategies across the three historical codebases:
1. `vijaykumarjogi266-star/algopro` (`/home/ubuntu/algopro`, commit `e7a02fa6...`)
2. `vijaykumarjogi266-star/all-in-one` (`/home/ubuntu/all-in-one`, commit `8a23393c...`)
3. `algo-lab` and `algo-lab-unified` (`/home/ubuntu/algolab`, `/opt/ai-builder/workspaces/algolab-hardening-20261019-204100/algo-lab-unified/`, commit `0e9d2aab...`)

Each strategy has been inspected for indicator formulation, parameter boundaries, timing rules, known defects, and duplicate implementations.

---

## 2. Strategy Inventory Matrix

| Strategy ID | Name | Source Repo & File | Direction | Key Indicators | Default Parameters | Key Defect / Limitation | Proposed Canonical Action |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`STRAT_SMA_CROSS`** | SMA Crossover | `algopro/quant/strategies/sma_cross.py` | Long | SMA(9), SMA(21) | `fast=9, slow=21, sl=3%` | Static 3% stop without volatility adaptation. | Adopt with parameter validation (`fast < slow`) and ATR stops. |
| **`STRAT_VWAP_REVERSION`** | VWAP Mean Reversion | `algopro/quant/strategies/vwap_reversion.py` | Long | VWAP, Bands (2σ), ATR | `std=2.0, sl_atr=1.5` | Hardcoded ATR fallback `5.0`; session reset bugs across midnight. | Reset VWAP at Asia/Kolkata session open; calculate real ATR. |
| **`STRAT_ORB`** | Opening Range Breakout | `algopro/quant/strategies/orb.py` | Long/Short | High/Low 15m, RVOL, ATR | `range_bars=3, vol_mult=1.2` | Cutoff check was empty comment; marks daily trade consumed on signal. | Enforce Asia/Kolkata 14:45 cutoff; decouple candidate from fill. |
| **`STRAT_DONCHIAN_BREAKOUT`**| Donchian 20D Breakout | `algopro/quant/strategies/donchian_breakout.py` | Long | Donchian 20 High, ATR | `period=20, sl_atr=1.5, tp_atr=3.0` | Uses slice that could look ahead if index includes current bar. | Strictly lag channel calculations (`highs[:-1]`); intraday vs swing IDs. |
| **`STRAT_BOLLINGER_REVERSION`**| Bollinger Bands Reversion| `algopro/quant/strategies/bollinger_reversion.py`| Long | SMA 20, BB (2.0σ), ATR | `period=20, num_std=2.0, sl=1.5` | Current bar included in rolling window calculation. | Exclude current bar from band calculation; enforce parameter overrides. |
| **`STRAT_TTM_SQUEEZE`** | TTM Volatility Squeeze | `algopro/quant/strategies/volatility_squeeze.py` | Long/Short | BB (20, 2σ), KC (20, 1.5 ATR), Mom | `bb=20, std=2.0, kc=1.5, vol=1.15`| Runtime parameter overrides ignored (`self` references instead of `params`).| Fix parameter override bindings and enforce strict lagged bar state. |
| **`STRAT_EMA_PULLBACK`** | EMA 9/21 Pullback Swing | `algolab/scripts/run_swing_trading_strategies_backtest.py`| Long | EMA 9, EMA 21, SMA 200, ATR | `ema_fast=9, ema_slow=21, sl=2.0` | Monolithic script code without standalone strategy class. | Extract and formalize into canonical `EMAPullbackStrategy` class. |
| **`STRAT_MOMENTUM_BREAKOUT_V1`**| Intraday Momentum Breakout | `unified_base/src/strategies/momentum_breakout.py`| Long/Short | High/Low 20, RVOL 20, ATR | `rr=2.0, atr_mult=1.0` | Hardened in base commit `0e9d2aa`; benchmark breakout. | Preserve as unified core benchmark strategy. |
| **`STRAT_OMS_MEAN_REV`** | OMS Mean Reversion | `all-in-one/oms_project/strategy_factory.py` | Long/Short | SMA, StdDev | `window=20, threshold=1.5` | Emitted raw `OrderIntent`, bypassing capital reservations. | Adapt to canonical `StrategySignal` interface. |
| **`STRAT_OMS_TREND_FOLLOWING`**| OMS Trend Following | `all-in-one/oms_project/strategy_factory.py` | Long | Dual SMA | `short=5, long=20` | Emitted raw `OrderIntent`; duplicate of `SMACross`. | Adapt to canonical `StrategySignal` interface. |
| **`STRAT_OMS_HYBRID_ALPHA`** | OMS Hybrid Alpha | `all-in-one/oms_project/strategy_factory.py` | Long/Short | Realized Vol, SMA, BB | `vol_window=10, vol_thresh=0.01` | Emitted raw `OrderIntent`; duplicate regime logic. | Adapt as canonical regime-switching meta-strategy. |
| **`STRAT_BUY_AND_HOLD`** | Buy & Hold Benchmark | `algopro/quant/strategies/buy_and_hold.py` | Long | None (Buy on bar 0) | N/A | Non-intraday; baseline buy & hold only. | Preserve as registry benchmark baseline. |

---

## 3. Duplicate and Conflicting Implementations Analysis

1. **Mean Reversion Duplication:**
   - `algopro/quant/strategies/bollinger_reversion.py` vs `all-in-one/oms_project/strategies/mean_reversion/mean_reversion_strat.py`.
   - **Resolution:** Canonical implementation is `BollingerReversionStrategy` (with standard SMA/BB indicators) + adapter for OMS config schemas.
2. **Trend Following / Moving Average Duplication:**
   - `algopro/quant/strategies/sma_cross.py` vs `all-in-one/oms_project/strategies/trend_following/trend_following_strat.py`.
   - **Resolution:** Canonical implementation is `SMACrossoverStrategy` with configurable fast/slow periods and trailing stops.
3. **Breakout Strategy Redundancy:**
   - `unified_base/src/strategies/momentum_breakout.py` vs `algopro/quant/strategies/donchian_breakout.py`.
   - **Resolution:** `MomentumBreakoutStrategy` is intraday with RVOL filter; `DonchianBreakoutStrategy` is pure channel high/low without volume condition. Both preserved with distinct strategy IDs.
4. **Registry Architecture:**
   - `algopro/quant/strategies/registry.py` had silent default fallbacks when parameter initialization failed (`return strategy_cls()`).
   - **Resolution:** Replace with fail-closed parameter validation and explicit exception raising.
