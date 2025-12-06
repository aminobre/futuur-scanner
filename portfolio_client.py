from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

from requests import HTTPError

from futuur_api_raw import call_api


# ---------- date helpers ----------

def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """Handle ISO strings with or without microseconds and Z suffix."""
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
        return None


def _fmt_dt(dt: Optional[datetime]) -> str:
    if not dt:
        return "-"
    return dt.strftime("%b %d, %y %H:%M")  # e.g. Dec 06, 25 02:15


# ---------- dataclasses ----------

@dataclass
class BetRow:
    bet_id: int
    question_id: int
    question_title: str
    outcome_id: int
    outcome_title: str
    category_title: str
    category_slug: str
    position: str  # 'l' or 's'
    currency: str
    shares: float
    amount_invested: float
    avg_price: float
    mark_price: float
    mark_value: float
    unrealized_pnl: float
    realized_pnl: float
    status: str  # 'open' or 'closed'
    created: Optional[datetime]
    closed: Optional[datetime]

    @property
    def side_display(self) -> str:
        return "Long" if self.position == "l" else "Short"

    @property
    def created_str(self) -> str:
        return _fmt_dt(self.created)

    @property
    def closed_str(self) -> str:
        return _fmt_dt(self.closed)


@dataclass
class LimitOrderRow:
    order_id: int
    question: str          # title only
    outcome: str           # title only
    side: str              # 'bid' or 'ask'
    position: str          # 'l' or 's'
    price: float
    shares_requested: float
    shares_filled: float
    remaining_shares: float
    reserved_notional: float
    currency: str
    status: str
    created: Optional[datetime]
    expired_at: Optional[datetime]

    @property
    def created_str(self) -> str:
        return _fmt_dt(self.created)

    @property
    def expired_str(self) -> str:
        return _fmt_dt(self.expired_at)


# ---------- wallet / bankroll ----------

def fetch_wallet_balance() -> Optional[float]:
    """Best-effort real-money wallet balance in canonical currency (e.g. USDC)."""
    try:
        data = call_api("me/", params=None, method="GET", auth=True)
    except HTTPError as e:
        print(f"/me/ HTTP error: {e}")
        return None
    except Exception as e:
        print(f"/me/ unexpected error: {e}")
        return None

    if not isinstance(data, list) or not data:
        return None

    me = data[0]
    wallet = me.get("wallet") or {}

    # 1) direct USDC
    for key in ("USDC", "usdc"):
        if key in wallet:
            try:
                return float(wallet[key])
            except Exception:
                pass

    # 2) nested dicts that may contain USDC
    for sub_key in ("real_money", "real", "canonical", "balances"):
        sub = wallet.get(sub_key)
        if isinstance(sub, dict):
            for key in ("USDC", "usdc"):
                if key in sub:
                    try:
                        return float(sub[key])
                    except Exception:
                        pass

    # 3) generic numeric "total" style keys
    for key in ("total_usdc", "total", "total_real"):
        if key in wallet:
            try:
                return float(wallet[key])
            except Exception:
                pass

    # 4) last resort: first numeric value
    for v in wallet.values():
        try:
            return float(v)
        except Exception:
            continue

    return None


# ---------- common helpers ----------

def _extract_outcome_price(outcome: dict) -> float:
    price_val = outcome.get("price")
    if isinstance(price_val, dict):
        for v in price_val.values():
            try:
                return float(v)
            except Exception:
                continue
        return 0.0
    try:
        return float(price_val)
    except Exception:
        return 0.0


def _map_bet(raw: dict, status_label: str) -> BetRow:
    q = raw.get("question") or {}
    outcome = raw.get("outcome") or {}
    category = q.get("category") or {}

    active_purchases = raw.get("active_purchases") or []

    total_amount = 0.0
    total_shares = 0.0
    currency = None
    for p in active_purchases:
        try:
            amt = float(p.get("amount", 0.0))
            sh = float(p.get("shares", 0.0))
        except Exception:
            continue
        total_amount += amt
        total_shares += sh
        if not currency:
            currency = p.get("currency")

    if total_shares > 0:
        avg_price = total_amount / total_shares
    else:
        avg_price = 0.0

    mark_price = _extract_outcome_price(outcome)
    shares = max(total_shares, 0.0)
    mark_value = shares * mark_price
    unrealized_pnl = mark_value - total_amount if status_label == "open" else 0.0

    # Realized PnL not available via this endpoint – keep at 0.0
    realized_pnl = 0.0

    # use last_action.created with microseconds
    last_action = raw.get("last_action") or {}
    created = _parse_dt(last_action.get("created") or raw.get("created"))
    closed_dt = created if status_label == "closed" else None

    return BetRow(
        bet_id=raw.get("id"),
        question_id=q.get("id"),
        question_title=q.get("title") or "",
        outcome_id=outcome.get("id"),
        outcome_title=outcome.get("title") or "",
        category_title=category.get("title") or "",
        category_slug=category.get("slug") or "",
        position=raw.get("position") or "l",
        currency=currency or q.get("canonical_currency") or "",
        shares=shares,
        amount_invested=total_amount,
        avg_price=avg_price,
        mark_price=mark_price,
        mark_value=mark_value,
        unrealized_pnl=unrealized_pnl,
        realized_pnl=realized_pnl,
        status=status_label,
        created=created,
        closed=closed_dt,
    )


