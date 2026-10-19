"""Canonical Abstract Base Strategy Interface for Algo Lab Unified.

Adheres to Non-Negotiable Invariants:
- Principle 1: Never force a trade.
- Principle 2: WAIT is a valid decision (returns None).
- Principle 4: No look-ahead bias; point-in-time evaluation only.
- Principle 8: Every strategy must be explicitly identified and versioned.
- Fail-Closed: Invalid or out-of-bounds parameters raise InvalidStrategyParameterError.
  No silent fallbacks to defaults on bad configuration.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
import math
from typing import Any, Dict, List, Optional

from src.core.exceptions import InvalidStrategyParameterError
from src.scanner.scanner import MarketBar, ScanCandidate
from src.strategies.models import StrategySignal


class BaseStrategy(ABC):
    """Abstract base class for all canonical quantitative strategies."""

    def __init__(
        self,
        strategy_id: str,
        version: str = "1.0.0",
        parameters: Optional[Dict[str, Any]] = None,
    ):
        if not strategy_id or not isinstance(strategy_id, str):
            raise InvalidStrategyParameterError(f"strategy_id must be a non-empty string, got {strategy_id!r}")
        if not version or not isinstance(version, str):
            raise InvalidStrategyParameterError(f"version must be a non-empty string, got {version!r}")

        self.strategy_id = strategy_id.strip()
        self.version = version.strip()
        raw_params = parameters if parameters is not None else {}
        self.parameters = self.validate_and_sanitize_parameters(raw_params)

    @abstractmethod
    def validate_and_sanitize_parameters(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Validates parameter types, bounded ranges, and logic fail-closed.

        Must raise InvalidStrategyParameterError if any constraint is violated.
        Must NOT silently substitute defaults for invalid user-supplied inputs.
        """
        pass

    @abstractmethod
    def evaluate(
        self,
        current_bar: MarketBar,
        history: List[MarketBar],
        indicators: Optional[Dict[str, Any]] = None,
    ) -> Optional[StrategySignal]:
        """Evaluates point-in-time market data and returns StrategySignal if a setup triggers.

        Returns None (WAIT) if criteria are not fully met.
        """
        pass

    def evaluate_candidate(self, candidate: ScanCandidate) -> Optional[StrategySignal]:
        """Adapter allowing scanner-driven workflows to trigger strategy signals."""
        return None

    def default_wait(self, symbol: str, timestamp_iso: str, reason: str = "WAIT") -> None:
        """Explicit hook recording a no-trade WAIT decision."""
        return None

    @staticmethod
    def _validate_numeric_range(
        name: str,
        val: Any,
        min_val: Optional[float] = None,
        max_val: Optional[float] = None,
        is_integer: bool = False,
    ) -> None:
        """Helper enforcing strict numeric type and finite bounds."""
        if isinstance(val, bool) or not isinstance(val, (int, float, Decimal)):
            raise InvalidStrategyParameterError(f"Parameter '{name}' must be numeric, got {type(val).__name__} ({val!r})")

        f_val = float(val)
        if math.isnan(f_val) or math.isinf(f_val):
            raise InvalidStrategyParameterError(f"Parameter '{name}' cannot be NaN or Infinite")

        if is_integer and not float(f_val).is_integer():
            raise InvalidStrategyParameterError(f"Parameter '{name}' must be an integer, got {val!r}")

        if min_val is not None and f_val < min_val:
            raise InvalidStrategyParameterError(
                f"Parameter '{name}' value {val} is below minimum allowed {min_val}"
            )
        if max_val is not None and f_val > max_val:
            raise InvalidStrategyParameterError(
                f"Parameter '{name}' value {val} exceeds maximum allowed {max_val}"
            )
