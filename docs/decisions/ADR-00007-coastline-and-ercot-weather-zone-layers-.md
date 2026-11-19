# ADR-00007: Auxiliary Coastline and ERCOT Weather-Zone Spatial Layers

- **ADR ID**: ADR-00007
- **Filename**: `ADR-00007-coastline-and-ercot-weather-zone-layers-.md`
- **Date**: 2026-11-18 17:25 UTC
- **Status**: PROPOSED
- **Target Subsystem**: Spatial geometry, station metadata covariates, and Voronoi aggregation weights (`data/external/`, `src/bayes_demand_fcst/`, `scripts/spatial/`, `scripts/weather/`, `dvc.yaml`)
- **Linked Commits**: Pending acceptance

---

## 1. Context & Problem Statement
The repository currently relies on a single external polygon — the EPA eGRID `ERCT` subregion boundary (`data/interim/ercot_map/ercot_boundary.geojson`) — to clip Voronoi polygons across all 317 ASOS/AWOS stations in [`scripts/spatial/build_voronoi_weights.py`](../../scripts/spatial/build_voronoi_weights.py). Inspection of the weather imputation design ([`ADR-00003`](ADR-00003-spatiotemporal-kriging-group-a-weather--.md)–[`ADR-00006`](ADR-00006-cloud-fraction-kriging-group-d-sky-cover.md)) and canonical hourly load target ([`ADR-00001`](ADR-00001-adopt-ercot-native-load-hourly-target---.md)) exposes two structural gaps caused by having only a single statewide boundary:

1. **Missing Coastal Distance Covariate (`dist_coast_km`)**: [`ADR-00003`](ADR-00003-spatiotemporal-kriging-group-a-weather--.md) uses coastal proximity (`dist_coast`) alongside `elevation_m` and `latitude` to parameterize marine vs. continental diurnal amplitude damping when station history is sparse (and to support coastal regime diagnostics). However, `ercot_boundary.geojson` encloses both inland grid borders and the Gulf shoreline without distinguishing them, so distance to `ercot_boundary.geojson` does not measure distance to the Gulf of Mexico.
2. **Severe Load vs. Area Weight Dilution in Statewide Voronoi Aggregation**: Because Greater Houston has 7+ closely spaced ASOS stations (`HOU`, `IAH`, `GLS`, `EFD`, `LVJ`, `SGR`, `DWH`), their combined statewide Voronoi `area_weight` in `voronoi_weights.parquet` is only **~0.94%** of `ERCT` area, whereas a single rural inland station (`NMT`) receives **1.08%**. In reality, ERCOT's `COAST` weather zone (Houston–Galveston) represents **~25–30%** of system demand (`coast_mw` in `load_hourly`). Meanwhile, [`ADR-00001`](ADR-00001-adopt-ercot-native-load-hourly-target---.md) already normalized all **8 ERCOT weather zones** (`coast_mw`, `east_mw`, `fwest_mw`, `north_mw`, `ncent_mw`, `south_mw`, `scent_mw`, `west_mw`) into `load_hourly`, but no matching weather-zone polygons or zone-level Voronoi weights exist yet.

