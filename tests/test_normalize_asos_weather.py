import importlib.util
from pathlib import Path

import pandas as pd
import pytest


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "normalize_asos_weather.py"
SPEC = importlib.util.spec_from_file_location("normalize_asos_weather", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"Unable to import normalizer from {SCRIPT_PATH}")
normalize_asos_weather = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(normalize_asos_weather)


def _row(station="AAA", valid="2024-01-01 00:00", **overrides):
    row = {
        "station": station,
        "valid": valid,
        "lon": -100.0,
        "lat": 30.0,
        "elevation": 100.0,
        "tmpf": 50.0,
        "dwpf": 40.0,
        "relh": 60.0,
        "feel": 49.0,
        "skyc1": "CLR",
        "skyl1": "M",
        "p01i": 0.0,
        "mslp": 30.0,
        "alti": 30.1,
        "vsby": 10.0,
    }
    row.update(overrides)
    return row


def _write_state_file(raw_dir, state_code, rows):
    directory = raw_dir / f"weather_{state_code}_asos"
    directory.mkdir(parents=True)
    path = directory / f"{state_code}_asos_land_2024_01.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_normalize_chunk_parses_utc_and_preserves_pressure_fields():
    frame = pd.DataFrame([_row()])

    normalized, quality = normalize_asos_weather.normalize_chunk(frame, "tx", "tx.csv")

    assert normalized["timestamp_utc"].iloc[0] == pd.Timestamp("2024-01-01 00:00", tz="UTC")
    assert normalized["mslp"].iloc[0] == 30.0
    assert normalized["alti"].iloc[0] == 30.1
    assert normalized["source_state"].iloc[0] == "TX"
    assert quality["missing_skyl1"] == 1


def test_normalize_chunk_collapses_identical_duplicates():
    frame = pd.DataFrame([_row(), _row()])

    normalized, quality = normalize_asos_weather.normalize_chunk(frame, "tx", "tx.csv")

    assert len(normalized) == 1
    assert quality["duplicate_station_hours"] == 2
    assert quality["identical_duplicate_station_hours_collapsed"] == 1


def test_normalize_chunk_rejects_conflicting_duplicates():
    frame = pd.DataFrame([_row(), _row(tmpf=51.0)])

    with pytest.raises(ValueError, match="Conflicting duplicate"):
        normalize_asos_weather.normalize_chunk(frame, "tx", "tx.csv")


def test_normalize_weather_writes_month_partition_and_report(tmp_path):
    raw_dir = tmp_path / "raw"
    _write_state_file(raw_dir, "tx", [_row(), _row("BBB", "2024-02-01 00:00")])
    output_dir = tmp_path / "weather_station_hourly"

    report = normalize_asos_weather.normalize_weather(
        raw_dir,
        output_dir,
        state_codes=("tx",),
        chunk_size=1,
    )

    output_files = sorted(output_dir.rglob("*.parquet"))
    assert len(output_files) == 2
    assert report["row_count_read"] == 2
    assert report["row_count_written"] == 2
    assert report["station_count"] == 2
    assert (output_dir / "quality_report.json").exists()
    assert set(pd.read_parquet(output_files[0])["source_state"]) == {"TX"}


def test_normalize_weather_collapses_duplicates_across_chunks(tmp_path):
    raw_dir = tmp_path / "raw"
    _write_state_file(raw_dir, "tx", [_row(), _row()])
    output_dir = tmp_path / "weather_station_hourly"

    report = normalize_asos_weather.normalize_weather(
        raw_dir,
        output_dir,
        state_codes=("tx",),
        chunk_size=1,
    )

    assert report["row_count_read"] == 2
    assert report["row_count_written"] == 1
    assert report["quality"]["duplicate_station_hours_across_chunks"] == 1


def test_normalize_weather_requires_overwrite_for_existing_output(tmp_path):
    raw_dir = tmp_path / "raw"
    _write_state_file(raw_dir, "tx", [_row()])
    output_dir = tmp_path / "weather_station_hourly"
    output_dir.mkdir()
    (output_dir / "existing.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError, match="use --overwrite"):
        normalize_asos_weather.normalize_weather(raw_dir, output_dir, state_codes=("tx",))
