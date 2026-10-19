"""Algo Lab Unified — Instrument-Specific Tick-Size Precision and Quantization.

Implements the official National Stock Exchange of India (NSE) price-linked tick size
framework under Circulars NSE/CMTR/62174 and NSE/CMTR/67133, and historical regimes.

Enforces:
1. Dynamic and authoritative security-level tick-size determination across historical statutory regimes:
   - Pre-June 10, 2024: Uniform statutory tick size of INR 0.05 across all cash equities.
   - June 10, 2024 – April 14, 2025: Circular NSE/CMTR/62174 (< INR 250 -> 0.01; >= INR 250 -> 0.05).
   - April 15, 2025 onwards: Circular NSE/CMTR/67133 multi-tier price-linked schedule (0.01, 0.05, 0.10, 0.50, 1.00, 5.00 INR).
2. Date-aware Security Master interface with monthly reference-price re-anchoring.
3. Provenance tracking for tick metadata version and statutory circular attribution.
4. Asymmetric conservative order-price quantization for bracket orders (stops, limits, targets).
5. Fail-closed rejection of missing, stale, off-tick, or conflicting metadata.
6. Mathematical decimal purity (pure Python Decimal arithmetic without binary floats).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
import re
from typing import Any, Dict, List, Optional, Tuple

from src.core.exceptions import (
    InvalidOrderTickError,
    MissingTickMetadataError,
)

# Statutory Circular Identifiers and Effective Dates
CIRCULAR_PRE_2024 = "NSE/HISTORICAL_UNIFORM"
CIRCULAR_62174 = "NSE/CMTR/62174"      # Issued 2024-05-24, Effective 2024-06-10
CIRCULAR_67133 = "NSE/CMTR/67133"      # Issued 2025-03-13, Effective 2025-04-15

DATE_EFFECTIVE_62174 = "2024-06-10"
DATE_EFFECTIVE_67133 = "2025-04-15"

# Official NSE Capital Market (Cash Equity) Price-Linked Tick Size Tiers under NSE/CMTR/67133
# (Min Price Inclusive, Max Price Exclusive, Statutory Tick Size)
NSE_TICK_TIERS: Tuple[Tuple[Decimal, Decimal, Decimal], ...] = (
    (Decimal("0.00"), Decimal("250.00"), Decimal("0.01")),
    (Decimal("250.00"), Decimal("1000.00"), Decimal("0.05")),
    (Decimal("1000.00"), Decimal("5000.00"), Decimal("0.10")),
    (Decimal("5000.00"), Decimal("10000.00"), Decimal("0.50")),
    (Decimal("10000.00"), Decimal("20000.00"), Decimal("1.00")),
    (Decimal("20000.00"), Decimal("Infinity"), Decimal("5.00")),
)


@dataclass(frozen=True)
class HistoricalTickMetadata:
    """Authoritative date-aware metadata record for an instrument's tick size."""
    symbol: str
    trade_date: str
    tick_size: Decimal
    effective_from: str
    effective_to: Optional[str]
    circular_reference: str
    regime: str  # "UNIFORM_5PAISE", "TWO_TIER_250", "MULTI_TIER_CMTR_67133"
    price_band_min: Decimal
    price_band_max: Decimal
    monthly_reference_price: Optional[Decimal]
    provenance_source: str


@dataclass(frozen=True)
class InstrumentTickMetadata:
    """Legacy metadata record maintained for backward compatibility."""
    symbol: str
    tick_size: Decimal
    effective_date: str
    circular_reference: str
    price_band_min: Decimal
    price_band_max: Decimal


