# ADR-00003: Local Spatiotemporal Regression-Kriging for Group A Weather Variables

- **ADR ID**: ADR-00003
- **Filename**: `ADR-00003-spatiotemporal-kriging-group-a-weather--.md`
- **Date**: 2026-11-17 22:50 UTC
- **Status**: PROPOSED
- **Target Subsystem**: ASOS weather alignment and thermodynamic reconstruction (`src/bayes_demand_fcst/`, `scripts/weather/`, `tests/`, `dvc.yaml`)
- **Linked Commits**: Pending acceptance

---

## 1. Context & Problem Statement
[`ADR-00001`](ADR-00001-adopt-ercot-native-load-hourly-target---.md) requires 1:1 alignment between hourly UTC ERCOT load (`load_hourly`), hourly UTC fuel mix (`fuel_mix_hourly`), and regional weather (`weather_regional_hourly`). Inspection of raw ASOS archives across all five states (`TX`, `NM`, `OK`, `AR`, `LA`) shows that raw `valid` timestamps are irregular and sub-hourly:
- Only **0.1%–0.5%** of ASOS rows occur at exact top-of-hour `:00` marks.
- METAR stations report primarily at `:51` or `:53` plus irregular `SPECI` updates (with occasional multi-hour outages, e.g., 5 hours at `AUS` in Jan 2014), whereas AWOS stations report every 20 minutes (`:15`, `:35`, `:55`).
- [`scripts/weather/normalize_asos_weather.py`](../../scripts/weather/normalize_asos_weather.py) currently preserves raw sub-hourly timestamps verbatim and [`scripts/weather/aggregate_regional_weather.py`](../../scripts/weather/aggregate_regional_weather.py) groups by exact `timestamp_utc`, causing downstream exact-hour joins in [`scripts/panel/assemble_observed_panel.py`](../../scripts/panel/assemble_observed_panel.py) to discard ~99.5% of observations.

Simple temporal interpolation (e.g., per-station PCHIP) aligns regular `:53`/`:55` cadences but cannot reliably reconstruct multi-hour or multi-day station outages without erasing diurnal cycles. Moreover, the four thermal/moisture variables in **Group A (`tmpf`, `dwpf`, `relh`, `feel`)** are physically coupled; interpolating each independently risks violating thermodynamic constraints ($T_d > T$, inconsistent $RH$, or mismatches between apparent and dry-bulb temperature).

## 2. Decision Drivers & Constraints
- **Exact Canonical Grid**: Output station-hour records must align strictly to the 105,192 contiguous UTC hour-ending timestamps (`2014-01-01T07:00:00+00:00` through `2026-01-01T06:00:00+00:00`) defined by `load_hourly` and `fuel_mix_hourly`.
- **Thermodynamic Consistency**: Reconstructed station-hours must guarantee $\widehat{\text{dwpf}} \le \widehat{\text{tmpf}}$, $0\% \le \widehat{\text{relh}} \le 100\%$, and physically consistent apparent temperature ($\widehat{\text{feel}}$).
- **Space-Time Continuity**: Irregular `:51`/`:53`/`:55`/`SPECI` observations must enter with their exact timestamps ($\Delta t$) without discarding sub-hourly information or inventing artificial diurnal cycles across long outages.
- **Projected Spatial Metric**: All spatial distances must use the repository's projected coordinate reference system (`EPSG:3083`, Texas Centric Albers Equal Area, meters) established in [`scripts/spatial/build_voronoi_weights.py`](../../scripts/spatial/build_voronoi_weights.py).
- **No Wind Speed in Raw CSVs**: Raw ASOS archives do not include wind speed; cold-weather wind chill ($\text{tmpf} \le 50^\circ\text{F}$) cannot be recomputed algebraically when `feel` is missing and must be inferred spatially/temporally from neighboring observations.

## 3. Considered Alternatives & Rejection Rationale
- **Alternative A: Purely temporal binning (`ceil("h")`) or PCHIP per station without spatial kriging**. Rejected because raw `ceil("h")` drops station coverage during single-hour gaps, while unconditional temporal PCHIP across multi-hour/multi-day outages linearly bridges missing overnight lows and daytime highs; additionally, Voronoi weight renormalization abruptly shifts regional weight to distant polygons.
- **Alternative B: Kriging all four Group A variables (`tmpf`, `dwpf`, `relh`, `feel`) independently**. Rejected because `relh` exhibits sharp local swings from afternoon heating and cloud shadows (unlike smooth air-mass `dwpf`), independent kriging of `tmpf`, `dwpf`, and `relh` frequently violates $T_d \le T$ and $RH = f(T, T_d)$, and kriging raw `feel` duplicates >99% correlated work already performed on `tmpf`.
- **Alternative C: Cubic spline resampling**. Rejected because curvature continuity ($C^2$) constraints introduce overshoot/ringing artifacts around sharp frontal passages or missing-data boundaries.

## 4. Decision Outcome
Adopt a **3-step Local Spatiotemporal Regression-Kriging (ST-RK)** pipeline for **Group A (`tmpf`, `dwpf`, `relh`, `feel`)** across all 317 ASOS stations onto the canonical UTC hourly grid, modeling **2.5 variables** (`tmpf`, `dwpf`, and $\Delta_{\text{feel}} = \text{feel} - \text{tmpf}$):

