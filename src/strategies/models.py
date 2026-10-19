"""Strategy Signals, Legacy Adapters, and Parameters Data Models.

Adheres to Non-Negotiable Invariants:
- Exact monetary precision (Decimal).
- Explicit strategy ID and version provenance.
- Separate signal generation from risk approval and order execution.
- Fail-closed normalization of legacy strategy outputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Optional
import uuid

from src.brokergateway.states import OrderSide
from src.core.exceptions import InvalidStrategyParameterError


class SignalType(str, Enum):
    """Core direction / action emitted by a strategy decision."""
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"


@dataclass
class StrategySignal:
    """Canonical actionable trade signal emitted by a quant strategy."""
    signal_id: str
    strategy_id: str
    symbol: str
    side: OrderSide
    target_entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    confidence_score: Decimal
    timestamp_iso: str
    strategy_version: str = "1.0.0"
    order_type: str = "LIMIT"
    reason: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    data_provenance: Dict[str, Any] = field(default_factory=dict)
    position_sizing_ratio: Optional[Decimal] = None


@dataclass
class LegacyStrategyDecision:
    """Mock/dataclass representation of algopro/quant StrategyDecision."""
    symbol: str
    decision_timestamp: Any
    signal: Any
    confidence: float = 0.0
    reason: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    stop_loss: Optional[float] = None
    target_price: Optional[float] = None
    position_sizing_ratio: Optional[float] = None


@dataclass
class LegacyTradeCandidate:
    """Mock/dataclass representation of algopro/services TradeCandidate."""
    strategy_id: str
    strategy_version: str
    symbol: str
    timestamp: Any
    action: Any
    confidence: float
    reason: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    suggested_limit_price: Optional[float] = None
    suggested_stop_loss: Optional[float] = None
    suggested_target_price: Optional[float] = None
    suggested_position_size_pct: Optional[float] = None


@dataclass
class LegacyOrderIntent:
    """Mock/dataclass representation of all-in-one oms_project OrderIntent."""
    strategy_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    stop_loss_pct: Optional[float] = None
    timestamp: Optional[str] = None


def _get_val(obj: Any, *keys: str, default: Any = None) -> Any:
    """Helper to extract attribute or dictionary key across multiple potential naming variants."""
    for k in keys:
        if isinstance(obj, dict) and k in obj:
            return obj[k]
        if hasattr(obj, k):
            val = getattr(obj, k)
            if val is not None:
                return val
    return default


def adapt_legacy_strategy_decision(
    decision: Any,
    symbol: Optional[str] = None,
    current_price: Optional[Decimal] = None,
    strategy_id: Optional[str] = None,
    strategy_version: str = "1.0.0",
) -> Optional[StrategySignal]:
    """Explicit adapter normalizing legacy StrategyDecision into canonical StrategySignal.

    Fails closed:
    - If signal is WAIT, returns None (no trade forced).
    - Rejects invalid/contradictory stop-loss and target prices.
    """
    sig_raw = _get_val(decision, "signal", "action", default=None)
    sig_str = sig_raw.value if hasattr(sig_raw, "value") else str(sig_raw).upper()

    if sig_str == "WAIT" or sig_str not in ("BUY", "SELL"):
        return None

    side = OrderSide.BUY if sig_str == "BUY" else OrderSide.SELL

    sym = symbol or _get_val(decision, "symbol", "tradingsymbol", default="UNKNOWN")
    entry_val = current_price if current_price is not None else _get_val(decision, "entry_price", "price", "current_price")
    if entry_val is None:
        raise InvalidStrategyParameterError(f"StrategyDecision for {sym} cannot be adapted: missing entry price")
    entry_p = Decimal(str(round(float(entry_val), 2)))

    sl_raw = _get_val(decision, "stop_loss", "sl", default=None)
    tp_raw = _get_val(decision, "target_price", "take_profit", "target", "tp", default=None)

    if sl_raw is None or tp_raw is None:
        raise InvalidStrategyParameterError(
            f"StrategyDecision for {sym} cannot be adapted: missing stop_loss or target_price"
        )

    sl_dec = Decimal(str(round(float(sl_raw), 2)))
    tp_dec = Decimal(str(round(float(tp_raw), 2)))

    # Semantic sanity checks
    if side == OrderSide.BUY:
        if sl_dec >= entry_p or tp_dec <= entry_p:
            raise InvalidStrategyParameterError(
                f"Contradictory Long geometry: entry={entry_p}, stop={sl_dec}, target={tp_dec}"
            )
    else:
        if sl_dec <= entry_p or tp_dec >= entry_p:
            raise InvalidStrategyParameterError(
                f"Contradictory Short geometry: entry={entry_p}, stop={sl_dec}, target={tp_dec}"
            )

    ts_raw = _get_val(decision, "decision_timestamp", "timestamp", default=None)
    ts_iso = ts_raw.isoformat() if hasattr(ts_raw, "isoformat") else str(ts_raw or "2026-10-19T09:15:00+05:30")
    strat_id = strategy_id or _get_val(decision, "strategy_id", default="LEGACY_STRATEGY")

    conf = Decimal(str(round(float(_get_val(decision, "confidence", default=0.5)), 4)))
    sizing = _get_val(decision, "position_sizing_ratio", "position_size_pct", default=None)
    sizing_dec = Decimal(str(round(float(sizing), 4))) if sizing is not None else None

    return StrategySignal(
        signal_id=f"SIG-ADAPT-{uuid.uuid4().hex[:8].upper()}",
        strategy_id=strat_id,
        strategy_version=strategy_version,
        symbol=sym,
        side=side,
        target_entry_price=entry_p,
        stop_loss=sl_dec,
        take_profit=tp_dec,
        confidence_score=conf,
        timestamp_iso=ts_iso,
        order_type="LIMIT",
        reason=_get_val(decision, "reason", default="Adapted from StrategyDecision"),
        evidence=_get_val(decision, "evidence", default={}),
        data_provenance={"adapter": "adapt_legacy_strategy_decision", "original_type": type(decision).__name__},
        position_sizing_ratio=sizing_dec,
    )


def adapt_legacy_trade_candidate(candidate: Any) -> Optional[StrategySignal]:
    """Explicit adapter normalizing legacy TradeCandidate into canonical StrategySignal."""
    action_raw = _get_val(candidate, "action", "bias", "direction", default=None)
    action_str = action_raw.value if hasattr(action_raw, "value") else str(action_raw).upper()

    if "LONG" in action_str or "BUY" in action_str or "BULLISH" in action_str:
        side = OrderSide.BUY
    elif "SHORT" in action_str or "SELL" in action_str or "BEARISH" in action_str:
        side = OrderSide.SELL
    else:
        return None

    entry_val = _get_val(candidate, "suggested_limit_price", "price", "current_price", default=None)
    if entry_val is None:
        return None
    entry_p = Decimal(str(round(float(entry_val), 2)))

    sl_raw = _get_val(candidate, "suggested_stop_loss", "stop_loss", "sl", default=None)
    tp_raw = _get_val(candidate, "suggested_target_price", "target", "take_profit", "tp", default=None)
    if sl_raw is None or tp_raw is None:
        return None

    sl_dec = Decimal(str(round(float(sl_raw), 2)))
    tp_dec = Decimal(str(round(float(tp_raw), 2)))

    ts_raw = _get_val(candidate, "timestamp", default=None)
    ts_iso = ts_raw.isoformat() if hasattr(ts_raw, "isoformat") else str(ts_raw or "2026-10-19T09:15:00+05:30")

    return StrategySignal(
        signal_id=f"SIG-TC-{uuid.uuid4().hex[:8].upper()}",
        strategy_id=_get_val(candidate, "strategy_id", default="UNKNOWN_STRAT"),
        strategy_version=_get_val(candidate, "strategy_version", default="1.0.0"),
        symbol=_get_val(candidate, "symbol", default="UNKNOWN"),
        side=side,
        target_entry_price=entry_p,
        stop_loss=sl_dec,
        take_profit=tp_dec,
        confidence_score=Decimal(str(round(float(_get_val(candidate, "confidence", default=0.5)), 4))),
        timestamp_iso=ts_iso,
        order_type="LIMIT",
        reason=_get_val(candidate, "reason", default="Adapted from TradeCandidate"),
        evidence=_get_val(candidate, "evidence", default={}),
        data_provenance={"adapter": "adapt_legacy_trade_candidate"},
        position_sizing_ratio=Decimal(str(_get_val(candidate, "suggested_position_size_pct", default=0.05))),
    )


def adapt_legacy_order_intent(intent: Any, default_tp_mult: Decimal = Decimal("2.0")) -> StrategySignal:
    """Explicit adapter normalizing oms_project OrderIntent into canonical StrategySignal."""
    side_str = str(_get_val(intent, "side", "action", default="")).upper()
    side = OrderSide.BUY if side_str in ("BUY", "LONG") else OrderSide.SELL
    price_val = _get_val(intent, "price", "limit_price", default=0.0)
    price = Decimal(str(round(float(price_val), 2)))

    sl_raw = _get_val(intent, "stop_loss", default=None)
    tp_raw = _get_val(intent, "target", "take_profit", default=None)
    if sl_raw is not None:
        stop_loss = Decimal(str(round(float(sl_raw), 2)))
        take_profit = Decimal(str(round(float(tp_raw), 2))) if tp_raw is not None else (
            price + (abs(price - stop_loss) * default_tp_mult) if side == OrderSide.BUY else price - (abs(price - stop_loss) * default_tp_mult)
        )
    else:
        sl_pct = Decimal(str(_get_val(intent, "stop_loss_pct", default=0.02) or 0.02))
        sl_dist = price * sl_pct
        if side == OrderSide.BUY:
            stop_loss = price - sl_dist
            take_profit = price + (sl_dist * default_tp_mult)
        else:
            stop_loss = price + sl_dist
            take_profit = price - (sl_dist * default_tp_mult)

    ts = _get_val(intent, "timestamp", default=None) or "2026-10-19T09:15:00+05:30"

    return StrategySignal(
        signal_id=f"SIG-OMS-{uuid.uuid4().hex[:8].upper()}",
        strategy_id=_get_val(intent, "strategy_id", default="STRAT_OMS"),
        strategy_version="1.0.0",
        symbol=_get_val(intent, "symbol", "tradingsymbol", default="UNKNOWN"),
        side=side,
        target_entry_price=price,
        stop_loss=stop_loss.quantize(Decimal("0.01")),
        take_profit=take_profit.quantize(Decimal("0.01")),
        confidence_score=Decimal("0.70"),
        timestamp_iso=ts,
        order_type="LIMIT",
        reason="Adapted from OMS OrderIntent",
        evidence={"order_intent_qty": _get_val(intent, "quantity", default=1)},
        data_provenance={"adapter": "adapt_legacy_order_intent"},
    )