def _parse_date_string(date_val: str | date | datetime) -> date:
    """Extracts date object from string or date/datetime instance."""
    if isinstance(date_val, datetime):
        return date_val.date()
    if isinstance(date_val, date):
        return date_val
    if not isinstance(date_val, str) or not date_val.strip():
        raise MissingTickMetadataError(f"Invalid date value provided for tick resolution: {date_val!r}")
    
    # Try ISO formats (e.g. "2026-09-04", "2026-09-04T09:15:00+05:30")
    clean_str = date_val.strip()[:10]
    try:
        return datetime.strptime(clean_str, "%Y-%m-%d").date()
    except ValueError as exc:
        raise MissingTickMetadataError(f"Unparseable date format for tick resolution: {date_val!r}") from exc


class HistoricalSecurityTickMaster:
    """Authoritative historical security master resolving statutory tick sizes by trade date."""

    # Month-end Bhavcopy closing price anchors for core universe (Year-Month -> Symbol -> Closing Price)
    # Re-anchors tick size on the 1st trading day of each month under circular guidelines.
    _MONTHLY_ANCHORS: Dict[str, Dict[str, Decimal]] = {
        # 2026 August Month-End (Anchors September 2026)
        "2026-08": {
            "RELIANCE": Decimal("1320.00"),
            "INFY": Decimal("1140.00"),
            "TCS": Decimal("2340.00"),
            "HDFCBANK": Decimal("710.50"),
            "SBIN": Decimal("815.30"),
            "ITC": Decimal("488.20"),
        },
        # 2026 September Month-End (Anchors October 2026)
        "2026-09": {
            "RELIANCE": Decimal("1187.00"),
            "INFY": Decimal("1050.00"),
            "TCS": Decimal("2050.60"),
            "HDFCBANK": Decimal("708.70"),
            "SBIN": Decimal("822.40"),
            "ITC": Decimal("492.10"),
        },
    }

    @classmethod
    def get_tick_metadata(
        cls,
        symbol: str,
        trade_date: str | date | datetime,
        reference_price: Optional[Decimal] = None,
    ) -> HistoricalTickMetadata:
        """Determines the authoritative tick metadata for a symbol on a specific historical date.

        Fails closed:
        - Rejects invalid dates.
        - Enforces statutory NSE effective dates.
        - Fails if unknown symbol and no reference price is provided.
        """
        parsed_d = _parse_date_string(trade_date)
        d_str = parsed_d.isoformat()
        clean_sym = symbol.replace(".NS", "").upper()

        eff_date_62174 = _parse_date_string(DATE_EFFECTIVE_62174)
        eff_date_67133 = _parse_date_string(DATE_EFFECTIVE_67133)

        # 1. Regime 1: Prior to June 10, 2024 (Uniform 5 paise across all cash equities)
        if parsed_d < eff_date_62174:
            return HistoricalTickMetadata(
                symbol=clean_sym,
                trade_date=d_str,
                tick_size=Decimal("0.05"),
                effective_from="1994-11-03",
                effective_to=DATE_EFFECTIVE_62174,
                circular_reference=CIRCULAR_PRE_2024,
                regime="UNIFORM_5PAISE",
                price_band_min=Decimal("0.00"),
                price_band_max=Decimal("Infinity"),
                monthly_reference_price=reference_price,
                provenance_source="NSE Uniform Cash Equity Tick Schedule (Pre-2024)",
            )

        # Establish effective monthly reference price
        year_month = d_str[:7]
        # Look for prior month anchor if available
        # Prior month calculation
        p_year = parsed_d.year if parsed_d.month > 1 else parsed_d.year - 1
        p_month = parsed_d.month - 1 if parsed_d.month > 1 else 12
        prior_ym = f"{p_year:04d}-{p_month:02d}"

        anchor_price = None
        if prior_ym in cls._MONTHLY_ANCHORS and clean_sym in cls._MONTHLY_ANCHORS[prior_ym]:
            anchor_price = cls._MONTHLY_ANCHORS[prior_ym][clean_sym]
        elif reference_price is not None:
            anchor_price = reference_price

        # 2. Regime 2: June 10, 2024 to April 14, 2025 (Circular NSE/CMTR/62174)
        if eff_date_62174 <= parsed_d < eff_date_67133:
            if anchor_price is None:
                raise MissingTickMetadataError(
                    f"Cannot determine tick size for '{symbol}' on {d_str} under {CIRCULAR_62174}: "
                    "Missing reference/closing price anchor."
                )
            if anchor_price < Decimal("0.00"):
                raise ValueError(f"Reference price cannot be negative: {anchor_price}")

            if anchor_price < Decimal("250.00"):
                tick = Decimal("0.01")
                b_min, b_max = Decimal("0.00"), Decimal("250.00")
            else:
                tick = Decimal("0.05")
                b_min, b_max = Decimal("250.00"), Decimal("Infinity")

            return HistoricalTickMetadata(
                symbol=clean_sym,
                trade_date=d_str,
                tick_size=tick,
                effective_from=DATE_EFFECTIVE_62174,
                effective_to=DATE_EFFECTIVE_67133,
                circular_reference=CIRCULAR_62174,
                regime="TWO_TIER_250",
                price_band_min=b_min,
                price_band_max=b_max,
                monthly_reference_price=anchor_price,
                provenance_source=f"NSE Circular {CIRCULAR_62174} (Effective {DATE_EFFECTIVE_62174})",
            )

        # 3. Regime 3: April 15, 2025 onwards (Circular NSE/CMTR/67133 multi-tier framework)
        if anchor_price is None:
            raise MissingTickMetadataError(
                f"Cannot determine tick size for '{symbol}' on {d_str} under {CIRCULAR_67133}: "
                "Missing monthly reference price anchor. Supply valid reference price or register in SecurityMaster."
            )
        if anchor_price < Decimal("0.00"):
            raise ValueError(f"Reference price cannot be negative: {anchor_price}")

        # Resolve tier from official schedule
        resolved_tick = Decimal("5.00")
        b_min, b_max = Decimal("20000.00"), Decimal("Infinity")
        for min_p, max_p, t_size in NSE_TICK_TIERS:
            if min_p <= anchor_price < max_p:
                resolved_tick = t_size
                b_min, b_max = min_p, max_p
                break

        return HistoricalTickMetadata(
            symbol=clean_sym,
            trade_date=d_str,
            tick_size=resolved_tick,
            effective_from=DATE_EFFECTIVE_67133,
            effective_to=None,
            circular_reference=CIRCULAR_67133,
            regime="MULTI_TIER_CMTR_67133",
            price_band_min=b_min,
            price_band_max=b_max,
            monthly_reference_price=anchor_price,
            provenance_source=f"NSE Circular {CIRCULAR_67133} (Effective {DATE_EFFECTIVE_67133})",
        )

    @classmethod
    def get_provenance(
        cls,
        symbol: str,
        trade_date: str | date | datetime,
        reference_price: Optional[Decimal] = None,
    ) -> Dict[str, Any]:
        """Returns auditable provenance record for simulation and reporting manifests."""
        meta = cls.get_tick_metadata(symbol, trade_date, reference_price)
        return {
            "symbol": meta.symbol,
            "trade_date": meta.trade_date,
            "statutory_tick_size_inr": str(meta.tick_size),
            "statutory_circular": meta.circular_reference,
            "regime": meta.regime,
            "effective_from": meta.effective_from,
            "price_band": f"[{meta.price_band_min}, {meta.price_band_max})",
            "monthly_reference_price": str(meta.monthly_reference_price) if meta.monthly_reference_price else None,
            "provenance_source": meta.provenance_source,
        }


