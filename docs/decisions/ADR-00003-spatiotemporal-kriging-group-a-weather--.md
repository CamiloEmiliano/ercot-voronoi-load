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
Adopt a **3-stage Local Spatiotemporal Regression-Kriging (ST-RK)** imputation pipeline for **Group A (`tmpf`, `dwpf`, `relh`, `feel`)** across all 317 ASOS stations onto the canonical 105,192-hour UTC grid, modeling **`tmpf`**, **`dwpf`**, and **`feel_diff = feel - tmpf`**:

1. **Stage 1 — Before Kriging: Algebraic Pre-Fill Only (No Synthetic Guesses)**:
   Leave all true missing gaps as `NaN` so kriging never treats synthetic guesses as real observations. Apply only exact physical identities on raw observations (converting $^\circ\text{F} \leftrightarrow {}^\circ\text{C}$):
   - **Recover `dwpf` when `tmpf` and `relh` both exist** (Magnus-Tetens with $a = 17.625$, $b = 243.04^\circ\text{C}$):
     $$\gamma = \ln\!\left(\frac{\text{relh}}{100}\right) + \frac{a T_C}{b + T_C}, \qquad T_{d,C} = \frac{b \gamma}{a - \gamma}$$
     Convert $T_{d,C}$ back to $^\circ\text{F}$.
   - **Derive `feel_diff = feel - tmpf`** (leaving `NaN` if either is missing). Only optionally fill `feel_diff = 0.0` in the mild regime ($50^\circ\text{F} < \text{tmpf} < 80^\circ\text{F}$) after empirically verifying on non-missing rows that IEM/NWS `feel == tmpf` holds there (note: Jan 2014 `TX` shows small Steadman/humidity deviations up to `2.98°F` near $79.9^\circ\text{F}$, so mild-range `0.0` fill is gated on empirical threshold verification rather than assumed unconditionally).
   - **Exclude `relh` from kriging completely** (`relh` is recomputed deterministically in Stage 3).

