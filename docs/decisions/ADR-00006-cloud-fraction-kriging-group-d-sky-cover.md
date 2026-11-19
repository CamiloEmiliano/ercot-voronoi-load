# ADR-00006: Cloud Fraction Encoding and Kriging for Group D Sky Cover Variables (`skyc1`, `skyl1`)

- **ADR ID**: ADR-00006
- **Filename**: `ADR-00006-cloud-fraction-kriging-group-d-sky-cover.md`
- **Date**: 2026-11-18 02:50 UTC
- **Status**: PROPOSED
- **Target Subsystem**: ASOS sky-cover encoding (`skyc1`, `cloud_frac`), ceiling handling (`skyl1`), and regional weather schema (`src/bayes_demand_fcst/`, `scripts/weather/`, `tests/`, `dvc.yaml`)
- **Linked Commits**: Pending acceptance

---

## 1. Context & Problem Statement
[`scripts/weather/aggregate_regional_weather.py`](../../scripts/weather/aggregate_regional_weather.py) currently drops `skyc1` and `skyl1` because they are string/partially missing fields. However, from 2018 through 2025, behind-the-meter rooftop solar and grid-scale PV penetration in ERCOT make afternoon net load sensitive to overcast vs. clear-sky irradiance at identical air temperatures (`tmpf`).

Empirical inspection of raw `TX` ASOS observations (`tx_asos_land_2014_01.csv`) highlights three key properties of `skyc1` and `skyl1`:
1. **`skyc1` encodes standard aviation okta categories** (including trailing whitespace such as `'VV '`), with `CLR` (184,658), `OVC` (33,994), `BKN` (14,967), `SCT` (14,607), `FEW` (8,394), `'VV '` (1,840), and `<NA>` (4,254).
2. **`skyl1` is structurally missing on clear skies**: 0 rows have numeric `skyl1` when `skyc1 == "CLR"`. Unconditional kriging on `skyl1` would fabricate cloud ceilings on cloudless days.
3. **12,000-ft automated ceilometer cap bias**: Rural automated ASOS/AWOS laser ceilometers detect clouds only up to `12,000` ft (`~3,660` m) and report `CLR` under higher decks, whereas major staffed airports (`LBB`, `ELP`, `DFW`, `SAT`, `DAL`, etc.) manually augment `BKN`/`OVC` layers up to `35,000` ft (`4,287` rows `> 12,000` ft in Jan 2014 TX alone).

## 2. Decision Drivers & Constraints
- **Solar / Net-Load Signal**: Retain cloud fraction in the regional hourly weather schema (`cloud_frac_regional`) so downstream Bayesian GAMs can distinguish clear vs. overcast afternoons.
- **No Ceilometer Urban/Rural Bias**: Prevent staffed-airport manual high-cloud augmentation (`skyl1 > 12,000` ft) from creating artificial clear-rural vs. cloudy-urban spatial artifacts in `cloud_frac`.
- **No Fabricated Clear-Sky Ceilings**: `skyl1` MUST NEVER be unconditionally interpolated across clear (`CLR`) hours.
- **Canonical Grid & Boundedness**: Output station-hour `cloud_frac` aligns to the canonical 105,192 UTC hour-ending grid (`2014-01-01T07:00:00+00:00`–`2026-01-01T06:00:00+00:00`) using `EPSG:3083` distances and stays strictly bounded in $[0.0,\, 1.0]$.

