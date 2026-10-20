from datetime import date
import random

import openpyxl
import pandas as pd
import pytest

from scripts.ercot_fuel_mix_qc import _iter_candidates, audit_workbook_to_parquet, write_parquet_with_qc


def _make_2017_workbook(path):
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for month, month_name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        start=1,
    ):
        sheet = workbook.create_sheet(month_name)
        headers = ["Date", "Fuel", "Settlement Type", "Total"]
        headers.extend(f"{minutes // 60}:{minutes % 60:02d}" for minutes in range(15, 1441, 15))
        if month == 11:
            headers.extend(("01:15 (DST)", "01:30 (DST)", "01:45 (DST)", "02:00 (DST)"))
        sheet.append(headers)
        values = [float(index + 1) for index in range(96)]
        if month == 11:
            values.extend((201.0, 202.0, 203.0, 204.0))
        sheet.append([date(2017, month, 1), "Biomass", "FINAL", 123.0, *values])
    workbook.save(path)


def _write_candidate_parquet(workbook_path, parquet_path, corrupt_key=None):
    candidates = list(_iter_candidates(2017, workbook_path))
    values = []
    for candidate in candidates:
        value = candidate.expected
        if corrupt_key == (candidate.timestamp_local, candidate.fuel_type):
            value += 1.0
        values.append({
            "timestamp_local": candidate.timestamp_local,
            "fuel_type": candidate.fuel_type,
            "generation_mwh": value,
        })
    pd.DataFrame.from_records(values).to_parquet(parquet_path, index=False)
    return candidates


def test_audit_accepts_exact_monthly_samples(tmp_path):
    workbook_path = tmp_path / "source.xlsx"
    parquet_path = tmp_path / "output.parquet"
    _make_2017_workbook(workbook_path)
    _write_candidate_parquet(workbook_path, parquet_path)

    assert audit_workbook_to_parquet(2017, workbook_path, parquet_path, seed=42) == 120


def test_audit_fails_when_a_sampled_value_differs(tmp_path):
    workbook_path = tmp_path / "source.xlsx"
    parquet_path = tmp_path / "output.parquet"
    _make_2017_workbook(workbook_path)
    candidates = list(_iter_candidates(2017, workbook_path))
    by_month = {month: [candidate for candidate in candidates if candidate.month == month] for month in range(1, 13)}
    sampled_first_month = random.Random(42).sample(by_month[1], 10)
    corrupted_key = (sampled_first_month[0].timestamp_local, sampled_first_month[0].fuel_type)
    _write_candidate_parquet(workbook_path, parquet_path, corrupt_key=corrupted_key)

    with pytest.raises(AssertionError, match=r"2017 workbook-to-Parquet QC failed: 1/120"):
        audit_workbook_to_parquet(2017, workbook_path, parquet_path, seed=42)


def test_failed_qc_does_not_replace_existing_parquet(tmp_path):
    workbook_path = tmp_path / "source.xlsx"
    parquet_path = tmp_path / "output.parquet"
    _make_2017_workbook(workbook_path)
    candidates = list(_iter_candidates(2017, workbook_path))
    original = pd.DataFrame.from_records([
        {
            "timestamp_local": candidate.timestamp_local,
            "fuel_type": candidate.fuel_type,
            "generation_mwh": candidate.expected,
        }
        for candidate in candidates
    ])
    original.to_parquet(parquet_path, index=False)
    original_saved = pd.read_parquet(parquet_path)

    monthly_candidates = [candidate for candidate in candidates if candidate.month == 1]
    sampled = random.Random(2017).sample(monthly_candidates, 10)
    bad_key = (sampled[0].timestamp_local, sampled[0].fuel_type)
    failed_frame = original.copy()
    corrupted_row = (
        failed_frame.timestamp_local.eq(bad_key[0])
        & failed_frame.fuel_type.eq(bad_key[1])
    )
    failed_frame.loc[corrupted_row, "generation_mwh"] += 1.0

    with pytest.raises(AssertionError, match=r"2017 workbook-to-Parquet QC failed"):
        write_parquet_with_qc(failed_frame, 2017, workbook_path, parquet_path)

    pd.testing.assert_frame_equal(pd.read_parquet(parquet_path), original_saved)
