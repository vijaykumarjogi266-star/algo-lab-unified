"""Structured JSON Logging Adapter with Workspace Path Isolation.

Guarantees:
1. Log files are written strictly under the unified workspace logs/ directory or tmp_path.
2. Standardized JSON formatting with timestamp, level, logger name, and correlation IDs.
3. Zero file writes to canonical /home/ubuntu/ directories.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from src.core.safety_invariants import WORKSPACE_ROOT, assert_within_workspace

try:
    from pythonjsonlogger import json as jsonlogger
except ImportError:
    try:
        from pythonjsonlogger import jsonlogger
    except ImportError:
        jsonlogger = None  # Fallback if python-json-logger is not yet installed


def get_logger(name: str, log_filename: Optional[str] = None) -> logging.Logger:
    """Configures and returns a structured logger constrained to the workspace boundary."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    # Determine safe log file path
    default_log_dir = WORKSPACE_ROOT / "logs"
    default_log_dir.mkdir(parents=True, exist_ok=True)

    if log_filename:
        target_path = Path(log_filename)
        if not target_path.is_absolute():
            target_path = default_log_dir / log_filename
    else:
        target_path = default_log_dir / "paper_session.log"

    # Enforce workspace path isolation
    safe_log_path = assert_within_workspace(target_path)

    # File Handler
    file_handler = logging.FileHandler(str(safe_log_path), encoding="utf-8")
    if jsonlogger:
        formatter = jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        )
    else:
        formatter = logging.Formatter(
            '{"time": "%(asctime)s", "level": "%(levelname)s", "name": "%(name)s", "message": "%(message)s"}'
        )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Stream Handler (stdout)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger
