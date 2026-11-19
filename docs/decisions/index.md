# Architecture Decision Records (ADR) Registry

This directory maintains the immutable, chronological record of all high-consequence architectural decisions, design trade-offs, and invariants for the ERCOT Bayesian Demand Forecasting data science project.

---

## Filename & Formatting Conventions

To preserve visual discipline, strict columnar alignment in directory listings, and predictable sorting, every ADR filename MUST adhere to the following specification:

- **Format**: `ADR-[5_DIGIT_ID]-[40_CHARACTER_SLUG].md`
- **Total Filename Length**: Exactly **53 characters** (`4 + 5 + 1 + 40 + 3 = 53`).
- **Slug Padding Rule**: The descriptive slug must be exactly **40 characters** in length using lowercase alphanumeric characters and hyphens. If the natural description is under 40 characters, pad it with trailing hyphens (`-`).

Example:
```
ADR-00001-record-architecture-decisions-in-repo--.md  (53 chars)
ADR-00002-hourly-ercot-native-load-and-fuel-mix--.md  (53 chars)
```

---

## Lifecycle Statuses

- **`PROPOSED`**: Under active discussion and review; not yet accepted into code.
- **`ACCEPTED`**: Approved and actively governing the codebase.
- **`SUPERSEDED BY ADR-XXXXX`**: Formally replaced by a subsequent decision record.
- **`DEPRECATED`**: Obsolete; no longer enforced or maintained.

---

## Master Decision Index

| ADR ID | Title | Status | Date (UTC) | Target Subsystem | Linked Commits |
|---|---|---|---|---|---|
| [ADR-00001](ADR-00001-adopt-ercot-native-load-hourly-target---.md) | Adopt ERCOT Native Load as Hourly Forecasting Target | ACCEPTED | 2026-11-17 | Load ingestion, fuel-mix aggregation, Bayesian GAM scope | [`3624d47`](../../scripts/load/normalize_ercot_native_load.py) |
| [ADR-00002](ADR-00002-restructure-scripts-into-src-and-domains.md) | Restructure `scripts/` into `src/` Package and Domain Subdirectories | ACCEPTED | 2026-11-17 | Repository layout, DVC wiring, `src/`, `scripts/`, `tests/` | [`c162855`](../../src/bayes_demand_fcst/contracts.py), [`6eb7389`](../../scripts/weather/normalize_asos_weather.py), [`75a9955`](../../scripts/fuel_mix/aggregate_ercot_fuel_mix_hourly.py), [`373c260`](../../scripts/load/normalize_ercot_native_load.py) |
| [ADR-00003](ADR-00003-spatiotemporal-kriging-group-a-weather--.md) | Local Spatiotemporal Regression-Kriging for Group A Weather Variables | PROPOSED | 2026-11-17 | ASOS weather alignment, ST-RK (`tmpf`, `dwpf`, `relh`, `feel`) | Pending acceptance |
| [ADR-00004](ADR-00004-spatiotemporal-kriging-group-b-pressure-.md) | Primary/Auxiliary Spatiotemporal Kriging for Group B Pressure Variables (`alti`, `mslp`) | PROPOSED | 2026-11-17 | ASOS barometric alignment (`alti`, `mslp`) | Pending acceptance |
| [ADR-00005](ADR-00005-bounded-interpolation-group-c-precip-vis.md) | Bounded Non-Gaussian Interpolation for Group C Weather Variables (`p01i`, `vsby`) | PROPOSED | 2026-11-18 | ASOS precipitation & visibility (`p01i`, `vsby`) | Pending acceptance |
| [ADR-00006](ADR-00006-cloud-fraction-kriging-group-d-sky-cover.md) | Cloud Fraction Encoding and Kriging for Group D Sky Cover Variables (`skyc1`, `skyl1`) | PROPOSED | 2026-11-18 | ASOS sky cover & regional `cloud_frac` (`skyc1`, `skyl1`) | Pending acceptance |
| [ADR-00007](ADR-00007-coastline-and-ercot-weather-zone-layers-.md) | Auxiliary Coastline and ERCOT Weather-Zone Spatial Layers | PROPOSED | 2026-11-18 | Gulf coastline (`dist_coast_km`) & 8 ERCOT weather-zone Voronoi weights | Pending acceptance |
| [ADR-00008](ADR-00008-weather-imputation-dag-and-anti-leakage-.md) | Cross-Group Weather Imputation DAG and Causal Anti-Leakage Governance | PROPOSED | 2026-11-19 | Cross-group dependency DAG, `t_obs <= t_0` causality & train-window freezing | Pending acceptance |
| [ADR-00009](ADR-00009-incremental-weather-imputation-delivery-.md) | Incremental Software Delivery for Weather Imputation | PROPOSED | 2026-11-19 | Six-gate implementation plan, DVC slices, tests & canonical cutover | Pending acceptance |
