import importlib.util
from pathlib import Path

import pandas as pd


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "extract_ercot_backcast.py"
SPEC = importlib.util.spec_from_file_location("extract_ercot_backcast", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"Unable to import extractor from {SCRIPT_PATH}")
extract_ercot_backcast = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(extract_ercot_backcast)


def test_extract_workbook_writes_quarterly_parquet_files(tmp_path):
    raw_path = tmp_path / "ERCOT Backcasted Load Profiles 2015.xlsx"
    interim_dir = tmp_path / "interim"
    dataframe = pd.DataFrame(
        {
            "PType_WZ": ["BUSHIDG_COAST"],
            "Date": ["2015-01-01"],
            "int_kWh1": [12.871],
            "ADDTIME": ["2015-01-02"],
        }
    )
    with pd.ExcelWriter(raw_path) as writer:
        dataframe.to_excel(writer, sheet_name="Q1_2015", index=False)
        dataframe.to_excel(writer, sheet_name="Q2_2015", index=False)

    outputs = extract_ercot_backcast.extract_workbook(raw_path, interim_dir)

    assert outputs == [interim_dir / "2015/q1.parquet", interim_dir / "2015/q2.parquet"]
    result = pd.read_parquet(outputs[0])
    assert result["source_workbook"].iloc[0] == raw_path.name
    assert result["source_sheet"].iloc[0] == "Q1_2015"
    assert result["int_kWh1"].iloc[0] == 12.871


def test_get_workbook_year_rejects_filename_without_year():
    invalid_path = Path("ERCOT Backcasted Load Profiles.xlsx")

    try:
        extract_ercot_backcast.get_workbook_year(invalid_path)
    except ValueError as error:
        assert "Unable to determine year" in str(error)
    else:
        raise AssertionError("Expected a workbook filename without a year to fail.")


def test_open_workbook_retries_a_mislabeled_xlsx_file(tmp_path):
    xlsx_path = tmp_path / "source.xlsx"
    mislabeled_path = tmp_path / "ERCOT Backcasted Load Profiles 2016.xls"
    pd.DataFrame({"Date": ["2016-01-01"]}).to_excel(xlsx_path, index=False)
    xlsx_path.rename(mislabeled_path)

    workbook = extract_ercot_backcast.open_workbook(mislabeled_path)

    assert workbook.sheet_names == ["Sheet1"]


def test_extract_workbook_preserves_2018_month_tabs(tmp_path):
    raw_path = tmp_path / "ERCOT Backcasted Load Profiles 2018.xlsx"
    interim_dir = tmp_path / "interim"
    with pd.ExcelWriter(raw_path) as writer:
        for month_number, sheet_name in enumerate(
            ("Jan", "Feb", "Mar", "Apr", "May", "June", "July", "Aug", "SEP", "OCT", "NOV", "DEC"),
            start=1,
        ):
            pd.DataFrame(
                {
                    "PType_WZ": ["BUSHIDG_COAST"],
                    "Date": [f"2018-{month_number:02d}-01"],
                    "int_kWh1": [month_number],
                    "ADDTIME": [f"2018-{month_number:02d}-02"],
                }
            ).to_excel(writer, sheet_name=sheet_name, index=False)

    outputs = extract_ercot_backcast.extract_workbook(raw_path, interim_dir)

    assert outputs == [interim_dir / "2018" / f"m{month_number:02d}.parquet" for month_number in range(1, 13)]
    january = pd.read_parquet(outputs[0])
    assert january["int_kWh1"].tolist() == [1]
    assert january["source_sheet"].iloc[0] == "Jan"


def test_extract_workbook_preserves_2019_month_tabs(tmp_path):
    raw_path = tmp_path / "ERCOT Backcasted Load Profiles 2019.xlsx"
    interim_dir = tmp_path / "interim"
    with pd.ExcelWriter(raw_path) as writer:
        for month_number, sheet_name in enumerate(extract_ercot_backcast.MONTH_SHEETS_2019, start=1):
            pd.DataFrame(
                {
                    "PType_WZ": ["BUSHIDG_COAST"],
                    "Date": [f"2019-{month_number:02d}-01"],
                    "int_kWh1": [month_number],
                    "ADDTIME": [f"2019-{month_number:02d}-02"],
                }
            ).to_excel(writer, sheet_name=sheet_name, index=False)

    outputs = extract_ercot_backcast.extract_workbook(raw_path, interim_dir)

    assert outputs == [interim_dir / "2019" / f"m{month_number:02d}.parquet" for month_number in range(1, 13)]
    february = pd.read_parquet(outputs[1])
    assert february["int_kWh1"].tolist() == [2]
    assert february["source_sheet"].iloc[0] == "FEB"


