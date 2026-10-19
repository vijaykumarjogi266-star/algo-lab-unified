"""Core Package for Algo Lab Unified."""

from src.core.exceptions import (
    AlgoLabSecurityError,
    BrokerExecutionBlockedError,
    MalformedConfigurationError,
    RiskApprovalBypassError,
    SecurityInvariantViolation,
    WorkspaceIsolationViolation,
)
from src.core.logging import get_logger
from src.core.safety_invariants import (
    ALLOW_LIVE_TRADING,
    ALLOW_REAL_BROKER_EXECUTION,
    PAPER_BROKER_ONLY,
    REAL_MONEY_RISK_PAISE,
    WORKSPACE_ROOT,
    assert_paper_only_invariants,
    assert_within_workspace,
    guard_broker_dispatch,
    validate_effective_configuration,
)

__all__ = [
    "AlgoLabSecurityError",
    "SecurityInvariantViolation",
    "BrokerExecutionBlockedError",
    "WorkspaceIsolationViolation",
    "MalformedConfigurationError",
    "RiskApprovalBypassError",
    "get_logger",
    "ALLOW_LIVE_TRADING",
    "ALLOW_REAL_BROKER_EXECUTION",
    "REAL_MONEY_RISK_PAISE",
    "PAPER_BROKER_ONLY",
    "WORKSPACE_ROOT",
    "assert_paper_only_invariants",
    "assert_within_workspace",
    "guard_broker_dispatch",
    "validate_effective_configuration",
]
