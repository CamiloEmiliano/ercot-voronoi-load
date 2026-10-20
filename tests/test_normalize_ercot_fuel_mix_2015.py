from datetime import date

import numpy as np
import pandas as pd

from scripts.normalize_ercot_fuel_mix_2015 import _interval_minutes, normalize_2015, normalize_row


def _headers():
    return [15 * (index + 1) for index in range(96)]


def test_spring_forward_preserves_96_slots_and_maps_values_to_post_shift_labels():
    values = [float(index) for index in range(92)] + ["", "", "", ""]
    records = normalize_row(date(2015, 3, 8), "Biomass", values, _headers())

    assert len(records) == 96
    assert [records[index]["timestamp_local"] for index in range(7, 12)] == [
        "2015-03-08T02:00:00-06:00",
        "2015-03-08T02:15:00-06:00",
        "2015-03-08T02:30:00-06:00",
        "2015-03-08T02:45:00-06:00",
        "2015-03-08T03:00:00-05:00",
    ]
    assert all(np.isnan(records[index]["generation_mwh"]) for index in range(7, 11))
    assert records[11]["generation_mwh"] == 7.0


def test_fall_back_retains_first_and_second_occurrence_with_distinct_offsets():
    values = [float(index) for index in range(96)]
    dst_values = [1000.0 + index for index in range(4)]
    records = normalize_row(date(2015, 11, 1), "Biomass", values, _headers(), dst_values)
    second_pass = next(record for record in records if record["timestamp_local"] == "2015-11-01T01:00:00-06:00")

    assert len(records) == 100
    assert records[3]["timestamp_local"] == "2015-11-01T01:00:00-05:00"
    assert records[3]["generation_mwh"] == values[3]
    assert second_pass["generation_mwh"] == dst_values[0]
    assert records[7]["timestamp_local"] == "2015-11-01T02:00:00-06:00"


def test_dash_becomes_zero_and_blank_becomes_nan():
    values = [1.0] * 96
    values[0] = "-"
    values[1] = ""
    records = normalize_row(date(2015, 1, 1), "Biomass", values, _headers())

    assert records[0]["generation_mwh"] == 0.0
    assert np.isnan(records[1]["generation_mwh"])


def test_midnight_header_is_next_day_endpoint():
    assert _interval_minutes(0.0) == 1440


def test_generated_fall_back_rows_are_sorted_by_instant(tmp_path):
    output = tmp_path / "fuel_mix_2015.parquet"
    frame = normalize_2015(output_path=output, workers=1)

    biomass = frame[
        (frame["fuel_type"] == "Biomass")
        & frame["timestamp_local"].str.startswith("2015-11-01T")
    ].copy()
    repeated = biomass[
        biomass["timestamp_local"].str.startswith((
            "2015-11-01T01:00", "2015-11-01T01:15",
            "2015-11-01T01:30", "2015-11-01T01:45",
        ))
    ]
    utc_instants = pd.to_datetime(repeated["timestamp_local"], format="ISO8601", utc=True)

    assert utc_instants.is_monotonic_increasing
    assert repeated["timestamp_local"].tolist() == [
        "2015-11-01T01:00:00-05:00",
        "2015-11-01T01:15:00-05:00",
        "2015-11-01T01:30:00-05:00",
        "2015-11-01T01:45:00-05:00",
        "2015-11-01T01:00:00-06:00",
        "2015-11-01T01:15:00-06:00",
        "2015-11-01T01:30:00-06:00",
        "2015-11-01T01:45:00-06:00",
    ]

    spring = frame[frame["timestamp_local"].str.startswith("2015-03-08T")]
    null_positions = np.flatnonzero(spring["generation_mwh"].isna().to_numpy())
    assert len(null_positions) == 36
    assert np.array_equal(null_positions, np.arange(null_positions[0], null_positions[-1] + 1))
    spring_nulls = spring.iloc[null_positions]
    fuels = sorted(frame["fuel_type"].unique())
    assert list(zip(spring_nulls["timestamp_local"], spring_nulls["fuel_type"])) == [
        (f"2015-03-08T02:{minute:02d}:00-06:00", fuel)
        for minute in (0, 15, 30, 45)
        for fuel in fuels
    ]