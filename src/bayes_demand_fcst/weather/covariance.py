"""Positive-definite space-time covariance kernels for weather imputation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
import math

import numpy as np
from numpy.typing import NDArray

from bayes_demand_fcst.weather.space_time import SpaceTimeObservation, SpaceTimePoint


class CovarianceKernel(StrEnum):
    """Supported stationary covariance kernels."""

    MATERN_32 = "matern32"
    EXPONENTIAL = "exponential"


@dataclass(frozen=True, slots=True)
class SpaceTimeCovariance:
    """Validated anisotropic space-time covariance parameters."""

    variance: float
    spatial_length_m: float
    temporal_length_hours: float
    nugget_variance: float
    kernel: CovarianceKernel = CovarianceKernel.MATERN_32

    def __post_init__(self) -> None:
        if not isinstance(self.kernel, CovarianceKernel):
            raise TypeError("kernel must be a CovarianceKernel")
        if not math.isfinite(self.variance) or self.variance <= 0.0:
            raise ValueError("Covariance variance must be finite and positive")
        if not math.isfinite(self.spatial_length_m) or self.spatial_length_m <= 0.0:
            raise ValueError("Covariance spatial length must be finite and positive")
        if not math.isfinite(self.temporal_length_hours) or self.temporal_length_hours <= 0.0:
            raise ValueError("Covariance temporal length must be finite and positive")
        if not math.isfinite(self.nugget_variance) or self.nugget_variance <= 0.0:
            raise ValueError("Covariance nugget must be finite and strictly positive")

    def kernel_value(self, spatial_distance_m: float, temporal_distance_hours: float) -> float:
        """Return covariance for positive, dimensionless space-time separation."""
        if spatial_distance_m < 0.0 or temporal_distance_hours < 0.0:
            raise ValueError("Covariance separations cannot be negative")
        scaled_distance = math.hypot(
            spatial_distance_m / self.spatial_length_m,
            temporal_distance_hours / self.temporal_length_hours,
        )
        if self.kernel is CovarianceKernel.MATERN_32:
            scaled = math.sqrt(3.0) * scaled_distance
            return self.variance * (1.0 + scaled) * math.exp(-scaled)
        return self.variance * math.exp(-scaled_distance)


def _temporal_hours(first: SpaceTimePoint | SpaceTimeObservation, second: SpaceTimePoint | SpaceTimeObservation) -> float:
    return abs((first.observed_at_utc - second.observed_at_utc).total_seconds()) / 3600.0


def build_covariance_matrix(
    observations: Sequence[SpaceTimeObservation],
    parameters: SpaceTimeCovariance,
) -> NDArray[np.float64]:
    """Build the symmetric observation covariance including a diagonal nugget."""
    count = len(observations)
    matrix = np.empty((count, count), dtype=np.float64)
    for row, left in enumerate(observations):
        for column in range(row, count):
            right = observations[column]
            spatial_distance = math.hypot(left.x_m - right.x_m, left.y_m - right.y_m)
            temporal_distance = _temporal_hours(left, right)
            value = parameters.kernel_value(spatial_distance, temporal_distance)
            if row == column:
                value += parameters.nugget_variance
            matrix[row, column] = value
            matrix[column, row] = value
    return matrix


def covariance_to_target(
    observations: Sequence[SpaceTimeObservation],
    target: SpaceTimePoint,
    parameters: SpaceTimeCovariance,
) -> NDArray[np.float64]:
    """Return causal target covariances, rejecting future observations."""
    values = np.empty(len(observations), dtype=np.float64)
    for index, observation in enumerate(observations):
        if observation.observed_at_utc > target.observed_at_utc:
            raise ValueError(
                "Cannot use a future observation in covariance prediction: "
                f"{observation.observed_at_utc.isoformat()} > {target.observed_at_utc.isoformat()}"
            )
        spatial_distance = math.hypot(observation.x_m - target.x_m, observation.y_m - target.y_m)
        temporal_distance = (target.observed_at_utc - observation.observed_at_utc).total_seconds() / 3600.0
        values[index] = parameters.kernel_value(spatial_distance, temporal_distance)
    return values
