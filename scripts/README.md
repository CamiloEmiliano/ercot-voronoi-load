# Scripts Inventory

CLI entrypoints are grouped by domain under `scripts/<domain>/`. Reusable contracts, QC logic, and future modeling code live in `src/bayes_demand_fcst/` (`ADR-00002`).

## Domain Directory Layout

- `scripts/weather/` — ASOS archive downloads, Pass-0 irregular cleaning, frozen calibration, legacy station-hour normalization, and regional Voronoi aggregation
- `scripts/spatial/` — ERCOT eGRID boundary extraction, ASOS station metadata, and Voronoi area weights
- `scripts/fuel_mix/` — Year-specific 15-minute ERCOT fuel-mix normalizers (2014–2025) and hourly UTC aggregator
- `scripts/load/` — Canonical ERCOT Native Load download/normalization (`ADR-00001`), manual API probe, and legacy backcast scripts
- `scripts/panel/` — Final hourly modeling panel assembly

## Script Manifest

| Domain | Script | Status | Inputs | Outputs |
|---|---|---|---|---|
| `weather` | `scripts/weather/download_asos_weather_ar.py` | Active DVC | Iowa Mesonet ASOS API (`AR`) | `data/raw/weather_ar_asos/` |
| `weather` | `scripts/weather/download_asos_weather_la.py` | Active DVC | Iowa Mesonet ASOS API (`LA`) | `data/raw/weather_la_asos/` |
| `weather` | `scripts/weather/download_asos_weather_nm.py` | Active DVC | Iowa Mesonet ASOS API (`NM`) | `data/raw/weather_nm_asos/` |
| `weather` | `scripts/weather/download_asos_weather_ok.py` | Active DVC | Iowa Mesonet ASOS API (`OK`) | `data/raw/weather_ok_asos/` |
| `weather` | `scripts/weather/download_asos_weather_tx.py` | Active DVC | Iowa Mesonet ASOS API (`TX`) | `data/raw/weather_tx_asos/` |
| `weather` | `scripts/weather/build_weather_irregular_clean.py` | Active DVC | `data/raw/weather_*_asos/` | `data/interim/weather_irregular_clean/` |
| `weather` | `scripts/weather/build_weather_calibration.py` | Active DVC | `data/interim/weather_irregular_clean/` | `data/interim/weather_calibration/` |
| `weather` | `scripts/weather/normalize_asos_weather.py` | Active DVC (legacy) | `data/raw/weather_*_asos/` | `data/interim/weather_station_hourly/` |
| `weather` | `scripts/weather/aggregate_regional_weather.py` | Active DVC | `data/interim/weather_station_hourly/`, `voronoi_weights.parquet` | `data/interim/weather_regional_hourly/` |
| `spatial` | `scripts/spatial/extract_ercot_map.py` | Active DVC | `data/raw/ercot_map/eGRID2023_Subregions.shp` | `data/interim/ercot_map/ercot_boundary.geojson` |
| `spatial` | `scripts/spatial/build_station_metadata.py` | Active DVC | `data/raw/weather_*_asos/` | `data/interim/station_metadata/station_metadata.parquet` |
| `spatial` | `scripts/spatial/build_voronoi_weights.py` | Active DVC | `station_metadata.parquet`, `ercot_boundary.geojson` | `data/interim/voronoi_weights/voronoi_weights.parquet` |
| `fuel_mix` | `scripts/fuel_mix/normalize_ercot_fuel_mix_2014.py` | Active DVC | `data/raw/ercot_fuel_mix/IntGenByFuel2014.xls` | `data/interim/fuel_mix/fuel_mix_2014.parquet` |
| `fuel_mix` | `scripts/fuel_mix/normalize_ercot_fuel_mix_2015.py` | Active DVC | `data/raw/ercot_fuel_mix/IntGenByFuel2015.xls` | `data/interim/fuel_mix/fuel_mix_2015.parquet` |
| `fuel_mix` | `scripts/fuel_mix/normalize_ercot_fuel_mix_2016.py` | Active DVC | `data/raw/ercot_fuel_mix/IntGenbyFuel2016.xlsx` | `data/interim/fuel_mix/fuel_mix_2016.parquet` |
| `fuel_mix` | `scripts/fuel_mix/normalize_ercot_fuel_mix_2017.py` | Active DVC | `data/raw/ercot_fuel_mix/IntGenbyFuel2017.xlsx` | `data/interim/fuel_mix/fuel_mix_2017.parquet` |
| `fuel_mix` | `scripts/fuel_mix/normalize_ercot_fuel_mix_2018.py` | Active DVC | `data/raw/ercot_fuel_mix/IntGenbyFuel2018.xlsx` | `data/interim/fuel_mix/fuel_mix_2018.parquet` |
| `fuel_mix` | `scripts/fuel_mix/normalize_ercot_fuel_mix_2019.py` | Active DVC | `data/raw/ercot_fuel_mix/IntGenbyFuel2019.xlsx` | `data/interim/fuel_mix/fuel_mix_2019.parquet` |
| `fuel_mix` | `scripts/fuel_mix/normalize_ercot_fuel_mix_2020.py` | Active DVC | `data/raw/ercot_fuel_mix/IntGenbyFuel2020.xlsx` | `data/interim/fuel_mix/fuel_mix_2020.parquet` |
| `fuel_mix` | `scripts/fuel_mix/normalize_ercot_fuel_mix_2021.py` | Active DVC | `data/raw/ercot_fuel_mix/IntGenbyFuel2021.xlsx` | `data/interim/fuel_mix/fuel_mix_2021.parquet` |
| `fuel_mix` | `scripts/fuel_mix/normalize_ercot_fuel_mix_2022.py` | Active DVC | `data/raw/ercot_fuel_mix/IntGenbyFuel2022.xlsx` | `data/interim/fuel_mix/fuel_mix_2022.parquet` |
| `fuel_mix` | `scripts/fuel_mix/normalize_ercot_fuel_mix_2023.py` | Active DVC | `data/raw/ercot_fuel_mix/IntGenbyFuel2023.xlsx` | `data/interim/fuel_mix/fuel_mix_2023.parquet` |
| `fuel_mix` | `scripts/fuel_mix/normalize_ercot_fuel_mix_2024.py` | Active DVC | `data/raw/ercot_fuel_mix/IntGenbyFuel2024.xlsx` | `data/interim/fuel_mix/fuel_mix_2024.parquet` |
| `fuel_mix` | `scripts/fuel_mix/normalize_ercot_fuel_mix_2025.py` | Active DVC | `data/raw/ercot_fuel_mix/IntGenbyFuel2025.xlsx` | `data/interim/fuel_mix/fuel_mix_2025.parquet` |
| `fuel_mix` | `scripts/fuel_mix/aggregate_ercot_fuel_mix_hourly.py` | Active DVC | `data/interim/fuel_mix/fuel_mix_2014..2025.parquet` | `data/interim/fuel_mix_hourly/` |
| `load` | `scripts/load/download_ercot_native_load.py` | Active DVC | ERCOT Native Load archive URLs (2014–2025) | `data/raw/ercot_hourly_load/` |
| `load` | `scripts/load/normalize_ercot_native_load.py` | Active DVC | `data/raw/ercot_hourly_load/` | `data/interim/load_hourly/` |
| `load` | `scripts/load/query_ercot_system_demand.py` | Manual / Exploratory | ERCOT Public Reports API (`NP6-235-CD`), `~/.env` | Stdout JSON summary |
| `load` | `scripts/load/extract_ercot_backcast.py` | Legacy (Non-canonical) | `data/raw/ercot_backcast/` | `data/interim/ercot_backcast/` |
| `load` | `scripts/load/normalize_ercot_load.py` | Legacy (DVC stage retained) | `data/interim/ercot_backcast/` | `data/interim/load_profile_long/` |
| `panel` | `scripts/panel/assemble_observed_panel.py` | Active DVC | `load_hourly/`, `weather_regional_hourly/`, `fuel_mix_hourly/` | `data/processed/ercot_hourly_panel/` |
