"""CLI entry point for Futuur scanner - prints market recommendations as a table."""

from __future__ import annotations

import importlib
import logging
import os
import sys
from typing import Any, Iterable

from config import validate_config
from models import Market, Recommendation
from utils import fmt_str, logger, safe_float

# Validate configuration on startup
try:
    validate_config()
except ValueError as e:
    print(f"Configuration error: {e}", file=sys.stderr)
    sys.exit(1)


# ---- Config (with safe fallbacks) ----
try:
    from config import BANKROLL_USD, RISK_MODE  # type: ignore
except Exception:
    BANKROLL_USD = float(os.getenv("BANKROLL_USD", "1000"))
    RISK_MODE = os.getenv("RISK_MODE", "half")


# ---- Strategy ----
from strategy import build_recommendations  # type: ignore


def _to_list(x: Any) -> list[Any]:
    """Normalize a value into a list without changing its contents."""
    if x is None:
        return []
    if isinstance(x, list):
        return x
    if isinstance(x, tuple):
        return list(x)
    if isinstance(x, Iterable) and not isinstance(x, (str, bytes, dict)):
        return list(x)
    return [x]


def _load_markets() -> list[Market]:
    """Load markets from one of several possible client modules/functions.

    Tries a handful of likely module/function combos so main.py doesn't have to
    be updated every time you refactor the client code.
    """
    module_candidates: tuple[str, ...] = (
        "futuur_client",
        "portfolio_client",
        "futuur_api_raw",
        "debug_futuur",
    )
    fn_candidates: tuple[str, ...] = (
        "fetch_markets",
        "get_markets",
        "load_markets",
        "list_markets",
        "fetch_open_markets",
        "get_open_markets",
        "fetch_all_markets",
    )

    last_err: Exception | None = None

    for module_name in module_candidates:
        try:
            module = importlib.import_module(module_name)
            logger.debug(f"Successfully imported module: {module_name}")
        except ImportError as exc:
            logger.debug(f"Module {module_name} not found: {exc}")
            last_err = exc
            continue
        except Exception as exc:  # keep going; we just remember the last error seen
            logger.debug(f"Error importing module {module_name}: {exc}")
            last_err = exc
            continue

        for fn_name in fn_candidates:
            candidate = getattr(module, fn_name, None)
            if not callable(candidate):
                continue
            try:
                markets = _to_list(candidate())
                if markets:
                    logger.info(f"Successfully loaded {len(markets)} markets using {module_name}.{fn_name}")
                    return markets
            except TypeError as exc:
                # Function exists but needs args; skip and remember for error context.
                logger.debug(f"Function {module_name}.{fn_name} requires arguments: {exc}")
                last_err = exc
                continue
            except Exception as exc:
                logger.debug(f"Error calling {module_name}.{fn_name}: {exc}")
                last_err = exc
                continue

    error_msg = (
        "Could not load markets. No known market-loader function was found or it failed."
        + (f" Last error: {last_err}" if last_err else "")
    )
    logger.error(error_msg)
    raise RuntimeError(error_msg)


def _call_build_recommendations(markets: list[Market]) -> list[Recommendation]:
    """Call strategy.build_recommendations while being tolerant to signature changes."""
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




# ---- Table formatting constants ----
_TABLE_HEADER = "| Market | Domain | Category | s | p | Edge | Side | Full % | Half % | Limit |"
_TABLE_SEPARATOR = "|--------|--------|----------|---|---|------|------|--------|--------|-------|"


def _extract_recommendation_data(rec: Recommendation) -> dict[str, Any]:
    """Extract all fields needed for table display from a recommendation."""
    market = getattr(rec, "market", None)

    title = getattr(rec, "title", None) or getattr(market, "title", "")
    domain = getattr(rec, "domain", None) or getattr(market, "domain", "")
    category = getattr(rec, "category", None) or getattr(market, "category_title", "")

    s = safe_float(getattr(rec, "s", None), safe_float(getattr(market, "s", None), 0.0))

    # Probability field has moved around in refactors (p vs p0)
    p = safe_float(getattr(rec, "p", None), safe_float(getattr(rec, "p0", None), 0.0))

    # Edge field has moved around too (edge vs edge0)
    edge = safe_float(getattr(rec, "edge", None), safe_float(getattr(rec, "edge0", None), p - s))

    side = getattr(rec, "side", "") or ""

    full_pct = 100.0 * safe_float(getattr(rec, "kelly_full", None), 0.0)
    half_pct = 100.0 * safe_float(getattr(rec, "kelly_half", None), 0.0)

    limit = getattr(rec, "limit", None)
    if limit is None:
        limit = s

    return {
        "title": title,
        "domain": domain,
        "category": category,
        "s": s,
        "p": p,
        "edge": edge,
        "side": side,
        "full_pct": full_pct,
        "half_pct": half_pct,
        "limit": limit,
    }


def _format_table_row(data: dict[str, Any]) -> str:
    """Format a single recommendation row for the table."""
    return (
        f"| {fmt_str(data['title'], 40)} | {fmt_str(data['domain'], 8)} | {fmt_str(data['category'], 10)} | "
        f"{data['s']:0.3f} | {data['p']:0.3f} | {data['edge']:+0.3f} | {fmt_str(data['side'], 4)} | "
        f"{data['full_pct']:6.2f}% | {data['half_pct']:6.2f}% | {data['limit']} |"
    )


def main() -> None:
    """Entry point: load markets, build recommendations, and print a table."""
    logger.info("Starting Futuur scanner...")
    try:
        markets = _load_markets()
        logger.info(f"Loaded {len(markets)} markets")
        
        recs = _call_build_recommendations(markets)
        logger.info(f"Generated {len(recs)} recommendations")
        
        print(f"Bankroll: ${BANKROLL_USD:.2f} | Risk mode: {RISK_MODE}")
        print(_TABLE_HEADER)
        print(_TABLE_SEPARATOR)

        for rec in recs:
            data = _extract_recommendation_data(rec)
            print(_format_table_row(data))
        
        logger.info("Scanner completed successfully")
    except Exception as e:
        logger.exception("Fatal error in main()")
        raise


if __name__ == "__main__":
    main()
