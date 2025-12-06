from __future__ import annotations

import csv
import io
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, Response, render_template_string, request, url_for

from config import APP_HOST, APP_PORT, BANKROLL_USD
from futuur_api_raw import call_api
from portfolio_client import (
    BetRow,
    LimitOrderRow,
    fetch_wallet_balance,
    list_closed_real_bets,
    list_open_limit_orders,
    list_open_real_bets,
)

app = Flask(__name__)

# ---------- shared date / time helpers ----------


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


def _days_to_close(bet_end: Optional[datetime]) -> Optional[float]:
    if not bet_end:
        return None
    now = datetime.now(tz=timezone.utc)
    delta = bet_end - now
    return delta.total_seconds() / 86400.0


def _human_delta(bet_end: Optional[datetime]) -> str:
    if not bet_end:
        return "-"
    now = datetime.now(tz=timezone.utc)
    delta = bet_end - now
    seconds = int(delta.total_seconds())
    sign = "" if seconds >= 0 else "-"
    seconds = abs(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return sign + " ".join(parts)


def _classify_group(cat_title: str, cat_slug: str) -> str:
    text = f"{cat_title} {cat_slug}".lower()
    if "sport" in text:
        return "Sports"
    if any(w in text for w in ("finance", "econom", "market", "stock", "crypto", "inflation", "gdp", "bank")):
        return "Finance"
    if any(w in text for w in ("politic", "election", "government", "policy", "geopolit")):
        return "Politics"
    if any(w in text for w in ("science", "space", "climate", "physics", "biology", "tech", "ai", "technology")):
        return "Science"
    if any(w in text for w in ("entertainment", "celebrity", "movies", "tv", "music", "hollywood", "culture", "award")):
        return "Entertainment"
    return "Other"


def _compute_bankroll() -> Tuple[float, str, Optional[float]]:
    """Use manual override if given, else wallet API, else config default."""
    override_str = (request.args.get("bankroll") or "").strip()
    if override_str:
        try:
            val = float(override_str)
            if val > 0:
                return val, "manual", None
        except ValueError:
            pass

    wallet = fetch_wallet_balance()
    if wallet is not None and wallet > 0:
        return wallet, "wallet", wallet

    return float(BANKROLL_USD), "default", None


def _sort_rows(rows: List[Dict[str, Any]], sort_by: str, sort_dir: str) -> List[Dict[str, Any]]:
    reverse = sort_dir == "desc"

    def key_fn(r: Dict[str, Any]) -> Any:
        v = r.get(sort_by)
        if isinstance(v, str):
            return v.lower()
        return v

    try:
        return sorted(rows, key=key_fn, reverse=reverse)
    except TypeError:
        return rows


# ---------- markets: fetch + filter helper (used by index and export) ----------


def _load_markets_rows_for_request(args) -> Tuple[
    List[Dict[str, Any]],
    str,
    str,
    str,
    str,
    str,
    List[str],
]:
    q = (args.get("q") or "").strip()
    selected_groups = args.getlist("group")
    min_vol_str = (args.get("min_vol") or "").strip()
    max_days_str = (args.get("max_days") or "").strip()
    sort_by = args.get("sort_by") or "created_on"
    sort_dir = args.get("sort_dir") or "desc"

    params = {
        "limit": 200,
        "offset": 0,
        "ordering": "-created_on",
        "currency_mode": "real_money",
    }
    data = call_api("markets/", params=params, method="GET", auth=True)
    now = datetime.now(tz=timezone.utc)

    rows: List[Dict[str, Any]] = []

    for raw in data.get("results", []):
        cat = raw.get("category") or {}
        cat_title = cat.get("title") or ""
        cat_slug = cat.get("slug") or ""
        group = _classify_group(cat_title, cat_slug)
        outcomes = raw.get("outcomes") or []

        n_outcomes = max(len(outcomes), 1)
        base_p = 1.0 / n_outcomes

        bet_end = _parse_dt(raw.get("bet_end_date"))
        created_on = _parse_dt(raw.get("created_on"))
        volume_real = float(raw.get("volume_real_money") or 0.0)

        for outcome in outcomes:
            price_val = outcome.get("price")
            try:
                s = float(price_val)
            except Exception:
                try:
                    s = float(next(iter(price_val.values()))) if isinstance(price_val, dict) else 0.0
                except Exception:
                    s = 0.0

            edge0 = base_p - s
            days_to_close = _days_to_close(bet_end)

            row = {
                "question_id": raw.get("id"),
                "title": raw.get("title") or "",
                "slug": raw.get("slug") or "",
                "outcome_id": outcome.get("id"),
                "outcome_title": outcome.get("title") or "",
                "group": group,
                "category_title": cat_title,
                "category_slug": cat_slug,
                "tags": [t.get("name") for t in (raw.get("tags") or [])],
                "s": s,
                "p0": base_p,
                "edge0": edge0,
                "bet_end_date": bet_end,
                "bet_end_str": bet_end.strftime("%b %d, %y %H:%M") if bet_end else "-",
                "created_on": created_on or now,
                "created_str": (created_on or now).strftime("%b %d, %y %H:%M"),
                "volume_real": volume_real,
                "days_to_close": days_to_close,
                "days_to_close_str": _human_delta(bet_end),
                "url": f"https://www.futuur.com/markets/{raw.get('slug')}",
            }

            rows.append(row)

    # Filtering
    if selected_groups:
        rows = [r for r in rows if r["group"] in selected_groups]

    if q:
        q_lower = q.lower()
        rows = [
            r
            for r in rows
            if q_lower in r["title"].lower()
            or q_lower in r["outcome_title"].lower()
            or any(q_lower in t.lower() for t in r["tags"])
        ]

    if min_vol_str:
        try:
            min_vol = float(min_vol_str)
            rows = [r for r in rows if r["volume_real"] >= min_vol]
        except ValueError:
            pass

    if max_days_str:
        try:
            max_days = float(max_days_str)
            rows = [r for r in rows if r["days_to_close"] is None or r["days_to_close"] <= max_days]
        except ValueError:
            pass

    # Sorting
    if sort_by not in {
        "title",
        "group",
        "s",
        "p0",
        "edge0",
        "volume_real",
        "bet_end_date",
        "created_on",
        "days_to_close",
    }:
        sort_by = "created_on"
    rows = _sort_rows(rows, sort_by=sort_by, sort_dir=sort_dir)

    return rows, q, min_vol_str, max_days_str, sort_by, sort_dir, selected_groups


# ---------- markets page ----------


@app.route("/")
def index() -> str:
    bankroll, bankroll_source, wallet_balance = _compute_bankroll()
    bankroll_input = request.args.get("bankroll") or f"{bankroll:.2f}"

    rows, q, min_vol_str, max_days_str, sort_by, sort_dir, selected_groups = _load_markets_rows_for_request(
        request.args
    )

    def col_sort_url(column: str) -> str:
        params = dict(request.args)
        current = params.get("sort_by", "created_on")
        dir_now = params.get("sort_dir", "desc")
        if current == column:
            new_dir = "asc" if dir_now == "desc" else "desc"
        else:
            new_dir = "desc"
        params["sort_by"] = column
        params["sort_dir"] = new_dir
        return url_for("index", **params)

    template = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Futuur Scanner – Markets</title>
    <style>
      body { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; padding: 0; background: #0b1120; color: #e5e7eb; }
      header { padding: 12px 24px; background: #020617; display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #1f2937; }
      a { color: #60a5fa; text-decoration: none; }
      a:hover { text-decoration: underline; }
      .nav-links a { margin-right: 16px; }
      .nav-links a.active { font-weight: 600; color: #facc15; }
      main { padding: 16px 24px 32px; }
      .filters { display:flex; flex-wrap:wrap; gap:12px; margin-bottom:16px; align-items:flex-end; }
      .filters label { font-size: 12px; color:#9ca3af; display:block; margin-bottom:4px; }
      .filters input[type="text"],
      .filters input[type="number"],
      .filters select { padding:4px 6px; border-radius:4px; border:1px solid #374151; background:#020617; color:#e5e7eb; font-size:13px; min-width:100px; }
      .filters .group-select { display:flex; flex-wrap:wrap; gap:8px; font-size:12px; }
      .chip { padding:4px 8px; border-radius:999px; border:1px solid #374151; cursor:pointer; }
      .chip.selected { background:#4b5563; border-color:#9ca3af; }
      button { padding:6px 10px; border-radius:4px; border:none; background:#2563eb; color:white; font-size:13px; cursor:pointer; }
      button.secondary { background:#111827; border:1px solid #374151; }
      table { width:100%; border-collapse:collapse; font-size:12px; margin-top:8px; }
      th, td { padding:6px 8px; border-bottom:1px solid #111827; vertical-align:top; }
      th { text-align:left; font-size:11px; color:#9ca3af; white-space:nowrap; }
      th a { color:inherit; }
      tr:nth-child(even) { background:#020617; }
      tr:nth-child(odd) { background:#020617; }
      .tag-pill { display:inline-block; padding:2px 6px; border-radius:999px; background:#111827; margin-right:4px; margin-bottom:2px; font-size:10px; }
      .pill { display:inline-block; padding:2px 6px; border-radius:999px; font-size:10px; }
      .pill.finance { background:#0f172a; color:#22c55e; }
      .pill.entertainment { background:#0f172a; color:#f97316; }
      .pill.politics { background:#0f172a; color:#facc15; }
      .pill.science { background:#0f172a; color:#22d3ee; }
      .pill.sports { background:#0f172a; color:#34d399; }
      .stat-bar { display:flex; gap:16px; font-size:12px; margin-bottom:10px; color:#9ca3af; flex-wrap:wrap; }
      .stat-bar span.value { color:#e5e7eb; font-weight:500; }
    </style>
  </head>
  <body>
    <header>
      <div class="nav-links">
        <a href="{{ url_for('index') }}" class="active">Markets</a>
        <a href="{{ url_for('portfolio') }}">Portfolio</a>
      </div>
      <form method="get" action="{{ url_for('index') }}" style="display:flex; align-items:center; gap:8px;">
        <label style="font-size:11px; color:#9ca3af;">
          Bankroll (USD)
          <input type="number" step="0.01" name="bankroll" value="{{ bankroll_input }}" style="padding:4px 6px; border-radius:4px; border:1px solid #374151; background:#020617; color:#e5e7eb; width:110px;">
        </label>
        <span style="font-size:11px; color:#6b7280;">
          Source: {{ bankroll_source }}{% if wallet_balance is not none %} (wallet ~ {{ '%.2f' % wallet_balance }}){% endif %}
        </span>
        <button type="submit" class="secondary">Apply</button>
      </form>
    </header>
    <main>
      <div class="stat-bar">
        <span>Markets: <span class="value">{{ rows|length }}</span></span>
        <span>Sort: <span class="value">{{ sort_by }} {{ sort_dir }}</span></span>
      </div>

      <form method="get" action="{{ url_for('index') }}">
        <input type="hidden" name="bankroll" value="{{ bankroll_input }}">
        <div class="filters">
          <div>
            <label>Search</label>
            <input type="text" name="q" value="{{ q }}" placeholder="title / outcome / tag">
          </div>
          <div>
            <label>Min real vol (USDC)</label>
            <input type="number" step="0.01" name="min_vol" value="{{ min_vol_str }}">
          </div>
          <div>
            <label>Max days to close</label>
            <input type="number" step="1" name="max_days" value="{{ max_days_str }}">
          </div>
          <div>
            <label>Categories</label>
            <div class="group-select">
              {% for g in ["All","Finance","Entertainment","Politics","Science","Sports"] %}
                {% set selected = (not selected_groups and g == "All") or (g != "All" and g in selected_groups) %}
                <label class="chip {% if selected %}selected{% endif %}">
                  <input type="checkbox" name="group" value="{{ g }}" style="display:none;" {% if g in selected_groups %}checked{% endif %}>
                  {{ g }}
                </label>
              {% endfor %}
            </div>
          </div>
          <div>
            <label>Sort by</label>
            <select name="sort_by">
              <option value="created_on" {% if sort_by == "created_on" %}selected{% endif %}>Created</option>
              <option value="bet_end_date" {% if sort_by == "bet_end_date" %}selected{% endif %}>Bet end</option>
              <option value="s" {% if sort_by == "s" %}selected{% endif %}>Price</option>
              <option value="p0" {% if sort_by == "p0" %}selected{% endif %}>p₀</option>
              <option value="edge0" {% if sort_by == "edge0" %}selected{% endif %}>Edge₀</option>
              <option value="volume_real" {% if sort_by == "volume_real" %}selected{% endif %}>Real vol</option>
              <option value="days_to_close" {% if sort_by == "days_to_close" %}selected{% endif %}>Days to close</option>
            </select>
          </div>
          <div>
            <label>Direction</label>
            <select name="sort_dir">
              <option value="desc" {% if sort_dir == "desc" %}selected{% endif %}>Desc</option>
              <option value="asc" {% if sort_dir == "asc" %}selected{% endif %}>Asc</option>
            </select>
          </div>
          <div>
            <label>&nbsp;</label>
            <button type="submit">Apply filters</button>
          </div>
        </div>
      </form>

      <form method="get" action="{{ url_for('export_markets_csv') }}">
        <input type="hidden" name="bankroll" value="{{ bankroll_input }}">
        <input type="hidden" name="q" value="{{ q }}">
        <input type="hidden" name="min_vol" value="{{ min_vol_str }}">
        <input type="hidden" name="max_days" value="{{ max_days_str }}">
        <input type="hidden" name="sort_by" value="{{ sort_by }}">
        <input type="hidden" name="sort_dir" value="{{ sort_dir }}">
        {% for g in selected_groups %}
          <input type="hidden" name="group" value="{{ g }}">
        {% endfor %}

        <div style="margin-bottom:6px; font-size:11px; color:#9ca3af;">
          Select rows and click "Export selected to CSV". If none selected, all visible rows are exported.
        </div>

        <table>
          <thead>
            <tr>
              <th></th>
              <th><a href="{{ col_sort_url('group') }}">Group</a></th>
              <th><a href="{{ col_sort_url('title') }}">Market</a></th>
              <th><a href="{{ col_sort_url('outcome_title') }}">Outcome</a></th>
              <th>Tags</th>
              <th><a href="{{ col_sort_url('s') }}">Price</a></th>
              <th><a href="{{ col_sort_url('p0') }}">p₀</a></th>
              <th><a href="{{ col_sort_url('edge0') }}">Edge₀</a></th>
              <th><a href="{{ col_sort_url('volume_real') }}">Vol (real)</a></th>
              <th><a href="{{ col_sort_url('bet_end_date') }}">Closes</a></th>
              <th><a href="{{ col_sort_url('days_to_close') }}">Δt</a></th>
              <th><a href="{{ col_sort_url('created_on') }}">Created</a></th>
            </tr>
          </thead>
          <tbody>
            {% for r in rows %}
            <tr>
              <td><input type="checkbox" name="sel" value="{{ r.question_id }}:{{ r.outcome_id }}"></td>
              <td>
                <span class="pill {% if r.group == 'Finance' %}finance{% elif r.group == 'Entertainment' %}entertainment{% elif r.group == 'Politics' %}politics{% elif r.group == 'Science' %}science{% elif r.group == 'Sports' %}sports{% endif %}">
                  {{ r.group }}
                </span>
              </td>
              <td>
                <a href="{{ r.url }}" target="_blank">{{ r.title }}</a>
              </td>
              <td>{{ r.outcome_title }}</td>
              <td>
                {% for t in r.tags %}
                  <span class="tag-pill">{{ t }}</span>
                {% endfor %}
              </td>
              <td>{{ "%.3f"|format(r.s) }}</td>
              <td>{{ "%.3f"|format(r.p0) }}</td>
              <td{% if r.edge0 > 0 %} style="color:#22c55e;"{% elif r.edge0 < 0 %} style="color:#f97316;"{% endif %}>
                {{ "%.3f"|format(r.edge0) }}
              </td>
              <td>{{ "%.2f"|format(r.volume_real) }}</td>
              <td>{{ r.bet_end_str }}</td>
              <td>{{ r.days_to_close_str }}</td>
              <td>{{ r.created_str }}</td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
        <div style="margin-top:8px;">
          <button type="submit">Export selected to CSV</button>
        </div>
      </form>
    </main>
  </body>
</html>
    """
    return render_template_string(
        template,
        rows=rows,
        q=q,
        min_vol_str=min_vol_str,
        max_days_str=max_days_str,
        sort_by=sort_by,
        sort_dir=sort_dir,
        selected_groups=selected_groups,
        bankroll=bankroll,
        bankroll_input=bankroll_input,
        bankroll_source=bankroll_source,
        wallet_balance=wallet_balance,
        col_sort_url=col_sort_url,
    )


# ---------- markets CSV export ----------


@app.route("/markets/export")
def export_markets_csv() -> Response:
    rows, q, min_vol_str, max_days_str, sort_by, sort_dir, selected_groups = _load_markets_rows_for_request(
        request.args
    )

    selected_ids = request.args.getlist("sel")
    if selected_ids:
        sel_set = set(selected_ids)
        rows = [
            r
            for r in rows
            if f"{r['question_id']}:{r['outcome_id']}" in sel_set
        ]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "question_id",
            "outcome_id",
            "title",
            "outcome_title",
            "group",
            "category_title",
            "category_slug",
            "tags",
            "s",
            "p0",
            "edge0",
            "volume_real",
            "bet_end",
            "days_to_close",
            "url",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r["question_id"],
                r["outcome_id"],
                r["title"],
                r["outcome_title"],
                r["group"],
                r["category_title"],
                r["category_slug"],
                ";".join(r["tags"]),
                f"{r['s']:.4f}",
                f"{r['p0']:.4f}",
                f"{r['edge0']:.4f}",
                f"{r['volume_real']:.2f}",
                r["bet_end_str"],
                f"{r['days_to_close']:.2f}" if r["days_to_close"] is not None else "",
                r["url"],
            ]
        )

    csv_data = output.getvalue()
    output.close()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=futuur_markets.csv"},
    )


# ---------- portfolio helpers ----------


def _sort_bets(bets: List[BetRow], sort_key: str, sort_dir: str) -> List[BetRow]:
    reverse = sort_dir == "desc"

    def key_fn(b: BetRow):
        if sort_key == "title":
            return (b.question_title or "").lower()
        if sort_key == "created":
            return b.created or datetime.min.replace(tz=timezone.utc)
        if sort_key == "amount":
            return b.amount_invested
        if sort_key == "value":
            return b.mark_value
        if sort_key == "unrealized":
            return b.unrealized_pnl
        if sort_key == "realized":
            return b.realized_pnl
        if sort_key == "pct":
            return getattr(b, "pct_bankroll", 0.0)
        return b.bet_id

    try:
        return sorted(bets, key=key_fn, reverse=reverse)
    except TypeError:
        return bets


def _sort_orders(orders: List[LimitOrderRow], sort_key: str, sort_dir: str) -> List[LimitOrderRow]:
    reverse = sort_dir == "desc"

    def key_fn(o: LimitOrderRow):
        if sort_key == "market":
            return (o.question or "").lower()
        if sort_key == "outcome":
            return (o.outcome or "").lower()
        if sort_key == "price":
            return o.price
        if sort_key == "remaining":
            return o.remaining_shares
        if sort_key == "reserved":
            return o.reserved_notional
        if sort_key == "created":
            return o.created or datetime.min.replace(tz=timezone.utc)
        if sort_key == "expires":
            return o.expired_at or (datetime.max.replace(tz=timezone.utc) - timedelta(days=365))
        return o.order_id

    try:
        return sorted(orders, key=key_fn, reverse=reverse)
    except TypeError:
        return orders


# ---------- portfolio page ----------


@app.route("/portfolio")
def portfolio() -> str:
    bankroll, bankroll_source, wallet_balance = _compute_bankroll()
    bankroll_input = request.args.get("bankroll") or f"{bankroll:.2f}"

    sort_open = request.args.get("sort_open") or "value"
    dir_open = request.args.get("dir_open") or "desc"
    sort_closed = request.args.get("sort_closed") or "closed"
    dir_closed = request.args.get("dir_closed") or "desc"
    sort_orders = request.args.get("sort_orders") or "created"
    dir_orders = request.args.get("dir_orders") or "desc"

    open_bets, open_err = list_open_real_bets(limit=500)
    closed_bets, closed_err = list_closed_real_bets(limit=500)
    open_orders, orders_err = list_open_limit_orders(limit=500)

    if bankroll > 0:
        for b in open_bets:
            b.pct_bankroll = (b.mark_value / bankroll) * 100.0 if b.mark_value else 0.0
    else:
        for b in open_bets:
            b.pct_bankroll = 0.0

    total_open_notional = sum(b.mark_value for b in open_bets)
    total_unrealized = sum(b.unrealized_pnl for b in open_bets)
    total_realized = sum(b.realized_pnl for b in closed_bets)
    reserved_notional = sum(o.reserved_notional for o in open_orders)
    total_exposure = total_open_notional + reserved_notional

    open_bets_sorted = _sort_bets(open_bets, sort_key=sort_open, sort_dir=dir_open)
    closed_bets_sorted = _sort_bets(closed_bets, sort_key=sort_closed, sort_dir=dir_closed)
    open_orders_sorted = _sort_orders(open_orders, sort_key=sort_orders, sort_dir=dir_orders)

    def sort_url(table: str, column: str) -> str:
        params = dict(request.args)
        key = f"sort_{table}"
        dir_key = f"dir_{table}"
        current = params.get(key, "created")
        dir_now = params.get(dir_key, "desc")
        if current == column:
            new_dir = "asc" if dir_now == "desc" else "desc"
        else:
            new_dir = "desc"
        params[key] = column
        params[dir_key] = new_dir
        params["bankroll"] = bankroll_input
        return url_for("portfolio", **params)

    template = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Futuur Scanner – Portfolio</title>
    <style>
      body { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin:0; padding:0; background:#020617; color:#e5e7eb; }
      header { padding:12px 24px; background:#020617; display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #1f2937; }
      a { color:#60a5fa; text-decoration:none; }
      a:hover { text-decoration:underline; }
      .nav-links a { margin-right:16px; }
      .nav-links a.active { font-weight:600; color:#facc15; }
      main { padding:16px 24px 32px; }
      h2 { margin-top:24px; margin-bottom:8px; font-size:15px; }
      table { width:100%; border-collapse:collapse; font-size:12px; margin-top:8px; }
      th, td { padding:6px 8px; border-bottom:1px solid #111827; vertical-align:top; }
      th { text-align:left; font-size:11px; color:#9ca3af; white-space:nowrap; }
      th a { color:inherit; }
      tr:nth-child(even) { background:#020617; }
      tr:nth-child(odd) { background:#020617; }
      .stat-bar { display:flex; flex-wrap:wrap; gap:16px; font-size:12px; margin-bottom:10px; color:#9ca3af; }
      .stat-bar span.value { color:#e5e7eb; font-weight:500; }
      .pill { display:inline-block; padding:2px 6px; border-radius:999px; font-size:10px; }
      .pill.gain { background:#064e3b; color:#4ade80; }
      .pill.loss { background:#7f1d1d; color:#fecaca; }
      button { padding:6px 10px; border-radius:4px; border:none; background:#2563eb; color:white; font-size:13px; cursor:pointer; }
      button.secondary { background:#111827; border:1px solid #374151; }
      .error { color:#f97316; font-size:11px; margin-top:4px; }
    </style>
  </head>
  <body>
    <header>
      <div class="nav-links">
        <a href="{{ url_for('index') }}">Markets</a>
        <a href="{{ url_for('portfolio') }}" class="active">Portfolio</a>
      </div>
      <form method="get" action="{{ url_for('portfolio') }}" style="display:flex; align-items:center; gap:8px;">
        <label style="font-size:11px; color:#9ca3af;">
          Bankroll (USD)
          <input type="number" step="0.01" name="bankroll" value="{{ bankroll_input }}" style="padding:4px 6px; border-radius:4px; border:1px solid #374151; background:#020617; color:#e5e7eb; width:110px;">
        </label>
        <span style="font-size:11px; color:#6b7280;">
          Source: {{ bankroll_source }}{% if wallet_balance is not none %} (wallet ~ {{ '%.2f' % wallet_balance }}){% endif %}
        </span>
        <button type="submit" class="secondary">Apply</button>
        <a href="{{ url_for('export_portfolio_csv', bankroll=bankroll_input) }}"><button type="button">Export CSV</button></a>
      </form>
    </header>
    <main>
      <div class="stat-bar">
        <span>Bankroll: <span class="value">{{ '%.2f' % bankroll }}</span></span>
        <span>Open notional: <span class="value">{{ '%.2f' % total_open_notional }}</span></span>
        <span>Reserved (limits): <span class="value">{{ '%.2f' % reserved_notional }}</span></span>
        <span>Exposure: <span class="value">{{ '%.2f' % total_exposure }}</span></span>
        <span>Unrealized PnL: <span class="value {% if total_unrealized >= 0 %}pill gain{% else %}pill loss{% endif %}">{{ '%.2f' % total_unrealized }}</span></span>
        <span>Realized PnL (approx): <span class="value {% if total_realized >= 0 %}pill gain{% else %}pill loss{% endif %}">{{ '%.2f' % total_realized }}</span></span>
      </div>

      {% if open_err %}<div class="error">Open bets error: {{ open_err }}</div>{% endif %}
      {% if closed_err %}<div class="error">Closed bets error: {{ closed_err }}</div>{% endif %}
      {% if orders_err %}<div class="error">Limit orders error: {{ orders_err }}</div>{% endif %}

      <h2>Open positions ({{ open_bets_sorted|length }})</h2>
      <table>
        <thead>
          <tr>
            <th><a href="{{ sort_url('open','title') }}">Market</a></th>
            <th>Outcome</th>
            <th>Side</th>
            <th><a href="{{ sort_url('open','amount') }}">Amount in</a></th>
            <th>Shares</th>
            <th>Avg price</th>
            <th>Mark price</th>
            <th><a href="{{ sort_url('open','value') }}">Mark value</a></th>
            <th><a href="{{ sort_url('open','unrealized') }}">Unrealized PnL</a></th>
            <th><a href="{{ sort_url('open','pct') }}">% bankroll</a></th>
            <th><a href="{{ sort_url('open','created') }}">Created</a></th>
          </tr>
        </thead>
        <tbody>
          {% for b in open_bets_sorted %}
          <tr>
            <td>{{ b.question_title }}</td>
            <td>{{ b.outcome_title }}</td>
            <td>{{ b.side_display }}</td>
            <td>{{ '%.2f' % b.amount_invested }}</td>
            <td>{{ '%.4f' % b.shares }}</td>
            <td>{{ '%.3f' % b.avg_price }}</td>
            <td>{{ '%.3f' % b.mark_price }}</td>
            <td>{{ '%.2f' % b.mark_value }}</td>
            <td class="{% if b.unrealized_pnl >= 0 %}pill gain{% else %}pill loss{% endif %}">{{ '%.2f' % b.unrealized_pnl }}</td>
            <td>{{ '%.2f' % b.pct_bankroll }}%</td>
            <td>{{ b.created_str }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>

      <h2>Open limit orders ({{ open_orders_sorted|length }})</h2>
      <table>
        <thead>
          <tr>
            <th><a href="{{ sort_url('orders','market') }}">Market</a></th>
            <th><a href="{{ sort_url('orders','outcome') }}">Outcome</a></th>
            <th>Side</th>
            <th>Pos</th>
            <th><a href="{{ sort_url('orders','price') }}">Price</a></th>
            <th>Requested</th>
            <th>Filled</th>
            <th><a href="{{ sort_url('orders','remaining') }}">Remaining</a></th>
            <th><a href="{{ sort_url('orders','reserved') }}">Reserved notional</a></th>
            <th>Status</th>
            <th><a href="{{ sort_url('orders','created') }}">Created</a></th>
            <th><a href="{{ sort_url('orders','expires') }}">Expires</a></th>
          </tr>
        </thead>
        <tbody>
          {% for o in open_orders_sorted %}
          <tr>
            <td>{{ o.question }}</td>
            <td>{{ o.outcome }}</td>
            <td>{{ o.side }}</td>
            <td>{{ o.position }}</td>
            <td>{{ '%.3f' % o.price }}</td>
            <td>{{ '%.4f' % o.shares_requested }}</td>
            <td>{{ '%.4f' % o.shares_filled }}</td>
            <td>{{ '%.4f' % o.remaining_shares }}</td>
            <td>{{ '%.2f' % o.reserved_notional }}</td>
            <td>{{ o.status }}</td>
            <td>{{ o.created_str }}</td>
            <td>{{ o.expired_str }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>

      <h2>Closed bets ({{ closed_bets_sorted|length }})</h2>
      <table>
        <thead>
          <tr>
            <th><a href="{{ sort_url('closed','title') }}">Market</a></th>
            <th>Outcome</th>
            <th>Side</th>
            <th><a href="{{ sort_url('closed','amount') }}">Amount in (approx)</a></th>
            <th><a href="{{ sort_url('closed','realized') }}">Realized PnL (placeholder)</a></th>
            <th><a href="{{ sort_url('closed','created') }}">Closed</a></th>
          </tr>
        </thead>
        <tbody>
          {% for b in closed_bets_sorted %}
          <tr>
            <td>{{ b.question_title }}</td>
            <td>{{ b.outcome_title }}</td>
            <td>{{ b.side_display }}</td>
            <td>{{ '%.2f' % b.amount_invested }}</td>
            <td class="{% if b.realized_pnl >= 0 %}pill gain{% else %}pill loss{% endif %}">{{ '%.2f' % b.realized_pnl }}</td>
            <td>{{ b.closed_str }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>

    </main>
  </body>
</html>
    """
    return render_template_string(
        template,
        bankroll=bankroll,
        bankroll_input=bankroll_input,
        bankroll_source=bankroll_source,
        wallet_balance=wallet_balance,
        total_open_notional=total_open_notional,
        reserved_notional=reserved_notional,
        total_exposure=total_exposure,
        total_unrealized=total_unrealized,
        total_realized=total_realized,
        open_bets_sorted=open_bets_sorted,
        closed_bets_sorted=closed_bets_sorted,
        open_orders_sorted=open_orders_sorted,
        open_err=open_err,
        closed_err=closed_err,
        orders_err=orders_err,
        sort_url=sort_url,
    )


# ---------- portfolio CSV export ----------


@app.route("/portfolio/export")
def export_portfolio_csv() -> Response:
    bankroll, _, _ = _compute_bankroll()

    open_bets, _ = list_open_real_bets(limit=500)
    closed_bets, _ = list_closed_real_bets(limit=500)
    open_orders, _ = list_open_limit_orders(limit=500)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "type",
            "id",
            "market",
            "outcome",
            "side",
            "position",
            "amount_in",
            "shares",
            "avg_price",
            "mark_price",
            "mark_value",
            "unrealized_pnl",
            "realized_pnl",
            "pct_bankroll",
            "created",
            "closed_or_expires",
        ]
    )

    # Open bets
    for b in open_bets:
        pct = (b.mark_value / bankroll) * 100.0 if bankroll > 0 and b.mark_value else 0.0
        writer.writerow(
            [
                "open_bet",
                b.bet_id,
                b.question_title,
                b.outcome_title,
                b.side_display,
                b.position,
                f"{b.amount_invested:.2f}",
                f"{b.shares:.4f}",
                f"{b.avg_price:.4f}",
                f"{b.mark_price:.4f}",
                f"{b.mark_value:.2f}",
                f"{b.unrealized_pnl:.2f}",
                "",
                f"{pct:.2f}",
                b.created.isoformat() if b.created else "",
                "",
            ]
        )

    # Closed bets
    for b in closed_bets:
        writer.writerow(
            [
                "closed_bet",
                b.bet_id,
                b.question_title,
                b.outcome_title,
                b.side_display,
                b.position,
                f"{b.amount_invested:.2f}",
                f"{b.shares:.4f}",
                f"{b.avg_price:.4f}",
                f"{b.mark_price:.4f}",
                "",
                "",
                f"{b.realized_pnl:.2f}",
                "",
                b.created.isoformat() if b.created else "",
                b.closed.isoformat() if b.closed else "",
            ]
        )

    # Open limit orders
    for o in open_orders:
        writer.writerow(
            [
                "limit_order",
                o.order_id,
                o.question,
                o.outcome,
                o.side,
                o.position,
                "",
                f"{o.shares_requested:.4f}",
                f"{o.price:.4f}",
                "",
                "",
                "",
                "",
                "",
                o.created.isoformat() if o.created else "",
                o.expired_at.isoformat() if o.expired_at else "",
            ]
        )

    csv_data = output.getvalue()
    output.close()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=futuur_portfolio.csv"},
    )


if __name__ == "__main__":
    app.run(host=APP_HOST, port=APP_PORT, debug=True)
