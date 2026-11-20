"""Geometry-provider interfaces consumed by Gate 0 spatial contracts."""

from typing import Protocol

from bayes_demand_fcst.spatial.contracts import AreaWeight, StationSpatialRecord, WeatherZone


class CoastlineDistanceProvider(Protocol):
    def distance_km(self, longitude_wgs84: float, latitude_wgs84: float) -> float: ...


class WeatherZoneAssigner(Protocol):
    def assign(self, longitude_wgs84: float, latitude_wgs84: float) -> WeatherZone: ...


class AreaWeightProvider(Protocol):
    def weights_for(self, scope: str) -> tuple[AreaWeight, ...]: ...


def enrich_station_spatial_record(
    *,
    station_id: str,
    longitude_wgs84: float,
    latitude_wgs84: float,
    elevation_m: float,
    coastline: CoastlineDistanceProvider,
    zones: WeatherZoneAssigner,
) -> StationSpatialRecord:
    """Combine independent geometry providers into the canonical station record."""
    distance_km = coastline.distance_km(longitude_wgs84, latitude_wgs84)
    weather_zone = zones.assign(longitude_wgs84, latitude_wgs84)
    return StationSpatialRecord(
        station_id=station_id,
        longitude_wgs84=longitude_wgs84,
        latitude_wgs84=latitude_wgs84,
        elevation_m=elevation_m,
        dist_coast_km=distance_km,
        weather_zone=weather_zone,
    )
