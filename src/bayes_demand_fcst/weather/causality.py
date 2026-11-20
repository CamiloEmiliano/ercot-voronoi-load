"""Causal configuration and neighborhood contracts for weather imputation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Protocol, TypeVar

UTC = timezone.utc


class ObservationMode(StrEnum):
    """Declare whether a weather artifact is operational or explicitly ex-post."""

    CAUSAL = "causal"
    REANALYSIS = "reanalysis"


def require_utc_datetime(value: datetime, field_name: str) -> datetime:
    """Return a UTC-normalized datetime or reject naive/non-UTC input."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be UTC-aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must use UTC, not a non-zero offset")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class WeatherImputationConfig:
    """Anti-leakage configuration shared by every weather execution pass."""

    train_end_utc: datetime
    mode: ObservationMode = ObservationMode.CAUSAL

    def __post_init__(self) -> None:
        if not isinstance(self.mode, ObservationMode):
            raise TypeError("mode must be an ObservationMode")
        object.__setattr__(
            self,
            "train_end_utc",
            require_utc_datetime(self.train_end_utc, "train_end_utc"),
        )

    def is_calibration_eligible(self, observed_at_utc: datetime) -> bool:
        """Whether an observation is inside the frozen inclusive training window."""
        observed = require_utc_datetime(observed_at_utc, "observed_at_utc")
        return observed <= self.train_end_utc


@dataclass(frozen=True, slots=True)
class CausalNeighborhoodQuery:
    """A one-sided observation window ending at a target hour."""

    target_at_utc: datetime
    lookback: timedelta

    def __post_init__(self) -> None:
        target = require_utc_datetime(self.target_at_utc, "target_at_utc")
        if target.minute or target.second or target.microsecond:
            raise ValueError("target_at_utc must be an exact UTC hour")
        if self.lookback <= timedelta(0):
            raise ValueError("lookback must be positive")
        object.__setattr__(self, "target_at_utc", target)

    @property
    def earliest_at_utc(self) -> datetime:
        return self.target_at_utc - self.lookback

    def accepts(self, observed_at_utc: datetime) -> bool:
        """Return whether an observation lies in the inclusive causal window."""
        observed = require_utc_datetime(observed_at_utc, "observed_at_utc")
        return self.earliest_at_utc <= observed <= self.target_at_utc

    def require(self, observed_at_utc: datetime) -> None:
        """Reject a future or too-old observation for this query."""
        observed = require_utc_datetime(observed_at_utc, "observed_at_utc")
        if observed > self.target_at_utc:
            raise ValueError(
                f"Cannot accept future observation {observed.isoformat()} after {self.target_at_utc.isoformat()}"
            )
        if observed < self.earliest_at_utc:
            raise ValueError(
                f"Observation {observed.isoformat()} predates causal window {self.earliest_at_utc.isoformat()}"
            )


@dataclass(frozen=True, slots=True)
class WeatherObservation:
    """Minimal observation identity used to assemble a causal neighborhood."""

    station_id: str
    observed_at_utc: datetime

    def __post_init__(self) -> None:
        if not self.station_id:
            raise ValueError("station_id cannot be empty")
        object.__setattr__(
            self,
            "observed_at_utc",
            require_utc_datetime(self.observed_at_utc, "observed_at_utc"),
        )


class TimedObservation(Protocol):
    @property
    def observed_at_utc(self) -> datetime: ...


ObservationT = TypeVar("ObservationT", bound=TimedObservation)


def filter_causal_observations(
    observations: Iterable[ObservationT], query: CausalNeighborhoodQuery
) -> tuple[ObservationT, ...]:
    """Retain observations available at or before the target within the lookback."""
    return tuple(observation for observation in observations if query.accepts(observation.observed_at_utc))
