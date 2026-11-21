"""Build the causal Gate 3 primary weather cube in bounded month windows."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import shutil
import tempfile
from typing import Any

import numpy as np
import pandas as pd
from pyproj import Transformer

from bayes_demand_fcst.spatial import EPSG_METRIC, EPSG_WGS84
from bayes_demand_fcst.weather.calibration import DEFAULT_HARMONIC_PERIODS_HOURS
from bayes_demand_fcst.weather.causality import ObservationMode, require_utc_datetime
from bayes_demand_fcst.weather.contracts import weather_primary_hourly_schema
from bayes_demand_fcst.weather.covariance import CovarianceKernel, SpaceTimeCovariance
from bayes_demand_fcst.weather.kriging import ordinary_kriging, simple_kriging
from bayes_demand_fcst.weather.primary import (
    CANONICAL_PRIMARY_END_UTC,
    CANONICAL_PRIMARY_START_UTC,
    MISSING_PROVENANCE,
    PRIMARY_VARIABLES,
    align_causal_local,
    canonical_primary_hours,
    collapse_precipitation_cycle,
    impute_precipitation_hurdle,
    reconcile_group_a,
)
from bayes_demand_fcst.weather.provenance import WeatherProvenance
from bayes_demand_fcst.weather.space_time import SpaceTimeObservation, SpaceTimePoint

SPATIAL_OBSERVATION_LOOKBACK = timedelta(hours=24)
MAX_SPATIAL_STATIONS = 10
MAX_OBSERVATIONS_PER_STATION = 4
MAX_PRECIPITATION_NEIGHBORS = 8
TMPF_12H_CONTEXT_HOURS = 11
COMPUTATION_START_UTC = pd.Timestamp("2014-01-01T00:00:00Z")
SMOOTH_KRIGING_METHODS = {
    "tmpf": "simple",
    "dwpf": "simple",
    "feel_diff": "ordinary",
    "alti": "simple",
}
FLOAT_OUTPUT_COLUMNS = tuple(
    field.name
    for field in weather_primary_hourly_schema.fields
    if field.logical_type == "float32"
)
PROVENANCE_OUTPUT_COLUMNS = tuple(
    field.name
    for field in weather_primary_hourly_schema.fields
    if field.logical_type == "uint8" and field.name != "tmpf_12h_complete_count"
)


@dataclass(frozen=True, slots=True)
class HarmonicDriftModel:
    """Frozen coefficients and basis metadata for one station/variable."""

    station_id: str
    variable: str
    reference_utc: pd.Timestamp
    periods_hours: tuple[float, ...]
    coefficients: np.ndarray
    fallback_from: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StationSeries:
    """Causal residual observations for one station and variable."""

    station_id: str
    x_m: float
    y_m: float
    times_ns: np.ndarray
    residuals: np.ndarray

    def before(self, target: pd.Timestamp, lookback: timedelta) -> list[tuple[pd.Timestamp, float]]:
        target_ns = int(target.value)
        earliest_ns = int((target - lookback).value)
        stop = int(np.searchsorted(self.times_ns, target_ns, side="right"))
        start = max(0, stop - MAX_OBSERVATIONS_PER_STATION, int(np.searchsorted(self.times_ns, earliest_ns, side="left")))
        return [
            (pd.Timestamp(self.times_ns[index], tz="UTC"), float(self.residuals[index]))
            for index in range(start, stop)
        ]


@dataclass(slots=True)
class MethodCounts:
    """Mutable per-run solver/provenance diagnostics."""

    local_exact: dict[str, int]
    local_temporal: dict[str, int]
    spatial: dict[str, int]
    prior_fallback: dict[str, int]

    @classmethod
    def create(cls, variables: Sequence[str]) -> MethodCounts:
        return cls(
            local_exact={variable: 0 for variable in variables},
            local_temporal={variable: 0 for variable in variables},
            spatial={variable: 0 for variable in variables},
            prior_fallback={variable: 0 for variable in variables},
        )


def _parse_utc(value: datetime | pd.Timestamp, field_name: str) -> pd.Timestamp:
    if isinstance(value, datetime):
        require_utc_datetime(value, field_name)
    parsed = pd.to_datetime(value, utc=True, errors="raise")
    return pd.Timestamp(parsed)


def _validate_variables(variables: Sequence[str]) -> tuple[str, ...]:
    selected = tuple(dict.fromkeys(str(variable).strip() for variable in variables))
    if not selected:
        raise ValueError("At least one Gate 3 primary variable is required")
    unknown = sorted(set(selected) - set(PRIMARY_VARIABLES))
    if unknown:
        raise ValueError(f"Unknown primary variables: {', '.join(unknown)}")
    return selected


def _parse_target_periods(
    year_months: Sequence[str] | None,
    start_utc: datetime | pd.Timestamp | None,
    end_utc: datetime | pd.Timestamp | None,
) -> list[tuple[pd.Period, pd.Timestamp, pd.Timestamp]]:
    if year_months is not None and (start_utc is not None or end_utc is not None):
        raise ValueError("Use year_months or start_utc/end_utc, not both")

    target_hours: pd.DatetimeIndex
    if start_utc is not None or end_utc is not None:
        if start_utc is None or end_utc is None:
            raise ValueError("start_utc and end_utc must be provided together")
        start = _parse_utc(start_utc, "start_utc")
        end = _parse_utc(end_utc, "end_utc")
        target_hours = canonical_primary_hours(start, end)
    elif year_months is not None:
        periods: list[pd.Period] = []
        for value in year_months:
            try:
                period = pd.Period(str(value), freq="M")
            except ValueError as error:
                raise ValueError(f"Invalid year-month {value!r}; expected YYYY-MM") from error
            periods.append(period)
        hours: list[pd.Timestamp] = []
        for period in sorted(set(periods)):
            period_start = pd.Timestamp(period.start_time, tz="UTC")
            period_end = pd.Timestamp(period.end_time, tz="UTC").floor("h")
            start = max(period_start, CANONICAL_PRIMARY_START_UTC)
            end = min(period_end, CANONICAL_PRIMARY_END_UTC)
            if start <= end:
                hours.extend(canonical_primary_hours(start, end).tolist())
        target_hours = pd.DatetimeIndex(sorted(set(hours)), tz="UTC")
        if target_hours.empty:
            raise ValueError("Selected year_months do not intersect the canonical primary grid")
    else:
        target_hours = canonical_primary_hours()

    result: list[tuple[pd.Period, pd.Timestamp, pd.Timestamp]] = []
    month_labels = target_hours.strftime("%Y-%m")
    for month_label in dict.fromkeys(month_labels):
        period_hours = target_hours[month_labels == month_label]
        result.append((pd.Period(month_label, freq="M"), period_hours[0], period_hours[-1]))
    return result


def _station_geometry(station_metadata_path: Path) -> tuple[pd.DataFrame, dict[str, float], dict[str, tuple[str, ...]]]:
    stations = pd.read_parquet(station_metadata_path)
    required = {"station_id", "longitude_wgs84", "latitude_wgs84", "elevation_m"}
    missing = sorted(required - set(stations.columns))
    if missing:
        raise ValueError(f"Station metadata is missing columns: {', '.join(missing)}")
    stations = stations.loc[:, list(required)].copy()
    stations["station_id"] = stations["station_id"].astype(str)
    if stations["station_id"].duplicated().any():
        raise ValueError("Station metadata contains duplicate station IDs")
    if len(stations) < 1:
        raise ValueError("Station metadata is empty")
    stations[["longitude_wgs84", "latitude_wgs84", "elevation_m"]] = stations[
        ["longitude_wgs84", "latitude_wgs84", "elevation_m"]
    ].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(stations[["longitude_wgs84", "latitude_wgs84", "elevation_m"]].to_numpy()).all():
        raise ValueError("Station metadata contains non-finite geometry values")
    transformer = Transformer.from_crs(EPSG_WGS84, EPSG_METRIC, always_xy=True)
    x_values, y_values = transformer.transform(
        stations["longitude_wgs84"].to_numpy(),
        stations["latitude_wgs84"].to_numpy(),
    )
    stations["x_m"] = x_values
    stations["y_m"] = y_values
    stations = stations.sort_values("station_id", kind="stable").reset_index(drop=True)

    distances: dict[str, float] = {}
    neighbor_ids: dict[str, tuple[str, ...]] = {}
    coordinates = stations[["x_m", "y_m"]].to_numpy(dtype=np.float64)
    station_ids = stations["station_id"].tolist()
    for index, station_id in enumerate(station_ids):
        order = np.argsort(np.hypot(coordinates[:, 0] - coordinates[index, 0], coordinates[:, 1] - coordinates[index, 1]))
        neighbor_ids[station_id] = tuple(station_ids[int(position)] for position in order)
        for other_index in order:
            distances[(station_id, station_ids[int(other_index)])] = float(
                math.hypot(
                    coordinates[int(other_index), 0] - coordinates[index, 0],
                    coordinates[int(other_index), 1] - coordinates[index, 1],
                )
            )
    return stations, distances, neighbor_ids


def _load_solver_configuration(calibration_dir: Path) -> tuple[pd.Timestamp, dict[str, SpaceTimeCovariance], dict[str, float]]:
    path = calibration_dir / "solver_config.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing Gate 2 solver configuration: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    train_end = pd.Timestamp(payload["train_end_utc"])
    if str(train_end.tz) != "UTC":
        raise ValueError("Gate 2 solver train_end_utc must be UTC")
    covariance: dict[str, SpaceTimeCovariance] = {}
    for variable, values in payload["covariance_parameters"].items():
        covariance[variable] = SpaceTimeCovariance(
            variance=float(values["variance"]),
            spatial_length_m=float(values["spatial_length_m"]),
            temporal_length_hours=float(values["temporal_length_hours"]),
            nugget_variance=float(values["nugget_variance"]),
            kernel=CovarianceKernel(str(values["kernel"])),
        )
    idw = {
        "spatial_length_m": float(payload["idw_parameters"]["spatial_length_m"]),
        "prior_weight": float(payload["idw_parameters"]["prior_weight"]),
        "wet_probability_threshold": float(payload["idw_parameters"]["wet_probability_threshold"]),
    }
    return train_end, covariance, idw


def _load_harmonic_models(
    calibration_dir: Path,
    stations: pd.DataFrame,
    distances: dict[str, float],
    neighbor_ids: dict[str, tuple[str, ...]],
    variables: Sequence[str],
) -> tuple[dict[tuple[str, str], HarmonicDriftModel], dict[str, int]]:
    path = calibration_dir / "harmonic_coefficients.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Missing Gate 2 harmonic coefficients: {path}")
    coefficients = pd.read_parquet(path)
    required = {
        "station_id",
        "variable",
        "reference_utc",
        "coefficient_00",
        "coefficient_01",
        "coefficient_02",
        "coefficient_03",
        "coefficient_04",
        "coefficient_05",
        "coefficient_06",
    }
    missing = sorted(required - set(coefficients.columns))
    if missing:
        raise ValueError(f"Harmonic coefficients are missing columns: {', '.join(missing)}")
    coefficient_columns = tuple(column for column in sorted(coefficients.columns) if column.startswith("coefficient_"))
    models: dict[tuple[str, str], HarmonicDriftModel] = {}
    fallback_count = 0
    station_ids = stations["station_id"].tolist()
    selected_fitted_variables = tuple(variable for variable in variables if variable in {"tmpf", "dwpf", "alti"})
    periods = tuple(float(value) for value in DEFAULT_HARMONIC_PERIODS_HOURS)

    for variable in selected_fitted_variables:
        available = coefficients.loc[coefficients["variable"].astype(str).eq(variable)].copy()
        available_by_station = {
            str(row.station_id): row for row in available.itertuples(index=False)
        }
        for station_id in station_ids:
            row = available_by_station.get(station_id)
            if row is not None:
                source_ids = (station_id,)
                values = np.asarray([getattr(row, column) for column in coefficient_columns], dtype=np.float64)
                reference = pd.Timestamp(row.reference_utc)
            else:
                nearest = tuple(
                    neighbor
                    for neighbor in neighbor_ids[station_id]
                    if neighbor in available_by_station
                )[:4]
                if not nearest:
                    raise ValueError(f"No spatial harmonic fallback available for {station_id}/{variable}")
                inverse_distance = np.asarray(
                    [1.0 / max(1.0, distances[(station_id, neighbor)]) for neighbor in nearest],
                    dtype=np.float64,
                )
                weights = inverse_distance / inverse_distance.sum()
                values = sum(
                    weight
                    * np.asarray(
                        [getattr(available_by_station[neighbor], column) for column in coefficient_columns],
                        dtype=np.float64,
                    )
                    for weight, neighbor in zip(weights, nearest, strict=True)
                )
                reference = pd.Timestamp(available_by_station[nearest[0]].reference_utc)
                source_ids = nearest
                fallback_count += 1
            models[(station_id, variable)] = HarmonicDriftModel(
                station_id=station_id,
                variable=variable,
                reference_utc=reference,
                periods_hours=periods,
                coefficients=np.asarray(values, dtype=np.float64),
                fallback_from=source_ids if station_id not in available_by_station else (),
            )
    return models, {"station_variable_harmonic_fallbacks": fallback_count}


def _drift_prediction(model: HarmonicDriftModel, timestamps: pd.DatetimeIndex) -> np.ndarray:
    elapsed = (timestamps - model.reference_utc).total_seconds().to_numpy(dtype=np.float64) / 3600.0
    columns: list[np.ndarray] = [np.ones(len(timestamps), dtype=np.float64)]
    for period in model.periods_hours:
        phase = 2.0 * np.pi * elapsed / period
        columns.extend((np.cos(phase), np.sin(phase)))
    design = np.column_stack(columns)
    return design @ model.coefficients


def _discover_clean_partitions(clean_dir: Path, source_start: pd.Timestamp, output_end: pd.Timestamp) -> tuple[Path, ...]:
    selected: list[Path] = []
    source_periods = set(
        period.strftime("%Y-%m")
        for period in pd.period_range(
            source_start.strftime("%Y-%m"),
            output_end.strftime("%Y-%m"),
            freq="M",
        )
    )
    for path in sorted(clean_dir.rglob("*.parquet")):
        parts = {part.split("=", 1)[0]: part.split("=", 1)[1] for part in path.parts if "=" in part}
        if "year" not in parts or "month" not in parts:
            continue
        if f"{parts['year']}-{parts['month']}" in source_periods:
            selected.append(path)
    if not selected:
        raise FileNotFoundError(
            f"No clean weather partitions intersect {source_start.isoformat()} through {output_end.isoformat()}"
        )
    return tuple(selected)


def _load_clean_window(
    clean_dir: Path,
    source_start: pd.Timestamp,
    output_end: pd.Timestamp,
    variables: Sequence[str],
) -> tuple[pd.DataFrame, tuple[Path, ...], int]:
    paths = _discover_clean_partitions(clean_dir, source_start, output_end)
    columns = ["station_id", "observed_at_utc", *variables]
    frames = [pd.read_parquet(path, columns=columns) for path in paths]
    frame = pd.concat(frames, ignore_index=True)
    frame["station_id"] = frame["station_id"].astype(str)
    frame["observed_at_utc"] = pd.to_datetime(frame["observed_at_utc"], utc=True, errors="raise")
    future_count = int(frame["observed_at_utc"].gt(output_end).sum())
    frame = frame.loc[
        frame["observed_at_utc"].between(source_start, output_end, inclusive="both")
    ].copy()
    return frame, paths, future_count


def _observation_series(
    observations: pd.DataFrame,
    stations: pd.DataFrame,
    models: dict[tuple[str, str], HarmonicDriftModel],
    variables: Sequence[str],
) -> dict[str, dict[str, StationSeries]]:
    geometry = stations.set_index("station_id")[["x_m", "y_m"]].to_dict("index")
    result: dict[str, dict[str, StationSeries]] = {variable: {} for variable in variables if variable != "p01i"}
    for variable in result:
        valid = observations.loc[observations[variable].notna(), ["station_id", "observed_at_utc", variable]].copy()
        for station_id, group in valid.groupby("station_id", sort=False):
            if station_id not in geometry:
                continue
            group = group.sort_values("observed_at_utc", kind="stable")
            times = pd.DatetimeIndex(group["observed_at_utc"])
            values = group[variable].to_numpy(dtype=np.float64)
            if variable in {"tmpf", "dwpf", "alti"}:
                residuals = values - _drift_prediction(models[(str(station_id), variable)], times)
            else:
                residuals = values
            result[variable][str(station_id)] = StationSeries(
                station_id=str(station_id),
                x_m=float(geometry[str(station_id)]["x_m"]),
                y_m=float(geometry[str(station_id)]["y_m"]),
                times_ns=np.asarray([timestamp.value for timestamp in times], dtype=np.int64),
                residuals=np.asarray(residuals, dtype=np.float64),
            )
    return result


def _kriging_prediction(
    station_id: str,
    variable: str,
    target: pd.Timestamp,
    stations: pd.DataFrame,
    neighbors: dict[str, tuple[str, ...]],
    observation_series: dict[str, dict[str, StationSeries]],
    models: dict[tuple[str, str], HarmonicDriftModel],
    covariance: SpaceTimeCovariance,
) -> tuple[float, float, bool]:
    station_geometry = stations.set_index("station_id")[["x_m", "y_m"]]
    target_point = SpaceTimePoint(
        float(station_geometry.at[station_id, "x_m"]),
        float(station_geometry.at[station_id, "y_m"]),
        target.to_pydatetime(),
    )
    candidates: list[SpaceTimeObservation] = []
    used_station_count = 0
    for neighbor_id in neighbors[station_id]:
        series = observation_series[variable].get(neighbor_id)
        if series is None:
            continue
        recent = series.before(target, SPATIAL_OBSERVATION_LOOKBACK)
        if not recent:
            continue
        used_station_count += 1
        for observed_at, residual in recent:
            candidates.append(
                SpaceTimeObservation(
                    station_id=neighbor_id,
                    x_m=series.x_m,
                    y_m=series.y_m,
                    observed_at_utc=observed_at.to_pydatetime(),
                    value=residual,
                )
            )
        if used_station_count >= MAX_SPATIAL_STATIONS:
            break

    if variable in {"tmpf", "dwpf", "alti"}:
        drift = float(_drift_prediction(models[(station_id, variable)], pd.DatetimeIndex([target]))[0])
    else:
        drift = 0.0
    if not candidates:
        return drift, float(covariance.variance), False

    if SMOOTH_KRIGING_METHODS[variable] == "simple":
        result = simple_kriging(candidates, target_point, covariance, prior_mean=0.0)
    else:
        result = ordinary_kriging(candidates, target_point, covariance)
    return drift + result.prediction, result.variance, True


def _initialize_station_frame(
    station_id: str,
    context_hours: pd.DatetimeIndex,
    observations: pd.DataFrame,
    variables: Sequence[str],
) -> pd.DataFrame:
    frame = pd.DataFrame({"timestamp_utc": context_hours})
    station_observations = observations.loc[observations["station_id"].astype(str).eq(station_id)]
    for variable in variables:
        if variable == "p01i":
            continue
        aligned = align_causal_local(station_observations, station_id, context_hours, variable)
        frame[variable] = aligned[variable].to_numpy(dtype=np.float64)
        frame[f"{variable}_provenance"] = aligned[f"{variable}_provenance"].to_numpy(dtype=np.uint8)
        variance_column = f"{variable}_kriging_variance"
        frame[variance_column] = 0.0
        frame.loc[frame[f"{variable}_provenance"].eq(MISSING_PROVENANCE), variance_column] = np.nan

    if "p01i" in variables:
        if station_observations["p01i"].notna().any():
            local_precipitation = collapse_precipitation_cycle(station_observations)
            aligned = frame.merge(
                local_precipitation[["timestamp_utc", "p01i", "p01i_provenance"]],
                on="timestamp_utc",
                how="left",
                validate="one_to_one",
            )
            frame = aligned
        else:
            frame["p01i"] = np.nan
            frame["p01i_provenance"] = MISSING_PROVENANCE
        frame["p01i_wet_probability"] = np.where(
            frame["p01i"].notna().to_numpy(),
            frame["p01i"].gt(0.0).to_numpy(dtype=np.float64),
            np.nan,
        )

    frame["station_id"] = station_id
    return frame


def _fill_smooth_variables(
    frame: pd.DataFrame,
    stations: pd.DataFrame,
    neighbors: dict[str, tuple[str, ...]],
    observation_series: dict[str, dict[str, StationSeries]],
    models: dict[tuple[str, str], HarmonicDriftModel],
    covariance: dict[str, SpaceTimeCovariance],
    variables: Sequence[str],
) -> pd.DataFrame:
    station_frames: list[pd.DataFrame] = []
    for station_id, station_frame in frame.groupby("station_id", sort=True, dropna=False):
        station_frame = station_frame.sort_values("timestamp_utc", kind="stable").copy()
        for variable in variables:
            if variable == "p01i":
                continue
            provenance_column = f"{variable}_provenance"
            variance_column = f"{variable}_kriging_variance"
            missing_positions = np.flatnonzero(station_frame[variable].isna().to_numpy())
            for position in missing_positions:
                target = pd.Timestamp(station_frame.iloc[position]["timestamp_utc"])
                try:
                    value, variance, used_neighbors = _kriging_prediction(
                        str(station_id),
                        variable,
                        target,
                        stations,
                        neighbors,
                        observation_series,
                        models,
                        covariance[variable],
                    )
                except np.linalg.LinAlgError:
                    value, variance, used_neighbors = (
                        0.0,
                        covariance[variable].variance,
                        False,
                    )
                    if variable in {"tmpf", "dwpf", "alti"}:
                        value = float(_drift_prediction(models[(str(station_id), variable)], pd.DatetimeIndex([target]))[0])
                station_frame.iat[position, station_frame.columns.get_loc(variable)] = value
                station_frame.iat[position, station_frame.columns.get_loc(variance_column)] = max(0.0, variance)
                station_frame.iat[position, station_frame.columns.get_loc(provenance_column)] = int(
                    WeatherProvenance.SPATIOTEMPORAL_KRIG
                )
                station_frame.loc[station_frame.index[position], f"{variable}_used_spatial"] = used_neighbors
        station_frames.append(station_frame)
    return pd.concat(station_frames, ignore_index=True)


def _fill_precipitation(
    frame: pd.DataFrame,
    distances: dict[str, float],
    neighbors: dict[str, tuple[str, ...]],
    idw_parameters: dict[str, float],
) -> pd.DataFrame:
    output = frame.copy()
    local_only = output.loc[output["p01i"].notna()]
    values_by_time_station = local_only.set_index(["timestamp_utc", "station_id"])["p01i"]
    for target_timestamp, target_frame in output.groupby("timestamp_utc", sort=True):
        missing_positions = target_frame.index[target_frame["p01i"].isna()]
        for position in missing_positions:
            station_id = str(output.at[position, "station_id"])
            values: list[float] = []
            station_distances: list[float] = []
            for neighbor_id in neighbors[station_id][1:]:
                key = (target_timestamp, neighbor_id)
                if key not in values_by_time_station.index:
                    continue
                values.append(float(values_by_time_station.at[key]))
                station_distances.append(distances[(station_id, neighbor_id)])
                if len(values) >= MAX_PRECIPITATION_NEIGHBORS:
                    break
            estimate = impute_precipitation_hurdle(
                values,
                station_distances,
                spatial_length_m=idw_parameters["spatial_length_m"],
                prior_weight=idw_parameters["prior_weight"],
                wet_threshold=idw_parameters["wet_probability_threshold"],
            )
            output.at[position, "p01i"] = estimate.p01i
            output.at[position, "p01i_wet_probability"] = estimate.wet_probability
            output.at[position, "p01i_provenance"] = int(WeatherProvenance.SPATIOTEMPORAL_KRIG)
            output.at[position, "p01i_used_spatial"] = bool(values)
    return output


def _add_derived_group_a_and_rolling(frame: pd.DataFrame, variables: Sequence[str]) -> pd.DataFrame:
    output_frames: list[pd.DataFrame] = []
    for _, station_frame in frame.groupby("station_id", sort=True, dropna=False):
        station_frame = station_frame.sort_values("timestamp_utc", kind="stable").copy()
        if {"tmpf", "dwpf", "feel_diff"}.issubset(variables):
            station_frame = reconcile_group_a(station_frame)
        if "tmpf" in variables:
            station_frame["tmpf_12h"] = (
                station_frame["tmpf"].rolling(window=12, min_periods=1).mean().astype("float64")
            )
            station_frame["tmpf_12h_complete_count"] = (
                station_frame["tmpf"].rolling(window=12, min_periods=1).count().astype("uint8")
            )
        output_frames.append(station_frame)
    return pd.concat(output_frames, ignore_index=True)


def _prepare_empty_output_columns(frame: pd.DataFrame, variables: Sequence[str]) -> pd.DataFrame:
    output = frame.copy()
    for field in weather_primary_hourly_schema.fields:
        if field.name in output.columns:
            continue
        if field.logical_type == "uint8":
            output[field.name] = MISSING_PROVENANCE if field.name != "tmpf_12h_complete_count" else 0
        elif field.logical_type == "float32":
            output[field.name] = np.nan
    return output.loc[:, list(weather_primary_hourly_schema.required_columns)]


def _accumulate_output_methods(
    frame: pd.DataFrame,
    variables: Sequence[str],
    counts: MethodCounts,
) -> None:
    for variable in variables:
        provenance = frame[f"{variable}_provenance"]
        counts.local_exact[variable] += int(provenance.eq(int(WeatherProvenance.EXACT_ON_HOUR)).sum())
        counts.local_temporal[variable] += int(provenance.eq(int(WeatherProvenance.LOCAL_TEMPORAL)).sum())
        used_spatial = frame.get(f"{variable}_used_spatial")
        if used_spatial is not None:
            counts.spatial[variable] += int(used_spatial.fillna(False).astype(bool).sum())
            counts.prior_fallback[variable] += int(
                provenance.eq(int(WeatherProvenance.SPATIOTEMPORAL_KRIG)).sum()
                - used_spatial.fillna(False).astype(bool).sum()
            )
        else:
            counts.spatial[variable] += int(
                provenance.eq(int(WeatherProvenance.SPATIOTEMPORAL_KRIG)).sum()
            )


def _validate_primary_physics(frame: pd.DataFrame, variables: Sequence[str]) -> dict[str, int]:
    quality: dict[str, int] = {
        "duplicate_station_hours": int(frame.duplicated(["station_id", "timestamp_utc"]).sum()),
        "dwpf_exceeds_tmpf": 0,
        "relative_humidity_out_of_bounds": 0,
        "negative_precipitation": 0,
        "wet_probability_out_of_bounds": 0,
        "alti_out_of_bounds": 0,
    }
    if {"tmpf", "dwpf"}.issubset(variables):
        quality["dwpf_exceeds_tmpf"] = int(frame["dwpf"].gt(frame["tmpf"]).sum())
    if {"tmpf", "dwpf", "feel_diff"}.issubset(variables):
        quality["relative_humidity_out_of_bounds"] = int(
            frame["relh"].lt(0.0).sum() + frame["relh"].gt(100.0).sum()
        )
    if "p01i" in variables:
        quality["negative_precipitation"] = int(frame["p01i"].lt(0.0).sum())
        quality["wet_probability_out_of_bounds"] = int(
            frame["p01i_wet_probability"].lt(0.0).sum()
            + frame["p01i_wet_probability"].gt(1.0).sum()
        )
    if "alti" in variables:
        quality["alti_out_of_bounds"] = int(
            frame["alti"].lt(28.5).sum() + frame["alti"].gt(31.2).sum()
        )
    selected_value_columns = list(variables)
    if "tmpf" in variables:
        selected_value_columns.append("tmpf_12h")
    if {"tmpf", "dwpf", "feel_diff"}.issubset(variables):
        selected_value_columns.extend(("relh", "feel"))
    if "p01i" in variables:
        selected_value_columns.append("p01i_wet_probability")
    quality["non_finite_selected_values"] = int(
        sum((~np.isfinite(frame[column].to_numpy(dtype=np.float64))).sum() for column in selected_value_columns)
    )
    if any(value > 0 for value in quality.values()):
        failures = ", ".join(f"{key}={value}" for key, value in quality.items() if value > 0)
        raise ValueError(f"Primary cube physical validation failed: {failures}")
    return quality


def _cast_primary_output(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    output["station_id"] = output["station_id"].astype("string")
    output["timestamp_utc"] = pd.to_datetime(output["timestamp_utc"], utc=True, errors="raise")
    for column in FLOAT_OUTPUT_COLUMNS:
        output[column] = pd.to_numeric(output[column], errors="coerce").astype("float32")
    for column in PROVENANCE_OUTPUT_COLUMNS:
        output[column] = pd.to_numeric(output[column], errors="coerce").fillna(MISSING_PROVENANCE).astype("uint8")
    output["tmpf_12h_complete_count"] = (
        pd.to_numeric(output["tmpf_12h_complete_count"], errors="coerce").fillna(0).astype("uint8")
    )
    return output.loc[:, list(weather_primary_hourly_schema.required_columns)]


def build_weather_primary_hourly(
    clean_dir: Path,
    calibration_dir: Path,
    station_metadata_path: Path,
    output_dir: Path,
    *,
    variables: Sequence[str] = PRIMARY_VARIABLES,
    year_months: Sequence[str] | None = None,
    start_utc: datetime | pd.Timestamp | None = None,
    end_utc: datetime | pd.Timestamp | None = None,
    mode: ObservationMode = ObservationMode.CAUSAL,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Build causal primary hourly cubes from Gate 1 and frozen Gate 2 artifacts."""
    if mode is not ObservationMode.CAUSAL:
        raise ValueError("Canonical weather_primary_hourly accepts causal mode only")
    selected_variables = _validate_variables(variables)
    smooth_variables = tuple(variable for variable in selected_variables if variable != "p01i")
    periods = _parse_target_periods(year_months, start_utc, end_utc)
    if output_dir.exists() and any(output_dir.iterdir()):
        if not overwrite:
            raise FileExistsError(f"Output directory is not empty: {output_dir}; use --overwrite to replace it")

    stations, distances, neighbors = _station_geometry(station_metadata_path)
    train_end, covariance, idw_parameters = _load_solver_configuration(calibration_dir)
    models, model_quality = _load_harmonic_models(
        calibration_dir,
        stations,
        distances,
        neighbors,
        smooth_variables,
    )
    for variable in smooth_variables:
        if variable not in covariance:
            raise ValueError(f"Solver configuration has no covariance parameters for {variable}")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    counts = MethodCounts.create(selected_variables)
    all_files: set[Path] = set()
    rows_written = 0
    future_observations_excluded = 0
    input_rows_scanned = 0
    physical_quality = {
        "duplicate_station_hours": 0,
        "dwpf_exceeds_tmpf": 0,
        "relative_humidity_out_of_bounds": 0,
        "negative_precipitation": 0,
        "wet_probability_out_of_bounds": 0,
        "alti_out_of_bounds": 0,
        "non_finite_selected_values": 0,
    }
    try:
        for period, period_start, period_end in periods:
            context_start = max(COMPUTATION_START_UTC, period_start - pd.Timedelta(hours=TMPF_12H_CONTEXT_HOURS))
            source_start = context_start - SPATIAL_OBSERVATION_LOOKBACK
            observations, paths, future_count = _load_clean_window(
                clean_dir,
                source_start,
                period_end,
                selected_variables,
            )
            all_files.update(paths)
            input_rows_scanned += len(observations)
            future_observations_excluded += future_count
            observation_series = _observation_series(observations, stations, models, smooth_variables)
            context_hours = pd.date_range(context_start, period_end, freq="h", tz="UTC")

            station_frames = [
                _initialize_station_frame(str(station_id), context_hours, observations, selected_variables)
                for station_id in stations["station_id"]
            ]
            cube = pd.concat(station_frames, ignore_index=True)
            if smooth_variables:
                cube = _fill_smooth_variables(
                    cube,
                    stations,
                    neighbors,
                    observation_series,
                    models,
                    covariance,
                    smooth_variables,
                )
            if "p01i" in selected_variables:
                cube = _fill_precipitation(cube, distances, neighbors, idw_parameters)
            cube = _add_derived_group_a_and_rolling(cube, selected_variables)
            cube = cube.loc[cube["timestamp_utc"].between(period_start, period_end)].copy()
            _accumulate_output_methods(cube, selected_variables, counts)
            period_physics = _validate_primary_physics(cube, selected_variables)
            for key, value in period_physics.items():
                physical_quality[key] += value
            cube = _prepare_empty_output_columns(cube, selected_variables)
            cube = _cast_primary_output(cube)
            weather_primary_hourly_schema.validate_columns(cube.columns)
            if cube.duplicated(["station_id", "timestamp_utc"]).any():
                raise AssertionError("Primary cube contains duplicate station-hour keys")

            destination = staging / f"year={period.year}" / f"month={period.month:02d}" / "primary.parquet"
            destination.parent.mkdir(parents=True, exist_ok=True)
            cube.to_parquet(destination, index=False, engine="pyarrow", compression="zstd")
            rows_written += len(cube)

        for variable in selected_variables:
            method_total = sum(
                counts_by_method[variable]
                for counts_by_method in (
                    counts.local_exact,
                    counts.local_temporal,
                    counts.spatial,
                    counts.prior_fallback,
                )
            )
            if method_total != rows_written:
                raise AssertionError(
                    f"{variable} provenance methods total {method_total} does not match {rows_written} rows"
                )
        report: dict[str, Any] = {
            "schema": {
                "name": weather_primary_hourly_schema.name,
                "version": weather_primary_hourly_schema.version,
                "primary_key": list(weather_primary_hourly_schema.primary_key),
            },
            "accepted": True,
            "mode": mode.value,
            "train_end_utc": train_end.isoformat(),
            "target_start_utc": periods[0][1].isoformat(),
            "target_end_utc": periods[-1][2].isoformat(),
            "variables": list(selected_variables),
            "files_processed": len(all_files),
            "periods_processed": len(periods),
            "input_rows_scanned": input_rows_scanned,
            "row_count_written": rows_written,
            "station_count": len(stations),
            "future_observations_excluded": future_observations_excluded,
            "methods": {
                "local_exact": counts.local_exact,
                "local_temporal": counts.local_temporal,
                "spatial": counts.spatial,
                "prior_fallback": counts.prior_fallback,
            },
            "quality": {**model_quality, **physical_quality},
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
