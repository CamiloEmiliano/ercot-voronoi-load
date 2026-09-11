"""Source schema contracts for the first data-preparation phase."""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

ASOS_REQUIRED_COLUMNS = (
    "station",
    "valid",
    "lon",
    "lat",
    "elevation",
    "tmpf",
    "dwpf",
    "relh",
    "feel",
    "skyc1",
    "skyl1",
    "p01i",
    "mslp",
    "alti",
    "vsby",
)
ASOS_NUMERIC_COLUMNS = tuple(
    column for column in ASOS_REQUIRED_COLUMNS if column not in {"station", "valid", "skyc1", "skyl1"}
)
LOAD_PROFILE_COLUMNS = ("PType_WZ", "Date", "ADDTIME", "source_workbook", "source_sheet")
LOAD_INTERVAL_COLUMNS = tuple(f"int_kWh{number}" for number in range(1, 101))


def missing_columns(columns: Iterable[str], required: Iterable[str]) -> tuple[str, ...]:
    """Return required columns that are absent, preserving contract order."""
    available = set(columns)
    return tuple(column for column in required if column not in available)


def validate_asos_columns(columns: Iterable[str]) -> None:
    """Require the complete ASOS schema used by the download scripts."""
    missing = missing_columns(columns, ASOS_REQUIRED_COLUMNS)
    if missing:
        raise ValueError(f"ASOS schema is missing columns: {', '.join(missing)}")


def validate_load_profile_columns(columns: Iterable[str]) -> None:
    """Require the current wide ERCOT backcast profile schema."""
    required = (*LOAD_PROFILE_COLUMNS, *LOAD_INTERVAL_COLUMNS)
    missing = missing_columns(columns, required)
    if missing:
        raise ValueError(f"ERCOT load profile schema is missing columns: {', '.join(missing)}")


def parse_asos_valid(values: pd.Series) -> pd.Series:
    """Parse the ASOS `valid` field as UTC, rejecting invalid timestamps."""
    parsed = pd.to_datetime(values, utc=True, errors="coerce")
    if parsed.isna().any():
        raise ValueError(f"ASOS valid field contains {int(parsed.isna().sum())} invalid timestamps")
    return parsed


def validate_asos_frame(frame: pd.DataFrame) -> None:
    """Validate ASOS columns, timestamp parsing, and numeric conversion."""
    validate_asos_columns(frame.columns)
    parse_asos_valid(frame["valid"])
    for column in ASOS_NUMERIC_COLUMNS:
        pd.to_numeric(frame[column], errors="raise")


def validate_load_profile_frame(frame: pd.DataFrame) -> None:
    """Validate the extracted wide load profile without assigning timezone semantics."""
    validate_load_profile_columns(frame.columns)
    pd.to_datetime(frame["Date"], errors="raise")
    pd.to_datetime(frame["ADDTIME"], errors="raise")
    for column in LOAD_INTERVAL_COLUMNS:
        pd.to_numeric(frame[column], errors="raise")
