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
- **Alternative B: Unconditionally krige both `skyc1` (encoded) and `skyl1` across all hours**. Rejected because `skyl1` is structurally undefined when `skyc1 == "CLR"` and adds negligible regional demand signal beyond `cloud_frac`, `tmpf`, and `dwpf`.
- **Alternative C: Retain unadjusted manual high-cloud layers (`skyl1 > 12,000` ft) as equivalent to low overcast**. Rejected because rural automated stations physically cannot see clouds above `12,000` ft, introducing systematic staffed-airport vs. rural-AWOS bias.

## 4. Decision Outcome
Adopt **numeric okta encoding, 12,000-ft ceilometer harmonization, and bounded ST-Kriging** for **Group D (`skyc1`, `skyl1`)**:

1. **Encode `skyc1` (with `.str.strip()`) to Numeric `cloud_frac` $\in [0.0,\, 1.0]$**:
   - `CLR`, `SKC`, `NCD`, `NSC` → `0.00` ($0/8$)
   - `FEW` → `0.1875` ($1.5/8$)
   - `SCT` → `0.4375` ($3.5/8$)
   - `BKN` → `0.75` ($6/8$)
   - `OVC`, `VV` → `1.00` ($8/8$)
   - Missing / unrecognized codes remain `NaN`.
2. **Harmonize the 12,000-ft Ceilometer Cap**:
   - Standardize high-altitude manual augmentations where numeric `skyl1 > 12000` ft to `0.00` (clear below the 12,000-ft automated sensor horizon) — or flag and exclude/down-weight them consistently — so staffed airports and automated ASOS/AWOS sites observe the same physical cloud-base ceiling domain (recording harmonized row counts in `quality_report.json`).
3. **Temporal Alignment & Local Space-Time Kriging for `cloud_frac`**:
   - Align raw observations onto `H:00Z` via **nearest valid observation within $\pm 30\text{ min}$** (or linear interpolation on `cloud_frac`).
   - Impute remaining missing station-hours via **Local Space-Time Kriging** on `cloud_frac` with moderate mesoscale correlation lengths ($\ell_s \approx 80\text{–}150\text{ km}$, $\ell_t \approx 3\text{–}6\text{ hr}$), strictly clamped to $[0.0,\, 1.0]$.
4. **Promote `cloud_frac_regional` & Drop `skyl1` from Stage 3**:
   - Add `cloud_frac` to `WEATHER_VARIABLES` in [`scripts/weather/aggregate_regional_weather.py`](../../scripts/weather/aggregate_regional_weather.py) so `weather_regional_hourly` and `ercot_hourly_panel` carry `cloud_frac_regional` and `cloud_frac_coverage`.
   - Leave `skyl1` omitted from Stage 3 regional aggregation (if retained at Stage 2 station level, `skyl1` may only be kriged in $\log(\text{skyl1})$ space conditioned on `cloud_frac > 0` and left `NaN` wherever `cloud_frac == 0`).

## 5. Invariants & Guardrails
- **Bounded Cloud Fraction**: Every output `cloud_frac` MUST satisfy $0.0 \le \widehat{\text{cloud\_frac}} \le 1.0$.
- **Strict String Normalization**: `skyc1` codes MUST be `.str.strip()`-normalized prior to mapping (handling padded tokens such as `'VV '`).
- **No Unconditional `skyl1` Imputation**: `skyl1` MUST NEVER be kriged where `cloud_frac == 0` (or `skyc1 == "CLR"`).
- **Sensor-Horizon Consistency**: High-altitude manual reports (`skyl1 > 12,000` ft) MUST be treated consistently across staffed and automated stations rather than treated as equivalent to low-level solar-blocking overcast.

## 6. Consequences & Downstream Impact
- **Positive / Guarantees**: Unlocks a bounded, spatially consistent regional cloud-cover feature (`cloud_frac_regional`) for 2018–2025 behind-the-meter solar / net-load modeling while eliminating staffed-airport 12,000-ft ceilometer bias and `skyl1` clear-sky fabrication.
- **Negative / Trade-offs**: Expands the regional weather schema from 8 to 9 numeric variables (`cloud_frac_regional` + `cloud_frac_coverage`); low `FEW` layers can still mask higher `OVC` decks in single-layer `skyc1`, which is an inherent limitation of the single-layer ASOS archive schema.
