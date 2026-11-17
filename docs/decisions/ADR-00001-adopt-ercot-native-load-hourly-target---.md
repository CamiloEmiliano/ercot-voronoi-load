# ADR-00001: Adopt ERCOT Native Load as Hourly Forecasting Target

- **ADR ID**: ADR-00001
- **Filename**: `ADR-00001-adopt-ercot-native-load-hourly-target---.md`
- **Date**: 2026-11-17 04:32 UTC
- **Status**: ACCEPTED
- **Target Subsystem**: Load ingestion, fuel-mix aggregation, and Bayesian GAM forecasting scope (`scripts/`, `dvc.yaml`, `data/`)
- **Linked Commits**: Pending implementation

---

## 1. Context & Problem Statement
The repository currently holds 2014--2025 ERCOT Fuel Mix Parquets at 15-minute resolution (`data/interim/fuel_mix/`), 2014--2025 ASOS weather archives and Voronoi spatial weights at hourly resolution ([`scripts/aggregate_regional_weather.py`](../../scripts/aggregate_regional_weather.py)), and extraction tooling for ERCOT Backcasted Load Profiles ([`scripts/extract_ercot_backcast.py`](../../scripts/extract_ercot_backcast.py), [`scripts/normalize_ercot_load.py`](../../scripts/normalize_ercot_load.py)). However, the downstream panel assembler ([`scripts/assemble_observed_panel.py`](../../scripts/assemble_observed_panel.py)) requires a canonical hourly `load_mw` series indexed by `timestamp_utc`, which is not yet present.

Two paths to a system-wide demand target failed to satisfy project requirements:
1. **Backcasted Load Profiles (`PType_WZ`)** represent synthetic 15-minute kWh shapes per customer profile type and weather zone, not measured system-wide demand; they cannot be summed to ERCOT MW without customer-usage scaling weights (`LSEGUNADJ`).
2. **System-Wide Demand (`NP6-235-CD`)** provides 15-minute actual MW, but direct queries via [`scripts/query_ercot_system_demand.py`](../../scripts/query_ercot_system_demand.py) confirmed that ERCOT's public API and archive endpoints only retain data back to `2026-03-26`, leaving 2014--2025 unavailable without a support-ticket fulfillment or costly commercial feeds.

To complete the supply-demand balance over 2014--2025 with reproducible public data, the project must adopt an accessible target series, align the 15-minute fuel-mix data to it, and redefine its forecasting figures of merit away from 15-minute coincident peaks.

## 2. Decision Drivers & Constraints
- **Temporal Coverage**: Must span 2014--2025 continuously to match existing ASOS weather and ERCOT Fuel Mix artifacts.
- **Public Reproducibility & Terms**: Must rely on publicly accessible ERCOT archives reproducible under local DVC without paid third-party subscriptions or agentic credentialed scraping.
- **Physical Semantics**: Target must represent measured/settled ERCOT-wide load (MW / hourly MWh), not synthetic per-premise profile shapes.
- **DST & UTC Integrity**: Must preserve and test Central Prevailing Time (`CPT`) daylight-saving transitions (23-hour spring-forward and 25-hour fall-back days) before producing `timestamp_utc`.
- **Forecast-Time Validity**: Any fuel-mix covariates used in downstream Bayesian GAMs must respect forecast-origin availability lags and avoid leaking contemporaneous generation into the load target.

## 3. Considered Alternatives & Rejection Rationale
- **Alternative A: Use Backcasted Load Profiles (`load_profile_long`) as the system target**. Rejected because `PType_WZ` rows are unscaled per-profile kWh shapes built by ERCOT's profiling models; aggregating them without meter counts does not yield ERCOT load and introduces model-on-model circularity.
- **Alternative B: Acquire 15-minute `NP6-235-CD` history via third-party mirrors or ERCOT tickets**. Rejected for the primary 2014--2025 baseline because ERCOT's public API stops at `2026-03-26`, commercial vendors (Yes Energy, GridStatus) are cost-prohibitive (and GridStatus begins `2015-12-31`), and ServiceNow archival fulfillment has an uncertain timeline.
- **Alternative C: Upsample hourly load to 15-minute intervals via monotonic splines (e.g., PCHIP)**. Rejected as ground truth because hourly values are integrated averages rather than instantaneous boundary points, and interpolation cannot recover unobserved intra-hour 15-minute coincident peaks.

