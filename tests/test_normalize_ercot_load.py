import importlib.util
from pathlib import Path

import pandas as pd
import pytest


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "normalize_ercot_load.py"
SPEC = importlib.util.spec_from_file_location("normalize_ercot_load", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"Unable to import load normalizer from {SCRIPT_PATH}")
normalize_ercot_load = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(normalize_ercot_load)


def _wide_frame() -> pd.DataFrame:
    frame = {
        "PType_WZ": ["NORTH", "SOUTH"],
        "Date": ["2024-01-01", "2024-01-01"],
        "ADDTIME": ["2024-01-02", "2024-01-02"],
        "source_workbook": ["source.xlsx", "source.xlsx"],
        "source_sheet": ["Q1_2024", "Q1_2024"],
    }
    frame.update(
        {
            column: [float(index), float(index + 10)]
            for index, column in enumerate(
                normalize_ercot_load.LOAD_SOURCE_INTERVAL_COLUMNS, start=1
            )
        }
    )
    return pd.DataFrame(frame)


def test_normalize_profile_melts_intervals_and_preserves_source_time():
    normalized = normalize_ercot_load.normalize_profile(_wide_frame(), "2014/q1.parquet")

    assert len(normalized) == 200
    assert normalized["interval_number"].min() == 1
    assert normalized["interval_number"].max() == 100
    assert normalized["date_source_naive"].dt.tz is None
    assert normalized["addtime_source_naive"].dt.tz is None
    assert normalized["interval_value_kwh_source"].iloc[0] == 1.0
    assert set(normalized["source_file"]) == {"2014/q1.parquet"}


def test_normalize_load_writes_partitioned_long_output(tmp_path):
    input_dir = tmp_path / "input" / "2024"
    input_dir.mkdir(parents=True)
    source = input_dir / "q1.parquet"
    _wide_frame().to_parquet(source, index=False)
    output_dir = tmp_path / "output"

    report = normalize_ercot_load.normalize_load(tmp_path / "input", output_dir)

    output = pd.read_parquet(output_dir / "2024/q1.parquet")
    assert len(output) == 200
    assert report["files_processed"] == 1
    assert report["rows_read"] == 2
    assert report["rows_written"] == 200
    assert report["interval_duration_minutes"] == 15
    assert report["unit_semantics"] == "kWh per 15-minute interval"
    assert report["timestamp_semantics"].startswith("preserved")
    assert (output_dir / "quality_report.json").exists()


def test_normalize_load_requires_overwrite_for_existing_output(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    _wide_frame().to_parquet(input_dir / "q1.parquet", index=False)
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "existing.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError, match="use --overwrite"):
        normalize_ercot_load.normalize_load(input_dir, output_dir)


def test_normalize_load_requires_extracted_input(tmp_path):
    with pytest.raises(FileNotFoundError, match="No extracted ERCOT Parquet"):
        normalize_ercot_load.normalize_load(tmp_path / "missing", tmp_path / "output")
