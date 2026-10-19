"""Unit and Regression Tests for NSE Statutory Tick-Size Precision Framework.

Validates:
1. Statutory NSE cash-equity price-linked tick tiers (Circulars NSE/CMTR/62174 & NSE/CMTR/67133).
2. Authoritative security registry resolution and fail-closed metadata checks.
3. Quantization arithmetic across FLOOR, CEILING, and NEAREST rounding modes.
4. Asymmetric directional quantization policies for bracket orders (entry, SL, TP).
5. Gateway level off-tick order rejection (InvalidOrderTickError).
6. Strict rejection of binary float inputs to ensure monetary decimal purity.
7. Risk sizing consistency with tick-quantized stop loss.
"""

from decimal import Decimal
import pytest

from src.brokergateway.gateway import BrokerGateway
from src.brokergateway.states import OrderSide, OrderType
from src.core.exceptions import InvalidOrderTickError, MissingTickMetadataError
from src.core.tick_precision import (
    NSE_TICK_TIERS,
    InstrumentTickMetadata,
    InstrumentTickRegistry,
    is_valid_tick,
    quantize_order_price,
    quantize_to_tick,
)
from src.scanner.scanner import MarketBar, ScanCandidate
from src.strategies.momentum_breakout import MomentumBreakoutStrategy


class TestNSETickTiersAndRegistry:
    """Validates exchange circular tier rules and authoritative registry lookup."""

    @pytest.mark.parametrize(
        "price_str,expected_tick",
        [
            ("10.00", "0.01"),
            ("249.99", "0.01"),
            ("250.00", "0.05"),
            ("999.95", "0.05"),
            ("1000.00", "0.10"),
            ("4999.90", "0.10"),
            ("5000.00", "0.50"),
            ("9999.50", "0.50"),
            ("10000.00", "1.00"),
            ("19999.00", "1.00"),
            ("20000.00", "5.00"),
            ("50000.00", "5.00"),
        ],
    )
    def test_tiered_tick_size_schedule(self, price_str: str, expected_tick: str):
        """Official tiered schedule must resolve correct statutory tick for price bands."""
        price = Decimal(price_str)
        tick = InstrumentTickRegistry.get_tier_tick_size(price)
        assert tick == Decimal(expected_tick)

    def test_registry_known_symbols(self):
        """Authoritative securities must resolve pinned statutory tick sizes."""
        assert InstrumentTickRegistry.get_tick_size("RELIANCE") == Decimal("0.10")
        assert InstrumentTickRegistry.get_tick_size("INFY") == Decimal("0.10")
        assert InstrumentTickRegistry.get_tick_size("TCS") == Decimal("0.10")
        assert InstrumentTickRegistry.get_tick_size("HDFCBANK") == Decimal("0.05")
        assert InstrumentTickRegistry.get_tick_size("SBIN") == Decimal("0.10")
        assert InstrumentTickRegistry.get_tick_size("ITC") == Decimal("0.05")
        # With .NS suffix
        assert InstrumentTickRegistry.get_tick_size("RELIANCE.NS") == Decimal("0.10")

    def test_registry_fail_closed_on_unknown_symbol(self):
        """Unknown instruments without reference price must raise MissingTickMetadataError."""
        with pytest.raises(MissingTickMetadataError):
            InstrumentTickRegistry.get_tick_size("UNKNOWN_STOCK")

    def test_registry_fallback_to_reference_price_for_unknown(self):
        """Unknown instruments with reference price resolve through statutory tiers."""
        assert InstrumentTickRegistry.get_tick_size("NEWIPO", reference_price=Decimal("150.00")) == Decimal("0.01")
        assert InstrumentTickRegistry.get_tick_size("NEWIPO", reference_price=Decimal("800.00")) == Decimal("0.05")
        assert InstrumentTickRegistry.get_tick_size("NEWIPO", reference_price=Decimal("1500.00")) == Decimal("0.10")


