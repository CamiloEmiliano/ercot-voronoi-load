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
Adopt a **3-step Primary/Auxiliary Space-Time Regression-Kriging (ST-RK)** pipeline for **Group B (`alti`, `mslp`)** across all 317 ASOS/AWOS stations:

1. **Step 1 — Complete `alti` via ST-RK**:
   - Sanity-filter gross `alti` outliers prior to fitting (retaining physically valid synoptic bounds around $28.5\text{–}31.0\text{ inHg}$, logging any filtered anomalies to `quality_report.json`).
   - Run Local 3D Space-Time Regression-Kriging on `alti` across all ASOS and AWOS stations (`EPSG:3083` $(x, y)$ + exact $\Delta t$, exploiting broad synoptic pressure correlation scales) to produce the complete 105,192-hour $\widehat{\text{alti}}(\mathbf{s}, t)$ cube.
2. **Step 2 — Model the Thermal/Elevation Discrepancy $\Delta P = \text{mslp} - 33.86389 \cdot \text{alti}$**:
   - On ASOS observations where both `mslp` and `alti` are present, compute $\Delta P(\mathbf{s}, t) = \text{mslp}(\mathbf{s}, t) - 33.86389 \cdot \text{alti}(\mathbf{s}, t)$.
   - Fit the deterministic thermal/elevation reduction model using `elevation_m` and the completed Group A temperature (`tmpf` / rolling 12-hour mean $\text{tmpf}_{12\text{h}}$ per NWS reduction rules):
     $$m_{\Delta P}(\mathbf{s}, t) = \beta_0 + \beta_1\,\text{elev}(\mathbf{s}) + \beta_2\,\text{elev}(\mathbf{s})\cdot(\text{tmpf}_{12\text{h}}(\mathbf{s}, t) - 59)$$
   - Run Local ST-RK on the residual $\varepsilon_{\Delta P}(\mathbf{s}, t) = \Delta P(\mathbf{s}, t) - m_{\Delta P}(\mathbf{s}, t)$ from nearby full ASOS stations to capture remaining plateau/station-reduction effects.
3. **Step 3 — Reconstruct `mslp` Across All Stations**:
   - Reconstruct every missing `mslp` value (including all AWOS stations) as:
     $$\widehat{\text{mslp}}(\mathbf{s}, t) = 33.86389 \times \widehat{\text{alti}}(\mathbf{s}, t) + \widehat{m}_{\Delta P}(\mathbf{s}, t) + \widehat{\varepsilon}_{\Delta P}(\mathbf{s}, t)$$
   - Preserve exact observed `mslp` values where direct station observations exist on the hour, and track observed vs. reconstructed provenance and kriging variances in `quality_report.json` / diagnostic columns.

*(Remaining raw ASOS fields — `p01i`, `vsby`, `skyc1`, `skyl1` — will be specified in a companion ADR before full weather pipeline implementation.)*

## 5. Invariants & Guardrails
- **Primary/Auxiliary Ordering**: `alti` MUST be completed first across all stations, and Group A `tmpf` ([`ADR-00003`](ADR-00003-spatiotemporal-kriging-group-a-weather--.md)) MUST be completed before estimating $m_{\Delta P}(\mathbf{s}, t)$ and reconstructing `mslp`.
- **No Standalone Univariate `mslp` Kriging**: Missing `mslp` values MUST NOT be kriged directly without conditioning on $P_{\text{alti}} = 33.86389 \times \widehat{\text{alti}}$.
- **Physical Range Sanity**: Gross sensor outliers outside physical ERCOT synoptic pressure bounds MUST be filtered before kriging and logged rather than propagated into neighboring Voronoi cells.
- **No Unchecked Collinear Feature Leakage in GAMs**: Downstream modeling code in `src/bayes_demand_fcst/` MUST NOT treat `alti_regional` and `mslp_regional` as independent covariates in unregularized additive terms without explicit selection or pressure-tendency transformation.

## 6. Consequences & Downstream Impact
- **Positive / Guarantees**: Eliminates the ~80% structural `mslp` blind spot across rural Texas AWOS stations; respects NWS thermal/elevation sea-level reduction physics across coastal (`HOU`, `CRP`) and high-elevation (`ELP`, `AMA`) stations; yields a complete, physically consistent 105,192-hour pressure cube for both `alti` and `mslp`.
- **Negative / Trade-offs**: Creates a pipeline execution dependency on Group A `tmpf` before Group B `mslp` reconstruction can run; leaves `mslp_regional` and `alti_regional` $>0.99$ correlated, requiring careful feature selection in future GAM specifications.
