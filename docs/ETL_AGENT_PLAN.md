# ETL and Agent Adoption Plan

## Purpose

Build the data pipeline in deterministic, testable stages first, then add a
bounded ETL agent that observes the pipeline, explains failures, and proposes
approved actions. The agent should not become the source of scientific or data
transformation decisions before those decisions are encoded in tested code.

## Current Position

The repository currently has:

- DVC stages for the five ASOS weather downloads.
- Raw ASOS monthly CSV archives in `data/raw/weather_*_asos/`.
- Extracted ERCOT load Parquet files in `data/interim/ercot_backcast/`.
- An ERCT boundary in `data/interim/ercot_map/`.
- Station metadata in `data/interim/station_metadata/`.
- No canonical station-hour weather panel yet.
- No Voronoi weights, regional weather panel, or final observed load-weather panel yet.

The first implementation goal is therefore a trustworthy data contract, not an
autonomous agent.

## Target Data Flow

```text
raw ASOS CSVs ---------------------> station-hour weather Parquet
                                          |
station metadata + ERCT boundary --> Voronoi weights
                                          |
                                          v
                                   regional weather Parquet
                                          |
ERCOT load Parquet ----------------------+
                                          v
                              processed observed hourly panel
                                          |
                                          v
                              experiment-specific feature matrix
```

## Data Layer Rules

### `data/raw/`

Downloaded source files only. Preserve them unchanged. Do not normalize,
rename, overwrite, or delete raw data as part of an ETL transformation.

### `data/interim/`

Auditable, reusable transformations that are not yet the final modeling panel:

```text
data/interim/
├── ercot_backcast/
├── ercot_map/
├── station_metadata/
├── weather_station_hourly/
├── voronoi_weights/
└── weather_regional_hourly/
```

Quarterly and monthly source partitions are acceptable. Downstream scripts
should discover Parquet files recursively and normalize their schemas rather
than requiring identical source partition names.

### `data/processed/`

The canonical observed load-weather panel. It may contain direct temporal,
spatial, and unit transformations, but not experiment-specific feature choices.

### `data/features/`

Model-specific generated features such as lags, rolling values, calendar
encodings, nonlinear temperature terms, and forecast targets. These should be
versioned separately from the canonical observed panel.

## Execution Sequence

### Phase 1: Freeze the source contracts

**Goal:** establish what each source means before adding automation.

1. Document the ERCOT load schema, timestamp fields, timezone, units, and load
   measure used as the target.
2. Document the ASOS schema, including `tmpf`, `dwpf`, `relh`, `feel`, `p01i`,
   `mslp`, `alti`, visibility, sky fields, station coordinates, and elevation.
3. Define the canonical timestamp as timezone-aware UTC.
4. Define the duplicate rule for station-hour observations.
5. Define missingness policy: preserve missing observations and report coverage;
   do not silently interpolate.
6. Add tests for the contracts before processing the full archives.

**Gate:** a small fixture can be read and validated with no ambiguous units,
timestamps, or duplicate handling.

### Phase 2: Build the station-hour weather panel

**Goal:** convert the large monthly ASOS CSV corpus into a canonical,
streamable Parquet dataset.

Create a script such as:

```text
scripts/normalize_asos_weather.py
```

The script should:

1. Discover all five state archive directories.
2. Read CSV files in chunks; never load the full corpus into memory.
3. Parse `valid` as UTC.
4. Standardize station IDs, column names, numeric types, and missing values.
5. Preserve `alti` and `mslp` as separate measurements.
6. Preserve station coordinates and elevation for validation/provenance.
7. Apply the explicit station-hour duplicate rule.
8. Write partitioned Parquet, for example:

```text
data/interim/weather_station_hourly/
├── year=2014/month=01/part-000.parquet
├── year=2014/month=02/part-000.parquet
└── ...
```

9. Produce a compact quality report containing row counts, station counts,
   time range, missingness by field, duplicate counts, and parse failures.

Add focused fixture tests before processing all data. Then add a DVC stage whose
dependency is the raw weather directories and whose output is
`data/interim/weather_station_hourly/`.

**Gate:** the full corpus can be processed reproducibly; the output schema,
row counts, station count, UTC range, and quality report are stable and
explainable.

