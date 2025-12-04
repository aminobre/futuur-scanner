from datetime import datetime
from flask import Flask, render_template_string, request, Response
from urllib.parse import urlencode
import csv
import io
import math

from futuur_client import get_markets
from strategy import build_recommendations
from config import BANKROLL_USD, RISK_MODE

app = Flask(__name__)

HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Futuur Scanner</title>
  <style>
    body { font-family: sans-serif; padding: 10px; }
    h2 { margin-top: 0; }
    table { border-collapse: collapse; width: 100%; font-size: 12px; }
    th, td { border: 1px solid #ccc; padding: 4px; vertical-align: top; }
    th { background: #eee; }
    .controls { margin-bottom: 6px; }
    .controls form { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    .controls label { font-size: 12px; }
    .controls select, .controls input { font-size: 12px; }
    button { font-size: 12px; padding: 2px 8px; }
    .pagination { margin-bottom: 8px; font-size: 12px; }
    .pagination a { margin: 0 4px; }
  </style>
</head>
<body>
  <h2>Futuur Scanner</h2>
  <p>Bankroll: ${{ bankroll }} | Risk mode: {{ risk_mode }}</p>

  <div class="controls">
    <form method="get">
      <label>
        Domain:
        <select name="domain">
          <option value="">All</option>
          {% for d in domains %}
            <option value="{{ d }}" {% if d == current_domain %}selected{% endif %}>{{ d }}</option>
          {% endfor %}
        </select>
      </label>

      <label>
        Category:
        <select name="category">
          <option value="">All</option>
          {% for c in categories %}
            <option value="{{ c }}" {% if c == current_category %}selected{% endif %}>{{ c }}</option>
          {% endfor %}
        </select>
      </label>

      <label>
        Sort:
        <select name="sort">
          <option value="edge_desc" {% if current_sort == "edge_desc" %}selected{% endif %}>Edge (high → low)</option>
          <option value="resolve_asc" {% if current_sort == "resolve_asc" %}selected{% endif %}>Close date ↑</option>
          <option value="resolve_desc" {% if current_sort == "resolve_desc" %}selected{% endif %}>Close date ↓</option>
          <option value="created_asc" {% if current_sort == "created_asc" %}selected{% endif %}>Created ↑</option>
          <option value="created_desc" {% if current_sort == "created_desc" %}selected{% endif %}>Created ↓</option>
          <option value="category_asc" {% if current_sort == "category_asc" %}selected{% endif %}>Category A→Z</option>
          <option value="category_desc" {% if current_sort == "category_desc" %}selected{% endif %}>Category Z→A</option>
        </select>
      </label>

      <label>
        Min vol (real):
        <input type="number" name="min_vol" min="0" step="1" value="{{ request.args.get('min_vol', '') }}">
      </label>

      <label>
        Min days to close:
        <input type="number" name="min_days" min="0" step="1" value="{{ request.args.get('min_days', '') }}">
      </label>

      <label>
        Max days to close:
        <input type="number" name="max_days" min="0" step="1" value="{{ request.args.get('max_days', '') }}">
      </label>

      <label>
        Min edge:
        <input type="number" name="min_edge" step="0.01" value="{{ request.args.get('min_edge', '') }}">
      </label>

      <label>
        Min half-Kelly %:
        <input type="number" name="min_half" step="0.1" value="{{ request.args.get('min_half', '') }}">
      </label>

      <label>
        Search:
        <input type="text" name="q" value="{{ request.args.get('q', '') }}">
      </label>

      <label>
        Rows / page:
        <input type="number" name="limit" min="10" max="500" value="{{ current_limit }}">
      </label>

      <button type="submit">Apply</button>
    </form>
  </div>

  <div class="pagination">
    Page {{ current_page }} / {{ total_pages }}
    {% if prev_url %}
      <a href="{{ prev_url }}">Prev</a>
    {% endif %}
    {% if next_url %}
      <a href="{{ next_url }}">Next</a>
    {% endif %}
    |
    <a href="{{ export_url }}">Export CSV (this page)</a>
  </div>

  <table>
    <tr>
      <th>Market</th>
      <th>Domain</th>
      <th>Category</th>
      <th>Close</th>
      <th>Created</th>
      <th>TTC (d)</th>
      <th>Vol (real)</th>
      <th>s</th>
      <th>p</th>
      <th>Edge</th>
      <th>Side</th>
      <th>Full %</th>
      <th>Half %</th>
      <th>Full $</th>
      <th>Half $</th>
      <th>Limit</th>
      <th>Order (Half)</th>
      <th>Rationale</th>
    </tr>
    {% for r in recs %}
    <tr>
      <td>
        {% if r.url %}
          <a href="{{ r.url }}" target="_blank">{{ r.title }}</a>
        {% else %}
          {{ r.title }}
        {% endif %}
      </td>
      <td>{{ r.domain }}</td>
      <td>{{ r.category }}</td>
      <td>{{ r.resolves_at or "" }}</td>
      <td>{{ r.created_at or "" }}</td>
      <td>
        {% if r.ttc_days is not none %}
          {{ "%.1f"|format(r.ttc_days) }}
        {% endif %}
      </td>
      <td>{{ "%.0f"|format(r.volume_real) }}</td>
      <td>{{ "%.2f"|format(r.s) }}</td>
      <td>{{ "%.2f"|format(r.p) }}</td>
      <td>{{ "%.2f"|format(r.edge) }}</td>
      <td>{{ r.side }}</td>
      <td>{{ "%.1f"|format(r.full_frac*100) }}</td>
      <td>{{ "%.1f"|format(r.half_frac*100) }}</td>
      <td>{{ "%.2f"|format(r.full_frac*bankroll) }}</td>
      <td>{{ "%.2f"|format(r.half_frac*bankroll) }}</td>
      <td>{{ "%.2f"|format(r.limit) }}</td>
      <td>
        [{{ r.side }}] {{ r.title }} — Limit {{ "%.2f"|format(r.limit) }} — Stake {{ "%.1f"|format(r.half_frac*100) }}% (${{ "%.2f"|format(r.half_frac*bankroll) }})
      </td>
      <td>{{ r.rationale }}</td>
    </tr>
    {% endfor %}
  </table>
</body>
</html>
"""


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(v)
        return dt.replace(tzinfo=None)
    except Exception:
        return None


@app.route("/")
def index():
    sort = request.args.get("sort", "edge_desc")
    category_filter = request.args.get("category", "").strip()
    domain_filter = request.args.get("domain", "").strip()
    limit = request.args.get("limit", "").strip()
    min_vol = request.args.get("min_vol", "").strip()
    min_days = request.args.get("min_days", "").strip()
    max_days = request.args.get("max_days", "").strip()
    query = request.args.get("q", "").strip().lower()
    min_edge = request.args.get("min_edge", "").strip()
    min_half = request.args.get("min_half", "").strip()
    page_str = request.args.get("page", "").strip()
    fmt = request.args.get("format", "").strip().lower()

    try:
        limit_n = int(limit) if limit else 50
    except ValueError:
        limit_n = 50

    try:
        min_vol_n = float(min_vol) if min_vol else 0.0
    except ValueError:
        min_vol_n = 0.0

    try:
        min_days_n = int(min_days) if min_days else None
    except ValueError:
        min_days_n = None

    try:
        max_days_n = int(max_days) if max_days else None
    except ValueError:
        max_days_n = None

    try:
        min_edge_n = float(min_edge) if min_edge else 0.0
    except ValueError:
        min_edge_n = 0.0

    try:
        min_half_n = float(min_half) if min_half else 0.0  # percent
    except ValueError:
        min_half_n = 0.0

    try:
        page = int(page_str) if page_str else 1
    except ValueError:
        page = 1

    markets = get_markets()
    recs = build_recommendations(markets)

    categories = sorted(
        {r.category for r in recs if r.category},
        key=str.lower,
    )
    domains = sorted(
        {r.domain for r in recs if r.domain},
        key=str.lower,
    )

    # Filters
    if domain_filter:
        recs = [r for r in recs if r.domain == domain_filter]

    if category_filter:
        recs = [r for r in recs if r.category == category_filter]

    if min_vol_n > 0:
        recs = [r for r in recs if r.volume_real >= min_vol_n]

    now = datetime.utcnow()
    if min_days_n is not None or max_days_n is not None:
        filtered = []
        for r in recs:
            dt = _parse_dt(r.resolves_at)
            if dt is None:
                continue
            delta_days = (dt - now).total_seconds() / 86400.0
            if min_days_n is not None and delta_days < min_days_n:
                continue
            if max_days_n is not None and (delta_days > max_days_n or delta_days < 0):
                continue
            filtered.append(r)
        recs = filtered

    if query:
        recs = [r for r in recs if query in r.title.lower()]

    if min_edge_n > 0:
        recs = [r for r in recs if abs(r.edge) >= min_edge_n]

    if min_half_n > 0:
        recs = [r for r in recs if (r.half_frac * 100.0) >= min_half_n]

    # Sorting
    if sort == "resolve_asc":
        recs.sort(key=lambda r: (_parse_dt(r.resolves_at) or datetime.max))
    elif sort == "resolve_desc":
        recs.sort(key=lambda r: (_parse_dt(r.resolves_at) or datetime.min), reverse=True)
    elif sort == "created_asc":
        recs.sort(key=lambda r: (_parse_dt(r.created_at) or datetime.max))
    elif sort == "created_desc":
        recs.sort(key=lambda r: (_parse_dt(r.created_at) or datetime.min), reverse=True)
    elif sort == "category_asc":
        recs.sort(key=lambda r: ((r.category or "").lower(), r.title))
    elif sort == "category_desc":
        recs.sort(key=lambda r: ((r.category or "").lower(), r.title), reverse=True)
    else:
        recs.sort(key=lambda r: abs(r.edge), reverse=True)

    # Time-to-close (days)
    now = datetime.utcnow()
    for r in recs:
        dt = _parse_dt(r.resolves_at)
        if dt is None:
            r.ttc_days = None
        else:
            r.ttc_days = (dt - now).total_seconds() / 86400.0

    # Pagination
    total = len(recs)
    page_size = max(1, limit_n)
    total_pages = max(1, math.ceil(total / page_size))
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages

    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_recs = recs[start_idx:end_idx]

    # CSV export of current page
    if fmt == "csv":
        si = io.StringIO()
        writer = csv.writer(si)
        writer.writerow([
            "market_id", "domain", "title", "category",
            "resolves_at", "created_at", "ttc_days", "volume_real",
            "s", "p", "edge", "side",
            "full_frac", "half_frac", "full_d", "half_d",
            "limit", "order", "rationale", "url",
        ])
        for r in page_recs:
            full_d = r.full_frac * BANKROLL_USD
            half_d = r.half_frac * BANKROLL_USD
            order_str = f"[{r.side}] {r.title} — Limit {r.limit:.2f} — Stake {r.half_frac*100:.1f}% (${half_d:.2f})"
            writer.writerow([
                r.market_id,
                r.domain,
                r.title,
                r.category,
                r.resolves_at or "",
                r.created_at or "",
                "" if r.ttc_days is None else f"{r.ttc_days:.2f}",
                f"{r.volume_real:.0f}",
                f"{r.s:.4f}",
                f"{r.p:.4f}",
                f"{r.edge:.4f}",
                r.side,
                f"{r.full_frac:.4f}",
                f"{r.half_frac:.4f}",
                f"{full_d:.2f}",
                f"{half_d:.2f}",
                f"{r.limit:.4f}",
                order_str,
                r.rationale,
                r.url or "",
            ])
        output = si.getvalue()
        return Response(
            output,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=futuur_page.csv"},
        )

    base_params = {
        "domain": domain_filter,
        "category": category_filter,
        "sort": sort,
        "min_vol": min_vol,
        "min_days": min_days,
        "max_days": max_days,
        "q": request.args.get("q", ""),
        "limit": page_size,
        "min_edge": min_edge,
        "min_half": min_half,
    }

    prev_url = None
    next_url = None
    if page > 1:
        prev_params = base_params.copy()
        prev_params["page"] = page - 1
        prev_url = "?" + urlencode(prev_params)
    if page < total_pages:
        next_params = base_params.copy()
        next_params["page"] = page + 1
        next_url = "?" + urlencode(next_params)

    export_params = base_params.copy()
    export_params["page"] = page
    export_params["format"] = "csv"
    export_url = "?" + urlencode(export_params)

    return render_template_string(
        HTML,
        recs=page_recs,
        bankroll=BANKROLL_USD,
        risk_mode=RISK_MODE,
        categories=categories,
        domains=domains,
        current_category=category_filter,
        current_domain=domain_filter,
        current_sort=sort,
        current_limit=page_size,
        current_page=page,
        total_pages=total_pages,
        prev_url=prev_url,
        next_url=next_url,
        export_url=export_url,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
