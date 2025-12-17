from __future__ import annotations

import csv
import io
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable

from flask import Flask, Response, jsonify, render_template_string, request, session, url_for

from config import APP_HOST, APP_PORT, BANKROLL_USD, RISK_MODE, validate_config
from futuur_api_raw import call_api
from futuur_client import get_markets
from models import Market
from portfolio_client import (
    fetch_wallet_balance,
    list_closed_real_bets,
    list_open_limit_orders,
    list_open_real_bets,
)
from prompt_builder import build_prompt
from strategy import _kelly_no, _kelly_yes
from utils import logger, parse_dt

GetPFromGptFunc = Callable[[Market], tuple[float, str]]
gpt_import_error: Exception | None = None
get_p_from_gpt: GetPFromGptFunc | None = None

try:
    from gpt_client import get_p_from_gpt as _imported_get_p_from_gpt
except Exception as exc:
    gpt_import_error = exc
else:
    get_p_from_gpt = _imported_get_p_from_gpt
    gpt_import_error = None
if gpt_import_error:
    logger.warning("GPT client not available: %s", gpt_import_error)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-in-production")

# Validate configuration on startup
try:
    validate_config()
except ValueError as e:
    logger.error(f"Configuration validation failed: {e}")


# ---------- shared date / time helpers ----------


def _days_to_close(bet_end: datetime | None) -> float | None:
    if not bet_end:
        return None
    now = datetime.now(tz=timezone.utc)
    delta = bet_end - now
    return delta.total_seconds() / 86400.0


def _human_delta(bet_end: datetime | None) -> str:
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


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def _compute_cash() -> tuple[float, str, float | None]:
    """Compute cash input from request or wallet, return (cash, source, wallet_balance)."""
    override_str = (request.args.get("cash") or "").strip()
    if override_str:
        try:
            val = float(override_str)
            if val >= 0:  # Allow 0 cash
                return val, "manual", None
        except ValueError:
            pass

    wallet = fetch_wallet_balance()
    if wallet is not None and wallet >= 0:
        return wallet, "wallet", wallet

    return float(BANKROLL_USD), "default", None


def _sort_rows(rows: list[dict[str, Any]], sort_by: str, sort_dir: str) -> list[dict[str, Any]]:
    reverse = sort_dir == "desc"

    def key_fn(r: dict[str, Any]) -> Any:
        v = r.get(sort_by)
        if isinstance(v, str):
            return v.lower()
        return v

    try:
        return sorted(rows, key=key_fn, reverse=reverse)
    except TypeError:
        return rows


# ---------- markets: fetch + filter helper (used by index and export) ----------


def _load_markets_rows_for_request(args) -> tuple[
    list[dict[str, Any]],
    str,
    str,
    str,
    str,
    str,
    list[str],
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

    rows: list[dict[str, Any]] = []

    for raw in data.get("results", []):
        cat = raw.get("category") or {}
        cat_title = cat.get("title") or ""
        cat_slug = cat.get("slug") or ""
        group = _classify_group(cat_title, cat_slug)
        outcomes = raw.get("outcomes") or []

        n_outcomes = max(len(outcomes), 1)
        base_p = 1.0 / n_outcomes

        bet_end = parse_dt(raw.get("bet_end_date"))
        created_on = parse_dt(raw.get("created_on"))
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
                "bet_end_date": bet_end.isoformat() if bet_end else None,
                "bet_end_str": bet_end.strftime("%b %d, %y %H:%M") if bet_end else "-",
                "created_on": (created_on or now).isoformat(),
                "created_str": (created_on or now).strftime("%b %d, %y %H:%M"),
                "volume_real": volume_real,
                "days_to_close": days_to_close,
                "days_to_close_str": _human_delta(bet_end),
                "url": f"https://www.futuur.com/markets/{raw.get('slug')}",
            }
            
            # Add JSON-serialized version for JavaScript
            row["json_data"] = json.dumps(row)

            rows.append(row)

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
            rows = [r for r in rows if (r["days_to_close"] is None) or (r["days_to_close"] <= max_days)]
        except ValueError:
            pass

    rows = _sort_rows(rows, sort_by, sort_dir)

    return rows, q, min_vol_str, max_days_str, sort_by, sort_dir, selected_groups


# ---------- routes: markets ----------