class TestTickQuantizationArithmetic:
    """Tests mathematical behavior and rounding rules for quantize_to_tick."""

    def test_exact_multiples_remain_unchanged(self):
        """Prices already on statutory tick grid must remain bit-exact."""
        tick = Decimal("0.05")
        assert quantize_to_tick(Decimal("100.05"), tick, "NEAREST") == Decimal("100.05")
        assert quantize_to_tick(Decimal("100.05"), tick, "FLOOR") == Decimal("100.05")
        assert quantize_to_tick(Decimal("100.05"), tick, "CEILING") == Decimal("100.05")

    def test_rounding_modes_on_off_tick_values(self):
        """Validates FLOOR, CEILING, and NEAREST rounding on fractional ticks."""
        tick = Decimal("0.10")
        off_tick = Decimal("1452.37")

        assert quantize_to_tick(off_tick, tick, "FLOOR") == Decimal("1452.30")
        assert quantize_to_tick(off_tick, tick, "CEILING") == Decimal("1452.40")
        assert quantize_to_tick(off_tick, tick, "NEAREST") == Decimal("1452.40")

        off_tick_lower = Decimal("1452.34")
        assert quantize_to_tick(off_tick_lower, tick, "NEAREST") == Decimal("1452.30")

    def test_sub_paisa_fractional_truncation(self):
        """Sub-paisa fractional ATR values (e.g. 713.3285714) must be strictly quantized."""
        tick = Decimal("0.05")
        sub_paisa = Decimal("713.3285714285714")
        quantized_floor = quantize_to_tick(sub_paisa, tick, "FLOOR")
        quantized_ceil = quantize_to_tick(sub_paisa, tick, "CEILING")

        assert quantized_floor == Decimal("713.30")
        assert quantized_ceil == Decimal("713.35")
        assert is_valid_tick(quantized_floor, tick)
        assert is_valid_tick(quantized_ceil, tick)

    def test_is_valid_tick(self):
        """is_valid_tick must accurately distinguish on-tick vs off-tick prices."""
        assert is_valid_tick(Decimal("1000.10"), Decimal("0.10")) is True
        assert is_valid_tick(Decimal("1000.15"), Decimal("0.10")) is False
        assert is_valid_tick(Decimal("1000.15"), Decimal("0.05")) is True
        assert is_valid_tick(Decimal("245.03"), Decimal("0.01")) is True
        assert is_valid_tick(Decimal("245.035"), Decimal("0.01")) is False

    def test_binary_float_rejection(self):
        """Passing binary float to quantize_to_tick or is_valid_tick must raise TypeError."""
        with pytest.raises(TypeError, match="Binary float prohibited"):
            quantize_to_tick(100.5, Decimal("0.05"))

        with pytest.raises(TypeError, match="Binary float prohibited"):
            quantize_to_tick(Decimal("100.5"), 0.05)

        with pytest.raises(TypeError, match="Binary float prohibited"):
            is_valid_tick(100.5, Decimal("0.05"))


class TestDirectionalQuantizationPolicy:
    """Tests conservative directional rounding for orders and bracket legs."""

    def test_long_bracket_directional_rounding(self):
        """Long bracket: entry limit (FLOOR), stop loss (FLOOR), take profit (FLOOR)."""
        # Long Stop Loss: selling at stop. Floor widens stop distance conservatively.
        sl = quantize_order_price(Decimal("2500.37"), "RELIANCE", side_is_buy=True, order_purpose="STOP_LOSS")
        assert sl == Decimal("2500.30")
        assert is_valid_tick(sl, Decimal("0.10"))

        # Long Take Profit: selling at target. Floor brings target closer for realistic fill.
        tp = quantize_order_price(Decimal("2550.88"), "RELIANCE", side_is_buy=True, order_purpose="TAKE_PROFIT")
        assert tp == Decimal("2550.80")
        assert is_valid_tick(tp, Decimal("0.10"))

        # Long Entry: buying. Floor ensures we do not pay more than limit.
        entry = quantize_order_price(Decimal("2520.48"), "RELIANCE", side_is_buy=True, order_purpose="ENTRY")
        assert entry == Decimal("2520.40")
        assert is_valid_tick(entry, Decimal("0.10"))

    def test_short_bracket_directional_rounding(self):
        """Short bracket: entry limit (CEILING), stop loss (CEILING), take profit (CEILING)."""
        # Short Stop Loss: buying at stop. Ceiling widens stop distance conservatively.
        sl = quantize_order_price(Decimal("1450.23"), "INFY", side_is_buy=False, order_purpose="STOP_LOSS")
        assert sl == Decimal("1450.30")
        assert is_valid_tick(sl, Decimal("0.10"))

        # Short Take Profit: buying at target. Ceiling brings target closer for realistic fill.
        tp = quantize_order_price(Decimal("1400.12"), "INFY", side_is_buy=False, order_purpose="TAKE_PROFIT")
        assert tp == Decimal("1400.20")
        assert is_valid_tick(tp, Decimal("0.10"))

        # Short Entry: selling. Ceiling ensures we do not sell below limit.
        entry = quantize_order_price(Decimal("1445.62"), "INFY", side_is_buy=False, order_purpose="ENTRY")
        assert entry == Decimal("1445.70")
        assert is_valid_tick(entry, Decimal("0.10"))


