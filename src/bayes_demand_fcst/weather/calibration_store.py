"""Stream the irregular clean store into frozen Gate 2 calibration artifacts."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

import numpy as np
import pandas as pd

from bayes_demand_fcst.weather.calibration import (
    CALIBRATION_REFERENCE_UTC,
    DEFAULT_COVARIANCE_PRIORS,
    DEFAULT_HARMONIC_PERIODS_HOURS,
    DEFAULT_IDW_PARAMETERS,
    DEFAULT_TRAIN_END_UTC,
    harmonic_design_matrix,
)
from bayes_demand_fcst.weather.causality import require_utc_datetime
from bayes_demand_fcst.weather.contracts import weather_calibration_harmonic_schema

CALIBRATION_VARIABLES = ("tmpf", "dwpf", "alti")
MIN_CALIBRATION_OBSERVATIONS = 1_000


@dataclass(slots=True)
class _SufficientStatistics:
    count: int
    normal_matrix: np.ndarray
    normal_rhs: np.ndarray

    @classmethod
    def create(cls, coefficient_count: int) -> _SufficientStatistics:
        return cls(
            count=0,
            normal_matrix=np.zeros((coefficient_count, coefficient_count), dtype=np.float64),
            normal_rhs=np.zeros(coefficient_count, dtype=np.float64),
        )

    def add(self, design: np.ndarray, values: np.ndarray) -> None:
        self.normal_matrix += design.T @ design
        self.normal_rhs += design.T @ values
        self.count += len(values)


def _find_clean_partitions(clean_dir: Path) -> tuple[Path, ...]:
    paths = tuple(sorted(clean_dir.rglob("*.parquet")))
    if not paths:
        raise FileNotFoundError(f"No clean weather Parquet files found under {clean_dir}")
    return paths


def _validate_variables(variables: Sequence[str]) -> tuple[str, ...]:
    selected = tuple(dict.fromkeys(variable.strip() for variable in variables))
    if not selected:
        raise ValueError("At least one calibration variable is required")
    unknown = sorted(set(selected) - set(CALIBRATION_VARIABLES))
    if unknown:
        raise ValueError(f"Unsupported harmonic calibration variables: {', '.join(unknown)}")
    return selected


def _require_utc_series(series: pd.Series) -> pd.Series:
    timestamps = pd.to_datetime(series, errors="raise")
    if not hasattr(timestamps.dtype, "tz") or str(timestamps.dtype.tz) != "UTC":
        raise ValueError("Clean-store observed_at_utc must already be timezone-aware UTC")
    return timestamps


def _serialize_covariance_priors() -> dict[str, dict[str, float | str]]:
    return {
        variable: {
            "kernel": parameters.kernel.value,
            "variance": parameters.variance,
            "spatial_length_m": parameters.spatial_length_m,
            "temporal_length_hours": parameters.temporal_length_hours,
            "nugget_variance": parameters.nugget_variance,
        }
        for variable, parameters in DEFAULT_COVARIANCE_PRIORS.items()
    }


def _fit_accumulators(
    accumulators: dict[tuple[str, str], _SufficientStatistics],
    train_end_utc: datetime,
    min_observations: int,
) -> tuple[pd.DataFrame, dict[str, int]]:
    coefficient_count = 1 + 2 * len(DEFAULT_HARMONIC_PERIODS_HOURS)
    rows: list[dict[str, Any]] = []
    skipped_low_sample = 0
    skipped_rank_deficient = 0
    for (station_id, variable), statistics in sorted(accumulators.items()):
        if statistics.count < min_observations:
            skipped_low_sample += 1
            continue
        rank = int(np.linalg.matrix_rank(statistics.normal_matrix))
        if rank < coefficient_count:
            skipped_rank_deficient += 1
            continue
        coefficients, _, _, _ = np.linalg.lstsq(
            statistics.normal_matrix,
            statistics.normal_rhs,
            rcond=None,
        )
        if not np.isfinite(coefficients).all():
            raise ValueError(f"Non-finite harmonic coefficients for {station_id}/{variable}")
        normal_condition = float(np.linalg.cond(statistics.normal_matrix))
        row: dict[str, Any] = {
            "station_id": station_id,
            "variable": variable,
            "train_end_utc": train_end_utc,
            "reference_utc": CALIBRATION_REFERENCE_UTC,
            "sample_count": statistics.count,
            "design_matrix_condition": float(np.sqrt(normal_condition)),
        }
        for index, coefficient in enumerate(coefficients):
            row[f"coefficient_{index:02d}"] = float(coefficient)
        rows.append(row)

    columns = list(weather_calibration_harmonic_schema.required_columns)
    fits = pd.DataFrame(rows, columns=columns)
    if not fits.empty:
        fits["sample_count"] = fits["sample_count"].astype("int64")
    return fits, {
        "skipped_station_variables_below_minimum": skipped_low_sample,
        "skipped_station_variables_rank_deficient": skipped_rank_deficient,
    }


def build_weather_calibration(
    clean_dir: Path,
    output_dir: Path,
    train_end_utc: datetime = DEFAULT_TRAIN_END_UTC,
    *,
    variables: Sequence[str] = CALIBRATION_VARIABLES,
    periods_hours: Sequence[float] = DEFAULT_HARMONIC_PERIODS_HOURS,
    min_observations: int = MIN_CALIBRATION_OBSERVATIONS,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Fit frozen harmonic parameters from train-window rows without loading the archive at once."""
    cutoff = require_utc_datetime(train_end_utc, "train_end_utc")
    selected_variables = _validate_variables(variables)
    periods = tuple(float(period) for period in periods_hours)
    if periods != tuple(DEFAULT_HARMONIC_PERIODS_HOURS):
        raise ValueError("Gate 2 harmonic artifact schema currently requires the default periods")
    if min_observations < 1:
        raise ValueError("min_observations must be positive")
    paths = _find_clean_partitions(clean_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        if not overwrite:
            raise FileExistsError(f"Output directory is not empty: {output_dir}; use --overwrite to replace it")

    coefficient_count = 1 + 2 * len(periods)
    accumulators: dict[tuple[str, str], _SufficientStatistics] = {}
    rows_scanned = 0
    rows_in_window = 0
    rows_after_cutoff = 0
    variable_observations = {variable: 0 for variable in selected_variables}

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    try:
        required_columns = ("station_id", "observed_at_utc", *selected_variables)
        for path in paths:
            frame = pd.read_parquet(path, columns=list(required_columns))
            missing = sorted(set(required_columns) - set(frame.columns))
            if missing:
                raise ValueError(f"Clean partition {path} is missing columns: {', '.join(missing)}")
            frame["observed_at_utc"] = _require_utc_series(frame["observed_at_utc"])
            rows_scanned += len(frame)
            eligible = frame.loc[frame["observed_at_utc"] <= cutoff].copy()
            rows_in_window += len(eligible)
            rows_after_cutoff += int(len(frame) - len(eligible))
            if eligible.empty:
                continue

            design = harmonic_design_matrix(
                tuple(eligible["observed_at_utc"]),
                periods,
                reference_utc=CALIBRATION_REFERENCE_UTC,
            )
            station_positions = eligible.groupby("station_id", sort=False, dropna=False).indices
            for variable in selected_variables:
                values = pd.to_numeric(eligible[variable], errors="coerce").to_numpy(dtype=np.float64)
                for station_id, positions in station_positions.items():
                    position_array = np.asarray(positions, dtype=np.int64)
                    valid = np.isfinite(values[position_array])
                    if not valid.any():
                        continue
                    station_values = values[position_array[valid]]
                    station_design = design[position_array[valid]]
                    key = (str(station_id), variable)
                    accumulator = accumulators.get(key)
                    if accumulator is None:
                        accumulator = _SufficientStatistics.create(coefficient_count)
                        accumulators[key] = accumulator
                    accumulator.add(station_design, station_values)
                    variable_observations[variable] += int(valid.sum())

        fits, fit_quality = _fit_accumulators(accumulators, cutoff, min_observations)
        weather_calibration_harmonic_schema.validate_columns(fits.columns)
        if fits.empty:
            raise ValueError(
                f"No station/variable harmonic fits met the {min_observations}-observation Gate 2 minimum"
            )
        fits.to_parquet(
            staging / "harmonic_coefficients.parquet",
            index=False,
            engine="pyarrow",
            compression="zstd",
        )

        solver_config = {
            "schema_version": 1,
            "train_end_utc": cutoff.isoformat(),
            "reference_utc": CALIBRATION_REFERENCE_UTC.isoformat(),
            "harmonic_periods_hours": list(periods),
            "covariance_parameter_source": "adr_initial_prior_frozen_for_gate_2",
            "covariance_parameters": _serialize_covariance_priors(),
            "idw_parameters": dict(DEFAULT_IDW_PARAMETERS),
        }
        (staging / "solver_config.json").write_text(
            json.dumps(solver_config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        report: dict[str, Any] = {
            "schema": {
                "name": weather_calibration_harmonic_schema.name,
                "version": weather_calibration_harmonic_schema.version,
                "primary_key": list(weather_calibration_harmonic_schema.primary_key),
            },
            "accepted": True,
            "train_end_utc": cutoff.isoformat(),
            "reference_utc": CALIBRATION_REFERENCE_UTC.isoformat(),
            "files_processed": len(paths),
            "row_count_scanned": rows_scanned,
            "rows_in_train_window": rows_in_window,
            "post_cutoff_rows_excluded": rows_after_cutoff,
            "station_variable_fits": len(fits),
            "station_count": int(fits["station_id"].nunique()),
            "variable_observations_in_window": variable_observations,
            "quality": fit_quality,
        }
        (staging / "quality_report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        if output_dir.exists():
            shutil.rmtree(output_dir)
        staging.rename(output_dir)
        return report
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
