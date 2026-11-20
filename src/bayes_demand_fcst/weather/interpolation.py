"""Bounded, prior-regularized interpolation helpers."""

from __future__ import annotations

from collections.abc import Sequence
import math

import numpy as np


def prior_shrunk_idw(
    values: Sequence[float],
    distances_m: Sequence[float],
    *,
    prior_value: float,
    spatial_length_m: float,
    prior_weight: float,
) -> float:
    """Exponential-decay IDW shrunk toward a strictly weighted background prior."""
    if len(values) != len(distances_m):
        raise ValueError("values and distances_m must have equal lengths")
    if not math.isfinite(prior_value):
        raise ValueError("prior_value must be finite")
    if not math.isfinite(spatial_length_m) or spatial_length_m <= 0.0:
        raise ValueError("spatial_length_m must be finite and positive")
    if not math.isfinite(prior_weight) or prior_weight <= 0.0:
        raise ValueError("prior_weight must be finite and strictly positive")
    if not values:
        return prior_value

    value_array = np.asarray(tuple(values), dtype=np.float64)
    distance_array = np.asarray(tuple(distances_m), dtype=np.float64)
    if not np.isfinite(value_array).all():
        raise ValueError("IDW values must be finite")
    if not np.isfinite(distance_array).all() or (distance_array < 0.0).any():
        raise ValueError("IDW distances must be finite and non-negative")
    weights = np.exp(-distance_array / spatial_length_m)
    denominator = prior_weight + float(weights.sum())
    return float((prior_weight * prior_value + weights @ value_array) / denominator)
