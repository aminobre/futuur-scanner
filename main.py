# main.py

from __future__ import annotations

import importlib
import os
from typing import Any, Iterable, List


# ---- Config (with safe fallbacks) ----
try:
    from config import BANKROLL_USD, RISK_MODE  # type: ignore
except Exception:
    BANKROLL_USD = float(os.getenv("BANKROLL_USD", "1000"))
    RISK_MODE = os.getenv("RISK_MODE", "balanced")


# ---- Strategy ----
from strategy import build_recommendations  # type: ignore


def _to_list(x: Any) -> list:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    if isinstance(x, tuple):
        return list(x)
    if isinstance(x, Iterable) and not isinstance(x, (str, bytes, dict)):
        return list(x)
    return [x]


def _load_markets() -> list:
    """
    Tries a handful of likely module/function combos so main.py doesn't have to
    be updated every time you refactor the client code.
    """
    module_candidates = [
        "futuur_client",
        "portfolio_client",
        "futuur_api_raw",
        "debug_futuur",
    ]
    fn_candidates = [
        "fetch_markets",
        "get_markets",
        "load_markets",
        "list_markets",
        "fetch_open_markets",
        "get_open_markets",
        "fetch_all_markets",
    ]

    last_err: Exception | None = None

    for mod_name in module_candidates:
        try:
            mod = importlib.import_module(mod_name)
        except Exception as e:
            last_err = e
            continue

        for fn_name in fn_candidates:
            fn = getattr(mod, fn_name, None)
            if not callable(fn):
                continue
            try:
                markets = _to_list(fn())
                if markets:
                    return markets
            except TypeError as e:
                # function exists but needs args; skip
                last_err = e
                continue
            except Exception as e:
                last_err = e
                continue

    raise RuntimeError(
        "Could not load markets. No known market-loader function was found or it failed."
        + (f" Last error: {last_err}" if last_err else "")
    )


def _call_build_recommendations(markets: list) -> list:
    # Be tolerant to signature changes.
    try:
        return _to_list(build_recommendations(markets, bankroll=BANKROLL_USD, risk_mode=RISK_MODE))
    except TypeError:
        try:
            return _to_list(build_recommendations(markets, BANKROLL_USD, RISK_MODE))
        except TypeError:
            try:
                return _to_list(build_recommendations(markets, BANKROLL_USD))
            except TypeError:
                return _to_list(build_recommendations(markets))


def _fmt_str(x: Any, width: int) -> str:
    s = "" if x is None else str(x)
    s = s.replace("\n", " ").replace("\r", " ")
    return f"{s[:width]:{width}}"


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def main() -> None:
    markets = _load_markets()
    recs = _call_build_recommendations(markets)

    print(f"Bankroll: ${BANKROLL_USD:.2f} | Risk mode: {RISK_MODE}")

    print("| Market | Domain | Category | s | p | Edge | Side | Full % | Half % | Limit |")
    print("|--------|--------|----------|---|---|------|------|--------|--------|-------|")

    for r in recs:
        m = getattr(r, "market", None)

        title = getattr(r, "title", None) or getattr(m, "title", "")
        domain = getattr(r, "domain", None) or getattr(m, "domain", "")
        category = getattr(r, "category", None) or getattr(m, "category", "")

        s = _safe_float(getattr(r, "s", None), _safe_float(getattr(m, "s", None), 0.0))

        # probability field has moved around in your refactors (p vs p0)
        p = _safe_float(getattr(r, "p", None), _safe_float(getattr(r, "p0", None), 0.0))

        # edge field has moved around too (edge vs edge0)
        edge = _safe_float(getattr(r, "edge", None), _safe_float(getattr(r, "edge0", None), p - s))

        side = getattr(r, "side", "") or ""

        full_pct = 100.0 * _safe_float(getattr(r, "kelly_full", None), 0.0)
        half_pct = 100.0 * _safe_float(getattr(r, "kelly_half", None), 0.0)

        limit = getattr(r, "limit", None)
        if limit is None:
            limit = s

        print(
            f"| {_fmt_str(title, 40)} | {_fmt_str(domain, 8)} | {_fmt_str(category, 10)} | "
            f"{s:0.3f} | {p:0.3f} | {edge:+0.3f} | {_fmt_str(side, 4)} | "
            f"{full_pct:6.2f}% | {half_pct:6.2f}% | {limit} |"
        )


if __name__ == "__main__":
    main()