## 3. Considered Alternatives & Rejection Rationale
- **Alternative A: Keep `skyc1` and `skyl1` dropped from regional aggregation**. Rejected because it discards an observed cloud-cover proxy (`skyc1`) directly relevant to 2018–2025 solar/net-load afternoon demand profiles.
- **Alternative B: Two-pass imputation that also kriges `skyl1` (`log(skyl1 + 50)`) and round-trips continuous `cloud_frac` back into categorical `skyc1` strings before dropping both at Stage 3**. Rejected because `skyl1` is structurally undefined on clear skies (~70%+ of Texas hours), adds negligible regional demand signal beyond `cloud_frac`, `tmpf`, and `dwpf`, hits empty-neighbor (`N_cloudy == 0`) edge cases on isolated `FEW` clouds, and wastes ~50% of Group D compute and 2 Parquet columns on artifacts immediately discarded at regional aggregation. `skyl1`'s sole physical role here is gating raw observations at the `12,000-ft` sensor horizon.
- **Alternative C: Retain or merely halve manual high-cloud layers (`skyl1 > 12,000` ft) instead of reclassifying to `CLR`**. Rejected because rural automated ceilometers report `0.00` (`CLR`) under the exact same high cirrus sheet, so retaining or halving staffed-airport manual layers (`20,000–35,000` ft) still injects `+0.22` to `+0.50` urban cloud bullseyes across surrounding rural cells.

## 4. Decision Outcome
Restructure **Group D (`skyc1`, `skyl1`)** into a streamlined, **single-variable `cloud_frac` pipeline** where `skyl1` serves strictly as a raw sensor-horizon validation gate before being dropped:

1. **Step 1 — Joint Raw Validation, Ceilometer Horizon Gate & Okta Encoding**:
   *(Ordered strictly so sensor decoder glitches never corrupt valid `skyc1` reports:)*
   - **Strip & Parse**: Normalize `skyc1` via `.str.strip()` (handling trailing whitespace such as `'VV '`) and parse numeric `skyl1`.
   - **Mask Corrupt `skyl1` Heights First**: Set numeric `skyl1 < 0` or `skyl1 > 35,000 ft` (e.g., `95,000 ft` decoder glitches observed in `tx_asos_land_2023_07.csv`) to `NaN` *before* applying the ceilometer horizon rule, and clear any spurious non-null `skyl1` on raw clear (`CLR`/`SKC`/`NCD`/`NSC`) rows.
   - **Enforce Uniform `12,000-ft` Ceilometer Horizon**: Where valid `skyl1` satisfies `12,000 < skyl1 <= 35,000 ft` (`~3,660 m`), reclassify `skyc1` deterministically to `'CLR'` so staffed airports and automated rural ASOS/AWOS stations share the identical `[0, 12,000 ft]` cloud-detection column (logging reclassified row counts to `quality_report.json`).
   - **Encode `skyc1` to Numeric `cloud_frac` $\in [0.0,\, 1.0]$ & Drop Raw Sky Columns**:
     - `CLR`, `SKC`, `NCD`, `NSC` $\to \mathbf{0.00}$
     - `FEW` $\to \mathbf{0.1875}$
     - `SCT` $\to \mathbf{0.4375}$
     - `BKN` $\to \mathbf{0.75}$
     - `OVC`, `VV` $\to \mathbf{1.00}$
     - Missing (`M` / blank / unrecognized) $\to \text{NaN}$
     - Drop string `skyc1` and height `skyl1` immediately after encoding (do not carry them into the `33.35M`-row hourly station cube).

2. **Step 2 — Local Temporal Alignment onto `HH:00 UTC` & Hourly Provenance**:
   - **Short bracket ($\le 45\text{ min}$ at the same station)**: Evaluate time-linear interpolation when bracketed on both sides within $\le 45\text{ min}$ (smoothing single-cloud laser-beam step jitter across `:53`/`:55`), falling back to causal nearest valid observation ($t \le t_0$) when only one side is present within $\le 45\text{ min}$.
   - **Hourly Provenance (`uint8`)**: Record provenance directly on the target `HH:00 UTC` grid (`0 = exact_on_hour`, `2 = local_temporal`, `3 = spatiotemporal_krig`), avoiding raw-to-hourly index mismatches.

