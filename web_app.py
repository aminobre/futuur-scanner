from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
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

# -------------------- small TTL cache --------------------

_CACHE: Dict[str, Tuple[float, Any]] = {}


def cached_call(key: str, ttl_s: int, fn):
    now = time.time()
    if key in _CACHE:
        t, v = _CACHE[key]
        if now - t < ttl_s:
            return v
    v = fn()
    _CACHE[key] = (now, v)
    return v


# -------------------- helpers --------------------

def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        v = value.strip()
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        return datetime.fromisoformat(v)
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
    secs = int((bet_end - now).total_seconds())
    sign = "" if secs >= 0 else "-"
    secs = abs(secs)
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins or not parts:
        parts.append(f"{mins}m")
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
    override_str = (request.args.get("bankroll") or "").strip()
    if override_str:
        try:
            v = float(override_str)
            if v > 0:
                return v, "manual", None
        except ValueError:
            pass

    wallet = cached_call("wallet", 30, fetch_wallet_balance)
    if wallet is not None and wallet > 0:
        return wallet, "wallet", wallet

    return float(BANKROLL_USD), "default", None


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def signed_shares(position: str, shares: float) -> float:
    # position expected: "l" (long) or "s" (short)
    return shares if (position or "").lower().startswith("l") else -shares


def kelly_fraction(p: float, mkt_p: float, cap: float = 0.25) -> float:
    """
    Simple binary contract kelly approximation using market probability as price.
    If price is mkt_p, payoff for $1 is:
      - if YES: (1/price - 1)
      - if NO: (1/(1-price) - 1)
    But here we’re sizing on outcome contract value; practical heuristic:
      edge = p - mkt_p, scale by variance.
    This is not perfect market-microstructure kelly; it’s stable and actionable.
    """
    edge = p - mkt_p
    var = max(mkt_p * (1 - mkt_p), 1e-6)
    f = edge / var
    # keep it sane
    if f > cap:
        return cap
    if f < -cap:
        return -cap
    return f


# -------------------- markets rows (shared by export) --------------------

def _load_markets_rows(args) -> Tuple[List[Dict[str, Any]], Dict[str, int], Dict[str, Any]]:
    q = (args.get("q") or "").strip()
    selected_groups = [g for g in args.getlist("group") if g and g != "All"]
    min_vol_str = (args.get("min_vol") or "").strip()
    max_days_str = (args.get("max_days") or "").strip()
    sort_by = args.get("sort_by") or "created_on"
    sort_dir = args.get("sort_dir") or "desc"
    show_ids = (args.get("show_ids") or "") == "1"

    params = {"limit": 200, "offset": 0, "ordering": "-created_on", "currency_mode": "real_money"}
    data = cached_call(f"markets:{json.dumps(params, sort_keys=True)}", 60, lambda: call_api("markets/", params=params, auth=True))

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
        created_on = _parse_dt(raw.get("created_on")) or now
        volume_real = float(raw.get("volume_real_money") or 0.0)

        for outcome in outcomes:
            price_val = outcome.get("price")
            try:
                s = float(price_val)
            except Exception:
                if isinstance(price_val, dict) and price_val:
                    s = float(next(iter(price_val.values())))
                else:
                    s = 0.0

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
                    "edge0": base_p - s,
                    "bet_end_date": bet_end,
                    "bet_end_str": bet_end.strftime("%b %d, %y %H:%M") if bet_end else "-",
                    "created_on": created_on,
                    "created_str": created_on.strftime("%b %d, %y %H:%M"),
                    "volume_real": volume_real,
                    "days_to_close": _days_to_close(bet_end),
                    "days_to_close_str": _human_delta(bet_end),
                    "url": f"https://www.futuur.com/markets/{raw.get('slug')}",
                }
            )

    # q/min/max first (counts should respect these)
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in r["title"].lower() or ql in r["outcome_title"].lower() or any(ql in t.lower() for t in r["tags"])]

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

    # counts pre group filter
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["group"]] = counts.get(r["group"], 0) + 1

    if selected_groups:
        rows = [r for r in rows if r["group"] in selected_groups]

    valid_sort = {"created_on", "bet_end_date", "s", "p0", "edge0", "volume_real", "days_to_close", "title", "group"}
    if sort_by not in valid_sort:
        sort_by = "created_on"
    reverse = sort_dir != "asc"

    def key_fn(r):
        v = r.get(sort_by)
        return v.lower() if isinstance(v, str) else (v if v is not None else 0)

    rows = sorted(rows, key=key_fn, reverse=reverse)

    state = {
        "q": q,
        "min_vol_str": min_vol_str,
        "max_days_str": max_days_str,
        "sort_by": sort_by,
        "sort_dir": "asc" if not reverse else "desc",
        "selected_groups": selected_groups,
        "show_ids": show_ids,
    }
    return rows, counts, state


