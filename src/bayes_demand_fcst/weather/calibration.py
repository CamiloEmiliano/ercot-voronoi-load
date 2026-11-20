"""Frozen training-window harmonic calibration helpers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import math

import numpy as np
from numpy.typing import NDArray

from bayes_demand_fcst.weather.causality import require_utc_datetime
from bayes_demand_fcst.weather.covariance import CovarianceKernel, SpaceTimeCovariance

DEFAULT_HARMONIC_PERIODS_HOURS = (365.2425 * 24.0, 24.0, 12.0)
DEFAULT_TRAIN_END_UTC = datetime(2021, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
CALIBRATION_REFERENCE_UTC = datetime(2014, 1, 1, tzinfo=timezone.utc)
DEFAULT_COVARIANCE_PRIORS = {
    "tmpf": SpaceTimeCovariance(
        variance=25.0,
        spatial_length_m=200_000.0,
        temporal_length_hours=12.0,
        nugget_variance=1.0,
        kernel=CovarianceKernel.MATERN_32,
    ),
    "dwpf": SpaceTimeCovariance(
        variance=25.0,
        spatial_length_m=200_000.0,
        temporal_length_hours=12.0,
        nugget_variance=1.0,
        kernel=CovarianceKernel.MATERN_32,
    ),
    "feel_diff": SpaceTimeCovariance(
        variance=16.0,
        spatial_length_m=150_000.0,
        temporal_length_hours=8.0,
        nugget_variance=1.0,
        kernel=CovarianceKernel.EXPONENTIAL,
    ),
    "alti": SpaceTimeCovariance(
        variance=0.02,
        spatial_length_m=400_000.0,
        temporal_length_hours=18.0,
        nugget_variance=0.0004,
        kernel=CovarianceKernel.MATERN_32,
    ),
    "delta_p": SpaceTimeCovariance(
        variance=2.0,
        spatial_length_m=400_000.0,
        temporal_length_hours=18.0,
        nugget_variance=0.09,
        kernel=CovarianceKernel.MATERN_32,
    ),
    "cloud_frac": SpaceTimeCovariance(
        variance=0.08,
        spatial_length_m=100_000.0,
        temporal_length_hours=4.0,
        nugget_variance=0.01,
        kernel=CovarianceKernel.EXPONENTIAL,
    ),
}
DEFAULT_IDW_PARAMETERS = {
    "spatial_length_m": 45_000.0,
    "prior_weight": 0.15,
    "wet_probability_threshold": 0.5,
}


@dataclass(frozen=True, slots=True)
class HarmonicModelFit:
    """One deterministic station/variable harmonic calibration."""

    station_id: str
    variable: str
    train_end_utc: datetime
    reference_utc: datetime
    periods_hours: tuple[float, ...]
    coefficients: tuple[float, ...]
    sample_count: int
    design_matrix_condition: float

    def __post_init__(self) -> None:
        if not self.station_id or not self.variable:
            raise ValueError("station_id and variable must be non-empty")
        object.__setattr__(
            self,
            "train_end_utc",
            require_utc_datetime(self.train_end_utc, "train_end_utc"),
        )
        object.__setattr__(
            self,
            "reference_utc",
            require_utc_datetime(self.reference_utc, "reference_utc"),
        )
        if self.sample_count < 1:
            raise ValueError("Harmonic model sample_count must be positive")
        expected = 1 + 2 * len(self.periods_hours)
        if len(self.coefficients) != expected:
            raise ValueError(f"Expected {expected} harmonic coefficients, found {len(self.coefficients)}")
        if not all(math.isfinite(value) for value in self.coefficients):
            raise ValueError("Harmonic coefficients must be finite")
        if not math.isfinite(self.design_matrix_condition) or self.design_matrix_condition < 1.0:
            raise ValueError("Harmonic design condition number must be finite and at least one")


def harmonic_design_matrix(
    observed_at_utc: Sequence[datetime],
    periods_hours: Sequence[float],
    *,
    reference_utc: datetime,
) -> NDArray[np.float64]:
    """Build intercept plus cosine/sine columns for validated cyclic periods."""
    if not periods_hours:
        raise ValueError("At least one harmonic period is required")
    if any(not math.isfinite(period) or period <= 0.0 for period in periods_hours):
        raise ValueError("Harmonic periods must be finite and positive")
    reference = require_utc_datetime(reference_utc, "reference_utc")
    normalized = tuple(require_utc_datetime(value, "observed_at_utc") for value in observed_at_utc)
    elapsed_hours = np.asarray(
        [(value - reference).total_seconds() / 3600.0 for value in normalized],
        dtype=np.float64,
    )
    columns: list[NDArray[np.float64]] = [np.ones(len(normalized), dtype=np.float64)]
    for period in periods_hours:
        phase = 2.0 * np.pi * elapsed_hours / period
        columns.extend((np.cos(phase), np.sin(phase)))
    return np.column_stack(columns)


def fit_harmonic_model(
    *,
    station_id: str,
    variable: str,
    observed_at_utc: Sequence[datetime],
    values: Sequence[float],
    train_end_utc: datetime,
    periods_hours: Sequence[float] = DEFAULT_HARMONIC_PERIODS_HOURS,
    min_observations: int = 8,
) -> HarmonicModelFit:
    """Fit a deterministic harmonic model, rejecting every post-cutoff sample."""
    cutoff = require_utc_datetime(train_end_utc, "train_end_utc")
    if len(observed_at_utc) != len(values):
        raise ValueError("observed_at_utc and values must have equal lengths")
    if min_observations < 1:
        raise ValueError("min_observations must be positive")
    timestamps = tuple(require_utc_datetime(value, "observed_at_utc") for value in observed_at_utc)
    future = tuple(timestamp for timestamp in timestamps if timestamp > cutoff)
    if future:
        raise ValueError(
            f"Calibration input contains observations outside train window after {cutoff.isoformat()}"
        )

    finite_rows: list[tuple[datetime, float]] = []
    for timestamp, raw_value in zip(timestamps, values, strict=True):
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            finite_rows.append((timestamp, value))
    if len(finite_rows) < min_observations:
        raise ValueError(
            f"Harmonic calibration requires at least {min_observations} finite observations; "
            f"found {len(finite_rows)}"
        )
    fit_timestamps = tuple(timestamp for timestamp, _ in finite_rows)
    fit_values = np.asarray(tuple(value for _, value in finite_rows), dtype=np.float64)
    reference = min(fit_timestamps)
    periods = tuple(float(period) for period in periods_hours)
    design = harmonic_design_matrix(fit_timestamps, periods, reference_utc=reference)
    if np.linalg.matrix_rank(design) != design.shape[1]:
        raise ValueError("Harmonic design matrix is rank deficient")
    coefficients, _, _, _ = np.linalg.lstsq(design, fit_values, rcond=None)
    return HarmonicModelFit(
        station_id=station_id,
        variable=variable,
        train_end_utc=cutoff,
        reference_utc=reference,
        periods_hours=periods,
        coefficients=tuple(float(value) for value in coefficients),
        sample_count=len(finite_rows),
        design_matrix_condition=float(np.linalg.cond(design)),
    )


def predict_harmonic_model(
    fit: HarmonicModelFit,
    observed_at_utc: Sequence[datetime],
) -> NDArray[np.float64]:
    """Apply frozen harmonic coefficients at in-sample or future timestamps."""
    design = harmonic_design_matrix(
        observed_at_utc,
        fit.periods_hours,
        reference_utc=fit.reference_utc,
    )
    return design @ np.asarray(fit.coefficients, dtype=np.float64)
