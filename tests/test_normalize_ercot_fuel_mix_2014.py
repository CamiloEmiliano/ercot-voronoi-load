from datetime import date

import numpy as np
import pandas as pd

from scripts.normalize_ercot_fuel_mix_2014 import (
    _interval_minutes,
    _normalize_row,
    _parse_date_fuel,
    _sort_by_utc,
)


def _headers():
    return [15 * (index + 1) for index in range(96)]


def test_parse_date_fuel_extracts_date_and_category():
    assert _parse_date_fuel("03/09/14_Gas-CC") == (date(2014, 3, 9), "Gas-CC")


def test_midnight_excel_header_means_next_day_endpoint():
    assert _interval_minutes(0.0) == 1440


def test_march_dst_keeps_96_slots_and_local_iso_timestamp():
    values = [float(index) for index in range(92)] + ["", "", "", ""]
    records = _normalize_row(date(2014, 3, 9), "Biomass", values, _headers())

    assert len(records) == 96
    assert set(records[0]) == {"timestamp_local", "fuel_type", "generation_mwh"}
    assert [records[index]["timestamp_local"] for index in range(7, 12)] == [
        "2014-03-09T02:00:00-06:00",
        "2014-03-09T02:15:00-06:00",
        "2014-03-09T02:30:00-06:00",
        "2014-03-09T02:45:00-06:00",
        "2014-03-09T03:00:00-05:00",
    ]
    assert [records[index]["timestamp_local"] for index in range(7, 11)] == [
        "2014-03-09T02:00:00-06:00",
        "2014-03-09T02:15:00-06:00",
        "2014-03-09T02:30:00-06:00",
        "2014-03-09T02:45:00-06:00",
    ]
    assert all(np.isnan(records[index]["generation_mwh"]) for index in range(7, 11))
    assert records[11]["generation_mwh"] == 7.0
    assert records[11]["timestamp_local"] == "2014-03-09T03:00:00-05:00"


def test_november_dst_uses_offsets_to_distinguish_passes_without_marker():
    values = [float(index) for index in range(96)]
    records = _normalize_row(date(2014, 11, 2), "Biomass", values, _headers())
    first_pass = records[3]
    second_pass = next(record for record in records if record["timestamp_local"] == "2014-11-02T01:00:00-06:00")

    assert len(records) == 100
    assert first_pass["timestamp_local"] == "2014-11-02T01:00:00-05:00"
    assert second_pass["timestamp_local"] == "2014-11-02T01:00:00-06:00"
    assert np.isnan(first_pass["generation_mwh"])
    assert second_pass["generation_mwh"] == values[3]
    assert records[7]["timestamp_local"] == "2014-11-02T02:00:00-06:00"
    assert records[7]["generation_mwh"] == values[7]
    assert "dst_occurrence" not in first_pass


def test_sorting_keeps_each_fall_pass_contiguous_in_utc_order():
    values = [float(index) for index in range(96)]
    records = _normalize_row(date(2014, 11, 2), "Biomass", values, _headers())
    frame = _sort_by_utc(pd.DataFrame.from_records(records))
    repeated = frame[frame["timestamp_local"].str.contains("T01:")]

    assert repeated["timestamp_local"].tolist() == [
        "2014-11-02T01:00:00-05:00",
        "2014-11-02T01:15:00-05:00",
        "2014-11-02T01:30:00-05:00",
        "2014-11-02T01:45:00-05:00",
        "2014-11-02T01:00:00-06:00",
        "2014-11-02T01:15:00-06:00",
        "2014-11-02T01:30:00-06:00",
        "2014-11-02T01:45:00-06:00",
    ]


def test_sorting_keeps_spring_gap_as_contiguous_timestamp_major_block():
    values = [float(index) for index in range(92)] + ["", "", "", ""]
    records = [
        record
        for fuel in ("Biomass", "Coal")
        for record in _normalize_row(date(2014, 3, 9), fuel, values, _headers())
    ]
    frame = _sort_by_utc(pd.DataFrame.from_records(records))
    null_positions = np.flatnonzero(frame["generation_mwh"].isna().to_numpy())

    assert len(null_positions) == 8
    assert np.array_equal(null_positions, np.arange(null_positions[0], null_positions[-1] + 1))
    gap = frame.iloc[null_positions]
    assert list(zip(gap["timestamp_local"], gap["fuel_type"])) == [
        (f"2014-03-09T02:{minute:02d}:00-06:00", fuel)
        for minute in (0, 15, 30, 45)
        for fuel in ("Biomass", "Coal")
    ]
    assert frame.iloc[null_positions[0] - 1]["timestamp_local"] == "2014-03-09T01:45:00-06:00"
    assert frame.iloc[null_positions[-1] + 1]["timestamp_local"] == "2014-03-09T03:00:00-05:00"


def test_dash_is_zero_and_blank_is_nan():
    values = [1.0] * 96
    values[0] = "-"
    values[1] = ""
    records = _normalize_row(date(2014, 1, 1), "Biomass", values, _headers())

    assert records[0]["generation_mwh"] == 0.0
    assert np.isnan(records[1]["generation_mwh"])