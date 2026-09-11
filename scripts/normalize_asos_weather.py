"""Normalize raw ASOS CSV archives into a partitioned station-hour Parquet panel."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from source_contracts import (
        ASOS_NUMERIC_COLUMNS,
        ASOS_REQUIRED_COLUMNS,
        parse_asos_valid,
        validate_asos_columns,
    )
except ModuleNotFoundError:
    from scripts.source_contracts import (
        ASOS_NUMERIC_COLUMNS,
        ASOS_REQUIRED_COLUMNS,
        parse_asos_valid,
        validate_asos_columns,
    )

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_CODES = ("tx", "nm", "ok", "ar", "la")
CSV_CHUNK_SIZE = 100_000
MISSING_TOKENS = ("", "M", "NA", "N/A", "null", "NULL", "None")
OUTPUT_COLUMNS = (
    "station_id",
    "timestamp_utc",
    "source_state",
    "longitude_wgs84",
    "latitude_wgs84",
    "elevation_m",
    "tmpf",
    "dwpf",
    "relh",
    "feel",
    "skyc1",
    "skyl1",
    "p01i",
    "mslp",
    "alti",
    "vsby",
    "source_file",
)


def weather_files(raw_dir: Path, state_code: str) -> tuple[Path, ...]:
    return tuple(sorted((raw_dir / f"weather_{state_code}_asos").glob(f"{state_code}_asos_land_*.csv")))


def _missing_count(series: pd.Series) -> int:
    return int(series.isna().sum())


def normalize_chunk(chunk: pd.DataFrame, state_code: str, source_file: str) -> tuple[pd.DataFrame, dict[str, int]]:
    """Normalize one CSV chunk and enforce the station-hour duplicate rule."""
    validate_asos_columns(chunk.columns)
    quality = Counter()

    normalized = pd.DataFrame(index=chunk.index)
    normalized["station_id"] = chunk["station"].astype("string").str.strip()
    normalized["timestamp_utc"] = parse_asos_valid(chunk["valid"])
    normalized["source_state"] = state_code.upper()
    normalized["longitude_wgs84"] = pd.to_numeric(chunk["lon"], errors="coerce")
    normalized["latitude_wgs84"] = pd.to_numeric(chunk["lat"], errors="coerce")
    normalized["elevation_m"] = pd.to_numeric(chunk["elevation"], errors="coerce")

    for column in ASOS_NUMERIC_COLUMNS:
        output_column = {
            "lon": "longitude_wgs84",
            "lat": "latitude_wgs84",
            "elevation": "elevation_m",
        }.get(column, column)
        if output_column not in normalized:
            normalized[output_column] = pd.to_numeric(chunk[column], errors="coerce")
        quality[f"missing_{output_column}"] += _missing_count(normalized[output_column])

    for column in ("skyc1", "skyl1"):
        normalized[column] = chunk[column].astype("string").str.strip()
        normalized.loc[normalized[column].isin(MISSING_TOKENS), column] = pd.NA
        quality[f"missing_{column}"] += _missing_count(normalized[column])

    normalized["source_file"] = source_file
    normalized = normalized.loc[:, OUTPUT_COLUMNS]

    duplicate_keys = normalized.duplicated(["station_id", "timestamp_utc"], keep=False)
    duplicate_rows = normalized.loc[duplicate_keys]
    if not duplicate_rows.empty:
        quality["duplicate_station_hours"] += int(len(duplicate_rows))
        value_columns = [column for column in OUTPUT_COLUMNS if column not in {"source_file"}]
        conflicting = duplicate_rows.groupby(["station_id", "timestamp_utc"], dropna=False)[value_columns].nunique(
            dropna=False
        ).gt(1).any(axis=1)
        if conflicting.any():
            stations = conflicting[conflicting].index.tolist()[:5]
            raise ValueError(f"Conflicting duplicate ASOS station-hours: {stations}")
        normalized = normalized.drop_duplicates(["station_id", "timestamp_utc"], keep="first")
        quality["identical_duplicate_station_hours_collapsed"] += int(len(duplicate_rows) - len(normalized.loc[duplicate_keys]))

    return normalized, dict(quality)


def _write_partitions(frame: pd.DataFrame, output_dir: Path, state_code: str, source_stem: str, chunk_number: int) -> int:
    """Write one normalized chunk into UTC calendar-month Parquet partitions."""
    written = 0
    periods = frame["timestamp_utc"].dt.tz_convert("UTC").dt.strftime("%Y-%m")
    for period, period_frame in frame.groupby(periods, sort=True):
        year, month = period.split("-")
        destination_dir = output_dir / f"year={year}" / f"month={month}"
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"part-{state_code}-{source_stem}-{chunk_number:06d}.parquet"
        period_frame.to_parquet(destination, index=False, engine="pyarrow", compression="zstd")
        written += len(period_frame)
    return written


def _deduplicate_across_chunks(frame: pd.DataFrame, connection: sqlite3.Connection) -> tuple[pd.DataFrame, dict[str, int]]:
    """Drop identical keys already seen and reject conflicting observations."""
    value_columns = [column for column in OUTPUT_COLUMNS if column not in {"source_file", "station_id", "timestamp_utc"}]
    tracked = frame.loc[:, ["station_id", "timestamp_utc", *value_columns]].copy()
    tracked["timestamp_key"] = tracked["timestamp_utc"].map(pd.Timestamp.isoformat)
    tracked["digest"] = pd.util.hash_pandas_object(tracked[value_columns], index=False).map(lambda value: f"{value:016x}")

    connection.execute("DROP TABLE IF EXISTS current_rows")
    connection.execute(
        "CREATE TEMP TABLE current_rows (station_id TEXT, timestamp_key TEXT, digest TEXT, "
        "PRIMARY KEY (station_id, timestamp_key))"
    )
    connection.executemany(
        "INSERT INTO current_rows (station_id, timestamp_key, digest) VALUES (?, ?, ?)",
        tracked[["station_id", "timestamp_key", "digest"]].itertuples(index=False, name=None),
    )
    conflicts = connection.execute(
        "SELECT c.station_id, c.timestamp_key FROM current_rows c "
        "JOIN seen_rows s USING (station_id, timestamp_key) WHERE c.digest != s.digest LIMIT 5"
    ).fetchall()
    if conflicts:
        raise ValueError(f"Conflicting duplicate ASOS station-hours: {conflicts}")

    new_keys = pd.read_sql_query(
        "SELECT c.station_id, c.timestamp_key FROM current_rows c "
        "LEFT JOIN seen_rows s USING (station_id, timestamp_key) WHERE s.station_id IS NULL",
        connection,
    )
    connection.execute(
        "INSERT INTO seen_rows (station_id, timestamp_key, digest) "
        "SELECT c.station_id, c.timestamp_key, c.digest FROM current_rows c "
        "LEFT JOIN seen_rows s USING (station_id, timestamp_key) WHERE s.station_id IS NULL"
    )
    connection.commit()
    output = frame.copy()
    output["timestamp_key"] = output["timestamp_utc"].map(pd.Timestamp.isoformat)
    output = output.merge(new_keys, on=["station_id", "timestamp_key"], how="inner")
    output = output.drop(columns=["timestamp_key"])
    return output, {
        "duplicate_station_hours_across_chunks": int(len(frame) - len(new_keys)),
    }


def normalize_weather(
    raw_dir: Path,
    output_dir: Path,
    state_codes: tuple[str, ...] = STATE_CODES,
    chunk_size: int = CSV_CHUNK_SIZE,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Normalize all state archives and return a JSON-serializable quality report."""
    if output_dir.exists():
        existing_files = tuple(output_dir.rglob("*"))
        if existing_files and not overwrite:
            raise FileExistsError(f"Output directory is not empty: {output_dir}; use --overwrite to replace it")
        if overwrite:
            shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    file_descriptor, database_name = tempfile.mkstemp(prefix="asos-duplicates-", suffix=".sqlite3")
    os.close(file_descriptor)
    connection = sqlite3.connect(database_name)
    connection.execute(
        "CREATE TABLE seen_rows (station_id TEXT, timestamp_key TEXT, digest TEXT, "
        "PRIMARY KEY (station_id, timestamp_key))"
    )

    report: dict[str, Any] = {
        "states": {},
        "row_count_read": 0,
        "row_count_written": 0,
        "files_processed": 0,
        "station_count": 0,
        "timestamp_min_utc": None,
        "timestamp_max_utc": None,
        "quality": Counter(),
    }
    station_ids: set[str] = set()

    try:
        for state_code in state_codes:
            paths = weather_files(raw_dir, state_code)
            if not paths:
                raise FileNotFoundError(f"No ASOS CSV files found for {state_code.upper()}")
            state_report = {"files_processed": 0, "row_count_read": 0, "row_count_written": 0}
            for path in paths:
                for chunk_number, chunk in enumerate(
                    pd.read_csv(
                        path,
                        usecols=ASOS_REQUIRED_COLUMNS,
                        chunksize=chunk_size,
                        na_values=MISSING_TOKENS,
                        keep_default_na=True,
                        dtype={"station": "string", "skyc1": "string", "skyl1": "string"},
                    )
                ):
                    normalized, quality = normalize_chunk(chunk, state_code, path.name)
                    normalized, cross_chunk_quality = _deduplicate_across_chunks(normalized, connection)
                    written = _write_partitions(normalized, output_dir, state_code, path.stem, chunk_number)
                    report["row_count_read"] += len(chunk)
                    report["row_count_written"] += written
                    state_report["row_count_read"] += len(chunk)
                    state_report["row_count_written"] += written
                    station_ids.update(normalized["station_id"].dropna().astype(str))
                    minimum = normalized["timestamp_utc"].min()
                    maximum = normalized["timestamp_utc"].max()
                    report["timestamp_min_utc"] = min(report["timestamp_min_utc"], minimum.isoformat()) if report["timestamp_min_utc"] else minimum.isoformat()
                    report["timestamp_max_utc"] = max(report["timestamp_max_utc"], maximum.isoformat()) if report["timestamp_max_utc"] else maximum.isoformat()
                    report["quality"].update(quality)
                    report["quality"].update(cross_chunk_quality)
                state_report["files_processed"] += 1
                report["files_processed"] += 1
            report["states"][state_code] = state_report
    finally:
        connection.close()
        Path(database_name).unlink(missing_ok=True)

    report["station_count"] = len(station_ids)
    report["quality"] = dict(report["quality"])
    report_path = output_dir / "quality_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=PROJECT_ROOT / "data/raw")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "data/interim/weather_station_hourly")
    parser.add_argument("--chunk-size", type=int, default=CSV_CHUNK_SIZE)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = normalize_weather(args.raw_dir, args.output, chunk_size=args.chunk_size, overwrite=args.overwrite)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
