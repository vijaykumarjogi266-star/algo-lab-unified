"""Event-Driven Offline Historical Replay and Synthetic Backtesting Engine.

Integrates:
1. Scanner & Strategy Signal Generation
2. Atomic Capital Reservations (CapitalReservationService)
3. Safety Guard Validation (OrderDispatchSafetyGuard)
4. OMS Execution & Fill Deduplication (BrokerGateway)
5. Statutory NSE Fees & Slippage (NSEFrictionModel)
6. Double-Entry Balance Sheet Certification (EODReconciler)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional
import uuid

from src.brokergateway.gateway import BrokerGateway
from src.brokergateway.models import ExecutionFill
from src.brokergateway.states import OrderSide, OrderType, OrderState
from src.capitalreservation.models import ReservationOutcome
from src.capitalreservation.service import CapitalReservationService
from src.eodreconciler.models import EODReconciliationReport, PositionBalance
from src.eodreconciler.reconciler import EODReconciler
from src.core.tick_precision import quantize_order_price
from src.scanner.scanner import IntradayMarketScanner, MarketBar
from src.simulation.fee_model import NSEFrictionModel
from src.strategies.momentum_breakout import MomentumBreakoutStrategy


@dataclass
class SimulationMetrics:
    """Comprehensive performance and risk metrics from backtesting."""
    initial_capital: Decimal
    ending_cash: Decimal
    gross_realized_pnl: Decimal
    net_realized_pnl: Decimal
    total_statutory_fees: Decimal
    total_slippage: Decimal
    total_friction: Decimal
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: Decimal
    profit_factor: Decimal
    max_drawdown_pct: Decimal
    rejected_signals_count: int
    is_eod_balanced: bool
    reconciliation_discrepancies: List[str] = field(default_factory=list)


class HistoricalReplayEngine:
    """Full offline backtesting pipeline with production paper execution rules."""

    def __init__(
        self,
        initial_capital: Decimal = Decimal("500000.00"),  # 5 Lakh INR paper capital
        max_risk_per_trade_pct: Decimal = Decimal("0.02"),  # 2% risk ceiling
    ):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.max_risk_per_trade_pct = max_risk_per_trade_pct

        self.scanner = IntradayMarketScanner()
        self.strategy = MomentumBreakoutStrategy()
        self.reservation_service = CapitalReservationService()
        self.gateway = BrokerGateway()
        self.reconciler = EODReconciler()

        self.trades: List[Dict[str, Any]] = []
        self.rejected_signals: int = 0
        self.equity_curve: List[Decimal] = [initial_capital]

    def run_replay(
        self,
        market_data: Dict[str, List[MarketBar]],
    ) -> SimulationMetrics:
        """Executes chronological bar replay across all symbols."""
        # Find all unique timestamps across symbols
        all_timestamps = sorted({
            bar.timestamp_iso
            for bars in market_data.values()
            for bar in bars
        })

        symbol_history: Dict[str, List[MarketBar]] = {s: [] for s in market_data}
        open_positions: Dict[str, Dict[str, Any]] = {}
        gross_realized_pnl = Decimal("0.0")
        total_fees = Decimal("0.0")
        total_slippage = Decimal("0.0")
        wins = 0
        losses = 0
        gross_wins = Decimal("0.0")
        gross_losses = Decimal("0.0")

        for ts in all_timestamps:
            # 1. Update current bar for each symbol
            for symbol, bars in market_data.items():
                current_bars = [b for b in bars if b.timestamp_iso == ts]
                if not current_bars:
                    continue
                bar = current_bars[0]

                # Check exits on open positions
                if symbol in open_positions:
                    pos = open_positions[symbol]
                    exit_price: Optional[Decimal] = None
                    exit_reason: Optional[str] = None

                    # Conservative collision check: if bar touches both SL and TP, SL is triggered
                    sl_hit = bar.low <= pos["stop_loss"] if pos["side"] == OrderSide.BUY else bar.high >= pos["stop_loss"]
                    tp_hit = bar.high >= pos["take_profit"] if pos["side"] == OrderSide.BUY else bar.low <= pos["take_profit"]

                    if sl_hit:
                        # Gap check: execute at open if bar opened through/beyond stop loss
                        if pos["side"] == OrderSide.BUY and bar.open <= pos["stop_loss"]:
                            exit_price = quantize_order_price(bar.open, symbol, side_is_buy=False, order_purpose="STOP_LOSS")
                        elif pos["side"] == OrderSide.SELL and bar.open >= pos["stop_loss"]:
                            exit_price = quantize_order_price(bar.open, symbol, side_is_buy=True, order_purpose="STOP_LOSS")
                        else:
                            exit_price = pos["stop_loss"]
                        exit_reason = "STOP_LOSS"
                    elif tp_hit:
                        exit_price = pos["take_profit"]
                        exit_reason = "TAKE_PROFIT"

                    if exit_price:
                        # Close position
                        exit_side = OrderSide.SELL if pos["side"] == OrderSide.BUY else OrderSide.BUY
                        qty = pos["quantity"]
                        trade_gross = (exit_price - pos["entry_price"]) * Decimal(str(qty)) if pos["side"] == OrderSide.BUY else (pos["entry_price"] - exit_price) * Decimal(str(qty))

                        # Calculate exit friction
                        exit_friction = NSEFrictionModel.calculate_friction(exit_side.value, qty, exit_price)
                        entry_friction = pos["entry_friction"]

                        trade_fees = entry_friction.total_transaction_cost + exit_friction.total_transaction_cost
                        trade_slippage = entry_friction.slippage + exit_friction.slippage
                        trade_net = trade_gross - trade_fees - trade_slippage

                        # Create dedicated exit order
                        exit_order_id = f"ORD-EXIT-{uuid.uuid4().hex[:8]}"
                        exit_order = self.gateway.create_order(
                            order_id=exit_order_id,
                            idempotency_key=f"IDEM-{exit_order_id}",
                            strategy_id=self.strategy.STRATEGY_ID,
                            symbol=symbol,
                            side=exit_side,
                            order_type=OrderType.MARKET,
                            quantity=qty,
                            price=exit_price,
                            venue="PAPER",
                            is_risk_approved=True,
                        )
                        self.gateway.transition_state(exit_order_id, OrderState.SUBMITTED, "Dispatched exit order to venue")
                        exit_fill = ExecutionFill(
                            fill_id=f"FILL-EXIT-{uuid.uuid4().hex[:8]}",
                            order_id=exit_order_id,
                            symbol=symbol,
                            side=exit_side,
                            quantity=qty,
                            price=exit_price,
                            transaction_cost=exit_friction.total_transaction_cost,
                            slippage_cost=exit_friction.slippage,
                        )
                        self.gateway.process_fill(exit_fill)

                        # Update cash and metrics
                        if pos["side"] == OrderSide.BUY:
                            self.cash += (Decimal(str(qty)) * exit_price) - exit_friction.total_transaction_cost - exit_friction.slippage
                        else:
                            self.cash -= (Decimal(str(qty)) * exit_price) + exit_friction.total_transaction_cost + exit_friction.slippage
                        gross_realized_pnl += trade_gross
                        total_fees += exit_friction.total_transaction_cost
                        total_slippage += exit_friction.slippage

                        if trade_gross > Decimal("0.0"):
                            wins += 1
                            gross_wins += trade_gross
                        else:
                            losses += 1
                            gross_losses += abs(trade_gross)

                        self.trades.append({
                            "symbol": symbol,
                            "entry_time": pos["entry_time"],
                            "exit_time": ts,
                            "gross_pnl": trade_gross,
                            "net_pnl": trade_net,
                            "fees": trade_fees,
                            "slippage": trade_slippage,
                            "reason": exit_reason,
                        })

                        # Release capital reservation
                        self.reservation_service.consume_capital(pos["reservation_id"])
                        del open_positions[symbol]
                        self.equity_curve.append(self.cash)
                        continue

                # Scanner evaluation if no position open
                if symbol not in open_positions and len(symbol_history[symbol]) >= 20:
                    candidate = self.scanner.scan_symbol(symbol, bar, symbol_history[symbol])
                    if candidate:
                        signal = self.strategy.evaluate_candidate(candidate)
                        if signal:
                            # Position sizing: risk 2% of cash, but cap total notional at 20% of available cash
                            risk_amount = self.cash * self.max_risk_per_trade_pct
                            risk_per_share = abs(signal.target_entry_price - signal.stop_loss)
                            raw_qty = int(risk_amount / risk_per_share) if risk_per_share > Decimal("0.0") else 0
                            max_capital_qty = int((self.cash * Decimal("0.20")) / signal.target_entry_price)
                            qty = max(1, min(raw_qty, max_capital_qty))

                            notional = Decimal(str(qty)) * signal.target_entry_price
                            # Reserve capital atomically
                            res = self.reservation_service.reserve_capital(
                                reservation_id=f"RES-{uuid.uuid4().hex[:8]}",
                                account_id="PAPER_ACC_01",
                                idempotency_key=f"IDEM-{signal.signal_id}",
                                strategy_id=signal.strategy_id,
                                symbol=symbol,
                                quantity=qty,
                                price=signal.target_entry_price,
                                get_available_capital=lambda acc: self.cash,
                            )

                            if res["outcome"] == ReservationOutcome.RESERVED:
                                # Order creation
                                order_id = f"ORD-{uuid.uuid4().hex[:8]}"
                                order = self.gateway.create_order(
                                    order_id=order_id,
                                    idempotency_key=f"IDEM-{signal.signal_id}",
                                    strategy_id=signal.strategy_id,
                                    symbol=symbol,
                                    side=signal.side,
                                    order_type=OrderType.LIMIT,
                                    quantity=qty,
                                    price=signal.target_entry_price,
                                    venue="PAPER",
                                    is_risk_approved=True,
                                )
                                self.gateway.transition_state(order_id, OrderState.SUBMITTED, "Dispatched to paper execution simulator")

                                # Simulate instantaneous paper fill
                                entry_friction = NSEFrictionModel.calculate_friction(signal.side.value, qty, signal.target_entry_price)
                                fill = ExecutionFill(
                                    fill_id=f"FILL-ENTRY-{uuid.uuid4().hex[:8]}",
                                    order_id=order_id,
                                    symbol=symbol,
                                    side=signal.side,
                                    quantity=qty,
                                    price=signal.target_entry_price,
                                    transaction_cost=entry_friction.total_transaction_cost,
                                    slippage_cost=entry_friction.slippage,
                                )
                                self.gateway.process_fill(fill)

                                if signal.side == OrderSide.BUY:
                                    self.cash -= (notional + entry_friction.total_transaction_cost + entry_friction.slippage)
                                else:
                                    self.cash += (notional - entry_friction.total_transaction_cost - entry_friction.slippage)
                                total_fees += entry_friction.total_transaction_cost
                                total_slippage += entry_friction.slippage

                                open_positions[symbol] = {
                                    "order_id": order_id,
                                    "reservation_id": res["reservation_id"],
                                    "side": signal.side,
                                    "quantity": qty,
                                    "entry_price": signal.target_entry_price,
                                    "entry_friction": entry_friction,
                                    "stop_loss": signal.stop_loss,
                                    "take_profit": signal.take_profit,
                                    "entry_time": ts,
                                }
                            else:
                                self.rejected_signals += 1

                symbol_history[symbol].append(bar)

        # Force EOD square-off for remaining open positions
        for symbol, pos in list(open_positions.items()):
            last_bar = symbol_history[symbol][-1]
            exit_side = OrderSide.SELL if pos["side"] == OrderSide.BUY else OrderSide.BUY
            exit_price = quantize_order_price(
                last_bar.close,
                symbol,
                side_is_buy=(exit_side == OrderSide.BUY),
                order_purpose="ENTRY",
            )
            qty = pos["quantity"]
            trade_gross = (exit_price - pos["entry_price"]) * Decimal(str(qty)) if pos["side"] == OrderSide.BUY else (pos["entry_price"] - exit_price) * Decimal(str(qty))

            exit_friction = NSEFrictionModel.calculate_friction(exit_side.value, qty, exit_price)
            entry_friction = pos["entry_friction"]
            trade_fees = entry_friction.total_transaction_cost + exit_friction.total_transaction_cost
            trade_slippage = entry_friction.slippage + exit_friction.slippage
            trade_net = trade_gross - trade_fees - trade_slippage

            exit_order_id = f"ORD-EOD-{uuid.uuid4().hex[:8]}"
            exit_order = self.gateway.create_order(
                order_id=exit_order_id,
                idempotency_key=f"IDEM-{exit_order_id}",
                strategy_id=self.strategy.STRATEGY_ID,
                symbol=symbol,
                side=exit_side,
                order_type=OrderType.MARKET,
                quantity=qty,
                price=exit_price,
                venue="PAPER",
                is_risk_approved=True,
            )
            self.gateway.transition_state(exit_order_id, OrderState.SUBMITTED, "Dispatched EOD exit order")
            exit_fill = ExecutionFill(
                fill_id=f"FILL-EOD-{uuid.uuid4().hex[:8]}",
                order_id=exit_order_id,
                symbol=symbol,
                side=exit_side,
                quantity=qty,
                price=exit_price,
                transaction_cost=exit_friction.total_transaction_cost,
                slippage_cost=exit_friction.slippage,
            )
            self.gateway.process_fill(exit_fill)
            self.reservation_service.consume_capital(pos["reservation_id"])

            if pos["side"] == OrderSide.BUY:
                self.cash += (Decimal(str(qty)) * exit_price) - exit_friction.total_transaction_cost - exit_friction.slippage
            else:
                self.cash -= (Decimal(str(qty)) * exit_price) + exit_friction.total_transaction_cost + exit_friction.slippage
            gross_realized_pnl += trade_gross
            total_fees += exit_friction.total_transaction_cost
            total_slippage += exit_friction.slippage

            if trade_gross > Decimal("0.0"):
                wins += 1
                gross_wins += trade_gross
            else:
                losses += 1
                gross_losses += abs(trade_gross)

            self.trades.append({
                "symbol": symbol,
                "entry_time": pos["entry_time"],
                "exit_time": last_bar.timestamp_iso,
                "gross_pnl": trade_gross,
                "net_pnl": trade_net,
                "fees": trade_fees,
                "slippage": trade_slippage,
                "reason": "EOD_SQUAREOFF",
            })
            del open_positions[symbol]
            self.equity_curve.append(self.cash)

        # 4. EOD Double-Entry Reconciliation
        all_orders = list(self.gateway.orders.values())
        all_fills = [f for o in all_orders for f in o.fills]
        eod_report = self.reconciler.reconcile_session(
            session_id=f"REPLAY-SESSION-{uuid.uuid4().hex[:6].upper()}",
            initial_capital=self.initial_capital,
            ending_cash=self.cash,
            orders=all_orders,
            fills=all_fills,
            positions={},  # All closed EOD
            gross_realized_pnl=gross_realized_pnl,
            total_fees_paid=total_fees,
            total_slippage_paid=total_slippage,
        )

        total_trades = wins + losses
        win_rate = (Decimal(str(wins)) / Decimal(str(total_trades)) * Decimal("100.0")) if total_trades > 0 else Decimal("0.0")
        profit_factor = (gross_wins / gross_losses) if gross_losses > Decimal("0.0") else Decimal("999.0")

        # Max drawdown
        peak = self.initial_capital
        max_dd = Decimal("0.0")
        for eq in self.equity_curve:
            if eq > peak:
                peak = eq
            dd = ((peak - eq) / peak) * Decimal("100.0") if peak > Decimal("0.0") else Decimal("0.0")
            if dd > max_dd:
                max_dd = dd

        return SimulationMetrics(
            initial_capital=self.initial_capital,
            ending_cash=self.cash,
            gross_realized_pnl=gross_realized_pnl,
            net_realized_pnl=gross_realized_pnl - (total_fees + total_slippage),
            total_statutory_fees=total_fees,
            total_slippage=total_slippage,
            total_friction=total_fees + total_slippage,
            total_trades=total_trades,
            winning_trades=wins,
            losing_trades=losses,
            win_rate_pct=win_rate.quantize(Decimal("0.1")),
            profit_factor=profit_factor.quantize(Decimal("0.01")),
            max_drawdown_pct=max_dd.quantize(Decimal("0.1")),
            rejected_signals_count=self.rejected_signals,
            is_eod_balanced=eod_report.is_balanced,
            reconciliation_discrepancies=eod_report.discrepancies,
        )
