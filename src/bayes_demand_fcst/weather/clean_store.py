"""Build the typed, irregular-observation ASOS clean store for Pass 0."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

import numpy as np
import pandas as pd

from bayes_demand_fcst.contracts import (
    ASOS_REQUIRED_COLUMNS,
    parse_asos_valid,
    validate_asos_columns,
)
from bayes_demand_fcst.weather.contracts import weather_irregular_clean_schema
from bayes_demand_fcst.weather.provenance import WeatherCleanProvenance

STATE_CODES = ("tx", "nm", "ok", "ar", "la")
MISSING_TOKENS = ("", "M", "NA", "N/A", "null", "NULL", "None")
ALTI_TO_HPA = 33.86389
ALTI_MIN_INHG = 28.50
ALTI_MAX_INHG = 31.20
MSLP_MIN_HPA = 965.0
MSLP_MAX_HPA = 1055.0
MSLP_ALTI_TOLERANCE_HPA = 12.0
P01I_MAX_IN_HOUR = 8.0
P01I_TRACE_THRESHOLD_IN = 0.005
VSBY_MAX_MILES = 10.0
SKYL1_MIN_FEET = 0.0
SKYL1_MAX_FEET = 35_000.0
SKYL1_HORIZON_FEET = 12_000.0
CLEAR_SKY_CODES = ("CLR", "SKC", "NCD", "NSC")
CLOUD_FRACTION_MAP = {
    "CLR": 0.00,
    "SKC": 0.00,
    "NCD": 0.00,
    "NSC": 0.00,
    "FEW": 0.1875,
    "SCT": 0.4375,
    "BKN": 0.75,
    "OVC": 1.00,
    "VV": 1.00,
}
MONTH_PATTERN = re.compile(r"_(\d{4})_(\d{2})\.csv$")
KEY_COLUMNS = ("station_id", "observed_at_utc")
PROVENANCE_VALUE_COLUMNS = {
    "dwpf_provenance": "dwpf",
    "feel_diff_provenance": "feel_diff",
    "cloud_frac_provenance": "cloud_frac",
    "p01i_provenance": "p01i",
    "mslp_provenance": "mslp",
    "alti_provenance": "alti",
    "alti_hpa_provenance": "alti_hpa",
    "delta_p_provenance": "delta_p",
    "vsby_provenance": "vsby",
}
NUMERIC_OUTPUT_COLUMNS = (
    "longitude_wgs84",
    "latitude_wgs84",
    "elevation_m",
    "tmpf",
    "dwpf",
    "relh",
    "feel",
    "feel_diff",
    "skyl1",
    "cloud_frac",
    "p01i",
    "mslp",
    "alti",
    "alti_hpa",
    "delta_p",
    "vsby",
)
STATIC_OUTPUT_COLUMNS = ("source_state", "source_file")
DUPLICATE_COMPLETENESS_COLUMNS = (
    "tmpf",
    "dwpf",
    "relh",
    "feel",
    "p01i",
    "mslp",
    "alti",
    "vsby",
)
QUALITY_KEYS = (
    "non_numeric_values",
    "relh_out_of_bounds",
    "dwpf_algebraic_recoveries",
    "p01i_trace_zeroed",
    "p01i_out_of_bounds",
    "vsby_capped",
    "vsby_out_of_bounds",
    "alti_out_of_bounds",
    "mslp_out_of_bounds",
    "mslp_alti_mismatch",
    "skyl1_out_of_bounds",
    "skyl1_clear_height_removed",
    "skyc1_horizon_reclassified",
    "skyc1_unrecognized",
    "duplicate_station_observations",
    "duplicate_rows_collapsed",
    "duplicate_numeric_measurements_averaged",
    "duplicate_categorical_conflicts_resolved",
)


def _numeric_series(series: pd.Series, column: str, quality: Counter[str]) -> pd.Series:
    text = series.astype("string").str.strip()
    source_missing = series.isna() | text.isin(MISSING_TOKENS)
    numeric = pd.to_numeric(series.where(~source_missing), errors="coerce").astype("float64")
    invalid = ~source_missing & numeric.isna()
    if invalid.any():
        count = int(invalid.sum())
        quality["non_numeric_values"] += count
        quality[f"{column}_non_numeric"] += count
    return numeric


def _raw_provenance(values: pd.Series) -> pd.Series:
    codes = np.where(
        values.notna().to_numpy(),
        int(WeatherCleanProvenance.RAW_OBSERVED),
        int(WeatherCleanProvenance.MISSING),
    )
    return pd.Series(codes, index=values.index, dtype="uint8")


def _recover_dewpoint_fahrenheit(tmpf: pd.Series, relh: pd.Series) -> pd.Series:
    mask = tmpf.notna() & relh.notna() & relh.gt(0.0) & relh.le(100.0)
    temperature_c = (tmpf - 32.0) * 5.0 / 9.0
    magnus_a = 17.625
    magnus_b = 243.04
    with np.errstate(divide="ignore", invalid="ignore"):
        gamma = np.log(relh / 100.0) + magnus_a * temperature_c / (magnus_b + temperature_c)
        dewpoint_c = magnus_b * gamma / (magnus_a - gamma)
    return (dewpoint_c * 9.0 / 5.0 + 32.0).where(mask).astype("float64")


def _consistent_non_null_value(values: pd.Series, column: str) -> Any:
    present = values.dropna()
    unique = pd.unique(present)
    if len(unique) > 1:
        raise ValueError(f"Conflicting duplicate ASOS field {column}: {list(unique)[:5]}")
    return unique[0] if len(unique) else pd.NA


def _select_duplicate_skyc1(values: pd.DataFrame) -> tuple[Any, float]:
    candidates = values.loc[values["skyc1"].notna()].copy()
    if candidates.empty:
        return pd.NA, np.nan
    candidates["_completeness"] = candidates[list(DUPLICATE_COMPLETENESS_COLUMNS)].notna().sum(axis=1)
    candidates["_cloud_rank"] = candidates["cloud_frac"].fillna(-1.0)
    preferred = candidates.sort_values(
        ["_completeness", "_cloud_rank", "skyc1"],
        ascending=[False, False, True],
        kind="stable",
    ).iloc[0]
    return preferred["skyc1"], float(preferred["cloud_frac"])


def _deduplicate_clean_frame(frame: pd.DataFrame, quality: Counter[str]) -> pd.DataFrame:
    duplicate_rows = frame.duplicated(list(KEY_COLUMNS), keep=False)
    if not duplicate_rows.any():
        return frame.reset_index(drop=True)

    duplicates = frame.loc[duplicate_rows].copy()
    retained = frame.loc[~duplicate_rows]
    grouped = duplicates.groupby(list(KEY_COLUMNS), sort=True, dropna=False)
    quality["duplicate_station_observations"] += int(len(duplicates))
    quality["duplicate_rows_collapsed"] += int(len(duplicates) - grouped.ngroups)

    for column in STATIC_OUTPUT_COLUMNS:
        if grouped[column].nunique(dropna=True).gt(1).any():
            raise ValueError(f"Conflicting duplicate ASOS field {column}")

    differing_numeric = 0
    for column in NUMERIC_OUTPUT_COLUMNS:
        differing_numeric += int(grouped[column].nunique(dropna=True).gt(1).sum())
    quality["duplicate_numeric_measurements_averaged"] += differing_numeric

    sky_records: list[dict[str, Any]] = []
    for key, values in grouped:
        selected_skyc1, selected_cloud_frac = _select_duplicate_skyc1(values)
        skyc1_conflict = int(values["skyc1"].nunique(dropna=True) > 1)
        quality["duplicate_categorical_conflicts_resolved"] += skyc1_conflict
        key_values = key if isinstance(key, tuple) else (key,)
        sky_records.append(
            {
                **dict(zip(KEY_COLUMNS, key_values, strict=True)),
                "skyc1": selected_skyc1,
                "cloud_frac_selected": selected_cloud_frac,
            }
        )

    static = grouped[list(STATIC_OUTPUT_COLUMNS)].agg(
        lambda values: _consistent_non_null_value(values, str(values.name))
    )
    numeric = grouped[list(NUMERIC_OUTPUT_COLUMNS)].mean()
    selected_sky = pd.DataFrame(sky_records).set_index(list(KEY_COLUMNS))
    aggregated = static.join(numeric)
    aggregated = aggregated.join(selected_sky["skyc1"])
    aggregated["cloud_frac"] = selected_sky["cloud_frac_selected"]
    aggregated = aggregated.reset_index()
    for provenance_column, value_column in PROVENANCE_VALUE_COLUMNS.items():
        aggregated[provenance_column] = np.where(
            aggregated[value_column].notna().to_numpy(),
            int(WeatherCleanProvenance.DUPLICATE_AGGREGATE),
            int(WeatherCleanProvenance.MISSING),
        ).astype("uint8")

    output = pd.concat([retained, aggregated], ignore_index=True)
    output = output.sort_values(list(KEY_COLUMNS), kind="stable").reset_index(drop=True)
    return output


def clean_asos_frame(
    frame: pd.DataFrame,
    state_code: str,
    source_file: str,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Apply row-level Pass-0 QC and exact recoveries to one ASOS file."""
    validate_asos_columns(frame.columns)
    normalized_state = state_code.strip().lower()
    if normalized_state not in STATE_CODES:
        raise ValueError(f"Unknown ASOS state code {state_code!r}")
    if not source_file.strip():
        raise ValueError("source_file cannot be empty")

    quality: Counter[str] = Counter()
    station_id = frame["station"].astype("string").str.strip()
    station_missing = station_id.isna() | station_id.eq("")
    if station_missing.any():
        raise ValueError(f"ASOS station field contains {int(station_missing.sum())} missing IDs")

    clean = pd.DataFrame(index=frame.index)
    clean["station_id"] = station_id
    clean["observed_at_utc"] = parse_asos_valid(frame["valid"])
    clean["source_state"] = normalized_state.upper()
    clean["source_file"] = source_file
    clean["longitude_wgs84"] = _numeric_series(frame["lon"], "lon", quality)
    clean["latitude_wgs84"] = _numeric_series(frame["lat"], "lat", quality)
    clean["elevation_m"] = _numeric_series(frame["elevation"], "elevation", quality)
    missing_coordinates = clean[
        ["longitude_wgs84", "latitude_wgs84", "elevation_m"]
    ].isna().any(axis=1)
    if missing_coordinates.any():
        raise ValueError(
            f"ASOS metadata contains {int(missing_coordinates.sum())} rows with missing coordinates or elevation"
        )

    clean["tmpf"] = _numeric_series(frame["tmpf"], "tmpf", quality)
    clean["dwpf"] = _numeric_series(frame["dwpf"], "dwpf", quality)
    clean["relh"] = _numeric_series(frame["relh"], "relh", quality)
    clean["feel"] = _numeric_series(frame["feel"], "feel", quality)
    clean["p01i"] = _numeric_series(frame["p01i"], "p01i", quality)
    clean["mslp"] = _numeric_series(frame["mslp"], "mslp", quality)
    clean["alti"] = _numeric_series(frame["alti"], "alti", quality)
    clean["vsby"] = _numeric_series(frame["vsby"], "vsby", quality)
    clean["skyl1"] = _numeric_series(frame["skyl1"], "skyl1", quality)

    skyc1 = frame["skyc1"].astype("string").str.strip()
    skyc1 = skyc1.mask(skyc1.isin(MISSING_TOKENS))
    clean["skyc1"] = skyc1

    relh_invalid = clean["relh"].notna() & (
        clean["relh"].lt(0.0) | clean["relh"].gt(100.0)
    )
    clean.loc[relh_invalid, "relh"] = np.nan
    quality["relh_out_of_bounds"] += int(relh_invalid.sum())

    dwpf_provenance = _raw_provenance(clean["dwpf"])
    recovery_mask = clean["dwpf"].isna() & clean["tmpf"].notna() & clean["relh"].notna()
    recovered_dwpf = _recover_dewpoint_fahrenheit(clean["tmpf"], clean["relh"])
    applied_recovery = recovery_mask & recovered_dwpf.notna()
    clean.loc[applied_recovery, "dwpf"] = recovered_dwpf.loc[applied_recovery]
    dwpf_provenance.loc[applied_recovery] = int(WeatherCleanProvenance.ALGEBRAIC_RECOVERY)
    clean["dwpf_provenance"] = dwpf_provenance.astype("uint8")
    quality["dwpf_algebraic_recoveries"] += int(applied_recovery.sum())

    clean["feel_diff"] = (clean["feel"] - clean["tmpf"]).astype("float64")
    clean["feel_diff_provenance"] = np.where(
        clean["feel_diff"].notna().to_numpy(),
        int(WeatherCleanProvenance.ALGEBRAIC_RECOVERY),
        int(WeatherCleanProvenance.MISSING),
    ).astype("uint8")

    p01i_provenance = _raw_provenance(clean["p01i"])
    p01i_invalid = clean["p01i"].notna() & (
        clean["p01i"].lt(0.0) | clean["p01i"].gt(P01I_MAX_IN_HOUR)
    )
    clean.loc[p01i_invalid, "p01i"] = np.nan
    p01i_provenance.loc[p01i_invalid] = int(WeatherCleanProvenance.MISSING)
    quality["p01i_out_of_bounds"] += int(p01i_invalid.sum())
    trace = clean["p01i"].notna() & clean["p01i"].gt(0.0) & clean["p01i"].lt(P01I_TRACE_THRESHOLD_IN)
    clean.loc[trace, "p01i"] = 0.0
    p01i_provenance.loc[trace] = int(WeatherCleanProvenance.QC_DERIVED)
    clean["p01i_provenance"] = p01i_provenance.astype("uint8")
    quality["p01i_trace_zeroed"] += int(trace.sum())

    vsby_provenance = _raw_provenance(clean["vsby"])
    vsby_invalid = clean["vsby"].notna() & clean["vsby"].lt(0.0)
    clean.loc[vsby_invalid, "vsby"] = np.nan
    vsby_provenance.loc[vsby_invalid] = int(WeatherCleanProvenance.MISSING)
    quality["vsby_out_of_bounds"] += int(vsby_invalid.sum())
    vsby_capped = clean["vsby"].notna() & clean["vsby"].gt(VSBY_MAX_MILES)
    clean.loc[vsby_capped, "vsby"] = VSBY_MAX_MILES
    vsby_provenance.loc[vsby_capped] = int(WeatherCleanProvenance.QC_DERIVED)
    clean["vsby_provenance"] = vsby_provenance.astype("uint8")
    quality["vsby_capped"] += int(vsby_capped.sum())

    alti_provenance = _raw_provenance(clean["alti"])
    alti_invalid = clean["alti"].notna() & (
        clean["alti"].lt(ALTI_MIN_INHG) | clean["alti"].gt(ALTI_MAX_INHG)
    )
    clean.loc[alti_invalid, "alti"] = np.nan
    alti_provenance.loc[alti_invalid] = int(WeatherCleanProvenance.MISSING)
    clean["alti_provenance"] = alti_provenance.astype("uint8")
    quality["alti_out_of_bounds"] += int(alti_invalid.sum())

    mslp_provenance = _raw_provenance(clean["mslp"])
    mslp_invalid = clean["mslp"].notna() & (
        clean["mslp"].lt(MSLP_MIN_HPA) | clean["mslp"].gt(MSLP_MAX_HPA)
    )
    clean.loc[mslp_invalid, "mslp"] = np.nan
    mslp_provenance.loc[mslp_invalid] = int(WeatherCleanProvenance.MISSING)
    quality["mslp_out_of_bounds"] += int(mslp_invalid.sum())
    mslp_mismatch = (
        clean["mslp"].notna()
        & clean["alti"].notna()
        & (clean["mslp"] - ALTI_TO_HPA * clean["alti"]).abs().gt(MSLP_ALTI_TOLERANCE_HPA)
    )
    clean.loc[mslp_mismatch, "mslp"] = np.nan
    mslp_provenance.loc[mslp_mismatch] = int(WeatherCleanProvenance.MISSING)
    clean["mslp_provenance"] = mslp_provenance.astype("uint8")
    quality["mslp_alti_mismatch"] += int(mslp_mismatch.sum())

    clean["alti_hpa"] = (ALTI_TO_HPA * clean["alti"]).astype("float64")
    clean["alti_hpa_provenance"] = np.where(
        clean["alti_hpa"].notna().to_numpy(),
        int(WeatherCleanProvenance.ALGEBRAIC_RECOVERY),
        int(WeatherCleanProvenance.MISSING),
    ).astype("uint8")
    clean["delta_p"] = (clean["mslp"] - clean["alti_hpa"]).astype("float64")
    clean["delta_p_provenance"] = np.where(
        clean["delta_p"].notna().to_numpy(),
        int(WeatherCleanProvenance.ALGEBRAIC_RECOVERY),
        int(WeatherCleanProvenance.MISSING),
    ).astype("uint8")

    skyl1_invalid = clean["skyl1"].notna() & (
        clean["skyl1"].lt(SKYL1_MIN_FEET) | clean["skyl1"].gt(SKYL1_MAX_FEET)
    )
    clean.loc[skyl1_invalid, "skyl1"] = np.nan
    quality["skyl1_out_of_bounds"] += int(skyl1_invalid.sum())
    clear_height = clean["skyc1"].isin(CLEAR_SKY_CODES) & clean["skyl1"].notna()
    clean.loc[clear_height, "skyl1"] = np.nan
    quality["skyl1_clear_height_removed"] += int(clear_height.sum())
    horizon_reclassified = clean["skyl1"].notna() & clean["skyl1"].gt(SKYL1_HORIZON_FEET)
    clean.loc[horizon_reclassified, "skyc1"] = "CLR"
    quality["skyc1_horizon_reclassified"] += int(horizon_reclassified.sum())

    clean["cloud_frac"] = clean["skyc1"].map(CLOUD_FRACTION_MAP).astype("float64")
    recognized_sky = clean["skyc1"].isna() | clean["skyc1"].isin(CLOUD_FRACTION_MAP)
    quality["skyc1_unrecognized"] += int((~recognized_sky).sum())
    clean["cloud_frac_provenance"] = np.where(
        clean["cloud_frac"].notna().to_numpy(),
        int(WeatherCleanProvenance.QC_DERIVED),
        int(WeatherCleanProvenance.MISSING),
    ).astype("uint8")

    clean = clean.loc[:, list(weather_irregular_clean_schema.required_columns)]
    clean = _deduplicate_clean_frame(clean, quality)
    clean = clean.loc[:, list(weather_irregular_clean_schema.required_columns)]
    weather_irregular_clean_schema.validate_columns(clean.columns)
    if clean.duplicated(list(KEY_COLUMNS)).any():
        raise AssertionError("Clean ASOS frame still contains duplicate primary keys")
    return clean, {key: int(quality[key]) for key in sorted(set(quality) | set(QUALITY_KEYS))}


