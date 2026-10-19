"""Centralized Paper-Only Safety Invariants and Boundary Gatekeepers.

Enforces:
1. Four immutable paper-only invariants:
   - ALLOW_LIVE_TRADING == False
   - ALLOW_REAL_BROKER_EXECUTION == False
   - REAL_MONEY_RISK_PAISE == 0
   - PAPER_BROKER_ONLY == True
2. Fail-closed configuration validation.
3. Path containment within the unified workspace boundary.
4. AST module and import restrictions.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, FrozenSet, Mapping, Optional

from src.core.exceptions import (
    BrokerExecutionBlockedError,
    MalformedConfigurationError,
    SecurityInvariantViolation,
    WorkspaceIsolationViolation,
)

# Immutable hardcoded safety baseline
ALLOW_LIVE_TRADING: bool = False
ALLOW_REAL_BROKER_EXECUTION: bool = False
REAL_MONEY_RISK_PAISE: int = 0
PAPER_BROKER_ONLY: bool = True

# Workspace root boundary anchor
WORKSPACE_ROOT: Path = Path("/opt/ai-builder/workspaces/algolab-unification-20261019-083200/algo-lab-unified").resolve()

# AST Prohibited broker SDKs and unsafe networking modules
PROHIBITED_IMPORT_NAMES: FrozenSet[str] = frozenset({
    "kiteconnect",
    "smartapi",
    "upstox_client",
    "fyers_api",
    "angelone",
    "alice_blue",
    "dhan",
})

# Protected canonical paths that must NEVER be accessed for write operations
PROTECTED_CANONICAL_PATHS: FrozenSet[str] = frozenset({
    "/home/ubuntu/algopro",
    "/home/ubuntu/all-in-one",
    "/home/ubuntu/algolab",
})


def assert_paper_only_invariants(overrides: Optional[Mapping[str, Any]] = None) -> None:
    """Verifies that all four paper-only execution invariants are strictly enforced.

    Raises SecurityInvariantViolation immediately if any invariant is breached.
    """
    env = os.environ if overrides is None else overrides

    # Check ALLOW_LIVE_TRADING
    val_live = str(env.get("ALLOW_LIVE_TRADING", "false")).strip().lower()
    if val_live not in ("false", "0", "no"):
        raise SecurityInvariantViolation(
            f"CRITICAL SAFETY VIOLATION: ALLOW_LIVE_TRADING is '{val_live}', must be 'false'."
        )

    # Check ALLOW_REAL_BROKER_EXECUTION
    val_broker = str(env.get("ALLOW_REAL_BROKER_EXECUTION", "false")).strip().lower()
    if val_broker not in ("false", "0", "no"):
        raise SecurityInvariantViolation(
            f"CRITICAL SAFETY VIOLATION: ALLOW_REAL_BROKER_EXECUTION is '{val_broker}', must be 'false'."
        )

    # Check REAL_MONEY_RISK_PAISE
    val_risk = env.get("REAL_MONEY_RISK_PAISE", 0)
    try:
        val_risk_int = int(val_risk)
    except (ValueError, TypeError):
        raise MalformedConfigurationError(
            f"Malformed REAL_MONEY_RISK_PAISE value: '{val_risk}'. Must be integer 0."
        )
    if val_risk_int != 0:
        raise SecurityInvariantViolation(
            f"CRITICAL SAFETY VIOLATION: REAL_MONEY_RISK_PAISE is {val_risk_int}, must be 0."
        )

    # Check PAPER_BROKER_ONLY
    val_paper = str(env.get("PAPER_BROKER_ONLY", "true")).strip().lower()
    if val_paper not in ("true", "1", "yes"):
        raise SecurityInvariantViolation(
            f"CRITICAL SAFETY VIOLATION: PAPER_BROKER_ONLY is '{val_paper}', must be 'true'."
        )


def validate_effective_configuration(env_vars: Mapping[str, Any]) -> Dict[str, Any]:
    """Validates configuration dictionary and fails closed if missing or contradictory.

    Returns sanitized, certified paper configuration.
    """
    # 1. Assert paper invariants
    assert_paper_only_invariants(env_vars)

    # 2. Check for contradictory flags (e.g. LIVE_TRADING=True alongside PAPER_BROKER_ONLY=True)
    live_signals = [
        str(env_vars.get("LIVE_TRADING_ENABLED", "false")).lower() in ("true", "1"),
        str(env_vars.get("ENABLE_REAL_ORDERS", "false")).lower() in ("true", "1"),
        str(env_vars.get("EXECUTION_ENVIRONMENT", "PAPER")).upper() == "LIVE",
    ]
    if any(live_signals):
        raise MalformedConfigurationError(
            "Contradictory configuration detected: Live trading flags present in paper mode."
        )

    return {
        "ALLOW_LIVE_TRADING": False,
        "ALLOW_REAL_BROKER_EXECUTION": False,
        "REAL_MONEY_RISK_PAISE": 0,
        "PAPER_BROKER_ONLY": True,
        "EXECUTION_ENVIRONMENT": "PAPER",
        "WORKSPACE_ROOT": str(WORKSPACE_ROOT),
    }


def assert_within_workspace(target_path: Path) -> Path:
    """Enforces strict path containment within the designated workspace boundary.

    Raises WorkspaceIsolationViolation if path attempts to escape or access canonical repos.
    """
    resolved = target_path.resolve()

    # Reject access to protected canonical directories
    for canonical in PROTECTED_CANONICAL_PATHS:
        canonical_p = Path(canonical).resolve()
        if resolved == canonical_p or canonical_p in resolved.parents:
            raise WorkspaceIsolationViolation(
                f"WORKSPACE ISOLATION VIOLATION: Target path '{resolved}' targets protected canonical path '{canonical}'."
            )

    # Ensure path is within workspace root
    if not (resolved == WORKSPACE_ROOT or WORKSPACE_ROOT in resolved.parents):
        raise WorkspaceIsolationViolation(
            f"WORKSPACE ISOLATION VIOLATION: Target path '{resolved}' escapes workspace root '{WORKSPACE_ROOT}'."
        )

    return resolved


def guard_broker_dispatch(venue: str, action: str) -> None:
    """Interceptor that halts any live broker execution attempt."""
    venue_lower = venue.strip().lower()
    if venue_lower not in ("paper", "simulated", "virtual", "mock", "backtest"):
        raise BrokerExecutionBlockedError(
            f"LIVE EXECUTION BLOCKED: Venue '{venue}' is not an authorized paper execution venue. "
            f"Attempted action: '{action}'."
        )
