"""Negative Security and Boundary Verification Tests.

Proves:
1. Invariant violations fail closed immediately.
2. Contradictory or malformed configs fail closed.
3. Live broker venues are strictly rejected.
4. Unapproved order intents are rejected before execution.
5. Path containment blocks writes to canonical or external paths.
"""

from __future__ import annotations

import os
from pathlib import Path
import pytest

from src.core.exceptions import (
    BrokerExecutionBlockedError,
    MalformedConfigurationError,
    RiskApprovalBypassError,
    SecurityInvariantViolation,
    WorkspaceIsolationViolation,
)
from src.core.safety_invariants import (
    WORKSPACE_ROOT,
    assert_paper_only_invariants,
    assert_within_workspace,
    guard_broker_dispatch,
    validate_effective_configuration,
)
from src.brokergateway.safety_guard import OrderDispatchSafetyGuard


class TestPaperOnlyInvariants:
    """Tests enforcement of the 4 non-negotiable safety invariants."""

    def test_pass_on_default_clean_invariants(self):
        assert_paper_only_invariants({
            "ALLOW_LIVE_TRADING": "false",
            "ALLOW_REAL_BROKER_EXECUTION": "false",
            "REAL_MONEY_RISK_PAISE": "0",
            "PAPER_BROKER_ONLY": "true",
        })

    def test_reject_live_trading_true(self):
        with pytest.raises(SecurityInvariantViolation, match="ALLOW_LIVE_TRADING is 'true'"):
            assert_paper_only_invariants({"ALLOW_LIVE_TRADING": "true"})

    def test_reject_real_broker_execution_true(self):
        with pytest.raises(SecurityInvariantViolation, match="ALLOW_REAL_BROKER_EXECUTION is 'true'"):
            assert_paper_only_invariants({"ALLOW_REAL_BROKER_EXECUTION": "true"})

    def test_reject_real_money_risk_non_zero(self):
        with pytest.raises(SecurityInvariantViolation, match="REAL_MONEY_RISK_PAISE is 500"):
            assert_paper_only_invariants({"REAL_MONEY_RISK_PAISE": 500})

    def test_reject_paper_broker_only_false(self):
        with pytest.raises(SecurityInvariantViolation, match="PAPER_BROKER_ONLY is 'false'"):
            assert_paper_only_invariants({"PAPER_BROKER_ONLY": "false"})

    def test_reject_malformed_risk_value(self):
        with pytest.raises(MalformedConfigurationError, match="Malformed REAL_MONEY_RISK_PAISE"):
            assert_paper_only_invariants({"REAL_MONEY_RISK_PAISE": "NOT_AN_INT"})


class TestConfigurationValidation:
    """Tests fail-closed handling of contradictory configurations."""

    def test_reject_contradictory_live_trading_flag(self):
        with pytest.raises(MalformedConfigurationError, match="Contradictory configuration detected"):
            validate_effective_configuration({
                "ALLOW_LIVE_TRADING": "false",
                "ALLOW_REAL_BROKER_EXECUTION": "false",
                "REAL_MONEY_RISK_PAISE": "0",
                "PAPER_BROKER_ONLY": "true",
                "LIVE_TRADING_ENABLED": "true",
            })

    def test_reject_live_execution_environment(self):
        with pytest.raises(MalformedConfigurationError, match="Contradictory configuration detected"):
            validate_effective_configuration({
                "ALLOW_LIVE_TRADING": "false",
                "ALLOW_REAL_BROKER_EXECUTION": "false",
                "REAL_MONEY_RISK_PAISE": "0",
                "PAPER_BROKER_ONLY": "true",
                "EXECUTION_ENVIRONMENT": "LIVE",
            })


class TestBrokerVenueRejection:
    """Tests rejection of live broker execution paths."""

    def test_allow_paper_venue(self):
        guard_broker_dispatch("PAPER", "SUBMIT_ORDER")
        guard_broker_dispatch("SIMULATED", "SUBMIT_ORDER")

    def test_reject_live_zerodha_venue(self):
        with pytest.raises(BrokerExecutionBlockedError, match="Venue 'ZERODHA' is not an authorized"):
            guard_broker_dispatch("ZERODHA", "SUBMIT_ORDER")

    def test_reject_live_angelone_venue(self):
        with pytest.raises(BrokerExecutionBlockedError, match="Venue 'SMARTAPI' is not an authorized"):
            guard_broker_dispatch("SMARTAPI", "SUBMIT_ORDER")


class TestOrderDispatchSafetyGuard:
    """Tests pre-submission safety and risk approval gates."""

    def test_reject_order_without_risk_approval(self):
        intent = {
            "order_id": "ORD-001",
            "symbol": "RELIANCE",
            "venue": "PAPER",
            "is_risk_approved": False,
        }
        with pytest.raises(RiskApprovalBypassError, match="did not receive explicit centralized risk approval"):
            OrderDispatchSafetyGuard.intercept_order_intent(intent)

    def test_accept_order_with_risk_approval(self):
        intent = {
            "order_id": "ORD-002",
            "symbol": "TCS",
            "venue": "PAPER",
            "is_risk_approved": True,
            "real_money": False,
        }
        validated = OrderDispatchSafetyGuard.intercept_order_intent(intent)
        assert validated["order_id"] == "ORD-002"

    def test_reject_real_money_intent(self):
        intent = {
            "order_id": "ORD-003",
            "symbol": "INFY",
            "venue": "PAPER",
            "is_risk_approved": True,
            "real_money": True,
        }
        with pytest.raises(SecurityInvariantViolation, match="real_money=True"):
            OrderDispatchSafetyGuard.intercept_order_intent(intent)


class TestWorkspaceContainment:
    """Tests file path boundary enforcement."""

    def test_allow_path_inside_workspace(self):
        target = WORKSPACE_ROOT / "logs" / "test.log"
        assert assert_within_workspace(target) == target.resolve()

    def test_block_canonical_algopro_path(self):
        with pytest.raises(WorkspaceIsolationViolation, match="targets protected canonical path"):
            assert_within_workspace(Path("/home/ubuntu/algopro/data/logs/audit.log"))

    def test_block_canonical_all_in_one_path(self):
        with pytest.raises(WorkspaceIsolationViolation, match="targets protected canonical path"):
            assert_within_workspace(Path("/home/ubuntu/all-in-one/.env"))

    def test_block_path_escaping_workspace(self):
        with pytest.raises(WorkspaceIsolationViolation, match="escapes workspace root"):
            assert_within_workspace(Path("/tmp/arbitrary_file.log"))
