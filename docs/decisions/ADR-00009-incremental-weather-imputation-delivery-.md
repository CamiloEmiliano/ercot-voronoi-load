# ADR-00009: Incremental Software Delivery for Weather Imputation

- **ADR ID**: ADR-00009
- **Filename**: `ADR-00009-incremental-weather-imputation-delivery-.md`
- **Date**: 2026-11-19 07:14 UTC
- **Status**: PROPOSED
- **Target Subsystem**: Incremental implementation, test gates, DVC stage delivery, and package layout for the weather imputation system (`src/bayes_demand_fcst/`, `scripts/weather/`, `scripts/spatial/`, `tests/`, `dvc.yaml`)
- **Linked Commits**: Pending acceptance

---

## 1. Context & Problem Statement
[`ADR-00003`](ADR-00003-spatiotemporal-kriging-group-a-weather--.md) through [`ADR-00007`](ADR-00007-coastline-and-ercot-weather-zone-layers-.md) specify variable-specific weather physics, spatial geometry, imputation methods, and output schemas. [`ADR-00008`](ADR-00008-weather-imputation-dag-and-anti-leakage-.md) specifies the cross-group dependency DAG, strict causal observation boundary, frozen calibration window, and pass-level orchestration.

Neither set of ADRs specifies how this system should be delivered as production software. Implementing all proposed stages, ~33.35M station-hours per variable, shared ST-RK infrastructure, geometry enrichment, and downstream aggregation in one implementation commit would create an unreviewable, difficult-to-debug change. A naive implementation in ADR-number order (`ADR-00003` -> `ADR-00004` -> `ADR-00005` -> `ADR-00006` -> `ADR-00007`) is also incorrect because dependencies are cross-cutting: the Group B $\Delta P$ model needs Group A $\text{tmpf}_{12\text{h}}$, visibility reconciliation needs Group A plus `p01i`, and cloud reconciliation needs completed `p01i`.

This ADR defines the software-production strategy: small releasable slices, shared infrastructure boundaries, acceptance gates, intermediate DVC artifacts, and conditions for accepting final canonical outputs.

## 2. Decision Drivers & Constraints
- **Reviewable Changes**: Each delivery increment must be testable and reviewable independently; no increment may combine every variable group, solver, spatial artifact, and DVC rewrite at once.
- **Specification Fidelity**: Implementation order may differ from ADR number, but final behavior must still satisfy [`ADR-00003`](ADR-00003-spatiotemporal-kriging-group-a-weather--.md)–[`ADR-00008`](ADR-00008-weather-imputation-dag-and-anti-leakage-.md).
- **Causal & Calibration Invariants First**: `t_obs <= t_0` enforcement, provenance, and `train_end_utc` parameter freezing are foundational infrastructure, not late-stage patches.
- **Small Synthetic Tests, Selective Full-Data Runs**: Routine unit tests must use compact synthetic fixtures; expensive 720-CSV/full-year integration stages must be explicit slow/integration tests or DVC runs, not implicit `pytest` work.
- **DVC Artifact Stability**: Existing canonical `weather_station_hourly` and `weather_regional_hourly` outputs must not be overwritten by incomplete or diagnostic-only implementations.
- **Low-Risk CLI Cutover**: Existing scripts remain usable until their replacement stage is tested and wired; CLIs remain thin orchestrators while reusable logic lives in `src/bayes_demand_fcst/` per [`ADR-00002`](ADR-00002-restructure-scripts-into-src-and-domains.md).

## 3. Considered Alternatives & Rejection Rationale
- **Alternative A: Implement all of `ADR-00003`–`ADR-00008` in one large refactor**. Rejected because shared-solver bugs, physical reconciliation defects, and DVC lineage regressions would be difficult to isolate; review and rollback scope would be too broad.
- **Alternative B: Implement strictly by ADR number (`00003` -> `00004` -> `00005` -> `00006` -> `00007`)**. Rejected because [`ADR-00004`](ADR-00004-spatiotemporal-kriging-group-b-pressure-.md) requires Group A $\text{tmpf}_{12\text{h}}$, [`ADR-00005`](ADR-00005-bounded-interpolation-group-c-precip-vis.md) and [`ADR-00006`](ADR-00006-cloud-fraction-kriging-group-d-sky-cover.md) consume completed primary cubes, and [`ADR-00007`](ADR-00007-coastline-and-ercot-weather-zone-layers-.md) is a prerequisite for both drift and aggregation.
- **Alternative C: Build ad hoc variable scripts independently and integrate later**. Rejected because it duplicates neighborhood, causality, covariance, provenance, QC, and temporal-grid logic and would create incompatible intermediate schemas.

## 4. Decision Outcome
Adopt a **vertical-slice, dependency-driven production plan** in six delivery gates. Within a gate, independent branches may be developed in parallel; later gates do not begin until required artifacts and acceptance tests from earlier gates pass.

