import json
import os
import time
from typing import Tuple

from models import Market

try:
    from openai import OpenAI, OpenAIError
except ImportError as exc:
    OpenAI = None  # type: ignore[assignment]
    OpenAIError = Exception  # type: ignore[assignment]
    OPENAI_IMPORT_ERROR = exc
else:
    OPENAI_IMPORT_ERROR = None

MODEL_NAME = os.getenv("GPT_MODEL", "gpt-4o-mini")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), max_retries=0) if OpenAI else None
RATE_LIMITED_UNTIL = 0.0
RATE_LIMIT_COOLDOWN_SEC = 60.0


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
    Uses actual Market model fields.
    """
    lines = [
        f"Title: {market.title}",
        f"Category: {market.category_title}",
        f"Domain: {market.domain}",
        f"Outcome: {market.outcome_title}",
        f"Price (s): {market.s:.4f}",
        f"No price (implied): {1.0 - market.s:.4f}",
    ]
    
    if market.bet_end:
        lines.append(f"Resolves at: {market.bet_end}")
    if market.days_to_close is not None:
        lines.append(f"Days to close: {market.days_to_close:.1f}")

    # Include tags if present
    if market.tags:
        lines.append("Tags: " + ", ".join(market.tags))

    # Include raw data if available
    raw = market.raw or {}
    question = raw.get("question", {})
    if question:
        if question.get("description"):
            lines.append(f"Description: {question.get('description')[:200]}")
    
    return "\n".join(lines)


def _clamp_p(x: float) -> float:
    return max(0.0, min(1.0, x))


def _fallback_p(market: Market, explanation: str | None = None) -> Tuple[float, str]:
    """Basic fallback that reuses the market price and mentions the missing client."""
    s = market.s or 0.5
    # Slightly nudge extremes toward the center for a more conservative default
    if s >= 0.9:
        p = s - 0.05
    elif s <= 0.1:
        p = s + 0.05
    else:
        p = s

    reason = explanation or "OpenAI client unavailable; defaulting to the market price."
    return _clamp_p(p), reason.strip()


def get_p_from_gpt(market: Market) -> Tuple[float, str]:
    if client is None:
        explanation = f"{OPENAI_IMPORT_ERROR}" if OPENAI_IMPORT_ERROR else "OpenAI client not configured"
        return _fallback_p(market, explanation)

    if time.time() < RATE_LIMITED_UNTIL:
        reason = "OpenAI rate limit still in effect; using market price"
        return _fallback_p(market, reason)

    prompt = build_market_prompt(market)

    try:
        resp = client.responses.create(
            model=MODEL_NAME,
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
        )
    except OpenAIError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        reason = f"API error: {exc}"
        if status == 429:
            globals()["RATE_LIMITED_UNTIL"] = time.time() + RATE_LIMIT_COOLDOWN_SEC
            reason = "OpenAI rate limit exceeded; using market price"
        return _fallback_p(market, reason)

    text = resp.output[0].content[0].text
    try:
        data = json.loads(text)
    except Exception as exc:
        raise RuntimeError(f"Failed to parse JSON from model: {exc}\nRaw: {text}")

    if "p" not in data or not isinstance(data["p"], (int, float)):
        raise RuntimeError(f"Model JSON missing numeric 'p': {data}")

    p = _clamp_p(float(data["p"]))
    reason = str(data.get("reason", "")).strip()
    if not reason:
        reason = "Model did not provide a reason."
    return p, reason
