"""CLI wrapper around `prompt_builder.build_prompt` for manual prompt creation."""

from __future__ import annotations

import argparse
from pathlib import Path

from prompt_builder import (
    SAMPLE_MARKETS,
    build_prompt,
    load_markets_from_csv,
    load_markets_from_json,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build analysis prompts for ChatGPT.")
    parser.add_argument(
        "--mode",
        choices=["research", "assess"],
        default="research",
        help="Which prompt to use (RESEARCH for Markets, ASSESS for Portfolio).",
    )
    parser.add_argument("--json", type=Path, help="Path to JSON file with an array of markets.")
    parser.add_argument("--csv", type=Path, help="Path to CSV file with market rows.")
    parser.add_argument("--output", type=Path, help="Optional file path to write the prompt.")
    parser.add_argument("--sample", action="store_true", help="Use sample markets.")
    args = parser.parse_args()

    markets: list[dict[str, object]] = []
    if args.json:
        markets = load_markets_from_json(args.json)
    elif args.csv:
        markets = load_markets_from_csv(args.csv)
    elif args.sample:
        markets = SAMPLE_MARKETS
    else:
        parser.error("Provide --json, --csv, or --sample to supply market data.")

    prompt = build_prompt(args.mode, markets)
    if args.output:
        args.output.write_text(prompt, encoding="utf-8")
        print(f"Saved prompt to {args.output}")
    else:
        print(prompt)


if __name__ == "__main__":
    main()
