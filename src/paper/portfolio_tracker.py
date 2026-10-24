"""Paper Portfolio and Position Lifecycle Tracker.

Enforces:
1. Strict paper safety invariants (assert_paper_only_invariants).
2. Precise Decimal arithmetic for all currency and position sizing.
3. Realistic statutory friction accounting on both entry and exit.
4. Position lifecycle management:
   - Capital reservation and entry fill
   - Real-time mark-to-market updates
   - Dynamic stop-loss and take-profit hit detection
   - Clean double-entry cash and PnL conservation
5. Atomic JSON state persistence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import json
import logging
import os
from pathlib import Path
import shutil
import threading
from typing import Any, Dict, List, Optional, Tuple
import uuid

from src.core.safety_invariants import assert_paper_only_invariants
from src.core.tick_precision import quantize_order_price
from src.simulation.fee_model import NSEFrictionModel

logger = logging.getLogger(__name__)


def _to_decimal(val: Any) -> Decimal:
    """Helper to safely convert float/int/str/Decimal to Decimal with 4 decimals."""
    if isinstance(val, Decimal):
        return val
    return Decimal(str(val))


@dataclass
class PaperPosition:
    """Represents an active open paper trading position."""
    position_id: str
    symbol: str
    sector: str
    side: str
    quantity: int
    entry_price: Decimal
    current_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    entry_friction: Decimal
    entry_timestamp: str
    order_id: str
    fill_id: str
    strategy_id: str = "STRAT_ADAPTIVE_ROUTER"

    @property
    def notional(self) -> Decimal:
        return self.entry_price * Decimal(str(self.quantity))

    @property
    def current_value(self) -> Decimal:
        if self.side == "BUY":
            return self.current_price * Decimal(str(self.quantity))
        else:
            # DEF-013: Short positions represent delivery obligations (liabilities)
            return - (self.current_price * Decimal(str(self.quantity)))

    @property
    def unrealized_pnl(self) -> Decimal:
        if self.side == "BUY":
            return (self.current_price - self.entry_price) * Decimal(str(self.quantity))
        else:
            return (self.entry_price - self.current_price) * Decimal(str(self.quantity))

    @property
    def unrealized_pnl_pct(self) -> Decimal:
        if self.entry_price == Decimal("0"):
            return Decimal("0")
        diff = self.current_price - self.entry_price if self.side == "BUY" else self.entry_price - self.current_price
        return (diff / self.entry_price) * Decimal("100")

    def to_dict(self) -> dict:
        return {
            "position_id": self.position_id,
            "symbol": self.symbol,
            "sector": self.sector,
            "side": self.side,
            "quantity": self.quantity,
            "entry_price": float(self.entry_price),
            "current_price": float(self.current_price),
            "stop_loss": float(self.stop_loss),
            "take_profit": float(self.take_profit),
            "notional": float(self.notional),
            "current_value": float(self.current_value),
            "unrealized_pnl": float(self.unrealized_pnl),
            "unrealized_pnl_pct": float(self.unrealized_pnl_pct),
            "entry_friction": float(self.entry_friction),
            "entry_timestamp": self.entry_timestamp,
            "order_id": self.order_id,
            "fill_id": self.fill_id,
            "strategy_id": self.strategy_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PaperPosition:
        return cls(
            position_id=data["position_id"],
            symbol=data["symbol"],
            sector=data.get("sector", "GENERAL"),
            side=data.get("side", "BUY"),
            quantity=int(data["quantity"]),
            entry_price=_to_decimal(data["entry_price"]),
            current_price=_to_decimal(data.get("current_price", data["entry_price"])),
            stop_loss=_to_decimal(data["stop_loss"]),
            take_profit=_to_decimal(data["take_profit"]),
            entry_friction=_to_decimal(data.get("entry_friction", 0.0)),
            entry_timestamp=data.get("entry_timestamp", datetime.now().isoformat()),
            order_id=data.get("order_id", ""),
            fill_id=data.get("fill_id", ""),
            strategy_id=data.get("strategy_id", "STRAT_ADAPTIVE_ROUTER"),
        )


@dataclass
class PaperClosedTrade:
    """Audit record of a completed paper trade lifecycle."""
    trade_id: str
    position_id: str
    symbol: str
    sector: str
    side: str
    quantity: int
    entry_price: Decimal
    exit_price: Decimal
    entry_friction: Decimal
    exit_friction: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    return_pct: Decimal
    entry_timestamp: str
    exit_timestamp: str
    exit_reason: str

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "position_id": self.position_id,
            "symbol": self.symbol,
            "sector": self.sector,
            "side": self.side,
            "quantity": self.quantity,
            "entry_price": float(self.entry_price),
            "exit_price": float(self.exit_price),
            "entry_friction": float(self.entry_friction),
            "exit_friction": float(self.exit_friction),
            "total_friction": float(self.entry_friction + self.exit_friction),
            "gross_pnl": float(self.gross_pnl),
            "net_pnl": float(self.net_pnl),
            "return_pct": float(self.return_pct),
            "entry_timestamp": self.entry_timestamp,
            "exit_timestamp": self.exit_timestamp,
            "exit_reason": self.exit_reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PaperClosedTrade:
        return cls(
            trade_id=data["trade_id"],
            position_id=data["position_id"],
            symbol=data["symbol"],
            sector=data.get("sector", "GENERAL"),
            side=data.get("side", "BUY"),
            quantity=int(data["quantity"]),
            entry_price=_to_decimal(data["entry_price"]),
            exit_price=_to_decimal(data["exit_price"]),
            entry_friction=_to_decimal(data.get("entry_friction", 0.0)),
            exit_friction=_to_decimal(data.get("exit_friction", 0.0)),
            gross_pnl=_to_decimal(data["gross_pnl"]),
            net_pnl=_to_decimal(data["net_pnl"]),
            return_pct=_to_decimal(data.get("return_pct", 0.0)),
            entry_timestamp=data["entry_timestamp"],
            exit_timestamp=data["exit_timestamp"],
            exit_reason=data["exit_reason"],
        )


class PaperPortfolio:
    """Manages paper trading portfolio state, risk allocation, and balance conservation."""

    def __init__(
        self,
        account_id: str = "PAPER_ACC_10K",
        initial_capital: Decimal = Decimal("10000.00"),
        cash_balance: Optional[Decimal] = None,
        max_positions: int = 4,
        max_sector_exposure: int = 2,
    ):
        assert_paper_only_invariants()
        self._lock = threading.RLock()
        self.account_id = account_id
        self.initial_capital = _to_decimal(initial_capital)
        self.cash_balance = _to_decimal(cash_balance) if cash_balance is not None else self.initial_capital
        self.max_positions = max_positions
        self.max_sector_exposure = max_sector_exposure
        self.positions: dict[str, PaperPosition] = {}
        self.closed_trades: list[PaperClosedTrade] = []
        self.processed_fill_ids: set[str] = set()
        self.restricted_short_proceeds: Decimal = Decimal("0.00")
        self.locked_margin: Decimal = Decimal("0.00")
        self.broker_payable: Decimal = Decimal("0.00")
        self.is_frozen: bool = False
        self.ledger_journal: list[dict] = []
        self.created_at = datetime.now().isoformat()
        self.updated_at = self.created_at

        # DEF-012/DEF-018: Balanced double-entry opening record
        if not self.ledger_journal and self.cash_balance > Decimal("0.00"):
            self._record_journal_entry(
                tx_type="INITIAL_DEPOSIT",
                description="Initial portfolio capital allocation",
                debits=[("CASH:FREE", self.cash_balance)],
                credits=[("EQUITY:CAPITAL", self.cash_balance)],
            )

    def _record_journal_entry(
        self,
        tx_type: str,
        description: str,
        debits: list[Tuple[str, Decimal]],
        credits: list[Tuple[str, Decimal]],
    ) -> dict:
        """Records a balanced double-entry transaction in ledger_journal (DEF-012, DEF-018)."""
        sum_debit = sum((amt for _, amt in debits), Decimal("0.00"))
        sum_credit = sum((amt for _, amt in credits), Decimal("0.00"))
        if sum_debit != sum_credit:
            raise ValueError(f"Ledger imbalance: debits ({sum_debit}) != credits ({sum_credit})")

        entry = {
            "entry_id": f"JRN-{uuid.uuid4().hex[:8]}",
            "timestamp": datetime.now().isoformat(),
            "tx_type": tx_type,
            "description": description,
            "debits": [{"account": acc, "amount": float(amt)} for acc, amt in debits],
            "credits": [{"account": acc, "amount": float(amt)} for acc, amt in credits],
            "total_amount": float(sum_debit),
        }
        self.ledger_journal.append(entry)
        return entry

    def can_open(self, symbol: str, sector: str, required_cash: Decimal, side: str = "BUY") -> Tuple[bool, str]:
        """Validates risk limits before placing paper order."""
        with self._lock:
            # DEF-019: Account freeze enforcement
            if self.is_frozen:
                return False, f"Account is frozen due to broker payable / negative equity (payable: ₹{self.broker_payable:.2f})"
            if symbol in self.positions:
                return False, f"Position already open for {symbol}"
            if len(self.positions) >= self.max_positions:
                return False, f"Max concurrent positions reached ({self.max_positions})"
            
            sector_count = sum(1 for p in self.positions.values() if p.sector == sector)
            if sector_count >= self.max_sector_exposure:
                return False, f"Max sector exposure reached for {sector} ({self.max_sector_exposure})"

            if self.cash_balance < required_cash:
                return False, f"Insufficient cash: Required ₹{required_cash:.2f}, Available ₹{self.cash_balance:.2f}"

            return True, "Approved"

    def open_position(
        self,
        symbol: str,
        sector: str,
        quantity: int,
        entry_price: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
        strategy_id: str = "STRAT_ADAPTIVE_ROUTER",
        order_id: Optional[str] = None,
        fill_id: Optional[str] = None,
        side: str = "BUY",
        slippage: Decimal = Decimal("0.00"),
    ) -> PaperPosition:
        """Opens a new long or short paper position with exact NSE statutory entry friction."""
        assert_paper_only_invariants()
        with self._lock:
            # DEF-009: Idempotency check on fill_id
            if fill_id and fill_id in self.processed_fill_ids:
                if symbol in self.positions and self.positions[symbol].fill_id == fill_id:
                    return self.positions[symbol]
                raise ValueError(f"Duplicate fill detected: {fill_id}")

            side = side.upper()
            if side not in ("BUY", "SELL"):
                raise ValueError(f"Invalid position side: {side}")

            slippage = _to_decimal(slippage)
            notional = entry_price * Decimal(str(quantity))
            friction = NSEFrictionModel.calculate_friction("BUY" if side == "BUY" else "SELL", quantity, entry_price)

            if side == "BUY":
                total_deduction = notional + friction.total_friction + slippage
                can, reason = self.can_open(symbol, sector, total_deduction, side=side)
                if not can:
                    raise ValueError(f"Cannot open position in {symbol}: {reason}")

                self.cash_balance -= total_deduction
                # Double entry journal for BUY
                debits: list[Tuple[str, Decimal]] = [
                    ("ASSET:EQUITY_LONG", notional),
                    ("EXPENSE:STATUTORY_FRICTION", friction.total_friction),
                ]
                if slippage > Decimal("0.00"):
                    debits.append(("EXPENSE:SLIPPAGE", slippage))

                self._record_journal_entry(
                    tx_type="POSITION_OPEN_BUY",
                    description=f"Open BUY {quantity} {symbol} @ {entry_price}",
                    debits=debits,
                    credits=[
                        ("CASH:FREE", total_deduction),
                    ],
                )
            else:
                # DEF-017: Short entry accounting with margin segregation
                required_margin = notional
                total_cash_needed = required_margin + friction.total_friction + slippage
                can, reason = self.can_open(symbol, sector, total_cash_needed, side=side)
                if not can:
                    raise ValueError(f"Cannot open short position in {symbol}: {reason}")

                # Lock margin and deduct friction and slippage from free cash; segregate short proceeds
                self.cash_balance -= total_cash_needed
                self.locked_margin += required_margin
                self.restricted_short_proceeds += notional

                # Double entry journal for SHORT entry
                debits: list[Tuple[str, Decimal]] = [
                    ("CASH:MARGIN_LOCKED", required_margin),
                    ("EXPENSE:STATUTORY_FRICTION", friction.total_friction),
                    ("CASH:SHORT_RESTRICTED", notional),
                ]
                if slippage > Decimal("0.00"):
                    debits.append(("EXPENSE:SLIPPAGE", slippage))

                self._record_journal_entry(
                    tx_type="POSITION_OPEN_SHORT",
                    description=f"Open SHORT {quantity} {symbol} @ {entry_price} with margin lock",
                    debits=debits,
                    credits=[
                        ("CASH:FREE", total_cash_needed),
                        ("LIABILITY:EQUITY_SHORT", notional),
                    ],
                )

            assigned_order_id = order_id or f"ORD-PAPER-{uuid.uuid4().hex[:8]}"
            assigned_fill_id = fill_id or f"FILL-PAPER-{uuid.uuid4().hex[:8]}"
            pid = f"POS-{uuid.uuid4().hex[:8]}"

            pos = PaperPosition(
                position_id=pid,
                symbol=symbol,
                sector=sector,
                side=side,
                quantity=quantity,
                entry_price=entry_price,
                current_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                entry_friction=friction.total_friction,
                entry_timestamp=datetime.now().isoformat(),
                order_id=assigned_order_id,
                fill_id=assigned_fill_id,
                strategy_id=strategy_id,
            )
            self.positions[symbol] = pos
            self.processed_fill_ids.add(assigned_fill_id)
            self.updated_at = datetime.now().isoformat()
            return pos

    def update_quotes(self, quotes: dict[str, Decimal]) -> None:
        """Updates current price and unrealized PnL for active positions."""
        with self._lock:
            for sym, pos in self.positions.items():
                if sym in quotes:
                    pos.current_price = _to_decimal(quotes[sym])
            self.updated_at = datetime.now().isoformat()

    def evaluate_exits(self, quotes: dict[str, Decimal]) -> list[PaperClosedTrade]:
        """Evaluates whether any active position has breached Stop Loss or Take Profit."""
        with self._lock:
            self.update_quotes(quotes)
            closed_records = []
            symbols_to_close = []

            for sym, pos in self.positions.items():
                curr = pos.current_price
                if pos.side == "BUY":
                    if curr <= pos.stop_loss:
                        symbols_to_close.append((sym, curr, "STOP_LOSS"))
                    elif curr >= pos.take_profit:
                        symbols_to_close.append((sym, curr, "TAKE_PROFIT"))
                else:
                    # Short position: stop loss breached if price rises, take profit if price falls
                    if curr >= pos.stop_loss:
                        symbols_to_close.append((sym, curr, "STOP_LOSS"))
                    elif curr <= pos.take_profit:
                        symbols_to_close.append((sym, curr, "TAKE_PROFIT"))

            for sym, exit_price, reason in symbols_to_close:
                trade = self.close_position(sym, exit_price, reason)
                closed_records.append(trade)

            return closed_records

    def close_position(
        self,
        symbol: str,
        exit_price: Decimal,
        exit_reason: str = "MANUAL",
        exit_timestamp: Optional[str] = None,
        slippage: Decimal = Decimal("0.00"),
    ) -> PaperClosedTrade:
        """Closes an active paper position and books realized PnL with statutory exit friction and slippage."""
        assert_paper_only_invariants()
        with self._lock:
            if symbol not in self.positions:
                raise KeyError(f"No active position for {symbol}")

            slippage = _to_decimal(slippage)
            pos = self.positions.pop(symbol)
            exit_price = _to_decimal(exit_price)
            qty_dec = Decimal(str(pos.quantity))
            exit_notional = exit_price * qty_dec

            if pos.side == "BUY":
                friction = NSEFrictionModel.calculate_friction("SELL", pos.quantity, exit_price)
                net_proceeds = exit_notional - friction.total_friction - slippage
                self.cash_balance += net_proceeds

                gross_pnl = (exit_price - pos.entry_price) * qty_dec
                net_pnl = gross_pnl - pos.entry_friction - friction.total_friction - slippage
                ret_pct = ((exit_price - pos.entry_price) / pos.entry_price) * Decimal("100") if pos.entry_price else Decimal("0")

                # Double entry journal for BUY exit
                debits: list[Tuple[str, Decimal]] = [
                    ("CASH:FREE", net_proceeds),
                    ("EXPENSE:STATUTORY_FRICTION", friction.total_friction),
                ]
                if slippage > Decimal("0.00"):
                    debits.append(("EXPENSE:SLIPPAGE", slippage))
                credits: list[Tuple[str, Decimal]] = [("ASSET:EQUITY_LONG", pos.notional)]
                if gross_pnl >= Decimal("0.00"):
                    credits.append(("REVENUE:REALIZED_PNL", gross_pnl))
                else:
                    debits.append(("EXPENSE:REALIZED_LOSS", -gross_pnl))

                self._record_journal_entry(
                    tx_type="POSITION_CLOSE_BUY",
                    description=f"Close long {pos.quantity} {symbol} @ {exit_price} ({exit_reason})",
                    debits=debits,
                    credits=credits,
                )

            else:
                # DEF-017: Short exit / Buy to cover accounting
                friction = NSEFrictionModel.calculate_friction("BUY", pos.quantity, exit_price)
                total_cover_cost = exit_notional + friction.total_friction + slippage

                margin_released = pos.notional
                short_proceeds_released = pos.notional
                self.locked_margin -= margin_released
                self.restricted_short_proceeds -= short_proceeds_released

                gross_pnl = (pos.entry_price - exit_price) * qty_dec
                net_pnl = gross_pnl - pos.entry_friction - friction.total_friction - slippage
                ret_pct = ((pos.entry_price - exit_price) / pos.entry_price) * Decimal("100") if pos.entry_price else Decimal("0")

                # Net free cash adjustment: margin unlocked + short proceeds unlocked - cover cost
                cash_delta = margin_released + short_proceeds_released - total_cover_cost
                new_cash = self.cash_balance + cash_delta

                # DEF-019: Catastrophic overdraft handling
                overdraft = Decimal("0.00")
                cash_paid_from_free = Decimal("0.00")
                if new_cash < Decimal("0.00"):
                    overdraft = -new_cash
                    self.broker_payable += overdraft
                    cash_paid_from_free = self.cash_balance
                    self.cash_balance = Decimal("0.00")
                    self.is_frozen = True
                    logger.critical(
                        f"DEF-019: CATASTROPHIC OVERDRAFT ON SHORT COVER {symbol}: "
                        f"deficit ₹{overdraft:.2f} booked to broker_payable. Account is FROZEN."
                    )
                else:
                    self.cash_balance = new_cash

                # Double-entry balancing for short cover
                cover_debits: list[Tuple[str, Decimal]] = [
                    ("LIABILITY:EQUITY_SHORT", pos.notional),
                    ("EXPENSE:STATUTORY_FRICTION", friction.total_friction),
                ]
                if slippage > Decimal("0.00"):
                    cover_debits.append(("EXPENSE:SLIPPAGE", slippage))
                cover_credits: list[Tuple[str, Decimal]] = [
                    ("CASH:SHORT_RESTRICTED", pos.notional),
                    ("CASH:MARGIN_LOCKED", margin_released),
                ]

                if exit_notional > pos.notional:
                    cover_debits.append(("EXPENSE:REALIZED_LOSS", exit_notional - pos.notional))
                elif pos.notional > exit_notional:
                    cover_credits.append(("REVENUE:REALIZED_PNL", pos.notional - exit_notional))

                if cash_delta > Decimal("0.00"):
                    cover_debits.append(("CASH:FREE", cash_delta))
                elif cash_delta < Decimal("0.00"):
                    if overdraft > Decimal("0.00"):
                        if cash_paid_from_free > Decimal("0.00"):
                            cover_credits.append(("CASH:FREE", cash_paid_from_free))
                        cover_credits.append(("LIABILITY:BROKER_PAYABLE", overdraft))
                    else:
                        cover_credits.append(("CASH:FREE", -cash_delta))

                self._record_journal_entry(
                    tx_type="POSITION_CLOSE_SHORT",
                    description=f"Close short {pos.quantity} {symbol} @ {exit_price} ({exit_reason})",
                    debits=cover_debits,
                    credits=cover_credits,
                )

            closed = PaperClosedTrade(
                trade_id=f"TRD-{uuid.uuid4().hex[:8]}",
                position_id=pos.position_id,
                symbol=pos.symbol,
                sector=pos.sector,
                side=pos.side,
                quantity=pos.quantity,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                entry_friction=pos.entry_friction,
                exit_friction=friction.total_friction,
                gross_pnl=gross_pnl,
                net_pnl=net_pnl,
                return_pct=ret_pct,
                entry_timestamp=pos.entry_timestamp,
                exit_timestamp=exit_timestamp or datetime.now().isoformat(),
                exit_reason=exit_reason,
            )
            self.closed_trades.append(closed)
            self.updated_at = datetime.now().isoformat()
            return closed

    @property
    def invested_value(self) -> Decimal:
        with self._lock:
            return sum((p.current_value for p in self.positions.values()), Decimal("0.0"))

    @property
    def unrealized_pnl(self) -> Decimal:
        with self._lock:
            return sum((p.unrealized_pnl for p in self.positions.values()), Decimal("0.0"))

    @property
    def realized_pnl(self) -> Decimal:
        with self._lock:
            return sum((t.net_pnl for t in self.closed_trades), Decimal("0.0"))

    @property
    def total_equity(self) -> Decimal:
        with self._lock:
            # DEF-013/DEF-017/DEF-019: Correct short & liability balance reconciliation
            return (
                self.cash_balance
                + self.locked_margin
                + self.restricted_short_proceeds
                + self.invested_value
                - self.broker_payable
            )

    @property
    def total_return_pct(self) -> Decimal:
        with self._lock:
            if self.initial_capital == Decimal("0"):
                return Decimal("0")
            return ((self.total_equity - self.initial_capital) / self.initial_capital) * Decimal("100")

    def get_summary(self) -> dict:
        with self._lock:
            wins = sum(1 for t in self.closed_trades if t.net_pnl > Decimal("0"))
            total_closed = len(self.closed_trades)
            win_rate = (Decimal(str(wins)) / Decimal(str(total_closed)) * Decimal("100")) if total_closed > 0 else Decimal("0")

            return {
                "account_id": self.account_id,
                "initial_capital": float(self.initial_capital),
                "cash_balance": float(self.cash_balance),
                "locked_margin": float(self.locked_margin),
                "restricted_short_proceeds": float(self.restricted_short_proceeds),
                "broker_payable": float(self.broker_payable),
                "is_frozen": self.is_frozen,
                "invested_value": float(self.invested_value),
                "total_equity": float(self.total_equity),
                "unrealized_pnl": float(self.unrealized_pnl),
                "realized_pnl": float(self.realized_pnl),
                "total_pnl": float(self.total_equity - self.initial_capital),
                "total_return_pct": float(self.total_return_pct),
                "open_positions_count": len(self.positions),
                "closed_trades_count": total_closed,
                "win_rate_pct": float(win_rate),
                "updated_at": self.updated_at,
            }

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "account_id": self.account_id,
                "initial_capital": float(self.initial_capital),
                "cash_balance": float(self.cash_balance),
                "locked_margin": float(self.locked_margin),
                "restricted_short_proceeds": float(self.restricted_short_proceeds),
                "broker_payable": float(self.broker_payable),
                "is_frozen": self.is_frozen,
                "max_positions": self.max_positions,
                "max_sector_exposure": self.max_sector_exposure,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "positions": {k: v.to_dict() for k, v in self.positions.items()},
                "closed_trades": [t.to_dict() for t in self.closed_trades],
                "processed_fill_ids": list(self.processed_fill_ids),
                "ledger_journal": self.ledger_journal,
            }

    def save(self, filepath: Path) -> None:
        """Atomically saves portfolio state to disk with fsync and backup snapshot (DEF-F01, DEF-F04)."""
        with self._lock:
            filepath = Path(filepath)
            filepath.parent.mkdir(parents=True, exist_ok=True)
            temp_file = filepath.with_suffix(".tmp")
            bak_file = filepath.with_suffix(".bak")
            data = self.to_dict()
            with open(temp_file, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            # Backup existing file to .bak before replacing
            if filepath.exists():
                shutil.copy2(filepath, bak_file)
                try:
                    with open(bak_file, "r") as bf:
                        os.fsync(bf.fileno())
                except OSError:
                    pass

            temp_file.replace(filepath)

            # Sync directory
            try:
                dir_fd = os.open(str(filepath.parent), os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass

    @classmethod
    def _from_loaded_data(cls, data: dict, default_capital: Decimal) -> PaperPortfolio:
        port = cls(
            account_id=data.get("account_id", "PAPER_ACC_10K"),
            initial_capital=_to_decimal(data.get("initial_capital", default_capital)),
            cash_balance=_to_decimal(data.get("cash_balance", default_capital)),
            max_positions=data.get("max_positions", 4),
            max_sector_exposure=data.get("max_sector_exposure", 2),
        )
        port.locked_margin = _to_decimal(data.get("locked_margin", 0.0))
        port.restricted_short_proceeds = _to_decimal(data.get("restricted_short_proceeds", 0.0))
        port.broker_payable = _to_decimal(data.get("broker_payable", 0.0))
        port.is_frozen = bool(data.get("is_frozen", False))
        port.processed_fill_ids = set(data.get("processed_fill_ids", []))
        port.ledger_journal = list(data.get("ledger_journal", []))
        port.created_at = data.get("created_at", datetime.now().isoformat())
        port.updated_at = data.get("updated_at", datetime.now().isoformat())

        for sym, pos_data in data.get("positions", {}).items():
            port.positions[sym] = PaperPosition.from_dict(pos_data)

        for trade_data in data.get("closed_trades", []):
            port.closed_trades.append(PaperClosedTrade.from_dict(trade_data))

        return port

    @classmethod
    def load(cls, filepath: Path, default_capital: Decimal = Decimal("10000.00")) -> PaperPortfolio:
        """Loads portfolio state from disk or initializes a new one if not found.

        On JSONDecodeError or corruption, falls back gracefully to load from .bak snapshot (DEF-F01, DEF-F04).
        """
        filepath = Path(filepath)
        bak_file = filepath.with_suffix(".bak")

        if not filepath.exists():
            if bak_file.exists():
                try:
                    with open(bak_file, "r") as f:
                        data = json.load(f)
                    logger.warning("Main file %s missing, recovered from backup %s", filepath, bak_file)
                    port = cls._from_loaded_data(data, default_capital)
                    port.save(filepath)
                    return port
                except Exception as e:
                    logger.error("Failed to recover from %s: %s", bak_file, e)

            portfolio = cls(initial_capital=default_capital)
            portfolio.save(filepath)
            return portfolio

        try:
            with open(filepath, "r") as f:
                data = json.load(f)
            return cls._from_loaded_data(data, default_capital)
        except (json.JSONDecodeError, OSError, ValueError, KeyError) as err:
            logger.warning("Corrupted portfolio state in %s (%s). Attempting fallback to %s", filepath, err, bak_file)
            if bak_file.exists():
                try:
                    with open(bak_file, "r") as f:
                        bak_data = json.load(f)
                    logger.info("Successfully recovered portfolio from backup snapshot %s", bak_file)
                    return cls._from_loaded_data(bak_data, default_capital)
                except Exception as bak_err:
                    logger.error("Fallback recovery from %s also failed: %s", bak_file, bak_err)
                    raise
            raise