def test_extract_workbook_preserves_2020_month_tabs(tmp_path):
    raw_path = tmp_path / "ERCOT Backcasted Load Profiles 2020.xlsx"
    interim_dir = tmp_path / "interim"
    with pd.ExcelWriter(raw_path) as writer:
        for month_number, sheet_name in enumerate(extract_ercot_backcast.MONTH_SHEETS_2020, start=1):
            pd.DataFrame(
                {
                    "PType_WZ": ["BUSHIDG_COAST"],
                    "Date": [f"2020-{month_number:02d}-01"],
                    "int_kWh1": [month_number],
                    "ADDTIME": [f"2020-{month_number:02d}-02"],
                }
            ).to_excel(writer, sheet_name=sheet_name, index=False)

    outputs = extract_ercot_backcast.extract_workbook(raw_path, interim_dir)

    assert outputs == [interim_dir / "2020" / f"m{month_number:02d}.parquet" for month_number in range(1, 13)]
    december = pd.read_parquet(outputs[11])
    assert december["int_kWh1"].tolist() == [12]
    assert december["source_sheet"].iloc[0] == "December"


def test_extract_workbook_preserves_2021_month_tabs(tmp_path):
    raw_path = tmp_path / "ERCOT Backcasted Load Profiles 2021.xlsx"
    interim_dir = tmp_path / "interim"
    with pd.ExcelWriter(raw_path) as writer:
        for month_number, sheet_name in enumerate(extract_ercot_backcast.MONTH_SHEETS_2021, start=1):
            pd.DataFrame(
                {
                    "PType_WZ": ["BUSHIDG_COAST"],
                    "Date": [f"2021-{month_number:02d}-01"],
                    "int_kWh1": [month_number],
                    "ADDTIME": [f"2021-{month_number:02d}-02"],
                }
            ).to_excel(writer, sheet_name=sheet_name, index=False)

    outputs = extract_ercot_backcast.extract_workbook(raw_path, interim_dir)

    assert outputs == [interim_dir / "2021" / f"m{month_number:02d}.parquet" for month_number in range(1, 13)]
    december = pd.read_parquet(outputs[11])
    assert december["int_kWh1"].tolist() == [12]
    assert december["source_sheet"].iloc[0] == "DECEMBER"


def test_extract_workbook_preserves_2022_month_tabs(tmp_path):
    raw_path = tmp_path / "ERCOT Backcasted Load Profiles 2022.xlsx"
    interim_dir = tmp_path / "interim"
    with pd.ExcelWriter(raw_path) as writer:
        for month_number, sheet_name in enumerate(extract_ercot_backcast.MONTH_SHEETS_2022, start=1):
            pd.DataFrame(
                {
                    "PType_WZ": ["BUSHIDG_COAST"],
                    "Date": [f"2022-{month_number:02d}-01"],
                    "int_kWh1": [month_number],
                    "ADDTIME": [f"2022-{month_number:02d}-02"],
                }
            ).to_excel(writer, sheet_name=sheet_name, index=False)

    outputs = extract_ercot_backcast.extract_workbook(raw_path, interim_dir)

    assert outputs == [interim_dir / "2022" / f"m{month_number:02d}.parquet" for month_number in range(1, 13)]
    february = pd.read_parquet(outputs[1])
    assert february["int_kWh1"].tolist() == [2]
    assert february["source_sheet"].iloc[0] == "February"


def test_extract_workbook_uses_sep_not_september_for_2025(tmp_path):
    raw_path = tmp_path / "ERCOT Backcasted Load Profiles 2025.xlsx"
    interim_dir = tmp_path / "interim"
    sheet_names = (*extract_ercot_backcast.MONTH_SHEETS_2025[:8], "September", *extract_ercot_backcast.MONTH_SHEETS_2025[8:])
    with pd.ExcelWriter(raw_path) as writer:
        for sheet_name in sheet_names:
            pd.DataFrame(
                {
                    "PType_WZ": ["BUSHIDG_COAST"],
                    "Date": ["2025-01-01"],
                    "int_kWh1": [1],
                    "ADDTIME": ["2025-01-02"],
                }
            ).to_excel(writer, sheet_name=sheet_name, index=False)

    outputs = extract_ercot_backcast.extract_workbook(raw_path, interim_dir)

    assert outputs == [interim_dir / "2025" / f"m{month_number:02d}.parquet" for month_number in range(1, 13)]
    september = pd.read_parquet(outputs[8])
    assert september["source_sheet"].iloc[0] == "Sep"