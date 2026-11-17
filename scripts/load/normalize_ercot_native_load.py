"""Normalize ERCOT Native Load hourly archives (2014-2025) into canonical UTC Parquet."""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
import io
import json
import math
import os
from pathlib import Path
import random
import re
import tempfile
from typing import Any
from zipfile import ZipFile
from zoneinfo import ZoneInfo

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data/raw/ercot_hourly_load"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data/interim/load_hourly"
CENTRAL = ZoneInfo("America/Chicago")

ZONE_COLUMN_MAP = {
    "COAST": "coast_mw",
    "EAST": "east_mw",
    "FAR_WEST": "fwest_mw",
    "FWEST": "fwest_mw",
    "NORTH": "north_mw",
    "NORTH_C": "ncent_mw",
    "NCENT": "ncent_mw",
    "SOUTHERN": "south_mw",
    "SOUTH": "south_mw",
    "SOUTH_C": "scent_mw",
    "SCENT": "scent_mw",
    "WEST": "west_mw",
    "ERCOT": "load_mw",
}
VALUE_COLUMNS = (
    "load_mw",
    "coast_mw",
    "east_mw",
    "fwest_mw",
    "north_mw",
    "ncent_mw",
    "south_mw",
    "scent_mw",
    "west_mw",
)
OUTPUT_COLUMNS = ("timestamp_utc", "timestamp_local", *VALUE_COLUMNS, "dst_flag", "source_file")

SPRING_FORWARD_DATES = {
    2014: date(2014, 3, 9),
    2015: date(2015, 3, 8),
    2016: date(2016, 3, 13),
    2017: date(2017, 3, 12),
    2018: date(2018, 3, 11),
    2019: date(2019, 3, 10),
    2020: date(2020, 3, 8),
    2021: date(2021, 3, 14),
    2022: date(2022, 3, 13),
    2023: date(2023, 3, 12),
    2024: date(2024, 3, 10),
    2025: date(2025, 3, 9),
}
FALL_BACK_DATES = {
    2014: date(2014, 11, 2),
    2015: date(2015, 11, 1),
    2016: date(2016, 11, 6),
    2017: date(2017, 11, 5),
    2018: date(2018, 11, 4),
    2019: date(2019, 11, 3),
    2020: date(2020, 11, 1),
    2021: date(2021, 11, 7),
    2022: date(2022, 11, 6),
    2023: date(2023, 11, 5),
    2024: date(2024, 11, 3),
    2025: date(2025, 11, 2),
}


def find_source_files(input_dir: Path) -> dict[int, Path]:
    """Locate one Native_Load file per year 2014-2025."""
    files: dict[int, Path] = {}
    for path in sorted(input_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in {".xls", ".xlsx", ".zip"}:
            continue
        match = re.search(r"(201[4-9]|202[0-5])", path.name)
        if match is None:
            continue
        year = int(match.group(1))
        if year in files:
            raise ValueError(f"Duplicate source file for year {year}: {files[year]} and {path}")
        files[year] = path
    return files


def read_raw_native_load(path: Path) -> pd.DataFrame:
    """Read raw Native_Load spreadsheet from .xls or .zip/.xlsx."""
    if path.suffix.lower() == ".zip":
        with ZipFile(path) as archive:
            excel_members = [name for name in archive.namelist() if name.lower().endswith((".xlsx", ".xls"))]
            if len(excel_members) != 1:
                raise ValueError(f"Expected 1 Excel member in {path}, found {excel_members}")
            with archive.open(excel_members[0]) as handle:
                return pd.read_excel(io.BytesIO(handle.read()))
    return pd.read_excel(path)


def _parse_naive_hour_ending(raw_value: Any) -> tuple[datetime, bool]:
    """Parse Hour_End / HourEnding / Hour Ending into naive local hour-end and explicit DST flag."""
    if isinstance(raw_value, (datetime, pd.Timestamp)):
        ts = pd.Timestamp(raw_value).round("min")
        return ts.to_pydatetime().replace(tzinfo=None), False
    if isinstance(raw_value, str):
        text = raw_value.strip()
        explicit_dst = text.endswith(" DST")
        if explicit_dst:
            text = text[:-4].strip()
        match = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})\s+(\d{2}):(\d{2})", text)
        if match is None:
            raise ValueError(f"Unrecognized Hour Ending format: {raw_value!r}")
        month, day, year_num, hour, minute = (int(part) for part in match.groups())
        if minute != 0:
            raise ValueError(f"Expected whole-hour timestamp, got {raw_value!r}")
        base = datetime(year_num, month, day, 0, 0)
        return base + timedelta(hours=hour), explicit_dst
    raise ValueError(f"Unsupported timestamp cell value: {raw_value!r}")


