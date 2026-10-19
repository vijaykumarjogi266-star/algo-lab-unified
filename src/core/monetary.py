"""Algo Lab Unified — Monetary Precision & Serialization Standards.

Enforces:
1. Pure Decimal arithmetic for all monetary values, prices, fees, slippage, and PnL.
2. Complete prohibition of binary floating-point (float) types in monetary representations.
3. Fixed-scale decimal string serialization (e.g., "100000.00") with ROUND_HALF_UP rounding policy.
4. Schema verification utilities ensuring reports and JSON payloads contain zero float leaks.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
import re
from typing import Any, Mapping, Sequence, Set

MONETARY_ROUNDING = ROUND_HALF_UP
DEFAULT_MONETARY_PRECISION = 2

# Standard regex for fixed-scale decimal string with exactly N decimal places
MONETARY_STRING_PATTERN = re.compile(r"^-?\d+\.\d{2}$")

KNOWN_MONETARY_KEYS: Set[str] = {
    "initial_capital_inr",
    "initial_virtual_capital_inr",
    "cash_inr",
    "current_equity_inr",
    "ending_cash_inr",
    "peak_equity_inr",
    "gross_realized_pnl_inr",
    "total_statutory_fees_inr",
    "total_slippage_inr",
    "total_friction_inr",
    "net_realized_pnl_inr",
    "gross_pnl_inr",
    "net_pnl_inr",
    "fees_inr",
    "statutory_fees_inr",
    "slippage_inr",
    "friction_inr",
    "entry_price",
    "exit_price",
    "current_price",
    "target_entry_price",
    "stop_loss",
    "take_profit",
    "close",
    "open",
    "high",
    "low",
    "price",
}


def format_monetary_decimal(val: Decimal | int | str, precision: int = DEFAULT_MONETARY_PRECISION) -> str:
    """Formats a Decimal, integer, or clean numeric string into a fixed-scale decimal string.

    Args:
        val: The monetary quantity to format. Must NOT be a binary float.
        precision: Number of decimal places (default: 2).

    Returns:
        Fixed-scale string representation, e.g. "100000.00", "0.00", "-127.49".

    Raises:
        TypeError: If val is a binary float (to prevent floating-point precision corruption).
        ValueError: If val cannot be parsed as a valid Decimal.
    """
    if isinstance(val, float):
        raise TypeError(
            f"Binary floating-point value {val!r} prohibited for monetary representation. "
            "Use Python Decimal, integer paise/cents, or exact numeric string."
        )

    if not isinstance(val, Decimal):
        val = Decimal(str(val))

    quantizer = Decimal(10) ** -precision
    quantized = val.quantize(quantizer, rounding=MONETARY_ROUNDING)
    if quantized.is_zero():
        quantized = Decimal(0).quantize(quantizer)
    return str(quantized)


def quantize_monetary_decimal(val: Decimal | int | str, precision: int = DEFAULT_MONETARY_PRECISION) -> Decimal:
    """Quantizes a Decimal, integer, or clean numeric string to fixed precision using ROUND_HALF_UP.

    Args:
        val: The monetary quantity to quantize. Must NOT be a binary float.
        precision: Number of decimal places (default: 2).

    Returns:
        Quantized Decimal instance with normalized zero sign.

    Raises:
        TypeError: If val is a binary float.
    """
    if isinstance(val, float):
        raise TypeError(
            f"Binary floating-point value {val!r} prohibited for monetary representation. "
            "Use Python Decimal, integer paise/cents, or exact numeric string."
        )

    if not isinstance(val, Decimal):
        val = Decimal(str(val))

    quantizer = Decimal(10) ** -precision
    quantized = val.quantize(quantizer, rounding=MONETARY_ROUNDING)
    if quantized.is_zero():
        quantized = Decimal(0).quantize(quantizer)
    return quantized


def is_valid_monetary_string(val: Any, precision: int = DEFAULT_MONETARY_PRECISION) -> bool:
    """Validates whether a value is a valid fixed-scale decimal string."""
    if not isinstance(val, str):
        return False
    if precision == 2:
        return bool(MONETARY_STRING_PATTERN.match(val))
    pattern = re.compile(rf"^-?\d+\.\d{{{precision}}}$")
    return bool(pattern.match(val))


def assert_no_binary_floats_in_payload(
    payload: Any,
    monetary_keys: Set[str] | None = None,
    path: str = "",
) -> None:
    """Recursively validates a JSON-serializable structure to ensure monetary fields are not floats.

    Args:
        payload: Dict, list, or primitive to validate.
        monetary_keys: Set of field names considered monetary. Defaults to KNOWN_MONETARY_KEYS.
        path: Path prefix for diagnostic error reporting.

    Raises:
        AssertionError: If any monetary field is represented as a float or if float leaks are found.
    """
    target_keys = monetary_keys if monetary_keys is not None else KNOWN_MONETARY_KEYS

    if isinstance(payload, Mapping):
        for k, v in payload.items():
            current_path = f"{path}.{k}" if path else str(k)
            key_lower = str(k).lower()
            is_monetary = key_lower in target_keys or key_lower.endswith("_inr") or key_lower.endswith("_price")

            if is_monetary:
                if isinstance(v, float):
                    raise AssertionError(
                        f"Prohibited binary float found in monetary field '{current_path}': {v!r}. "
                        "Must be serialized as a fixed-scale decimal string (e.g. '100000.00')."
                    )
                if isinstance(v, str) and not is_valid_monetary_string(v):
                    raise AssertionError(
                        f"Invalid monetary string format in '{current_path}': {v!r}. "
                        "Must match fixed-scale pattern '^-?\\d+\\.\\d{2}$'."
                    )

            assert_no_binary_floats_in_payload(v, target_keys, current_path)

    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        for idx, item in enumerate(payload):
            current_path = f"{path}[{idx}]"
            assert_no_binary_floats_in_payload(item, target_keys, current_path)