## 4. Decision Outcome
Adopt **ERCOT Hourly Load Data Archives (`Native_Load_2014`--`2025`)** as the canonical load dataset and align all pipeline inputs and modeling objectives to **hourly UTC resolution**:

1. **Hourly System-Wide Load Ingestion**: Download `2014_ercot_hourly_load_data.xls`, `native_load_2015.xls`, and `Native_Load_2016`--`2025.zip` into `data/raw/ercot_hourly_load/`, and normalize them (preserving system total `ERCOT` plus the 8 weather zones and resolving `HourEnding` / DST transitions) into `data/interim/load_hourly/` with `timestamp_utc` and `load_mw`.
2. **Hourly Fuel-Mix Aggregation**: Add a deterministic stage aggregating the existing 15-minute `data/interim/fuel_mix/fuel_mix_YYYY.parquet` datasets into hourly `timestamp_utc` totals (`sum(generation_mwh)` per hour-ending window and `fuel_type`, numerically equal to average hourly MW) under `data/interim/fuel_mix_hourly/`.
3. **Hourly Weather Alignment Verification**: Verify one-to-one `timestamp_utc` alignment and coverage consistency across `load_hourly`, `weather_regional_hourly` ([`scripts/aggregate_regional_weather.py`](../../scripts/aggregate_regional_weather.py)), and `fuel_mix_hourly` inside [`scripts/assemble_observed_panel.py`](../../scripts/assemble_observed_panel.py) (or an updated observed-panel builder).
4. **Redefined Bayesian GAM Use Case & Figures of Merit**: Shift the primary forecasting objective from 15-minute coincident-peak (4CP) prediction to hourly probabilistic figures of merit suitable for settled hourly load, including:
   - full hourly demand predictive distributions and calibrated credible/prediction intervals (`CRPS`, pinball loss, interval coverage);
   - probability of exceeding high-load or tight-reserve thresholds during summer/winter peak hours;
   - hourly evening net-load ramp uncertainty when paired with lagged renewable generation shares; and
   - optional spatial extensions across ERCOT's 8 weather zones.

## 5. Invariants & Guardrails
- **Target Separation**: `load_hourly` (`Native_Load`) MUST remain separate from `load_profile_long` (Backcasted Load Profiles); downstream modeling panels must never mix unscaled backcasted profile kWh with `Native_Load` MW.
- **No Synthetic Sub-Hourly Ground Truth**: Hourly `Native_Load` MUST NOT be spline-interpolated to 15-minute intervals and labeled as observed 15-minute demand.
- **Explicit DST Handling**: Every annual `Native_Load` normalizer MUST assert expected row counts on spring-forward (23 hours) and fall-back (25 hours) dates and map repeated fall-back hours unambiguously to `timestamp_utc`.
- **Sampled Persisted-Output QC**: Following the pattern in [`scripts/ercot_fuel_mix_qc.py`](../../scripts/ercot_fuel_mix_qc.py), `Native_Load` normalization and hourly fuel-mix aggregation must reopen persisted Parquet artifacts and verify sampled values and totals before publishing.
- **Supply-Demand Balance Semantics**: Total hourly `fuel_mix` generation and `Native_Load` are expected to differ slightly due to transmission/distribution losses, DC-tie interchange, and storage charging (`WSL`); contemporaneous fuel-mix totals MUST NOT be used as features for forecasting contemporaneous load.

## 6. Consequences & Downstream Impact
- **Positive / Guarantees**: Unlocks a complete, zero-cost, 12-year (`2014--2025`) public dataset aligning load, Voronoi-weighted weather, and fuel mix at a common hourly UTC cadence; preserves 8 ERCOT weather zones for spatial diagnostics and future hierarchical modeling; provides a clean physical consistency check via `sum(fuel_mix_hourly) - load_mw`.
- **Negative / Trade-offs**: Precludes direct 15-minute coincident-peak (4CP) modeling unless 15-minute historical data is later acquired; adds new DVC pipeline stages for `Native_Load` acquisition/normalization and hourly fuel-mix aggregation; requires updating project planning and theory documentation to reflect the hourly figures of merit.
