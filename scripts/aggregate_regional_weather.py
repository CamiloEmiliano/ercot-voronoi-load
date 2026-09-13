"""Aggregate station-hour weather using clipped Voronoi area weights."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEATHER = PROJECT_ROOT / "data/interim/weather_station_hourly"
DEFAULT_WEIGHTS = PROJECT_ROOT / "data/interim/voronoi_weights/voronoi_weights.parquet"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/interim/weather_regional_hourly"
WEATHER_VARIABLES = ("tmpf", "dwpf", "relh", "feel", "p01i", "mslp", "alti", "vsby")


def weather_partitions(weather_dir: Path) -> tuple[Path, ...]:
    return tuple(sorted(path for path in weather_dir.glob("year=*/month=*" ) if path.is_dir()))


def _weight_station_map(weights: pd.DataFrame) -> pd.DataFrame:
    required = {"station_id", "station_aliases", "area_weight"}
    missing = sorted(required - set(weights.columns))
    if missing:
        raise ValueError(f"Voronoi weights are missing columns: {', '.join(missing)}")
    rows: list[dict[str, Any]] = []
    for row in weights.itertuples(index=False):
        aliases = str(row.station_aliases).split(",")
        for station_id in aliases:
            rows.append(
                {
                    "station_id": station_id,
                    "canonical_station_id": row.station_id,
                    "area_weight": float(row.area_weight),
                }
            )
    mapping = pd.DataFrame(rows)
    if mapping["station_id"].duplicated().any():
        raise ValueError("Voronoi station aliases map one station ID to multiple cells")
    return mapping


def aggregate_weather_frame(frame: pd.DataFrame, weights: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Compute weighted regional values and variable-specific coverage."""
    required = {"station_id", "timestamp_utc", *WEATHER_VARIABLES}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Station weather frame is missing columns: {', '.join(missing)}")
    mapping = _weight_station_map(weights)
    joined = frame.merge(mapping, on="station_id", how="left", validate="many_to_one")
    if joined["area_weight"].isna().any():
        missing_stations = sorted(joined.loc[joined["area_weight"].isna(), "station_id"].astype(str).unique())[:10]
        raise ValueError(f"Station weather has no Voronoi weight: {missing_stations}")

    timestamp = pd.to_datetime(joined["timestamp_utc"], utc=True, errors="raise")
    joined["timestamp_utc"] = timestamp
    grouped = joined.groupby("timestamp_utc", sort=True)
    result = pd.DataFrame(index=grouped.size().index)
    result.index.name = "timestamp_utc"
    for variable in WEATHER_VARIABLES:
        values = pd.to_numeric(joined[variable], errors="coerce")
        valid = values.notna() & joined["area_weight"].gt(0)
        numerator = (values.where(valid, 0.0) * joined["area_weight"].where(valid, 0.0)).groupby(
            joined["timestamp_utc"], sort=True
        ).sum()
        denominator = joined["area_weight"].where(valid, 0.0).groupby(joined["timestamp_utc"], sort=True).sum()
        result[f"{variable}_regional"] = numerator.div(denominator.where(denominator.gt(0)))
        result[f"{variable}_coverage"] = denominator

    positive = joined["area_weight"].gt(0)
    contributing = joined["area_weight"].where(positive & joined[list(WEATHER_VARIABLES)].notna().any(axis=1))
    result["active_station_count"] = contributing.groupby(joined["timestamp_utc"], sort=True).count()
    result["weather_coverage"] = result[[f"{variable}_coverage" for variable in WEATHER_VARIABLES]].mean(axis=1)
    result["configuration_id"] = str(weights["configuration_id"].iloc[0]) if "configuration_id" in weights else "unknown"
    result = result.reset_index()
    return result, {
        "station_rows_read": int(len(frame)),
        "regional_rows_written": int(len(result)),
        "timestamps_with_zero_weather_coverage": int(result["weather_coverage"].eq(0).sum()),
    }


def aggregate_regional_weather(weather_dir: Path, weights_path: Path, output_dir: Path, overwrite: bool = False) -> dict[str, Any]:
    """Aggregate each year/month station-weather partition independently."""
    partitions = weather_partitions(weather_dir)
    if not partitions:
        raise FileNotFoundError(f"No weather partitions found under {weather_dir}")
    if output_dir.exists():
        existing = tuple(output_dir.rglob("*"))
        if existing and not overwrite:
            raise FileExistsError(f"Output directory is not empty: {output_dir}; use --overwrite to replace it")
        if overwrite:
            shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    weights = pd.read_parquet(weights_path)
    report: dict[str, Any] = {"partitions_processed": 0, "station_rows_read": 0, "regional_rows_written": 0, "partitions": {}}

    for partition in partitions:
        files = sorted(partition.glob("*.parquet"))
        if not files:
            continue
        frame = pd.concat((pd.read_parquet(path) for path in files), ignore_index=True)
        regional, partition_report = aggregate_weather_frame(frame, weights)
        relative = partition.relative_to(weather_dir)
        destination = output_dir / relative / "regional.parquet"
        destination.parent.mkdir(parents=True, exist_ok=True)
        regional.to_parquet(destination, index=False, engine="pyarrow", compression="zstd")
        key = str(relative)
        report["partitions"][key] = partition_report
        report["partitions_processed"] += 1
        report["station_rows_read"] += partition_report["station_rows_read"]
        report["regional_rows_written"] += partition_report["regional_rows_written"]

    (output_dir / "quality_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weather", type=Path, default=DEFAULT_WEATHER)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = aggregate_regional_weather(args.weather, args.weights, args.output, overwrite=args.overwrite)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
