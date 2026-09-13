import importlib.util
from pathlib import Path

import pandas as pd
import pytest


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "assemble_observed_panel.py"
SPEC = importlib.util.spec_from_file_location("assemble_observed_panel", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"Unable to import panel assembler from {SCRIPT_PATH}")
assemble_observed_panel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assemble_observed_panel)


def _load():
    return pd.DataFrame(
        {
            "timestamp_utc": ["2024-01-01 00:00Z", "2024-01-01 01:00Z"],
            "load_mw": [100.0, 110.0],
            "source_file": ["load.parquet", "load.parquet"],
        }
    )


def _weather():
    return pd.DataFrame(
        {
            "timestamp_utc": ["2024-01-01 00:00Z", "2024-01-01 01:00Z"],
            "tmpf_regional": [50.0, 51.0],
            "weather_coverage": [1.0, 0.9],
        }
    )


def test_assemble_panel_joins_one_to_one_and_normalizes_utc():
    panel, report = assemble_observed_panel.assemble_panel(_load(), _weather())

    assert len(panel) == 2
    assert panel["timestamp_utc"].dt.tz is not None
    assert str(panel["timestamp_utc"].dt.tz) == "UTC"
    assert report["panel_rows"] == 2
    assert panel["load_mw"].tolist() == [100.0, 110.0]


def test_assemble_panel_rejects_noncanonical_load():
    load = _load().rename(columns={"load_mw": "interval_value_kwh_source"})

    with pytest.raises(ValueError, match="resolve Phase 3"):
        assemble_observed_panel.assemble_panel(load, _weather())


def test_assemble_panel_rejects_duplicate_weather_timestamps():
    weather = pd.concat([_weather(), _weather().iloc[[0]]], ignore_index=True)

    with pytest.raises(ValueError, match="weather contains duplicate"):
        assemble_observed_panel.assemble_panel(_load(), weather)


def test_assemble_observed_panel_writes_month_partition(tmp_path):
    load_dir = tmp_path / "load"
    weather_dir = tmp_path / "weather"
    load_dir.mkdir()
    weather_dir.mkdir()
    _load().to_parquet(load_dir / "load.parquet", index=False)
    _weather().to_parquet(weather_dir / "weather.parquet", index=False)
    output_dir = tmp_path / "panel"

    report = assemble_observed_panel.assemble_observed_panel(load_dir, weather_dir, output_dir)

    assert report["panel_rows"] == 2
    assert (output_dir / "year=2024/month=01/panel.parquet").exists()
    assert (output_dir / "quality_report.json").exists()


def test_assemble_observed_panel_requires_both_inputs(tmp_path):
    with pytest.raises(FileNotFoundError, match="No canonical load"):
        assemble_observed_panel.assemble_observed_panel(
            tmp_path / "missing", tmp_path / "weather", tmp_path / "output"
        )