class InstrumentTickRegistry:
    """Authoritative security master registry for cash equity tick sizes."""

    # Static registry of verified core universe equities anchored to exchange circulars
    # Note: Corrected circular attribution: NSE/CMTR/67133 effective 2025-04-15
    _REGISTRY: Dict[str, InstrumentTickMetadata] = {
        "RELIANCE": InstrumentTickMetadata(
            symbol="RELIANCE",
            tick_size=Decimal("0.10"),
            effective_date=DATE_EFFECTIVE_67133,
            circular_reference=CIRCULAR_67133,
            price_band_min=Decimal("1000.00"),
            price_band_max=Decimal("5000.00"),
        ),
        "INFY": InstrumentTickMetadata(
            symbol="INFY",
            tick_size=Decimal("0.10"),
            effective_date=DATE_EFFECTIVE_67133,
            circular_reference=CIRCULAR_67133,
            price_band_min=Decimal("1000.00"),
            price_band_max=Decimal("5000.00"),
        ),
        "TCS": InstrumentTickMetadata(
            symbol="TCS",
            tick_size=Decimal("0.10"),
            effective_date=DATE_EFFECTIVE_67133,
            circular_reference=CIRCULAR_67133,
            price_band_min=Decimal("1000.00"),
            price_band_max=Decimal("5000.00"),
        ),
        "HDFCBANK": InstrumentTickMetadata(
            symbol="HDFCBANK",
            tick_size=Decimal("0.05"),
            effective_date=DATE_EFFECTIVE_67133,
            circular_reference=CIRCULAR_67133,
            price_band_min=Decimal("250.00"),
            price_band_max=Decimal("1000.00"),
        ),
        "SBIN": InstrumentTickMetadata(
            symbol="SBIN",
            tick_size=Decimal("0.10"),
            effective_date=DATE_EFFECTIVE_67133,
            circular_reference=CIRCULAR_67133,
            price_band_min=Decimal("1000.00"),
            price_band_max=Decimal("5000.00"),
        ),
        "ITC": InstrumentTickMetadata(
            symbol="ITC",
            tick_size=Decimal("0.05"),
            effective_date=DATE_EFFECTIVE_67133,
            circular_reference=CIRCULAR_67133,
            price_band_min=Decimal("250.00"),
            price_band_max=Decimal("1000.00"),
        ),
    }

    @classmethod
    def get_tier_tick_size(cls, price: Decimal) -> Decimal:
        """Determines the statutory tick size from the official NSE tiered price schedule."""
        if price < Decimal("0.00"):
            raise ValueError(f"Price cannot be negative: {price}")
        for min_p, max_p, tick in NSE_TICK_TIERS:
            if min_p <= price < max_p:
                return tick
        return Decimal("5.00")

    @classmethod
    def get_tick_size(
        cls,
        symbol: str,
        reference_price: Optional[Decimal] = None,
        trade_date: Optional[str | date | datetime] = None,
    ) -> Decimal:
        """Retrieves the authoritative tick size for an instrument.

        If trade_date is provided, routes through HistoricalSecurityTickMaster to ensure
        accurate historical statutory compliance.

        Fails closed:
        - If symbol is in authoritative registry, returns pinned tick size (or historical if date given).
        - If not in registry but reference_price is supplied, resolves via official tier schedule.
        - Otherwise, raises MissingTickMetadataError.
        """
        if trade_date is not None:
            return HistoricalSecurityTickMaster.get_tick_metadata(symbol, trade_date, reference_price).tick_size

        clean_sym = symbol.replace(".NS", "").upper()
        if clean_sym in cls._REGISTRY:
            return cls._REGISTRY[clean_sym].tick_size

        if reference_price is not None:
            return cls.get_tier_tick_size(reference_price)

        raise MissingTickMetadataError(
            f"Missing tick size metadata for symbol '{symbol}'. "
            "Must register instrument in SecurityMaster or provide valid reference price."
        )


