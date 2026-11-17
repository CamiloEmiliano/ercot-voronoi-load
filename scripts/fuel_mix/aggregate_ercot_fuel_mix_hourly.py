"""Aggregate 15-minute ERCOT fuel-mix Parquets into hourly UTC generation totals."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import random
import re
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data/interim/fuel_mix"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data/interim/fuel_mix_hourly"
CENTRAL = ZoneInfo("America/Chicago")
OUTPUT_COLUMNS = ("timestamp_utc", "fuel_type", "generation_mwh", "observed_intervals")


def _is_spring_forward_placeholder(timestamp_local: str) -> bool:
    """Return True if an ISO-8601 local timestamp is a nonexistent spring-forward placeholder.

    Spring-forward normalizers serialize the skipped 02:15..03:00 CST (-06:00) window
    as structural NaN rows; real post-jump intervals carry -05:00.
    """
    if not timestamp_local.endswith("-06:00"):
        return False
    dt = datetime.fromisoformat(timestamp_local)
    naive = dt.replace(tzinfo=None)
    # In America/Chicago, 02:00..02:59 on spring-forward day does not exist; 03:00 exists only as -05:00.
    if naive.month != 3:
        return False
    prev = (naive - timedelta(hours=1)).replace(tzinfo=CENTRAL)
    curr = naive.replace(tzinfo=CENTRAL)
    # At 02:15..02:45 on spring-forward, roundtrip shifts to 03:15..03:45; at 03:00, local offset is -05:00.
    if 2 <= naive.hour <= 3:
        roundtrip = curr.astimezone(timezone.utc).astimezone(CENTRAL).replace(tzinfo=None)
        if roundtrip != naive:
            return True
        if naive.hour == 3 and naive.minute == 0 and curr.utcoffset() == timedelta(hours=-5) and prev.utcoffset() != curr.utcoffset():
            return True
    return False


def find_fuel_mix_files(input_dir: Path) -> dict[int, Path]:
    """Locate annual 15-minute fuel_mix_YYYY.parquet files."""
    files: dict[int, Path] = {}
    for path in sorted(input_dir.glob("fuel_mix_*.parquet")):
        match = re.fullmatch(r"fuel_mix_(201[4-9]|202[0-5])\.parquet", path.name)
        if match is None:
            continue
        files[int(match.group(1))] = path
    return files


def aggregate_fuel_mix_frame(frame: pd.DataFrame, year: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Aggregate one annual 15-minute fuel-mix frame into hourly UTC totals."""
    required = {"timestamp_local", "fuel_type", "generation_mwh"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Fuel-mix frame missing columns: {', '.join(missing)}")
    if frame.empty:
        raise ValueError(f"{year}: fuel-mix frame is empty")

    work = frame.copy()
    work["generation_mwh"] = pd.to_numeric(work["generation_mwh"], errors="raise").astype("float64")
    is_placeholder = work["generation_mwh"].isna() & work["timestamp_local"].map(_is_spring_forward_placeholder)
    placeholder_count = int(is_placeholder.sum())
    work = work.loc[~is_placeholder].copy()

    work["interval_utc"] = pd.to_datetime(work["timestamp_local"], format="ISO8601", utc=True)
    work["timestamp_utc"] = work["interval_utc"].dt.ceil("h")

    if work.duplicated(["interval_utc", "fuel_type"]).any():
        raise AssertionError(f"{year}: duplicate (interval_utc, fuel_type) after removing spring placeholders")

    grouped = work.groupby(["timestamp_utc", "fuel_type"], sort=True)["generation_mwh"]
    hourly = pd.DataFrame({
        "observed_intervals": grouped.count().astype("int64"),
        "generation_mwh": grouped.sum(min_count=4).astype("float64"),
    }).reset_index()

    if (hourly["observed_intervals"] > 4).any():
        raise AssertionError(f"{year}: found hour with >4 15-minute intervals")

    hourly = hourly.sort_values(["timestamp_utc", "fuel_type"], ignore_index=True).loc[:, OUTPUT_COLUMNS]
    expected_start = pd.Timestamp(f"{year}-01-01 07:00:00+00:00")
    expected_end = pd.Timestamp(f"{year + 1}-01-01 06:00:00+00:00")
    if hourly["timestamp_utc"].min() != expected_start or hourly["timestamp_utc"].max() != expected_end:
        raise AssertionError(
            f"{year}: unexpected UTC span {hourly['timestamp_utc'].min()} .. {hourly['timestamp_utc'].max()}"
        )

    unique_hours = pd.Series(hourly["timestamp_utc"].unique()).sort_values(ignore_index=True)
    expected_hours = 8784 if year in (2016, 2020, 2024) else 8760
    if len(unique_hours) != expected_hours:
        raise AssertionError(f"{year}: expected {expected_hours} unique UTC hours, got {len(unique_hours)}")
    diffs = unique_hours.diff().dropna()
    if not (diffs == pd.Timedelta(hours=1)).all():
        raise AssertionError(f"{year}: non-hourly UTC gap(s) detected")

    report = {
        "year": year,
        "input_rows": int(len(frame)),
        "spring_placeholders_excluded": placeholder_count,
        "output_rows": int(len(hourly)),
        "unique_hours": int(len(unique_hours)),
        "fuels": sorted(hourly["fuel_type"].unique().tolist()),
        "incomplete_hours_rows": int((hourly["observed_intervals"] < 4).sum()),
        "nan_generation_rows": int(hourly["generation_mwh"].isna().sum()),
        "negative_generation_rows": int((hourly["generation_mwh"] < 0).sum()),
        "timestamp_min_utc": hourly["timestamp_utc"].min().isoformat(),
        "timestamp_max_utc": hourly["timestamp_utc"].max().isoformat(),
    }
    return hourly, report


def audit_hourly_fuel_mix_parquet(
    source_15m: pd.DataFrame,
    parquet_path: Path,
    year: int,
    *,
    sample_per_month: int = 5,
    seed: int | None = None,
) -> int:
    """Reopen persisted hourly Parquet and verify exact parity against 15-minute source."""
    persisted = pd.read_parquet(parquet_path)
    if persisted.columns.tolist() != list(OUTPUT_COLUMNS):
        raise AssertionError(f"{parquet_path}: unexpected columns {persisted.columns.tolist()!r}")
    if persisted.duplicated(["timestamp_utc", "fuel_type"]).any():
        raise AssertionError(f"{parquet_path}: duplicate (timestamp_utc, fuel_type) keys")

    src = source_15m.copy()
    src = src.loc[~(src["generation_mwh"].isna() & src["timestamp_local"].map(_is_spring_forward_placeholder))].copy()
    src["timestamp_utc"] = pd.to_datetime(src["timestamp_local"], format="ISO8601", utc=True).dt.ceil("h")

    months = persisted["timestamp_utc"].dt.month
    rng = random.Random(year if seed is None else seed)
    selected_indices: set[int] = set()
    for month in range(1, 13):
        month_idx = persisted.index[months == month].tolist()
        if len(month_idx) < sample_per_month:
            raise AssertionError(f"{year} month {month:02d}: fewer than {sample_per_month} rows")
        selected_indices.update(rng.sample(month_idx, sample_per_month))

    # Always include incomplete hours (e.g., 2014-11-02 06:00/07:00 UTC) if any exist.
    incomplete_idx = persisted.index[persisted["observed_intervals"] < 4].tolist()
    selected_indices.update(incomplete_idx)

    for idx in sorted(selected_indices):
        row = persisted.iloc[idx]
        sub = src.loc[(src["timestamp_utc"] == row["timestamp_utc"]) & (src["fuel_type"] == row["fuel_type"])]
        expected_count = int(sub["generation_mwh"].notna().sum())
        if int(row["observed_intervals"]) != expected_count:
            raise AssertionError(
                f"{parquet_path} {row['timestamp_utc']} {row['fuel_type']}: "
                f"observed_intervals={row['observed_intervals']} != {expected_count}"
            )
        if expected_count < 4:
            if not pd.isna(row["generation_mwh"]):
                raise AssertionError(
                    f"{parquet_path} incomplete hour {row['timestamp_utc']} {row['fuel_type']} "
                    f"must be NaN, got {row['generation_mwh']!r}"
                )
            continue
        expected_sum = float(sub["generation_mwh"].sum())
        actual_sum = float(row["generation_mwh"])
        if not math.isfinite(actual_sum) or not math.isclose(actual_sum, expected_sum, rel_tol=1e-12, abs_tol=1e-9):
            raise AssertionError(
                f"{parquet_path} {row['timestamp_utc']} {row['fuel_type']}: "
                f"source_sum={expected_sum!r}, persisted={actual_sum!r}"
            )
    return len(selected_indices)


def write_parquet_with_qc(source_15m: pd.DataFrame, hourly: pd.DataFrame, parquet_path: Path, year: int) -> int:
    """Write temporary Parquet, verify persisted values against 15-min source, and publish."""
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{parquet_path.stem}.",
        suffix=".parquet",
        dir=parquet_path.parent,
        delete=False,
    ) as tmp:
        tmp_path = Path(tmp.name)
    try:
        hourly.to_parquet(tmp_path, index=False, engine="pyarrow", compression="zstd")
        checked = audit_hourly_fuel_mix_parquet(source_15m, tmp_path, year)
        os.replace(tmp_path, parquet_path)
        return checked
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def aggregate_all_years(
    input_dir: Path,
    output_dir: Path,
    *,
    start_year: int = 2014,
    end_year: int = 2025,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Aggregate all 15-minute fuel-mix Parquets in [start_year, end_year] to hourly UTC."""
    source_files = find_fuel_mix_files(input_dir)
    expected_years = list(range(start_year, end_year + 1))
    missing = [y for y in expected_years if y not in source_files]
    if missing:
        raise FileNotFoundError(f"Missing fuel_mix_YYYY.parquet in {input_dir} for years: {missing}")

    output_dir.mkdir(parents=True, exist_ok=True)
    reports: dict[str, Any] = {}
    for year in expected_years:
        source_path = source_files[year]
        destination = output_dir / f"year={year}" / f"fuel_mix_hourly_{year}.parquet"
        if destination.exists() and not overwrite:
            print(f"{year}: output already exists; skipping {destination} (use --overwrite to rebuild).")
            continue
        source_15m = pd.read_parquet(source_path)
        hourly, year_report = aggregate_fuel_mix_frame(source_15m, year)
        checked = write_parquet_with_qc(source_15m, hourly, destination, year)
        year_report["qc_rows_verified"] = checked
        year_report["output_file"] = str(destination.relative_to(output_dir))
        reports[str(year)] = year_report
        print(
            f"{year}: wrote {year_report['output_rows']} rows ({year_report['unique_hours']} hours) to {destination}; "
            f"QC verified {checked} rows."
        )

    summary = {"years_processed": sorted(reports), "years": reports}
    (output_dir / "quality_report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start-year", type=int, default=2014)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    aggregate_all_years(
        args.input_dir,
        args.output_dir,
        start_year=args.start_year,
        end_year=args.end_year,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
