from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from bayes_demand_fcst.weather import WeatherCleanProvenance, weather_irregular_clean_schema
from bayes_demand_fcst.weather.clean_store import clean_asos_frame, build_weather_irregular_clean


def _row(station: str = "AAA", valid: str = "2024-01-01 00:00", **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "station": station,
        "valid": valid,
        "lon": -100.0,
        "lat": 30.0,
        "elevation": 100.0,
        "tmpf": 68.0,
        "dwpf": pd.NA,
        "relh": 50.0,
        "feel": 67.0,
        "skyc1": "BKN",
        "skyl1": 5_000.0,
        "p01i": 0.0,
        "mslp": 1013.0,
        "alti": 29.92,
        "vsby": 10.0,
    }
    row.update(overrides)
    return row


def _write_month(raw_dir: Path, state_code: str, month: str, rows: list[dict[str, object]]) -> Path:
    directory = raw_dir / f"weather_{state_code}_asos"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{state_code}_asos_land_{month.replace('-', '_')}.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_clean_asos_frame_recovers_exact_fields_and_prepares_provenance() -> None:
    clean, quality = clean_asos_frame(pd.DataFrame([_row()]), "tx", "tx_sample.csv")

    temperature_c = (68.0 - 32.0) * 5.0 / 9.0
    gamma = math.log(0.5) + 17.625 * temperature_c / (243.04 + temperature_c)
    expected_dewpoint_c = 243.04 * gamma / (17.625 - gamma)
    expected_dewpoint_f = expected_dewpoint_c * 9.0 / 5.0 + 32.0

    assert clean.loc[0, "observed_at_utc"] == pd.Timestamp("2024-01-01", tz="UTC")
    assert clean.loc[0, "dwpf"] == pytest.approx(expected_dewpoint_f, rel=1e-7)
    assert clean.loc[0, "feel_diff"] == -1.0
    assert clean.loc[0, "alti_hpa"] == pytest.approx(33.86389 * 29.92)
    assert clean.loc[0, "delta_p"] == pytest.approx(1013.0 - 33.86389 * 29.92)
    assert clean.loc[0, "cloud_frac"] == 0.75
    assert clean.loc[0, "dwpf_provenance"] == WeatherCleanProvenance.ALGEBRAIC_RECOVERY
    assert clean.loc[0, "cloud_frac_provenance"] == WeatherCleanProvenance.QC_DERIVED
    assert quality["dwpf_algebraic_recoveries"] == 1


def test_clean_asos_frame_applies_physical_qc_without_corrupt_sky_overwrite() -> None:
    frame = pd.DataFrame(
        [
            _row("TRACE", p01i=0.001, vsby=12.0),
            _row("BAD_PRECIP", p01i=8.1),
            _row("BAD_HEIGHT", skyc1="BKN", skyl1=95_000.0),
            _row("HIGH_CLOUD", skyc1="OVC", skyl1=15_000.0),
            _row("BAD_ALTI", alti=50.0),
            _row("BAD_MSLP", mslp=990.0, alti=29.92),
        ]
    )
    clean, quality = clean_asos_frame(frame, "tx", "tx_sample.csv")
    clean = clean.set_index("station_id")

    assert clean.loc["TRACE", "p01i"] == 0.0
    assert clean.loc["TRACE", "vsby"] == 10.0
    assert pd.isna(clean.loc["BAD_PRECIP", "p01i"])
    assert pd.isna(clean.loc["BAD_HEIGHT", "skyl1"])
    assert clean.loc["BAD_HEIGHT", "skyc1"] == "BKN"
    assert clean.loc["HIGH_CLOUD", "skyc1"] == "CLR"
    assert clean.loc["HIGH_CLOUD", "cloud_frac"] == 0.0
    assert pd.isna(clean.loc["BAD_ALTI", "alti"])
    assert pd.isna(clean.loc["BAD_MSLP", "mslp"])
    assert quality["p01i_trace_zeroed"] == 1
    assert quality["p01i_out_of_bounds"] == 1
    assert quality["vsby_capped"] == 1
    assert quality["skyl1_out_of_bounds"] == 1
    assert quality["skyc1_horizon_reclassified"] == 1
    assert quality["alti_out_of_bounds"] == 1
    assert quality["mslp_alti_mismatch"] == 1


def test_clean_asos_frame_averages_conflicting_duplicate_measurements() -> None:
    frame = pd.DataFrame([_row(tmpf=50.0), _row(tmpf=52.0)])

    clean, quality = clean_asos_frame(frame, "tx", "tx_sample.csv")

    assert len(clean) == 1
    assert clean.loc[0, "tmpf"] == 51.0
    assert not clean.duplicated(["station_id", "observed_at_utc"]).any()
    assert quality["duplicate_station_observations"] == 2
    assert quality["duplicate_rows_collapsed"] == 1
    assert quality["duplicate_numeric_measurements_averaged"] >= 1


def test_build_weather_irregular_clean_isolates_raw_and_reports_contract(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    source_a = _write_month(raw_dir, "tx", "2024-01", [_row(), _row("AAA", "2024-01-01 01:00")])
    source_b = _write_month(raw_dir, "nm", "2024-01", [_row("BBB")])
    raw_before = {source_a: source_a.read_bytes(), source_b: source_b.read_bytes()}
    output_dir = tmp_path / "weather_irregular_clean"

    report = build_weather_irregular_clean(
        raw_dir=raw_dir,
        output_dir=output_dir,
        state_codes=("tx", "nm"),
        year_months=("2024-01",),
    )

    parquet_files = sorted(output_dir.rglob("*.parquet"))
    combined = pd.concat([pd.read_parquet(path) for path in parquet_files], ignore_index=True)
    persisted_report = json.loads((output_dir / "quality_report.json").read_text(encoding="utf-8"))

    assert len(parquet_files) == 2
    assert len(combined) == 3
    assert combined.columns.tolist() == list(weather_irregular_clean_schema.required_columns)
    assert str(combined["observed_at_utc"].dtype.tz) == "UTC"
    provenance_columns = [column for column in combined.columns if column.endswith("_provenance")]
    assert all(str(combined[column].dtype) == "uint8" for column in provenance_columns)
    assert not combined.duplicated(["station_id", "observed_at_utc"]).any()
    assert persisted_report == report
    assert report["accepted"] is True
    assert report["row_count_read"] == 3
    assert report["row_count_written"] == 3
    assert report["quality"]["duplicate_station_observations"] == 0
    assert raw_before == {source_a: source_a.read_bytes(), source_b: source_b.read_bytes()}


def test_build_weather_irregular_clean_requires_overwrite(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    _write_month(raw_dir, "tx", "2024-01", [_row()])
    output_dir = tmp_path / "weather_irregular_clean"
    output_dir.mkdir()
    (output_dir / "existing.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError, match="use --overwrite"):
        build_weather_irregular_clean(raw_dir, output_dir, state_codes=("tx",))
