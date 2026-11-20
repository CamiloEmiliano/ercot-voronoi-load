from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from bayes_demand_fcst.weather import (
    CovarianceKernel,
    SpaceTimeCovariance,
    SpaceTimeObservation,
    SpaceTimePoint,
    build_covariance_matrix,
    covariance_to_target,
    fit_harmonic_model,
    nearest_causal_observation,
    ordinary_kriging,
    predict_harmonic_model,
    prior_shrunk_idw,
    select_causal_neighborhood,
    simple_kriging,
)

UTC = timezone.utc


@pytest.fixture
def reference_time() -> datetime:
    return datetime(2020, 1, 1, tzinfo=UTC)


def test_harmonic_fit_recovers_known_signal_and_rejects_post_cutoff(reference_time: datetime) -> None:
    timestamps = tuple(reference_time + timedelta(hours=index * 3) for index in range(16))
    values = tuple(
        12.5
        + 2.0 * math.cos(2.0 * math.pi * (timestamp - reference_time).total_seconds() / (24.0 * 3600.0))
        - 1.5 * math.sin(2.0 * math.pi * (timestamp - reference_time).total_seconds() / (24.0 * 3600.0))
        for timestamp in timestamps
    )

    fit = fit_harmonic_model(
        station_id="AAA",
        variable="tmpf",
        observed_at_utc=timestamps,
        values=values,
        train_end_utc=timestamps[-1],
        periods_hours=(24.0,),
        min_observations=8,
    )
    predicted = predict_harmonic_model(fit, timestamps)

    assert np.allclose(predicted, values, atol=1e-10)
    assert fit.sample_count == len(timestamps)
    assert math.isfinite(fit.design_matrix_condition)
    assert fit.design_matrix_condition >= 1.0

    with pytest.raises(ValueError, match="train window"):
        fit_harmonic_model(
            station_id="AAA",
            variable="tmpf",
            observed_at_utc=(*timestamps, timestamps[-1] + timedelta(seconds=1)),
            values=(*values, 9999.0),
            train_end_utc=timestamps[-1],
            periods_hours=(24.0,),
            min_observations=8,
        )


def test_covariance_parameters_and_matrix_are_strictly_positive_definite(reference_time: datetime) -> None:
    covariance = SpaceTimeCovariance(
        variance=4.0,
        spatial_length_m=200_000.0,
        temporal_length_hours=12.0,
        nugget_variance=0.1,
        kernel=CovarianceKernel.MATERN_32,
    )
    observations = (
        SpaceTimeObservation("AAA", 0.0, 0.0, reference_time - timedelta(hours=2), 1.0),
        SpaceTimeObservation("BBB", 20_000.0, 0.0, reference_time - timedelta(hours=1), 2.0),
        SpaceTimeObservation("CCC", 20_000.0, 0.0, reference_time - timedelta(hours=1), 3.0),
        SpaceTimeObservation("DDD", 20_000.0, 20_000.0, reference_time, 4.0),
    )

    matrix = build_covariance_matrix(observations, covariance)
    eigenvalues = np.linalg.eigvalsh(matrix)
    target = SpaceTimePoint(5_000.0, 5_000.0, reference_time)

    assert np.all(eigenvalues > 0.0)
    assert matrix.shape == (4, 4)
    assert np.allclose(matrix, matrix.T)
    assert covariance_to_target(observations, target, covariance).shape == (4,)

    with pytest.raises(ValueError, match="nugget"):
        SpaceTimeCovariance(
            variance=4.0,
            spatial_length_m=200_000.0,
            temporal_length_hours=12.0,
            nugget_variance=0.0,
        )


def test_covariance_and_neighborhood_reject_or_filter_future_observations(reference_time: datetime) -> None:
    covariance = SpaceTimeCovariance(
        variance=1.0,
        spatial_length_m=100_000.0,
        temporal_length_hours=6.0,
        nugget_variance=0.05,
    )
    target = SpaceTimePoint(0.0, 0.0, reference_time)
    observations = (
        SpaceTimeObservation("AAA", 10.0, 0.0, reference_time - timedelta(hours=2), 1.0),
        SpaceTimeObservation("AAA", 10.0, 0.0, reference_time - timedelta(minutes=30), 1.5),
        SpaceTimeObservation("BBB", 20.0, 0.0, reference_time - timedelta(minutes=20), 2.0),
        SpaceTimeObservation("CCC", 30.0, 0.0, reference_time + timedelta(minutes=1), 99.0),
    )

    selected = select_causal_neighborhood(
        observations,
        target,
        max_stations=2,
        max_observations_per_station=1,
        lookback=timedelta(hours=3),
    )

    assert [(item.station_id, item.value) for item in selected] == [("AAA", 1.5), ("BBB", 2.0)]
    with pytest.raises(ValueError, match="future observation"):
        covariance_to_target((observations[-1],), target, covariance)