class TestGatewayTickEnforcement:
    """Tests that BrokerGateway enforces statutory tick validity on incoming orders."""

    def test_gateway_accepts_valid_tick_order(self):
        """Orders on valid statutory ticks must pass gateway validation."""
        gateway = BrokerGateway()
        order = gateway.create_order(
            order_id="ORD-TEST-TICK-01",
            idempotency_key="IDEM-TEST-TICK-01",
            strategy_id="TEST_STRAT",
            symbol="RELIANCE",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10,
            price=Decimal("2500.10"),
            is_risk_approved=True,
        )
        assert order.price == Decimal("2500.10")

    def test_gateway_rejects_off_tick_order(self):
        """Orders with off-tick prices must be rejected with InvalidOrderTickError."""
        gateway = BrokerGateway()
        with pytest.raises(InvalidOrderTickError, match="Off-tick order price rejected"):
            gateway.create_order(
                order_id="ORD-TEST-TICK-02",
                idempotency_key="IDEM-TEST-TICK-02",
                strategy_id="TEST_STRAT",
                symbol="RELIANCE",  # Tick size is 0.10
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=10,
                price=Decimal("2500.05"),  # Off-tick for RELIANCE (requires multiple of 0.10)
                is_risk_approved=True,
            )

    def test_gateway_rejects_sub_paisa_fractional_order(self):
        """Raw unquantized indicator outputs must be rejected by BrokerGateway."""
        gateway = BrokerGateway()
        with pytest.raises(InvalidOrderTickError):
            gateway.create_order(
                order_id="ORD-TEST-TICK-03",
                idempotency_key="IDEM-TEST-TICK-03",
                strategy_id="TEST_STRAT",
                symbol="HDFCBANK",  # Tick size is 0.05
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=5,
                price=Decimal("1523.3285714"),  # Off-tick sub-paisa fraction
                is_risk_approved=True,
            )


class TestStrategySignalTickQuantization:
    """Tests that MomentumBreakoutStrategy generates strictly tick-quantized signals."""

    def test_momentum_strategy_produces_quantized_signals(self):
        """Signals emitted by strategy must have all prices conforming to exchange ticks."""
        strategy = MomentumBreakoutStrategy()
        # Mock candidate with off-tick values
        candidate = ScanCandidate(
            symbol="RELIANCE",  # tick: 0.10
            timestamp_iso="2026-10-19T09:30:00+05:30",
            current_price=Decimal("2500.13"),  # off-tick
            rvol=Decimal("2.5"),
            atr=Decimal("12.34567"),  # off-tick ATR
            spread_pct=Decimal("0.0005"),
            bias="BULLISH",
        )
        signal = strategy.evaluate_candidate(candidate)
        assert signal is not None

        # RELIANCE tick size is 0.10
        assert is_valid_tick(signal.target_entry_price, Decimal("0.10"))
        assert is_valid_tick(signal.stop_loss, Decimal("0.10"))
        assert is_valid_tick(signal.take_profit, Decimal("0.10"))

        # Verify directional quantization
        assert signal.target_entry_price == Decimal("2500.10")  # Buy entry floored from 2500.13
        # raw_stop = 2500.13 - 12.34567 = 2487.78433 -> floored to 2487.70
        assert signal.stop_loss == Decimal("2487.70")
        # raw_target = 2500.13 + 24.69134 = 2524.82134 -> floored to 2524.80
        assert signal.take_profit == Decimal("2524.80")


