"""Regression tests for monetary precision and serialization.

Validates that:
1. All monetary fields are serialized as fixed-scale decimal strings.
2. No JSON monetary field is serialized as a binary floating-point number.
3. Decimal values round consistently under the documented ROUND_HALF_UP rounding policy.
4. Zero, negative values, fees, slippage, cash, realized P&L, and position values retain exact representations.
5. Serialization does not mutate underlying Decimal ledger balances or trading state.
6. Forward runner snapshot generation produces schema-compatible, float-free JSON payloads.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import tempfile
import pytest

from src.core.monetary import (
    DEFAULT_MONETARY_PRECISION,
    MONETARY_ROUNDING,
    assert_no_binary_floats_in_payload,
    format_monetary_decimal,
    is_valid_monetary_string,
    quantize_monetary_decimal,
)
from src.scanner.scanner import MarketBar


class TestMonetarySerialization:
    """Verifies strict monetary string serialization and float prohibition."""

    def test_fixed_scale_string_formatting_and_rounding(self):
        """Validates formatting and ROUND_HALF_UP behavior on positive, zero, and negative values."""
        # Clean exact values
        assert format_monetary_decimal(Decimal("100000.00")) == "100000.00"
        assert format_monetary_decimal(Decimal("0.00")) == "0.00"
        assert format_monetary_decimal(Decimal("0")) == "0.00"
        assert format_monetary_decimal(Decimal("-127.49")) == "-127.49"

        # Rounding edge cases under ROUND_HALF_UP
        assert format_monetary_decimal(Decimal("1202.055")) == "1202.06"
        assert format_monetary_decimal(Decimal("1202.054")) == "1202.05"
        assert format_monetary_decimal(Decimal("-1202.055")) == "-1202.06"
        assert format_monetary_decimal(Decimal("-1202.054")) == "-1202.05"
        assert format_monetary_decimal(Decimal("0.005")) == "0.01"
        assert format_monetary_decimal(Decimal("0.004")) == "0.00"
        assert format_monetary_decimal(Decimal("-0.005")) == "-0.01"
        assert format_monetary_decimal(Decimal("-0.004")) == "0.00"

        # From clean integer or string input
        assert format_monetary_decimal(100000) == "100000.00"
        assert format_monetary_decimal("543.21") == "543.21"

    def test_binary_float_prohibited_as_monetary_input(self):
        """Validates that passing binary float raises TypeError immediately to prevent precision corruption."""
        with pytest.raises(TypeError, match="Binary floating-point value.*prohibited"):
            format_monetary_decimal(100.5)

        with pytest.raises(TypeError, match="Binary floating-point value.*prohibited"):
            quantize_monetary_decimal(0.0)

    def test_monetary_string_validator(self):
        """Tests the pattern matcher for fixed-scale strings."""
        assert is_valid_monetary_string("100000.00") is True
        assert is_valid_monetary_string("0.00") is True
        assert is_valid_monetary_string("-127.49") is True
        assert is_valid_monetary_string("12.50") is True

        # Invalid formats
        assert is_valid_monetary_string("100") is False  # Missing decimal places
        assert is_valid_monetary_string("100.5") is False  # Single decimal place
        assert is_valid_monetary_string("100.505") is False  # 3 decimal places
        assert is_valid_monetary_string(100.00) is False  # Not a string
        assert is_valid_monetary_string(None) is False

    def test_assert_no_binary_floats_in_payload_passes_for_clean_strings(self):
        """Ensures compliant payload with string money passes assertion."""
        clean_payload = {
            "financials": {
                "initial_capital_inr": "100000.00",
                "cash_inr": "99524.36",
                "current_equity_inr": "99524.36",
                "gross_realized_pnl_inr": "-252.40",
                "total_statutory_fees_inr": "123.24",
                "total_slippage_inr": "100.00",
                "total_friction_inr": "223.24",
                "net_realized_pnl_inr": "-475.64",
            },
            "completed_trades": [
                {
                    "symbol": "RELIANCE",
                    "entry_price": "1200.00",
                    "exit_price": "1195.00",
                    "gross_pnl_inr": "-250.00",
                    "fees_inr": "50.00",
                    "slippage_inr": "20.00",
                    "net_pnl_inr": "-320.00",
                }
            ],
            "cycle_count": 42,  # integer is allowed
            "mode": "PAPER",  # string is allowed
        }
        assert_no_binary_floats_in_payload(clean_payload)

    def test_assert_no_binary_floats_in_payload_detects_float_leaks(self):
        """Ensures that any binary float in a monetary key raises an AssertionError."""
        leaky_payload = {
            "financials": {
                "initial_capital_inr": "100000.00",
                "cash_inr": 99524.36,  # FLOAT LEAK
            }
        }
        with pytest.raises(AssertionError, match="Prohibited binary float found in monetary field"):
            assert_no_binary_floats_in_payload(leaky_payload)

    def test_assert_no_binary_floats_detects_nested_float_in_trades(self):
        """Ensures that float leaks nested inside lists of completed trades are caught."""
        leaky_payload = {
            "completed_trades": [
                {
                    "symbol": "INFY",
                    "entry_price": "1130.00",
                    "exit_price": 1135.50,  # FLOAT LEAK
                }
            ]
        }
        with pytest.raises(AssertionError, match="Prohibited binary float found in monetary field"):
            assert_no_binary_floats_in_payload(leaky_payload)

    def test_serialization_does_not_mutate_state_or_ledger(self):
        """Verifies that string serialization preserves exact mathematical conservation."""
        initial_cash = Decimal("100000.00")
        current_cash = Decimal("95000.00")
        stock_value = Decimal("4800.00")
        fees = Decimal("150.00")
        slippage = Decimal("50.00")
        net_pnl = (current_cash + stock_value + fees + slippage) - initial_cash  # = 0.00

        # Create serialized dictionary
        report = {
            "initial_cash_inr": format_monetary_decimal(initial_cash),
            "cash_inr": format_monetary_decimal(current_cash),
            "stock_value_inr": format_monetary_decimal(stock_value),
            "fees_inr": format_monetary_decimal(fees),
            "slippage_inr": format_monetary_decimal(slippage),
            "net_pnl_inr": format_monetary_decimal(net_pnl),
        }

        # Assert no mutation of original Decimals
        assert initial_cash == Decimal("100000.00")
        assert current_cash == Decimal("95000.00")

        # Parse back from serialized strings and verify double-entry conservation bit-for-bit
        deserialized_initial = Decimal(report["initial_cash_inr"])
        deserialized_cash = Decimal(report["cash_inr"])
        deserialized_stock = Decimal(report["stock_value_inr"])
        deserialized_fees = Decimal(report["fees_inr"])
        deserialized_slippage = Decimal(report["slippage_inr"])
        deserialized_net_pnl = Decimal(report["net_pnl_inr"])

        balance_delta = (
            deserialized_cash
            + deserialized_stock
            + deserialized_fees
            + deserialized_slippage
            - deserialized_net_pnl
            - deserialized_initial
        )
        assert balance_delta == Decimal("0.00")

    def test_forward_session_snapshot_float_free_schema(self, monkeypatch):
        """Tests that ForwardPaperTradingSession snapshot produces 100% float-free JSON."""
        # Use an isolated temp directory for SQLite and snapshot
        import importlib.util

        script_path = (
            Path(__file__).resolve().parents[2]
            / "scripts"
            / "trial-20261019"
            / "run_forward_paper_trading.py"
        )
        spec = importlib.util.spec_from_file_location("run_forward_paper_trading", script_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ForwardPaperTradingSession = mod.ForwardPaperTradingSession

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            session = ForwardPaperTradingSession(
                initial_capital=Decimal("100000.00"),
                data_dir=tmp_path / "data",
                reports_dir=tmp_path / "reports",
                logs_dir=tmp_path / "logs",
            )

            # Synthetic bars
            synthetic_bars = {
                "RELIANCE": MarketBar(
                    symbol="RELIANCE",
                    timestamp_iso="2026-10-19T15:25:00+05:30",
                    open=Decimal("1200.00"),
                    high=Decimal("1205.00"),
                    low=Decimal("1198.00"),
                    close=Decimal("1202.00"),
                    volume=50000,
                )
            }

            # Generate snapshot
            session.write_snapshot(synthetic_bars)

            # Read back snapshot file
            with open(tmp_path / "reports" / "forward_trading_live_snapshot.json", "r") as f:
                data = json.load(f)

            # Assert all monetary keys are string type and match pattern
            assert isinstance(data["financials"]["initial_capital_inr"], str)
            assert data["financials"]["initial_capital_inr"] == "100000.00"
            assert isinstance(data["financials"]["cash_inr"], str)
            assert data["financials"]["cash_inr"] == "100000.00"
            assert isinstance(data["financials"]["net_realized_pnl_inr"], str)
            assert data["financials"]["net_realized_pnl_inr"] == "0.00"

            # Quotes
            assert isinstance(data["latest_quotes"]["RELIANCE"]["close"], str)
            assert data["latest_quotes"]["RELIANCE"]["close"] == "1202.00"

            # Non-monetary types preserved
            assert isinstance(data["cycle_count"], int)
            assert isinstance(data["safety_controls"]["PAPER_BROKER_ONLY"], bool)

            # Full payload validation
            assert_no_binary_floats_in_payload(data)
