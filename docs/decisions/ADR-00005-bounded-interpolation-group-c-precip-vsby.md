# ADR-00005: Bounded Non-Gaussian Interpolation for Group C Weather Variables (`p01i`, `vsby`)

- **ADR ID**: ADR-00005
- **Filename**: `ADR-00005-bounded-interpolation-group-c-precip-vsby.md`
- **Date**: 2026-11-18 02:45 UTC
- **Status**: PROPOSED
- **Target Subsystem**: ASOS precipitation (`p01i`) and visibility (`vsby`) alignment and reconstruction (`src/bayes_demand_fcst/`, `scripts/weather/`, `tests/`, `dvc.yaml`)
- **Linked Commits**: Pending acceptance

---

## 1. Context & Problem Statement
Group C of the regional weather schema comprises **`p01i`** (1-hour precipitation accumulation, inches) and **`vsby`** (horizontal visibility, miles). Neither variable behaves as a smooth, unbounded Gaussian field:
- **`p01i`** is an accumulated total since the last routine METAR reset (typically `:51`–`:55`), not an instantaneous rate. In `TX` Jan 2014 raw ASOS observations, **97.2%** of rows are `0.00`, **1.39%** are `NaN` (including omitted dry precipitation groups or `SPECI` rows), and only **~1.4%** exceed `0.005` in (while IEM encodes trace `"T"` as `0.0001`). Summing `SPECI` + routine readings within an hour double-counts rainfall, temporal interpolation smears discrete accumulations, and unconstrained Gaussian kriging creates negative rain and wide artificial drizzle around isolated convective cells.
- **`vsby`** is heavily right-censored at `10.0` mi (**87.4%** of `TX` Jan 2014 observations equal `10.0` mi, with a small tail `> 10.0` at manual/augmented stations and discrete aviation steps below `10.0`). Low visibility (`< 3` mi) in `TX` Jan 2014 strongly tracks near-saturated air (`median(T - T_d) = 0.8°F`). Unconstrained linear/Gaussian kriging extrapolates above `10.0` mi, over-smooths rapid sunrise fog burn-off, and can bleed localized coastal/valley fog into dry inland stations.

Together with [`ADR-00003`](ADR-00003-spatiotemporal-kriging-group-a-weather--.md) (Group A) and [`ADR-00004`](ADR-00004-spatiotemporal-kriging-group-b-pressure-.md) (Group B), completing Group C finishes the 8-variable regional weather contract in [`scripts/weather/aggregate_regional_weather.py`](../../scripts/weather/aggregate_regional_weather.py).

## 2. Decision Drivers & Constraints
- **Accumulator Semantics (`p01i`)**: Within-hour `SPECI` and routine reports share the same hourly accumulation bucket and MUST NOT be summed or interpolated across hour boundaries.
- **Zero-Inflation & Non-Negativity (`p01i`)**: Imputed rainfall MUST satisfy $\widehat{\text{p01i}} \ge 0$ without smearing point storms across dry regions.
- **Right-Censoring & Fog Physics (`vsby`)**: Reconstructed visibility MUST respect $\widehat{\text{vsby}} \in [0.0,\, 10.0]$ (capping sensor-augmented values `> 10.0` to the common ASOS `10.0` mi ceiling before spatial interpolation) and leverage Group A dewpoint depression ($T - T_d$) from [`ADR-00003`](ADR-00003-spatiotemporal-kriging-group-a-weather--.md) to reject dry false-fog imputations.
- **Canonical Grid & Projected Distance**: Outputs align strictly to the 105,192 contiguous UTC hour-ending timestamps (`2014-01-01T07:00:00+00:00`–`2026-01-01T06:00:00+00:00`), using `EPSG:3083` meters for spatial distances.
- **Sky Cover Exclusion (`skyc1`, `skyl1`)**: Categorical sky cover (`skyc1`) and ceiling height (`skyl1`) remain excluded from regional numeric Voronoi aggregation in [`scripts/weather/aggregate_regional_weather.py`](../../scripts/weather/aggregate_regional_weather.py) (preserved only as raw/station metadata if needed).

## 3. Considered Alternatives & Rejection Rationale
- **Alternative A: Standard Gaussian ST-RK on raw `p01i` and raw `vsby`**. Rejected because Gaussian kriging predicts negative rainfall (`p01i < 0`), smears localized thunderstorms into widespread drizzle, extrapolates `vsby > 10.0`, and spreads coastal fog onto dry inland stations.
- **Alternative B: Summing all `p01i` rows within an hour-ending window**. Rejected because `SPECI` reports during rain already roll into the subsequent routine METAR accumulation, causing double-counting.
- **Alternative C: Dropping `p01i` and `vsby` from the weather pipeline**. Rejected because keeping the full 8-variable regional weather schema intact preserves downstream feature exploration (e.g., storm/cloud-cover proxies for solar/cooling demand) with minimal added complexity when handled cleanly.

