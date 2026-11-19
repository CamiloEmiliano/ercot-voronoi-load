# ADR-00005: Bounded Non-Gaussian Interpolation for Group C Weather Variables (`p01i`, `vsby`)

- **ADR ID**: ADR-00005
- **Filename**: `ADR-00005-bounded-interpolation-group-c-precip-vis.md`
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
- **No Harmonic Drift Removal**: Unlike smooth synoptic temperature and pressure fields ([`ADR-00003`](ADR-00003-spatiotemporal-kriging-group-a-weather--.md), [`ADR-00004`](ADR-00004-spatiotemporal-kriging-group-b-pressure-.md)), precipitation and fog are intermittent event processes and MUST NOT have Fourier harmonic baselines subtracted prior to interpolation.

## 3. Considered Alternatives & Rejection Rationale
- **Alternative A: Standard Gaussian ST-RK (with Fourier drift) on raw `p01i` and raw `vsby`**. Rejected because rain and fog are intermittent non-Gaussian fields; Gaussian kriging predicts negative rainfall (`p01i < 0`), smears localized thunderstorms into widespread drizzle, extrapolates `vsby > 10.0`, and spreads coastal fog onto dry inland stations.
- **Alternative B: Summing all `p01i` rows within an hour-ending window or interpolating across `:00`**. Rejected because `SPECI` reports during rain already roll into the subsequent routine METAR accumulation, causing double-counting if summed and smearing discrete hourly buckets if interpolated.
- **Alternative C: Dropping `p01i` and `vsby` from the weather pipeline**. Rejected because keeping `p01i` and `vsby` in the regional weather schema preserves storm and fog/visibility signals with bounded, event-appropriate non-Gaussian interpolation.

## 4. Decision Outcome
Adopt a **3-stage non-Gaussian, short-range bounded interpolation** pipeline for **Group C (`p01i`, `vsby`)** across all 317 stations onto the canonical 105,192-hour UTC grid (without Fourier harmonic drift removal):

1. **Stage 1 — Before (Prepare): Accumulator Alignment, Dry-`NaN` Fix & Transform**:
   - **For `p01i` (Hourly Precipitation)**:
     - **Reset-Aligned Cycle Collapse (No Interpolation!)**: Because `p01i` is a running bucket resetting after the routine `:51`–`:55` METAR (while AWOS reports at `:15/:35/:55`), define each target hour `HH:00 UTC` via the reset-aligned half-open cycle **`(H-1:56, H:56]`** (i.e., `(timestamp_utc + 4 min).ceil("h")`) and take the **routine end-of-hour reading (or `max` within that cycle)** — never linearly interpolate across `:00`, never sum `SPECI` + routine readings, and never split post-`:53` `SPECI` reports into the wrong hour.
     - **Neighbor-Aware Dry-`NaN` vs. `PNO` Gauge-Outage Resolution**: When `p01i` is `NaN` while `tmpf` / `alti` are valid, distinguish dry `P0000` omissions from clogged/frozen rain gauges (`PNO`, common during Texas winter ice storms): set `p01i = 0.00` only if the station chronically omits dry zeros (`zero_rate < 5%`) **or** surrounding active neighbors are also dry (`mean(I_wet) < 0.25`); keep `p01i = NaN` during active regional precipitation so broken gauges are spatially imputed in Stage 2 rather than hardcoded to `0.00`.
     - **QC & Two-Part Target Split**: Set `< 0` or `> 8.0 in/hr` to `NaN` and threshold sub-measurable trace values (`0 < p01i < 0.005`, including `0.0001`) $\to$ `0.00` *before* defining wet occurrence. On valid rows, construct:
       - Binary occurrence indicator: $I_{\text{wet}} = \mathbf{1}[\text{p01i} > 0]$
       - Transformed wet intensity (defined where $\text{p01i} > 0$): $Y_{\text{wet}} = \sqrt{\text{p01i}}$
   - **For `vsby` (Visibility)**:
     - **QC & Censoring Cap**: Set `< 0` to `NaN` and cap any rare manual/augmented reports $> 10.0\text{ mi}$ to `10.0`.
     - **Log Transform**: Compute $Y_{\text{vsby}} = \log(\text{vsby} + 0.1)$ to stabilize heavy skew across $0.25\text{–}2.0\text{ mi}$ fog events.