2. **Stage 2 — During Kriging: ST-RK on `tmpf`, `dwpf`, and `feel_diff`**:
   - **Step A — Remove Deterministic Drift $m(\mathbf{s}, t)$**:
     - For **`tmpf` and `dwpf`**, capture station-specific diurnal amplitude and regional climate differences (e.g., $25\text{–}30^\circ\text{F}$ West Texas swing vs. $8\text{–}10^\circ\text{F}$ coastal swing plus elevation lapse rates) via a smooth **station harmonic baseline** (annual and $24\text{h}/12\text{h}$ diurnal Fourier harmonics and interactions, ~8–12 basis terms per station):
       $$m_i(t) = \alpha_i + \sum_{\text{annual, diurnal harmonics}} \big[c_{ik}\cos(\omega_k t) + s_{ik}\sin(\omega_k t)\big]$$
       *(Fit directly via OLS whenever a station has sufficient valid history, e.g., $\ge 1{,}000$ observations; for any sparse station, predict harmonic coefficients from `elevation_m`, `dist_coast_km` provided by [`ADR-00007`](ADR-00007-coastline-and-ercot-weather-zone-layers-.md), and `latitude` across neighboring stations.)*
     - Subtract the baseline to obtain synoptic residuals: $\varepsilon(\mathbf{s}_i, t) = Z(\mathbf{s}_i, t) - m_i(t)$.
     - For **`feel_diff`**, omit station harmonic drift removal and apply **Local Ordinary Space-Time Kriging** (estimating the local neighborhood mean) directly.
   - **Step B — Local 3D Space-Time Kriging on $\varepsilon(\mathbf{s}, t)$** at each target `(station_i, HH:00 UTC)` that is missing or off-hour:
     - **Stratified Space-Time Neighborhood**: Select the nearest $m_s \approx 8\text{–}12$ active spatial stations (projected in `EPSG:3083`) and up to $m_t \approx 3\text{–}4$ nearest valid timestamps around $t_0$ from each (plus the target station's own nearby timestamps), preventing a single high-frequency AWOS neighbor from monopolizing all neighborhood slots.
     - **Anisotropic 3D Covariance Kernel** with scaled distance $d = \sqrt{h^2 / \ell_s^2 + u^2 / \ell_t^2}$:
       - Smooth `tmpf` / `dwpf` residuals use **Matérn $\nu = 3/2$**:
         $$C_{\text{st}}(h, u) = \sigma^2 \left(1 + \sqrt{3}\,d\right)\exp\!\left(-\sqrt{3}\,d\right) + \sigma_{\text{nug}}^2 \mathbf{1}_{\{i=j\}}$$
       - Wind-driven `feel_diff` uses **Matérn $\nu = 1/2$ (exponential)**: $C_{\text{st}}(h, u) = \sigma^2 \exp(-d) + \sigma_{\text{nug}}^2 \mathbf{1}_{\{i=j\}}$.
       - Initial Texas synoptic prior scales before empirical variogram fitting: $\ell_s \approx 150\text{–}300\text{ km}$, $\ell_t \approx 8\text{–}18\text{ hr}$.
     - **Predict & Store Uncertainty**: Re-add drift $\widehat{Z}(\mathbf{s}_0, t_0) = m(\mathbf{s}_0, t_0) + \widehat{\varepsilon}(\mathbf{s}_0, t_0)$ and persist kriging variance $\sigma^2_{\text{krig}}(\mathbf{s}_0, t_0)$ alongside provenance flags.

   - **Step C — Numerical Stability, Execution & Storage Architecture** (resolving the ~`100M`-point scale across 317 stations $\times$ 105,192 hours $\times$ 3 variables):
     - **Observation Deduplication & Diagonal Measurement Nugget**: Exact `(station_id, timestamp)` collisions MUST be deduplicated/averaged prior to neighborhood assembly, and a strictly positive measurement nugget $\sigma_{\text{nug}}^2 > 0$ MUST be added to every diagonal entry $C_{ii}$ ($i = j$). This prevents matrix singular/ill-conditioned failures when `SPECI` reports sit minutes apart ($u \ll \ell_t$) or when distinct station IDs share coincident `(lon, lat)` coordinates ($h = 0$).
     - **Unified SPD Cholesky Solver (Simple & Ordinary Kriging)**: Zero-mean Simple Kriging (`tmpf`, `dwpf`) solves $C \mathbf{w} = \mathbf{c}_0$ directly via symmetric positive-definite (SPD) Cholesky factorization $C = L L^{\mathsf T}$. Unbiased Ordinary Kriging (`feel_diff`) MUST be solved via the **Schur complement** on the same SPD Cholesky factor ($\mathbf{u} = C^{-1}\mathbf{c}_0$, $\mathbf{v} = C^{-1}\mathbf{1}$, $\hat{\mu} = (\mathbf{1}^{\mathsf T}\mathbf{v})^{-1}(\mathbf{1}^{\mathsf T}\mathbf{y})$) rather than assembling an indefinite $(K+1) \times (K+1)$ Lagrange KKT matrix, with graceful fallback when neighborhoods contain $< 2$ valid observations.
     - **Throughput Acceleration (Two-Tier Fast Path & Batched/Numba Solves)**: Where a target station has valid local observations closely bracketing `HH:00 UTC` on both sides (e.g., within $\pm 40\text{ min}$, $u_{\text{prev}} + u_{\text{next}} \le 65\text{ min}$), a vectorized 1D local temporal kriging / PCHIP fast path may evaluate the on-hour value directly, reserving full 3D space-time neighborhood solves (accelerated via `np.searchsorted` indexing + `numba` / batched `np.linalg.solve`) for actual intra-station gaps and outages.
     - **Two-Pass Memory Architecture (`±24h` Rolling Annual Windows)**: Replace isolated 100k-row CSV SQLite chunking with (1) a one-time raw-to-clean typed Parquet staging pass across all 720 CSVs, followed by (2) rolling 1-year execution windows across all 5 states simultaneously with a **`±24-hour` overlap buffer** from adjacent years (trimmed before writing), bounding peak RAM to a few hundred MB while preserving exact cross-month and cross-year continuity.
     - **Compact Provenance & Variance Dtypes**: Store kriging variances $\sigma^2_{\text{krig}}$ as `float32` and observation provenance as `uint8` enum codes (`0 = exact_on_hour`, `1 = algebraic_prefill`, `2 = local_temporal`, `3 = spatiotemporal_krig`) so `weather_station_hourly` Parquet artifacts do not bloat disk or downstream I/O.

3. **Stage 3 — After Kriging: Physical Reconciliation**:
   Once `tmpf`, `dwpf`, and `feel_diff` are filled across all stations and hours:
   - **Enforce saturation**: $\widehat{\text{dwpf}} \leftarrow \min(\widehat{\text{dwpf}},\, \widehat{\text{tmpf}})$.
   - **Recompute `relh` deterministically** ($a = 17.625$, $b = 243.04^\circ\text{C}$):
     $$\widehat{\text{relh}} = 100 \cdot \exp\!\left(\frac{a \widehat{T}_{d,C}}{b + \widehat{T}_{d,C}} - \frac{a \widehat{T}_C}{b + \widehat{T}_C}\right)$$
   - **Reconstruct `feel`**: $\widehat{\text{feel}} = \widehat{\text{tmpf}} + \widehat{\text{feel\_diff}}$.
   - *(Downstream modeling note: avoid passing collinear raw `tmpf`/`dwpf`/`relh`/`feel` directly; prefer `tmpf`, dewpoint depression `dew_depr = tmpf - dwpf`, and `feel_diff`.)*

*(Companion spatial layers and Group B, C, and D weather procedures are specified in [`ADR-00007`](ADR-00007-coastline-and-ercot-weather-zone-layers-.md) and [`ADR-00004`](ADR-00004-spatiotemporal-kriging-group-b-pressure-.md)–[`ADR-00006`](ADR-00006-cloud-fraction-kriging-group-d-sky-cover.md).)*

## 5. Invariants & Guardrails
- **No Synthetic Observations Before Kriging**: Only exact physical identities (`dwpf` from `tmpf`+`relh` via Magnus-Tetens, verified `feel_diff`) may pre-fill values prior to kriging; unverified or model-guessed values MUST remain `NaN`.
- **No Independent `relh` Kriging**: `relh` MUST NEVER be kriged independently of `tmpf` and `dwpf`; it MUST be derived post-reconciliation from $(\widehat{\text{tmpf}}, \widehat{\text{dwpf}})$ using $a = 17.625$, $b = 243.04^\circ\text{C}$.
- **Strict Positive-Definiteness & Schur-Complement OK**: Observation covariance matrices MUST apply $\sigma_{\text{nug}}^2 > 0$ on every diagonal entry $C_{ii}$ after `(station_id, timestamp)` deduplication, and Ordinary Kriging (`feel_diff`) MUST solve via SPD Cholesky + Schur complement rather than indefinite KKT systems.
- **Stratified Neighborhood & Exact Timestamps**: Local space-time neighborhoods MUST stratify across stations ($m_s \approx 8\text{–}12$ stations $\times$ $m_t \approx 3\text{–}4$ timestamps) using exact observation timestamps and projected `EPSG:3083` coordinates.
- **Saturation Invariant**: Every output station-hour MUST satisfy $\widehat{\text{dwpf}} \le \widehat{\text{tmpf}}$ and $0.0 \le \widehat{\text{relh}} \le 100.0$.
- **Canonical Grid & Cross-Boundary Continuity**: Resampling MUST process all 5 states simultaneously across month/year boundaries (e.g., via `±24h`-buffered annual windows) and output the exact 105,192 UTC hours matching `load_hourly`.
- **Raw Provenance Separation & Compact Uncertainty Dtypes**: Raw ASOS archives in `data/raw/weather_*_asos/` MUST remain unmodified; kriged/reconstructed station-hours MUST record compact `uint8` provenance codes and `float32` $\sigma^2_{\text{krig}}(\mathbf{s}, t)$.
- **Library Placement**: Reusable thermodynamic conversions, harmonic drift models, and ST-RK solvers MUST live in `src/bayes_demand_fcst/` per [`ADR-00002`](ADR-00002-restructure-scripts-into-src-and-domains.md).

## 6. Consequences & Downstream Impact
- **Positive / Guarantees**: Eliminates the ~99.5% timestamp-join data loss in [`scripts/panel/assemble_observed_panel.py`](../../scripts/panel/assemble_observed_panel.py); preserves exact Magnus-Tetens thermodynamics across `tmpf`, `dwpf`, `relh`, and `feel`; captures sharp West Texas vs. coastal diurnal amplitude differences via station harmonic baselines; prevents AWOS cadence crowding via stratified space-time neighborhoods; yields $\sigma^2_{\text{krig}}$ as an explicit uncertainty diagnostic.
- **Negative / Trade-offs**: Increases compute complexity relative to simple chunked CSV filtering (requiring multi-year station harmonic fitting, empirical variogram calibration, and local kriging solves across 317 stations $\times$ 105,192 hours); commits downstream GAMs to use orthogonalized thermal features (`tmpf`, `dew_depr`, `feel_diff`) where appropriate; remains tied to completion of companion ADRs [`ADR-00004`](ADR-00004-spatiotemporal-kriging-group-b-pressure-.md)–[`ADR-00006`](ADR-00006-cloud-fraction-kriging-group-d-sky-cover.md).
