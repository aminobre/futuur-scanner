from futuur_client import get_markets
from strategy import build_recommendations
from config import BANKROLL_USD, RISK_MODE


def main():
    markets = get_markets()
    recs = build_recommendations(markets)

    print(f"Bankroll: ${BANKROLL_USD:.2f} | Risk mode: {RISK_MODE}")
    print("| Market | s | p | Edge | Side | Full % | Half % | Limit | Rationale |")
    print("|--------|---|---|------|------|--------|--------|-------|-----------|")

    for r in recs:
        print(
            f"| {r.title[:40]} | "
            f"{r.s:.2f} | {r.p:.2f} | {r.edge:.2f} | {r.side} | "
            f"{r.full_frac*100:.1f}% | {r.half_frac*100:.1f}% | "
            f"{r.limit:.2f} | {r.rationale} |"
        )

    print("\nOrders to Place Now:")
    for r in recs:
        frac = r.full_frac if RISK_MODE == "full" else r.half_frac
        stake_pct = frac * 100
        stake_usd = frac * BANKROLL_USD
        print(
            f"[{r.side}] {r.title[:60]} — Limit {r.limit:.2f} — "
            f"Stake {stake_pct:.1f}% (${stake_usd:.2f}) — {r.rationale}"
        )


if __name__ == "__main__":
    main()
