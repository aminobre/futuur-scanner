"""Shared utility functions used across the project."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


def safe_float(value: Any, default: float = 0.0) -> float:
    """Best-effort conversion to float, falling back to a default on failure."""
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def fmt_str(x: Any, width: int) -> str:
    """Format any value as a single-line, fixed-width string."""
    s = "" if x is None else str(x)
    s = s.replace("\n", " ").replace("\r", " ")
    return f"{s[:width]:{width}}"


def parse_dt(value: str | None) -> datetime | None:
    """Parse a datetime string in ISO format, handling various formats.
    
    Supports:
    - ISO format with Z suffix: "2025-12-10T08:00:00Z"
    - ISO format with timezone: "2025-12-10T08:00:00+00:00"
    - Alternative format: "2025-12-10T08:00:00%z"
    
    Returns None if parsing fails.
    """
    if not value:
        return None
    try:
        v = value.strip()
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        return datetime.fromisoformat(v)
    except Exception:
        pass
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    except Exception:
        logger.debug(f"Failed to parse datetime: {value}")
        return None

