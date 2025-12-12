from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

from requests import HTTPError

from futuur_api_raw import call_api


# ---------- date helpers ----------


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
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
    return dt.strftime("%b %d, %y %H:%M")


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
    close_date: Optional[datetime]  # question bet_end_date

    @property
    def side_display(self) -> str:
        return "Long" if self.position == "l" else "Short"

    @property
    def created_str(self) -> str:
        return _fmt_dt(self.created)

    @property
    def closed_str(self) -> str:
        return _fmt_dt(self.closed)

    @property
    def close_date_str(self) -> str:
        return _fmt_dt(self.close_date)


@dataclass
class LimitOrderRow:
    order_id: int
    question: str
    outcome: str
    side: str  # 'bid' or 'ask'
    position: str  # 'l' or 's'
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


def _fetch_me() -> Optional[dict]:
    try:
        data = call_api("me/", params=None, method="GET", auth=True)
    except HTTPError as e:
        print(f"/me/ HTTP error: {e}")
        return None
    except Exception as e:
        print(f"/me/ unexpected error: {e}")
        return None

    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None
    return data[0]


def fetch_wallet_balance() -> Optional[float]:
    me = _fetch_me()
    if me is None:
        return None

    wallet = me.get("wallet") or {}

    for key in ("USDC", "usdc", "USDT", "usdt"):
        if key in wallet:
            try:
                return float(wallet[key])
            except Exception:
                pass

    for sub_key in ("real_money", "real", "canonical", "balances"):
        sub = wallet.get(sub_key)
        if isinstance(sub, dict):
            for key in ("USDC", "usdc", "USDT", "usdt"):
                if key in sub:
                    try:
                        return float(sub[key])
                    except Exception:
                        pass

    for key in ("total_usdc", "total_usdt", "total", "total_real"):
        if key in wallet:
            try:
                return float(wallet[key])
            except Exception:
                pass

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
        # Prefer USDT/USDC if present
        for k in ("USDT", "USDC"):
            if k in price_val:
                try:
                    return float(price_val[k])
                except Exception:
                    pass
        # Otherwise first numeric
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

    avg_price = (total_amount / total_shares) if total_shares else 0.0

    mark_price = _extract_outcome_price(outcome)

    # IMPORTANT: keep sign for shorts.
    shares = total_shares
    mark_value = shares * mark_price

    unrealized_pnl = (mark_value - total_amount) if status_label == "open" else 0.0
    realized_pnl = 0.0  # not available here

    last_action = raw.get("last_action") or {}
    created = _parse_dt(last_action.get("created") or raw.get("created"))
    closed_dt = created if status_label == "closed" else None
    close_date = _parse_dt(q.get("bet_end_date"))

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
        close_date=close_date,
    )


# ---------- open / closed bets ----------


def list_open_real_bets(limit: int = 200, offset: int = 0) -> Tuple[List[BetRow], Optional[str]]:
    params = {"currency_mode": "real_money", "active": True, "limit": limit, "offset": offset}
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
    params = {"currency_mode": "real_money", "past_bets": True, "limit": limit, "offset": offset}
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


# ---------- open limit orders (THIS FIXES YOUR ISSUE) ----------


def list_open_limit_orders(limit: int = 200, offset: int = 0) -> Tuple[List[LimitOrderRow], Optional[str]]:
    """
    Robust: try several endpoints. Only accept results that *look* user-scoped.
    If the response looks like a global order book, DO NOT display it.
    """

    base_params = {
        "currency_mode": "real_money",
        "status": "open",
        "limit": limit,
        "offset": offset,
        # IMPORTANT: do NOT filter currency unless you know your orders are only that currency.
        # Your sample orders were currency=USDT.
    }

    # These are guesses; Futuur may only support some of them.
    candidate_endpoints = [
        ("orders/", base_params),
        ("orders/me/", base_params),
        ("me/orders/", base_params),
        ("orders/", {**base_params, "mine": "true"}),
        ("orders/", {**base_params, "only_mine": "true"}),
        ("orders/", {**base_params, "owner": "me"}),
        ("orders/", {**base_params, "user": "me"}),
    ]

    def looks_global(data: object) -> bool:
        """
        Heuristic: global books are huge.
        User order lists are usually small (dozens, maybe a few hundred).
        """
        if not isinstance(data, dict):
            return False

        pag = data.get("pagination") or {}
        total = None

        if isinstance(pag, dict) and "total" in pag:
            try:
                total = int(pag["total"])
            except Exception:
                total = None

        if total is None and "count" in data:
            try:
                total = int(data["count"])
            except Exception:
                total = None

        results = data.get("results") or []
        n = len(results) if isinstance(results, list) else 0

        # Hard guardrails:
        # - if total is massive, it's global.
        # - if we got near-full pages repeatedly and total missing, also likely global.
        if total is not None and total > 500:
            return True

        # If the response is giving you hundreds of open orders consistently, it’s probably not “just you”.
        if total is None and n >= 300:
            return True

        return False

    errors: List[str] = []

    for endpoint, params in candidate_endpoints:
        try:
            data = call_api(endpoint, params=params, method="GET", auth=True)
        except Exception as e:
            errors.append(f"{endpoint}: {e}")
            continue

        # Normalize results
        if isinstance(data, dict):
            results = data.get("results") or []
        elif isinstance(data, list):
            results = data
        else:
            errors.append(f"{endpoint}: unexpected response type {type(data)}")
            continue

        if looks_global(data):
            # reject and continue trying others
            errors.append(f"{endpoint}: looks like GLOBAL order book (rejected)")
            continue

        # Map rows
        rows: List[LimitOrderRow] = []
        for raw in results:
            try:
                q = raw.get("question") or {}
                o = raw.get("outcome") or {}

                question_title = q.get("title") if isinstance(q, dict) else (q or "")
                outcome_title = o.get("title") if isinstance(o, dict) else (o or "")

                price = float(raw.get("price") or 0.0)
                shares_req = float(raw.get("shares_requested", raw.get("shares", 0.0)) or 0.0)
                shares_filled = float(raw.get("shares_filled") or 0.0)
                remaining = max(shares_req - shares_filled, 0.0)

                side = (raw.get("side") or "").lower()
                reserved_notional = price * remaining if side == "bid" else 0.0

                rows.append(
                    LimitOrderRow(
                        order_id=raw.get("id"),
                        question=str(question_title or ""),
                        outcome=str(outcome_title or ""),
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
                errors.append(f"{endpoint}: map error on order {raw.get('id')}: {e}")

        # Accepted endpoint.
        return rows, None

    # If we got here, we refused to show global or all endpoints failed.
    msg = " | ".join(errors[-6:]) if errors else "Could not fetch user open limit orders"
    return [], f"Unable to load YOUR open limit orders safely (refused to display global book). Details: {msg}"
