"""Core Safety and Security Exception Hierarchy for Algo Lab Unified.

Guarantees fail-closed error handling across all security boundaries.
"""

class AlgoLabSecurityError(Exception):
    """Base exception for all security, boundary, and invariant violations."""
    pass


class SecurityInvariantViolation(AlgoLabSecurityError):
    """Raised when any of the four immutable paper-only safety invariants are violated."""
    pass


class BrokerExecutionBlockedError(AlgoLabSecurityError):
    """Raised whenever a code path attempts to contact live broker order execution endpoints."""
    pass


class WorkspaceIsolationViolation(AlgoLabSecurityError):
    """Raised when file I/O operations attempt to traverse outside the designated workspace boundary."""
    pass


class MalformedConfigurationError(AlgoLabSecurityError):
    """Raised when runtime or environment configuration is missing, ambiguous, or contradictory."""
    pass


class RiskApprovalBypassError(AlgoLabSecurityError):
    """Raised when an order signal attempts to bypass centralized risk engine approval."""
    pass


class InvalidOrderTickError(AlgoLabSecurityError):
    """Raised when an order price does not conform to statutory instrument tick size."""
    pass


class MissingTickMetadataError(AlgoLabSecurityError):
    """Raised when an instrument lacks valid, authoritative tick size metadata."""
    pass