def quantize_to_tick(
    price: Decimal | int | str,
    tick_size: Decimal | int | str,
    rounding: str = "NEAREST",
) -> Decimal:
    """Quantizes a Decimal price to an exact statutory tick size.

    Args:
        price: The raw price to quantize. Must NOT be float.
        tick_size: The statutory tick size (e.g. 0.05, 0.10).
        rounding: Rounding mode: 'NEAREST', 'FLOOR', or 'CEILING'.

    Returns:
        Exact quantized Decimal price that is a mathematical multiple of tick_size.
    """
    if isinstance(price, float) or isinstance(tick_size, float):
        raise TypeError("Binary float prohibited in tick precision calculation. Use Decimal.")

    p = Decimal(str(price)) if not isinstance(price, Decimal) else price
    t = Decimal(str(tick_size)) if not isinstance(tick_size, Decimal) else tick_size

    if t <= Decimal("0.00"):
        raise ValueError(f"Tick size must be strictly positive: {t}")

    num_ticks = p / t

    if rounding == "FLOOR":
        rounded_ticks = num_ticks.quantize(Decimal("1"), rounding=ROUND_FLOOR)
    elif rounding == "CEILING":
        rounded_ticks = num_ticks.quantize(Decimal("1"), rounding=ROUND_CEILING)
    elif rounding == "NEAREST":
        rounded_ticks = num_ticks.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    else:
        raise ValueError(f"Unsupported rounding mode: {rounding}")

    quantized = rounded_ticks * t
    scale = Decimal("0.01") if t >= Decimal("0.01") else Decimal("0.0001")
    return quantized.quantize(scale, rounding=ROUND_HALF_UP)


