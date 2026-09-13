import importlib.util
from pathlib import Path

import pandas as pd
import pytest


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "aggregate_regional_weather.py"
SPEC = importlib.util.spec_from_file_location("aggregate_regional_weather", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"Unable to import regional aggregator from {SCRIPT_PATH}")
aggregate_regional_weather = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(aggregate_regional_weather)


def _weights() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "configuration_id": ["config-1", "config-1"],
            "station_id": ["A", "B"],
            "station_aliases": ["A,A_ALIAS", "B"],
            "area_weight": [0.25, 0.75],
        }
    )


def _weather() -> pd.DataFrame:
    rows = []
    for station_id, tmpf, mslp in (("A_ALIAS", 40.0, 30.0), ("B", 60.0, None)):
        rows.append(
            {
                "station_id": station_id,
                "timestamp_utc": "2024-01-01 00:00Z",
                "tmpf": tmpf,
                "dwpf": 30.0,
                "relh": 50.0,
                "feel": tmpf,
                "p01i": 0.0,
                "mslp": mslp,
                "alti": 30.1,
                "vsby": 10.0,
            }
        )
    rows.append({**rows[0], "timestamp_utc": "2024-01-01 01:00Z", "tmpf": None, "mslp": None})
    rows[-1]["station_id"] = "B"
    return pd.DataFrame(rows)


def test_aggregate_weather_expands_aliases_and_renormalizes_per_variable():
    result, report = aggregate_regional_weather.aggregate_weather_frame(_weather(), _weights())

    first = result.iloc[0]
    assert first["tmpf_regional"] == pytest.approx(55.0)
    assert first["mslp_regional"] == pytest.approx(30.0)
    assert first["tmpf_coverage"] == pytest.approx(1.0)
    assert first["mslp_coverage"] == pytest.approx(0.25)
    assert first["active_station_count"] == 2
    assert report["regional_rows_written"] == 2


def test_aggregate_weather_preserves_zero_coverage_as_missing():
    result, report = aggregate_regional_weather.aggregate_weather_frame(_weather(), _weights())

    second = result.iloc[1]
    assert pd.isna(second["tmpf_regional"])
    assert second["weather_coverage"] == pytest.approx(0.5625)
    assert report["timestamps_with_zero_weather_coverage"] == 0


def test_aggregate_regional_weather_writes_month_partition(tmp_path):
    weather_dir = tmp_path / "weather" / "year=2024" / "month=01"
    weather_dir.mkdir(parents=True)
    _weather().to_parquet(weather_dir / "part.parquet", index=False)
    weights_path = tmp_path / "weights.parquet"
    _weights().to_parquet(weights_path, index=False)
    output_dir = tmp_path / "regional"

    report = aggregate_regional_weather.aggregate_regional_weather(
        tmp_path / "weather", weights_path, output_dir
    )

    assert report["partitions_processed"] == 1
    assert (output_dir / "year=2024/month=01/regional.parquet").exists()
    assert (output_dir / "quality_report.json").exists()


def test_aggregate_regional_weather_requires_weather_partitions(tmp_path):
    weights_path = tmp_path / "weights.parquet"
    _weights().to_parquet(weights_path, index=False)

    with pytest.raises(FileNotFoundError, match="No weather partitions"):
        aggregate_regional_weather.aggregate_regional_weather(
            tmp_path / "missing", weights_path, tmp_path / "output"
        )