def _normalize_state_codes(state_codes: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(state.strip().lower() for state in state_codes))
    if not normalized:
        raise ValueError("At least one state code is required")
    unknown = sorted(set(normalized) - set(STATE_CODES))
    if unknown:
        raise ValueError(f"Unknown state codes: {', '.join(unknown)}")
    return normalized


def _normalize_year_months(year_months: tuple[str, ...] | None) -> tuple[str, ...] | None:
    if year_months is None:
        return None
    normalized = tuple(dict.fromkeys(month.strip() for month in year_months))
    for month in normalized:
        if re.fullmatch(r"\d{4}-\d{2}", month) is None:
            raise ValueError(f"Invalid year-month {month!r}; expected YYYY-MM")
        _, month_number = month.split("-")
        if not 1 <= int(month_number) <= 12:
            raise ValueError(f"Invalid year-month {month!r}; month is outside 01-12")
    return normalized


def find_weather_source_files(
    raw_dir: Path,
    state_codes: tuple[str, ...] = STATE_CODES,
    year_months: tuple[str, ...] | None = None,
) -> list[tuple[str, str, Path]]:
    """Discover monthly source CSVs grouped by UTC year-month and state."""
    states = _normalize_state_codes(state_codes)
    months = _normalize_year_months(year_months)
    discovered: list[tuple[str, str, Path]] = []
    for state_code in states:
        source_dir = raw_dir / f"weather_{state_code}_asos"
        paths = sorted(source_dir.glob(f"{state_code}_asos_land_*.csv"))
        for path in paths:
            match = MONTH_PATTERN.search(path.name)
            if match is None:
                raise ValueError(f"ASOS source filename does not contain a year/month: {path}")
            year_month = f"{match.group(1)}-{match.group(2)}"
            if months is None or year_month in months:
                discovered.append((year_month, state_code, path))
        if not any(item[1] == state_code for item in discovered):
            suffix = f" for {', '.join(months)}" if months is not None else ""
            raise FileNotFoundError(f"No ASOS CSV files found for {state_code.upper()}{suffix}")
    return sorted(discovered, key=lambda item: (item[0], item[1], item[2].name))


