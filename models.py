from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class Market:
    id: str
    title: str
    category: str
    subcategory: Optional[str]
    yes_price: float  # 0–1
    no_price: float   # 0–1
    resolves_at: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None  # full JSON if needed


@dataclass
class Recommendation:
    market_id: str
    title: str
    s: float
    p: float
    edge: float
    side: str          # "Yes" or "No"
    full_frac: float   # full Kelly fraction of bankroll
    half_frac: float   # half Kelly fraction
    limit: float       # limit price to post
    rationale: str
