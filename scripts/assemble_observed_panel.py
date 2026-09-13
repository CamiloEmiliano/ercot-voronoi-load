"""Assemble canonical UTC ERCOT load and regional weather observations."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOAD = PROJECT_ROOT / "data/interim/load_hourly"
DEFAULT_WEATHER = PROJECT_ROOT / "data/interim/weather_regional_hourly"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/processed/ercot_hourly_panel"


def parquet_files(directory: Path) -> tuple[Path, ...]:
    return tuple(sorted(directory.rglob("*.parquet"))) if directory.is_dir() else ()


def _read_dataset(directory: Path, label: str) -> pd.DataFrame:
    paths = parquet_files(directory)
    if not paths:
        raise FileNotFoundError(f"No {label} Parquet files found under {directory}")
    return pd.concat((pd.read_parquet(path) for path in paths), ignore_index=True)


def _validate_utc(frame: pd.DataFrame, label: str) -> pd.Series:
    parsed = pd.to_datetime(frame["timestamp_utc"], utc=True, errors="coerce")
    if parsed.isna().any():
        raise ValueError(f"{label} contains invalid timestamp_utc values")
    return parsed


def assemble_panel(load: pd.DataFrame, weather: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Validate and one-to-one join canonical load and regional weather."""
    missing_load = sorted({"timestamp_utc", "load_mw"} - set(load.columns))
    if missing_load:
        raise ValueError(
            "Canonical load is missing columns: "
            + ", ".join(missing_load)
            + "; resolve Phase 3 timestamp and unit semantics before Phase 6"
        )
    if "timestamp_utc" not in weather.columns:
        raise ValueError("Regional weather is missing column: timestamp_utc")
    load = load.copy()
    weather = weather.copy()
    load["timestamp_utc"] = _validate_utc(load, "Canonical load")
    weather["timestamp_utc"] = _validate_utc(weather, "Regional weather")
    load["load_mw"] = pd.to_numeric(load["load_mw"], errors="raise")
    if load["timestamp_utc"].duplicated().any():
        raise ValueError("Canonical load contains duplicate timestamp_utc values")
    if weather["timestamp_utc"].duplicated().any():
        raise ValueError("Regional weather contains duplicate timestamp_utc values")

    panel = load.merge(weather, on="timestamp_utc", how="inner", validate="one_to_one", sort=True)
    report = {
        "load_rows": int(len(load)),
        "weather_rows": int(len(weather)),
        "panel_rows": int(len(panel)),
        "load_timestamps_without_weather": int(len(load) - panel["timestamp_utc"].isin(weather["timestamp_utc"]).sum()),
        "weather_timestamps_without_load": int(len(weather) - panel["timestamp_utc"].isin(load["timestamp_utc"]).sum()),
        "timestamp_min_utc": None if panel.empty else panel["timestamp_utc"].min().isoformat(),
        "timestamp_max_utc": None if panel.empty else panel["timestamp_utc"].max().isoformat(),
    }
    return panel, report


def assemble_observed_panel(load_dir: Path, weather_dir: Path, output_dir: Path, overwrite: bool = False) -> dict[str, Any]:
    """Join canonical inputs and write a month-partitioned observed panel."""
    load = _read_dataset(load_dir, "canonical load")
    weather = _read_dataset(weather_dir, "regional weather")
    panel, report = assemble_panel(load, weather)
    if output_dir.exists():
        existing = tuple(output_dir.rglob("*"))
        if existing and not overwrite:
            raise FileExistsError(f"Output directory is not empty: {output_dir}; use --overwrite to replace it")
        if overwrite:
            shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    periods = panel["timestamp_utc"].dt.strftime("%Y-%m")
    for period, period_frame in panel.groupby(periods, sort=True):
        year, month = period.split("-")
        destination = output_dir / f"year={year}" / f"month={month}" / "panel.parquet"
        destination.parent.mkdir(parents=True, exist_ok=True)
        period_frame.to_parquet(destination, index=False, engine="pyarrow", compression="zstd")
    (output_dir / "quality_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--load", type=Path, default=DEFAULT_LOAD)
    parser.add_argument("--weather", type=Path, default=DEFAULT_WEATHER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = assemble_observed_panel(args.load, args.weather, args.output, overwrite=args.overwrite)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