def _localize_hour_ending(naive_end: datetime, explicit_dst: bool, seen_fall_two_am: set[date]) -> tuple[datetime, bool]:
    """Resolve naive CPT Hour Ending to offset-aware local datetime and DST-pass boolean.

    On normal hours, ``(naive_end - 1h)`` localized in ``America/Chicago`` plus 1 UTC hour
    places both ``02:00`` (1st pass, ``-05:00``) and ``02:00 DST`` (2nd pass, ``-06:00``) on their
    true UTC instants. On 2014-2016 spring-forward, ERCOT omitted ``02:00`` and labeled ``08:00 UTC``
    as ``03:00`` CDT directly, so ``naive_end`` at ``03:00`` with nonexistent ``02:00`` hour-start
    is localized directly as CDT ``-05:00``.
    """
    naive_start = naive_end - timedelta(hours=1)

    # Handle 2014-2016 spring-forward label (03:00 CDT directly, where start 02:00 is nonexistent).
    if naive_end.hour == 3:
        probe_0 = naive_start.replace(tzinfo=CENTRAL, fold=0)
        probe_1 = naive_start.replace(tzinfo=CENTRAL, fold=1)
        if probe_0.utcoffset() != probe_1.utcoffset() or (
            probe_0.utcoffset() == timedelta(hours=-5)
            and (naive_start - timedelta(hours=1)).replace(tzinfo=CENTRAL).utcoffset() == timedelta(hours=-6)
            and naive_end.month == 3
        ):
            # Verify whether naive_start is actually in the nonexistent spring-forward gap.
            roundtrip = probe_0.astimezone(timezone.utc).astimezone(CENTRAL).replace(tzinfo=None)
            if roundtrip != naive_start:
                local_dt = naive_end.replace(tzinfo=CENTRAL)
                return local_dt, False

    # Identify repeated 02:00 on fall-back day (hour_start == 01:00 is ambiguous in America/Chicago).
    start_fold0 = naive_start.replace(tzinfo=CENTRAL, fold=0).astimezone(timezone.utc)
    start_fold1 = naive_start.replace(tzinfo=CENTRAL, fold=1).astimezone(timezone.utc)
    is_fall_second_hour = start_fold0 != start_fold1

    if is_fall_second_hour:
        if explicit_dst or naive_end.date() in seen_fall_two_am:
            start_utc = start_fold1
            dst_flag = True
        else:
            seen_fall_two_am.add(naive_end.date())
            start_utc = start_fold0
            dst_flag = False
    else:
        if explicit_dst:
            raise ValueError(f"Unexpected DST marker on non-ambiguous timestamp: {naive_end!r}")
        start_utc = start_fold0
        dst_flag = False

    end_utc = start_utc + timedelta(hours=1)
    offset = start_utc.astimezone(CENTRAL).utcoffset()
    if offset is None:
        raise RuntimeError("Missing UTC offset for America/Chicago")
    local_dt = end_utc.astimezone(timezone(offset))
    return local_dt, dst_flag


