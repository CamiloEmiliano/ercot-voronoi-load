"""Normalize the 2015 ERCOT Fuel Mix workbook into interval-level Parquet."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
import json
import math
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import xlrd

if __package__:
    from .ercot_fuel_mix_qc import write_parquet_with_qc
else:
    from ercot_fuel_mix_qc import write_parquet_with_qc

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data/raw/ercot_fuel_mix/IntGenByFuel2015.xls"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/interim/fuel_mix/fuel_mix_2015.parquet"
WORKSHEETS = (
    "Jan15", "Feb15", "Mar15", "Apr15", "May15", "Jun15",
    "Jul15", "Aug15", "Sep15", "Oct15", "Nov15", "Dec15",
)
FUEL_ROW_PATTERN = re.compile(r"^(?P<date>\d{2}/\d{2}/\d{2})_(?P<fuel>.+)$")
CENTRAL = ZoneInfo("America/Chicago")
OUTPUT_COLUMNS = ("timestamp_local", "fuel_type", "generation_mwh")


def _parse_date_fuel(value: Any) -> tuple[date, str] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    match = FUEL_ROW_PATTERN.fullmatch(value.strip())
    if match is None:
        return None
    return datetime.strptime(match.group("date"), "%m/%d/%y").date(), match.group("fuel")


def _interval_minutes(header: Any) -> int:
    if not isinstance(header, (int, float)):
        raise ValueError(f"Expected Excel time fraction in interval header, got {header!r}")
    minutes = int(round(float(header) * 24 * 60))
    if minutes == 0:
        return 1440
    if minutes < 15 or minutes > 1440 or minutes % 15:
        raise ValueError(f"Invalid 15-minute interval header: {header!r}")
    return minutes


def _energy_value(value: Any) -> float:
    if value == "-":
        return 0.0
    if value is None or value == "":
        return math.nan
    return float(value)


def _serialize_local(local_end: datetime, fold: int | None = 0) -> str:
    if fold is None:
        return local_end.replace(tzinfo=timezone(timedelta(hours=-6))).isoformat(timespec="seconds")
    return local_end.replace(tzinfo=CENTRAL, fold=fold).isoformat(timespec="seconds")


def normalize_row(
    operating_date: date,
    fuel_type: str,
    interval_values: Sequence[Any],
    interval_headers: Sequence[int],
    dst_values: Sequence[Any] = (),
) -> list[dict[str, Any]]:
    """Map one 2015 daily fuel row to its 96 slots plus fall-back pass two."""
    spring_forward = operating_date == date(2015, 3, 8)
    fall_back = operating_date == date(2015, 11, 1)
    value_by_slot = {slot: interval_values[slot] for slot in range(96)}
    if spring_forward:
        value_by_slot = {slot: interval_values[slot] for slot in range(7)}
        value_by_slot.update({slot + 4: interval_values[slot] for slot in range(7, 92)})

    records: list[dict[str, Any]] = []
    for slot, endpoint_minutes in enumerate(interval_headers):
        local_end = datetime.combine(operating_date, time()) + timedelta(minutes=endpoint_minutes)
        if spring_forward and slot in range(7, 11):
            timestamp_local = _serialize_local(local_end, fold=None)
            energy = math.nan
        else:
            timestamp_local = _serialize_local(local_end, fold=0)
            energy = _energy_value(value_by_slot[slot])
        records.append({
            "timestamp_local": timestamp_local,
            "fuel_type": fuel_type,
            "generation_mwh": energy,
        })

    if fall_back:
        if len(dst_values) != 4:
            raise ValueError("2015-11-01 rows require four second-pass DST values")
        for dst_index in range(4):
            local_end = datetime.combine(operating_date, time()) + timedelta(minutes=60 + 15 * dst_index)
            records.append({
                "timestamp_local": _serialize_local(local_end, fold=1),
                "fuel_type": fuel_type,
                "generation_mwh": _energy_value(dst_values[dst_index]),
            })
    return records


def _normalize_sheet(args: tuple[str, str]) -> list[dict[str, Any]]:
    workbook_path, sheet_name = args
    workbook = xlrd.open_workbook(workbook_path, on_demand=True)
    try:
        sheet = workbook.sheet_by_name(sheet_name)
        headers = sheet.row_values(0)
        interval_headers = [_interval_minutes(value) for value in headers[2:98]]
        if interval_headers != list(range(15, 1441, 15)):
            raise ValueError(f"{sheet_name}: interval headers do not match quarter-hour endpoints")

        records: list[dict[str, Any]] = []
        for row_index in range(1, sheet.nrows):
            row = sheet.row_values(row_index)
            parsed = _parse_date_fuel(row[0] if row else None)
            if parsed is None:
                continue
            operating_date, fuel_type = parsed
            if len(row) < 98:
                raise ValueError(f"{sheet_name} row {row_index + 1}: fewer than 96 interval cells")
            interval_values = row[2:98]
            if operating_date == date(2015, 3, 8) and any(
                value not in ("", None) for value in interval_values[92:96]
            ):
                raise ValueError(f"{sheet_name} row {row_index + 1}: expected CQ:CT padding blanks")
            if operating_date == date(2015, 11, 1) and len(row) < 102:
                raise ValueError(f"{sheet_name} row {row_index + 1}: missing DST1:DST4 columns")
            dst_values = row[98:102] if operating_date == date(2015, 11, 1) else ()
            records.extend(
                normalize_row(operating_date, fuel_type, interval_values, interval_headers, dst_values)
            )
        return records
    finally:
        workbook.release_resources()


def _sort_records(frame: pd.DataFrame) -> pd.DataFrame:
    sort_utc = pd.to_datetime(frame["timestamp_local"], format="ISO8601", utc=True)
    spring_gap = frame["timestamp_local"].str.startswith("2015-03-08T02:") & frame["generation_mwh"].isna()
    # Structural placeholders have no real instant; keep them together before 03:00.
    sort_utc.loc[spring_gap] = pd.Timestamp("2015-03-08T07:59:59Z")
    return (
        frame.assign(_sort_utc=sort_utc)
        .sort_values(["_sort_utc", "timestamp_local", "fuel_type"], ignore_index=True)
        .drop(columns="_sort_utc")
    )


def normalize_2015(
    input_path: Path = DEFAULT_INPUT,
    output_path: Path = DEFAULT_OUTPUT,
    workers: int | None = None,
) -> pd.DataFrame:
    """Create one long-form 2015 Parquet artifact from the twelve monthly sheets."""
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    jobs = [(str(input_path), sheet_name) for sheet_name in WORKSHEETS]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        partitions = list(executor.map(_normalize_sheet, jobs))
    records = [record for partition in partitions for record in partition]
    frame = pd.DataFrame.from_records(records, columns=OUTPUT_COLUMNS)
    frame["generation_mwh"] = frame["generation_mwh"].astype("float64")
    frame = _sort_records(frame)
    frame.attrs["sampled_values_verified"] = write_parquet_with_qc(frame, 2015, input_path, output_path)
    return frame


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args()
    frame = normalize_2015(args.input, args.output, args.workers)
    print(json.dumps({
        "output": str(args.output),
        "rows": len(frame),
        "fuels": sorted(frame["fuel_type"].unique().tolist()),
        "generation_nan": int(frame["generation_mwh"].isna().sum()),
        "sampled_values_verified": frame.attrs["sampled_values_verified"],
    }, indent=2))


if __name__ == "__main__":
    main()