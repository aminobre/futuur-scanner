from futuur_client import get_markets
from strategy import build_recommendations
from config import BANKROLL_USD, RISK_MODE


def main() -> None:
    markets = get_markets()
    recs = build_recommendations(markets)

    print(f"Bankroll: ${BANKROLL_USD:.2f} | Risk mode: {RISK_MODE}")
    print("| Market | Domain | Category | s | p | Edge | Side | Full % | Half % | Limit |")
    print("|--------|--------|----------|---|---|------|------|--------|--------|-------|")

    for r in recs[:50]:  # just show top 50
        print(
            f"| {r.title[:40]:40} | {r.domain:8} | {r.category[:10]:10} | "
            f"{r.s:.2f} | {r.p:.2f} | {r.edge:.2f} | {r.side:3} | "
            f"{r.full_frac*100:6.1f}% | {r.half_frac*100:6.1f}% | {r.limit:.2f} |"
        )


if __name__ == "__main__":
    main()
