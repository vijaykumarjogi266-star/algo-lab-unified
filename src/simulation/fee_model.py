"""Official NSE Intraday Statutory Fee and Friction Model.

Computes:
1. Brokerage: Min(INR 20, 0.03% of notional)
2. STT (Securities Transaction Tax): 0.025% on SELL only for intraday equity
3. Exchange Turnover Charges: 0.00345% (NSE)
4. SEBI Turnover Fee: 0.0001%
5. Stamp Duty: 0.003% on BUY only
6. GST: 18% on (Brokerage + Exchange Charges + SEBI Fees)
7. Slippage: Configurable basis points model (default: 2 bps)

All arithmetic uses Decimal with 28-digit precision.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, getcontext
from typing import NamedTuple

getcontext().prec = 28


class FrictionBreakdown(NamedTuple):
    brokerage: Decimal
    stt: Decimal
    exchange_charges: Decimal
    sebi_fees: Decimal
    stamp_duty: Decimal
    gst: Decimal
    slippage: Decimal
    total_transaction_cost: Decimal
    total_friction: Decimal


class NSEFrictionModel:
    """Official NSE Intraday statutory fee and friction calculator."""

    BROKERAGE_PERCENT = Decimal("0.0003")      # 0.03%
    BROKERAGE_CAP = Decimal("20.00")           # INR 20 max per order
    STT_SELL_PERCENT = Decimal("0.00025")      # 0.025% on sell
    EXCHANGE_TURNOVER_PCT = Decimal("0.0000345")  # 0.00345%
    SEBI_TURNOVER_PCT = Decimal("0.000001")    # 0.0001%
    STAMP_DUTY_BUY_PCT = Decimal("0.00003")    # 0.003% on buy
    GST_PERCENT = Decimal("0.18")              # 18%

    @classmethod
    def calculate_friction(
        cls,
        side: str,
        quantity: int,
        price: Decimal,
        slippage_bps: Decimal = Decimal("2.0"),
    ) -> FrictionBreakdown:
        """Calculates precise statutory fees and slippage for a fill."""
        qty_dec = Decimal(str(quantity))
        notional = (qty_dec * price).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        is_buy = side.upper() == "BUY"
        is_sell = side.upper() == "SELL"

        # 1. Brokerage
        raw_brokerage = notional * cls.BROKERAGE_PERCENT
        brokerage = min(cls.BROKERAGE_CAP, raw_brokerage).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 2. STT (0.025% on sell only)
        stt = (notional * cls.STT_SELL_PERCENT).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if is_sell else Decimal("0.00")

        # 3. Exchange Turnover Charges (0.00345%)
        exchange_charges = (notional * cls.EXCHANGE_TURNOVER_PCT).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 4. SEBI Turnover Fees (0.0001%)
        sebi_fees = (notional * cls.SEBI_TURNOVER_PCT).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 5. Stamp Duty (0.003% on buy only)
        stamp_duty = (notional * cls.STAMP_DUTY_BUY_PCT).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if is_buy else Decimal("0.00")

        # 6. GST (18% on Brokerage + Exchange + SEBI)
        gst_taxable = brokerage + exchange_charges + sebi_fees
        gst = (gst_taxable * cls.GST_PERCENT).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 7. Total Statutory Fees
        total_transaction_cost = brokerage + stt + exchange_charges + sebi_fees + stamp_duty + gst

        # 8. Slippage
        slippage_rate = slippage_bps / Decimal("10000.0")
        slippage = (notional * slippage_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        total_friction = total_transaction_cost + slippage

        return FrictionBreakdown(
            brokerage=brokerage,
            stt=stt,
            exchange_charges=exchange_charges,
            sebi_fees=sebi_fees,
            stamp_duty=stamp_duty,
            gst=gst,
            slippage=slippage,
            total_transaction_cost=total_transaction_cost,
            total_friction=total_friction,
        )
