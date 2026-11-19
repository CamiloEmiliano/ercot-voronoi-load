# ADR-00004: Primary/Auxiliary Spatiotemporal Kriging for Group B Pressure Variables (`alti`, `mslp`)

- **ADR ID**: ADR-00004
- **Filename**: `ADR-00004-spatiotemporal-kriging-group-b-pressure-.md`
- **Date**: 2026-11-17 23:35 UTC
- **Status**: PROPOSED
- **Target Subsystem**: ASOS barometric pressure alignment and reconstruction (`src/bayes_demand_fcst/`, `scripts/weather/`, `tests/`, `dvc.yaml`)
- **Linked Commits**: Pending acceptance

---

## 1. Context & Problem Statement
Raw ASOS archives carry two sea-level-reduced pressure variables — `alti` (altimeter setting, inHg) and `mslp` (mean sea-level pressure, hPa/mb) — with sharply asymmetric station coverage:
- **`alti`** is reported at **99.8%+** of observations across both full ASOS and rural AWOS stations because aviation mandates altimeter reporting.
- **`mslp`** is only computed and transmitted by full ASOS sites (~20% of TX raw rows overall; **0%** at AWOS stations such as `0F2`).
- Physically, both measure the same surface pressure reduced to sea level under different atmospheric column temperature assumptions: `alti` uses the US Standard Atmosphere ($59^\circ\text{F} / 15^\circ\text{C}$), whereas `mslp` uses the station's actual 12-hour mean temperature. At coastal stations (`HOU`, `CRP`), $\Delta P = \text{mslp} - 33.86389\,\text{alti}$ stays near $0\text{ hPa}$ ($\sigma \approx 0.11\text{ hPa}$); at high-elevation stations (`ELP` at 1,194 m, `AMA` at 1,099 m), thermal density and plateau reduction differences reach $1\text{–}4+\text{ hPa}$.

Running standalone univariate kriging on `mslp` would discard barometer measurements from all rural AWOS sites, fail to learn station-specific climatologies at AWOS locations, and leave `mslp_regional` biased toward urban ASOS airports.

## 2. Decision Drivers & Constraints
- **Use All Barometers**: Rural AWOS `alti` observations MUST inform reconstructed `mslp` rather than being discarded by univariate `mslp` kriging.
- **Physical Consistency**: `mslp` and `alti` MUST share the same underlying synoptic pressure field ($P_{\text{alti}} = 33.86389 \times \text{alti}$) while accounting for the elevation- and temperature-dependent reduction offset $\Delta P$.
- **Dependency Ordering**: Group B reconstruction depends on the completed Group A `tmpf` cube from [`ADR-00003`](ADR-00003-spatiotemporal-kriging-group-a-weather--.md) (specifically $\text{tmpf}_{12\text{h}}$) and `elevation_m` from station metadata.
- **Canonical UTC Alignment**: Output station-hour `alti` and `mslp` must align to the same 105,192 contiguous UTC hour-ending grid (`2014-01-01T07:00:00+00:00` through `2026-01-01T06:00:00+00:00`) as [`ADR-00001`](ADR-00001-adopt-ercot-native-load-hourly-target---.md) and [`ADR-00003`](ADR-00003-spatiotemporal-kriging-group-a-weather--.md).
- **Downstream GAM Collinearity Guardrail**: Because reconstructed `mslp_regional` and `alti_regional` are $>0.99$ collinear, downstream GAM specifications must treat them as redundant indicators (selecting one, or using pressure tendency $\Delta P / \Delta t$).

## 3. Considered Alternatives & Rejection Rationale
- **Alternative A: Univariate ST-RK on `mslp` independently of `alti`**. Rejected because it ignores ~80% of Texas stations (all AWOS sites) that actively measure pressure via `alti`, producing coarse coverage and urban airport bias.
- **Alternative B: Direct linear unit conversion $\text{mslp} = 33.86389 \times \text{alti}$ everywhere**. Rejected because it ignores the $1\text{–}4\text{ hPa}$ elevation/temperature column discrepancy ($\Delta P$) in West Texas and the Panhandle (`ELP`, `AMA`), injecting systematic spatial bias during winter cold snaps and summer heat waves.
- **Alternative C: Drop `mslp` from the regional panel schema**. Rejected because preserving the full 8-variable regional weather contract in [`scripts/weather/aggregate_regional_weather.py`](../../scripts/weather/aggregate_regional_weather.py) keeps downstream artifacts uniform while enabling optional pressure-tendency diagnostics.