## 2. Decision Drivers & Constraints
- **Separation of Concerns**: Spatial layer acquisition, projection, station covariate enrichment, and multi-zone Voronoi clipping are substantial geometry tasks that must stay decoupled from the spatiotemporal kriging math in [`ADR-00003`](ADR-00003-spatiotemporal-kriging-group-a-weather--.md)–[`ADR-00006`](ADR-00006-cloud-fraction-kriging-group-d-sky-cover.md).
- **Public, Reproducible Spatial Sources**: External shapefiles/GeoJSONs placed under `data/external/` must come from public-domain or open government sources (e.g., Natural Earth 10m coastline and US Census TIGER/Line Texas counties + official ERCOT county-to-weather-zone mapping, or ERCOT's published Weather Zone boundary layer) and be tracked deterministically in DVC.
- **Consistent Projected Metric (`EPSG:3083`)**: All distance calculations (`dist_coast_km`) and polygon intersections/areas MUST be computed in `EPSG:3083` (Texas Centric Albers Equal Area, meters), matching [`scripts/spatial/build_voronoi_weights.py`](../../scripts/spatial/build_voronoi_weights.py).
- **Backward Compatibility with Statewide Series**: Existing statewide ERCT Voronoi weights (`sum(area_weight) == 1.0`) must remain available alongside the 8 per-zone Voronoi weight sets (`sum(zone_area_weight) == 1.0` within each weather zone).

## 3. Considered Alternatives & Rejection Rationale
- **Alternative A: Approximate coastal distance with global `(x, y)` coordinates or neighbor-only interpolation**. Rejected because the Texas Gulf shoreline curves ~90° from Brownsville (`S–N`) to Beaumont/Lake Charles (`SW–NE`), making linear planar proxies physically inaccurate across the 0–80 km sea-breeze decay zone, while an explicit coastline layer makes `dist_coast_km` exact in a few lines of `shapely`.
- **Alternative B: Rely exclusively on statewide ERCT Voronoi area weights for downstream GAMs**. Rejected because a 25× under-weighting of Houston/Galveston (`0.94%` area vs. `~28%` load) masks coastal heat-index and cloud-cover spikes (`ADR-00003`, `ADR-00006`) in `load_mw` and prevents 1:1 weather alignment with the 8 weather-zone load columns already materialized in `load_hourly`.
- **Alternative C: Fold coastline and weather-zone geometry processing into `ADR-00003`**. Rejected because mixing spatial ETL, external shapefile contracts, and Voronoi re-partitioning into the Group A kriging ADR bloats both the specification and implementation scope.

## 4. Decision Outcome
Introduce two auxiliary spatial reference layers under `data/external/` and extend the `spatial` / `weather` pipeline stages cleanly before downstream GAM modeling:

1. **Auxiliary Spatial Inputs (`data/external/` + DVC)**:
   - **Gulf Coastline Layer (`data/external/coastline/`)**: Ingest public-domain Natural Earth 10m coastline (`ne_10m_coastline`, clipped to the Gulf of Mexico / Western Atlantic bounding box in `EPSG:4326` and projected to `EPSG:3083`) into `data/interim/ercot_map/gulf_coastline.geojson`.
   - **ERCOT 8 Weather-Zone Polygons (`data/external/ercot_weather_zones/`)**: Construct canonical polygons for the 8 ERCOT weather zones (`COAST`, `EAST`, `FWEST`, `NORTH`, `NCENT`, `SOUTH`, `SCENT`, `WEST`) — either from ERCOT's published Weather Zone GIS layer or by dissolving US Census TIGER/Line Texas county polygons (`cb_2023_us_county_500k`) via ERCOT's official county-to-weather-zone assignment table, clipped/aligned to the ERCOT footprint — into `data/interim/ercot_map/ercot_weather_zones.geojson`.
2. **Station Metadata Enrichment (`station_metadata.parquet`)**:
   - Extend [`scripts/spatial/build_station_metadata.py`](../../scripts/spatial/build_station_metadata.py) (and reusable spatial helpers in `src/bayes_demand_fcst/`) to compute and persist:
     - `dist_coast_km`: exact Euclidean distance in `EPSG:3083` kilometers from each of the 317 stations to `gulf_coastline.geojson` (enabling both linear $d_{\text{coast}}$ and exponential marine-decay $\exp(-d_{\text{coast}} / L_{\text{sea}})$ terms in [`ADR-00003`](ADR-00003-spatiotemporal-kriging-group-a-weather--.md)).
     - `weather_zone`: assigned ERCOT weather zone (`COAST`..`WEST`, or `OUTSIDE_ERCOT` for out-of-state border stations in `NM`, `OK`, `AR`, `LA` that still contribute to Voronoi cells intersecting ERCOT).
3. **Dual Statewide + Weather-Zone Voronoi Weights**:
   - Retain statewide clipped Voronoi weights (`voronoi_weights.parquet`, `sum == 1.0` over `ERCT`) AND produce per-weather-zone clipped Voronoi weights (`voronoi_weights_by_zone.parquet`, intersecting the 316 global station Voronoi polygons with each of the 8 weather-zone polygons in `EPSG:3083` so `sum(area_weight) == 1.0` within every zone).
   - Note: Global Voronoi tessellation MUST be computed across all 317 five-state stations *before* clipping to each weather zone so border zones (`NORTH`, `EAST`, `FWEST`, `WEST`) properly receive geometric support from nearby `OK`, `AR`, `LA`, and `NM` stations where appropriate.
4. **Downstream Regional & Zone Weather Aggregation**:
   - Enable [`scripts/weather/aggregate_regional_weather.py`](../../scripts/weather/aggregate_regional_weather.py) (or a companion zone aggregator) to output both ERCOT-wide area-weighted weather and per-weather-zone hourly weather (`COAST`..`WEST`), allowing downstream GAMs to pair `coast_mw` with `COAST` weather or form load-weighted system weather composites without geometric distortion.

## 5. Invariants & Guardrails
- **Global Tessellation Before Zone Clipping**: Voronoi cells MUST be generated once from the full 5-state station set in `EPSG:3083` and then intersected with each weather-zone polygon — NEVER tessellated separately using only stations inside a single zone.
- **Unit-Sum Partition of Unity**: Area weights MUST sum to `1.0` (within `1e-9` tolerance) both for `ERCT` statewide and independently within each of the 8 ERCOT weather zones (`COAST`, `EAST`, `FWEST`, `NORTH`, `NCENT`, `SOUTH`, `SCENT`, `WEST`).
- **CRS & Units Discipline**: `gulf_coastline.geojson` and `ercot_weather_zones.geojson` MUST validate as `EPSG:4326` on disk (matching `ercot_boundary.geojson`) and project to `EPSG:3083` meters for all distance and area math, storing `dist_coast_km` explicitly in kilometers.
- **Canonical Zone Naming**: Weather-zone identifiers MUST match the 8 normalized zone keys established in [`ADR-00001`](ADR-00001-adopt-ercot-native-load-hourly-target---.md) (`coast`, `east`, `fwest`, `north`, `ncent`, `south`, `scent`, `west`).

## 6. Consequences & Downstream Impact
- **Positive / Guarantees**: Supplies exact `dist_coast_km` for Group A harmonic drift and coastal sea-breeze physics ([`ADR-00003`](ADR-00003-spatiotemporal-kriging-group-a-weather--.md)); eliminates the 25× Houston/Galveston Voronoi area-weight dilution by resolving `COAST` (and all 8 ERCOT weather zones) directly alongside `coast_mw`..`west_mw`; keeps spatial geometry ETL cleanly isolated from kriging solver code.
- **Negative / Trade-offs**: Adds two lightweight external GIS dependencies under `data/external/`, new spatial DVC stages/artifacts, and an 8× wider/longer zone-level weather summary when zone-level covariates or hierarchical GAMs are enabled.
