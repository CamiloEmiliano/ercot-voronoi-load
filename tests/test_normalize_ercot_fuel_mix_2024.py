from datetime import date, datetime

import numpy as np
import openpyxl
import pandas as pd

from scripts.normalize_ercot_fuel_mix_2024 import (
    DEFAULT_INPUT,
    FUEL_TYPES,
    _interval_minutes,
    _sort_records,
    normalize_2024,
    normalize_row,
)


def _headers():
    return [15 * (index + 1) for index in range(96)]


def test_2024_includes_wsl_as_a_tenth_fuel():
    assert "WSL" in FUEL_TYPES
    assert len(FUEL_TYPES) == 10


def test_spring_forward_preserves_2024_0215_through_0300_gap():
    values = [float(index) for index in range(96)]
    values[8:12] = [None] * 4
    records = normalize_row(date(2024, 3, 10), "WSL", values, _headers())
    assert [records[index]["timestamp_local"] for index in range(7, 13)] == [
        "2024-03-10T02:00:00-06:00", "2024-03-10T02:15:00-06:00",
        "2024-03-10T02:30:00-06:00", "2024-03-10T02:45:00-06:00",
        "2024-03-10T03:00:00-06:00", "2024-03-10T03:15:00-05:00",
    ]
    assert all(np.isnan(records[index]["generation_mwh"]) for index in range(8, 12))
    assert records[12]["generation_mwh"] == 12.0


def test_fall_back_preserves_both_0115_through_0200_passes():
    values = [float(index) for index in range(96)]
    dst_values = [1000.0 + index for index in range(4)]
    records = normalize_row(date(2024, 11, 3), "WSL", values, _headers(), dst_values)
    assert len(records) == 100
    assert [records[index]["timestamp_local"] for index in range(4, 8)] == [
        "2024-11-03T01:15:00-05:00", "2024-11-03T01:30:00-05:00",
        "2024-11-03T01:45:00-05:00", "2024-11-03T02:00:00-05:00",
    ]
    assert [records[index]["timestamp_local"] for index in range(96, 100)] == [
        "2024-11-03T01:15:00-06:00", "2024-11-03T01:30:00-06:00",
        "2024-11-03T01:45:00-06:00", "2024-11-03T02:00:00-06:00",
    ]
    assert [record["generation_mwh"] for record in records[96:100]] == dst_values


def test_spring_gap_sorts_as_timestamp_major_contiguous_block():
    values = [float(index) for index in range(96)]
    values[8:12] = [None] * 4
    records = [record for fuel in ("Biomass", "WSL") for record in normalize_row(date(2024, 3, 10), fuel, values, _headers())]
    frame = _sort_records(pd.DataFrame.from_records(records))
    positions = np.flatnonzero(frame["generation_mwh"].isna().to_numpy())
    assert len(positions) == 8
    assert np.array_equal(positions, np.arange(positions[0], positions[-1] + 1))
    gap = frame.iloc[positions]
    assert gap.fuel_type.tolist() == ["Biomass", "WSL"] * 4
    assert gap.timestamp_local.tolist() == [
        f"2024-03-10T{hour:02d}:{minute:02d}:00-06:00"
        for hour, minute in ((2, 15), (2, 15), (2, 30), (2, 30), (2, 45), (2, 45), (3, 0), (3, 0))
    ]
    assert frame.iloc[positions[0] - 1].timestamp_local == "2024-03-10T02:00:00-06:00"
    assert frame.iloc[positions[-1] + 1].timestamp_local == "2024-03-10T03:15:00-05:00"


def test_time_headers_parse():
    assert _interval_minutes("0:15") == 15
    assert _interval_minutes("0:00") == 1440
    assert _interval_minutes(15 / 1440) == 15


def test_saved_parquet_matches_workbook_dst_rows(tmp_path):
    output = tmp_path / "fuel_mix_2024.parquet"
    frame = normalize_2024(output_path=output, workers=1)
    saved = pd.read_parquet(output)
    assert frame.attrs["sampled_values_verified"] == 120
    assert saved.columns.tolist() == ["timestamp_local", "fuel_type", "generation_mwh"]
    assert len(saved) == 351400 and saved.groupby("fuel_type").size().eq(35140).all()

    march = saved[saved.timestamp_local.str.startswith("2024-03-10T")].reset_index(drop=True)
    positions = np.flatnonzero(march.generation_mwh.isna().to_numpy())
    assert len(positions) == 40 and np.array_equal(positions, np.arange(positions[0], positions[-1] + 1))
    assert march.iloc[positions[0] - 1].timestamp_local == "2024-03-10T02:00:00-06:00"
    assert march.iloc[positions[-1] + 1].timestamp_local == "2024-03-10T03:15:00-05:00"

    workbook = openpyxl.load_workbook(DEFAULT_INPUT, read_only=True, data_only=True)
    try:
        sheet = workbook["Nov"]
        sheet.reset_dimensions()
        source_rows = [row for row in sheet.iter_rows(values_only=True)
                       if isinstance(row[0], datetime) and row[0].date() == date(2024, 11, 3)]
        assert len(source_rows) == 10 and {row[1] for row in source_rows} == set(FUEL_TYPES)
        for row in source_rows:
            daily = saved[(saved.fuel_type == row[1]) & saved.timestamp_local.str.startswith("2024-11-03T")]
            for index, local_time in enumerate(("01:15", "01:30", "01:45", "02:00")):
                first = daily[daily.timestamp_local == f"2024-11-03T{local_time}:00-05:00"]
                second = daily[daily.timestamp_local == f"2024-11-03T{local_time}:00-06:00"]
                assert first.generation_mwh.item() == float(row[8 + index])
                assert second.generation_mwh.item() == float(row[100 + index])
    finally:
        workbook.close()
