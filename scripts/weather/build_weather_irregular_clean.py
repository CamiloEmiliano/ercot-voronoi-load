"""Build the versioned irregular-observation ASOS clean store."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bayes_demand_fcst.weather import build_weather_irregular_clean
from bayes_demand_fcst.weather.clean_store import STATE_CODES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW_DIR = PROJECT_ROOT / "data/raw"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/interim/weather_irregular_clean"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--state-codes", nargs="+", default=list(STATE_CODES))
    parser.add_argument("--year-months", nargs="+", default=None)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_weather_irregular_clean(
        raw_dir=args.raw_dir,
        output_dir=args.output,
        state_codes=tuple(args.state_codes),
        year_months=tuple(args.year_months) if args.year_months is not None else None,
        overwrite=args.overwrite,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
