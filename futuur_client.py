from typing import List, Dict, Any

from py_futuur_client.client import Client

from models import Market
from config import FUTUUR_PUBLIC_KEY, FUTUUR_PRIVATE_KEY, FUTUUR_BASE_URL


def _client() -> Client:
    """
    Build a Futuur client using your API keys from env vars.
    """
    if not FUTUUR_PUBLIC_KEY or not FUTUUR_PRIVATE_KEY:
        raise RuntimeError(
            "FUTUUR_PUBLIC_KEY / FUTUUR_PRIVATE_KEY are not set. "
            "Set them in your environment before calling Futuur."
        )

    return Client(
        public_key=FUTUUR_PUBLIC_KEY,
        private_key=FUTUUR_PRIVATE_KEY,
        base_url=FUTUUR_BASE_URL,
    )


def _is_open_real_orderbook(m: Dict[str, Any]) -> bool:
    """
    Keep only markets that:
      - are open
      - support real currency
      - have order book enabled
    """
    if m.get("status_display") != "open":
        return False
    if not m.get("real_currency_available", False):
        return False
    if not m.get("order_book_enabled", False):
        return False
    return True


def _infer_domain(m: Dict[str, Any]) -> str:
    """
    Roughly classify a market into macro/crypto/reg, sports, entertainment, or other.
    This is text-based only; tweak keywords as you see fit.
    """
    title = (m.get("title") or "").lower()
    cat = m.get("category") or {}
    cat_title = (cat.get("title") or "").lower()
    cat_slug = (cat.get("slug") or "").lower()
    tags = " ".join((t.get("name") or "").lower() for t in m.get("tags", []))

    text = " ".join([title, cat_title, cat_slug, tags])

    sports_kw = (
        "sports", "game", "match", "tournament", "league",
        "nba", "nfl", "nhl", "mlb", "soccer", "football",
        "tennis", "f1", "formula", "ufc", "fight", "world cup",
    )
    ent_kw = (
        "oscar", "oscars", "emmy", "grammy", "bafta",
        "movie", "box office", "film", "tv series", "season",
        "netflix", "disney", "hbo", "series finale", "album",
    )
    macro_kw = (
        "inflation", "cpi", "gdp", "unemployment", "jobs",
        "interest rate", "fed", "federal reserve", "ecb", "central bank",
        "recession", "growth", "economy", "economic",
        "election", "president", "parliament", "congress",
        "regulation", "regulatory", "sec", "court", "supreme court",
        "crypto", "bitcoin", "btc", "ethereum", "eth", "defi",
        "finance", "stock", "equity", "bond", "treasury",
    )

    if any(k in text for k in sports_kw):
        return "sports"
    if any(k in text for k in ent_kw):
        return "entertainment"
    if any(k in text for k in macro_kw):
        return "macro"
    return "other"


def _extract_outcome_price(outcome: Dict[str, Any]) -> float | None:
    """
    Extract outcome price from:
      'price': {'OOM': 0.34, 'USDC': 0.34}
    Prefer USDC, fall back to OOM.
    """
    price_dict = outcome.get("price") or {}
    if "USDC" in price_dict:
        val = float(price_dict["USDC"])
    elif "OOM" in price_dict:
        val = float(price_dict["OOM"])
    else:
        return None

    if not (0.0 < val < 1.0):
        return None
    return val


def _map_outcome_to_market(m: Dict[str, Any], outcome: Dict[str, Any]) -> Market | None:
    """
    Turn one Futuur outcome into a Market dataclass instance.

    id: "<market_id>:<outcome_id>"
    title: "<market title> — <outcome title>"
    yes_price: outcome price
    no_price: 1 - yes_price
    """
    price = _extract_outcome_price(outcome)
    if price is None:
        return None

    market_id = m.get("id")
    outcome_id = outcome.get("id")

    composite_id = f"{market_id}:{outcome_id}"
    composite_title = f"{m.get('title', 'Untitled')} — {outcome.get('title', '')}"

    cat = m.get("category") or {}
    category_title = cat.get("title") or ""
    subcategory_slug = cat.get("slug")

    resolves_at = (
        m.get("bet_end_date")
        or m.get("event_end_date")
        or m.get("resolve_date")
    )

    created_at = m.get("created_on")
    volume_real = float(m.get("volume_real_money", 0.0))

    slug = m.get("slug")
    url = f"https://futuur.com/markets/{slug}" if slug else None

    yes_price = price
    no_price = 1.0 - yes_price

    domain = _infer_domain(m)

    return Market(
        id=str(composite_id),
        title=composite_title,
        category=category_title,
        subcategory=subcategory_slug,
        yes_price=yes_price,
        no_price=no_price,
        resolves_at=resolves_at,
        created_at=created_at,
        volume_real=volume_real,
        url=url,
        domain=domain,
        raw=m,
    )


def get_markets() -> List[Market]:
    """
    Fetch markets from Futuur via py_futuur_client and convert each outcome
    into a Market object.

    Uses client.market.list(), which returns:
      {'pagination': {...}, 'results': [ {...}, ... ]}
    """
    client = _client()
    raw = client.market.list()

    results = raw.get("results") or []
    markets: List[Market] = []

    for m in results:
        if not isinstance(m, dict):
            continue

        if not _is_open_real_orderbook(m):
            continue

        for outcome in m.get("outcomes") or []:
            market_obj = _map_outcome_to_market(m, outcome)
            if market_obj:
                markets.append(market_obj)

    return markets