## 4. Decision Outcome
Adopt a **3-stage Primary/Auxiliary Space-Time Regression-Kriging (ST-RK)** imputation pipeline for **Group B (`alti`, `mslp`)** across all 317 ASOS/AWOS stations onto the canonical 105,192-hour UTC grid, modeling **`alti`** and **`delta_p = mslp - P_alti`**:

1. **Stage 1 — Before Kriging: QC & Offset Transformation**:
   Leave all missing values as `NaN` and prepare the two target series (`alti` and `delta_p`), logging filtered outlier counts to `quality_report.json`:
   - **Sanity Bounds & METAR Decoder Bug Filter**:
     - Set `alti` outside $[28.50,\, 31.20]\text{ inHg}$ and `mslp` outside $[965.0,\, 1055.0]\text{ hPa}$ to `NaN`.
     - Convert `alti` to hPa: $P_{\text{alti}} = 33.86389 \times \text{alti}$.
     - **Filter METAR `SLP` 100-hPa wrap-around typos**: Wherever both `mslp` and `alti` exist, if $|\text{mslp} - P_{\text{alti}}| > 12.0\text{ hPa}$, set `mslp` to `NaN`.
   - **Create `delta_p = mslp - P_alti`**:
     - Compute $\Delta P = \text{mslp} - 33.86389 \times \text{alti}$ on all ASOS rows where both QC-passed values exist (leaving `NaN` on AWOS stations and missing rows).
     - *(Optional rare sea-level recovery)*: If `alti` is `NaN` but QC-passed `mslp` is valid at a sea-level station ($\text{elevation\_m} < 50\text{ m}$), pre-fill $\text{alti} = \text{mslp} / 33.86389$.

