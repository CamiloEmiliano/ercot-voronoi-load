"""Versioned contracts for the raw-clean and hourly weather artifacts."""

from bayes_demand_fcst.schema import DatasetSchema, FieldSchema


WEATHER_IRREGULAR_CLEAN_SCHEMA = DatasetSchema(
    name="weather_irregular_clean",
    version=1,
    fields=(
        FieldSchema("station_id", "string", nullable=False),
        FieldSchema("observed_at_utc", "timestamp[UTC]", nullable=False),
        FieldSchema("source_state", "string", nullable=False),
        FieldSchema("source_file", "string", nullable=False),
        FieldSchema("longitude_wgs84", "float64", nullable=False),
        FieldSchema("latitude_wgs84", "float64", nullable=False),
        FieldSchema("elevation_m", "float64"),
        FieldSchema("tmpf", "float64"),
        FieldSchema("dwpf", "float64"),
        FieldSchema("relh", "float64"),
        FieldSchema("feel", "float64"),
        FieldSchema("feel_diff", "float64"),
        FieldSchema("skyc1", "string"),
        FieldSchema("skyl1", "float64"),
        FieldSchema("cloud_frac", "float64"),
        FieldSchema("p01i", "float64"),
        FieldSchema("mslp", "float64"),
        FieldSchema("alti", "float64"),
        FieldSchema("alti_hpa", "float64"),
        FieldSchema("delta_p", "float64"),
        FieldSchema("vsby", "float64"),
        FieldSchema("dwpf_provenance", "uint8", nullable=False),
        FieldSchema("feel_diff_provenance", "uint8", nullable=False),
        FieldSchema("cloud_frac_provenance", "uint8", nullable=False),
        FieldSchema("p01i_provenance", "uint8", nullable=False),
        FieldSchema("mslp_provenance", "uint8", nullable=False),
        FieldSchema("alti_provenance", "uint8", nullable=False),
        FieldSchema("alti_hpa_provenance", "uint8", nullable=False),
        FieldSchema("delta_p_provenance", "uint8", nullable=False),
        FieldSchema("vsby_provenance", "uint8", nullable=False),
    ),
    primary_key=("station_id", "observed_at_utc"),
)

WEATHER_STATION_HOURLY_SCHEMA = DatasetSchema(
    name="weather_station_hourly",
    version=1,
    fields=(
        FieldSchema("station_id", "string", nullable=False),
        FieldSchema("timestamp_utc", "timestamp[UTC]", nullable=False),
        FieldSchema("tmpf", "float32"),
        FieldSchema("dwpf", "float32"),
        FieldSchema("relh", "float32"),
        FieldSchema("feel", "float32"),
        FieldSchema("feel_diff", "float32"),
        FieldSchema("alti", "float32"),
        FieldSchema("mslp", "float32"),
        FieldSchema("p01i", "float32"),
        FieldSchema("vsby", "float32"),
        FieldSchema("cloud_frac", "float32"),
        FieldSchema("tmpf_provenance", "uint8", nullable=False),
        FieldSchema("dwpf_provenance", "uint8", nullable=False),
        FieldSchema("feel_diff_provenance", "uint8", nullable=False),
        FieldSchema("alti_provenance", "uint8", nullable=False),
        FieldSchema("mslp_provenance", "uint8", nullable=False),
        FieldSchema("p01i_provenance", "uint8", nullable=False),
        FieldSchema("vsby_provenance", "uint8", nullable=False),
        FieldSchema("cloud_frac_provenance", "uint8", nullable=False),
        FieldSchema("tmpf_kriging_variance", "float32"),
        FieldSchema("dwpf_kriging_variance", "float32"),
        FieldSchema("feel_diff_kriging_variance", "float32"),
        FieldSchema("alti_kriging_variance", "float32"),
        FieldSchema("delta_p_kriging_variance", "float32"),
        FieldSchema("mslp_kriging_variance", "float32"),
    ),
    primary_key=("station_id", "timestamp_utc"),
)

WEATHER_CALIBRATION_HARMONIC_SCHEMA = DatasetSchema(
    name="weather_calibration_harmonic",
    version=1,
    fields=(
        FieldSchema("station_id", "string", nullable=False),
        FieldSchema("variable", "string", nullable=False),
        FieldSchema("train_end_utc", "timestamp[UTC]", nullable=False),
        FieldSchema("reference_utc", "timestamp[UTC]", nullable=False),
        FieldSchema("sample_count", "int64", nullable=False),
        FieldSchema("design_matrix_condition", "float64", nullable=False),
        FieldSchema("coefficient_00", "float64", nullable=False),
        FieldSchema("coefficient_01", "float64", nullable=False),
        FieldSchema("coefficient_02", "float64", nullable=False),
        FieldSchema("coefficient_03", "float64", nullable=False),
        FieldSchema("coefficient_04", "float64", nullable=False),
        FieldSchema("coefficient_05", "float64", nullable=False),
        FieldSchema("coefficient_06", "float64", nullable=False),
    ),
    primary_key=("station_id", "variable"),
)

weather_irregular_clean_schema = WEATHER_IRREGULAR_CLEAN_SCHEMA
weather_station_hourly_schema = WEATHER_STATION_HOURLY_SCHEMA
weather_calibration_harmonic_schema = WEATHER_CALIBRATION_HARMONIC_SCHEMA
