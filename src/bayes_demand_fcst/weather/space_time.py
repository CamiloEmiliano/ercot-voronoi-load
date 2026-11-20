"""Immutable space-time records shared by weather neighborhoods and solvers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math

from bayes_demand_fcst.weather.causality import require_utc_datetime


@dataclass(frozen=True, slots=True)
class SpaceTimePoint:
    """A target or support point in projected space and UTC time."""

    x_m: float
    y_m: float
    observed_at_utc: datetime

    def __post_init__(self) -> None:
        if not math.isfinite(self.x_m) or not math.isfinite(self.y_m):
            raise ValueError("Projected coordinates must be finite")
        object.__setattr__(
            self,
            "observed_at_utc",
            require_utc_datetime(self.observed_at_utc, "observed_at_utc"),
        )


@dataclass(frozen=True, slots=True)
class SpaceTimeObservation:
    """One finite station observation with an exact projected location and UTC time."""

    station_id: str
    x_m: float
    y_m: float
    observed_at_utc: datetime
    value: float

    def __post_init__(self) -> None:
        if not self.station_id:
            raise ValueError("station_id cannot be empty")
        if not math.isfinite(self.x_m) or not math.isfinite(self.y_m):
            raise ValueError("Projected coordinates must be finite")
        if not math.isfinite(self.value):
            raise ValueError("Observation value must be finite")
        object.__setattr__(
            self,
            "observed_at_utc",
            require_utc_datetime(self.observed_at_utc, "observed_at_utc"),
        )

    def as_point(self) -> SpaceTimePoint:
        return SpaceTimePoint(self.x_m, self.y_m, self.observed_at_utc)
