from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from bayes_demand_fcst.weather import (
    DEFAULT_TRAIN_END_UTC,
    build_weather_calibration,
    weather_calibration_harmonic_schema,
)

UTC = timezone.utc


def _write_partition(clean_dir: Path, state: str, rows: list[dict[str, object]]) -> Path:
    path = clean_dir / f"state={state}" / "year=2020" / "month=01" / f"part-{state}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def _training_rows() -> list[dict[str, object]]:
    start = datetime(2020, 1, 1, tzinfo=UTC)
    rows: list[dict[str, object]] = []
    for index in range(16):
        rows.append(
            {
                "station_id": "AAA",
                "observed_at_utc": start + timedelta(hours=3 * index),
                "tmpf": float(index % 5),
                "dwpf": pd.NA,
                "alti": pd.NA,
            }
        )
    rows.append(
        {
            "station_id": "AAA",
            "observed_at_utc": DEFAULT_TRAIN_END_UTC + timedelta(seconds=1),
            "tmpf": 9999.0,
            "dwpf": pd.NA,
            "alti": pd.NA,
        }
    )
    return rows


def test_build_weather_calibration_persists_frozen_fits_and_solver_config(tmp_path: Path) -> None:
    clean_dir = tmp_path / "weather_irregular_clean"
    source = _write_partition(clean_dir, "tx", _training_rows())
    output_dir = tmp_path / "weather_calibration"

    report = build_weather_calibration(
        clean_dir=clean_dir,
        output_dir=output_dir,
        train_end_utc=DEFAULT_TRAIN_END_UTC,
        variables=("tmpf",),
        min_observations=8,
    )

    coefficients = pd.read_parquet(output_dir / "harmonic_coefficients.parquet")
    persisted_report = json.loads((output_dir / "quality_report.json").read_text(encoding="utf-8"))
    solver_config = json.loads((output_dir / "solver_config.json").read_text(encoding="utf-8"))

    assert source.exists()
    assert coefficients.columns.tolist() == list(weather_calibration_harmonic_schema.required_columns)
    assert len(coefficients) == 1
    assert coefficients.loc[0, "station_id"] == "AAA"
    assert coefficients.loc[0, "variable"] == "tmpf"
    assert coefficients.loc[0, "sample_count"] == 16
    assert pd.Timestamp(coefficients.loc[0, "train_end_utc"]) == pd.Timestamp(DEFAULT_TRAIN_END_UTC)
    assert report == persisted_report
    assert report["accepted"] is True
    assert report["row_count_scanned"] == 17
    assert report["post_cutoff_rows_excluded"] == 1
    assert report["station_variable_fits"] == 1
    assert solver_config["train_end_utc"] == DEFAULT_TRAIN_END_UTC.isoformat()
    assert solver_config["covariance_parameters"]["tmpf"]["nugget_variance"] > 0.0
    assert solver_config["idw_parameters"]["prior_weight"] > 0.0


def test_build_weather_calibration_is_atomic_and_requires_overwrite(tmp_path: Path) -> None:
    clean_dir = tmp_path / "weather_irregular_clean"
    _write_partition(clean_dir, "tx", _training_rows())
    output_dir = tmp_path / "weather_calibration"
    output_dir.mkdir()
    (output_dir / "existing.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError, match="use --overwrite"):
        build_weather_calibration(
            clean_dir=clean_dir,
            output_dir=output_dir,
            train_end_utc=DEFAULT_TRAIN_END_UTC,
            variables=("tmpf",),
            min_observations=8,
        )


def test_default_train_cutoff_is_the_frozen_adr_cutoff() -> None:
    assert DEFAULT_TRAIN_END_UTC == datetime(2021, 12, 31, 23, 59, 59, tzinfo=UTC)
