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
from src.core.monetary import (
    DEFAULT_MONETARY_PRECISION,
    KNOWN_MONETARY_KEYS,
    MONETARY_ROUNDING,
    assert_no_binary_floats_in_payload,
    format_monetary_decimal,
    is_valid_monetary_string,
    quantize_monetary_decimal,
)
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
    "format_monetary_decimal",
    "quantize_monetary_decimal",
    "is_valid_monetary_string",
    "assert_no_binary_floats_in_payload",
    "MONETARY_ROUNDING",
    "DEFAULT_MONETARY_PRECISION",
    "KNOWN_MONETARY_KEYS",
]
