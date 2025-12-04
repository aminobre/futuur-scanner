from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class Market:
    id: str
    title: str
    category: str
    subcategory: Optional[str]
    yes_price: float        # 0–1
    no_price: float         # 0–1
    resolves_at: Optional[str] = None   # ISO datetime string
    created_at: Optional[str] = None    # ISO datetime string
    volume_real: float = 0.0            # real-money volume
    url: Optional[str] = None           # direct Futuur URL
    domain: str = "other"               # macro/sports/entertainment/other
    raw: Optional[Dict[str, Any]] = None


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

    # metadata
    category: str = ""
    subcategory: Optional[str] = None
    resolves_at: Optional[str] = None
    created_at: Optional[str] = None
    volume_real: float = 0.0
    url: Optional[str] = None
    domain: str = "other"
