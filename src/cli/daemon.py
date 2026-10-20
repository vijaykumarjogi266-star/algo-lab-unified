"""Algo Lab Unified Paper Trading CLI Daemon Entry Point."""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal

from src.core.logging import get_logger
from src.core.safety_invariants import assert_paper_only_invariants

logger = get_logger(__name__)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for algo-paper command."""
    parser = argparse.ArgumentParser(
        prog="algo-paper",
        description="Algo Lab Unified — Paper Trading & Quantitative Simulation Daemon",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Check paper daemon configuration and safety status",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Execute safety assertion check and exit cleanly",
    )

    args = parser.parse_args(argv)

    # Enforce non-negotiable paper safety invariants
    try:
        assert_paper_only_invariants()
    except Exception as exc:
        logger.critical("FATAL: Paper safety invariant violation: %s", exc)
        return 1

    logger.info("Algo Lab Unified paper daemon initialized in fail-closed simulation mode.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