def test_spatial_temporal_neighborhood_is_station_stratified(reference_time: datetime) -> None:
    target = SpaceTimePoint(0.0, 0.0, reference_time)
    observations = tuple(
        SpaceTimeObservation("AAA", 1_000.0, 0.0, reference_time - timedelta(minutes=minute), float(minute))
        for minute in (10, 20, 30)
    ) + (
        SpaceTimeObservation("BBB", 100_000.0, 0.0, reference_time - timedelta(minutes=40), 40.0),
    )

    selected = select_causal_neighborhood(
        observations,
        target,
        max_stations=2,
        max_observations_per_station=2,
        lookback=timedelta(hours=1),
    )

    assert [item.station_id for item in selected].count("AAA") == 2
    assert [item.station_id for item in selected].count("BBB") == 1


def test_kriging_solvers_are_deterministic_and_ordinary_weights_sum_to_one(reference_time: datetime) -> None:
    covariance = SpaceTimeCovariance(
        variance=2.0,
        spatial_length_m=200_000.0,
        temporal_length_hours=8.0,
        nugget_variance=0.1,
        kernel=CovarianceKernel.EXPONENTIAL,
    )
    target = SpaceTimePoint(0.0, 0.0, reference_time)
    observations = (
        SpaceTimeObservation("AAA", -25_000.0, 0.0, reference_time - timedelta(hours=1), 4.0),
        SpaceTimeObservation("BBB", 25_000.0, 0.0, reference_time - timedelta(hours=1), 6.0),
    )

    simple = simple_kriging(observations, target, covariance, prior_mean=5.0)
    ordinary = ordinary_kriging(observations, target, covariance)

    assert simple.prediction == pytest.approx(5.0)
    assert ordinary.prediction == pytest.approx(5.0)
    assert sum(ordinary.weights) == pytest.approx(1.0, abs=1e-12)
    assert simple.variance >= 0.0
    assert ordinary.variance >= 0.0
    assert simple.weights == pytest.approx(simple_kriging(observations, target, covariance, prior_mean=5.0).weights)


def test_ordinary_kriging_falls_back_gracefully_to_one_observation(reference_time: datetime) -> None:
    covariance = SpaceTimeCovariance(
        variance=2.0,
        spatial_length_m=200_000.0,
        temporal_length_hours=8.0,
        nugget_variance=0.1,
    )
    target = SpaceTimePoint(0.0, 0.0, reference_time)
    observation = SpaceTimeObservation("AAA", 0.0, 0.0, reference_time, 7.0)

    result = ordinary_kriging((observation,), target, covariance)

    assert result.prediction == 7.0
    assert result.weights == (1.0,)
    assert result.method == "ordinary_kriging_fallback"
    assert result.variance >= 0.0


def test_prior_shrunk_idw_uses_positive_background_weight() -> None:
    result = prior_shrunk_idw(
        values=(1.0,),
        distances_m=(45_000.0,),
        prior_value=0.0,
        spatial_length_m=45_000.0,
        prior_weight=0.15,
    )

    expected = math.exp(-1.0) / (0.15 + math.exp(-1.0))
    assert result == pytest.approx(expected)
    assert result < 1.0


def test_nearest_causal_observation_never_uses_future_data(reference_time: datetime) -> None:
    target = reference_time
    observations = (
        SpaceTimeObservation("AAA", 0.0, 0.0, target - timedelta(minutes=55), 4.0),
        SpaceTimeObservation("AAA", 0.0, 0.0, target - timedelta(minutes=5), 5.0),
        SpaceTimeObservation("AAA", 0.0, 0.0, target + timedelta(minutes=5), 6.0),
    )

    selected = nearest_causal_observation(observations, target, timedelta(minutes=10))

    assert selected is not None
    assert selected.value == 5.0
    assert selected.observed_at_utc <= target