# -------------------- routes --------------------

@app.route("/health")
def health():
    return {"status": "ok"}, 200


@app.route("/")
def markets() -> str:
    bankroll, bankroll_source, wallet_balance = _compute_bankroll()
    bankroll_input = request.args.get("bankroll") or f"{bankroll:.2f}"

    rows, counts, st = _load_markets_rows(request.args)

    def preset_url(**overrides) -> str:
        params = dict(request.args)
        params.update({k: v for k, v in overrides.items() if v is not None})
        return url_for("markets", **params)

    clear_url = url_for("markets")

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
  th, td { padding:4px 6px; border-bottom:1px solid #1f2937; text-align:left; vertical-align:top; }
  th { background:#020617; position:sticky; top:0; }
  input, select { background:#020617; color:#e5e7eb; border:1px solid #4b5563; padding:2px 4px; }
  button { padding:4px 8px; background:#0ea5e9; color:#020617; border:none; cursor:pointer; }
  button:hover { background:#38bdf8; }
  .section { margin-top:12px; }
  .pill { display:inline-block; padding:2px 8px; border-radius:999px; border:1px solid #4b5563; margin-right:6px; cursor:pointer; user-select:none; }
  .pill-selected { background:#111827; border-color:#0ea5e9; }
  .mini { font-size:12px; color:#94a3b8; }
  .right { text-align:right; font-variant-numeric: tabular-nums; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
  .idcol { display:none; }
  .idcol.show { display:table-cell; }
</style>
</head>
<body>
<nav>
  <a href="{{ url_for('markets') }}">Markets</a>
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
    <span class="mini"> | Sort: {{ st.sort_by }} {{ st.sort_dir }}</span>
  </div>

  <div class="section">
    <label>Search:
      <input type="text" name="q" value="{{ st.q }}">
    </label>
    <label style="margin-left:8px;">Min vol:
      <input type="number" step="1" name="min_vol" value="{{ st.min_vol_str }}">
    </label>
    <label style="margin-left:8px;">Max days:
      <input type="number" step="1" name="max_days" value="{{ st.max_days_str }}">
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
    {% set is_all = (not st.selected_groups) %}
    <label class="pill {% if is_all %}pill-selected{% endif %}">
      <input type="checkbox" id="group_all" {% if is_all %}checked{% endif %} style="display:none;">
      All <span class="mini">({{ rows|length if is_all else (counts.get('Finance',0)+counts.get('Entertainment',0)+counts.get('Politics',0)+counts.get('Science',0)+counts.get('Sports',0)+counts.get('Other',0)) }})</span>
    </label>
    {% for g in ["Finance","Entertainment","Politics","Science","Sports","Other"] %}
      <label class="pill {% if g in st.selected_groups %}pill-selected{% endif %}">
        <input type="checkbox" class="group_cb" name="group" value="{{ g }}" {% if g in st.selected_groups %}checked{% endif %} style="display:none;">
        {{ g }} <span class="mini">({{ counts.get(g,0) }})</span>
      </label>
    {% endfor %}
  </div>

  <div class="section">
    <label>Sort by:
      <select name="sort_by">
        {% for opt,val in [("Created","created_on"),("Closes","bet_end_date"),("Price","s"),("p0","p0"),("Edge0","edge0"),("Vol","volume_real"),("Days","days_to_close"),("Title","title"),("Group","group")] %}
          <option value="{{ val }}" {% if st.sort_by == val %}selected{% endif %}>{{ opt }}</option>
        {% endfor %}
      </select>
    </label>
    <label style="margin-left:8px;">Dir:
      <select name="sort_dir">
        <option value="desc" {% if st.sort_dir == "desc" %}selected{% endif %}>Desc</option>
        <option value="asc" {% if st.sort_dir == "asc" %}selected{% endif %}>Asc</option>
      </select>
    </label>

    <label style="margin-left:16px;">
      <input type="checkbox" id="toggleIds" {% if st.show_ids %}checked{% endif %}>
      Show IDs
    </label>
    <input type="hidden" name="show_ids" id="show_ids" value="{{ '1' if st.show_ids else '0' }}">
  </div>
</form>

<div class="section">
  <form method="get" action="{{ url_for('export_markets_csv') }}" id="exportForm">
    {% for name, value in request.args.items() %}
      {% if name != "sel" and name != "group" and name != "pmap" %}
        <input type="hidden" name="{{ name }}" value="{{ value }}">
      {% endif %}
    {% endfor %}
    {% for g in st.selected_groups %}
      <input type="hidden" name="group" value="{{ g }}">
    {% endfor %}
    <input type="hidden" name="pmap" id="pmap_hidden" value="">

    <table>
      <thead>
        <tr>
          <th><input type="checkbox" id="selectAll" title="Select all below"></th>
          <th class="idcol {% if st.show_ids %}show{% endif %}">Market ID</th>
          <th class="idcol {% if st.show_ids %}show{% endif %}">Outcome ID</th>
          <th>Group</th>
          <th>Market</th>
          <th>Outcome</th>
          <th>Tags</th>
          <th class="right">Price</th>
          <th class="right">p0</th>
          <th class="right">Edge0</th>
          <th class="right">Vol</th>
          <th>Closes</th>
          <th>Δt</th>
          <th>Created</th>
          <th>Copy</th>
        </tr>
      </thead>
      <tbody>
      {% for r in rows %}
        <tr>
          <td><input type="checkbox" class="rowSel" name="sel" value="{{ r.question_id }}:{{ r.outcome_id }}"></td>
          <td class="idcol {% if st.show_ids %}show{% endif %}">{{ r.question_id }}</td>
          <td class="idcol {% if st.show_ids %}show{% endif %}">{{ r.outcome_id }}</td>
          <td>{{ r.group }}</td>
          <td><a href="{{ r.url }}" target="_blank" rel="noreferrer">{{ r.title }}</a></td>
          <td>{{ r.outcome_title }}</td>
          <td>{% for t in r.tags %}<span>{{ t }}</span>{% endfor %}</td>
          <td class="right">{{ "%.3f"|format(r.s) }}</td>
          <td class="right">{{ "%.3f"|format(r.p0) }}</td>
          <td class="right" style="{% if r.edge0 > 0 %}color:#22c55e{% elif r.edge0 < 0 %}color:#f97316{% endif %}">{{ "%.3f"|format(r.edge0) }}</td>
          <td class="right">{{ "%.2f"|format(r.volume_real) }}</td>
          <td>{{ r.bet_end_str }}</td>
          <td>{{ r.days_to_close_str }}</td>
          <td>{{ r.created_str }}</td>
          <td>
            <button type="button" class="copyUrl" data-url="{{ r.url }}">URL</button>
            <button type="button" class="copyJson"
              data-json='{{ {"question_id": r.question_id, "outcome_id": r.outcome_id, "price": r.s}|tojson }}'>JSON</button>
          </td>
        </tr>
      {% endfor %}
      </tbody>
    </table>

    <button type="submit" style="margin-top:8px;" id="exportBtn">Export selected (or all visible)</button>
  </form>
</div>

<script>
  // select-all + indeterminate
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

  // category pills behavior
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

  // show IDs toggle
  const toggleIds = document.getElementById("toggleIds");
  const showIdsHidden = document.getElementById("show_ids");
  toggleIds.addEventListener("change", () => {
    showIdsHidden.value = toggleIds.checked ? "1" : "0";
    document.getElementById("filtersForm").submit();
  });

  // copy buttons
  document.querySelectorAll(".copyUrl").forEach(btn => {
    btn.addEventListener("click", async () => {
      await navigator.clipboard.writeText(btn.dataset.url || "");
    });
  });
  document.querySelectorAll(".copyJson").forEach(btn => {
    btn.addEventListener("click", async () => {
      await navigator.clipboard.writeText(btn.dataset.json || "");
    });
  });

  // pmap support for export (reserved for future: if you add per-row p editing on Markets)
  // Keep empty for now, but the pipeline exists.
  document.getElementById("exportForm").addEventListener("submit", () => {
    document.getElementById("pmap_hidden").value = localStorage.getItem("pmap") || "{}";
  });
</script>

</body>
</html>
"""
    return render_template_string(
        template,
        rows=rows,
        counts=counts,
        st=st,
        bankroll_input=bankroll_input,
        bankroll_source=bankroll_source,
        wallet_balance=wallet_balance,
        preset_url=preset_url,
        clear_url=clear_url,
        request=request,
    )


@app.route("/markets/export")
def export_markets_csv() -> Response:
    rows, _, _ = _load_markets_rows(request.args)

    selected_ids = request.args.getlist("sel")
    if selected_ids:
        sel = set(selected_ids)
        rows = [r for r in rows if f"{r['question_id']}:{r['outcome_id']}" in sel]

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["question_id", "outcome_id", "title", "outcome_title", "group", "category_title", "tags", "s", "p0", "edge0", "volume_real", "bet_end", "days_to_close", "url"])
    for r in rows:
        w.writerow([
            r["question_id"], r["outcome_id"], r["title"], r["outcome_title"], r["group"],
            r["category_title"], ";".join(r["tags"]), f"{r['s']:.4f}", f"{r['p0']:.4f}", f"{r['edge0']:.4f}",
            f"{r['volume_real']:.2f}", r["bet_end_str"], f"{r['days_to_close']:.2f}" if r["days_to_close"] is not None else "", r["url"]
        ])

    data = out.getvalue()
    out.close()
    return Response(data, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=futuur_markets.csv"})


@app.route("/portfolio")
def portfolio() -> str:
    bankroll, bankroll_source, wallet_balance = _compute_bankroll()
    bankroll_input = request.args.get("bankroll") or f"{bankroll:.2f}"

    open_bets, open_err = cached_call("open_bets", 20, lambda: list_open_real_bets(limit=500))
    closed_bets, closed_err = cached_call("closed_bets", 60, lambda: list_closed_real_bets(limit=500))
    open_orders, orders_err = cached_call("open_orders", 30, lambda: list_open_limit_orders(limit=500))

    # Read pmap injected by JS (for export correctness + stress test)
    # Format: {"<bet_id>": 0.63, ...}
    pmap_raw = request.args.get("pmap") or "{}"
    try:
        pmap = json.loads(pmap_raw)
        if not isinstance(pmap, dict):
            pmap = {}
    except Exception:
        pmap = {}

    # Portfolio totals + summaries
    mv_port = 0.0
    ev_port = 0.0
    edge_value_total = 0.0

    exposure_by_group: Dict[str, float] = {}

    # Expected fields from bet rows: bet_id, question_title, outcome_title, position, shares, avg_price, mark_price, mark_value, amount_invested, unrealized_pnl, close_date, created, group
    # If your portfolio_client doesn't provide group, we approximate group="Other".
    for b in open_bets:
        mkt_p = clamp01(float(getattr(b, "mark_price", 0.0) or 0.0))
        user_p = pmap.get(str(getattr(b, "bet_id", "")))
        try:
            user_p = clamp01(float(user_p)) if user_p is not None else mkt_p
        except Exception:
            user_p = mkt_p

        pos = (getattr(b, "position", "") or "l").lower()
        sh = float(getattr(b, "shares", 0.0) or 0.0)
        s_sh = signed_shares(pos, sh)

        mv = float(getattr(b, "mark_value", 0.0) or 0.0)  # already signed? assume absolute and recompute if missing
        if mv == 0.0 and sh != 0.0:
            mv = s_sh * mkt_p

        ev = s_sh * user_p
        edge_value = s_sh * (user_p - mkt_p)

        setattr(b, "mkt_p", mkt_p)
        setattr(b, "user_p", user_p)
        setattr(b, "ev_value", ev)
        setattr(b, "edge_value", edge_value)

        # Kelly + sizing
        kf = kelly_fraction(user_p, mkt_p, cap=0.25)
        setattr(b, "kelly", kf)
        setattr(b, "kelly_dollars", kf * bankroll)

        mv_port += mv
        ev_port += ev
        edge_value_total += edge_value

        grp = getattr(b, "group", None) or "Other"
        exposure_by_group[grp] = exposure_by_group.get(grp, 0.0) + mv

    cash = float(wallet_balance or 0.0)
    mv_total = mv_port + cash
    ev_total = ev_port + cash

    # Stress test: "top 3 groups wrong" => flip user_p to (1-user_p) for those groups
    top_groups = sorted(exposure_by_group.items(), key=lambda kv: abs(kv[1]), reverse=True)[:3]
    stress_groups = [g for g, _ in top_groups]

    stress_swing = 0.0
    for b in open_bets:
        grp = getattr(b, "group", None) or "Other"
        if grp not in stress_groups:
            continue
        mkt_p = getattr(b, "mkt_p", 0.0)
        user_p = getattr(b, "user_p", mkt_p)
        pos = (getattr(b, "position", "") or "l").lower()
        sh = float(getattr(b, "shares", 0.0) or 0.0)
        s_sh = signed_shares(pos, sh)

        ev_now = s_sh * user_p
        ev_wrong = s_sh * (1.0 - user_p)
        stress_swing += (ev_wrong - ev_now)

    template = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Futuur Scanner – Portfolio</title>
<style>
  body { font-family: system-ui, sans-serif; background:#020617; color:#e5e7eb; padding:16px; }
  a { color:#38bdf8; text-decoration:none; margin-right:10px; }
  table { width:100%; border-collapse:collapse; margin-top:8px; font-size:14px; }
  th, td { padding:4px 6px; border-bottom:1px solid #1f2937; text-align:left; vertical-align:top; }
  th { background:#020617; position:sticky; top:0; }
  input, select { background:#020617; color:#e5e7eb; border:1px solid #4b5563; padding:2px 4px; }
  input[type="number"] { width:6.5em; }
  button { padding:4px 8px; background:#0ea5e9; color:#020617; border:none; cursor:pointer; }
  button:hover { background:#38bdf8; }
  .section { margin-top:12px; }
  .mini { font-size:12px; color:#94a3b8; }
  .right { text-align:right; font-variant-numeric: tabular-nums; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
  .presetbar button { margin-right:8px; }
  .col-scanner .c-sizing, .col-scanner .c-audit { display:none; }
  .col-sizing .c-scanner, .col-sizing .c-audit { display:none; }
  .col-audit .c-scanner, .col-audit .c-sizing { display:none; }
</style>
</head>
<body>
<nav>
  <a href="{{ url_for('markets') }}">Markets</a>
  <a href="{{ url_for('portfolio') }}">Portfolio</a>
  <a href="{{ url_for('export_open_positions_csv') }}">Export Open Positions CSV</a>
  <a href="{{ url_for('export_closed_bets_csv') }}">Export Closed Bets CSV</a>
</nav>

<form method="get" class="section" id="portForm">
  <div>
    <label>Bankroll (USD):
      <input type="number" step="0.01" name="bankroll" value="{{ bankroll_input }}">
    </label>
    <span class="mini">Source: {{ bankroll_source }}{% if wallet_balance is not none %} (wallet ~ {{ '%.2f' % wallet_balance }}){% endif %}</span>
    <input type="hidden" name="pmap" id="pmap_field" value="{}">
    <button type="submit" id="applyBtn">Apply</button>
  </div>

  <div class="section">
    <strong>Totals</strong>
    <span class="mini">
      | MVPort: {{ '%.2f' % mv_port }}
      | EVPort: {{ '%.2f' % ev_port }}
      | Cash: {{ '%.2f' % cash }}
      | MVTotal: {{ '%.2f' % mv_total }}
      | EVTotal: {{ '%.2f' % ev_total }}
      | Edge$: {{ '%.2f' % edge_value_total }}
    </span>
  </div>

  <div class="section">
    <strong>Exposure by group</strong>
    <span class="mini">
      {% for g,v in exposure_by_group.items() %}
        | {{ g }}: {{ '%.2f' % v }}
      {% endfor %}
    </span>
  </div>

  <div class="section">
    <strong>Stress: top groups wrong</strong>
    <span class="mini">
      Groups: {{ stress_groups }}
      | EV swing: {{ '%.2f' % stress_swing }}
    </span>
  </div>

  {% if open_err %}<div class="section" style="color:#f97316;">Open bets error: {{ open_err }}</div>{% endif %}
  {% if closed_err %}<div class="section" style="color:#f97316;">Closed bets error: {{ closed_err }}</div>{% endif %}
  {% if orders_err %}<div class="section" style="color:#f97316;">Limit orders error: {{ orders_err }}</div>{% endif %}

  <div class="section presetbar">
    <span class="mini">Column preset:</span>
    <button type="button" data-mode="scanner">Scanner</button>
    <button type="button" data-mode="sizing">Sizing</button>
    <button type="button" data-mode="audit">Audit</button>
  </div>

  <div class="section">
    <div><strong>Open positions ({{ open_bets|length }})</strong></div>
    <table id="openTable" class="col-scanner">
      <thead>
        <tr>
          <th>Market</th>
          <th>Outcome</th>

          <th class="right c-scanner">Mkt p</th>
          <th class="right c-scanner">Your p</th>
          <th class="right c-scanner">Edge$</th>

          <th class="right c-sizing">Kelly</th>
          <th class="right c-sizing">$ Kelly</th>

          <th class="right">Shares</th>
          <th class="right">Avg</th>
          <th class="right">Amount In</th>
          <th class="right">MV</th>
          <th class="right">EV</th>

          <th class="right c-audit">Pos</th>
          <th class="right c-audit">Created</th>
          <th class="right c-audit">Close Date</th>

          <th>Copy</th>
        </tr>
      </thead>
      <tbody>
        {% for b in open_bets %}
          <tr data-betid="{{ b.bet_id }}">
            <td>{{ b.question_title }}</td>
            <td>{{ b.outcome_title }}</td>

            <td class="right c-scanner">{{ "%.3f"|format(b.mkt_p) }}</td>
            <td class="right c-scanner">
              <input class="pInput" type="number" step="0.001" min="0" max="1" value="{{ "%.3f"|format(b.user_p) }}">
            </td>
            <td class="right c-scanner" style="{% if b.edge_value > 0 %}color:#22c55e{% elif b.edge_value < 0 %}color:#f97316{% endif %}">
              {{ "%.2f"|format(b.edge_value) }}
            </td>

            <td class="right c-sizing">{{ "%.3f"|format(b.kelly) }}</td>
            <td class="right c-sizing">{{ "%.2f"|format(b.kelly_dollars) }}</td>

            <td class="right">{{ "%.2f"|format(b.shares) }}</td>
            <td class="right">{{ "%.2f"|format(b.avg_price) }}</td>
            <td class="right">{{ "%.2f"|format(b.amount_invested) }}</td>
            <td class="right">{{ "%.2f"|format(b.mark_value) }}</td>
            <td class="right">{{ "%.2f"|format(b.ev_value) }}</td>

            <td class="right c-audit">{{ b.position }}</td>
            <td class="right c-audit">{{ b.created_str }}</td>
            <td class="right c-audit">{{ b.close_date_str }}</td>

            <td>
              <button type="button" class="copyJson" data-json='{{ {"bet_id": b.bet_id, "market": b.question_title, "outcome": b.outcome_title, "mkt_p": b.mkt_p }|tojson }}'>JSON</button>
            </td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</form>

<script>
  // Column presets
  const table = document.getElementById("openTable");
  document.querySelectorAll(".presetbar button").forEach(btn => {
    btn.addEventListener("click", () => {
      const mode = btn.dataset.mode;
      table.classList.remove("col-scanner","col-sizing","col-audit");
      table.classList.add("col-" + mode);
    });
  });

  // Persist pmap in localStorage; inject into form on submit (so server recalculates totals/exports/stress)
  const STORAGE_KEY = "pmap";

  function loadPMap() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}"); }
    catch { return {}; }
  }
  function savePMap(pmap) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(pmap));
  }

  const pmap = loadPMap();

  // Hydrate inputs from localStorage
  document.querySelectorAll("tr[data-betid]").forEach(tr => {
    const betId = tr.dataset.betid;
    const inp = tr.querySelector(".pInput");
    if (!inp) return;
    if (pmap[betId] !== undefined) inp.value = pmap[betId];
    inp.addEventListener("change", () => {
      const v = parseFloat(inp.value);
      if (!isNaN(v)) {
        const clamped = Math.max(0, Math.min(1, v));
        pmap[betId] = clamped;
        savePMap(pmap);
      }
    });
  });

  // copy json
  document.querySelectorAll(".copyJson").forEach(btn => {
    btn.addEventListener("click", async () => {
      await navigator.clipboard.writeText(btn.dataset.json || "");
    });
  });

  // On submit: attach pmap to query so server recomputes totals/stress with persisted p
  document.getElementById("portForm").addEventListener("submit", () => {
    document.getElementById("pmap_field").value = localStorage.getItem(STORAGE_KEY) || "{}";
  });
</script>

</body>
</html>
"""
    return render_template_string(
        template,
        bankroll_input=bankroll_input,
        bankroll_source=bankroll_source,
        wallet_balance=wallet_balance,
        mv_port=mv_port,
        ev_port=ev_port,
        cash=cash,
        mv_total=mv_total,
        ev_total=ev_total,
        edge_value_total=edge_value_total,
        exposure_by_group=exposure_by_group,
        stress_groups=stress_groups,
        stress_swing=stress_swing,
        open_bets=open_bets,
        closed_bets=closed_bets,
        open_orders=open_orders,
        open_err=open_err,
        closed_err=closed_err,
        orders_err=orders_err,
    )


@app.route("/portfolio/export_open")
def export_open_positions_csv() -> Response:
    bankroll, _, _ = _compute_bankroll()
    open_bets, _ = list_open_real_bets(limit=500)

    pmap_raw = request.args.get("pmap") or "{}"
    try:
        pmap = json.loads(pmap_raw)
        if not isinstance(pmap, dict):
            pmap = {}
    except Exception:
        pmap = {}

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([
        "bet_id","market","outcome","position","amount_in","shares","avg_price",
        "mkt_p","user_p","edge_value","mv_value","ev_value","kelly","kelly_$","created","close_date"
    ])

    for b in open_bets:
        mkt_p = clamp01(float(getattr(b, "mark_price", 0.0) or 0.0))
        user_p = pmap.get(str(getattr(b, "bet_id", "")))
        try:
            user_p = clamp01(float(user_p)) if user_p is not None else mkt_p
        except Exception:
            user_p = mkt_p

        pos = (getattr(b, "position", "") or "l").lower()
        sh = float(getattr(b, "shares", 0.0) or 0.0)
        s_sh = signed_shares(pos, sh)

        mv = float(getattr(b, "mark_value", 0.0) or 0.0)
        if mv == 0.0 and sh != 0.0:
            mv = s_sh * mkt_p

        ev = s_sh * user_p
        edge_value = s_sh * (user_p - mkt_p)

        kf = kelly_fraction(user_p, mkt_p, cap=0.25)
        kd = kf * bankroll

        created = getattr(b, "created", None)
        close_date = getattr(b, "close_date", None)

        w.writerow([
            getattr(b, "bet_id", ""),
            getattr(b, "question_title", ""),
            getattr(b, "outcome_title", ""),
            getattr(b, "position", ""),
            f"{float(getattr(b,'amount_invested',0.0) or 0.0):.2f}",
            f"{sh:.2f}",
            f"{float(getattr(b,'avg_price',0.0) or 0.0):.2f}",
            f"{mkt_p:.3f}",
            f"{user_p:.3f}",
            f"{edge_value:.2f}",
            f"{mv:.2f}",
            f"{ev:.2f}",
            f"{kf:.3f}",
            f"{kd:.2f}",
            created.isoformat() if created else "",
            close_date.isoformat() if close_date else "",
        ])

    data = out.getvalue()
    out.close()
    return Response(data, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=futuur_open_positions.csv"})


@app.route("/portfolio/export_closed")
def export_closed_bets_csv() -> Response:
    closed_bets, _ = list_closed_real_bets(limit=500)

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["bet_id","market","outcome","position","amount_in","shares","avg_price","realized_pnl","created","closed"])
    for b in closed_bets:
        created = getattr(b, "created", None)
        closed = getattr(b, "closed", None)
        w.writerow([
            getattr(b, "bet_id", ""),
            getattr(b, "question_title", ""),
            getattr(b, "outcome_title", ""),
            getattr(b, "position", ""),
            f"{float(getattr(b,'amount_invested',0.0) or 0.0):.2f}",
            f"{float(getattr(b,'shares',0.0) or 0.0):.2f}",
            f"{float(getattr(b,'avg_price',0.0) or 0.0):.2f}",
            f"{float(getattr(b,'realized_pnl',0.0) or 0.0):.2f}",
            created.isoformat() if created else "",
            closed.isoformat() if closed else "",
        ])

    data = out.getvalue()
    out.close()
    return Response(data, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=futuur_closed_bets.csv"})


if __name__ == "__main__":
    app.run(host=APP_HOST, port=APP_PORT, debug=True)
