import numpy as np
import pandas as pd
import pytest

from scripts.fuel_mix import aggregate_ercot_fuel_mix_hourly as fm


def _synthetic_15m_year(year: int, *, fuels: tuple[str, ...] = ("Coal", "Wind")) -> pd.DataFrame:
    start_utc = pd.Timestamp(f"{year}-01-01 06:15:00+00:00")
    end_utc = pd.Timestamp(f"{year + 1}-01-01 06:00:00+00:00")
    intervals = pd.date_range(start_utc, end_utc, freq="15min")
    rows: list[dict[str, object]] = []
    for idx, ts_utc in enumerate(intervals):
        local_str = ts_utc.tz_convert(fm.CENTRAL).isoformat(timespec="seconds")
        for f_idx, fuel in enumerate(fuels):
            rows.append({
                "timestamp_local": local_str,
                "fuel_type": fuel,
                "generation_mwh": float((idx % 4) + 10 + f_idx),
            })
    return pd.DataFrame(rows)


def test_spring_forward_structural_placeholders_do_not_corrupt_hour_totals():
    df = _synthetic_15m_year(2024, fuels=("Coal", "WSL"))
    # Inject 4 structural NaN placeholders for 2024-03-10 02:15..03:00 -06:00 (matching 2024 normalizer)
    placeholders = [
        {"timestamp_local": f"2024-03-10T{hh:02d}:{mm:02d}:00-06:00", "fuel_type": fuel, "generation_mwh": np.nan}
        for hh, mm in ((2, 15), (2, 30), (2, 45), (3, 0))
        for fuel in ("Coal", "WSL")
    ]
    df = pd.concat([df, pd.DataFrame(placeholders)], ignore_index=True)

    hourly, report = fm.aggregate_fuel_mix_frame(df, 2024)
    assert report["spring_placeholders_excluded"] == 8
    assert report["unique_hours"] == 8784
    assert report["incomplete_hours_rows"] == 0

    # Hour ending 09:00 UTC (04:00 CDT) must sum only the 4 real intervals (0+1+2+3 + 4*10 = 46.0 for Coal)
    row = hourly.loc[(hourly["timestamp_utc"] == pd.Timestamp("2024-03-10 09:00:00+00:00")) & (hourly["fuel_type"] == "Coal")].iloc[0]
    assert row["observed_intervals"] == 4
    assert row["generation_mwh"] == pytest.approx(46.0)


def test_incomplete_fall_back_hours_become_nan_and_preserve_signed_wsl(tmp_path):
    df = _synthetic_15m_year(2014, fuels=("Coal", "WSL"))
    # Simulate 2014-11-02 lost 1st pass (01:00..01:45 -05:00)
    lost = df["timestamp_local"].isin([
        "2014-11-02T01:00:00-05:00",
        "2014-11-02T01:15:00-05:00",
        "2014-11-02T01:30:00-05:00",
        "2014-11-02T01:45:00-05:00",
    ])
    df.loc[lost, "generation_mwh"] = np.nan
    # Negative WSL value on a real interval
    df.loc[(df["timestamp_local"] == "2014-01-01T00:15:00-06:00") & (df["fuel_type"] == "WSL"), "generation_mwh"] = -50.0

    hourly, report = fm.aggregate_fuel_mix_frame(df, 2014)
    assert report["incomplete_hours_rows"] == 4  # 2 fuels * 2 incomplete hours (06:00 and 07:00 UTC)
    assert report["nan_generation_rows"] == 4
    assert report["negative_generation_rows"] >= 1

    out_path = tmp_path / "fuel_mix_hourly_2014.parquet"
    checked = fm.write_parquet_with_qc(df, hourly, out_path, 2014)
    assert checked >= 64  # 60 monthly + 4 incomplete rows

    persisted = pd.read_parquet(out_path)
    h06 = persisted.loc[(persisted["timestamp_utc"] == pd.Timestamp("2014-11-02 06:00:00+00:00")) & (persisted["fuel_type"] == "Coal")].iloc[0]
    h07 = persisted.loc[(persisted["timestamp_utc"] == pd.Timestamp("2014-11-02 07:00:00+00:00")) & (persisted["fuel_type"] == "Coal")].iloc[0]
    assert h06["observed_intervals"] == 3 and pd.isna(h06["generation_mwh"])
    assert h07["observed_intervals"] == 1 and pd.isna(h07["generation_mwh"])
