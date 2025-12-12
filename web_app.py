from __future__ import annotations

import csv
import io
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, Response, render_template_string, request, url_for

from config import APP_HOST, APP_PORT, BANKROLL_USD
from futuur_api_raw import call_api
from portfolio_client import (
    fetch_wallet_balance,
    list_closed_real_bets,
    list_open_limit_orders,
    list_open_real_bets,
)

app = Flask(__name__)


# ---------- time helpers ----------

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


def _days_to_close(bet_end: Optional[datetime]) -> Optional[float]:
    if not bet_end:
        return None
    now = datetime.now(tz=timezone.utc)
    return (bet_end - now).total_seconds() / 86400.0


def _human_delta(bet_end: Optional[datetime]) -> str:
    if not bet_end:
        return "-"
    now = datetime.now(tz=timezone.utc)
    seconds = int((bet_end - now).total_seconds())
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


# ---------- classification ----------

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


# ---------- bankroll ----------

def _compute_bankroll() -> Tuple[float, str, Optional[float]]:
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


# ---------- sorting ----------

def _sort_rows(rows: List[Dict[str, Any]], sort_by: str, sort_dir: str) -> List[Dict[str, Any]]:
    reverse = sort_dir == "desc"

    def key_fn(r: Dict[str, Any]) -> Any:
        v = r.get(sort_by)
        return v.lower() if isinstance(v, str) else v

    try:
        return sorted(rows, key=key_fn, reverse=reverse)
    except TypeError:
        return rows


# ---------- markets: fetch + filter (shared by page + export) ----------