def _observation_keys(frame: pd.DataFrame) -> set[tuple[str, str]]:
    return set(
        zip(
            frame["station_id"].astype(str),
            frame["observed_at_utc"].map(lambda value: pd.Timestamp(value).isoformat()),
            strict=True,
        )
    )


def build_weather_irregular_clean(
    raw_dir: Path,
    output_dir: Path,
    state_codes: tuple[str, ...] = STATE_CODES,
    year_months: tuple[str, ...] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Read each selected raw CSV once and atomically publish the Pass-0 store."""
    sources = find_weather_source_files(raw_dir, state_codes, year_months)
    if output_dir.exists() and any(output_dir.iterdir()):
        if not overwrite:
            raise FileExistsError(f"Output directory is not empty: {output_dir}; use --overwrite to replace it")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    state_reports = {state: {"files_processed": 0, "row_count_read": 0, "row_count_written": 0} for state in sorted({item[1] for item in sources})}
    quality: Counter[str] = Counter()
    station_ids: set[str] = set()
    total_read = 0
    total_written = 0
    minimum_utc: pd.Timestamp | None = None
    maximum_utc: pd.Timestamp | None = None
    month_reports: dict[str, dict[str, int]] = {}

    try:
        grouped_sources: dict[str, list[tuple[str, Path]]] = {}
        for year_month, state_code, path in sources:
            grouped_sources.setdefault(year_month, []).append((state_code, path))

        for year_month in sorted(grouped_sources):
            seen_keys: set[tuple[str, str]] = set()
            month_report = {"files_processed": 0, "row_count_read": 0, "row_count_written": 0}
            for state_code, path in grouped_sources[year_month]:
                raw = pd.read_csv(
                    path,
                    usecols=ASOS_REQUIRED_COLUMNS,
                    na_values=MISSING_TOKENS,
                    keep_default_na=True,
                    dtype={"station": "string", "skyc1": "string", "skyl1": "string"},
                )
                clean, frame_quality = clean_asos_frame(raw, state_code, path.name)
                actual_months = set(clean["observed_at_utc"].dt.strftime("%Y-%m"))
                if actual_months != {year_month}:
                    raise ValueError(
                        f"Source file {path} produced unexpected UTC months: {sorted(actual_months)}"
                    )
                keys = _observation_keys(clean)
                overlap = keys & seen_keys
                if overlap:
                    examples = sorted(overlap)[:5]
                    raise ValueError(f"Duplicate station observations across source files: {examples}")
                seen_keys.update(keys)

                destination = (
                    staging
                    / f"state={state_code}"
                    / f"year={year_month[:4]}"
                    / f"month={year_month[5:]}"
                    / f"part-{path.stem}.parquet"
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                clean.to_parquet(destination, index=False, engine="pyarrow", compression="zstd")

                row_count_read = int(len(raw))
                row_count_written = int(len(clean))
                total_read += row_count_read
                total_written += row_count_written
                state_reports[state_code]["files_processed"] += 1
                state_reports[state_code]["row_count_read"] += row_count_read
                state_reports[state_code]["row_count_written"] += row_count_written
                month_report["files_processed"] += 1
                month_report["row_count_read"] += row_count_read
                month_report["row_count_written"] += row_count_written
                quality.update(frame_quality)
                station_ids.update(clean["station_id"].astype(str))
                frame_minimum = clean["observed_at_utc"].min()
                frame_maximum = clean["observed_at_utc"].max()
                minimum_utc = frame_minimum if minimum_utc is None else min(minimum_utc, frame_minimum)
                maximum_utc = frame_maximum if maximum_utc is None else max(maximum_utc, frame_maximum)

            month_reports[year_month] = month_report

        quality_report = {key: int(quality[key]) for key in sorted(set(quality) | set(QUALITY_KEYS))}
        report: dict[str, Any] = {
            "schema": {
                "name": weather_irregular_clean_schema.name,
                "version": weather_irregular_clean_schema.version,
                "primary_key": list(weather_irregular_clean_schema.primary_key),
            },
            "accepted": True,
            "files_processed": len(sources),
            "row_count_read": total_read,
            "row_count_written": total_written,
            "station_count": len(station_ids),
            "observed_at_min_utc": minimum_utc.isoformat() if minimum_utc is not None else None,
            "observed_at_max_utc": maximum_utc.isoformat() if maximum_utc is not None else None,
            "states": state_reports,
            "months": month_reports,
            "quality": quality_report,
        }
        (staging / "quality_report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        if output_dir.exists():
            shutil.rmtree(output_dir)
        staging.rename(output_dir)
        return report
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
