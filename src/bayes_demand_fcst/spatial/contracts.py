"""Spatial CRS, station metadata, zone, and area-weight contracts."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
import math

EPSG_WGS84 = "EPSG:4326"
EPSG_METRIC = "EPSG:3083"
UNIT_SUM_TOLERANCE = 1e-9


class WeatherZone(StrEnum):
    """Canonical ERCOT weather-zone keys used by the normalized load panel."""

    COAST = "coast"
    EAST = "east"
    FAR_WEST = "fwest"
    NORTH = "north"
    NORTH_CENTRAL = "ncent"
    SOUTH = "south"
    SOUTH_CENTRAL = "scent"
    WEST = "west"
    OUTSIDE_ERCOT = "outside_ercot"

    @classmethod
    def parse(cls, value: str) -> WeatherZone:
        normalized = value.strip().lower()
        aliases = {
            "far_west": cls.FAR_WEST.value,
            "north_c": cls.NORTH_CENTRAL.value,
            "north_central": cls.NORTH_CENTRAL.value,
            "south_c": cls.SOUTH_CENTRAL.value,
            "south_central": cls.SOUTH_CENTRAL.value,
            "outside": cls.OUTSIDE_ERCOT.value,
        }
        normalized = aliases.get(normalized, normalized)
        try:
            return cls(normalized)
        except ValueError as error:
            allowed = ", ".join(member.value for member in cls)
            raise ValueError(f"Unknown weather zone {value!r}; expected one of: {allowed}") from error

    @classmethod
    def outside_ercot(cls) -> WeatherZone:
        return cls.OUTSIDE_ERCOT


ERCOT_WEATHER_ZONES = (
    WeatherZone.COAST,
    WeatherZone.EAST,
    WeatherZone.FAR_WEST,
    WeatherZone.NORTH,
    WeatherZone.NORTH_CENTRAL,
    WeatherZone.SOUTH,
    WeatherZone.SOUTH_CENTRAL,
    WeatherZone.WEST,
)


@dataclass(frozen=True, slots=True)
class SpatialCrsContract:
    """CRS pair for on-disk geometry and metric geometry calculations."""

    source: str = EPSG_WGS84
    metric: str = EPSG_METRIC

    def __post_init__(self) -> None:
        if self.source != EPSG_WGS84:
            raise ValueError(f"Spatial source CRS must be {EPSG_WGS84}")
        if self.metric != EPSG_METRIC:
            raise ValueError(f"Spatial metric CRS must be {EPSG_METRIC}")


@dataclass(frozen=True, slots=True)
class StationSpatialRecord:
    """Station metadata enrichment needed by weather drift and aggregation."""

    station_id: str
    longitude_wgs84: float
    latitude_wgs84: float
    elevation_m: float
    dist_coast_km: float
    weather_zone: WeatherZone

    def __post_init__(self) -> None:
        if not self.station_id:
            raise ValueError("station_id cannot be empty")
        if not math.isfinite(self.longitude_wgs84) or not -180 <= self.longitude_wgs84 <= 180:
            raise ValueError("longitude_wgs84 must be finite and within [-180, 180]")
        if not math.isfinite(self.latitude_wgs84) or not -90 <= self.latitude_wgs84 <= 90:
            raise ValueError("latitude_wgs84 must be finite and within [-90, 90]")
        if not math.isfinite(self.elevation_m):
            raise ValueError("elevation_m must be finite")
        if not math.isfinite(self.dist_coast_km) or self.dist_coast_km < 0:
            raise ValueError("dist_coast_km must be finite and non-negative")
        if not isinstance(self.weather_zone, WeatherZone):
            raise TypeError("weather_zone must be a WeatherZone")


@dataclass(frozen=True, slots=True)
class AreaWeight:
    """One station's share of a statewide or weather-zone geometry."""

    station_id: str
    scope: str
    weight: float
    cell_area_m2: float

    def __post_init__(self) -> None:
        if not self.station_id:
            raise ValueError("station_id cannot be empty")
        if not self.scope:
            raise ValueError("weight scope cannot be empty")
        if not math.isfinite(self.weight) or not 0 <= self.weight <= 1:
            raise ValueError("area weight must be finite and within [0, 1]")
        if not math.isfinite(self.cell_area_m2) or self.cell_area_m2 < 0:
            raise ValueError("cell_area_m2 must be finite and non-negative")


def validate_area_weight_partition(
    weights: Iterable[AreaWeight],
    scope: str,
    *,
    tolerance: float = UNIT_SUM_TOLERANCE,
) -> float:
    """Validate station uniqueness and the partition-of-unity invariant."""
    records = tuple(weights)
    if not records:
        raise ValueError(f"Area-weight partition {scope!r} is empty")
    wrong_scope = sorted({record.scope for record in records if record.scope != scope})
    if wrong_scope:
        raise ValueError(f"Area-weight records do not match scope {scope!r}: {', '.join(wrong_scope)}")

    station_ids = [record.station_id for record in records]
    if len(set(station_ids)) != len(station_ids):
        raise ValueError("Area-weight partition contains duplicate station IDs")

    total = math.fsum(record.weight for record in records)
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=tolerance):
        raise ValueError(f"Area weights for {scope!r} must sum to one, found {total:.17g}")
    return total
