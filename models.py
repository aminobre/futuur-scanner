from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Market:
    id: int
    question_id: int
    outcome_id: int
    title: str
    outcome_title: str
    slug: str
    domain: str
    category_title: str
    tags: list[str]
    is_binary: bool
    s: float  # market-implied probability for this outcome (0–1)
    price: float  # same as s, kept for clarity
    volume_real: float
    volume_play: float
    wagers_count: int
    bet_end: datetime | None
    days_to_close: float | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)


@dataclass
class Recommendation:
    market: Market
    side: str  # 'yes' or 'no'
    s: float   # market price
    p0: float  # pre-GPT probability
    edge0: float  # p0 - s for chosen side (Yes: p0-s; No: s-p0)
    kelly_full: float
    kelly_half: float
    limit: float
    notes: str = ""


@dataclass
class BetRow:
    id: int
    status: str
    status_display: str
    market_id: int
    market_title: str
    market_slug: str
    domain: str
    category_title: str
    outcome_id: int
    outcome_title: str
    position: str  # 'l' or 's'
    currency: str
    total_shares: float
    avg_entry_price: float | None
    last_price: float
    entry_notional: float
    current_notional: float
    realized_pnl: float
    unrealized_pnl: float
    realized_pct: float | None
    first_action_at: datetime | None
    last_action_at: datetime | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)
    pct_of_bankroll: float | None = None


@dataclass
class LimitOrderRow:
    order_id: int
    market_title: str
    outcome_title: str
    domain: str
    category_title: str
    side: str  # 'bid' or 'ask'
    position: str  # 'l' or 's'
    price: float
    shares_requested: float
    shares_filled: float
    remaining_shares: float
    reserved_notional: float
    status: str
    created: datetime | None
    expires: datetime | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)
