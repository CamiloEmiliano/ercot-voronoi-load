"""Causal hourly alignment and physical transforms for the Gate 3 primary cube."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
import math

import numpy as np
import pandas as pd

from bayes_demand_fcst.weather.provenance import WeatherProvenance

CANONICAL_PRIMARY_START_UTC = pd.Timestamp("2014-01-01T07:00:00Z")
CANONICAL_PRIMARY_END_UTC = pd.Timestamp("2026-01-01T06:00:00Z")
LOCAL_OBSERVATION_LOOKBACK = timedelta(minutes=60)
MISSING_PROVENANCE = 255
PRIMARY_SMOOTH_VARIABLES = ("tmpf", "dwpf", "feel_diff", "alti")
PRIMARY_VARIABLES = (*PRIMARY_SMOOTH_VARIABLES, "p01i")


@dataclass(frozen=True, slots=True)
class PrecipitationEstimate:
    """One bounded prior-shrunk precipitation estimate."""

    p01i: float
    wet_probability: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.p01i) or self.p01i < 0.0:
            raise ValueError("p01i estimate must be finite and non-negative")
        if not math.isfinite(self.wet_probability) or not 0.0 <= self.wet_probability <= 1.0:
            raise ValueError("wet_probability must be finite and within [0, 1]")


def canonical_primary_hours(
    start_utc: pd.Timestamp | None = None,
    end_utc: pd.Timestamp | None = None,
) -> pd.DatetimeIndex:
    """Return inclusive canonical hour-ending targets, clipped to the accepted grid."""
    start = CANONICAL_PRIMARY_START_UTC if start_utc is None else pd.to_datetime(start_utc, utc=True)
    end = CANONICAL_PRIMARY_END_UTC if end_utc is None else pd.to_datetime(end_utc, utc=True)
    if start > end:
        raise ValueError("start_utc must not be after end_utc")
    if start < CANONICAL_PRIMARY_START_UTC or end > CANONICAL_PRIMARY_END_UTC:
        raise ValueError(
            "Primary target range must lie inside the canonical weather grid: "
            f"{CANONICAL_PRIMARY_START_UTC.isoformat()} through {CANONICAL_PRIMARY_END_UTC.isoformat()}"
        )
    if start.minute or start.second or start.microsecond or end.minute or end.second or end.microsecond:
        raise ValueError("Primary target bounds must be exact UTC hours")
    return pd.date_range(start, end, freq="h", tz="UTC")


def _normalize_station_ids(values: pd.Series) -> pd.Series:
    return values.astype(str).astype(object)


def align_causal_local(
    observations: pd.DataFrame,
    station_id: str,
    target_hours: pd.DatetimeIndex,
    variable: str,
    *,
    lookback: timedelta = LOCAL_OBSERVATION_LOOKBACK,
) -> pd.DataFrame:
    """Use the latest causal same-station observation within a bounded local window."""
    if lookback <= timedelta(0):
        raise ValueError("lookback must be positive")
    required = {"station_id", "observed_at_utc", variable}
    missing = sorted(required - set(observations.columns))
    if missing:
        raise ValueError(f"Observations are missing columns: {', '.join(missing)}")

    targets = pd.DataFrame({"timestamp_utc": pd.to_datetime(target_hours, utc=True)})
    targets = targets.sort_values("timestamp_utc", kind="stable").reset_index(drop=True)
    candidates = observations.loc[
        observations["station_id"].astype(str).eq(str(station_id))
        & observations[variable].notna(),
        ["observed_at_utc", variable],
    ].copy()
    candidates["observed_at_utc"] = pd.to_datetime(candidates["observed_at_utc"], utc=True, errors="raise")
    candidates = candidates.sort_values("observed_at_utc", kind="stable")

    if candidates.empty:
        targets["observed_at_utc"] = pd.NaT
        targets[variable] = np.nan
        targets[f"{variable}_provenance"] = MISSING_PROVENANCE
        return targets.loc[:, ["timestamp_utc", variable, "observed_at_utc", f"{variable}_provenance"]]

    aligned = pd.merge_asof(
        targets.sort_values("timestamp_utc", kind="stable"),
        candidates.sort_values("observed_at_utc", kind="stable"),
        left_on="timestamp_utc",
        right_on="observed_at_utc",
        direction="backward",
        tolerance=pd.Timedelta(lookback),
        allow_exact_matches=True,
    )
    exact = aligned["observed_at_utc"].eq(aligned["timestamp_utc"])
    provenance = np.where(
        aligned["observed_at_utc"].isna().to_numpy(),
        MISSING_PROVENANCE,
        np.where(exact.to_numpy(), int(WeatherProvenance.EXACT_ON_HOUR), int(WeatherProvenance.LOCAL_TEMPORAL)),
    )
    aligned[f"{variable}_provenance"] = pd.Series(provenance, index=aligned.index, dtype="uint8")
    return aligned.loc[:, ["timestamp_utc", variable, "observed_at_utc", f"{variable}_provenance"]]


def reconcile_group_a(frame: pd.DataFrame) -> pd.DataFrame:
    """Enforce dewpoint saturation, derive humidity, and reconstruct apparent temperature."""
    required = {"tmpf", "dwpf", "feel_diff"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Group A reconciliation is missing columns: {', '.join(missing)}")

    reconciled = frame.copy()
    reconciled["tmpf"] = pd.to_numeric(reconciled["tmpf"], errors="coerce").astype("float64")
    reconciled["dwpf"] = pd.to_numeric(reconciled["dwpf"], errors="coerce").astype("float64")
    reconciled["feel_diff"] = pd.to_numeric(reconciled["feel_diff"], errors="coerce").astype("float64")
    valid_pair = reconciled["tmpf"].notna() & reconciled["dwpf"].notna()
    reconciled.loc[valid_pair, "dwpf"] = np.minimum(
        reconciled.loc[valid_pair, "dwpf"],
        reconciled.loc[valid_pair, "tmpf"],
    )

    temperature_c = (reconciled["tmpf"] - 32.0) * 5.0 / 9.0
    dewpoint_c = (reconciled["dwpf"] - 32.0) * 5.0 / 9.0
    magnus_a = 17.625
    magnus_b = 243.04
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        temperature_gamma = magnus_a * temperature_c / (magnus_b + temperature_c)
        dewpoint_gamma = magnus_a * dewpoint_c / (magnus_b + dewpoint_c)
        relative_humidity = 100.0 * np.exp(dewpoint_gamma - temperature_gamma)
    reconciled["relh"] = relative_humidity.where(valid_pair).clip(lower=0.0, upper=100.0)
    reconciled["feel"] = reconciled["tmpf"] + reconciled["feel_diff"]
    return reconciled


def collapse_precipitation_cycle(observations: pd.DataFrame) -> pd.DataFrame:
    """Collapse reset-aligned precipitation reports into one causal hour-ending row."""
    required = {"station_id", "observed_at_utc", "p01i"}
    missing = sorted(required - set(observations.columns))
    if missing:
        raise ValueError(f"Precipitation observations are missing columns: {', '.join(missing)}")
    candidates = observations.loc[:, ["station_id", "observed_at_utc", "p01i"]].copy()
    candidates["station_id"] = _normalize_station_ids(candidates["station_id"])
    candidates["observed_at_utc"] = pd.to_datetime(
        candidates["observed_at_utc"], utc=True, errors="raise"
    )
    candidates["p01i"] = pd.to_numeric(candidates["p01i"], errors="coerce")
    candidates = candidates.loc[candidates["p01i"].notna()]
    candidates["timestamp_utc"] = (candidates["observed_at_utc"] + pd.Timedelta(minutes=4)).dt.ceil("h")
    candidates = candidates.sort_values(["station_id", "timestamp_utc", "observed_at_utc"], kind="stable")

    collapsed = (
        candidates.groupby(["station_id", "timestamp_utc"], as_index=False, sort=True)
        .agg(p01i=("p01i", "max"), observed_at_utc=("observed_at_utc", "max"))
    )
    exact = collapsed["observed_at_utc"].eq(collapsed["timestamp_utc"])
    collapsed["p01i_provenance"] = pd.Series(
        np.where(
            exact.to_numpy(),
            int(WeatherProvenance.EXACT_ON_HOUR),
            int(WeatherProvenance.LOCAL_TEMPORAL),
        ),
        index=collapsed.index,
        dtype="uint8",
    )
    return collapsed.loc[
        :,
        ["station_id", "timestamp_utc", "p01i", "observed_at_utc", "p01i_provenance"],
    ]


def impute_precipitation_hurdle(
    values: Sequence[float],
    distances_m: Sequence[float],
    *,
    spatial_length_m: float,
    prior_weight: float,
    wet_threshold: float,
) -> PrecipitationEstimate:
    """Estimate occurrence with a dry prior, then wet intensity with convex weights."""
    if len(values) != len(distances_m):
        raise ValueError("values and distances_m must have equal lengths")
    if not values:
        return PrecipitationEstimate(p01i=0.0, wet_probability=0.0)
    if not math.isfinite(spatial_length_m) or spatial_length_m <= 0.0:
        raise ValueError("spatial_length_m must be finite and positive")
    if not math.isfinite(prior_weight) or prior_weight <= 0.0:
        raise ValueError("prior_weight must be finite and positive")
    if not 0.0 <= wet_threshold <= 1.0:
        raise ValueError("wet_threshold must lie within [0, 1]")

    value_array = np.asarray(tuple(values), dtype=np.float64)
    distance_array = np.asarray(tuple(distances_m), dtype=np.float64)
    if not np.isfinite(value_array).all() or (value_array < 0.0).any():
        raise ValueError("precipitation neighbors must be finite and non-negative")
    if not np.isfinite(distance_array).all() or (distance_array < 0.0).any():
        raise ValueError("precipitation distances must be finite and non-negative")

    weights = np.exp(-distance_array / spatial_length_m)
    wet = value_array > 0.0
    wet_probability = float(weights @ wet.astype(np.float64) / (prior_weight + weights.sum()))
    wet_probability = min(1.0, max(0.0, wet_probability))
    if wet_probability < wet_threshold or not wet.any():
        return PrecipitationEstimate(p01i=0.0, wet_probability=wet_probability)

    wet_weights = weights[wet]
    transformed = np.sqrt(value_array[wet])
    intensity = float(wet_weights @ transformed / wet_weights.sum())
    p01i = round(max(0.0, intensity * intensity) + 1e-12, 2)
    return PrecipitationEstimate(p01i=p01i, wet_probability=wet_probability)
