"""Shared utilities for building prompts from `PROMPTS.txt` and market data."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

BASE_DIR = Path(__file__).resolve().parent
PROMPTS_PATH = BASE_DIR / "PROMPTS.txt"


def read_prompts() -> tuple[str, dict[str, str]]:
    """Parse PROMPTS.txt into (general_text, section_texts)."""
    if not PROMPTS_PATH.exists():
        raise FileNotFoundError(f"{PROMPTS_PATH} does not exist")

    general_lines: list[str] = []
    section_lines: dict[str, list[str]] = {}
    current_section: str | None = None

    for raw in PROMPTS_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip("\n")
        if line.startswith("***") and line.endswith("***"):
            section = line.strip("*").strip()
            current_section = section.upper()
            section_lines[current_section] = []
            continue

        if current_section is None:
            general_lines.append(line)
        else:
            section_lines[current_section].append(line)

    general_text = "\n".join(general_lines).strip()
    sections = {k: "\n".join(v).strip() for k, v in section_lines.items()}
    return general_text, sections


def load_markets_from_json(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_markets_from_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return [dict(row) for row in reader]


def _safe_float(value: Any, fallback: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _parse_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _format_market(idx: int, data: dict[str, Any]) -> str:
    title = data.get("title") or data.get("question") or "Untitled market"
    outcome = data.get("outcome_title") or data.get("outcome") or "Unknown outcome"
    s = _safe_float(data.get("s"), _safe_float(data.get("price")))
    base_p = _safe_float(data.get("p0"), data.get("fair_value"))
    edge = _safe_float(data.get("edge0"), _safe_float(data.get("edge")))
    days = _safe_float(data.get("days_to_close"))
    if days is None:
        bet_end = _parse_datetime(data.get("bet_end"))
        if bet_end:
            now = datetime.now(tz=timezone.utc)
            delta = bet_end.replace(tzinfo=timezone.utc) - now
            days = round(delta.total_seconds() / 86400, 2)
    volume = _safe_float(data.get("volume_real"))
    group = data.get("group") or data.get("category") or "Other"
    tags = data.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    url = data.get("url") or data.get("market_url") or data.get("slug") or ""

    parts = [
        f"{idx}. Market: {title} [{group}]",
        f"   Outcome: {outcome}",
        f"   Price (s): {s:.3f}" if s is not None else "   Price (s): unknown",
        f"   Fair p0: {base_p:.3f}" if base_p is not None else "   Fair p0: unknown",
        f"   Edge: {edge:+0.3f}" if edge is not None else "   Edge: unknown",
        f"   Volume: {volume:.0f}" if volume is not None else "   Volume: unknown",
        f"   Days to close: {days:.1f}" if days is not None else "   Days to close: unknown",
    ]

    question_id = data.get("question_id")
    outcome_id = data.get("outcome_id")
    if tags:
        parts.append(f"   Tags: {', '.join(str(t) for t in tags if t)}")
    if url:
        parts.append(f"   URL: {url}")
    if question_id is not None:
        parts.append(f"   Question ID: {question_id}")
    if outcome_id is not None:
        parts.append(f"   Outcome ID: {outcome_id}")

    return "\n".join(parts)


def build_prompt(mode: str, markets: Iterable[dict[str, Any]]) -> str:
    general, sections = read_prompts()
    section = sections.get(mode.upper())
    if not section:
        raise ValueError(f"Unknown prompt mode '{mode}' (available: {', '.join(sections)})")

    header: list[str] = []
    if general:
        header.append(general)
    header.append(f"***{mode.upper()}***")
    header.append(section)
    header.append("")
    header.append("Markets:")

    body = [_format_market(idx, market) for idx, market in enumerate(markets, 1)]
    return "\n".join(header + body)


SAMPLE_MARKETS: list[dict[str, Any]] = [
    {
        "title": "Sample CPI Beats 2025",
        "outcome_title": "CPI YoY below 3.5%",
        "s": 0.42,
        "p0": 0.5,
        "edge0": 0.08,
        "volume_real": 12500,
        "days_to_close": 5.2,
        "group": "Finance",
        "tags": ["CPI", "Macro"],
        "url": "https://www.futuur.com/markets/sample-cpi",
    },
    {
        "title": "Sample AI Regulation Bill",
        "outcome_title": "Federal bill passes before 2025",
        "s": 0.67,
        "p0": 0.55,
        "edge0": -0.12,
        "volume_real": 8900,
        "days_to_close": 12.4,
        "group": "Politics",
        "tags": ["AI Policy"],
        "url": "https://www.futuur.com/markets/sample-ai",
    },
]