# ---------- open / closed bets ----------

def list_open_real_bets(limit: int = 200, offset: int = 0) -> Tuple[List[BetRow], Optional[str]]:
    params = {
        "currency_mode": "real_money",
        "active": True,
        "limit": limit,
        "offset": offset,
    }
    try:
        data = call_api("bets/", params=params, method="GET", auth=True)
    except HTTPError as e:
        return [], f"Error fetching open bets: {e}"
    except Exception as e:
        return [], f"Unexpected error fetching open bets: {e}"

    rows: List[BetRow] = []
    for raw in data.get("results", []):
        try:
            rows.append(_map_bet(raw, status_label="open"))
        except Exception as e:
            print(f"Error mapping open bet {raw.get('id')}: {e}")
    return rows, None


def list_closed_real_bets(limit: int = 200, offset: int = 0) -> Tuple[List[BetRow], Optional[str]]:
    params = {
        "currency_mode": "real_money",
        "past_bets": True,
        "limit": limit,
        "offset": offset,
    }
    try:
        data = call_api("bets/", params=params, method="GET", auth=True)
    except HTTPError as e:
        return [], f"Error fetching closed bets: {e}"
    except Exception as e:
        return [], f"Unexpected error fetching closed bets: {e}"

    rows: List[BetRow] = []
    for raw in data.get("results", []):
        try:
            rows.append(_map_bet(raw, status_label="closed"))
        except Exception as e:
            print(f"Error mapping closed bet {raw.get('id')}: {e}")
    return rows, None


# ---------- open limit orders ----------

def list_open_limit_orders(limit: int = 200, offset: int = 0) -> Tuple[List[LimitOrderRow], Optional[str]]:
    """
    Return open/partial user limit orders from /orders/.

    We expect question and outcome to be nested objects; we store their titles.
    Reserved notional = price * remaining_shares for bid orders.
    """
    params = {
        "currency_mode": "real_money",
        "status": "open",
        "limit": limit,
        "offset": offset,
        "currency": "USDC",
    }
    try:
        data = call_api("orders/", params=params, method="GET", auth=True)
    except HTTPError as e:
        return [], f"Error fetching open limit orders: {e}"
    except Exception as e:
        return [], f"Unexpected error fetching open limit orders: {e}"

    rows: List[LimitOrderRow] = []
    for raw in data.get("results", []):
        try:
            q = raw.get("question") or {}
            o = raw.get("outcome") or {}

            # Title extraction – avoid dumping the dict
            if isinstance(q, dict):
                question_title = q.get("title") or ""
            elif isinstance(q, str):
                question_title = q
            else:
                question_title = str(q) if q is not None else ""

            if isinstance(o, dict):
                outcome_title = o.get("title") or ""
            elif isinstance(o, str):
                outcome_title = o
            else:
                outcome_title = str(o) if o is not None else ""

            # price may already be float
            price_raw = raw.get("price", 0.0)
            try:
                price = float(price_raw)
            except Exception:
                price = 0.0

            shares_req_raw = raw.get("shares_requested", raw.get("shares", 0.0))
            try:
                shares_req = float(shares_req_raw)
            except Exception:
                shares_req = 0.0

            shares_filled_raw = raw.get("shares_filled", 0.0)
            try:
                shares_filled = float(shares_filled_raw)
            except Exception:
                shares_filled = 0.0

            remaining = max(shares_req - shares_filled, 0.0)
            reserved_notional = price * remaining if (raw.get("side") or "").lower() == "bid" else 0.0

            rows.append(
                LimitOrderRow(
                    order_id=raw.get("id"),
                    question=question_title,
                    outcome=outcome_title,
                    side=raw.get("side") or "",
                    position=raw.get("position") or "",
                    price=price,
                    shares_requested=shares_req,
                    shares_filled=shares_filled,
                    remaining_shares=remaining,
                    reserved_notional=reserved_notional,
                    currency=raw.get("currency") or "",
                    status=raw.get("status") or "",
                    created=_parse_dt(raw.get("created")),
                    expired_at=_parse_dt(raw.get("expired_at")),
                )
            )
        except Exception as e:
            print(f"Error mapping limit order {raw.get('id')}: {e}")
    return rows, None