1. **Step 1 — Pre-Kriging Algebraic Recovery (Physical Identities)**:
   - On raw ASOS rows with partial missingness (converting $^\circ\text{F} \leftrightarrow {}^\circ\text{C}$ as required):
     - **Recover `dwpf`**: Where `dwpf` is missing and both `tmpf` and `relh` are present, recover `dwpf` by inverting the Magnus-Tetens saturation vapor-pressure relation.
     - **Recover `feel` in warm/mild regimes**: Where `feel` is missing and $\text{tmpf} > 50^\circ\text{F}$, recompute apparent temperature using NWS Heat Index / Steadman rules from `tmpf` and `dwpf`/`relh` (setting $\text{feel} = \text{tmpf}$ in mild regimes where no Heat Index / Steadman adjustment applies). Cold gaps ($\text{tmpf} \le 50^\circ\text{F}$, where wind chill depends on unarchived wind speed) remain missing for Step 2.
2. **Step 2 — Local 3D Space-Time Regression-Kriging (ST-RK)**:
   - **Target variables**: Krige **`tmpf`**, **`dwpf`**, and the offset **$\Delta_{\text{feel}} = \text{feel} - \text{tmpf}$** (isolating spatial wind-chill depression and heat-index excess; never krige `relh` or raw `feel` directly).
   - **Deterministic trend $m(\mathbf{s}, t)$**: Remove station-level diurnal/seasonal baseline plus static spatial covariates (e.g., elevation lapse rate and coastal proximity in `EPSG:3083`).
   - **Synoptic residual kriging $\varepsilon(\mathbf{s}, t)$**: Perform local 3D space-time kriging (projected $(x, y)$ in `EPSG:3083` plus exact observation time lag $\Delta t$) on residuals $Z(\mathbf{s}, t) - m(\mathbf{s}, t)$ within a local space-time neighborhood around each target `(station_id, timestamp_utc)`, then add $m(\mathbf{s}, t)$ back. Irregular `:51`/`:53`/`:55`/`SPECI` timestamps enter with exact $\Delta t$, and station outages smoothly borrow strength from neighboring stations.
   - Retain per-variable kriging variance $\sigma_K^2(\mathbf{s}, t)$ (and raw observation flags) as audit diagnostics.
3. **Step 3 — Post-Kriging Thermodynamic Reconciliation**:
   - **Saturation clamp**: Enforce $\widehat{\text{dwpf}} \leftarrow \min(\widehat{\text{dwpf}},\, \widehat{\text{tmpf}})$.
   - **Deterministic `relh`**: Recompute $\widehat{\text{relh}}$ from $(\widehat{\text{tmpf}},\, \widehat{\text{dwpf}})$ via Magnus-Tetens, guaranteeing $0\% \le \widehat{\text{relh}} \le 100\%$ and exact physical consistency.
   - **Reconstruct `feel`**: Compute $\widehat{\text{feel}} = \widehat{\text{tmpf}} + \widehat{\Delta}_{\text{feel}}$ (or recompute NWS Heat Index directly from $(\widehat{\text{tmpf}}, \widehat{\text{relh}})$ when $\widehat{\text{tmpf}} \ge 80^\circ\text{F}$, reserving $\widehat{\Delta}_{\text{feel}}$ for the cold wind-chill regime $\widehat{\text{tmpf}} \le 50^\circ\text{F}$ and matching raw observed values where exact non-null station observations exist on the hour).

*(Group B variables — `mslp`, `alti`, `vsby`, `p01i`, and sky cover `skyc1`/`skyl1` — will be specified in a companion ADR before implementation of the updated weather pipeline.)*

## 5. Invariants & Guardrails
- **No Independent `relh` Kriging**: `relh` MUST NEVER be kriged independently of `tmpf` and `dwpf`; it MUST be derived post-reconciliation from $(\widehat{\text{tmpf}}, \widehat{\text{dwpf}})$.
- **Saturation Invariant**: Every output station-hour MUST satisfy $\widehat{\text{dwpf}} \le \widehat{\text{tmpf}}$ and $0.0 \le \widehat{\text{relh}} \le 100.0$.
- **Canonical Grid & Cross-Boundary Continuity**: Resampling MUST span month and year boundaries continuously using exact observation timestamps and output the exact 105,192 UTC hours matching `load_hourly`.
- **Raw Provenance Separation**: Raw ASOS archives in `data/raw/weather_*_asos/` MUST remain unmodified; kriged/reconstructed station-hours MUST record provenance/uncertainty metadata (observed vs. algebraically recovered vs. kriged, plus kriging variance) in `quality_report.json` or diagnostic columns.
- **Library Placement**: Reusable thermodynamic conversions, trend models, and ST-RK solvers MUST live in `src/bayes_demand_fcst/` per [`ADR-00002`](ADR-00002-restructure-scripts-into-src-and-domains.md).

## 6. Consequences & Downstream Impact
- **Positive / Guarantees**: Eliminates the ~99.5% timestamp-join data loss in [`scripts/panel/assemble_observed_panel.py`](../../scripts/panel/assemble_observed_panel.py); preserves exact thermodynamic coupling across `tmpf`, `dwpf`, `relh`, and `feel`; naturally handles mixed METAR/AWOS reporting cadences and station outages using all 317 five-state stations; yields spatial kriging variance as a built-in data-quality signal.
- **Negative / Trade-offs**: Increases compute complexity relative to simple chunked CSV filtering (requiring multi-month/cross-boundary state in memory or indexed disk windows for 317 stations $\times$ 105,192 hours); requires companion decisions for Group B weather variables (`mslp`, `alti`, `vsby`, `p01i`, `skyc1`/`skyl1`), variogram parameterization, and completing the missing `TX` ASOS raw archives (`2016-04`–`2025-12`).
