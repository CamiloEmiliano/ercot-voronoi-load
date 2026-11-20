"""Weather imputation configuration, contracts, and shared types."""

from bayes_demand_fcst.weather.calibration import (
    CALIBRATION_REFERENCE_UTC,
    DEFAULT_COVARIANCE_PRIORS,
    DEFAULT_HARMONIC_PERIODS_HOURS,
    DEFAULT_IDW_PARAMETERS,
    DEFAULT_TRAIN_END_UTC,
    HarmonicModelFit,
    fit_harmonic_model,
    harmonic_design_matrix,
    predict_harmonic_model,
)
from bayes_demand_fcst.weather.calibration_store import build_weather_calibration
from bayes_demand_fcst.weather.causality import (
    CausalNeighborhoodQuery,
    ObservationMode,
    WeatherImputationConfig,
    WeatherObservation,
    filter_causal_observations,
    require_utc_datetime,
)
from bayes_demand_fcst.weather.clean_store import (
    build_weather_irregular_clean,
    clean_asos_frame,
    find_weather_source_files,
)
from bayes_demand_fcst.weather.contracts import (
    WEATHER_CALIBRATION_HARMONIC_SCHEMA,
    WEATHER_IRREGULAR_CLEAN_SCHEMA,
    WEATHER_STATION_HOURLY_SCHEMA,
    weather_calibration_harmonic_schema,
    weather_irregular_clean_schema,
    weather_station_hourly_schema,
)
from bayes_demand_fcst.weather.covariance import (
    CovarianceKernel,
    SpaceTimeCovariance,
    build_covariance_matrix,
    covariance_to_target,
)
from bayes_demand_fcst.weather.interpolation import prior_shrunk_idw
from bayes_demand_fcst.weather.kriging import KrigingResult, ordinary_kriging, simple_kriging
from bayes_demand_fcst.weather.neighborhoods import (
    nearest_causal_observation,
    select_causal_neighborhood,
)
from bayes_demand_fcst.weather.provenance import WeatherCleanProvenance, WeatherProvenance
from bayes_demand_fcst.weather.space_time import SpaceTimeObservation, SpaceTimePoint

__all__ = [
    "CausalNeighborhoodQuery",
    "CovarianceKernel",
    "CALIBRATION_REFERENCE_UTC",
    "DEFAULT_COVARIANCE_PRIORS",
    "DEFAULT_HARMONIC_PERIODS_HOURS",
    "DEFAULT_IDW_PARAMETERS",
    "DEFAULT_TRAIN_END_UTC",
    "HarmonicModelFit",
    "KrigingResult",
    "ObservationMode",
    "SpaceTimeCovariance",
    "SpaceTimeObservation",
    "SpaceTimePoint",
    "WEATHER_CALIBRATION_HARMONIC_SCHEMA",
    "WEATHER_IRREGULAR_CLEAN_SCHEMA",
    "WEATHER_STATION_HOURLY_SCHEMA",
    "WeatherCleanProvenance",
    "WeatherImputationConfig",
    "WeatherObservation",
    "WeatherProvenance",
    "build_covariance_matrix",
    "build_weather_calibration",
    "build_weather_irregular_clean",
    "clean_asos_frame",
    "covariance_to_target",
    "filter_causal_observations",
    "find_weather_source_files",
    "fit_harmonic_model",
    "harmonic_design_matrix",
    "nearest_causal_observation",
    "ordinary_kriging",
    "predict_harmonic_model",
    "prior_shrunk_idw",
    "require_utc_datetime",
    "select_causal_neighborhood",
    "simple_kriging",
    "weather_calibration_harmonic_schema",
    "weather_irregular_clean_schema",
    "weather_station_hourly_schema",
]