def normalize_native_load_frame(raw: pd.DataFrame, year: int, source_file: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Normalize one annual Native_Load dataframe into canonical UTC hourly records."""
    if raw.empty:
        raise ValueError(f"{source_file}: empty Native_Load dataframe")

    time_col = raw.columns[0]
    if str(time_col).strip() not in {"Hour_End", "Hour Ending", "HourEnding"}:
        raise ValueError(f"{source_file}: unexpected timestamp column {time_col!r}")

    renamed: dict[str, str] = {}
    for col in raw.columns[1:]:
        clean = str(col).strip()
        if clean not in ZONE_COLUMN_MAP:
            raise ValueError(f"{source_file}: unexpected load column {col!r}")
        renamed[col] = ZONE_COLUMN_MAP[clean]
    if set(renamed.values()) != set(VALUE_COLUMNS):
        raise ValueError(f"{source_file}: incomplete zone columns; got {sorted(renamed.values())}")

    expected_rows = 8784 if year in (2016, 2020, 2024) else 8760
    if len(raw) != expected_rows:
        raise AssertionError(f"{source_file}: expected {expected_rows} rows for {year}, got {len(raw)}")

    seen_fall_two_am: set[date] = set()
    local_dts: list[datetime] = []
    dst_flags: list[bool] = []
    for raw_ts in raw[time_col]:
        naive_end, explicit_dst = _parse_naive_hour_ending(raw_ts)
        local_dt, dst_flag = _localize_hour_ending(naive_end, explicit_dst, seen_fall_two_am)
        local_dts.append(local_dt)
        dst_flags.append(dst_flag)

    frame = pd.DataFrame({
        "timestamp_utc": pd.to_datetime([dt.astimezone(timezone.utc) for dt in local_dts], utc=True),
        "timestamp_local": [dt.isoformat(timespec="seconds") for dt in local_dts],
    })
    for src_col, dst_col in renamed.items():
        frame[dst_col] = pd.to_numeric(raw[src_col], errors="raise").astype("float64")
    frame["dst_flag"] = pd.Series(dst_flags, dtype="bool")
    frame["source_file"] = source_file
    frame = frame.loc[:, OUTPUT_COLUMNS]

    # Assert strict UTC contiguity and DST day cardinalities.
    if frame["timestamp_utc"].duplicated().any():
        dups = frame.loc[frame["timestamp_utc"].duplicated(keep=False), ["timestamp_local", "timestamp_utc"]]
        raise AssertionError(f"{source_file}: duplicate timestamp_utc values:\n{dups.head()}")
    if not frame["timestamp_utc"].is_monotonic_increasing:
        raise AssertionError(f"{source_file}: timestamp_utc is not strictly increasing")
    diffs = frame["timestamp_utc"].diff().dropna()
    if not (diffs == pd.Timedelta(hours=1)).all():
        bad = diffs[diffs != pd.Timedelta(hours=1)]
        raise AssertionError(f"{source_file}: non-hourly UTC step(s): {bad.to_dict()}")

    expected_start = pd.Timestamp(f"{year}-01-01 07:00:00+00:00")
    expected_end = pd.Timestamp(f"{year + 1}-01-01 06:00:00+00:00")
    if frame["timestamp_utc"].iloc[0] != expected_start or frame["timestamp_utc"].iloc[-1] != expected_end:
        raise AssertionError(
            f"{source_file}: unexpected UTC span {frame['timestamp_utc'].iloc[0]} .. {frame['timestamp_utc'].iloc[-1]}"
        )

    local_dates = pd.Series([dt.date() for dt in local_dts])
    # Note: Hour Ending 00:00 of next day belongs to the prior operating day in local CPT,
    # except that local calendar-date grouping still counts spring/fall day timestamps directly:
    # on spring-forward (23 rows on calendar date), on fall-back (25 rows on calendar date).
    spring_date = SPRING_FORWARD_DATES[year]
    fall_date = FALL_BACK_DATES[year]
    spring_count = int((local_dates == spring_date).sum())
    fall_count = int((local_dates == fall_date).sum())
    if spring_count != 23:
        raise AssertionError(f"{source_file}: expected 23 local-date rows on spring-forward {spring_date}, got {spring_count}")
    if fall_count != 25:
        raise AssertionError(f"{source_file}: expected 25 local-date rows on fall-back {fall_date}, got {fall_count}")

    zone_cols = [c for c in VALUE_COLUMNS if c != "load_mw"]
    zone_diff = (frame[zone_cols].sum(axis=1) - frame["load_mw"]).abs()
    report = {
        "year": year,
        "source_file": source_file,
        "rows": int(len(frame)),
        "timestamp_min_utc": frame["timestamp_utc"].min().isoformat(),
        "timestamp_max_utc": frame["timestamp_utc"].max().isoformat(),
        "nan_load_rows": int(frame["load_mw"].isna().sum()),
        "nan_total_cells": int(frame[list(VALUE_COLUMNS)].isna().sum().sum()),
        "max_zone_sum_diff_mw": float(zone_diff.max(skipna=True)),
        "spring_forward_date": spring_date.isoformat(),
        "spring_forward_rows": spring_count,
        "fall_back_date": fall_date.isoformat(),
        "fall_back_rows": fall_count,
    }
    return frame, report


def audit_native_load_parquet(
    raw: pd.DataFrame,
    normalized: pd.DataFrame,
    parquet_path: Path,
    year: int,
    *,
    sample_per_month: int = 5,
    seed: int | None = None,
) -> int:
    """Reopen persisted Parquet and verify exact parity on sampled + DST rows."""
    persisted = pd.read_parquet(parquet_path)
    if persisted.columns.tolist() != list(OUTPUT_COLUMNS):
        raise AssertionError(f"{parquet_path}: unexpected columns {persisted.columns.tolist()!r}")
    if len(persisted) != len(normalized):
        raise AssertionError(f"{parquet_path}: row count mismatch {len(persisted)} != {len(normalized)}")

    months = persisted["timestamp_utc"].dt.month
    rng = random.Random(year if seed is None else seed)
    selected_indices: set[int] = set()
    for month in range(1, 13):
        month_idx = persisted.index[months == month].tolist()
        if len(month_idx) < sample_per_month:
            raise AssertionError(f"{year} month {month:02d}: fewer than {sample_per_month} rows")
        selected_indices.update(rng.sample(month_idx, sample_per_month))

    # Always include spring-forward and fall-back transition rows.
    spring_str = SPRING_FORWARD_DATES[year].isoformat()
    fall_str = FALL_BACK_DATES[year].isoformat()
    dst_indices = persisted.index[
        persisted["timestamp_local"].str.startswith(spring_str)
        | persisted["timestamp_local"].str.startswith(fall_str)
    ].tolist()
    selected_indices.update(dst_indices)

    zone_cols = [c for c in raw.columns[1:]]
    raw_renamed = {col: ZONE_COLUMN_MAP[str(col).strip()] for col in zone_cols}
    for idx in sorted(selected_indices):
        for raw_col, canon_col in raw_renamed.items():
            expected = float(raw.iloc[idx][raw_col]) if pd.notna(raw.iloc[idx][raw_col]) else math.nan
            actual = float(persisted.iloc[idx][canon_col]) if pd.notna(persisted.iloc[idx][canon_col]) else math.nan
            if math.isnan(expected) and math.isnan(actual):
                continue
            if expected != actual:
                raise AssertionError(
                    f"{parquet_path} row {idx} ({persisted.iloc[idx]['timestamp_local']}) {canon_col}: "
                    f"source={expected!r}, persisted={actual!r}"
                )
    return len(selected_indices)


def write_parquet_with_qc(
    raw: pd.DataFrame,
    normalized: pd.DataFrame,
    parquet_path: Path,
    year: int,
) -> int:
    """Write temporary Parquet, run persisted QC audit, and atomically publish."""
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{parquet_path.stem}.",
        suffix=".parquet",
        dir=parquet_path.parent,
        delete=False,
    ) as tmp:
        tmp_path = Path(tmp.name)
    try:
        normalized.to_parquet(tmp_path, index=False, engine="pyarrow", compression="zstd")
        checked = audit_native_load_parquet(raw, normalized, tmp_path, year)
        os.replace(tmp_path, parquet_path)
        return checked
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def normalize_all_years(
    input_dir: Path,
    output_dir: Path,
    *,
    start_year: int = 2014,
    end_year: int = 2025,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Normalize all Native_Load files in [start_year, end_year] into output_dir."""
    source_files = find_source_files(input_dir)
    expected_years = list(range(start_year, end_year + 1))
    missing = [y for y in expected_years if y not in source_files]
    if missing:
        raise FileNotFoundError(f"Missing Native_Load file(s) in {input_dir} for years: {missing}")

    output_dir.mkdir(parents=True, exist_ok=True)
    reports: dict[str, Any] = {}
    for year in expected_years:
        source_path = source_files[year]
        destination = output_dir / f"year={year}" / f"load_hourly_{year}.parquet"
        if destination.exists() and not overwrite:
            print(f"{year}: output already exists; skipping {destination} (use --overwrite to rebuild).")
            continue
        raw = read_raw_native_load(source_path)
        normalized, year_report = normalize_native_load_frame(raw, year, source_path.name)
        checked = write_parquet_with_qc(raw, normalized, destination, year)
        year_report["qc_rows_verified"] = checked
        year_report["output_file"] = str(destination.relative_to(output_dir))
        reports[str(year)] = year_report
        print(
            f"{year}: wrote {year_report['rows']} rows to {destination}; "
            f"QC verified {checked} rows (source NaNs={year_report['nan_load_rows']})."
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
    normalize_all_years(
        args.input_dir,
        args.output_dir,
        start_year=args.start_year,
        end_year=args.end_year,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
