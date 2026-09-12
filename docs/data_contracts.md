# Phase 1: Source Data Contracts

This document records the current source contracts before normalization. The
contracts describe what the files contain; they do not yet assign modeling
meaning to every field.

## ASOS Raw CSV

All five download scripts request the same columns and the refreshed raw files
share this header:

```text
station,valid,lon,lat,elevation,tmpf,dwpf,relh,feel,skyc1,skyl1,p01i,mslp,alti,vsby
```

Contract decisions:

- `valid` is parsed as timezone-aware UTC because the download requests use
  `tz=UTC`.
- `station` is the station identifier and is retained as a string.
- `lon` and `lat` are WGS84 coordinates; `elevation` is station elevation in
  meters as supplied by the ASOS request.
- `tmpf`, `dwpf`, and `feel` are Fahrenheit temperatures.
- `relh` is relative humidity in percent.
- `p01i` and `vsby` retain the ASOS/Iowa Mesonet source units until the
  normalization stage documents any conversion.
- `mslp` and `alti` are separate pressure measurements and must not be silently
  substituted for one another.
- `skyc1` is categorical; `skyl1` is a ceiling/sky height field and remains
  separate from the numeric weather variables until its missing-value behavior
  is profiled.
- Empty source values remain missing. They are not imputed in Phase 1.

The complete schema and numeric conversion are checked by
`scripts/source_contracts.py` without loading the full archive.

## Extracted ERCOT Backcast Parquet

The current extracted files are wide daily profile records. They contain:

```text
PType_WZ
Date
int_kWh1 ... int_kWh100
ADDTIME
source_workbook
source_sheet
```

Contract decisions:

- `PType_WZ` is retained as the ERCOT profile/zone identifier.
- `Date` and `ADDTIME` are parsed as datetimes but are not assumed to be UTC.
- `int_kWh1` through `int_kWh100` are numeric profile columns. Their exact
  interval duration, local-time convention, and relationship to the desired
  ERCOT load target must be verified before the Phase 3 load normalization.
- `source_workbook` and `source_sheet` are provenance fields and must be
  preserved.
- The monthly versus quarterly directory layout is an input partitioning
  detail, not a schema distinction. Downstream discovery should recurse over
  all Parquet files.

## Explicitly Deferred Decisions

These decisions are intentionally not guessed in Phase 1:

- the exact CPT/DST interpretation of the ERCOT profile timestamps;
- which profile columns and unit conversion define the final ERCOT MW target;
- the duplicate station-hour resolution rule for ASOS records;
- pressure conversion or any `mslp`/`alti` fallback policy;
- interpolation or imputation rules;
- the final canonical hourly load/weather join.

Each deferred decision needs a fixture and a focused test before the relevant
normalization stage is implemented.

## Phase 3 Normalized Load Profile

Phase 3 currently reshapes the wide extracted profiles into a long-form Parquet
panel under `data/interim/load_profile_long/`. Each row retains the source
profile identifier, the original `Date` and `ADDTIME` values as naive source
timestamps, an `interval_number`, the original `interval_value_kwh_source`, and
workbook/sheet/file provenance.

This is intentionally not yet the canonical UTC load panel. The normalizer does
not infer a 15-minute or hourly interval, attach a timezone, convert kWh to MW,
or collapse profile rows into an ERCOT aggregate. Those operations require the
source semantics to be verified first.

## Phase 4 Voronoi Weights

Phase 4 projects the WGS84 station coordinates and ERCT boundary to EPSG:3083
in memory, constructs finite Voronoi cells, clips them to ERCT, and writes only
the compact area-weight table under `data/interim/voronoi_weights/`.

Coincident station coordinates are represented by one deterministic canonical
station row with a comma-separated `station_aliases` field. This prevents
duplicate points from invalidating the Voronoi diagram while preserving the
station identity mapping for later weather aggregation. Station groups whose
clipped cell does not intersect ERCT remain in the artifact with zero area and
zero weight for auditability; they cannot contribute to the regional aggregate.