2. **Stage 2 — During (Solve): Prior-Shrunk KNN + Exponential Decay Interpolation** at each remaining `NaN` target `(station_i, HH:00 UTC)`:
   *(Because 42 of the 317 stations — representing `21.4%` of `ERCT` Voronoi area, e.g., `6R6` at `117.5 km`, `CDS` at `91.9 km`, `SJT` at `86.4 km` — have zero neighbors within a hard `60 km` radius, replace hard radius cutoffs with **$K$-Nearest Neighbors ($K \approx 8$ active stations) + soft exponential decay ($\ell_s \approx 45\text{ km}$) + positive background prior weight ($w_0 > 0$)** so sparse stations never produce `NaN` while isolated distant storms/fog shrink safely toward the dry/clear prior rather than teleporting $100+\text{ km}$.)*
   - **A. Solving `p01i` (Two-Part Hurdle with Dry-Prior Shrinkage)**:
     Query $K \approx 8$ nearest active stations in `EPSG:3083` within narrow time window ($\Delta t \le 1\text{ hr}$):
     0. *All-Dry Vectorized Short-Circuit*: If all $K$ active neighbors have $I_{\text{wet}} = 0$, immediately return `0.00` in $O(1)$ (skipping $>92\%$ of missing `p01i` targets).
     1. *Part 1 — Wet/Dry Probability*: Compute prior-shrunk exponential kernel probability with dry prior $p_0 = 0.0$ and pseudo-count weight $w_0 \approx 0.15$ ($\ell_s \approx 45\text{ km}$):
        $$\hat{p}_{\text{wet}}(\mathbf{s}_0, t_0) = \frac{\sum_{i=1}^{K} \exp(-h_i / \ell_s)\, I_{\text{wet}, i}}{w_0 + \sum_{i=1}^{K} \exp(-h_i / \ell_s)} \in [0,\, 1]$$
        *(In dense cities $\sum e^{-h_i/\ell_s} \gg w_0$; in sparse West Texas, 1 isolated storm at $117\text{ km}$ yields $\hat{p}_{\text{wet}} \approx 0.16 < 0.5 \to \text{dry}$, whereas a multi-station synoptic front yields $\hat{p}_{\text{wet}} \ge 0.5 \to \text{wet}$.)*
     2. *Part 2 — Wet Intensity*:
        - If $\hat{p}_{\text{wet}} < 0.5$: predict dry ($\widehat{Y}_{\text{wet}} = 0$).
        - If $\hat{p}_{\text{wet}} \ge 0.5$: compute convex exponential-decay weighted average of $Y_{\text{wet}}$ across **only the wet neighbors** ($I_{\text{wet}} = 1$), which handles $N_{\text{wet}} = 1\text{ or }2$ without singular matrix failures.
   - **B. Solving `vsby` (Causal Nearest Local Bracket + Clear-Prior-Shrunk ST Kernel / Kriging)**:
     1. *Short bracket ($\le 45\text{ min}$ at the same station)*: Take the nearest valid observation in time, breaking exact ties causally in favor of the earlier observation ($t \le t_0$) to avoid smearing rapid morning fog burn-off.
     2. *Longer outages*:
        - *All-Clear Vectorized Short-Circuit*: If all active neighbors in the space-time window equal `10.0 mi`, return `10.0` directly in $O(1)$ (skipping ~85% of missing `vsby` targets).
        - *Otherwise*: Interpolate $Y_{\text{vsby}}$ from $K \approx 8$ nearest stations ($\ell_s \approx 45\text{ km}$, $\ell_t \approx 2.5\text{ hr}$, Exponential $\nu = 1/2$) shrunk with weight $w_0 \approx 0.15$ toward the clear-sky prior $Y_0 = \log(10.0 + 0.1)$ (or SPD Simple/Ordinary Kriging with positive diagonal nugget $\sigma_{\text{nug}}^2 > 0$).

