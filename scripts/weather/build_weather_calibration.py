"""Build frozen weather harmonic parameters and shared solver configuration."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path

from bayes_demand_fcst.weather import (
    DEFAULT_TRAIN_END_UTC,
    build_weather_calibration,
)
from bayes_demand_fcst.weather.calibration_store import (
    CALIBRATION_VARIABLES,
    MIN_CALIBRATION_OBSERVATIONS,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CLEAN_DIR = PROJECT_ROOT / "data/interim/weather_irregular_clean"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/interim/weather_calibration"


def parse_utc_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("train_end_utc must include a UTC offset")
    if parsed.utcoffset().total_seconds() != 0.0:
        raise argparse.ArgumentTypeError("train_end_utc must be in UTC")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean-dir", type=Path, default=DEFAULT_CLEAN_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--train-end-utc",
        type=parse_utc_datetime,
        default=DEFAULT_TRAIN_END_UTC,
    )
    parser.add_argument("--variables", nargs="+", default=list(CALIBRATION_VARIABLES))
    parser.add_argument("--min-observations", type=int, default=MIN_CALIBRATION_OBSERVATIONS)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_weather_calibration(
        clean_dir=args.clean_dir,
        output_dir=args.output,
        train_end_utc=args.train_end_utc,
        variables=tuple(args.variables),
        min_observations=args.min_observations,
        overwrite=args.overwrite,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
