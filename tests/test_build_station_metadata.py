import pandas as pd
import pytest

from scripts.build_station_metadata import build_station_metadata


def _write_weather_file(directory, state_code, rows):
    directory.mkdir(parents=True)
    pd.DataFrame(rows).to_csv(directory / f"{state_code}_asos_land_2014_01.csv", index=False)


def test_build_station_metadata_writes_one_record_per_station(tmp_path):
    raw_dir = tmp_path / "raw"
    _write_weather_file(
        raw_dir / "weather_tx_asos",
        "tx",
        [
            {"station": "AAA", "valid": "2014-01-01 00:00", "lon": -100.0, "lat": 30.0, "elevation": 100.0},
            {"station": "AAA", "valid": "2014-01-01 01:00", "lon": -100.0, "lat": 30.0, "elevation": 100.0},
        ],
    )
    _write_weather_file(
        raw_dir / "weather_nm_asos",
        "nm",
        [{"station": "BBB", "valid": "2014-01-01 00:00", "lon": -105.0, "lat": 35.0, "elevation": 200.0}],
    )
    output = tmp_path / "station_metadata.parquet"

    metadata = build_station_metadata(raw_dir, output, state_codes=("tx", "nm"))

    assert output.exists()
    assert metadata["station_id"].tolist() == ["AAA", "BBB"]
    assert metadata.loc[0, "source_state"] == "TX"
    assert metadata.loc[0, "observation_count"] == 2
    assert metadata.loc[0, "first_observed_utc"] == pd.Timestamp("2014-01-01 00:00", tz="UTC")
    assert metadata.loc[0, "last_observed_utc"] == pd.Timestamp("2014-01-01 01:00", tz="UTC")
    assert pd.read_parquet(output).equals(metadata)


def test_build_station_metadata_rejects_changing_station_coordinates(tmp_path):
    raw_dir = tmp_path / "raw"
    _write_weather_file(
        raw_dir / "weather_tx_asos",
        "tx",
        [
            {"station": "AAA", "valid": "2014-01-01 00:00", "lon": -100.0, "lat": 30.0, "elevation": 100.0},
            {"station": "AAA", "valid": "2014-01-01 01:00", "lon": -99.0, "lat": 30.0, "elevation": 100.0},
        ],
    )

    with pytest.raises(ValueError, match="Station metadata changes over time: AAA"):
        build_station_metadata(raw_dir, tmp_path / "station_metadata.parquet", state_codes=("tx",))