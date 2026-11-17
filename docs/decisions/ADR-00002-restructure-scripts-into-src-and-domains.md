# ADR-00002: Restructure `scripts/` into `src/` Package and Domain Subdirectories

- **ADR ID**: ADR-00002
- **Filename**: `ADR-00002-restructure-scripts-into-src-and-domains.md`
- **Date**: 2026-11-17 05:28 UTC
- **Status**: PROPOSED
- **Target Subsystem**: Repository layout, DVC stage wiring, reusable Python modules, and CLI scripts (`src/`, `scripts/`, `tests/`, `dvc.yaml`)
- **Linked Commits**: Pending acceptance

---

## 1. Context & Problem Statement
The `scripts/` directory currently contains 28 flat Python files mixing unrelated responsibilities, while `src/` is empty despite already being configured on `pythonpath` in [`pytest.ini`](../../pytest.ini). Reusable library modules ([`scripts/source_contracts.py`](../../scripts/source_contracts.py), [`scripts/ercot_fuel_mix_qc.py`](../../scripts/ercot_fuel_mix_qc.py)), data-acquisition scripts, spatial geometry builders, weather/fuel-mix/load normalizers, legacy backcast scripts, and exploratory API probes all coexist without directory structure or a manifest.

Executing [`ADR-00001`](ADR-00001-adopt-ercot-native-load-hourly-target---.md) will add new `Native_Load` download/normalization scripts and hourly fuel-mix aggregation scripts, worsening discoverability and obscuring which code is active in [`dvc.yaml`](../../dvc.yaml), which code is reusable library logic, and which code is exploratory or superseded.

## 2. Decision Drivers & Constraints
- **Clear Separation of Library vs. CLI**: Reusable contracts, QC logic, parsers, and future Bayesian GAM modeling code must live in an importable package under `src/`, while `scripts/` holds thin, domain-grouped CLI entrypoints.
- **Self-Documentation**: Every directory and stage must be easy to navigate via a concise `scripts/README.md` inventory mapping stages, status (Active DVC / Legacy / Exploratory), inputs, and outputs.
- **DVC Lineage Integrity**: [`dvc.yaml`](../../dvc.yaml) and [`dvc.lock`](../../dvc.lock) must stay consistent with any moved scripts so local Parquet/data outputs are neither lost nor left with broken stage paths.
- **Test Continuity**: [`tests/`](../../tests) (and `importlib.util` / module imports) must remain clean and passing under `pythonpath = . src` without path hacks.

## 3. Considered Alternatives & Rejection Rationale
- **Alternative A: Leave existing 28 files flat in `scripts/` and only add `scripts/README.md`**. Rejected because it still forces new `ADR-00001` modules into a crowded flat folder and keeps `src/` empty.
- **Alternative B: Only place new `ADR-00001` code in `src/` and subdirectories while leaving old scripts flat**. Rejected because splitting old and new conventions leaves the codebase inconsistent and still mixes legacy backcast scripts with active pipeline entrypoints.
- **Alternative C: Collapse all 12 year-specific fuel-mix normalizers into one dynamic script**. Rejected because year-specific DST/column idiosyncrasies were explicitly audited and locked per year in [`dvc.yaml`](../../dvc.yaml); moving them cleanly into `scripts/ercot_fuel_mix/` preserves per-year auditability without rewriting working logic.

## 4. Decision Outcome
Reorganize Python code into a clean `src/` library package and domain-grouped `scripts/` tree, then update [`dvc.yaml`](../../dvc.yaml), [`dvc.lock`](../../dvc.lock), and [`tests/`](../../tests) atomically:

1. **Reusable Package (`src/bayes_demand_fcst/`)**:
   - Move shared contracts (`source_contracts.py` -> `src/bayes_demand_fcst/contracts.py`) and shared fuel-mix QC (`ercot_fuel_mix_qc.py` -> `src/bayes_demand_fcst/qc/fuel_mix.py`), establishing `src/bayes_demand_fcst/` as the home for shared parsers, QC helpers, and future Bayesian GAM modules.
2. **Domain-Grouped CLI Entrypoints (`scripts/`)**:
   - `scripts/README.md`: Concise table listing each script, status (`Active DVC`, `Manual/Exploratory`, or `Legacy`), inputs, and outputs.
   - `scripts/weather/`: ASOS downloaders (`download_asos_weather_{ar,la,nm,ok,tx}.py`), `normalize_asos_weather.py`, `aggregate_regional_weather.py`.
   - `scripts/spatial/`: `extract_ercot_map.py`, `build_station_metadata.py`, `build_voronoi_weights.py`.
   - `scripts/fuel_mix/`: `normalize_ercot_fuel_mix_2014.py`--`2025.py` (plus future hourly aggregator from `ADR-00001`).
   - `scripts/load/`: `query_ercot_system_demand.py`, legacy `extract_ercot_backcast.py` and `normalize_ercot_load.py` (clearly marked legacy/non-canonical in `scripts/README.md`), plus future `Native_Load` scripts from `ADR-00001`.
   - `scripts/panel/`: `assemble_observed_panel.py`.
3. **DVC & Test Re-wiring**:
   - Update `cmd` and `deps` paths in [`dvc.yaml`](../../dvc.yaml) and refresh [`dvc.lock`](../../dvc.lock) for moved dependencies (without losing existing `data/` artifacts; re-verify hashes via `dvc commit` / `dvc status` on active stages with existing inputs/outputs).
   - Update test imports and file-path loaders in [`tests/`](../../tests) and verify the full `pytest` suite passes.

## 5. Invariants & Guardrails
- **No Flat `scripts/` Additions**: New CLI entrypoints MUST be placed in an appropriate `scripts/<domain>/` subdirectory and documented in `scripts/README.md`.
- **No Business Logic Only Inside CLIs**: Shared validation, QC, DST rules, and modeling functions MUST live in `src/bayes_demand_fcst/` so they are importable without loading CLI modules by file path.
- **No Unwired `dvc.yaml` Paths**: Every script referenced in `dvc.yaml` `cmd:` and `deps:` MUST match its path on disk, with `dvc status` verified after any path migration.
- **Clean Imports**: Scripts run via `.venv/bin/python scripts/<domain>/<script>.py` (or `python -m`) MUST resolve `bayes_demand_fcst` deterministically without fragile relative-import fallbacks.

## 6. Consequences & Downstream Impact
- **Positive / Guarantees**: Eliminates the flat 28-file pile in `scripts/`; activates `src/` ahead of `ADR-00001` implementation and future Bayesian GAM modeling; gives immediate visibility into active vs. exploratory vs. legacy scripts.
- **Negative / Trade-offs**: Requires updating imports across ~27 test files and updating stage `cmd`/`deps` in `dvc.yaml` (and refreshing `dvc.lock` hashes) in a dedicated refactor commit before starting `ADR-00001` implementation.