### Phase 3: Normalize and validate the load panel

**Goal:** make monthly and quarterly ERCOT Parquets consumable through one
canonical interface.

Create a load normalization step that:

1. Discovers all Parquet files under `data/interim/ercot_backcast/`.
2. Standardizes column names and numeric types.
3. Preserves source workbook and sheet provenance.
4. Retains the original ERCOT timestamp.
5. Resolves the CPT/DST interpretation explicitly.
6. Adds a canonical `timestamp_utc` only after the DST rule is validated.
7. Checks duplicates, gaps, impossible values, and overlapping source files.

Write the normalized load output to a separate interim location, such as:

```text
data/interim/load_hourly/
```

The first implementation may safely produce a long-form profile panel under
`data/interim/load_profile_long/` while preserving naive source timestamps and
source interval values. It must not label those rows UTC or MW until the
interval and unit semantics are verified.

The current normalization policy uses `int_kWh1` through `int_kWh96` only.
The rare values sometimes present in `int_kWh97` through `int_kWh100` remain
available in the immutable raw workbooks and are validated as source columns,
but are excluded from the normalized profile until their meaning is established.
Treat these fields as a documented source anomaly: preserve their occurrence
counts and source locations for audit, avoid inferring an ERCOT rule from naming
alone, and revisit the exclusion only when external documentation or a stable
date/interval pattern supports a tested interpretation.

**Gate:** spring-forward and fall-back examples pass explicit timestamp tests,
and the normalized load table has one documented row per intended interval.

### Phase 4: Compute structural Voronoi weights

**Goal:** represent spatial coverage without recomputing geometry for every
weather row.

Create a deterministic spatial script that consumes:

```text
data/interim/station_metadata/station_metadata.parquet
data/interim/ercot_map/ercot_boundary.geojson
```

It should:

1. Read WGS84 artifacts from disk.
2. Transform station points and the ERCT boundary to EPSG:3083 in memory.
3. Construct the Voronoi diagram in memory.
4. Clip border and coastal cells to the ERCT polygon.
5. Calculate clipped cell areas and normalized area weights.
6. Persist compact weights, not hourly geometries:

```text
data/interim/voronoi_weights/voronoi_weights.parquet
```

A weight table should include at least:

```text
configuration_id
station_id
cell_area_m2
area_weight
```

If multiple station IDs share exact coordinates, represent them as one
canonical Voronoi point and preserve the aliases in the weight artifact. Keep
zero-area station groups for auditability, but exclude their zero weights from
later regional aggregation.

Optionally persist one geometry artifact per structural configuration for audit
and visualization. Do not re-tessellate because an individual weather value is
missing. Recompute only when the active station configuration changes.

**Gate:** every valid cell is inside ERCT, weights sum to one per configuration,
and a plotted/audited sample confirms the intended border clipping.

### Phase 5: Aggregate regional weather

**Goal:** combine station-hour weather with structural Voronoi weights.

For each weather variable and hour, calculate a weighted mean over observed
station values and renormalize the available weights:

$$
x_t =
\frac{\sum_i w_i x_{i,t}\mathbf{1}(x_{i,t}\text{ observed})}
     {\sum_i w_i\mathbf{1}(x_{i,t}\text{ observed})}
$$

Also write diagnostics such as:

```text
weather_coverage
active_station_count
weighted_station_count
```

The first implementation writes variable-specific coverage columns in addition
to the aggregate `weather_coverage`; this makes pressure, temperature, and
precipitation coverage distinguishable during EDA.

Keep `mslp` and `alti` separate in the station and regional panels. Any later
pressure estimate or substitution must be an explicit, documented feature
choice rather than an ingestion-side overwrite.

Write the result to:

```text
data/interim/weather_regional_hourly/
```

**Gate:** aggregation preserves UTC, handles missing values deterministically,
never divides by zero silently, and produces coverage diagnostics.

### Phase 6: Assemble the canonical observed panel

**Goal:** join normalized load and regional weather without model engineering.

Join:

```text
data/interim/load_hourly/
data/interim/weather_regional_hourly/
```

Write:

```text
data/processed/ercot_hourly_panel/
```