2. **Stage 2 — During Kriging: ST-RK on `alti` and `delta_p`**:
   - **Step A — Remove Deterministic Drift $m(\mathbf{s}, t)$**:
     Because `alti` is observed across all stations while `delta_p` is observed only at full ASOS stations, apply two tailored drift models:
     - **For `alti` (Station Harmonic Baseline with Semidiurnal Tide)**: Fit a smooth seasonal + diurnal Fourier curve $m_{\text{alti}, i}(t)$ per station — explicitly including the **12-hour semidiurnal harmonic ($\omega_{12\text{h}}$)** to capture the twice-daily atmospheric barometric tide — and subtract it:
       $$\varepsilon_{\text{alti}}(\mathbf{s}_i, t) = \text{alti}(\mathbf{s}_i, t) - m_{\text{alti}, i}(t)$$
     - **For `delta_p` (Global Physical Elevation–Temperature Regression)**: Because AWOS stations have zero `mslp` history for station-specific harmonics, fit a global physical drift across valid ASOS stations using `elevation_m` and rolling 12-hour temperature $\text{tmpf}_{12\text{h}}$ completed in Group A ([`ADR-00003`](ADR-00003-spatiotemporal-kriging-group-a-weather--.md)):
       $$m_{\Delta P}(\mathbf{s}_i, t) = \beta_0 + \beta_1\,\text{elev}_i + \beta_2\,\text{elev}_i\cdot\big(\text{tmpf}_{12\text{h}}(\mathbf{s}_i, t) - 59.0\big)$$
       *(Compute $\text{tmpf}_{12\text{h}}$ once on the regular hourly Group A `tmpf` cube — seeded with the `2014-01-01 00:00Z–06:00Z` raw warm-up window — and index raw sub-hourly ASOS training timestamps via `round("h")` rather than building irregular-time rolling windows.)*
       Subtract to obtain the sea-level reduction residual:
       $$\varepsilon_{\Delta P}(\mathbf{s}_i, t) = \Delta P(\mathbf{s}_i, t) - m_{\Delta P}(\mathbf{s}_i, t)$$
   - **Step B — Local 3D Space-Time Kriging on $\varepsilon_{\text{alti}}$ and $\varepsilon_{\Delta P}$** at each target `(station_i, HH:00 UTC)`:
     - **Stratified Space-Time Neighborhood**: Select the nearest $m_s \approx 8\text{–}12$ active stations in `EPSG:3083` (from all ASOS+AWOS stations for `alti`; from active full ASOS stations for `delta_p`) and up to $m_t \approx 3\text{–}4$ nearest valid timestamps around $t_0$ from each.
     - **Anisotropic 3D Matérn $\nu = 3/2$ Covariance Kernel** (used for both smooth fields):
       $$C_{\text{st}}(h, u) = \sigma^2 \left(1 + \sqrt{3}\,d\right)\exp\!\left(-\sqrt{3}\,d\right) + \sigma_{\text{nug}}^2 \mathbf{1}_{\{i=j\}}, \qquad d = \sqrt{\frac{h^2}{\ell_s^2} + \frac{u^2}{\ell_t^2}}$$
       *(Initial Texas synoptic pressure scales prior to empirical variogram fitting: $\ell_s \approx 300\text{–}500\text{ km}$, $\ell_t \approx 12\text{–}24\text{ hr}$; shares the SPD Cholesky solver, `±24h`-buffered annual windows, and compact `float32` / `uint8` dtypes from [`ADR-00003`](ADR-00003-spatiotemporal-kriging-group-a-weather--.md).)*
     - **Predict & Store Variance**: Add the drifts back to obtain $\widehat{\text{alti}}(\mathbf{s}_0, t_0)$ and $\widehat{\Delta P}(\mathbf{s}_0, t_0)$, storing $\sigma^2_{\text{krig, alti}}$ (strictly in $\text{inHg}^2$) and $\sigma^2_{\text{krig, }\Delta P}$ (strictly in $\text{hPa}^2$).
   - **Step C — Group B Numerical & Execution Refinements**:
     - **Two-Phase `delta_p` Alignment + 2D Spatial Broadcast**: Because AWOS stations (~60% of sites) have zero local `delta_p` history, avoid running ~20 million separate 3D space-time solves at AWOS targets. Instead, (1) align the ~130 five-state full-ASOS stations' $\varepsilon_{\Delta P}$ onto `HH:00 UTC` first, then (2) evaluate AWOS $\widehat{\varepsilon}_{\Delta P}(\mathbf{s}_0, t_0)$ per hour via a fast 2D spatial kriging solve / matrix multiply from active ASOS stations at $t_0$.
     - **Station-Table Nugget Floor on $\varepsilon_{\Delta P}$**: Enforce a realistic diagonal nugget ($\sigma_{\text{nug}} \approx 0.25\text{–}0.40\text{ hPa}$, i.e., $\sigma_{\text{nug}}^2 \approx 0.06\text{–}0.16\text{ hPa}^2$) on $\varepsilon_{\Delta P}$ so the broad $300\text{–}500\text{ km}$ Matérn surface smooths across static NWS station-specific barometer reduction table offsets (e.g., `HOU` $+0.44\text{ hPa}$ vs. adjacent `LVJ` $-0.15\text{ hPa}$ at sea level) without negative-weight overshoot.

3. **Stage 3 — After Kriging: Physical Reconciliation**:
   Once `alti` and `delta_p` are complete across all 317 stations and 105,192 hours:
   - **Reconstruct `mslp` Smoothly Across All Stations (Including AWOS)**:
     $$\widehat{\text{mslp}}(\mathbf{s}_0, t_0) = 33.86389 \times \widehat{\text{alti}}(\mathbf{s}_0, t_0) + \widehat{\Delta P}(\mathbf{s}_0, t_0)$$
     *(For full ASOS stations, evaluate `mslp` via the same smooth local 1D temporal / reconstructed curve across `:53`/`:00` rather than splicing isolated raw `:00` observations — only ~0.1% of rows — into a nugget-smoothed series, preventing single-hour $\pm 0.4\text{ hPa}$ jumps in 3-hour pressure tendencies.)*
   - **Combine Kriging Variance for `mslp` (with Explicit Unit Conversion)**:
     $$\sigma^2_{\text{krig, mslp}}(\mathbf{s}_0, t_0) \approx (33.86389)^2\,\sigma^2_{\text{krig, alti}}(\mathbf{s}_0, t_0) + \sigma^2_{\text{krig, }\Delta P}(\mathbf{s}_0, t_0)$$
     where $\sigma^2_{\text{krig, alti}}$ MUST be in $\text{inHg}^2$ and $\sigma^2_{\text{krig, }\Delta P}$ MUST be in $\text{hPa}^2$ prior to scaling by $33.86389^2 \approx 1146.76$.
   - **Regional/Zone-Level `mslp_tendency_3h` & Near-Orthogonal GAM Basis**: Because fixed-weight Voronoi spatial aggregation is a linear operator ($\sum_i w_i (P_{i,t} - P_{i,t-3}) = \bar{P}_t - \bar{P}_{t-3}$), compute 3-hour barometric tendency **`mslp_tendency_3h`** ($\widehat{\text{mslp}}_t - \widehat{\text{mslp}}_{t-3}$) at the aggregated regional/weather-zone panel layer (`105,192` rows) rather than bloating the `33.35M`-row station Parquet schema; pass **`mslp`** + optional **`mslp_tendency_3h`** (dropping $>0.99$-collinear `alti`) to downstream `sklearn` / GAM models.

