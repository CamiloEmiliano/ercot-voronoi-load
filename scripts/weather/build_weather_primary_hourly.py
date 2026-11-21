"""Build the causal Gate 3 primary hourly weather cube."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path

from bayes_demand_fcst.weather import ObservationMode, build_weather_primary_hourly
from bayes_demand_fcst.weather.primary import PRIMARY_VARIABLES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CLEAN_DIR = PROJECT_ROOT / "data/interim/weather_irregular_clean"
DEFAULT_CALIBRATION_DIR = PROJECT_ROOT / "data/interim/weather_calibration"
DEFAULT_STATIONS = PROJECT_ROOT / "data/interim/station_metadata/station_metadata.parquet"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/interim/weather_primary_hourly"


def parse_utc_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a UTC offset")
    if parsed.utcoffset().total_seconds() != 0.0:
        raise argparse.ArgumentTypeError("timestamp must be in UTC")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean-dir", type=Path, default=DEFAULT_CLEAN_DIR)
    parser.add_argument("--calibration-dir", type=Path, default=DEFAULT_CALIBRATION_DIR)
    parser.add_argument("--stations", type=Path, default=DEFAULT_STATIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--variables", nargs="+", default=list(PRIMARY_VARIABLES))
    parser.add_argument("--year-months", nargs="+", default=None)
    parser.add_argument("--start-utc", type=parse_utc_datetime, default=None)
    parser.add_argument("--end-utc", type=parse_utc_datetime, default=None)
    parser.add_argument("--mode", choices=(ObservationMode.CAUSAL.value,), default=ObservationMode.CAUSAL.value)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_weather_primary_hourly(
        clean_dir=args.clean_dir,
        calibration_dir=args.calibration_dir,
        station_metadata_path=args.stations,
        output_dir=args.output,
        variables=tuple(args.variables),
        year_months=tuple(args.year_months) if args.year_months is not None else None,
        start_utc=args.start_utc,
        end_utc=args.end_utc,
        mode=ObservationMode(args.mode),
        overwrite=args.overwrite,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