The assembly stage must consume a canonical `data/interim/load_hourly/` input
with `timestamp_utc` and numeric `load_mw`; it must reject the provisional
`load_profile_long/` artifact until Phase 3 resolves its interval, timezone,
and unit semantics.

Include observed or directly transformed fields such as:

```text
timestamp_utc
load_mw
temperature_regional
dewpoint_regional
relative_humidity_regional
pressure_regional
altimeter_regional
wind_regional
precipitation_regional
weather_coverage
active_station_count
```

Do not include lags, rolling features, holiday encodings, nonlinear transforms,
or forecast targets in this artifact.

**Gate:** the join has documented row counts, no accidental many-to-many
expansion, a known time range, and explicit missingness behavior.

### Phase 7: Add experiment-specific feature generation

**Goal:** keep modeling choices separate from the canonical data product.

Create feature-generation scripts that consume the processed panel and write
versioned outputs under `data/features/`, for example:

```text
data/features/baseline_hour_ahead/
```

Each experiment must declare its forecast horizon, lag definitions, training
cutoff, validation windows, and leakage checks. Chronological validation is
required.

**Gate:** feature tests prove that every feature at time `t` uses only information
available at or before the forecast origin.

## DVC Operating Rules

Each deterministic transformation should be a DVC stage with explicit
dependencies and outputs. Use `dvc dag` to inspect the graph and `dvc status` to
inspect staleness.

Use these operations deliberately:

- `dvc repro`: executes stages and may remove/recreate declared outputs. Use it
  only when rerunning the stage is intended.
- `dvc commit <stage>`: records already-created outputs without executing the
  stage. Use this after a manually completed download or transformation.
- `dvc checkout`: restores the version recorded in `dvc.lock`; it does not
  preserve newer uncommitted local outputs.

For large manually downloaded outputs:

1. Complete the download.
2. Confirm the files are in the exact DVC output directory.
3. Run `dvc commit <download_stage>` from the repository root.
4. Only then run downstream stages.

Never use `dvc repro` on a downstream stage while an upstream manually refreshed
output is uncommitted. DVC may clear the output before executing the upstream
stage.

## Phase 8: Introduce a bounded ETL agent

Only after Phases 1 through 6 are passing should the agent be introduced.

### Initial agent responsibilities

Use LangGraph with a locally hosted Qwen model as a planning and interpretation
layer. The agent may:

- inspect schemas and quality reports;
- compare current results with expected contracts;
- explain validation failures;
- identify affected downstream stages;
- propose a rerun or repair plan;
- request human approval for consequential actions.

Expose narrow tools such as:

```text
inspect_asos_schema()
profile_weather_archive()
validate_weather_panel()
validate_load_timestamps()
validate_voronoi_weights()
show_dvc_status()
plan_affected_stages()
```

The tools should invoke deterministic Python code and return structured results.
The model should not receive unrestricted shell access or directly rewrite raw
data, DVC history, timestamp rules, units, missingness policy, or spatial
methodology.

### Approval boundaries

Require approval before the agent can:

- delete or overwrite raw data;
- change units or timestamp interpretation;
- alter missing-data or imputation policy;
- change Voronoi geometry or weighting methodology;
- modify `dvc.yaml` or `dvc.lock` policy;
- execute an expensive full-corpus rebuild.

Permit automatic actions only for low-risk operations such as reading reports,
classifying a known validation failure, or retrying a transient operation under
a fixed policy.

### Agent pilot success criteria

The pilot is successful when the agent can produce a report like:

```text
weather_normalization: warning
rows_read: ...
stations: ...
alti_present: ...
mslp_present: ...
timestamp_parse_failures: 0
duplicate_station_hours: ...
recommended_action: inspect duplicate rule
approval_required: yes
```

The agent must not silently change data or declare a scientifically meaningful
pipeline result valid.

## Definition of Done

The project is ready for a broader agent role when:

- raw, interim, processed, and feature boundaries are documented;
- each deterministic transformation has tests and a DVC stage;
- weather and load timestamps are canonical UTC;
- ASOS units and missingness rules are explicit;
- Voronoi weights are validated independently of weather values;
- the observed panel is reproducible from declared inputs;
- feature generation has chronological leakage tests;
- the agent can inspect and explain the pipeline without bypassing controls.