*(Companion Group C, Group D, and spatial layer procedures are specified in [`ADR-00005`](ADR-00005-bounded-interpolation-group-c-precip-vis.md), [`ADR-00006`](ADR-00006-cloud-fraction-kriging-group-d-sky-cover.md), and [`ADR-00007`](ADR-00007-coastline-and-ercot-weather-zone-layers-.md).)*

## 5. Invariants & Guardrails
- **Primary/Auxiliary & Cross-Group Ordering**: `alti` MUST be completed across all stations and Group A `tmpf` ([`ADR-00003`](ADR-00003-spatiotemporal-kriging-group-a-weather--.md)) MUST be completed before evaluating $m_{\Delta P}(\mathbf{s}, t)$ on the target hourly grid and reconstructing `mslp`.
- **METAR `SLP` Wrap-Around & Bounds QC**: `alti` outside $[28.50,\, 31.20]\text{ inHg}$, `mslp` outside $[965.0,\, 1055.0]\text{ hPa}$, and any `mslp` with $|\text{mslp} - 33.86389\,\text{alti}| > 12.0\text{ hPa}$ MUST be masked to `NaN` before fitting drifts or kriging.
- **Semidiurnal Tide & Station-Table Nugget**: $m_{\text{alti}, i}(t)$ MUST include the 12-hour semidiurnal frequency ($\omega_{12\text{h}}$), and $\varepsilon_{\Delta P}$ MUST carry a non-zero station-table nugget floor ($\sigma_{\text{nug}} \approx 0.25\text{–}0.40\text{ hPa}$) to prevent overfitting static NWS reduction-table offsets between adjacent ASOS sites.
- **No Isolated `:00` Splice Artifacts**: `mslp` time series MUST maintain smooth local temporal continuity across `:53` and `:00` so `mslp_tendency_3h` is free of artificial single-hour splice spikes.
- **No Standalone Univariate `mslp` Kriging**: Missing `mslp` values MUST NOT be kriged directly without conditioning on $P_{\text{alti}} = 33.86389 \times \widehat{\text{alti}}$.
- **Explicit Variance Units & Panel-Layer Tendency**: Variance propagation MUST assert $\text{inHg}^2 \to \text{hPa}^2$ scaling ($33.86389^2$), `mslp_tendency_3h` MUST be derived at the aggregated regional/zone panel layer, and downstream GAMs MUST NOT pair `mslp` and `alti` together as unregularized collinear predictors.

## 6. Consequences & Downstream Impact
- **Positive / Guarantees**: Eliminates the ~80% structural `mslp` blind spot across rural Texas AWOS stations; catches 100-hPa METAR `SLP` decoding wrap-around bugs automatically via $|\text{mslp} - P_{\text{alti}}| > 12.0\text{ hPa}$; accounts for the twice-daily atmospheric pressure tide in `alti` and NWS thermal/elevation column physics in $\Delta P$; provides propagated analytical kriging variance $\sigma^2_{\text{krig, mslp}}$.
- **Negative / Trade-offs**: Requires Group A `tmpf` (`tmpf_12h`) to be materialized before `delta_p` drift evaluation on target hours; `delta_p` neighborhoods draw from the sparser ~65-station full-ASOS subset across the 5 states (well-supported by the broad $300\text{–}500\text{ km}$ synoptic correlation length).