@app.route("/")
def index() -> str:
    rows, q, min_vol_str, max_days_str, sort_by, sort_dir, selected_groups = _load_markets_rows_for_request(request.args)

    prompt_mode = session.get("analysis_mode", "research")

    template = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Futuur Scanner - Markets</title>
  <style>
    body { margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; background:#020617; color:#e5e7eb; }
    header { padding:14px 16px; border-bottom:1px solid #111827; display:flex; justify-content:space-between; align-items:center; }
    main { padding:16px; }
    a { color:#93c5fd; text-decoration:none; }
    .nav-links { display:flex; gap:12px; align-items:center; }
    .nav-links a { padding:6px 10px; border-radius:6px; }
      .nav-links a.active { background:#111827; color:#e5e7eb; }
      .filters { display:flex; flex-wrap:wrap; gap:10px; align-items:flex-end; margin:14px 0; }
      label { font-size:12px; color:#9ca3af; display:flex; flex-direction:column; gap:4px; }
      input, select { padding:8px 10px; border-radius:6px; border:1px solid #1f2937; background:#020617; color:#e5e7eb; }
      button { padding:8px 12px; border-radius:6px; border:none; background:#2563eb; color:white; font-weight:600; cursor:pointer; }
      button.secondary { background:#111827; border:1px solid #374151; }
      input[type="checkbox"] { cursor:pointer; }
    table { width:100%; border-collapse:collapse; font-size:12px; }
    th, td { padding:6px 8px; border-bottom:1px solid #111827; vertical-align:top; }
    th { text-align:left; font-size:11px; color:#9ca3af; white-space:nowrap; }
    tr:hover { background:#0b1220; }
    .pill { display:inline-block; padding:2px 6px; border-radius:999px; font-size:10px; }
    .pill.gain { background:#064e3b; color:#4ade80; }
    .pill.loss { background:#7f1d1d; color:#fecaca; }
  </style>
</head>
<body>
  <header>
    <div class="nav-links">
      <a href="{{ url_for('index') }}" class="active">Markets</a>
      <a href="{{ url_for('portfolio') }}">Portfolio</a>
      <a href="{{ url_for('analysis') }}">Analysis</a>
      <a href="{{ url_for('export_markets_csv', **request.args) }}">Export CSV</a>
    </div>
  </header>

  <main>
    <form class="filters" method="get" action="{{ url_for('index') }}">
      <label>Search
        <input type="text" name="q" value="{{ q }}">
      </label>
      <label>Min volume
        <input type="number" step="1" name="min_vol" value="{{ min_vol_str }}">
      </label>
      <label>Max days to close
        <input type="number" step="1" name="max_days" value="{{ max_days_str }}">
      </label>
      <label style="display:flex; flex-direction:column; gap:4px;">
        <span>Group</span>
        <div style="display:flex; flex-wrap:wrap; gap:8px; align-items:center;">
          {% for g in ["Finance","Politics","Science","Entertainment","Sports","Other"] %}
            <label style="display:flex; align-items:center; gap:4px; font-size:12px; cursor:pointer;">
              <input type="checkbox" name="group" value="{{ g }}" {% if g in selected_groups %}checked{% endif %}>
              <span>{{ g }}</span>
            </label>
          {% endfor %}
        </div>
      </label>
      <label>Sort by
        <select name="sort_by">
          {% for k in ["created_on","bet_end_date","s","edge0","volume_real","days_to_close","title","group"] %}
            <option value="{{ k }}" {% if sort_by == k %}selected{% endif %}>{{ k }}</option>
          {% endfor %}
        </select>
      </label>
      <label>Dir
        <select name="sort_dir">
          <option value="desc" {% if sort_dir == "desc" %}selected{% endif %}>desc</option>
          <option value="asc" {% if sort_dir == "asc" %}selected{% endif %}>asc</option>
        </select>
      </label>
      <button type="submit">Apply</button>
    </form>

    <div style="margin-bottom:10px; display:flex; align-items:center; gap:10px;">
      <button type="button" id="selectAllBtn" class="secondary">Select All</button>
      <button type="button" id="deselectAllBtn" class="secondary">Deselect All</button>
      <button type="button" id="addToAnalysisBtn">Add to Analysis</button>
      <span id="selectionCount" class="mini" style="margin-left:10px;"></span>
    </div>

    <table>
      <thead>
        <tr>
          <th><input type="checkbox" id="selectAllCheckbox"></th>
          <th>Group</th>
          <th>Market</th>
          <th>Outcome</th>
          <th>Price</th>
          <th>p0</th>
          <th>Edge0</th>
          <th>Vol</th>
          <th>Closes</th>
          <th>Δt</th>
          <th>Created</th>
        </tr>
      </thead>
      <tbody>
        {% for r in rows %}
          <tr>
            <td><input type="checkbox" class="market-checkbox" data-market-id="{{ r.question_id }}-{{ r.outcome_id }}" data-market-data="{{ r.json_data|e }}"></td>
            <td>{{ r.group }}</td>
            <td><a href="{{ r.url }}" target="_blank" rel="noreferrer">{{ r.title }}</a></td>
            <td>{{ r.outcome_title }}</td>
            <td>{{ '%.3f' % r.s }}</td>
            <td>{{ '%.3f' % r.p0 }}</td>
            <td class="{% if r.edge0 >= 0 %}pill gain{% else %}pill loss{% endif %}">{{ '%.3f' % r.edge0 }}</td>
            <td>{{ '%.2f' % r.volume_real }}</td>
            <td>{{ r.bet_end_str }}</td>
            <td>{{ r.days_to_close_str }}</td>
            <td>{{ r.created_str }}</td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
    <div class="panel" style="margin-top:20px;">
      <h3>Import GPT research</h3>
      <p class="mini" style="margin-bottom:6px;">Paste ChatGPT’s JSON `analysis` array (RESEARCH) and apply it to the saved markets.</p>
      <textarea id="marketAnalysisInput" rows="5" style="width:100%; background:#0b1220; border:1px solid #111827; color:#e5e7eb; border-radius:6px; padding:10px;" placeholder="{\"analysis\":[...]}"></textarea>
      <div style="margin-top:8px;">
        <button type="button" class="secondary" id="applyMarketAnalysisBtn">Apply Market Analysis</button>
      </div>
    </div>
  </main>

  <script>
    const checkboxes = document.querySelectorAll('.market-checkbox');
    const selectAllCheckbox = document.getElementById('selectAllCheckbox');
    const selectAllBtn = document.getElementById('selectAllBtn');
    const deselectAllBtn = document.getElementById('deselectAllBtn');
    const addToAnalysisBtn = document.getElementById('addToAnalysisBtn');
    const selectionCount = document.getElementById('selectionCount');

    function updateSelectionCount() {
      const selected = document.querySelectorAll('.market-checkbox:checked').length;
      selectionCount.textContent = `${selected} selected`;
    }

    selectAllCheckbox.addEventListener('change', (e) => {
      checkboxes.forEach(cb => cb.checked = e.target.checked);
      updateSelectionCount();
    });

    selectAllBtn.addEventListener('click', () => {
      checkboxes.forEach(cb => cb.checked = true);
      selectAllCheckbox.checked = true;
      updateSelectionCount();
    });

    deselectAllBtn.addEventListener('click', () => {
      checkboxes.forEach(cb => cb.checked = false);
      selectAllCheckbox.checked = false;
      updateSelectionCount();
    });

    checkboxes.forEach(cb => {
      cb.addEventListener('change', () => {
        const allChecked = Array.from(checkboxes).every(c => c.checked);
        selectAllCheckbox.checked = allChecked;
        updateSelectionCount();
      });
    });

    addToAnalysisBtn.addEventListener('click', async () => {
      const selected = [];
      document.querySelectorAll('.market-checkbox:checked').forEach(cb => {
        try {
          const dataStr = cb.getAttribute('data-market-data');
          if (dataStr) {
            const data = JSON.parse(dataStr);
            selected.push(data);
          }
        } catch (e) {
          console.error('Error parsing market data:', e, cb.getAttribute('data-market-data'));
        }
      });
      
      if (selected.length === 0) {
        alert('Please select at least one market');
        return;
      }

      try {
        const response = await fetch('{{ url_for("add_to_analysis") }}', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({markets: selected, mode: 'research'})
        });
        
        const result = await response.json();
        if (response.ok && result.success) {
          alert(`Added ${result.count} market(s) to analysis`);
          window.location.href = '{{ url_for("analysis") }}';
        } else {
          alert('Error adding markets to analysis: ' + (result.error || 'Unknown error'));
        }
      } catch (e) {
        console.error('Error:', e);
        alert('Error: ' + e.message);
      }
    });

    updateSelectionCount();

    document.getElementById("applyMarketAnalysisBtn")?.addEventListener("click", async () => {
      const textarea = document.getElementById("marketAnalysisInput");
      if (!textarea) return;
      const text = textarea.value.trim();
      if (!text) {
        alert("Paste the market analysis JSON before applying.");
        return;
      }
      try {
        const resp = await fetch('{{ url_for("apply_analysis_input") }}', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({analysis: text, mode: 'research'}),
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
          throw new Error(data.error || "Failed to apply market analysis");
        }
        alert(`Applied ${data.applied} market entries, ${data.missed} unmatched.`);
        window.location.reload();
      } catch (err) {
        console.error("Error applying market analysis", err);
        alert("Unable to apply market analysis: " + err);
      }
    });
  </script>
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
    )


# ---------- routes: analysis ----------


@app.route("/add_to_analysis", methods=["POST"])
def add_to_analysis() -> Response:
    """Add selected markets to analysis session."""
    try:
        if not request.is_json:
            return jsonify({"success": False, "error": "Request must be JSON"}), 400
        
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data received"}), 400
        
        markets = data.get("markets", [])
        mode = (data.get("mode") or "research").strip().lower()
        if mode not in {"research", "assess"}:
            mode = "research"
        session["analysis_mode"] = mode
        logger.info(f"Received {len(markets)} markets to add to analysis (mode={mode})")
        
        if not markets:
            return jsonify({"success": False, "error": "No markets provided"}), 400
        
        if "analysis_markets" not in session:
            session["analysis_markets"] = []
        
        # Add markets, avoiding duplicates
        existing_ids = {m.get("question_id", 0) * 10000 + m.get("outcome_id", 0) 
                        for m in session["analysis_markets"]}
        
        new_markets = []
        for m in markets:
            market_id = m.get("question_id", 0) * 10000 + m.get("outcome_id", 0)
            if market_id not in existing_ids:
                new_markets.append(m)
                existing_ids.add(market_id)
        
        session["analysis_markets"].extend(new_markets)
        session.modified = True
        
        logger.info(f"Added {len(new_markets)} new markets to analysis (total: {len(session['analysis_markets'])})")
        
        return jsonify({"success": True, "count": len(new_markets)})
    except Exception as e:
        logger.exception("Error adding markets to analysis")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/analysis")
def analysis() -> str:
    """Display analysis page with GPT-determined probabilities and Kelly sizing."""
    if "analysis_markets" not in session or not session["analysis_markets"]:
        template = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Futuur Scanner - Analysis</title>
  <style>
    body { margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; background:#020617; color:#e5e7eb; }
    header { padding:14px 16px; border-bottom:1px solid #111827; display:flex; justify-content:space-between; align-items:center; }
    main { padding:16px; }
    a { color:#93c5fd; text-decoration:none; }
    .nav-links { display:flex; gap:12px; align-items:center; }
    .nav-links a { padding:6px 10px; border-radius:6px; }
    .nav-links a.active { background:#111827; color:#e5e7eb; }
    button { padding:8px 12px; border-radius:6px; border:none; background:#2563eb; color:white; font-weight:600; cursor:pointer; }
  </style>
</head>
<body>
  <header>
    <div class="nav-links">
      <a href="{{ url_for('index') }}">Markets</a>
      <a href="{{ url_for('portfolio') }}">Portfolio</a>
      <a href="{{ url_for('analysis') }}" class="active">Analysis</a>
    </div>
  </header>
  <main>
    <h2>Analysis</h2>
    <p>No markets selected. Go to <a href="{{ url_for('index') }}">Markets</a> to select markets for analysis.</p>
  </main>
</body>
</html>
        """
        return render_template_string(template)
    
    # Get markets from session and convert to Market objects
    market_data = session["analysis_markets"]
    analysis_rows = []
    
    for m_data in market_data:
        try:
            # Create a Market object from the stored data
            # We need to fetch full market data or reconstruct from stored data
            market = Market(
                id=m_data.get("question_id", 0),
                question_id=m_data.get("question_id", 0),
                outcome_id=m_data.get("outcome_id", 0),
                title=m_data.get("title", ""),
                outcome_title=m_data.get("outcome_title", ""),
                slug=m_data.get("slug", ""),
                domain=m_data.get("group", "Other"),
                category_title=m_data.get("category_title", ""),
                tags=m_data.get("tags", []),
                is_binary=True,
                s=m_data.get("s", 0.0),
                price=m_data.get("s", 0.0),
                volume_real=m_data.get("volume_real", 0.0),
                volume_play=0.0,
                wagers_count=0,
                bet_end=parse_dt(m_data.get("bet_end_date")) if isinstance(m_data.get("bet_end_date"), str) else m_data.get("bet_end_date"),
                days_to_close=m_data.get("days_to_close"),
                raw={},
            )
            
            # Get GPT probability (with error handling)
            manual = m_data.get("manual_analysis") or {}
            manual_p = manual.get("p")
            manual_reason = manual.get("summary") or ""
            manual_price = manual.get("price_bought")
            manual_max_avg = manual.get("max_avg_price")
            manual_half = manual.get("half_kelly")
            manual_time = manual.get("time_to_close_days")
            manual_instrument = manual.get("instrument")

            if manual_p is not None:
                gpt_p = manual_p
                gpt_reason = manual_reason or "Manual analysis applied"
                extras = []
                if manual_price is not None:
                    extras.append(f"Bought at {manual_price:.3f}")
                if manual_max_avg is not None:
                    extras.append(f"Max avg {manual_max_avg:.3f}")
                if manual_instrument:
                    extras.append(f"Instr {manual_instrument}")
                if manual_time is not None:
                    extras.append(f"{manual_time:.1f}d to close")
                if extras:
                    gpt_reason = f"{gpt_reason} ({'; '.join(extras)})"
            elif get_p_from_gpt:
                try:
                    gpt_p, gpt_reason = get_p_from_gpt(market)
                except Exception as exc:
                    logger.warning(f"GPT analysis failed for {market.title}: {exc}")
                    gpt_p = market.s
                    gpt_reason = f"GPT analysis unavailable: {exc}"
            else:
                reason_text = str(gpt_import_error) if gpt_import_error else "gpt_client module not installed"
                gpt_p = market.s
                gpt_reason = f"GPT analysis unavailable: {reason_text}"
            
            # Calculate Kelly sizing
            s = market.s
            p = gpt_p
            edge = p - s
            
            kelly_yes = _kelly_yes(p, s)
            kelly_no = _kelly_no(p, s)
            
            if kelly_yes >= kelly_no:
                side = "Yes"
                kelly = kelly_yes
            else:
                side = "No"
                kelly = kelly_no
            
            # Apply risk mode
            risk_fraction = 1.0 if RISK_MODE.lower().startswith("full") else 0.5
            kelly_adjusted = kelly * risk_fraction
            
            analysis_rows.append({
                "market": market,
                "gpt_p": gpt_p,
                "gpt_reason": gpt_reason,
                "s": s,
                "edge": edge,
                "side": side,
                "kelly_full": kelly,
                "kelly_adjusted": kelly_adjusted,
                "risk_mode": RISK_MODE,
            })
        except Exception as e:
            logger.exception(f"Error processing market {m_data.get('title', 'unknown')}")
            continue
    
    template = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Futuur Scanner - Analysis</title>
  <style>
    body { margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; background:#020617; color:#e5e7eb; }
    header { padding:14px 16px; border-bottom:1px solid #111827; display:flex; justify-content:space-between; align-items:center; }
    main { padding:16px; }
    a { color:#93c5fd; text-decoration:none; }
    .nav-links { display:flex; gap:12px; align-items:center; }
    .nav-links a { padding:6px 10px; border-radius:6px; }
    .nav-links a.active { background:#111827; color:#e5e7eb; }
    button { padding:8px 12px; border-radius:6px; border:none; background:#2563eb; color:white; font-weight:600; cursor:pointer; }
    button.secondary { background:#111827; border:1px solid #374151; }
    table { width:100%; border-collapse:collapse; font-size:12px; margin-top:10px; }
    th, td { padding:6px 8px; border-bottom:1px solid #111827; vertical-align:top; }
    th { text-align:left; font-size:11px; color:#9ca3af; white-space:nowrap; }
    tr:hover { background:#0b1220; }
    .pill { display:inline-block; padding:2px 6px; border-radius:999px; font-size:10px; }
    .pill.gain { background:#064e3b; color:#4ade80; }
    .pill.loss { background:#7f1d1d; color:#fecaca; }
    .reason { font-size:11px; color:#9ca3af; max-width:300px; }
  </style>
</head>
<body>
    <header>
      <div class="nav-links">
        <a href="{{ url_for('index') }}">Markets</a>
        <a href="{{ url_for('portfolio') }}">Portfolio</a>
        <a href="{{ url_for('analysis') }}" class="active">Analysis</a>
      </div>
      <div style="display:flex; gap:8px; align-items:center;">
      <button type="button" class="secondary" onclick="clearAnalysis()">Clear All</button>
      <select id="promptModeSelect">
        <option value="research" {% if prompt_mode == "research" %}selected{% endif %}>RESEARCH</option>
        <option value="assess" {% if prompt_mode == "assess" %}selected{% endif %}>ASSESS</option>
      </select>
      <button type="button" class="secondary" id="prepareInputBtn">Prepare Input</button>
      <button type="button" class="secondary" id="refreshAnalysisBtn" onclick="refreshAnalysis()">Refresh</button>
      </div>
    </header>
  <main>
    <h2>Market Analysis ({{ rows|length }} markets)</h2>
    <p class="mini" style="color:#9ca3af; margin-bottom:10px;">
      Risk mode: {{ risk_mode }} | Showing GPT-determined probabilities and Kelly sizing
    </p>
    
    <table>
      <thead>
        <tr>
          <th></th>
          <th>Market</th>
          <th>Outcome</th>
          <th>Price (s)</th>
          <th>GPT p</th>
          <th>Edge</th>
          <th>Side</th>
          <th>Kelly Full</th>
          <th>Kelly ({{ risk_mode }})</th>
          <th>GPT Reason</th>
        </tr>
      </thead>
      <tbody>
        {% for r in rows %}
          <tr>
            <td>
              <button type="button" class="secondary" style="font-size:10px; padding:4px 8px;" onclick="removeAnalysisMarket({{ r.market.question_id }}, {{ r.market.outcome_id }})">Remove</button>
            </td>
            <td>{{ r.market.title }}</td>
            <td>{{ r.market.outcome_title }}</td>
            <td>{{ '%.3f' % r.s }}</td>
            <td>{{ '%.3f' % r.gpt_p }}</td>
            <td class="{% if r.edge >= 0 %}pill gain{% else %}pill loss{% endif %}">{{ '%+.3f' % r.edge }}</td>
            <td>{{ r.side }}</td>
            <td>{{ '%.3f' % r.kelly_full }}</td>
            <td>{{ '%.3f' % r.kelly_adjusted }}</td>
            <td class="reason">{{ r.gpt_reason }}</td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
    <div class="panel" style="margin-top:20px;">
      <h3>Import GPT analysis</h3>
      <p class="mini" style="margin-bottom:6px;">Paste the JSON object returned by the prompt (the `analysis` array) and apply it to this view.</p>
      <textarea id="analysisInput" rows="6" style="width:100%; background:#0b1220; border:1px solid #111827; color:#e5e7eb; border-radius:6px; padding:10px;" placeholder="{\"analysis\":[...]}"></textarea>
      <div style="margin-top:8px;">
        <button type="button" class="secondary" id="applyAnalysisBtn">Apply Analysis</button>
      </div>
    </div>
  </main>
  
  <script>
    function clearAnalysis() {
      if (confirm('Clear all markets from analysis?')) {
        fetch('{{ url_for("clear_analysis") }}', {method: 'POST'})
          .then(() => window.location.reload());
      }
    }
    
    async function removeAnalysisMarket(questionId, outcomeId) {
      if (!confirm("Remove this market from analysis?")) {
        return;
      }
      try {
        const resp = await fetch('{{ url_for("remove_analysis_market") }}', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ids: [{question_id: questionId, outcome_id: outcomeId}]})
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
          throw new Error(data.error || "Failed to remove market");
        }
        window.location.reload();
      } catch (err) {
        console.error("Failed to remove market", err);
        alert("Unable to remove market: " + err);
      }
    }

    function refreshAnalysis() {
      const btn = document.getElementById("refreshAnalysisBtn");
      if (!btn) {
        return;
      }
      btn.disabled = true;
      const origText = btn.textContent;
      btn.textContent = "Refreshing...";
      fetch('{{ url_for("refresh_analysis") }}', {method: 'POST'})
        .then((resp) => resp.json())
        .then(() => window.location.reload())
        .catch((err) => {
          console.error("Failed refreshing analysis", err);
          alert("Unable to refresh analysis. Try again.");
        })
        .finally(() => {
          btn.disabled = false;
          btn.textContent = origText;
        });
    }

    async function copyTextToClipboard(text) {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text);
      }
      const textarea = document.createElement("textarea");
      textarea.style.position = "fixed";
      textarea.style.top = "-9999px";
      textarea.value = text;
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      const successful = document.execCommand("copy");
      document.body.removeChild(textarea);
      if (!successful) {
        throw new Error("Fallback copy failed");
      }
    }

    async function prepareInput() {
      const btn = document.getElementById("prepareInputBtn");
      const select = document.getElementById("promptModeSelect");
      if (!btn || !select) {
        return;
      }

      const mode = select.value;
      btn.disabled = true;
      const origText = btn.textContent;
      btn.textContent = "Preparing...";

      try {
        const resp = await fetch(`{{ url_for("prepare_analysis_input") }}?mode=${encodeURIComponent(mode)}`);
        const data = await resp.json();
        if (!resp.ok || !data.success) {
          throw new Error(data.error || "Failed to prepare prompt");
        }
        await copyTextToClipboard(data.prompt);
        btn.textContent = "Copied!";
        setTimeout(() => {
          btn.textContent = origText;
        }, 2000);
      } catch (err) {
        console.error("Error preparing input", err);
        alert("Unable to prepare prompt: " + err);
        btn.textContent = origText;
      } finally {
        btn.disabled = false;
      }
    }

    async function applyAnalysisInput() {
      const btn = document.getElementById("applyAnalysisBtn");
      const textarea = document.getElementById("analysisInput");
      const select = document.getElementById("promptModeSelect");
      if (!btn || !textarea) {
        return;
      }

      const text = textarea.value.trim();
      if (!text) {
        alert("Paste the JSON analysis before applying.");
        return;
      }

      btn.disabled = true;
      const origText = btn.textContent;
      btn.textContent = "Applying...";

      try {
        const payload = {
          analysis: text,
          mode: select ? select.value : "{{ prompt_mode }}"
        };
        const resp = await fetch('{{ url_for("apply_analysis_input") }}', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
          throw new Error(data.error || "Failed to apply analysis");
        }
        alert(`Applied ${data.applied} entries, ${data.missed} unmatched.`);
        window.location.reload();
      } catch (err) {
        console.error("Error applying analysis", err);
        alert("Unable to apply analysis: " + err);
      } finally {
        btn.disabled = false;
        btn.textContent = origText;
      }
    }

    document.getElementById("applyAnalysisBtn")?.addEventListener("click", applyAnalysisInput);

    document.getElementById("prepareInputBtn")?.addEventListener("click", prepareInput);
  </script>
</body>
</html>
    """
    
    return render_template_string(
        template,
        rows=analysis_rows,
        risk_mode=RISK_MODE,
    )


@app.route("/clear_analysis", methods=["POST"])
def clear_analysis() -> Response:
    """Clear all markets from analysis session."""
    session["analysis_markets"] = []
    session.modified = True
    return jsonify({"success": True})


@app.route("/analysis/refresh", methods=["POST"])
def refresh_analysis() -> Response:
    """Refresh the analysis markets so they recompute on the next render."""
    if "analysis_markets" not in session:
        return jsonify({"success": False, "error": "No analysis markets"}), 400
    session["analysis_markets"] = session["analysis_markets"]
    session.modified = True
    return jsonify({"success": True})


@app.route("/analysis/prepare_input")
def prepare_analysis_input() -> Response:
    """Generate a ChatGPT prompt for the current analysis markets."""
    markets = session.get("analysis_markets")
    if not markets:
        return jsonify({"success": False, "error": "No markets selected"}), 400

    mode = (request.args.get("mode") or session.get("analysis_mode") or "research").strip().lower()
    if mode not in {"research", "assess"}:
        mode = "research"
    try:
        prompt_text = build_prompt(mode, markets)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    session["analysis_mode"] = mode
    session.modified = True
    return jsonify({"success": True, "mode": mode, "prompt": prompt_text})


@app.route("/analysis/remove", methods=["POST"])
def remove_analysis_market() -> Response:
    if not request.is_json:
        return jsonify({"success": False, "error": "Expected JSON body"}), 400

    payload = request.get_json(silent=True) or {}
    ids = payload.get("ids", [])
    if not isinstance(ids, list) or not ids:
        return jsonify({"success": False, "error": "No market IDs provided"}), 400

    to_remove: set[tuple[int, int]] = set()
    for entry in ids:
        if not isinstance(entry, dict):
            continue
        try:
            qid = int(entry.get("question_id"))
            oid = int(entry.get("outcome_id"))
        except Exception:
            continue
        to_remove.add((qid, oid))
    markets = session.get("analysis_markets", [])
    retained = []
    removed = 0
    for m in markets:
        qid = m.get("question_id")
        oid = m.get("outcome_id")
        if (qid, oid) in to_remove:
            removed += 1
            continue
        retained.append(m)

    session["analysis_markets"] = retained
    session.modified = True
    return jsonify({"success": True, "removed": removed})


@app.route("/analysis/apply", methods=["POST"])
def apply_analysis_input() -> Response:
    """Apply user-pasted analysis JSON into the current session."""
    if not request.is_json:
        return jsonify({"success": False, "error": "Expected JSON body"}), 400

    payload = request.get_json(silent=True) or {}
    raw = payload.get("analysis")
    if not raw:
        return jsonify({"success": False, "error": "No analysis data supplied"}), 400

    try:
        parsed = json.loads(raw)
    except Exception as exc:
        return jsonify({"success": False, "error": f"Invalid JSON: {exc}"}), 400

    entries = []
    if isinstance(parsed, dict) and isinstance(parsed.get("analysis"), list):
        entries = parsed["analysis"]
    elif isinstance(parsed, list):
        entries = parsed
    else:
        return jsonify({"success": False, "error": "JSON must be a list or contain an 'analysis' array"}), 400

    applied = 0
    missed = 0
    mode = (payload.get("mode") or session.get("analysis_mode") or "research").strip().lower()
    if mode not in {"research", "assess"}:
        mode = "research"

    markets = session.get("analysis_markets", [])
    for entry in entries:
        try:
            qid = int(entry.get("question_id"))
            oid = int(entry.get("outcome_id"))
            p = float(entry.get("p"))
        except Exception:
            missed += 1
            continue

        summary = str(entry.get("summary", "")).strip()
        sanitized = _normalize_manual_entry(entry)
        sanitized["p"] = p if sanitized.get("p") is None else sanitized["p"]
        sanitized["summary"] = sanitized.get("summary") or summary
        key_found = False
        for m in markets:
            if (m.get("question_id") == qid) and (m.get("outcome_id") == oid):
                m["manual_analysis"] = sanitized
                key_found = True
                applied += 1
                break

        if not key_found:
            missed += 1

    session["analysis_markets"] = markets
    session["analysis_mode"] = mode
    session.modified = True
    return jsonify({"success": True, "mode": mode, "applied": applied, "missed": missed})


@app.route("/export_markets")
def export_markets_csv() -> Response:
    rows, *_ = _load_markets_rows_for_request(request.args)

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["question_id", "outcome_id", "title", "outcome_title", "group", "category", "tags", "s", "edge0", "volume_real", "bet_end", "days_to_close", "url"])
    for r in rows:
        w.writerow([
            r["question_id"],
            r["outcome_id"],
            r["title"],
            r["outcome_title"],
            r["group"],
            r["category_title"],
            ";".join(r["tags"]),
            f"{r['s']:.4f}",
            f"{r['edge0']:.4f}",
            f"{r['volume_real']:.2f}",
            r["bet_end_str"],
            f"{r['days_to_close']:.2f}" if r["days_to_close"] is not None else "",
            r["url"],
        ])
    data = out.getvalue()
    out.close()
    return Response(data, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=futuur_markets.csv"})


# ---------- portfolio helpers ----------


def _pmap_from_request() -> dict[str, float]:
    raw = (request.args.get("pmap") or "").strip()
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and "pmap" in obj and isinstance(obj["pmap"], dict):
            obj = obj["pmap"]
        if not isinstance(obj, dict):
            return {}
        out: dict[str, float] = {}
        for k, v in obj.items():
            try:
                out[str(k)] = clamp01(float(v))
            except Exception:
                continue
        return out
    except Exception:
        return {}


def _market_p_win_for_position(position: str, outcome_price: float) -> float:
    s = clamp01(outcome_price)
    pos = (position or "l").lower()
    return s if pos == "l" else (1.0 - s)


def _normalize_manual_entry(entry: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {}

    def _as_float(key: str) -> float | None:
        val = entry.get(key)
        if val is None or val == "":
            return None
        try:
            return float(val)
        except Exception:
            return None

    return {
        "p": _as_float("p"),
        "half_kelly": _as_float("half_kelly"),
        "price_bought": _as_float("price_bought"),
        "max_avg_price": _as_float("max_avg_price"),
        "time_to_close_days": _as_float("time_to_close_days"),
        "instrument": entry.get("instrument"),
        "reference_sources": entry.get("reference_sources") or [],
        "invariant": entry.get("invariant"),
        "settlement_check": entry.get("settlement_check"),
        "catalyst": entry.get("catalyst"),
        "structural_risk": entry.get("structural_risk"),
        "summary": str(entry.get("summary", "") or "").strip(),
    }


def _calc_open_bets(open_bets, pmap: dict[str, float]) -> tuple[list[dict[str, Any]], float, float, float]:
    mv_port = 0.0
    ev_port = 0.0
    total_unrealized = 0.0

    rows: list[dict[str, Any]] = []
    now = datetime.now(tz=timezone.utc)

    for b in open_bets:
        mkt_p_win = _market_p_win_for_position(b.position, b.mark_price)

        p_user = None
        if str(b.bet_id) in pmap:
            p_user = pmap[str(b.bet_id)]
        else:
            legacy = request.args.get(f"p_{b.bet_id}")
            if legacy is not None and legacy != "":
                try:
                    p_user = clamp01(float(legacy))
                except Exception:
                    p_user = None
        if p_user is None:
            p_user = mkt_p_win

        mv_value = float(b.mark_value)  # already signed from portfolio_client
        ev_value = float(b.shares) * p_user  # shares signed
        ev_edge = ev_value - mv_value
        unrealized_calc = mv_value - float(b.amount_invested)

        delta_p = float(p_user) - float(mkt_p_win)
        abs_delta_p = abs(delta_p)

        # Check if market is pending resolution (bet_end_date passed but still open)
        is_pending = False
        if b.close_date is not None:
            if b.close_date < now:
                is_pending = True

        mv_port += mv_value
        ev_port += ev_value
        total_unrealized += unrealized_calc

        rows.append(
            {
                "bet_id": b.bet_id,
                "question_id": b.question_id,
                "outcome_id": b.outcome_id,
                "question_title": b.question_title,
                "outcome_title": b.outcome_title,
                "side_display": b.side_display,
                "position": b.position,
                "amount_invested": float(b.amount_invested),
                "shares": float(b.shares),
                "avg_price": float(b.avg_price),
                "mark_price": float(b.mark_price),
                "market_p_win": float(mkt_p_win),
                "p_input": float(p_user),
                "delta_p": float(delta_p),
                "abs_delta_p": float(abs_delta_p),
                "mv_value": float(mv_value),
                "ev_value": float(ev_value),
                "ev_edge": float(ev_edge),
                "unrealized_calc": float(unrealized_calc),
                "created_str": b.created_str,
                "close_date_str": b.close_date_str,
                "is_pending": is_pending,
            }
        )

    return rows, mv_port, ev_port, total_unrealized


def _portfolio_rows_to_prompt_markets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    for r in rows:
        question_id = r.get("question_id")
        outcome_id = r.get("outcome_id")
        market = {
            "question_id": question_id,
            "outcome_id": outcome_id,
            "title": r.get("question_title"),
            "outcome_title": r.get("outcome_title"),
            "s": r.get("mark_price"),
            "edge0": r.get("delta_p"),
            "volume_real": r.get("mv_value"),
            "group": r.get("side_display", "Portfolio"),
            "tags": ["Portfolio"],
        }
        markets.append(market)
    return markets

    return rows, mv_port, ev_port, total_unrealized


# ---------- routes: portfolio ----------


@app.route("/portfolio")
def portfolio() -> str:
    cash, cash_source, wallet_balance = _compute_cash()
    cash_input = request.args.get("cash") or f"{cash:.2f}"

    pmap = _pmap_from_request()

    open_bets, open_err = list_open_real_bets(limit=500)
    closed_bets, closed_err = list_closed_real_bets(limit=500)
    open_orders, orders_err = list_open_limit_orders(limit=500)

    open_rows, mv_port, ev_port, total_unrealized = _calc_open_bets(open_bets, pmap)
    
    # Bankroll = Cash + Market Value of Portfolio
    bankroll = cash + mv_port
    
    mv_total = mv_port + cash
    ev_total = ev_port + cash

    reserved_notional = sum(float(o.reserved_notional) for o in open_orders) if open_orders else 0.0
    total_exposure = mv_port + reserved_notional
    total_realized = 0.0

    # disagreement threshold
    dp_thresh = 0.05

    # Top 5 conviction differences by |Δp|
    top5 = sorted(open_rows, key=lambda r: r.get("abs_delta_p", 0.0), reverse=True)[:5]

    # Sorting controls
    sort_open = request.args.get("sort_open") or "mv_value"
    dir_open = request.args.get("dir_open") or "desc"
    sort_closed = request.args.get("sort_closed") or "closed"
    dir_closed = request.args.get("dir_closed") or "desc"
    sort_orders = request.args.get("sort_orders") or "created"
    dir_orders = request.args.get("dir_orders") or "desc"

    def sort_url(section: str, col: str) -> str:
        params = dict(request.args)
        key = f"sort_{section}"
        dkey = f"dir_{section}"
        cur_col = params.get(key) or ""
        cur_dir = params.get(dkey) or "desc"
        new_dir = "asc" if (cur_col == col and cur_dir == "desc") else "desc"
        params[key] = col
        params[dkey] = new_dir
        return url_for("portfolio", **params)

    open_bets_sorted = _sort_rows(open_rows, sort_open, dir_open)

    open_orders_rows = []
    for o in open_orders:
        open_orders_rows.append(
            {
                "question": o.question,
                "outcome": o.outcome,
                "side": o.side,
                "position": o.position,
                "price": float(o.price),
                "shares_requested": float(o.shares_requested),
                "shares_filled": float(o.shares_filled),
                "remaining_shares": float(o.remaining_shares),
                "reserved_notional": float(o.reserved_notional),
                "status": o.status,
                "created_str": o.created_str,
                "expired_str": o.expired_str,
                "created": o.created or datetime(1970, 1, 1, tzinfo=timezone.utc),
            }
        )
    open_orders_sorted = _sort_rows(open_orders_rows, sort_orders, dir_orders)

    closed_rows = []
    for b in closed_bets:
        closed_rows.append(
            {
                "question_title": b.question_title,
                "outcome_title": b.outcome_title,
                "side_display": b.side_display,
                "amount_invested": float(b.amount_invested),
                "realized_pnl": float(b.realized_pnl),
                "closed_str": b.closed_str,
                "closed": b.closed or datetime(1970, 1, 1, tzinfo=timezone.utc),
            }
        )
    closed_bets_sorted = _sort_rows(closed_rows, sort_closed, dir_closed)

    request_args = dict(request.args)

    template = r"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>Futuur Scanner - Portfolio</title>
    <style>
      body { margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; background:#020617; color:#e5e7eb; }
      header { padding:14px 16px; border-bottom:1px solid #111827; display:flex; justify-content:space-between; align-items:center; }
      main { padding:16px; }
      a { color:#93c5fd; text-decoration:none; }
      .nav-links { display:flex; gap:12px; align-items:center; }
      .nav-links a { padding:6px 10px; border-radius:6px; }
      .nav-links a.active { background:#111827; color:#e5e7eb; }

      table { width:100%; border-collapse:collapse; font-size:12px; margin-top:8px; }
      th, td { padding:6px 8px; border-bottom:1px solid #111827; vertical-align:top; }
      th { text-align:left; font-size:11px; color:#9ca3af; white-space:nowrap; }
      th a { color:inherit; }
      tr:hover { background:#0b1220; }

      .stat-bar { display:flex; flex-wrap:wrap; gap:16px; font-size:12px; margin-bottom:10px; color:#9ca3af; }
      .stat-bar span.value { color:#e5e7eb; font-weight:500; }

      .pill { display:inline-block; padding:2px 6px; border-radius:999px; font-size:10px; }
      .pill.gain { background:#064e3b; color:#4ade80; }
      .pill.loss { background:#7f1d1d; color:#fecaca; }

      .dp { font-variant-numeric: tabular-nums; }
      .dp.good { color:#22c55e; }
      .dp.bad { color:#f97316; }
      .dp.big { font-weight:700; text-decoration: underline; }

      button { padding:6px 10px; border-radius:4px; border:none; background:#2563eb; color:white; font-size:13px; cursor:pointer; }
      button.secondary { background:#111827; border:1px solid #374151; }

      input.num { padding:4px 6px; border-radius:4px; border:1px solid #374151; background:#020617; color:#e5e7eb; width:110px; }
      input.p { width:78px; }

      textarea { background:#020617; color:#e5e7eb; border:1px solid #374151; border-radius:6px; padding:8px; }
      .error { color:#f97316; font-size:11px; margin-top:4px; }
      .muted { color:#6b7280; }
      .mini { color:#94a3b8; font-size:11px; }

      .panel { border:1px solid #111827; background:#050b18; border-radius:10px; padding:10px; margin-top:10px; }
      .panel h3 { margin:0 0 8px 0; font-size:12px; color:#cbd5e1; }
      .panel table { margin-top:0; }
    </style>
  </head>
  <body>
    <header>
    <div class="nav-links">
      <a href="{{ url_for('index') }}">Markets</a>
      <a href="{{ url_for('portfolio') }}" class="active">Portfolio</a>
    </div>
    <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
      <button type="button" id="copyPromptBtn" class="secondary">Prepare Input</button>
      <a href="{{ url_for('export_portfolio_csv', **request_args) }}"><button type="button">Export CSV</button></a>
    </div>
  </header>

  <main>
    <div class="panel">
      <h3>Import GPT portfolio analysis</h3>
      <p class="mini">Paste the ChatGPT JSON `analysis` array (ASSESS) so the stored positions adopt the reported `p`, `price_bought`, and `max_avg_price`.</p>
      <textarea id="portfolioAnalysisInput" rows="4" style="width:100%; background:#0b1220; border:1px solid #111827; color:#e5e7eb; border-radius:6px; padding:10px;" placeholder="{\"analysis\":[...]}"></textarea>
      <div style="margin-top:8px;">
        <button type="button" class="secondary" id="applyPortfolioAnalysisBtn">Apply Portfolio Analysis</button>
      </div>
    </div>

    <form method="get" action="{{ url_for('portfolio') }}" id="portForm">
        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
          <label style="font-size:11px; color:#9ca3af;">
            Cash (USD)
            <input class="num" type="number" step="0.01" name="cash" value="{{ cash_input }}">
          </label>

          <input type="hidden" name="sort_open" value="{{ sort_open }}">
          <input type="hidden" name="dir_open" value="{{ dir_open }}">
          <input type="hidden" name="sort_closed" value="{{ sort_closed }}">
          <input type="hidden" name="dir_closed" value="{{ dir_closed }}">
          <input type="hidden" name="sort_orders" value="{{ sort_orders }}">
          <input type="hidden" name="dir_orders" value="{{ dir_orders }}">

          <input type="hidden" name="pmap" id="pmap_field" value="{}">

          <span class="mini">
            Cash source: {{ cash_source }}{% if wallet_balance is not none %} (wallet ~ {{ '%.2f' % wallet_balance }}){% endif %}
            | Bankroll (Cash + MVPort): {{ '%.2f' % bankroll }}
            | Highlight threshold |Δp| ≥ {{ '%.2f' % dp_thresh }}
          </span>

          <button type="submit" class="secondary">Apply</button>
        </div>

        <div style="margin-top:12px;">
          <strong>Your p (ChatGPT paste)</strong>
          <div class="mini">Paste JSON: { "6130248": 0.62, "6130256": 0.41 } or { "pmap": { ... } }</div>
          <textarea id="pmapPaste" rows="6" style="width:100%;"></textarea>
          <div style="margin-top:6px; display:flex; gap:8px; flex-wrap:wrap;">
            <button type="button" id="validateP" class="secondary">Validate</button>
            <button type="button" id="applyP">Apply to table</button>
            <button type="button" id="saveP" class="secondary">Save</button>
            <button type="button" id="clearP" class="secondary">Clear</button>
            <span id="pStatus" class="mini"></span>
          </div>
        </div>

        <div class="stat-bar" style="margin-top:10px;">
          <span>Cash: <span class="value">{{ '%.2f' % cash }}</span></span>
          <span>MVPort: <span class="value">{{ '%.2f' % mv_port }}</span></span>
          <span>Bankroll: <span class="value">{{ '%.2f' % bankroll }}</span></span>
          <span>EVPort: <span class="value">{{ '%.2f' % ev_port }}</span></span>
          <span>MVTotal: <span class="value">{{ '%.2f' % mv_total }}</span></span>
          <span>EVTotal: <span class="value">{{ '%.2f' % ev_total }}</span></span>
        </div>

        <div class="stat-bar">
          <span>Reserved (limits): <span class="value">{{ '%.2f' % reserved_notional }}</span></span>
          <span>Exposure (MVPort + reserved): <span class="value">{{ '%.2f' % total_exposure }}</span></span>
          <span>Unrealized (MV basis): <span class="value {% if total_unrealized >= 0 %}pill gain{% else %}pill loss{% endif %}">{{ '%.2f' % total_unrealized }}</span></span>
          <span>Realized (placeholder): <span class="value {% if total_realized >= 0 %}pill gain{% else %}pill loss{% endif %}">{{ '%.2f' % total_realized }}</span></span>
        </div>

        {% if open_err %}<div class="error">Open bets error: {{ open_err }}</div>{% endif %}
        {% if closed_err %}<div class="error">Closed bets error: {{ closed_err }}</div>{% endif %}
        {% if orders_err %}<div class="error">Limit orders error: {{ orders_err }}</div>{% endif %}

        <div class="panel">
          <h3>Top 5 conviction differences (by |Δp|)</h3>
          <table>
            <thead>
              <tr>
                <th>bet_id</th>
                <th>Market</th>
                <th>Outcome</th>
                <th>Mkt p(win)</th>
                <th>Your p(win)</th>
                <th>Δp</th>
                <th>EV-MV</th>
              </tr>
            </thead>
            <tbody>
              {% for r in top5 %}
                <tr>
                  <td>{{ r.bet_id }}</td>
                  <td>{{ r.question_title }}</td>
                  <td>{{ r.outcome_title }}</td>
                  <td>{{ '%.3f' % r.market_p_win }}</td>
                  <td>{{ '%.3f' % r.p_input }}</td>
                  {% set big = (r.abs_delta_p >= dp_thresh) %}
                  {% set cls = "dp " + ("good" if r.delta_p>0 else ("bad" if r.delta_p<0 else "")) + (" big" if big else "") %}
                  <td class="{{ cls }}">{{ '%+.3f' % r.delta_p }}</td>
                  <td class="{% if r.ev_edge >= 0 %}pill gain{% else %}pill loss{% endif %}">{{ '%.2f' % r.ev_edge }}</td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>

        <h2>Open positions ({{ open_bets_sorted|length }})</h2>
        <div class="muted" style="font-size:11px; margin-bottom:6px;">
          Inputs are <b>P(win for the position)</b>. Shorts default to <b>1 - outcome price</b>.
        </div>

        <table id="openPositionsTable">
          <thead>
            <tr>
              <th><a href="{{ sort_url('open','question_title') }}">Market</a></th>
              <th>Outcome</th>
              <th>Side</th>
              <th><a href="{{ sort_url('open','amount_invested') }}">Amount in</a></th>
              <th><a href="{{ sort_url('open','shares') }}">Shares</a></th>
              <th>Avg price</th>
              <th>Mkt p(win)</th>
              <th>Your p(win)</th>
              <th><a href="{{ sort_url('open','delta_p') }}">Δp</a></th>
              <th><a href="{{ sort_url('open','mv_value') }}">MV value</a></th>
              <th><a href="{{ sort_url('open','ev_value') }}">EV value</a></th>
              <th><a href="{{ sort_url('open','ev_edge') }}">EV-MV</a></th>
              <th><a href="{{ sort_url('open','unrealized_calc') }}">Unrealized</a></th>
              <th>Status</th>
              <th>Close date</th>
              <th><a href="{{ sort_url('open','created_str') }}">Created</a></th>
            </tr>
          </thead>
          <tbody>
            {% for b in open_bets_sorted %}
            {% set big = (b.abs_delta_p >= dp_thresh) %}
            {% set cls = "dp " + ("good" if b.delta_p>0 else ("bad" if b.delta_p<0 else "")) + (" big" if big else "") %}
            <tr data-betid="{{ b.bet_id }}" data-title="{{ b.question_title|e }}" data-outcome="{{ b.outcome_title|e }}" data-mktp="{{ '%.6f' % b.market_p_win }}" data-closedate="{{ b.close_date_str|e }}" data-created="{{ b.created_str|e }}" data-side="{{ b.side_display|e }}">
              <td>{{ b.question_title }}</td>
              <td>{{ b.outcome_title }}</td>
              <td>{{ b.side_display }}</td>
              <td>{{ '%.2f' % b.amount_invested }}</td>
              <td>{{ '%.2f' % b.shares }}</td>
              <td>{{ '%.2f' % b.avg_price }}</td>
              <td class="mktp">{{ '%.3f' % b.market_p_win }}</td>
              <td>
                <input class="num p pInput" type="number" step="0.001" min="0" max="1" name="p_{{ b.bet_id }}" value="{{ '%.3f' % b.p_input }}">
              </td>
              <td class="{{ cls }} dpCell">{{ '%+.3f' % b.delta_p }}</td>
              <td>{{ '%.2f' % b.mv_value }}</td>
              <td class="evCell">{{ '%.2f' % b.ev_value }}</td>
              <td class="{% if b.ev_edge >= 0 %}pill gain{% else %}pill loss{% endif %} evEdgeCell">{{ '%.2f' % b.ev_edge }}</td>
              <td class="{% if b.unrealized_calc >= 0 %}pill gain{% else %}pill loss{% endif %}">{{ '%.2f' % b.unrealized_calc }}</td>
              <td>{% if b.is_pending %}<span class="pill" style="background:#7c2d12; color:#fdba74;">Pending</span>{% else %}Open{% endif %}</td>
              <td>{{ b.close_date_str }}</td>
              <td>{{ b.created_str }}</td>
            </tr>
            {% endfor %}
          </tbody>
        </table>

        <h2>Open limit orders ({{ open_orders_sorted|length }})</h2>
        <table>
          <thead>
            <tr>
              <th><a href="{{ sort_url('orders','question') }}">Market</a></th>
              <th><a href="{{ sort_url('orders','outcome') }}">Outcome</a></th>
              <th>Side</th>
              <th>Pos</th>
              <th><a href="{{ sort_url('orders','price') }}">Price</a></th>
              <th>Requested</th>
              <th>Filled</th>
              <th><a href="{{ sort_url('orders','remaining_shares') }}">Remaining</a></th>
              <th><a href="{{ sort_url('orders','reserved_notional') }}">Reserved</a></th>
              <th>Status</th>
              <th><a href="{{ sort_url('orders','created_str') }}">Created</a></th>
              <th>Expires</th>
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
              <th><a href="{{ sort_url('closed','question_title') }}">Market</a></th>
              <th>Outcome</th>
              <th>Side</th>
              <th><a href="{{ sort_url('closed','amount_invested') }}">Amount in (approx)</a></th>
              <th><a href="{{ sort_url('closed','realized_pnl') }}">Realized PnL (placeholder)</a></th>
              <th><a href="{{ sort_url('closed','closed') }}">Closed</a></th>
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

        <div style="margin-top:14px;">
          <button type="submit">Apply</button>
        </div>
      </form>

      <script>
        const STORAGE_KEY = "pmap";
        const DP_THRESH = {{ dp_thresh|tojson }};

        function loadPMap() {
          try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}"); }
          catch { return {}; }
        }
        function savePMap(pmap) {
          localStorage.setItem(STORAGE_KEY, JSON.stringify(pmap));
        }
        function clamp01(x) {
          return Math.max(0, Math.min(1, x));
        }

        const statusEl = document.getElementById("pStatus");
        const pasteEl = document.getElementById("pmapPaste");

        function normalizePMap(obj) {
          if (obj && typeof obj === "object" && obj.pmap && typeof obj.pmap === "object") obj = obj.pmap;
          const out = {};
          for (const k in obj) {
            const v = Number(obj[k]);
            if (!isNaN(v)) out[String(k)] = clamp01(v);
          }
          return out;
        }

        function updateRowDerived(tr) {
          const betId = tr.dataset.betid;
          const inp = tr.querySelector(".pInput");
          const mktp = Number(tr.dataset.mktp);
          if (!inp || isNaN(mktp)) return;

          const yourp = clamp01(Number(inp.value));
          const dp = yourp - mktp;

          const dpCell = tr.querySelector(".dpCell");
          if (dpCell) {
            dpCell.textContent = (dp >= 0 ? "+" : "") + dp.toFixed(3);
            dpCell.classList.remove("good","bad","big");
            if (dp > 0) dpCell.classList.add("good");
            if (dp < 0) dpCell.classList.add("bad");
            if (Math.abs(dp) >= DP_THRESH) dpCell.classList.add("big");
          }
        }

        function applyPMapToTable(pmap) {
          let applied = 0, ignored = 0;
          document.querySelectorAll("tr[data-betid]").forEach(tr => {
            const betId = tr.dataset.betid;
            const inp = tr.querySelector(".pInput");
            if (!inp) return;
            if (pmap[betId] !== undefined) {
              inp.value = pmap[betId];
              updateRowDerived(tr);
              applied++;
            } else {
              ignored++;
            }
          });
          statusEl.textContent = `Applied ${applied}, ignored ${ignored}`;
        }

        // Hydrate textarea + table from localStorage on load
        const stored = loadPMap();
        if (Object.keys(stored).length > 0) {
          pasteEl.value = JSON.stringify(stored, null, 2);
          applyPMapToTable(stored);
        }

        // When user manually edits any p input, update storage + derived Δp instantly
        document.querySelectorAll("tr[data-betid]").forEach(tr => {
          const betId = tr.dataset.betid;
          const inp = tr.querySelector(".pInput");
          if (!inp) return;
          inp.addEventListener("change", () => {
            const v = Number(inp.value);
            if (!isNaN(v)) {
              const p = loadPMap();
              p[betId] = clamp01(v);
              savePMap(p);
              pasteEl.value = JSON.stringify(p, null, 2);
              updateRowDerived(tr);
            }
          });
        });

        document.getElementById("validateP").onclick = () => {
          try {
            const obj = JSON.parse(pasteEl.value);
            const p = normalizePMap(obj);
            for (const k in p) {
              const v = Number(p[k]);
              if (isNaN(v) || v < 0 || v > 1) throw `Invalid p for ${k}`;
            }
            statusEl.textContent = `Valid JSON (${Object.keys(p).length} entries)`;
          } catch (e) {
            statusEl.textContent = "Invalid: " + e;
          }
        };

        document.getElementById("applyP").onclick = () => {
          try {
            const obj = JSON.parse(pasteEl.value);
            const p = normalizePMap(obj);
            applyPMapToTable(p);
          } catch (e) {
            statusEl.textContent = "Invalid: " + e;
          }
        };

        document.getElementById("saveP").onclick = () => {
          try {
            const obj = JSON.parse(pasteEl.value);
            const p = normalizePMap(obj);
            savePMap(p);
            applyPMapToTable(p);
            statusEl.textContent = "Saved";
          } catch (e) {
            statusEl.textContent = "Invalid: " + e;
          }
        };

        document.getElementById("clearP").onclick = () => {
          localStorage.removeItem(STORAGE_KEY);
          pasteEl.value = "";
          document.querySelectorAll(".pInput").forEach(inp => inp.value = "");
          document.querySelectorAll("tr[data-betid]").forEach(tr => updateRowDerived(tr));
          statusEl.textContent = "Cleared";
        };

        // On submit, inject pmap into hidden field (server recalculates totals + export args)
        document.getElementById("portForm").addEventListener("submit", () => {
          const p = loadPMap();
          document.getElementById("pmap_field").value = JSON.stringify(p);
        });

        // Build CSV from current open positions table and copy prompt
        async function copyTextToClipboard(text) {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            return navigator.clipboard.writeText(text);
          }
          const textarea = document.createElement("textarea");
          textarea.style.position = "fixed";
          textarea.style.top = "-9999px";
          textarea.value = text;
          document.body.appendChild(textarea);
          textarea.focus();
          textarea.select();
          const successful = document.execCommand("copy");
          document.body.removeChild(textarea);
          if (!successful) {
            throw new Error("Fallback copy failed");
          }
        }

        async function preparePortfolioPrompt() {
          const btn = document.getElementById("copyPromptBtn");
          if (!btn) {
            return;
          }
          btn.disabled = true;
          const origText = btn.textContent;
          btn.textContent = "Preparing...";
          try {
            const resp = await fetch('{{ url_for("prepare_portfolio_input") }}');
            const data = await resp.json();
            if (!resp.ok || !data.success) {
              throw new Error(data.error || "Failed to prepare portfolio prompt");
            }
            await copyTextToClipboard(data.prompt);
            btn.textContent = "Copied!";
            setTimeout(() => {
              btn.textContent = origText;
            }, 2000);
          } catch (err) {
            console.error("Error preparing portfolio prompt", err);
            alert("Unable to prepare prompt: " + err);
            btn.textContent = origText;
          } finally {
            btn.disabled = false;
          }
        }

        document.getElementById("copyPromptBtn").onclick = async () => {
          await preparePortfolioPrompt();
        };

        document.getElementById("applyPortfolioAnalysisBtn")?.addEventListener("click", async () => {
          const textarea = document.getElementById("portfolioAnalysisInput");
          if (!textarea) return;
          const text = textarea.value.trim();
          if (!text) {
            alert("Paste the portfolio analysis JSON before applying.");
            return;
          }
          try {
            const resp = await fetch('{{ url_for("apply_analysis_input") }}', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({analysis: text, mode: 'assess'}),
            });
            const data = await resp.json();
            if (!resp.ok || !data.success) {
              throw new Error(data.error || "Failed to apply portfolio analysis");
            }
            alert(`Applied ${data.applied} entries, ${data.missed} unmatched.`);
            window.location.reload();
          } catch (err) {
            console.error("Error applying portfolio analysis", err);
            alert("Unable to apply portfolio analysis: " + err);
          }
        });
      </script>

    </main>
  </body>
</html>
    """

    return render_template_string(
        template,
        cash_input=cash_input,
        cash_source=cash_source,
        wallet_balance=wallet_balance,
        cash=cash,
        bankroll=bankroll,
        mv_port=mv_port,
        ev_port=ev_port,
        mv_total=mv_total,
        ev_total=ev_total,
        reserved_notional=reserved_notional,
        total_exposure=total_exposure,
        total_unrealized=total_unrealized,
        total_realized=total_realized,
        open_err=open_err,
        closed_err=closed_err,
        orders_err=orders_err,
        open_bets_sorted=open_bets_sorted,
        open_orders_sorted=open_orders_sorted,
        closed_bets_sorted=closed_bets_sorted,
        request_args=request_args,
        sort_open=sort_open,
        dir_open=dir_open,
        sort_closed=sort_closed,
        dir_closed=dir_closed,
        sort_orders=sort_orders,
        dir_orders=dir_orders,
        sort_url=sort_url,
        dp_thresh=dp_thresh,
        top5=top5,
    )


@app.route("/portfolio/export")
def export_portfolio_csv() -> Response:
    pmap = _pmap_from_request()
    open_bets, _ = list_open_real_bets(limit=500)
    open_rows, *_ = _calc_open_bets(open_bets, pmap)

    out = io.StringIO()
    w = csv.writer(out)

    w.writerow([
        "bet_id",
        "market",
        "outcome",
        "side",
        "shares",
        "amount_in",
        "avg_price",
        "mkt_p_win",
        "your_p_win",
        "delta_p",
        "mv_value",
        "ev_value",
        "ev_minus_mv",
        "unrealized_mv_basis",
        "close_date",
        "created",
    ])

    for r in open_rows:
        w.writerow([
            r["bet_id"],
            r["question_title"],
            r["outcome_title"],
            r["side_display"],
            f"{r['shares']:.2f}",
            f"{r['amount_invested']:.2f}",
            f"{r['avg_price']:.2f}",
            f"{r['market_p_win']:.3f}",
            f"{r['p_input']:.3f}",
            f"{r['delta_p']:+.3f}",
            f"{r['mv_value']:.2f}",
            f"{r['ev_value']:.2f}",
            f"{r['ev_edge']:.2f}",
            f"{r['unrealized_calc']:.2f}",
            r["close_date_str"],
            r["created_str"],
        ])

    data = out.getvalue()
    out.close()
    return Response(data, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=futuur_portfolio.csv"})


@app.route("/portfolio/prepare_input")
def prepare_portfolio_input() -> Response:
    """Generate an ASSESS prompt describing current open portfolio positions."""
    open_bets, open_err = list_open_real_bets(limit=500)
    if open_err:
        return jsonify({"success": False, "error": str(open_err)}), 400

    open_rows, *_ = _calc_open_bets(open_bets, {})
    if not open_rows:
        return jsonify({"success": False, "error": "No open bets found"}), 400

    markets = _portfolio_rows_to_prompt_markets(open_rows)
    prompt_text = build_prompt("assess", markets)
    return jsonify({"success": True, "mode": "assess", "prompt": prompt_text})


if __name__ == "__main__":
    app.run(host=APP_HOST, port=APP_PORT, debug=True)