## 4. Decision Outcome
Adopt **non-Gaussian, physically bounded alignment and local spatial interpolation** for **Group C (`p01i`, `vsby`)** onto the canonical 105,192-hour UTC grid:

1. **`p01i` (1-Hour Precipitation Accumulation)**:
   - **Temporal Reduction (Hour-Ending `(H-1, H]` → `H:00Z`)**: Never interpolate `p01i` temporally and never sum intra-hour `SPECI` + routine readings. Take the routine end-of-hour reading (or `max` within `(H-1:00, H:00]` when multiple reports share the hourly reset cycle).
   - **Dry `NaN` vs. True Missingness Audit**: Classify station-level `NaN` behavior before spatial gap-filling (treating trace `"T"` / `0.0001` as non-zero trace and distinguishing stations that omit dry `p01i` groups when `tmpf`/`alti` are healthy from true gauge outages).
   - **Two-Part Local Spatial Gap-Filling (on true missing station-hours)**:
     1. *Occurrence*: Run **Indicator Kriging** on $I(\mathbf{s}, t) = \mathbf{1}[\text{p01i} > 0.005]$ with a tight spatial correlation scale ($\ell_s \approx 40\text{–}60\text{ km}$).
     2. *Intensity*: Where predicted wet probability exceeds threshold (e.g., $\hat{p} > 0.5$), interpolate $\sqrt{\text{p01i}}$ or $\log(\text{p01i} + \epsilon)$ using **wet neighbors only** within local radius ($40\text{–}60\text{ km}$) and back-transform; otherwise assign `0.0` (with local capped-radius IDW as an approved deterministic fallback when kriging neighborhoods have too few wet pairs).
2. **`vsby` (Horizontal Visibility)**:
   - **Censoring & Temporal Alignment (`:53` → `:00`)**: Cap raw values at `10.0` mi (`min(vsby, 10.0)`) so occasional `> 10.0` manual entries match ASOS sensor ceilings, and align onto `H:00Z` via **nearest valid observation within $\pm 30\text{ min}$** (preserving sharp sunrise fog dissipation without multi-hour smearing).
   - **Spatial Gap-Filling**: On remaining missing station-hours, run Local Space-Time Kriging on $\log(\text{vsby} + \epsilon)$ with short correlation scales ($\ell_s \approx 40\text{–}60\text{ km}$, $\ell_t \approx 2\text{–}3\text{ hr}$), back-transform, and clamp strictly to $[0.0,\, 10.0]$.
   - **Group A Fog-Physics Guardrail**: Where completed Group A `tmpf` and `dwpf` ([`ADR-00003`](ADR-00003-spatiotemporal-kriging-group-a-weather--.md)) show dry air ($T - T_d > 6^\circ\text{F}$) and `p01i == 0`, suppress low spatially imputed fog visibility back toward `10.0` mi.

## 5. Invariants & Guardrails
- **Non-Negative Precipitation & No Double-Counting**: Every output station-hour MUST satisfy $\widehat{\text{p01i}} \ge 0.0$, and intra-hour `SPECI` + routine `p01i` values MUST NOT be summed.
- **Bounded Visibility**: Every output station-hour MUST satisfy $0.0 \le \widehat{\text{vsby}} \le 10.0$.
- **Dependency Ordering**: Group C `vsby` spatial reconciliation MUST run after Group A (`tmpf`, `dwpf`) and `p01i` so the dewpoint-depression fog check ($T - T_d > 6^\circ\text{F}$ with `p01i == 0`) has complete inputs.
- **Diagnostic Provenance**: Record dry-omission classification, trace counts, capped `> 10.0` raw counts, and fog-suppression adjustments in `quality_report.json`.

## 6. Consequences & Downstream Impact
- **Positive / Guarantees**: Completes the 8-variable regional weather schema across Groups A, B, and C without negative rainfall, storm smearing, or `> 10.0` visibility extrapolation; preserves localized convective rain and morning fog structure.
- **Negative / Trade-offs**: Two-part precipitation kriging and log-visibility clamping add per-variable branching to the weather normalization module under `src/bayes_demand_fcst/`; requires finalizing shared variogram/neighborhood defaults and resuming missing `TX` ASOS raw archives (`2016-04`–`2025-12`) before execution.
