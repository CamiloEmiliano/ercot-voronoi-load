from datetime import date

import numpy as np
import pandas as pd

from scripts.normalize_ercot_fuel_mix_2017 import _interval_minutes, _sort_records, normalize_2017, normalize_row


def _headers():
    return [15 * (index + 1) for index in range(96)]


def test_spring_forward_gap_is_2017_specific_0215_through_0300():
    values = [float(index) for index in range(96)]
    values[8:12] = [None] * 4
    records = normalize_row(date(2017, 3, 12), "Biomass", values, _headers())

    assert len(records) == 96
    assert [records[index]["timestamp_local"] for index in range(7, 13)] == [
        "2017-03-12T02:00:00-06:00",
        "2017-03-12T02:15:00-06:00",
        "2017-03-12T02:30:00-06:00",
        "2017-03-12T02:45:00-06:00",
        "2017-03-12T03:00:00-06:00",
        "2017-03-12T03:15:00-05:00",
    ]
    assert all(np.isnan(records[index]["generation_mwh"]) for index in range(8, 12))
    assert records[12]["generation_mwh"] == 12.0


def test_fall_back_second_pass_is_0115_through_0200():
    values = [float(index) for index in range(96)]
    dst_values = [1000.0 + index for index in range(4)]
    records = normalize_row(date(2017, 11, 5), "Biomass", values, _headers(), dst_values)

    assert len(records) == 100
    assert records[4]["timestamp_local"] == "2017-11-05T01:15:00-05:00"
    assert records[7]["timestamp_local"] == "2017-11-05T02:00:00-05:00"
    assert records[7]["generation_mwh"] == values[7]
    second_pass = [record for record in records if record["generation_mwh"] >= 1000]
    assert [record["timestamp_local"] for record in second_pass] == [
        "2017-11-05T01:15:00-06:00",
        "2017-11-05T01:30:00-06:00",
        "2017-11-05T01:45:00-06:00",
        "2017-11-05T02:00:00-06:00",
    ]
    assert [record["generation_mwh"] for record in second_pass] == dst_values


def test_spring_placeholders_sort_as_timestamp_major_block():
    values = [float(index) for index in range(96)]
    values[8:12] = [None] * 4
    records = [
        record
        for fuel in ("Biomass", "Coal")
        for record in normalize_row(date(2017, 3, 12), fuel, values, _headers())
    ]
    frame = _sort_records(pd.DataFrame.from_records(records))
    null_positions = np.flatnonzero(frame["generation_mwh"].isna().to_numpy())

    assert len(null_positions) == 8
    assert np.array_equal(null_positions, np.arange(null_positions[0], null_positions[-1] + 1))
    gap = frame.iloc[null_positions]
    assert gap["fuel_type"].tolist() == ["Biomass", "Coal"] * 4
    assert gap["timestamp_local"].tolist() == [
        f"2017-03-12T{hour:02d}:{minute:02d}:00-06:00"
        for hour, minute in ((2, 15), (2, 15), (2, 30), (2, 30), (2, 45), (2, 45), (3, 0), (3, 0))
    ]
    assert frame.iloc[null_positions[0] - 1]["timestamp_local"] == "2017-03-12T02:00:00-06:00"
    assert frame.iloc[null_positions[-1] + 1]["timestamp_local"] == "2017-03-12T03:15:00-05:00"


def test_interval_headers_accept_text_and_midnight():
    assert _interval_minutes("0:15") == 15
    assert _interval_minutes("0:00") == 1440


def test_generated_parquet_persists_2017_dst_order(tmp_path):
    output = tmp_path / "fuel_mix_2017.parquet"
    frame = normalize_2017(output_path=output, workers=1)
    saved = pd.read_parquet(output)

    assert saved.columns.tolist() == ["timestamp_local", "fuel_type", "generation_mwh"]
    assert len(saved) == 315396
    assert saved.groupby("fuel_type").size().eq(35044).all()
    march = saved[saved["timestamp_local"].str.startswith("2017-03-12T")].reset_index(drop=True)
    null_positions = np.flatnonzero(march["generation_mwh"].isna().to_numpy())
    assert len(null_positions) == 36
    assert np.array_equal(null_positions, np.arange(null_positions[0], null_positions[-1] + 1))
    assert march.iloc[null_positions[0] - 1]["timestamp_local"] == "2017-03-12T02:00:00-06:00"
    assert march.iloc[null_positions[-1] + 1]["timestamp_local"] == "2017-03-12T03:15:00-05:00"

    november = saved[(saved["fuel_type"] == "Biomass") & saved["timestamp_local"].str.startswith("2017-11-05T")]
    repeated = november[november["timestamp_local"].str.startswith(("2017-11-05T01:15", "2017-11-05T01:30", "2017-11-05T01:45", "2017-11-05T02:00"))]
    assert repeated["timestamp_local"].tolist() == [
        "2017-11-05T01:15:00-05:00",
        "2017-11-05T01:30:00-05:00",
        "2017-11-05T01:45:00-05:00",
        "2017-11-05T02:00:00-05:00",
        "2017-11-05T01:15:00-06:00",
        "2017-11-05T01:30:00-06:00",
        "2017-11-05T01:45:00-06:00",
        "2017-11-05T02:00:00-06:00",
    ]