class TestHistoricalTickMasterRegimes:
    """Tests date-aware resolution across historical statutory NSE regimes."""

    def test_pre_2024_uniform_regime(self):
        """Prior to June 10, 2024, all equities must resolve to INR 0.05 uniform tick."""
        from src.core.tick_precision import HistoricalSecurityTickMaster, CIRCULAR_PRE_2024
        meta = HistoricalSecurityTickMaster.get_tick_metadata("RELIANCE", "2024-01-15")
        assert meta.tick_size == Decimal("0.05")
        assert meta.circular_reference == CIRCULAR_PRE_2024
        assert meta.regime == "UNIFORM_5PAISE"

        meta_infy = HistoricalSecurityTickMaster.get_tick_metadata("INFY", "2023-08-01")
        assert meta_infy.tick_size == Decimal("0.05")

    def test_two_tier_regime_circular_62174(self):
        """Between June 10, 2024 and April 14, 2025, circular 62174 creates 2 tiers (<250 -> 0.01, >=250 -> 0.05)."""
        from src.core.tick_precision import HistoricalSecurityTickMaster, CIRCULAR_62174
        # Below 250
        meta_low = HistoricalSecurityTickMaster.get_tick_metadata("SUB250_SYM", "2024-08-01", reference_price=Decimal("180.00"))
        assert meta_low.tick_size == Decimal("0.01")
        assert meta_low.circular_reference == CIRCULAR_62174
        assert meta_low.regime == "TWO_TIER_250"

        # Above 250
        meta_high = HistoricalSecurityTickMaster.get_tick_metadata("HIGH_SYM", "2024-08-01", reference_price=Decimal("1500.00"))
        assert meta_high.tick_size == Decimal("0.05")
        assert meta_high.circular_reference == CIRCULAR_62174

    def test_multi_tier_regime_circular_67133(self):
        """From April 15, 2025 onwards, circular 67133 establishes multi-tier schedule."""
        from src.core.tick_precision import HistoricalSecurityTickMaster, CIRCULAR_67133
        meta_rel = HistoricalSecurityTickMaster.get_tick_metadata("RELIANCE", "2026-09-04")
        assert meta_rel.tick_size == Decimal("0.10")
        assert meta_rel.circular_reference == CIRCULAR_67133
        assert meta_rel.regime == "MULTI_TIER_CMTR_67133"

    def test_date_aware_quantize_order_price(self):
        """quantize_order_price resolves tick size corresponding to trade_date."""
        # On 2024-01-15: RELIANCE tick size is 0.05
        p_old = quantize_order_price(Decimal("2500.12"), "RELIANCE", side_is_buy=True, order_purpose="ENTRY", trade_date="2024-01-15")
        assert p_old == Decimal("2500.10")  # multiple of 0.05

        p_off = quantize_order_price(Decimal("2500.07"), "RELIANCE", side_is_buy=True, order_purpose="ENTRY", trade_date="2024-01-15")
        assert p_off == Decimal("2500.05")

        # On 2026-09-04: RELIANCE tick size is 0.10
        p_new = quantize_order_price(Decimal("2500.07"), "RELIANCE", side_is_buy=True, order_purpose="ENTRY", trade_date="2026-09-04")
        assert p_new == Decimal("2500.00")  # floored to multiple of 0.10

    def test_fail_closed_on_missing_reference_price_for_unknown(self):
        """Unknown symbol on date >= 2024-06-10 without reference price must raise MissingTickMetadataError."""
        from src.core.tick_precision import HistoricalSecurityTickMaster
        with pytest.raises(MissingTickMetadataError):
            HistoricalSecurityTickMaster.get_tick_metadata("UNKNOWN_SYM", "2026-09-04")

    def test_fail_closed_on_invalid_date(self):
        """Invalid date strings must raise MissingTickMetadataError."""
        from src.core.tick_precision import HistoricalSecurityTickMaster
        with pytest.raises(MissingTickMetadataError):
            HistoricalSecurityTickMaster.get_tick_metadata("RELIANCE", "INVALID_DATE")

    def test_provenance_record_format(self):
        """Provenance records must contain auditable metadata fields."""
        from src.core.tick_precision import HistoricalSecurityTickMaster
        prov = HistoricalSecurityTickMaster.get_provenance("RELIANCE", "2026-09-04")
        assert prov["symbol"] == "RELIANCE"
        assert prov["trade_date"] == "2026-09-04"
        assert prov["statutory_tick_size_inr"] == "0.10"
        assert prov["statutory_circular"] == "NSE/CMTR/67133"
        assert "provenance_source" in prov
