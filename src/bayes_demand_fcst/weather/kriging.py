"""SPD Cholesky simple and ordinary kriging solvers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import math

import numpy as np
from numpy.typing import NDArray

from bayes_demand_fcst.weather.covariance import (
    SpaceTimeCovariance,
    build_covariance_matrix,
    covariance_to_target,
)
from bayes_demand_fcst.weather.space_time import SpaceTimeObservation, SpaceTimePoint


@dataclass(frozen=True, slots=True)
class KrigingResult:
    """Compact deterministic prediction result."""

    prediction: float
    variance: float
    weights: tuple[float, ...]
    method: str

    def __post_init__(self) -> None:
        if not math.isfinite(self.prediction):
            raise ValueError("Kriging prediction must be finite")
        if not math.isfinite(self.variance) or self.variance < 0.0:
            raise ValueError("Kriging variance must be finite and non-negative")


def _solve_spd(matrix: NDArray[np.float64], right_hand_side: NDArray[np.float64]) -> NDArray[np.float64]:
    lower = np.linalg.cholesky(matrix)
    forward = np.linalg.solve(lower, right_hand_side)
    return np.linalg.solve(lower.T, forward)


def _prepare(
    observations: Sequence[SpaceTimeObservation],
    target: SpaceTimePoint,
    covariance: SpaceTimeCovariance,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    if not observations:
        raise ValueError("Kriging requires at least one observation")
    values = np.asarray(tuple(observation.value for observation in observations), dtype=np.float64)
    matrix = build_covariance_matrix(observations, covariance)
    target_covariance = covariance_to_target(observations, target, covariance)
    return values, matrix, target_covariance


def simple_kriging(
    observations: Sequence[SpaceTimeObservation],
    target: SpaceTimePoint,
    covariance: SpaceTimeCovariance,
    *,
    prior_mean: float = 0.0,
) -> KrigingResult:
    """Zero-mean/simple kriging around an explicit deterministic prior."""
    if not math.isfinite(prior_mean):
        raise ValueError("prior_mean must be finite")
    values, matrix, target_covariance = _prepare(observations, target, covariance)
    weights_array = _solve_spd(matrix, target_covariance)
    prediction = float(prior_mean + weights_array @ (values - prior_mean))
    variance = float(covariance.variance - target_covariance @ weights_array)
    return KrigingResult(
        prediction=prediction,
        variance=max(0.0, variance),
        weights=tuple(float(weight) for weight in weights_array),
        method="simple_kriging",
    )


def ordinary_kriging(
    observations: Sequence[SpaceTimeObservation],
    target: SpaceTimePoint,
    covariance: SpaceTimeCovariance,
) -> KrigingResult:
    """Ordinary kriging via SPD Cholesky solves and a Schur complement."""
    values, matrix, target_covariance = _prepare(observations, target, covariance)
    if len(observations) == 1:
        return KrigingResult(
            prediction=float(values[0]),
            variance=float(covariance.variance + covariance.nugget_variance),
            weights=(1.0,),
            method="ordinary_kriging_fallback",
        )

    ones = np.ones(len(observations), dtype=np.float64)
    inverse_target = _solve_spd(matrix, target_covariance)
    inverse_ones = _solve_spd(matrix, ones)
    schur_complement = float(ones @ inverse_ones)
    if not math.isfinite(schur_complement) or schur_complement <= 0.0:
        raise ValueError("Ordinary-kriging Schur complement must be positive")
    multiplier = (1.0 - float(ones @ inverse_target)) / schur_complement
    weights_array = inverse_target + multiplier * inverse_ones
    prediction = float(weights_array @ values)
    variance = float(
        covariance.variance
        - 2.0 * (weights_array @ target_covariance)
        + weights_array @ matrix @ weights_array
    )
    return KrigingResult(
        prediction=prediction,
        variance=max(0.0, variance),
        weights=tuple(float(weight) for weight in weights_array),
        method="ordinary_kriging",
    )