3. **Step 3 — Longer-Outage Spatial Imputation, Clamping & Precipitation Consistency**:
   - **Uniform-Sky Vectorized Short-Circuit**: On remaining `NaN` station-hours, if all active neighbors in the local space-time window equal `0.00` (all clear, ~70% of Texas hours) or all equal `1.00` (solid overcast), assign `0.00` or `1.00` directly in $O(1)$ without invoking a matrix solve.
   - **Local Ordinary Space-Time Kriging / Kernel Interpolation**: On mixed-sky outages, run local Ordinary Space-Time Kriging (Exponential kernel $\nu = 1/2$, scales $\ell_s \approx 80\text{–}150\text{ km}$, $\ell_t \approx 3\text{–}6\text{ hr}$, solved via SPD Cholesky + Schur complement with okta-quantization diagonal nugget $\sigma_{\text{nug}}^2 \approx 0.01$, or Space-Time IDW fallback) on `cloud_frac` (without Fourier harmonic drift subtraction).
   - **Bounds Clamp & Precipitation Cloud Floor**:
     1. Clamp strictly to $[0.0,\, 1.0]$: $\widehat{\text{cloud\_frac}} \leftarrow \operatorname{clip}(\widehat{\text{cloud\_frac}},\, 0.0,\, 1.0)$.
     2. Enforce physical rain-cloud consistency where imputed: if completed `p01i` ([`ADR-00005`](ADR-00005-bounded-interpolation-group-c-precip-vis.md)) indicates active measurable rainfall ($\widehat{\text{p01i}} \ge 0.05\text{ in/hr}$), enforce $\widehat{\text{cloud\_frac}} \leftarrow \max(\widehat{\text{cloud\_frac}},\, 0.75)$ (`BKN`/`OVC`).
   - **Regional & Weather-Zone Aggregation**: Aggregate `cloud_frac` across Voronoi weights $W$ (statewide and per weather zone under [`ADR-00007`](ADR-00007-coastline-and-ercot-weather-zone-layers-.md)) into `cloud_frac_regional` and `cloud_frac_coverage`.

## 5. Invariants & Guardrails
- **QC Before Horizon Gating**: Corrupt `skyl1` heights (`< 0` or `> 35,000 ft`) MUST be masked to `NaN` *prior* to evaluating `12,000 < skyl1 <= 35,000 ft` so garbled height numbers never overwrite cloudy `skyc1` observations to `'CLR'`.
- **Single Uniform `12,000-ft` Policy**: Valid `12,000 < skyl1 <= 35,000 ft` layers MUST be reclassified to `'CLR'` (`0.00`) uniformly across all stations — never left unadjusted or partially weighted.
- **No Dead-End `skyl1` / String `skyc1` Cube Columns**: `skyl1` and categorical `skyc1` MUST NOT be kriged or persisted in `weather_station_hourly` or `weather_regional_hourly`; only bounded numeric `cloud_frac` $\in [0.0,\, 1.0]$ is carried forward.
- **Strict String Normalization & Precipitation Consistency**: `skyc1` tokens MUST be `.str.strip()`-normalized prior to mapping, and imputed station-hours with $\widehat{\text{p01i}} \ge 0.05\text{ in/hr}$ MUST satisfy $\widehat{\text{cloud\_frac}} \ge 0.75$.

## 6. Consequences & Downstream Impact
- **Positive / Guarantees**: Cuts Group D implementation complexity and runtime roughly in half by eliminating unused `skyl1` kriging and string round-tripping; prevents `> 35,000 ft` decoder glitches (such as `95,000 ft` in July 2023 TX) from corrupting `cloud_frac`; eliminates staffed-airport cirrus bullseyes via a single deterministic `12,000-ft` rule; delivers a clean, bounded `cloud_frac_regional` covariate for 2018–2025 solar/net-load GAMs.
- **Negative / Trade-offs**: Expands the regional weather schema from 8 to 9 numeric variables (`cloud_frac_regional` + `cloud_frac_coverage`); drops cloud-base altitude (`skyl1`) after raw horizon gating; low `FEW` layers can still mask higher `OVC` decks below `12,000 ft` due to the single-layer `skyc1` archive schema.
