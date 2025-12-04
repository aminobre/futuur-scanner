from typing import List, Dict, Any

from py_futuur_client.client import Client

from models import Market
from config import FUTUUR_PUBLIC_KEY, FUTUUR_PRIVATE_KEY

# ---------- Futuur client wrapper ---------- #

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
    )



# ---------- filters and mapping ---------- #

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


def _is_finance_crypto_reg(m: Dict[str, Any]) -> bool:
    """
    Filter to markets you care about: crypto / macro / regulatory.
    Uses tags and category.
    """
    tag_names = [t.get("name", "").lower() for t in m.get("tags", [])]

    cat = m.get("category") or {}
    cat_title = (cat.get("title") or "").lower()
    cat_slug = (cat.get("slug") or "").lower()

    text = " ".join(tag_names + [cat_title, cat_slug])

    targets = (
        "crypto",
        "defi",
        "bitcoin",
        "ethereum",
        "inflation",
        "cpi",
        "gdp",
        "employment",
        "jobs",
        "interest",
        "rate",
        "regulation",
        "regulatory",
        "sec",
        "fed",
        "prediction markets",
    )

    return any(t in text for t in targets)


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

    yes_price = price
    no_price = 1.0 - yes_price

    return Market(
        id=str(composite_id),
        title=composite_title,
        category=category_title,
        subcategory=subcategory_slug,
        yes_price=yes_price,
        no_price=no_price,
        resolves_at=resolves_at,
        raw=m,
    )


# ---------- public function used by main.py / web_app.py ---------- #

def get_markets(limit: int = 100, offset: int = 0) -> List[Market]:
    """
    Fetch markets from Futuur via py_futuur_client and convert each outcome
    into a Market object.

    For now we call .list() with no extra kwargs because the SDK
    signature rejected 'ordering' (and maybe limit/offset).
    """
    client = _client()
    raw = client.market.list()  # <- no kwargs; this we know works


    results = raw.get("results") or []
    markets: List[Market] = []

    for m in results:
        if not isinstance(m, dict):
            continue

        if not _is_open_real_orderbook(m):
            continue

        if not _is_finance_crypto_reg(m):
            continue

        for outcome in m.get("outcomes") or []:
            market_obj = _map_outcome_to_market(m, outcome)
            if market_obj:
                markets.append(market_obj)

    return markets
