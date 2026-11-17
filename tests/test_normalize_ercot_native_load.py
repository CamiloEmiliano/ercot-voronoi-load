from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from scripts.load import normalize_ercot_native_load as nl


def _synthetic_year_frame(year: int, *, legacy_schema: bool) -> pd.DataFrame:
    """Build one synthetic full-year Native_Load dataframe matching ERCOT conventions."""
    start_utc = pd.Timestamp(f"{year}-01-01 07:00:00+00:00")
    end_utc = pd.Timestamp(f"{year + 1}-01-01 06:00:00+00:00")
    utc_hours = pd.date_range(start_utc, end_utc, freq="1h")
    spring_date = nl.SPRING_FORWARD_DATES[year]

    labels: list[object] = []
    for ts in utc_hours:
        local_end = ts.tz_convert(nl.CENTRAL)
        local_start = (ts - pd.Timedelta(hours=1)).tz_convert(nl.CENTRAL)
        if legacy_schema:
            # 2014-2016: naive Excel datetime of hour_start + 1h (so fall-back 07:00/08:00 UTC are both 02:00,
            # while spring-forward 08:00 UTC is labeled 03:00 CDT).
            if local_end.date() == spring_date and local_start.hour == 1:
                naive = local_end.tz_localize(None).to_pydatetime()
            else:
                naive = local_start.tz_localize(None).to_pydatetime() + timedelta(hours=1)
            if ts == utc_hours[0]:
                naive = naive + timedelta(milliseconds=3)
            labels.append(naive)
        else:
            # 2017-2025: MM/DD/YYYY HH:MM with 24:00 at midnight and "02:00 DST" on fall-back 2nd pass
            if local_end.hour == 0:
                op_date = local_start.date()
                labels.append(f"{op_date:%m/%d/%Y} 24:00")
            elif local_end.date() == spring_date and local_start.hour == 1:
                labels.append(f"{spring_date:%m/%d/%Y} 02:00")
            elif local_end.hour == 1 and local_end.utcoffset() == timedelta(hours=-6) and local_start.utcoffset() == timedelta(hours=-6) and (ts - pd.Timedelta(hours=1)).tz_convert(nl.CENTRAL).utcoffset() == timedelta(hours=-6) and local_end.date() == nl.FALL_BACK_DATES[year]:
                labels.append(f"{local_end:%m/%d/%Y} 02:00 DST")
            elif local_start.utcoffset() == timedelta(hours=-6) and (ts - pd.Timedelta(hours=2)).tz_convert(nl.CENTRAL).utcoffset() == timedelta(hours=-5):
                # 08:00 UTC on fall-back day (01:00 CST start -> 02:00 CST end -> labeled 02:00 DST)
                labels.append(f"{local_end:%m/%d/%Y} 02:00 DST")
            elif local_end.date() == nl.FALL_BACK_DATES[year] and local_start.hour == 1 and local_start.utcoffset() == timedelta(hours=-5):
                # 07:00 UTC on fall-back day (01:00 CDT start -> labeled 02:00)
                labels.append(f"{local_end:%m/%d/%Y} 02:00")
            else:
                labels.append(f"{local_end:%m/%d/%Y} {local_end:%H:%M}")

    n = len(utc_hours)
    base = np.arange(n, dtype="float64")
    if legacy_schema:
        return pd.DataFrame({
            "Hour_End": labels,
            "COAST": base + 100.0,
            "EAST": base + 20.0,
            "FAR_WEST": base + 30.0,
            "NORTH": base + 10.0,
            "NORTH_C": base + 120.0,
            "SOUTHERN": base + 40.0,
            "SOUTH_C": base + 60.0,
            "WEST": base + 15.0,
            "ERCOT": 8 * base + 395.0,
        })
    return pd.DataFrame({
        "Hour Ending": labels,
        "COAST": base + 100.0,
        "EAST": base + 20.0,
        "FWEST": base + 30.0,
        "NORTH": base + 10.0,
        "NCENT": base + 120.0,
        "SOUTH": base + 40.0,
        "SCENT": base + 60.0,
        "WEST": base + 15.0,
        "ERCOT": 8 * base + 395.0,
    })


def test_legacy_2014_spring_forward_and_fall_back_utc_mapping():
    raw = _synthetic_year_frame(2014, legacy_schema=True)
    normalized, report = nl.normalize_native_load_frame(raw, 2014, "2014_ercot_hourly_load_data.xls")

    assert report["rows"] == 8760
    assert report["spring_forward_rows"] == 23
    assert report["fall_back_rows"] == 25

    # Spring-forward 08:00 UTC was labeled 03:00 in 2014-2016
    sp_08 = normalized.loc[normalized["timestamp_utc"] == pd.Timestamp("2014-03-09 08:00:00+00:00")].iloc[0]
    assert sp_08["timestamp_local"] == "2014-03-09T03:00:00-05:00"

    # Fall-back 07:00 UTC is 1st pass (-05:00), 08:00 UTC is 2nd pass (-06:00, dst_flag=True)
    fb_07 = normalized.loc[normalized["timestamp_utc"] == pd.Timestamp("2014-11-02 07:00:00+00:00")].iloc[0]
    fb_08 = normalized.loc[normalized["timestamp_utc"] == pd.Timestamp("2014-11-02 08:00:00+00:00")].iloc[0]
    assert fb_07["timestamp_local"] == "2014-11-02T02:00:00-05:00"
    assert not bool(fb_07["dst_flag"])
    assert fb_08["timestamp_local"] == "2014-11-02T02:00:00-06:00"
    assert bool(fb_08["dst_flag"])


def test_modern_2024_leap_year_and_mixed_datetime_cell_supported():
    raw = _synthetic_year_frame(2024, legacy_schema=False)
    # Simulate 2022 Dec 1 native Excel datetime cell mixed into string column
    raw["Hour Ending"] = raw["Hour Ending"].astype(object)
    raw.loc[raw["Hour Ending"] == "12/01/2024 01:00", "Hour Ending"] = datetime(2024, 12, 1, 1, 0)

    normalized, report = nl.normalize_native_load_frame(raw, 2024, "Native_Load_2024.zip")

    assert report["rows"] == 8784
    assert report["spring_forward_rows"] == 23
    assert report["fall_back_rows"] == 25
    dec_row = normalized.loc[normalized["timestamp_utc"] == pd.Timestamp("2024-12-01 07:00:00+00:00")].iloc[0]
    assert dec_row["timestamp_local"] == "2024-12-01T01:00:00-06:00"


def test_write_parquet_with_qc_persists_and_verifies_source_nans(tmp_path):
    raw = _synthetic_year_frame(2016, legacy_schema=True)
    raw.loc[7463, raw.columns[1:]] = np.nan
    normalized, report = nl.normalize_native_load_frame(raw, 2016, "native_Load_2016.zip")
    assert report["nan_load_rows"] == 1

    out_path = tmp_path / "load_hourly_2016.parquet"
    checked = nl.write_parquet_with_qc(raw, normalized, out_path, 2016)
    assert checked >= 60
    persisted = pd.read_parquet(out_path)
    assert pd.isna(persisted.loc[7463, "load_mw"])
