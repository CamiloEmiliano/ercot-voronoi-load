"""Causal and station-stratified neighborhood selection."""

from __future__ import annotations

from collections.abc import Sequence
from collections import defaultdict
from datetime import datetime, timedelta
import math

from bayes_demand_fcst.weather.causality import require_utc_datetime
from bayes_demand_fcst.weather.space_time import SpaceTimeObservation, SpaceTimePoint


def select_causal_neighborhood(
    observations: Sequence[SpaceTimeObservation],
    target: SpaceTimePoint,
    *,
    max_stations: int = 10,
    max_observations_per_station: int = 4,
    lookback: timedelta = timedelta(hours=24),
) -> tuple[SpaceTimeObservation, ...]:
    """Select nearest stations, then recent causal observations within each station."""
    if max_stations < 1:
        raise ValueError("max_stations must be positive")
    if max_observations_per_station < 1:
        raise ValueError("max_observations_per_station must be positive")
    if lookback <= timedelta(0):
        raise ValueError("lookback must be positive")

    earliest = target.observed_at_utc - lookback
    eligible = tuple(
        observation
        for observation in observations
        if earliest <= observation.observed_at_utc <= target.observed_at_utc
    )
    by_station: dict[str, list[SpaceTimeObservation]] = defaultdict(list)
    for observation in eligible:
        by_station[observation.station_id].append(observation)

    ordered_stations = sorted(
        by_station,
        key=lambda station_id: (
            min(
                math.hypot(
                    observation.x_m - target.x_m,
                    observation.y_m - target.y_m,
                )
                for observation in by_station[station_id]
            ),
            station_id,
        ),
    )[:max_stations]

    selected: list[SpaceTimeObservation] = []
    for station_id in ordered_stations:
        recent = sorted(
            by_station[station_id],
            key=lambda observation: observation.observed_at_utc,
            reverse=True,
        )
        selected.extend(recent[:max_observations_per_station])
    return tuple(selected)


def nearest_causal_observation(
    observations: Sequence[SpaceTimeObservation],
    target_at_utc: datetime,
    max_lag: timedelta,
) -> SpaceTimeObservation | None:
    """Return the closest prior observation, with an exact-hour preference on ties."""
    target = require_utc_datetime(target_at_utc, "target_at_utc")
    if max_lag <= timedelta(0):
        raise ValueError("max_lag must be positive")
    candidates = tuple(
        observation
        for observation in observations
        if timedelta(0) <= target - observation.observed_at_utc <= max_lag
    )
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda observation: (
            target - observation.observed_at_utc,
            math.hypot(observation.x_m, observation.y_m),
            observation.station_id,
        ),
    )
