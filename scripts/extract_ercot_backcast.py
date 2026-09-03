"""Convert quarterly ERCOT backcast workbooks into interim Parquet files."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = PROJECT_ROOT / "data/raw/ercot_backcast"
QUARTER_SHEET_PATTERN = re.compile(r"^Q([1-4])_(20\d{2})$")
YEAR_PATTERN = re.compile(r"\b(20\d{2})\b")
# bespoke month sheet names for each year because ERCOT is inconsistent.
MONTH_SHEETS_2018 = (
    "Jan", "Feb", "Mar",
    "Apr", "May", "June",
    "July", "Aug", "SEP",
    "OCT", "NOV", "DEC"
)
MONTH_SHEETS_2019 = (
    "Jan", "FEB", "MAR",
    "APR", "May", "June",
    "July", "August", "September",
    "October", "November", "December"
)
MONTH_SHEETS_2020 = (
    "January", "February", "March",
    "April", "May", "June",
    "July", "August", "September",
    "October", "November", "December"
)
MONTH_SHEETS_2021 = (
    "January", "February", "March",
    "April", "May", "June",
    "July", "August", "SEPTEMBER",
    "OCTOBER", "NOVEMBER", "DECEMBER"
)
MONTH_SHEETS_2022 = (
    "January", "February", "March",
    "April", "May", "June",
    "July", "August", "September",
    "October", "November", "December"
)
MONTH_SHEETS_2023 = (
    "January", "February", "March",
    "April", "May", "June",
    "July", "August", "September",
    "October", "November", "December"
)
MONTH_SHEETS_2024 = (
    "January", "February", "March",
    "April", "May", "June",
    "July", "August", "September",
    "October", "November", "December"
)
# this year contains an extra tab titled "September", which is ignored
MONTH_SHEETS_2025 = (
    "January", "February", "March",
    "April", "May", "June",
    "July", "August", "Sep",
    "Oct", "Nov", "Dec"
)


def get_workbook_year(workbook_path: Path) -> int:
    """Extract the four-digit year embedded in an ERCOT workbook filename."""
    match = YEAR_PATTERN.search(workbook_path.name)
    if match is None:
        raise ValueError(f"Unable to determine year from workbook name: {workbook_path.name}")
    return int(match.group(1))


def open_workbook(workbook_path: Path) -> pd.ExcelFile:
    """Open a workbook, retrying mislabeled OOXML files with openpyxl."""
    try:
        return pd.ExcelFile(workbook_path)
    except Exception:
        return pd.ExcelFile(workbook_path, engine="openpyxl")


def add_provenance(dataframe: pd.DataFrame, workbook_name: str, sheet_names: str) -> pd.DataFrame:
    """Append source workbook and worksheet metadata without fragmenting the frame."""
    provenance = pd.DataFrame(
        {
            "source_workbook": workbook_name,
            "source_sheet": sheet_names,
        },
        index=dataframe.index,
    )
    return pd.concat((dataframe, provenance), axis=1)


def write_parquet(dataframe: pd.DataFrame, destination: Path) -> None:
    """Write one interim Parquet file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    dataframe.to_parquet(destination, index=False, engine="pyarrow", compression="zstd")
    print(f"wrote {len(dataframe):,} rows to {destination}.")


def extract_workbook(workbook_path: Path, interim_dir: Path, overwrite: bool = False) -> list[Path]:
    """Convert each quarterly worksheet in an ERCOT workbook to Parquet."""
    workbook_year = get_workbook_year(workbook_path)
    excel_file = open_workbook(workbook_path)
    outputs: list[Path] = []

    month_sheets = {
        2018: MONTH_SHEETS_2018,
        2019: MONTH_SHEETS_2019,
        2020: MONTH_SHEETS_2020,
        2021: MONTH_SHEETS_2021,
        2022: MONTH_SHEETS_2022,
        2023: MONTH_SHEETS_2023,
        2024: MONTH_SHEETS_2024,
        2025: MONTH_SHEETS_2025,
    }.get(workbook_year)
    if month_sheets is not None:
        for month_number, sheet_name in enumerate(month_sheets, start=1):
            destination = interim_dir / str(workbook_year) / f"m{month_number:02d}.parquet"
            if destination.exists() and not overwrite:
                print(f"{sheet_name}: already exists; skipping {destination}.")
                outputs.append(destination)
                continue
            dataframe = pd.read_excel(excel_file, sheet_name=sheet_name, parse_dates=["Date", "ADDTIME"])
            dataframe = add_provenance(dataframe, workbook_path.name, sheet_name)
            write_parquet(dataframe, destination)
            outputs.append(destination)
        return outputs

    for sheet_name in excel_file.sheet_names:
        if not isinstance(sheet_name, str):
            continue
        match = QUARTER_SHEET_PATTERN.fullmatch(sheet_name)
        if match is None:
            continue

        quarter, sheet_year = match.groups()
        if int(sheet_year) != workbook_year:
            raise ValueError(
                f"Worksheet {sheet_name} does not match the workbook year {workbook_year}: {workbook_path.name}"
            )

        destination = interim_dir / sheet_year / f"q{quarter}.parquet"
        if destination.exists() and not overwrite:
            print(f"{sheet_name}: already exists; skipping {destination}.")
            outputs.append(destination)
            continue

        dataframe = pd.read_excel(excel_file, sheet_name=sheet_name, parse_dates=["Date", "ADDTIME"])
        dataframe = add_provenance(dataframe, workbook_path.name, sheet_name)
        write_parquet(dataframe, destination)
        outputs.append(destination)

    if not outputs:
        raise ValueError(f"No quarterly worksheets matching Q[1-4]_YYYY found in {workbook_path.name}")
    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--year", type=int, action="append", help="Extract only this year; can be repeated.")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing quarterly Parquet files.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.raw_dir.is_dir():
        raise FileNotFoundError(f"Raw ERCOT directory does not exist: {args.raw_dir}")

    workbooks = sorted((*args.raw_dir.glob("*.xlsx"), *args.raw_dir.glob("*.xls")))
    if args.year:
        selected_years = set(args.year)
        workbooks = [path for path in workbooks if get_workbook_year(path) in selected_years]
    if not workbooks:
        raise FileNotFoundError("No matching ERCOT workbooks found.")

    interim_dir = Path.cwd()
    for workbook_path in workbooks:
        print(f"Processing {workbook_path.name}.")
        extract_workbook(workbook_path, interim_dir, overwrite=args.overwrite)


if __name__ == "__main__":
    main()