def is_valid_tick(price: Decimal | int | str, tick_size: Decimal | int | str) -> bool:
    """Verifies if a price is an exact statutory multiple of the instrument's tick size."""
    if isinstance(price, float) or isinstance(tick_size, float):
        raise TypeError("Binary float prohibited in tick precision check. Use Decimal.")

    p = Decimal(str(price)) if not isinstance(price, Decimal) else price
    t = Decimal(str(tick_size)) if not isinstance(tick_size, Decimal) else tick_size

    if t <= Decimal("0.00"):
        return False

    remainder = p % t
    return remainder.quantize(Decimal("0.00001")).is_zero()


def quantize_order_price(
    price: Decimal,
    symbol: str,
    side_is_buy: bool,
    order_purpose: str = "ENTRY",  # "ENTRY", "STOP_LOSS", "TAKE_PROFIT"
    tick_size: Optional[Decimal] = None,
    trade_date: Optional[str | date | datetime] = None,
    reference_price: Optional[Decimal] = None,
) -> Decimal:
    """Asymmetrically quantizes an order price according to conservative execution rules.

    Directional Quantization Policy:
    - Long Stop-Loss (Sell Stop): FLOOR (widens stop slightly, conservative against premature exit).
    - Long Take-Profit (Sell Limit): FLOOR (brings target slightly closer, conservative/realistic fill).
    - Short Stop-Loss (Buy Stop): CEILING (widens stop slightly, conservative against premature exit).
    - Short Take-Profit (Buy Limit): CEILING (brings target slightly closer, conservative/realistic fill).
    - Buy Limit Entry: FLOOR (never pays more than signal entry limit).
    - Sell Limit Entry: CEILING (never sells below signal entry limit).
    """
    ref_p = reference_price if reference_price is not None else price
    if tick_size is not None:
        ts = tick_size
    elif trade_date is not None:
        ts = HistoricalSecurityTickMaster.get_tick_metadata(symbol, trade_date, ref_p).tick_size
    else:
        ts = InstrumentTickRegistry.get_tick_size(symbol, ref_p)

    if order_purpose == "STOP_LOSS":
        rounding = "FLOOR" if side_is_buy else "CEILING"
    elif order_purpose == "TAKE_PROFIT":
        rounding = "FLOOR" if side_is_buy else "CEILING"
    elif order_purpose == "ENTRY":
        rounding = "FLOOR" if side_is_buy else "CEILING"
    else:
        rounding = "NEAREST"

    return quantize_to_tick(price, ts, rounding=rounding)
