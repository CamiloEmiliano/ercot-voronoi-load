"""Normalize extracted ERCOT wide load profiles without guessing time semantics."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from source_contracts import (
        LOAD_INTERVAL_COLUMNS,
        LOAD_INTERVAL_MINUTES,
        LOAD_SOURCE_INTERVAL_COLUMNS,
        LOAD_SOURCE_UNIT,
        validate_load_profile_frame,
    )
except ModuleNotFoundError:
    from scripts.source_contracts import (
        LOAD_INTERVAL_COLUMNS,
        LOAD_INTERVAL_MINUTES,
        LOAD_SOURCE_INTERVAL_COLUMNS,
        LOAD_SOURCE_UNIT,
        validate_load_profile_frame,
    )

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data/interim/ercot_backcast"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/interim/load_profile_long"


def parquet_files(input_dir: Path) -> tuple[Path, ...]:
    return tuple(sorted(input_dir.rglob("*.parquet")))


def normalize_profile(frame: pd.DataFrame, source_path: str) -> pd.DataFrame:
    """Melt one wide profile file while preserving naive source timestamps."""
    validate_load_profile_frame(frame)
    identifier_columns = ["PType_WZ", "Date", "ADDTIME", "source_workbook", "source_sheet"]
    long_frame = frame.melt(
        id_vars=identifier_columns,
        value_vars=list(LOAD_INTERVAL_COLUMNS),
        var_name="interval_column",
        value_name="interval_value_kwh_source",
    )
    long_frame["interval_number"] = long_frame["interval_column"].str.removeprefix("int_kWh").astype("int64")
    long_frame["source_file"] = source_path
    long_frame["date_source_naive"] = pd.to_datetime(long_frame.pop("Date"), errors="raise")
    long_frame["addtime_source_naive"] = pd.to_datetime(long_frame.pop("ADDTIME"), errors="raise")
    long_frame["interval_value_kwh_source"] = pd.to_numeric(
        long_frame["interval_value_kwh_source"], errors="coerce"
    )
    return long_frame[
        [
            "PType_WZ",
            "date_source_naive",
            "addtime_source_naive",
            "interval_number",
            "interval_value_kwh_source",
            "source_workbook",
            "source_sheet",
            "source_file",
        ]
    ].sort_values(["date_source_naive", "PType_WZ", "interval_number"], ignore_index=True)


def normalize_load(input_dir: Path, output_dir: Path, overwrite: bool = False) -> dict[str, Any]:
    """Normalize all extracted Parquets and write one Parquet per source file."""
    paths = parquet_files(input_dir)
    if not paths:
        raise FileNotFoundError(f"No extracted ERCOT Parquet files found under {input_dir}")
    if output_dir.exists():
        existing_files = tuple(output_dir.rglob("*"))
        if existing_files and not overwrite:
            raise FileExistsError(f"Output directory is not empty: {output_dir}; use --overwrite to replace it")
        if overwrite:
            shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "files_processed": 0,
        "rows_read": 0,
        "rows_written": 0,
        "profile_rows_written": 0,
        "source_files": [],
        "timestamp_semantics": "preserved as naive source fields; UTC conversion intentionally deferred",
        "interval_duration_minutes": LOAD_INTERVAL_MINUTES,
        "unit_semantics": LOAD_SOURCE_UNIT,
        "load_mw_conversion": "deferred until canonical timestamp and target aggregation are defined",
    }
    for path in paths:
        frame = pd.read_parquet(path)
        normalized = normalize_profile(frame, str(path))
        destination = output_dir / path.relative_to(input_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        normalized.to_parquet(destination, index=False, engine="pyarrow", compression="zstd")
        report["files_processed"] += 1
        report["rows_read"] += len(frame)
        report["rows_written"] += len(normalized)
        report["profile_rows_written"] += len(frame)
        report["source_files"].append(str(path))

    report_path = output_dir / "quality_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = normalize_load(args.input, args.output, overwrite=args.overwrite)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
