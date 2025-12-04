from typing import List

from models import Market, Recommendation
from config import BANKROLL_USD, EDGE_THRESHOLD, RISK_MODE


def estimate_p(market: Market, s: float) -> tuple[float, str]:
    """
    Simple baseline p-estimator.
    You will override this with CSV+ChatGPT 'p_final' externally.

    For now:
      - Start at 0.5 for all domains.
      - Apply a longshot clamp for small s.
    """
    # Domain-based tweaks can go here later if you want.
    p = 0.5

    # Longshot rule: Yes <= 10% -> usually No (cap p)
    if s <= 0.10 and p > 0.08:
        p = 0.08
        reason = "Baseline 0.5 with longshot cap"
    else:
        reason = "Baseline 0.5"

    return p, reason


def kelly_yes(p: float, s: float) -> float:
    """Kelly fraction for betting Yes at price s (probability p)."""
    if p <= s or s >= 1.0:
        return 0.0
    return (p - s) / (1.0 - s)


def kelly_no(p: float, s: float) -> float:
    """Kelly fraction for betting No against Yes price s (probability p)."""
    if p >= s or s <= 0.0:
        return 0.0
    return (s - p) / s


def build_recommendations(markets: List[Market]) -> List[Recommendation]:
    recs: List[Recommendation] = []

    for m in markets:
        s = m.yes_price
        p, p_reason = estimate_p(m, s)

        edge_yes = p - s
        edge_no = s - p

        f_yes = kelly_yes(p, s)
        f_no = kelly_no(p, s)

        # if neither side has positive Kelly, skip
        if f_yes <= 0 and f_no <= 0:
            continue

        # pick the side with larger Kelly
        if f_yes > f_no:
            edge = edge_yes
            side = "Yes"
            full_frac = f_yes
        else:
            edge = edge_no
            side = "No"
            full_frac = f_no

        # enforce minimum edge in percentage points (pre-research proxy)
        if edge < EDGE_THRESHOLD:
            continue

        half_frac = full_frac / 2.0
        limit = s if side == "Yes" else (1.0 - s)

        recs.append(
            Recommendation(
                market_id=m.id,
                title=m.title,
                s=s,
                p=p,
                edge=edge,
                side=side,
                full_frac=full_frac,
                half_frac=half_frac,
                limit=limit,
                rationale=p_reason,
                category=m.category,
                subcategory=m.subcategory,
                resolves_at=m.resolves_at,
                created_at=m.created_at,
                volume_real=m.volume_real,
                url=m.url,
                domain=m.domain,
            )
        )

    # default order: sort by |edge| descending
    recs.sort(key=lambda r: abs(r.edge), reverse=True)
    return recs
