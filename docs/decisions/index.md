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
| [ADR-00001](ADR-00001-adopt-ercot-native-load-hourly-target---.md) | Adopt ERCOT Native Load as Hourly Forecasting Target | ACCEPTED | 2026-11-17 | Load ingestion, fuel-mix aggregation, Bayesian GAM scope | Pending implementation |
