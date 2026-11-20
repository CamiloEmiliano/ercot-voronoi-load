"""Spatial contracts and provider interfaces for weather processing."""

from bayes_demand_fcst.spatial.contracts import (
    EPSG_METRIC,
    EPSG_WGS84,
    ERCOT_WEATHER_ZONES,
    UNIT_SUM_TOLERANCE,
    AreaWeight,
    SpatialCrsContract,
    StationSpatialRecord,
    WeatherZone,
    validate_area_weight_partition,
)
from bayes_demand_fcst.spatial.interfaces import (
    AreaWeightProvider,
    CoastlineDistanceProvider,
    WeatherZoneAssigner,
    enrich_station_spatial_record,
)

__all__ = [
    "EPSG_METRIC",
    "EPSG_WGS84",
    "ERCOT_WEATHER_ZONES",
    "UNIT_SUM_TOLERANCE",
    "AreaWeight",
    "AreaWeightProvider",
    "CoastlineDistanceProvider",
    "SpatialCrsContract",
    "StationSpatialRecord",
    "WeatherZone",
    "WeatherZoneAssigner",
    "enrich_station_spatial_record",
    "validate_area_weight_partition",
]
