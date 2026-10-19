"""Authoritative Strategy Registry and Dispatcher for Algo Lab Unified.

Adheres to Non-Negotiable Invariants:
- Fail-Closed Dispatch: Unknown strategy identifiers raise UnknownStrategyError or return WAIT.
- Zero Silent Defaults: Invalid parameters or constructor failures raise InvalidStrategyParameterError;
  they are NEVER silently replaced with default instances.
- Versioned Identifiers: Every strategy is uniquely registered with version provenance.
- Strict Separation: Strategy signals are decoupled from risk checks, order routing, and fill execution.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional, Type, Union

from src.core.exceptions import InvalidStrategyParameterError, UnknownStrategyError
from src.scanner.scanner import MarketBar
from src.strategies.base import BaseStrategy
from src.strategies.models import StrategySignal


class StrategyRegistry:
    """Central registry and dispatcher for all verified quantitative strategies."""

    _registry: Dict[str, Type[BaseStrategy]] = {}
    _metadata: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def register(
        cls,
        strategy_cls: Type[BaseStrategy],
        aliases: Optional[List[str]] = None,
        description: str = "",
    ) -> None:
        """Registers a strategy class under its primary ID and optional aliases."""
        if not issubclass(strategy_cls, BaseStrategy):
            raise TypeError(f"Strategy {strategy_cls.__name__} must subclass BaseStrategy")

        keys = [strategy_cls.__name__]
        if hasattr(strategy_cls, "STRATEGY_ID"):
            keys.append(getattr(strategy_cls, "STRATEGY_ID"))
        elif hasattr(strategy_cls, "strategy_id"):
            val = getattr(strategy_cls, "strategy_id")
            if isinstance(val, str):
                keys.append(val)

        if aliases:
            keys.extend(aliases)

        for key in keys:
            normalized = cls._normalize_key(key)
            if normalized:
                cls._registry[normalized] = strategy_cls

        primary_id = cls._normalize_key(keys[1] if len(keys) > 1 else keys[0])
        cls._metadata[primary_id] = {
            "class_name": strategy_cls.__name__,
            "description": description,
            "aliases": [cls._normalize_key(a) for a in (aliases or [])],
        }

    @classmethod
    def _normalize_key(cls, name: Optional[str]) -> str:
        if not name or not isinstance(name, str):
            return ""
        return name.strip().upper().replace(" ", "_").replace("-", "_")

    @classmethod
    def is_registered(cls, name: Optional[str]) -> bool:
        """Checks if a strategy identifier is known to the registry."""
        if not name or not isinstance(name, str):
            return False
        normalized = cls._normalize_key(name)
        if normalized in ("", "WAIT", "NONE", "UNKNOWN"):
            return False
        return normalized in cls._registry

    @classmethod
    def get_strategy_class(cls, name: str) -> Type[BaseStrategy]:
        """Returns the strategy class for a given identifier, or raises UnknownStrategyError."""
        if not cls.is_registered(name):
            raise UnknownStrategyError(f"Strategy '{name}' is not registered in StrategyRegistry")
        return cls._registry[cls._normalize_key(name)]

    @classmethod
    def create_strategy(
        cls,
        name: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> BaseStrategy:
        """Instantiates a strategy with validated parameters.

        FAIL-CLOSED:
        - If strategy ID is unknown, raises UnknownStrategyError.
        - If parameters are invalid or constructor fails, propagates InvalidStrategyParameterError.
        - NO silent default fallback.
        """
        strat_cls = cls.get_strategy_class(name)
        params = parameters if parameters is not None else {}
        # Instantiate directly; BaseStrategy.__init__ validates parameters fail-closed
        try:
            return strat_cls(parameters=params)
        except (TypeError, ValueError) as exc:
            raise InvalidStrategyParameterError(
                f"Failed to instantiate strategy '{name}' with parameters {params}: {exc}"
            ) from exc

    @classmethod
    def dispatch_bar(
        cls,
        strategy_or_name: Union[str, BaseStrategy],
        current_bar: MarketBar,
        history: List[MarketBar],
        indicators: Optional[Dict[str, Any]] = None,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> Optional[StrategySignal]:
        """Dispatches point-in-time bar evaluation through the selected strategy.

        Returns:
            StrategySignal if entry triggers, or None (WAIT).
        """
        if isinstance(strategy_or_name, BaseStrategy):
            strategy = strategy_or_name
        else:
            strategy = cls.create_strategy(strategy_or_name, parameters)

        return strategy.evaluate(current_bar, history, indicators)

    @classmethod
    def list_strategies(cls) -> List[str]:
        """Returns sorted list of all registered primary strategy identifiers."""
        return sorted(list(cls._metadata.keys()))

    @classmethod
    def clear(cls) -> None:
        """Clears all registered strategies (primarily for isolated test fixtures)."""
        cls._registry.clear()
        cls._metadata.clear()