def _load_markets_rows_for_request(
    args,
) -> Tuple[List[Dict[str, Any]], Dict[str, int], str, str, str, str, str, List[str], bool]:
    q = (args.get("q") or "").strip()
    selected_groups = [g for g in args.getlist("group") if g and g != "All"]
    min_vol_str = (args.get("min_vol") or "").strip()
    max_days_str = (args.get("max_days") or "").strip()
    sort_by = args.get("sort_by") or "created_on"
    sort_dir = args.get("sort_dir") or "desc"
    show_ids = (args.get("show_ids") or "").strip() == "1"

    params = {"limit": 200, "offset": 0, "ordering": "-created_on", "currency_mode": "real_money"}
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

            rows.append(
                {
                    "question_id": raw.get("id"),
                    "outcome_id": outcome.get("id"),
                    "title": raw.get("title") or "",
                    "slug": raw.get("slug") or "",
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
            )

    # Apply q/min_vol/max_days first (so category counts reflect these)
    if q:
        ql = q.lower()
        rows = [
            r
            for r in rows
            if ql in r["title"].lower()
            or ql in r["outcome_title"].lower()
            or any(ql in t.lower() for t in r["tags"])
        ]

    if min_vol_str:
        try:
            mv = float(min_vol_str)
            rows = [r for r in rows if r["volume_real"] >= mv]
        except ValueError:
            pass

    if max_days_str:
        try:
            md = float(max_days_str)
            rows = [r for r in rows if r["days_to_close"] is None or r["days_to_close"] <= md]
        except ValueError:
            pass

    # Category counts (post q/min/max, pre group filter)
    group_counts: Dict[str, int] = {}
    for r in rows:
        group_counts[r["group"]] = group_counts.get(r["group"], 0) + 1

    # Now apply group filter
    if selected_groups:
        rows = [r for r in rows if r["group"] in selected_groups]

    # Sorting
    valid_sort = {"title", "group", "s", "p0", "edge0", "volume_real", "bet_end_date", "created_on", "days_to_close"}
    if sort_by not in valid_sort:
        sort_by = "created_on"
    rows = _sort_rows(rows, sort_by=sort_by, sort_dir=sort_dir)

    return rows, group_counts, q, min_vol_str, max_days_str, sort_by, sort_dir, selected_groups, show_ids


# ---------- markets page ----------

@app.route("/")
def index() -> str:
    bankroll, bankroll_source, wallet_balance = _compute_bankroll()
    bankroll_input = request.args.get("bankroll") or f"{bankroll:.2f}"

    rows, group_counts, q, min_vol_str, max_days_str, sort_by, sort_dir, selected_groups, show_ids = (
        _load_markets_rows_for_request(request.args)
    )

    def col_sort_url(column: str) -> str:
        params = dict(request.args)
        current = params.get("sort_by", "created_on")
        dir_now = params.get("sort_dir", "desc")
        params["sort_by"] = column
        params["sort_dir"] = ("asc" if dir_now == "desc" else "desc") if current == column else "desc"
        return url_for("index", **params)

    def preset_url(**overrides) -> str:
        params = dict(request.args)
        params.update({k: v for k, v in overrides.items() if v is not None})
        return url_for("index", **params)

    clear_url = url_for("index")  # no args

    template = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Futuur Scanner – Markets</title>
  <style>
    body { font-family: system-ui, sans-serif; background:#020617; color:#e5e7eb; padding:16px; }
    a { color:#38bdf8; text-decoration:none; margin-right:10px; }
    table { width:100%; border-collapse:collapse; margin-top:8px; font-size:14px; }
    th, td { padding:4px 6px; border-bottom:1px solid #1f2937; text-align:left; }
    th { background:#020617; position:sticky; top:0; }
    input, select { background:#020617; color:#e5e7eb; border:1px solid #4b5563; padding:2px 4px; }
    button { padding:4px 8px; background:#0ea5e9; color:#020617; border:none; cursor:pointer; }
    button:hover { background:#38bdf8; }
    .section { margin-top:12px; }
    .pill { display:inline-block; padding:2px 8px; border-radius:999px; border:1px solid #4b5563; margin-right:6px; cursor:pointer; user-select:none; }
    .pill-selected { background:#111827; border-color:#0ea5e9; }
    .mini { font-size:12px; color:#94a3b8; }
    .right { text-align:right; font-variant-numeric: tabular-nums; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
    .idcol { display: none; }
    .idcol.show { display: table-cell; }
  </style>
</head>
<body>
  <nav>
    <a href="{{ url_for('index') }}">Markets</a>
    <a href="{{ url_for('portfolio') }}">Portfolio</a>
    <a href="{{ url_for('export_markets_csv') }}">Export Markets CSV</a>
  </nav>

  <form method="get" class="section" id="filtersForm">
    <div>
      <label>Bankroll (USD):
        <input type="number" step="0.01" name="bankroll" value="{{ bankroll_input }}">
      </label>
      <span class="mini">Source: {{ bankroll_source }}{% if wallet_balance is not none %} (wallet ~ {{ '%.2f' % wallet_balance }}){% endif %}</span>
      <button type="submit">Apply</button>
      <a href="{{ clear_url }}">Clear filters</a>
    </div>

    <div class="section">
      <strong>Visible rows: {{ rows|length }}</strong>
      <span class="mini"> | Sort: {{ sort_by }} {{ sort_dir }}</span>
    </div>

    <div class="section">
      <label>Search:
        <input type="text" name="q" value="{{ q }}">
      </label>
      <label style="margin-left:8px;">Min real vol:
        <input type="number" step="1" name="min_vol" value="{{ min_vol_str }}">
      </label>
      <label style="margin-left:8px;">Max days to close:
        <input type="number" step="1" name="max_days" value="{{ max_days_str }}">
      </label>
      <button type="submit" style="margin-left:8px;">Apply</button>
    </div>

    <div class="section">
      <span class="mini">Presets:</span>
      <a href="{{ preset_url(max_days='7') }}">Closing ≤ 7d</a>
      <a href="{{ preset_url(max_days='30') }}">Closing ≤ 30d</a>
      <a href="{{ preset_url(min_vol='100') }}">Vol ≥ 100</a>
      <a href="{{ preset_url(min_vol='500') }}">Vol ≥ 500</a>
    </div>

    <div class="section">
      <span>Categories:</span>
      {% set is_all = (not selected_groups) %}
      <label class="pill {% if is_all %}pill-selected{% endif %}">
        <input type="checkbox" id="group_all" {% if is_all %}checked{% endif %} style="display:none;">
        All <span class="mini">({{ group_counts.get('Finance',0)+group_counts.get('Entertainment',0)+group_counts.get('Politics',0)+group_counts.get('Science',0)+group_counts.get('Sports',0)+group_counts.get('Other',0) }})</span>
      </label>

      {% for g in ["Finance","Entertainment","Politics","Science","Sports","Other"] %}
        <label class="pill {% if g in selected_groups %}pill-selected{% endif %}">
          <input type="checkbox" class="group_cb" name="group" value="{{ g }}" {% if g in selected_groups %}checked{% endif %} style="display:none;">
          {{ g }} <span class="mini">({{ group_counts.get(g,0) }})</span>
        </label>
      {% endfor %}
    </div>

    <div class="section">
      <label>Sort by:
        <select name="sort_by">
          <option value="created_on" {% if sort_by == "created_on" %}selected{% endif %}>Created</option>
          <option value="bet_end_date" {% if sort_by == "bet_end_date" %}selected{% endif %}>Bet end</option>
          <option value="s" {% if sort_by == "s" %}selected{% endif %}>Price</option>
          <option value="p0" {% if sort_by == "p0" %}selected{% endif %}>p₀</option>
          <option value="edge0" {% if sort_by == "edge0" %}selected{% endif %}>Edge₀</option>
          <option value="volume_real" {% if sort_by == "volume_real" %}selected{% endif %}>Real vol</option>
          <option value="days_to_close" {% if sort_by == "days_to_close" %}selected{% endif %}>Days to close</option>
          <option value="title" {% if sort_by == "title" %}selected{% endif %}>Title</option>
          <option value="group" {% if sort_by == "group" %}selected{% endif %}>Group</option>
        </select>
      </label>
      <label style="margin-left:8px;">Direction:
        <select name="sort_dir">
          <option value="desc" {% if sort_dir == "desc" %}selected{% endif %}>Desc</option>
          <option value="asc" {% if sort_dir == "asc" %}selected{% endif %}>Asc</option>
        </select>
      </label>

      <label style="margin-left:16px;">
        <input type="checkbox" id="toggleIds" {% if show_ids %}checked{% endif %}>
        Show IDs
      </label>
      <input type="hidden" name="show_ids" id="show_ids" value="{{ '1' if show_ids else '0' }}">
    </div>
  </form>

  <div class="section">
    <form method="get" action="{{ url_for('export_markets_csv') }}" id="exportForm">
      {# preserve filters EXCEPT sel; preserve multi group explicitly #}
      {% for name, value in request.args.items() %}
        {% if name != "sel" and name != "group" %}
          <input type="hidden" name="{{ name }}" value="{{ value }}">
        {% endif %}
      {% endfor %}
      {% for g in selected_groups %}
        <input type="hidden" name="group" value="{{ g }}">
      {% endfor %}

      <table>
        <thead>
          <tr>
            <th><input type="checkbox" id="selectAll" title="Select all below"></th>
            <th class="idcol {% if show_ids %}show{% endif %}">Market ID</th>
            <th class="idcol {% if show_ids %}show{% endif %}">Outcome ID</th>
            <th><a href="{{ col_sort_url('group') }}">Group</a></th>
            <th>Market</th>
            <th>Outcome</th>
            <th>Tags</th>
            <th class="right"><a href="{{ col_sort_url('s') }}">Price</a></th>
            <th class="right"><a href="{{ col_sort_url('p0') }}">p₀</a></th>
            <th class="right"><a href="{{ col_sort_url('edge0') }}">Edge₀</a></th>
            <th class="right"><a href="{{ col_sort_url('volume_real') }}">Vol</a></th>
            <th><a href="{{ col_sort_url('bet_end_date') }}">Closes</a></th>
            <th><a href="{{ col_sort_url('days_to_close') }}">Δt</a></th>
            <th><a href="{{ col_sort_url('created_on') }}">Created</a></th>
          </tr>
        </thead>
        <tbody>
          {% for r in rows %}
            <tr>
              <td><input type="checkbox" class="rowSel" name="sel" value="{{ r.question_id }}:{{ r.outcome_id }}"></td>
              <td class="idcol {% if show_ids %}show{% endif %}">{{ r.question_id }}</td>
              <td class="idcol {% if show_ids %}show{% endif %}">{{ r.outcome_id }}</td>
              <td>{{ r.group }}</td>
              <td><a href="{{ r.url }}" target="_blank" rel="noreferrer">{{ r.title }}</a></td>
              <td>{{ r.outcome_title }}</td>
              <td>{% for t in r.tags %}<span>{{ t }}</span>{% endfor %}</td>
              <td class="right">{{ "%.3f"|format(r.s) }}</td>
              <td class="right">{{ "%.3f"|format(r.p0) }}</td>
              <td class="right" style="{% if r.edge0 > 0 %}color:#22c55e{% elif r.edge0 < 0 %}color:#f97316{% endif %}">
                {{ "%.3f"|format(r.edge0) }}
              </td>
              <td class="right">{{ "%.2f"|format(r.volume_real) }}</td>
              <td>{{ r.bet_end_str }}</td>
              <td>{{ r.days_to_close_str }}</td>
              <td>{{ r.created_str }}</td>
            </tr>
          {% endfor %}
        </tbody>
      </table>

      <button type="submit" style="margin-top:8px;">Export selected (or all visible if none selected)</button>
    </form>
  </div>

  <script>
    // Select-all rows
    const selectAll = document.getElementById("selectAll");
    const rowBoxes = () => Array.from(document.querySelectorAll(".rowSel"));

    function syncSelectAll() {
      const boxes = rowBoxes();
      if (boxes.length === 0) return;
      const checked = boxes.filter(b => b.checked).length;
      selectAll.indeterminate = checked > 0 && checked < boxes.length;
      selectAll.checked = checked === boxes.length;
    }

    selectAll.addEventListener("change", () => {
      rowBoxes().forEach(b => b.checked = selectAll.checked);
      syncSelectAll();
    });

    rowBoxes().forEach(b => b.addEventListener("change", syncSelectAll));
    syncSelectAll();

    // Category pills: "All" clears others; selecting any group unchecks "All"
    const allCb = document.getElementById("group_all");
    const groupCbs = Array.from(document.querySelectorAll(".group_cb"));

    allCb.addEventListener("change", () => {
      if (allCb.checked) groupCbs.forEach(cb => cb.checked = false);
      document.getElementById("filtersForm").submit();
    });

    groupCbs.forEach(cb => cb.addEventListener("change", () => {
      if (cb.checked) allCb.checked = false;
      document.getElementById("filtersForm").submit();
    }));

    // Show IDs toggle (persist via query arg show_ids=1)
    const toggleIds = document.getElementById("toggleIds");
    const showIdsHidden = document.getElementById("show_ids");
    toggleIds.addEventListener("change", () => {
      showIdsHidden.value = toggleIds.checked ? "1" : "0";
      document.getElementById("filtersForm").submit();
    });
  </script>
</body>
</html>
"""
    return render_template_string(
        template,
        rows=rows,
        group_counts=group_counts,
        q=q,
        min_vol_str=min_vol_str,
        max_days_str=max_days_str,
        sort_by=sort_by,
        sort_dir=sort_dir,
        selected_groups=selected_groups,
        show_ids=show_ids,
        bankroll=bankroll,
        bankroll_input=bankroll_input,
        bankroll_source=bankroll_source,
        wallet_balance=wallet_balance,
        col_sort_url=col_sort_url,
        preset_url=preset_url,
        clear_url=clear_url,
    )


# ---------- markets CSV export ----------

@app.route("/markets/export")
def export_markets_csv() -> Response:
    rows, *_ = _load_markets_rows_for_request(request.args)

    selected_ids = request.args.getlist("sel")
    if selected_ids:
        sel_set = set(selected_ids)
        rows = [r for r in rows if f"{r['question_id']}:{r['outcome_id']}" in sel_set]

    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(
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
        w.writerow(
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
    return Response(csv_data, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=futuur_markets.csv"})


# ---------- portfolio (kept from your existing build) ----------

def _sort_bets(bets, sort_key: str, sort_dir: str):
    reverse = sort_dir == "desc"

    def key_fn(b):
        if sort_key == "title":
            return (b.question_title or "").lower()
        if sort_key == "created":
            return b.created or datetime.min.replace(tzinfo=timezone.utc)
        if sort_key == "amount":
            return b.amount_invested
        if sort_key == "value":
            return b.mark_value
        if sort_key == "unrealized":
            return b.unrealized_pnl
        if sort_key == "pct":
            return getattr(b, "pct_bankroll", 0.0)
        if sort_key == "closed":
            return b.closed or datetime.min.replace(tzinfo=timezone.utc)
        return b.bet_id

    try:
        return sorted(bets, key=key_fn, reverse=reverse)
    except TypeError:
        return bets


def _sort_orders(orders, sort_key: str, sort_dir: str):
    reverse = sort_dir == "desc"

    def key_fn(o):
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
            return o.created or datetime.min.replace(tzinfo=timezone.utc)
        if sort_key == "expires":
            return o.expired_at or (datetime.max.replace(tzinfo=timezone.utc) - timedelta(days=365))
        return o.order_id

    try:
        return sorted(orders, key=key_fn, reverse=reverse)
    except TypeError:
        return orders


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

    mv_port = 0.0
    ev_port = 0.0

    for b in open_bets:
        b.pct_bankroll = (b.mark_value / bankroll) * 100.0 if bankroll > 0 and b.mark_value else 0.0
        mkt_p = max(min(b.mark_price, 1.0), 0.0)
        p_str = request.args.get(f"p_{b.bet_id}")
        if not p_str:
            user_p = mkt_p
        else:
            try:
                user_p = float(p_str)
            except ValueError:
                user_p = mkt_p
        user_p = 0.0 if user_p < 0 else (1.0 if user_p > 1 else user_p)

        b.mkt_p = mkt_p
        b.user_p = user_p
        b.ev_value = b.shares * user_p
        b.ue_pnl = b.ev_value - b.amount_invested

        mv_port += b.mark_value
        ev_port += b.ev_value

    total_unrealized = sum(b.unrealized_pnl for b in open_bets)
    reserved_notional = sum(o.reserved_notional for o in open_orders)
    total_exposure = mv_port + reserved_notional

    cash_balance = wallet_balance or 0.0
    mv_total = mv_port + cash_balance
    ev_total = ev_port + cash_balance

    open_bets_sorted = _sort_bets(open_bets, sort_key=sort_open, sort_dir=dir_open)
    closed_bets_sorted = _sort_bets(closed_bets, sort_key=sort_closed, sort_dir=dir_closed)
    open_orders_sorted = _sort_orders(open_orders, sort_key=sort_orders, sort_dir=dir_orders)

    def sort_url(table: str, column: str) -> str:
        params = dict(request.args)
        key = f"sort_{table}"
        dir_key = f"dir_{table}"
        current = params.get(key, "created")
        dir_now = params.get(dir_key, "desc")
        params[key] = column
        params[dir_key] = ("asc" if dir_now == "desc" else "desc") if current == column else "desc"
        params["bankroll"] = bankroll_input
        return url_for("portfolio", **params)

    template = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Futuur Scanner – Portfolio</title>
  <style>
    body { font-family: system-ui, sans-serif; background:#020617; color:#e5e7eb; padding:16px; }
    a { color:#38bdf8; text-decoration:none; margin-right:8px; }
    table { width:100%; border-collapse:collapse; margin-top:8px; font-size:14px; }
    th, td { padding:4px 6px; border-bottom:1px solid #1f2937; text-align:left; }
    th { background:#020617; position:sticky; top:0; }
    input, select { background:#020617; color:#e5e7eb; border:1px solid #4b5563; padding:2px 4px; }
    input[type="number"] { width:5em; }
    button { padding:4px 8px; background:#0ea5e9; color:#020617; border:none; cursor:pointer; }
    button:hover { background:#38bdf8; }
    .section { margin-top:12px; }
    .section-title { margin-top:24px; font-size:16px; font-weight:bold; }
  </style>
</head>
<body>
  <nav>
    <a href="{{ url_for('index') }}">Markets</a>
    <a href="{{ url_for('portfolio') }}">Portfolio</a>
    <a href="{{ url_for('export_open_positions_csv') }}">Export Open Positions CSV</a>
    <a href="{{ url_for('export_closed_bets_csv') }}">Export Closed Bets CSV</a>
  </nav>

  <form method="get" class="section">
    <div>
      <label>Bankroll (USD):
        <input type="number" step="0.01" name="bankroll" value="{{ bankroll_input }}">
      </label>
      <span>Source: {{ bankroll_source }}{% if wallet_balance is not none %} (wallet ~ {{ '%.2f' % wallet_balance }}){% endif %}</span>
      <button type="submit">Apply</button>
    </div>

    <div class="section">
      <strong>Totals</strong> –
      Bankroll: {{ '%.2f' % bankroll }}
      | MVPort: {{ '%.2f' % mv_port }}
      | EVPort: {{ '%.2f' % ev_port }}
      | Cash: {{ '%.2f' % cash_balance }}
      | MVTotal: {{ '%.2f' % mv_total }}
      | EVTotal: {{ '%.2f' % ev_total }}
      | Reserved: {{ '%.2f' % reserved_notional }}
      | Exposure: {{ '%.2f' % total_exposure }}
      | UMPnL: {{ '%.2f' % total_unrealized }}
    </div>

    {% if open_err %}<div class="section" style="color:#f97316;">Open bets error: {{ open_err }}</div>{% endif %}
    {% if closed_err %}<div class="section" style="color:#f97316;">Closed bets error: {{ closed_err }}</div>{% endif %}
    {% if orders_err %}<div class="section" style="color:#f97316;">Limit orders error: {{ orders_err }}</div>{% endif %}

    <div class="section-title">Open positions ({{ open_bets_sorted|length }})</div>
    <table>
      <thead>
        <tr>
          <th><a href="{{ sort_url('open','title') }}">Market</a></th>
          <th>Outcome</th>
          <th><a href="{{ sort_url('open','amount') }}">Amount in</a></th>
          <th>Shares</th>
          <th>Avg price</th>
          <th>Mkt p(win)</th>
          <th>Your p(win)</th>
          <th><a href="{{ sort_url('open','value') }}">MV value</a></th>
          <th>EV value</th>
          <th>UEPnL</th>
          <th><a href="{{ sort_url('open','unrealized') }}">UMPnL</a></th>
          <th><a href="{{ sort_url('open','pct') }}">% bankroll</a></th>
          <th><a href="{{ sort_url('open','created') }}">Created</a></th>
          <th>Close date</th>
        </tr>
      </thead>
      <tbody>
        {% for b in open_bets_sorted %}
          <tr>
            <td>{{ b.question_title }}</td>
            <td>{{ b.outcome_title }}</td>
            <td>{{ '%.2f' % b.amount_invested }}</td>
            <td>{{ '%.2f' % b.shares }}</td>
            <td>{{ '%.2f' % b.avg_price }}</td>
            <td>{{ '%.3f' % b.mkt_p }}</td>
            <td><input type="number" step="0.001" min="0" max="1" name="p_{{ b.bet_id }}" value="{{ '%.3f' % b.user_p }}"></td>
            <td>{{ '%.2f' % b.mark_value }}</td>
            <td>{{ '%.2f' % b.ev_value }}</td>
            <td>{{ '%.2f' % b.ue_pnl }}</td>
            <td>{{ '%.2f' % b.unrealized_pnl }}</td>
            <td>{{ '%.2f' % b.pct_bankroll }}%</td>
            <td>{{ b.created_str }}</td>
            <td>{{ b.close_date_str }}</td>
          </tr>
        {% endfor %}
      </tbody>
    </table>

    <div class="section-title">Open limit orders ({{ open_orders_sorted|length }})</div>
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
          <th><a href="{{ sort_url('orders','reserved') }}">Reserved</a></th>
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
            <td>{{ '%.2f' % o.shares_requested }}</td>
            <td>{{ '%.2f' % o.shares_filled }}</td>
            <td>{{ '%.2f' % o.remaining_shares }}</td>
            <td>{{ '%.2f' % o.reserved_notional }}</td>
            <td>{{ o.status }}</td>
            <td>{{ o.created_str }}</td>
            <td>{{ o.expired_str }}</td>
          </tr>
        {% endfor %}
      </tbody>
    </table>

    <div class="section-title">Closed bets ({{ closed_bets_sorted|length }})</div>
    <table>
      <thead>
        <tr>
          <th><a href="{{ sort_url('closed','title') }}">Market</a></th>
          <th>Outcome</th>
          <th>Amount in (approx)</th>
          <th>Realized PnL (placeholder)</th>
          <th><a href="{{ sort_url('closed','closed') }}">Close date</a></th>
        </tr>
      </thead>
      <tbody>
        {% for b in closed_bets_sorted %}
          <tr>
            <td>{{ b.question_title }}</td>
            <td>{{ b.outcome_title }}</td>
            <td>{{ '%.2f' % b.amount_invested }}</td>
            <td>{{ '%.2f' % b.realized_pnl }}</td>
            <td>{{ b.closed_str }}</td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  </form>
</body>
</html>
"""
    return render_template_string(
        template,
        bankroll=bankroll,
        bankroll_input=bankroll_input,
        bankroll_source=bankroll_source,
        wallet_balance=wallet_balance,
        mv_port=mv_port,
        ev_port=ev_port,
        cash_balance=cash_balance,
        mv_total=mv_total,
        ev_total=ev_total,
        reserved_notional=reserved_notional,
        total_exposure=total_exposure,
        total_unrealized=total_unrealized,
        open_bets_sorted=open_bets_sorted,
        closed_bets_sorted=closed_bets_sorted,
        open_orders_sorted=open_orders_sorted,
        open_err=open_err,
        closed_err=closed_err,
        orders_err=orders_err,
        sort_url=sort_url,
    )


@app.route("/portfolio/export_open")
def export_open_positions_csv() -> Response:
    bankroll, _, _ = _compute_bankroll()
    open_bets, _ = list_open_real_bets(limit=500)

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(
        [
            "bet_id",
            "market",
            "outcome",
            "position",
            "amount_in",
            "shares",
            "avg_price",
            "mkt_p",
            "user_p",
            "mv_value",
            "ev_value",
            "uepnl",
            "umpnl",
            "pct_bankroll",
            "created",
            "close_date",
        ]
    )

    for b in open_bets:
        mkt_p = max(min(b.mark_price, 1.0), 0.0)
        user_p = mkt_p  # note: export reflects default unless you implement persistence for edited p(win)
        ev_value = b.shares * user_p
        uepnl = ev_value - b.amount_invested
        pct = (b.mark_value / bankroll) * 100.0 if bankroll > 0 and b.mark_value else 0.0

        w.writerow(
            [
                b.bet_id,
                b.question_title,
                b.outcome_title,
                b.position,
                f"{b.amount_invested:.2f}",
                f"{b.shares:.2f}",
                f"{b.avg_price:.2f}",
                f"{mkt_p:.3f}",
                f"{user_p:.3f}",
                f"{b.mark_value:.2f}",
                f"{ev_value:.2f}",
                f"{uepnl:.2f}",
                f"{b.unrealized_pnl:.2f}",
                f"{pct:.2f}",
                b.created.isoformat() if b.created else "",
                b.close_date.isoformat() if b.close_date else "",
            ]
        )

    csv_data = out.getvalue()
    out.close()
    return Response(csv_data, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=futuur_open_positions.csv"})


@app.route("/portfolio/export_closed")
def export_closed_bets_csv() -> Response:
    closed_bets, _ = list_closed_real_bets(limit=500)

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["bet_id", "market", "outcome", "position", "amount_in", "shares", "avg_price", "realized_pnl", "created", "closed"])
    for b in closed_bets:
        w.writerow(
            [
                b.bet_id,
                b.question_title,
                b.outcome_title,
                b.position,
                f"{b.amount_invested:.2f}",
                f"{b.shares:.2f}",
                f"{b.avg_price:.2f}",
                f"{b.realized_pnl:.2f}",
                b.created.isoformat() if b.created else "",
                b.closed.isoformat() if b.closed else "",
            ]
        )

    csv_data = out.getvalue()
    out.close()
    return Response(csv_data, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=futuur_closed_bets.csv"})


if __name__ == "__main__":
    app.run(host=APP_HOST, port=APP_PORT, debug=True)