3. **Stage 3 — After (Reconcile): Back-Transform, Clamp & Physical Guards**:
   - **Reconstruct `p01i`**:
     $$\widehat{\text{p01i}}(\mathbf{s}_0, t_0) = \begin{cases} \big(\widehat{Y}_{\text{wet}}(\mathbf{s}_0, t_0)\big)^2 & \text{if } \hat{p}_{\text{wet}} \ge 0.5 \\ 0.00 & \text{if } \hat{p}_{\text{wet}} < 0.5 \end{cases}$$
     Round to $0.01\text{ in}$ and enforce $\widehat{\text{p01i}} \ge 0.00$.
   - **Reconstruct & Clamp `vsby`**:
     $$\widehat{\text{vsby}}(\mathbf{s}_0, t_0) = \operatorname{clip}\!\Big(\exp\!\big(\widehat{Y}_{\text{vsby}}(\mathbf{s}_0, t_0)\big) - 0.1,\; 0.0,\; 10.0\Big)$$
   - **Physical Fog Guard (Cross-Check with Group A)**:
     If an imputed visibility is foggy ($\widehat{\text{vsby}} < 5.0\text{ mi}$), check the station's completed dew point depression ($\widehat{\text{tmpf}} - \widehat{\text{dwpf}}$ from [`ADR-00003`](ADR-00003-spatiotemporal-kriging-group-a-weather--.md)) and $\widehat{\text{p01i}}$. If $\widehat{\text{tmpf}} - \widehat{\text{dwpf}} > 6.0^\circ\text{F}$ and $\widehat{\text{p01i}} == 0.00$ (air is physically too dry for radiation/advection fog and it is not raining), reset $\widehat{\text{vsby}} = 10.0\text{ mi}$.

*(Companion Group D sky-cover and auxiliary spatial layer procedures are specified in [`ADR-00006`](ADR-00006-cloud-fraction-kriging-group-d-sky-cover.md) and [`ADR-00007`](ADR-00007-coastline-and-ercot-weather-zone-layers-.md).)*

## 5. Invariants & Guardrails
- **No Harmonic Drift on Intermittent Fields**: Fourier seasonal/diurnal baselines MUST NOT be subtracted from `p01i` or `vsby`.
- **Zero Empty-Neighborhood `NaN`s & No Storm Teleportation**: Spatial lookup MUST use $K$-nearest active neighbors with soft exponential decay ($\ell_s \approx 45\text{ km}$) and positive dry/clear prior weight ($w_0 > 0$) — NEVER a hard `40–60 km` cutoff (which leaves 42–130 sparse stations empty) and NEVER unregularized $w_0 = 0$ KNN (which teleports single-station storms $100+\text{ km}$).
- **Non-Negative Precipitation & Reset-Aligned Cycles**: Every output station-hour MUST satisfy $\widehat{\text{p01i}} \ge 0.00$ (with raw `< 0` or `> 8.0 in/hr` masked to `NaN` and `0 < p01i < 0.005` mapped to `0.00`), intra-hour reports MUST be collapsed within reset-aligned `(H-1:56, H:56]` cycles without summing or interpolating, and dry-`NaN` zero-fills MUST check neighbor dryness to avoid masking `PNO` frozen/broken gauges.
- **Bounded Visibility & Causal Tie-Breaking**: Every output station-hour MUST satisfy $0.0 \le \widehat{\text{vsby}} \le 10.0$ via $\operatorname{clip}(\exp(\widehat{Y}_{\text{vsby}}) - 0.1,\, 0.0,\, 10.0)$, with exact temporal ties in $\le 45\text{ min}$ brackets resolved causally ($t \le t_0$).
- **Dependency Ordering**: Group C Stage 3 fog reconciliation MUST run after Group A (`tmpf`, `dwpf` in [`ADR-00003`](ADR-00003-spatiotemporal-kriging-group-a-weather--.md)) and `p01i` reconstruction so the dry-air guard ($\widehat{\text{tmpf}} - \widehat{\text{dwpf}} > 6.0^\circ\text{F}$ and $\widehat{\text{p01i}} == 0.00$ when imputed $\widehat{\text{vsby}} < 5.0\text{ mi}$) has complete inputs.
- **Diagnostic Provenance**: Record dry-`NaN` zero-fills, `PNO`-protected wet `NaN`s, trace mappings, `> 8.0 in/hr` precip outliers, capped `> 10.0 mi` visibility counts, and fog-guard resets in `quality_report.json` alongside compact `uint8` provenance codes.

## 6. Consequences & Downstream Impact
- **Positive / Guarantees**: Preserves Group C variables without negative rainfall, storm smearing, double-counted `SPECI` buckets, or `> 10.0 mi` visibility extrapolation; resolves omitted `P0000` dry `NaN`s cleanly using station liveness (`tmpf`/`alti`); prevents coastal/valley fog from bleeding onto dry inland stations during outages.
- **Negative / Trade-offs**: Two-part hurdle interpolation (`I_wet` + `sqrt(p01i)`) and `log(vsby + 0.1)` clamping introduce distinct solver branches alongside the smooth Matérn solvers of Groups A and B; isolated convective storms occurring $> 60\text{ km}$ from any active neighbor during a station outage will default to `0.00` (`p_wet < 0.5`).
