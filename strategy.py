from __future__ import annotations

from config import DEFAULT_RISK_MODE  # type: ignore
from models import Market, Recommendation


EDGE_THRESHOLD = 0.02  # 2 percentage points


def risk_mode_from_string(value: str | None) -> float:
    v = (value or DEFAULT_RISK_MODE or "half").lower()
    if v.startswith("full"):
        return 1.0
    if v.startswith("half"):
        return 0.5
    # Fallback: conservative
    return 0.5


def _compute_pre_p(domain: str, s: float) -> float:
    """
    Heuristic pre-GPT probability estimate.
    Simple and conservative; GPT updates refine it later.
    """
    s = max(0.0001, min(0.9999, float(s)))
    dom = (domain or "").lower()

    # Longshot Yes: default fade
    if s <= 0.10:
        return 0.04

    # Overconfident Yes: shrink toward high 80s/low 90s
    if s >= 0.90:
        return 0.90

    # Macro / science / fundamentals-heavy → keep closer to market
    if any(k in dom for k in ("finance", "science")) and not any(
        k in dom for k in ("sports", "entertainment", "politics")
    ):
        # Mild shrink toward 0.5
        return 0.5 + 0.5 * (s - 0.5)

    # Narrative-heavy domains → fade extremes a bit
    p_raw = 0.5 + 0.3 * (s - 0.5)
    if s > 0.65:
        p_adj = p_raw - 0.10  # trim optimism
    elif s < 0.35:
        p_adj = p_raw + 0.10  # trim pessimism
    else:
        p_adj = p_raw

    return max(0.01, min(0.99, p_adj))


def _kelly_yes(p: float, s: float) -> float:
    # Yes-side Kelly (from your spec)
    if p <= s or s >= 1.0:
        return 0.0
    return (p - s) / (1.0 - s)


def _kelly_no(p: float, s: float) -> float:
    # No-side Kelly (from your spec)
    if p >= s or s <= 0.0:
        return 0.0
    return (s - p) / s


def build_recommendations(
    markets: list[Market],
    bankroll: float | None = None,  # optional for backward compatibility
    risk_mode: str | None = None,
) -> list[Recommendation]:
    # bankroll currently not used (recommendations are Kelly fractions),
    # but kept in the signature so callers can pass it without breaking.
    _ = bankroll

    risk_fraction = risk_mode_from_string(risk_mode)

    recs: list[Recommendation] = []

    for m in markets:
        s = m.s
        if s <= 0.0 or s >= 1.0:
            continue

        p0 = _compute_pre_p(m.domain, s)

        edge_yes = p0 - s
        edge_no = s - p0

        f_yes = _kelly_yes(p0, s)
        f_no = _kelly_no(p0, s)

        if f_yes <= 0.0 and f_no <= 0.0:
            # Keep a record with zero Kelly so you still see the market, but it's not sized.
            if abs(edge_yes) >= abs(edge_no):
                side = "yes"
                best_edge = edge_yes
            else:
                side = "no"
                best_edge = edge_no
            full_kelly = 0.0
        else:
            if f_yes >= f_no:
                side = "yes"
                best_edge = edge_yes
                full_kelly = f_yes
            else:
                side = "no"
                best_edge = edge_no
                full_kelly = f_no

        # Filter tiny edges from being "recommended", but keep them in UI.
        if abs(best_edge) < EDGE_THRESHOLD:
            full_kelly = 0.0

        half_kelly = full_kelly * 0.5
        # Risk mode applies as a global multiplier on the Kelly fraction.
        full_kelly *= risk_fraction
        half_kelly *= risk_fraction

        notes = ""
        if s <= 0.10:
            notes = "Longshot fade baseline"
        elif s >= 0.90:
            notes = "Crowded favorite; trimmed p"

        recs.append(
            Recommendation(
                market=m,
                side=side,
                s=s,
                p0=p0,
                edge0=best_edge,
                kelly_full=full_kelly,
                kelly_half=half_kelly,
                limit=s,
                notes=notes,
            )
        )

    # Sort by absolute edge descending by default
    recs.sort(key=lambda r: abs(r.edge0), reverse=True)
    return recs
