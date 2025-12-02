from flask import Flask, render_template_string
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
  </style>
</head>
<body>
  <h2>Futuur Scanner</h2>
  <p>Bankroll: ${{ bankroll }} | Risk mode: {{ risk_mode }}</p>

  <table>
    <tr>
      <th>Market</th>
      <th>s</th>
      <th>p</th>
      <th>Edge</th>
      <th>Side</th>
      <th>Full %</th>
      <th>Half %</th>
      <th>Limit</th>
      <th>Rationale</th>
    </tr>
    {% for r in recs %}
    <tr>
      <td>{{ r.title }}</td>
      <td>{{ "%.2f"|format(r.s) }}</td>
      <td>{{ "%.2f"|format(r.p) }}</td>
      <td>{{ "%.2f"|format(r.edge) }}</td>
      <td>{{ r.side }}</td>
      <td>{{ "%.1f"|format(r.full_frac*100) }}</td>
      <td>{{ "%.1f"|format(r.half_frac*100) }}</td>
      <td>{{ "%.2f"|format(r.limit) }}</td>
      <td>{{ r.rationale }}</td>
    </tr>
    {% endfor %}
  </table>
</body>
</html>
"""

@app.route("/")
def index():
  markets = get_markets()
  recs = build_recommendations(markets)
  return render_template_string(
      HTML,
      recs=recs,
      bankroll=BANKROLL_USD,
      risk_mode=RISK_MODE,
  )

if __name__ == "__main__":
  # for local testing; default Flask port 5000
  app.run()
