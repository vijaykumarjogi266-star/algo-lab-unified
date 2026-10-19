"""Broker Gateway Safety Guard and Interceptor.

Ensures:
1. No order can be submitted to live broker endpoints.
2. Every order intent must possess verified risk approval before execution.
3. Fail-closed rejection of non-paper execution adapters.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from src.core.exceptions import (
    BrokerExecutionBlockedError,
    RiskApprovalBypassError,
    SecurityInvariantViolation,
)
from src.core.safety_invariants import assert_paper_only_invariants, guard_broker_dispatch


class OrderDispatchSafetyGuard:
    """Interceptor that inspects and certifies all order actions before execution."""

    @classmethod
    def intercept_order_intent(cls, order_intent: Dict[str, Any]) -> Dict[str, Any]:
        """Validates order intent before order creation."""
        # 1. Assert paper-only environment invariants
        assert_paper_only_invariants()

        # 2. Assert risk engine approval
        if not order_intent.get("is_risk_approved", False):
            raise RiskApprovalBypassError(
                f"ORDER REJECTED: Order intent '{order_intent.get('order_id', 'UNKNOWN')}' "
                "did not receive explicit centralized risk approval."
            )

        # 3. Assert target venue
        venue = str(order_intent.get("venue", "PAPER"))
        guard_broker_dispatch(venue, "SUBMIT_ORDER")

        # 4. Assert zero real money value
        if order_intent.get("real_money", False) or order_intent.get("is_live", False):
            raise SecurityInvariantViolation(
                "CRITICAL: Order intent marked with real_money=True or is_live=True."
            )

        return order_intent

    @classmethod
    def intercept_order_cancellation(cls, order_id: str, venue: str) -> None:
        """Validates cancellation dispatch."""
        assert_paper_only_invariants()
        guard_broker_dispatch(venue, f"CANCEL_ORDER_{order_id}")
