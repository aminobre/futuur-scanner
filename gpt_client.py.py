import json
from typing import Tuple

from openai import OpenAI

from models import Market

client = OpenAI()


SYSTEM_PROMPT = """
You are Futuur Finance + Crypto Betting assistant.

Task:
- Given a single Futuur market and its current Yes price s, estimate your fair probability p that this outcome resolves Yes.
- Follow the user's heuristics strictly:

Heuristics:
- Macro: Compare to broad consensus (e.g. CPI, jobs, GDP). Fade crowded mid-range bins if market shows 55–65%.
- Crypto barriers: Treat BTC daily vol ~45%, ETH ~70%. If barrier is >2σ away by expiry, lean No; if <1σ, lean Yes.
- Regulatory: Use process realism (bill stages, timelines). Over-priced optimism -> lean No.
- ROF: Start from 50/50; fade extremes >65% unless very strong justification.
- Longshots: Yes ≤10% are usually overpriced; default to p <= 0.08 unless exceptional evidence.
- Narrative fade: When hype dominates fundamentals, lean opposite sign.

Output requirements:
- You must produce a single JSON object with keys:
  - "p": fair probability in [0,1] as a float
  - "reason": short text explanation (1–3 sentences, max)
  - "notes": optional extra details (may be empty string)

Be disciplined about numbers: if you're unsure, keep p close to 0.5 and explain uncertainty.
"""


def build_market_prompt(market: Market) -> str:
    """
    Build a compact description of the market for the model.
    You can extend this later with more raw fields if useful.
    """
    lines = [
        f"Title: {market.title}",
        f"Category: {market.category}",
        f"Subcategory: {market.subcategory or ''}",
        f"Yes price s: {market.yes_price:.4f}",
        f"No price (implied): {market.no_price:.4f}",
        f"Resolves at: {market.resolves_at or 'unknown'}",
        f"Created at: {market.created_at or 'unknown'}",
    ]

    # Include tags if present in raw JSON
    tags = []
    raw = market.raw or {}
    for t in raw.get("tags", []):
        name = t.get("name") or ""
        slug = t.get("slug") or ""
        tags.append(f"{name} ({slug})")
    if tags:
        lines.append("Tags: " + ", ".join(tags))

    # Include outcomes summary for context (without prices, or with if you want)
    outs = []
    for o in raw.get("outcomes", []):
        outs.append(o.get("title") or "")
    if outs:
        lines.append("Outcomes: " + " | ".join(outs))

    return "\n".join(lines)


def get_p_from_gpt(market: Market) -> Tuple[float, str]:
    """
    Call OpenAI to get a fair probability p and a short rationale.

    Returns:
        (p, reason)
    Raises:
        RuntimeError if response cannot be parsed.
    """
    prompt = build_market_prompt(market)

    resp = client.responses.create(
        model="gpt-5.1-mini",  # or gpt-4.1-mini if you prefer
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Estimate fair probability p for this outcome resolving YES.\n\n"
                    + prompt
                    + "\n\nReturn ONLY a JSON object."
                ),
            },
        ],
        response_format={"type": "json_object"},
    )

    # New Responses API: text is in output[0].content[0].text
    text = resp.output[0].content[0].text
    try:
        data = json.loads(text)
    except Exception as e:
        raise RuntimeError(f"Failed to parse JSON from model: {e}\nRaw: {text}")

    if "p" not in data or not isinstance(data["p"], (int, float)):
        raise RuntimeError(f"Model JSON missing numeric 'p': {data}")

    p = float(data["p"])
    # Clamp to [0,1] for safety
    p = max(0.0, min(1.0, p))
    reason = str(data.get("reason", "")).strip()

    return p, reason or "Model did not provide a reason."
