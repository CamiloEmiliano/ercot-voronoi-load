from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from inspect import Parameter, signature
from typing import cast

import pytest

from bayes_demand_fcst.spatial import (
    EPSG_METRIC,
    EPSG_WGS84,
    ERCOT_WEATHER_ZONES,
    AreaWeight,
    SpatialCrsContract,
    StationSpatialRecord,
    WeatherZone,
    enrich_station_spatial_record,
    validate_area_weight_partition,
)
from bayes_demand_fcst.weather import (
    CausalNeighborhoodQuery,
    ObservationMode,
    WeatherImputationConfig,
    WeatherProvenance,
    WeatherObservation,
    filter_causal_observations,
    weather_irregular_clean_schema,
    weather_station_hourly_schema,
)

UTC = timezone.utc


@dataclass(frozen=True, slots=True)
class FakeCoastlineProvider:
    distance: float

    def distance_km(self, longitude_wgs84: float, latitude_wgs84: float) -> float:
        return self.distance


@dataclass(frozen=True, slots=True)
class FakeZoneAssigner:
    zone: WeatherZone

    def assign(self, longitude_wgs84: float, latitude_wgs84: float) -> WeatherZone:
        return self.zone


@pytest.fixture
def target_hour() -> datetime:
    return datetime(2021, 1, 1, 16, 0, tzinfo=UTC)


@pytest.fixture
def synthetic_observations(target_hour: datetime) -> tuple[WeatherObservation, ...]:
    return (
        WeatherObservation(station_id="AAA", observed_at_utc=target_hour - timedelta(minutes=45)),
        WeatherObservation(station_id="AAA", observed_at_utc=target_hour),
        WeatherObservation(station_id="BBB", observed_at_utc=target_hour + timedelta(minutes=1)),
    )


def test_causal_neighborhood_excludes_future_observations(
    target_hour: datetime, synthetic_observations: tuple[WeatherObservation, ...]
) -> None:
    query = CausalNeighborhoodQuery(target_at_utc=target_hour, lookback=timedelta(hours=1))

    selected = filter_causal_observations(synthetic_observations, query)

    assert [observation.station_id for observation in selected] == ["AAA", "AAA"]
    assert query.accepts(synthetic_observations[0].observed_at_utc)
    assert not query.accepts(synthetic_observations[2].observed_at_utc)


def test_causal_neighborhood_can_require_an_observation_be_causal(target_hour: datetime) -> None:
    query = CausalNeighborhoodQuery(target_at_utc=target_hour, lookback=timedelta(hours=1))

    with pytest.raises(ValueError, match="future observation"):
        query.require(target_hour + timedelta(seconds=1))


def test_weather_run_config_rejects_naive_train_cutoff() -> None:
    with pytest.raises(ValueError, match="UTC-aware"):
        WeatherImputationConfig(
            mode=ObservationMode.CAUSAL,
            train_end_utc=datetime(2021, 12, 31, 23, 59, 59),
        )


def test_weather_run_config_requires_a_cutoff_in_every_mode() -> None:
    train_end = signature(WeatherImputationConfig).parameters["train_end_utc"]

    assert train_end.default is Parameter.empty


def test_weather_config_freezes_calibration_at_inclusive_cutoff(target_hour: datetime) -> None:
    config = WeatherImputationConfig(
        mode=ObservationMode.CAUSAL,
        train_end_utc=target_hour,
    )

    assert config.is_calibration_eligible(target_hour)
    assert not config.is_calibration_eligible(target_hour + timedelta(microseconds=1))


def test_provenance_values_are_stable_uint8_codes() -> None:
    assert [member.value for member in WeatherProvenance] == [0, 1, 2, 3]
    assert max(member.value for member in WeatherProvenance) <= 255


def test_weather_schemas_are_versioned_and_require_canonical_keys() -> None:
    assert weather_irregular_clean_schema.name == "weather_irregular_clean"
    assert weather_irregular_clean_schema.version == 1
    assert {"station_id", "observed_at_utc"} <= set(weather_irregular_clean_schema.required_columns)
    assert weather_station_hourly_schema.primary_key == ("station_id", "timestamp_utc")
    assert {"feel", "mslp_provenance", "mslp_kriging_variance"} <= set(
        weather_station_hourly_schema.required_columns
    )

    with pytest.raises(ValueError, match="missing columns"):
        weather_station_hourly_schema.validate_columns(("station_id",))


def test_spatial_contract_uses_canonical_crs_and_zone_keys() -> None:
    contract = SpatialCrsContract()

    assert contract.source == EPSG_WGS84
    assert contract.metric == EPSG_METRIC
    assert tuple(zone.value for zone in ERCOT_WEATHER_ZONES) == (
        "coast",
        "east",
        "fwest",
        "north",
        "ncent",
        "south",
        "scent",
        "west",
    )
    assert WeatherZone.parse("COAST") is WeatherZone.COAST
    assert WeatherZone.parse("fwest") is WeatherZone.FAR_WEST
    assert WeatherZone.outside_ercot() is WeatherZone.OUTSIDE_ERCOT


def test_station_enrichment_uses_geometry_interfaces() -> None:
    record = enrich_station_spatial_record(
        station_id="AAA",
        longitude_wgs84=-97.1,
        latitude_wgs84=32.9,
        elevation_m=180.0,
        coastline=FakeCoastlineProvider(distance=475.2),
        zones=FakeZoneAssigner(zone=WeatherZone.NORTH_CENTRAL),
    )

    assert record == StationSpatialRecord(
        station_id="AAA",
        longitude_wgs84=-97.1,
        latitude_wgs84=32.9,
        elevation_m=180.0,
        dist_coast_km=475.2,
        weather_zone=WeatherZone.NORTH_CENTRAL,
    )


def test_station_spatial_record_rejects_invalid_distance_and_zone() -> None:
    with pytest.raises(ValueError, match="dist_coast_km"):
        StationSpatialRecord(
            station_id="AAA",
            longitude_wgs84=-97.1,
            latitude_wgs84=32.9,
            elevation_m=180.0,
            dist_coast_km=-1.0,
            weather_zone=WeatherZone.NORTH_CENTRAL,
        )

    with pytest.raises(TypeError, match="WeatherZone"):
        StationSpatialRecord(
            station_id="AAA",
            longitude_wgs84=-97.1,
            latitude_wgs84=32.9,
            elevation_m=180.0,
            dist_coast_km=1.0,
            weather_zone=cast(WeatherZone, "ncent"),
        )


def test_area_weight_partition_rejects_duplicate_or_non_unit_weights() -> None:
    valid = (
        AreaWeight(station_id="AAA", scope="ercot", weight=0.6, cell_area_m2=60.0),
        AreaWeight(station_id="BBB", scope="ercot", weight=0.4, cell_area_m2=40.0),
    )
    assert validate_area_weight_partition(valid, scope="ercot") == pytest.approx(1.0)

    with pytest.raises(ValueError, match="duplicate station IDs"):
        validate_area_weight_partition((valid[0], valid[0]), scope="ercot")

    with pytest.raises(ValueError, match="sum to one"):
        validate_area_weight_partition((valid[0],), scope="ercot")
