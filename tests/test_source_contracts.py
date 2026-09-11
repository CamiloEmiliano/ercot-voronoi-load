import importlib.util
from pathlib import Path

import pandas as pd
import pytest


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "source_contracts.py"
SPEC = importlib.util.spec_from_file_location("source_contracts", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"Unable to import source contracts from {SCRIPT_PATH}")
source_contracts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source_contracts)


def _asos_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "station": ["AAA"],
            "valid": ["2024-01-01 00:00"],
            "lon": [-100.0],
            "lat": [30.0],
            "elevation": [100.0],
            "tmpf": [50.0],
            "dwpf": [40.0],
            "relh": [60.0],
            "feel": [49.0],
            "skyc1": ["CLR"],
            "skyl1": ["M"],
            "p01i": [0.0],
            "mslp": [30.0],
            "alti": [30.1],
            "vsby": [10.0],
        }
    )


def _load_profile_frame() -> pd.DataFrame:
    frame = {
        "PType_WZ": ["NORTH"],
        "Date": ["2024-01-01"],
        "ADDTIME": ["2024-01-02"],
        "source_workbook": ["source.xlsx"],
        "source_sheet": ["Q1_2024"],
    }
    frame.update({column: [1.0] for column in source_contracts.LOAD_INTERVAL_COLUMNS})
    return pd.DataFrame(frame)


def test_asos_contract_accepts_current_schema_and_parses_valid_as_utc():
    frame = _asos_frame()

    source_contracts.validate_asos_frame(frame)
    parsed = source_contracts.parse_asos_valid(frame["valid"])

    assert parsed.iloc[0] == pd.Timestamp("2024-01-01 00:00", tz="UTC")


def test_asos_contract_requires_altimeter_and_mslp():
    frame = _asos_frame().drop(columns=["alti"])

    with pytest.raises(ValueError, match="alti"):
        source_contracts.validate_asos_frame(frame)


def test_load_contract_accepts_wide_profile_without_assigning_timezone():
    frame = _load_profile_frame()

    source_contracts.validate_load_profile_frame(frame)

    assert not pd.api.types.is_datetime64_any_dtype(frame["Date"])
    assert not pd.api.types.is_datetime64_any_dtype(frame["ADDTIME"])


def test_load_contract_requires_all_profile_columns():
    frame = _load_profile_frame().drop(columns=["int_kWh100"])

    with pytest.raises(ValueError, match="int_kWh100"):
        source_contracts.validate_load_profile_frame(frame)