| Delivery Gate | Scope | Required Outputs | Exit Criteria |
|---|---|---|---|
| **Gate 0 — Shared Contracts & Spatial Prerequisites** | Create the `src/bayes_demand_fcst/weather/` and `src/bayes_demand_fcst/spatial/` package skeletons, typed configuration/schema objects, compact provenance enums, causality helpers, synthetic fixtures, and [`ADR-00007`](ADR-00007-coastline-and-ercot-weather-zone-layers-.md) geometry interfaces. | Versioned schemas; `dist_coast_km` contract; zone/weight interfaces; causal neighborhood API; small deterministic fixtures. | Unit tests and Pylance clean; no causal helper accepts `t_obs > t_0`; spatial tests verify CRS, zone naming, and unit-sum invariants. |
| **Gate 1 — Raw Clean Store & Pass-0 QC** | Read the 720 ASOS CSVs once into a typed irregular-observation store; apply row-level QC, deduplication, exact algebraic recoveries, trace/height/QC handling, and raw-to-hour provenance preparation. | `data/interim/weather_irregular_clean/`; quality report and schema contract. | Synthetic plus selected-month integration checks; no duplicated `(station_id, valid)` keys; raw source archive remains unmodified; output can be consumed by all Group A–D modules. |
| **Gate 2 — Frozen Calibration & Shared Solver Infrastructure** | Implement train-window parameter artifacts, harmonic drift helpers, covariance kernels, stratified neighborhoods, SPD Cholesky/Schur-complement kriging, prior-shrunk KNN/IDW, and temporal local fast paths. | `data/interim/weather_calibration/` parameter artifacts; reusable solver modules. | Parameters reject `valid > train_end_utc`; covariance matrices remain positive definite on fixtures; causality counterfactual tests pass; solver behavior is deterministic and compactly typed. |
| **Gate 3 — Primary Cubes** | Deliver Group A (`tmpf`, `dwpf`, `feel_diff` plus reconciliation), primary `alti`, and `p01i` branches; include causal training replay needed to derive $\text{tmpf}_{12\text{h}}$. | `data/interim/weather_primary_hourly/`; per-variable QC/provenance. | Physical bounds and reconciliation tests pass; each primary variable can be computed independently after Gate 2; no test-year parameter leakage; a focused DVC repro completes without materializing dependent variables. |
| **Gate 4 — Conditioned Cubes** | Deliver $\Delta P \to \text{mslp}$, `vsby` fog reconciliation, and `cloud_frac` rain-cloud reconciliation once required primary cubes exist. | Final `data/interim/weather_station_hourly/`; propagated uncertainty/provenance. | Dependency checks fail early when required inputs are absent; $\text{mslp}$ units/variance, visibility bounds, and cloud bounds pass; causal counterfactual tests cover each conditioned variable. |
| **Gate 5 — Regional/Zone Integration & Panel Cutover** | Apply statewide and 8-zone Voronoi weights, derive regional pressure tendency, update the panel assembler, and replace the old weather normalizer/aggregator only after parity and quality review. | `data/interim/weather_regional_hourly/`, `weather_zone_hourly/`, and `data/processed/ercot_hourly_panel/`. | Weights sum to one; 105,192 UTC-hour alignment is exact; no `valid > timestamp_utc`; full `pytest`, Pylance, DVC status, and focused data-quality reports pass. |

Canonical output cutover is atomic by gate: an incomplete or experimental implementation may write to explicitly versioned diagnostic artifacts (for example `weather_station_hourly_v0/`) but MUST NOT replace the canonical `weather_station_hourly` or `weather_regional_hourly` DVC output until the relevant gate exits successfully.

## 5. Invariants & Guardrails
- **Specification vs. Delivery Separation**: [`ADR-00003`](ADR-00003-spatiotemporal-kriging-group-a-weather--.md)–[`ADR-00007`](ADR-00007-coastline-and-ercot-weather-zone-layers-.md) define *what* is correct; [`ADR-00008`](ADR-00008-weather-imputation-dag-and-anti-leakage-.md) defines dependency and leakage boundaries; this ADR defines *how* those capabilities are released and accepted.
- **No Naive ADR-Number Implementation Order**: Code delivery MUST follow data dependencies and gate readiness, not ADR numeric order.
- **Shared Infrastructure Before Variable Duplication**: Causality filtering, neighbor queries, covariance/solver primitives, provenance, and persisted QC helpers MUST exist once under `src/bayes_demand_fcst/` before multiple variable modules duplicate them.
- **Synthetic Unit Tests by Default**: Unit tests MUST use small synthetic ASOS fixtures; full 720-CSV or multi-year runs belong to explicit integration/DVC validation commands, not default unit-test setup.
- **Independent Gate Reproducibility**: Each delivery gate MUST have a named CLI/DVC entrypoint, explicit inputs/outputs, and a quality report sufficient to diagnose that gate without re-running unrelated downstream work.
- **No Silent Canonical Overwrite**: Experimental/intermediate algorithms and DVC outputs MUST use versioned artifact paths until Gate 5 review; canonical output replacement requires an atomic persisted-output QC pass.
- **One Acceptance Record per Release Slice**: A specification ADR remains `PROPOSED` until its required implementation gates, tests, DVC wiring, and quality reports are complete; unrelated gates do not partially mark it `ACCEPTED`.

## 6. Consequences & Downstream Impact
- **Positive / Guarantees**: Keeps the weather ETL manageable through reviewable vertical slices; allows parallel work on independent primary variables while preserving dependency correctness; isolates resource-heavy kriging from routine unit tests; provides an end-to-end route from spatial prerequisites through causal station and regional weather artifacts before downstream machine-learning work begins.
- **Negative / Trade-offs**: Requires additional intermediate DVC artifacts and integration points; a module may need schema adjustments when a later dependent gate exposes a missing field; Gate 5 panel work waits until all required station-level variables pass their quality and causality checks.
