from datetime import date, datetime, time

import numpy as np
import pandas as pd

from scripts.normalize_ercot_fuel_mix_2016 import _interval_minutes, _sort_records, normalize_row


def _headers():
    return [15 * (index + 1) for index in range(96)]


def test_spring_forward_preserves_96_slots_and_gap_nan():
    values = [float(index) for index in range(96)]
    for index in range(7, 11):
        values[index] = None
    records = normalize_row(date(2016, 3, 13), "Biomass", values, _headers())

    assert len(records) == 96
    assert [records[index]["timestamp_local"] for index in range(7, 12)] == [
        "2016-03-13T02:00:00-06:00",
        "2016-03-13T02:15:00-06:00",
        "2016-03-13T02:30:00-06:00",
        "2016-03-13T02:45:00-06:00",
        "2016-03-13T03:00:00-05:00",
    ]
    assert all(np.isnan(records[index]["generation_mwh"]) for index in range(7, 11))
    assert records[11]["generation_mwh"] == 11.0


def test_spring_gap_nan_rows_sort_as_one_block():
    values = [float(index) for index in range(96)]
    for index in range(7, 11):
        values[index] = None
    records = [
        record
        for fuel in ("Biomass", "Coal")
        for record in normalize_row(date(2016, 3, 13), fuel, values, _headers())
    ]

    frame = _sort_records(pd.DataFrame.from_records(records))
    null_positions = np.flatnonzero(frame["generation_mwh"].isna().to_numpy())
    assert len(null_positions) == 8
    assert np.array_equal(null_positions, np.arange(null_positions[0], null_positions[-1] + 1))
    assert frame.iloc[null_positions]["timestamp_local"].tolist() == [
        f"2016-03-13T{hour:02d}:{minute:02d}:00-06:00"
        for hour, minute in ((2, 0), (2, 0), (2, 15), (2, 15), (2, 30), (2, 30), (2, 45), (2, 45))
    ]
    assert frame.iloc[null_positions]["fuel_type"].tolist() == ["Biomass", "Coal"] * 4
    assert frame.iloc[null_positions[0] - 1]["timestamp_local"] == "2016-03-13T01:45:00-06:00"
    assert frame.iloc[null_positions[-1] + 1]["timestamp_local"] == "2016-03-13T03:00:00-05:00"


def test_fall_back_appends_dst_values_in_second_pass():
    values = [float(index) for index in range(96)]
    dst_values = [1000.0 + index for index in range(4)]
    records = normalize_row(date(2016, 11, 6), "Biomass", values, _headers(), dst_values)

    assert len(records) == 100
    first_pass = next(record for record in records if record["timestamp_local"] == "2016-11-06T01:00:00-05:00")
    second_pass = next(record for record in records if record["timestamp_local"] == "2016-11-06T01:00:00-06:00")
    assert first_pass["generation_mwh"] == values[3]
    assert second_pass["generation_mwh"] == dst_values[0]


def test_time_and_numeric_excel_headers_are_supported():
    assert _interval_minutes(time(0, 15)) == 15
    assert _interval_minutes(15 / 1440) == 15
    assert _interval_minutes(time(0, 0)) == 1440
    assert _interval_minutes(datetime(1900, 1, 1)) == 1440
    from datetime import timedelta
    assert _interval_minutes(timedelta(0)) == 1440