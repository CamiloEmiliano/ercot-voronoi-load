from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from bayes_demand_fcst.weather import (
    ObservationMode,
    WeatherProvenance,
    align_causal_local,
    collapse_precipitation_cycle,
    build_weather_primary_hourly,
    canonical_primary_hours,
    impute_precipitation_hurdle,
    reconcile_group_a,
    weather_primary_hourly_schema,
)

UTC = timezone.utc


def _station_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "station_id": ["AAA", "BBB"],
            "source_state": ["TX", "TX"],
            "longitude_wgs84": [-100.0, -99.9],
            "latitude_wgs84": [30.0, 30.1],
            "elevation_m": [100.0, 120.0],
            "first_observed_utc": [pd.Timestamp("2024-01-01", tz="UTC")] * 2,
            "last_observed_utc": [pd.Timestamp("2025-12-31", tz="UTC")] * 2,
            "observation_count": [1, 1],
        }
    )


def _calibration_dir(path: Path) -> Path:
    path.mkdir(parents=True)
    rows = []
    for variable, value in (("tmpf", 10.0), ("dwpf", 8.0), ("alti", 29.5)):
        rows.append(
            {
                "station_id": "AAA",
                "variable": variable,
                "train_end_utc": pd.Timestamp("2021-12-31T23:59:59Z"),
                "reference_utc": pd.Timestamp("2014-01-01T00:00:00Z"),
                "sample_count": 10_000,
                "design_matrix_condition": 2.0,
                "coefficient_00": value,
                "coefficient_01": 0.0,
                "coefficient_02": 0.0,
                "coefficient_03": 0.0,
                "coefficient_04": 0.0,
                "coefficient_05": 0.0,
                "coefficient_06": 0.0,
            }
        )
    pd.DataFrame(rows).to_parquet(path / "harmonic_coefficients.parquet", index=False)
    (path / "solver_config.json").write_text(
        json.dumps(
            {
                "train_end_utc": "2021-12-31T23:59:59+00:00",
                "reference_utc": "2014-01-01T00:00:00+00:00",
                "harmonic_periods_hours": [8765.82, 24.0, 12.0],
                "covariance_parameters": {
                    "tmpf": {
                        "kernel": "matern32",
                        "variance": 25.0,
                        "spatial_length_m": 200000.0,
                        "temporal_length_hours": 12.0,
                        "nugget_variance": 1.0,
                    },
                    "dwpf": {
                        "kernel": "matern32",
                        "variance": 25.0,
                        "spatial_length_m": 200000.0,
                        "temporal_length_hours": 12.0,
                        "nugget_variance": 1.0,
                    },
                    "feel_diff": {
                        "kernel": "exponential",
                        "variance": 16.0,
                        "spatial_length_m": 150000.0,
                        "temporal_length_hours": 8.0,
                        "nugget_variance": 1.0,
                    },
                    "alti": {
                        "kernel": "matern32",
                        "variance": 0.02,
                        "spatial_length_m": 400000.0,
                        "temporal_length_hours": 18.0,
                        "nugget_variance": 0.0004,
                    },
                },
                "idw_parameters": {
                    "spatial_length_m": 45000.0,
                    "prior_weight": 0.15,
                    "wet_probability_threshold": 0.5,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _write_clean_partition(clean_dir: Path, rows: list[dict[str, object]]) -> Path:
    path = clean_dir / "state=tx" / "year=2024" / "month=01" / "part-test.parquet"
    path.parent.mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def test_causal_local_alignment_excludes_future_reports() -> None:
    targets = pd.DatetimeIndex([pd.Timestamp("2024-01-01T01:00:00Z")], name="timestamp_utc")
    observations = pd.DataFrame(
        {
            "station_id": ["AAA", "AAA"],
            "observed_at_utc": [
                pd.Timestamp("2024-01-01T00:55:00Z"),
                pd.Timestamp("2024-01-01T01:05:00Z"),
            ],
            "tmpf": [50.0, 999.0],
        }
    )

    aligned = align_causal_local(observations, "AAA", targets, "tmpf")

    assert aligned.loc[0, "tmpf"] == 50.0
    assert aligned.loc[0, "observed_at_utc"] == pd.Timestamp("2024-01-01T00:55:00Z")
    assert aligned.loc[0, "tmpf_provenance"] == WeatherProvenance.LOCAL_TEMPORAL


def test_group_a_reconciliation_enforces_saturation_and_humidity() -> None:
    frame = pd.DataFrame(
        {
            "tmpf": [80.0],
            "dwpf": [90.0],
            "feel_diff": [-3.0],
        }
    )

    reconciled = reconcile_group_a(frame)

    assert reconciled.loc[0, "dwpf"] == 80.0
    assert 0.0 <= reconciled.loc[0, "relh"] <= 100.0
    assert reconciled.loc[0, "feel"] == 77.0


def test_precipitation_cycle_uses_max_without_future_or_double_counting() -> None:
    observations = pd.DataFrame(
        {
            "station_id": ["AAA", "AAA", "AAA"],
            "observed_at_utc": [
                pd.Timestamp("2024-01-01T00:15:00Z"),
                pd.Timestamp("2024-01-01T00:55:00Z"),
                pd.Timestamp("2024-01-01T01:05:00Z"),
            ],
            "p01i": [0.1, 0.2, 9.0],
        }
    )

    collapsed = collapse_precipitation_cycle(observations)
    one_hour = collapsed.loc[
        (collapsed["station_id"] == "AAA")
        & (collapsed["timestamp_utc"] == pd.Timestamp("2024-01-01T01:00:00Z"))
    ]

    assert len(one_hour) == 1
    assert one_hour.iloc[0]["p01i"] == 0.2


def test_precipitation_hurdle_shrinks_sparse_wet_signal_toward_dry_prior() -> None:
    dry = impute_precipitation_hurdle(
        values=(0.0, 0.0, 0.0),
        distances_m=(10_000.0, 15_000.0, 20_000.0),
        spatial_length_m=45_000.0,
        prior_weight=0.15,
        wet_threshold=0.5,
    )
    distant_wet = impute_precipitation_hurdle(
        values=(1.0,),
        distances_m=(117_000.0,),
        spatial_length_m=45_000.0,
        prior_weight=0.15,
        wet_threshold=0.5,
    )

    assert dry.p01i == 0.0
    assert dry.wet_probability == 0.0
    assert distant_wet.p01i == 0.0
    assert distant_wet.wet_probability < 0.5


def test_canonical_primary_grid_has_exact_105192_hours() -> None:
    grid = canonical_primary_hours()

    assert len(grid) == 105_192
    assert grid[0] == pd.Timestamp("2014-01-01T07:00:00Z")
    assert grid[-1] == pd.Timestamp("2026-01-01T06:00:00Z")


def test_build_primary_cube_uses_frozen_coefficients_and_causal_observations(tmp_path: Path) -> None:
    station_path = tmp_path / "station_metadata.parquet"
    _station_frame().to_parquet(station_path, index=False)
    clean_dir = tmp_path / "weather_irregular_clean"
    _write_clean_partition(
        clean_dir,
        [
            {
                "station_id": "AAA",
                "observed_at_utc": pd.Timestamp("2024-01-01T00:00:00Z"),
                "longitude_wgs84": -100.0,
                "latitude_wgs84": 30.0,
                "elevation_m": 100.0,
                "tmpf": 40.0,
                "dwpf": 30.0,
                "feel_diff": -2.0,
                "alti": 29.8,
                "p01i": 0.0,
            },
            {
                "station_id": "AAA",
                "observed_at_utc": pd.Timestamp("2024-01-01T00:55:00Z"),
                "longitude_wgs84": -100.0,
                "latitude_wgs84": 30.0,
                "elevation_m": 100.0,
                "tmpf": 42.0,
                "dwpf": 32.0,
                "feel_diff": -1.0,
                "alti": 29.7,
                "p01i": 0.1,
            },
            {
                "station_id": "AAA",
                "observed_at_utc": pd.Timestamp("2024-01-01T01:05:00Z"),
                "longitude_wgs84": -100.0,
                "latitude_wgs84": 30.0,
                "elevation_m": 100.0,
                "tmpf": 999.0,
                "dwpf": 999.0,
                "feel_diff": 999.0,
                "alti": 99.0,
                "p01i": 9.0,
            },
        ],
    )
    calibration_dir = _calibration_dir(tmp_path / "weather_calibration")
    output_dir = tmp_path / "weather_primary_hourly"

    report = build_weather_primary_hourly(
        clean_dir=clean_dir,
        calibration_dir=calibration_dir,
        station_metadata_path=station_path,
        output_dir=output_dir,
        variables=("tmpf", "dwpf", "feel_diff", "alti", "p01i"),
        start_utc=pd.Timestamp("2024-01-01T00:00:00Z"),
        end_utc=pd.Timestamp("2024-01-01T01:00:00Z"),
    )

    primary = pd.read_parquet(next(output_dir.rglob("*.parquet")))
    aaa = primary.loc[
        (primary["station_id"] == "AAA")
        & (primary["timestamp_utc"] == pd.Timestamp("2024-01-01T01:00:00Z"))
    ].iloc[0]

    assert primary.columns.tolist() == list(weather_primary_hourly_schema.required_columns)
    assert aaa["tmpf"] == 42.0
    assert aaa["p01i"] == 0.1
    assert aaa["tmpf_provenance"] == WeatherProvenance.LOCAL_TEMPORAL
    assert aaa["dwpf"] <= aaa["tmpf"]
    assert 0.0 <= aaa["relh"] <= 100.0
    assert aaa["tmpf_12h_complete_count"] == 12
    assert pd.notna(aaa["tmpf_12h"])
    assert report["accepted"] is True
    assert report["methods"]["spatial"]["tmpf"] >= 1
    assert report["future_observations_excluded"] >= 1


def test_build_primary_cube_can_run_one_branch_without_conditioned_outputs(tmp_path: Path) -> None:
    station_path = tmp_path / "station_metadata.parquet"
    _station_frame().to_parquet(station_path, index=False)
    clean_dir = tmp_path / "weather_irregular_clean"
    _write_clean_partition(
        clean_dir,
        [
            {
                "station_id": "AAA",
                "observed_at_utc": pd.Timestamp("2024-01-15T12:00:00Z"),
                "tmpf": 55.0,
                "dwpf": pd.NA,
                "feel_diff": pd.NA,
                "alti": pd.NA,
                "p01i": pd.NA,
            }
        ],
    )
    calibration_dir = _calibration_dir(tmp_path / "weather_calibration")
    output_dir = tmp_path / "weather_primary_hourly"

    report = build_weather_primary_hourly(
        clean_dir=clean_dir,
        calibration_dir=calibration_dir,
        station_metadata_path=station_path,
        output_dir=output_dir,
        variables=("tmpf",),
        start_utc=pd.Timestamp("2024-01-15T12:00:00Z"),
        end_utc=pd.Timestamp("2024-01-15T12:00:00Z"),
    )
    output = pd.read_parquet(next(output_dir.rglob("*.parquet")))

    assert report["accepted"] is True
    assert report["variables"] == ["tmpf"]
    assert output["tmpf"].notna().all()
    assert output["dwpf"].isna().all()
    assert output["p01i"].isna().all()
    assert set(output["dwpf_provenance"]) == {255}

    with pytest.raises(ValueError, match="causal mode only"):
        build_weather_primary_hourly(
            clean_dir=clean_dir,
            calibration_dir=calibration_dir,
            station_metadata_path=station_path,
            output_dir=tmp_path / "other_primary",
            variables=("tmpf",),
            start_utc=pd.Timestamp("2024-01-15T12:00:00Z"),
            end_utc=pd.Timestamp("2024-01-15T12:00:00Z"),
            mode=ObservationMode.REANALYSIS,
        )
