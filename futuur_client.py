from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import CURRENCY_MODE, CURRENCY
from futuur_api_raw import call_api
from models import Market


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        # Example format: 2025-12-10T08:00:00Z
        if value.endswith("Z"):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _infer_domain(question: Dict[str, Any]) -> str:
    """Map Futuur categories/tags to our coarse domains."""
    category = (question.get("category") or {}).get("title", "") or ""
    cat_slug = (question.get("category") or {}).get("slug", "") or ""
    tags = " ".join(t.get("name", "") for t in question.get("tags", [])).lower()
    text = " ".join([category, cat_slug, tags]).lower()

    if any(k in text for k in ("crypto", "bitcoin", "ethereum", "defi")):
        return "Finance"
    if any(k in text for k in ("stock", "equity", "index", "recession", "inflation", "gdp", "cpi")):
        return "Finance"
    if any(k in text for k in ("election", "president", "senate", "congress", "parliament", "vote")):
        return "Politics"
    if any(k in text for k in ("nfl", "nba", "premier league", "uefa", "world cup", "mlb", "nhl")):
        return "Sports"
    if any(k in text for k in ("oscars", "emmy", "movie", "box office", "grammy", "series")):
        return "Entertainment"
    if any(k in text for k in ("ai", "science", "space", "climate", "physics", "biology")):
        return "Science"
    return "Other"


def _extract_price(outcome: Dict[str, Any]) -> float:
    price = outcome.get("price")
    if isinstance(price, dict):
        # Use canonical CURRENCY if present, otherwise first value.
        if CURRENCY in price:
            return _safe_float(price[CURRENCY])
        # Sometimes they use 'OOM' for play money; fall back to any.
        if price:
            return _safe_float(next(iter(price.values())))
        return 0.0
    return _safe_float(price)


def get_markets(
    limit: int = 200,
    offset: int = 0,
    ordering: str = "-created_on",
) -> List[Market]:
    """
    Fetch a flat list of outcome-level markets.
    Each Futuur question with N outcomes becomes N Market rows.
    """
    params: Dict[str, Any] = {
        "currency_mode": CURRENCY_MODE,
        "limit": limit,
        "offset": offset,
        "ordering": ordering,
        "resolved_only": False,
        "hide_my_bets": False,
    }

    data = call_api("markets/", params=params, method="GET", auth=True)
    # markets endpoint is paginated: {"pagination": {...}, "results": [...]}
    results = data.get("results") if isinstance(data, dict) else data
    if not isinstance(results, list):
        return []

    markets: List[Market] = []
    now = datetime.now(timezone.utc)

    for q in results:
        bet_end = _parse_dt(q.get("bet_end_date"))
        days_to_close: Optional[float] = None
        if bet_end is not None:
            delta = (bet_end - now).total_seconds()
            days_to_close = delta / 86400.0

        domain = _infer_domain(q)
        category_title = (q.get("category") or {}).get("title") or ""
        tags = [t.get("name", "") for t in q.get("tags", [])]
        is_binary = bool(q.get("is_binary"))

        volume_real = _safe_float(q.get("volume_real_money"))
        volume_play = _safe_float(q.get("volume_play_money"))
        wagers_count = int(q.get("wagers_count") or 0)

        question_id = int(q.get("id") or 0)
        title = q.get("title") or ""
        slug = q.get("slug") or ""

        for outcome in q.get("outcomes", []) or []:
            outcome_id = int(outcome.get("id") or 0)
            outcome_title = outcome.get("title") or ""
            s = _extract_price(outcome)
            m = Market(
                id=question_id,
                question_id=question_id,
                outcome_id=outcome_id,
                title=title,
                outcome_title=outcome_title,
                slug=slug,
                domain=domain,
                category_title=category_title,
                tags=tags,
                is_binary=is_binary,
                s=s,
                price=s,
                volume_real=volume_real,
                volume_play=volume_play,
                wagers_count=wagers_count,
                bet_end=bet_end,
                days_to_close=days_to_close,
                raw={"question": q, "outcome": outcome},
            )
            markets.append(m)

    return markets
