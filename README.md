# ERCOT Load and Weather Forecasting

Project for ERCOT load forecasting with weather and geography
data lineage. The current focus is building a reproducible data architecture
from public ERCOT, ASOS, and Census sources before fitting forecasting models.

## Project goal

Create validated load, weather, and geography datasets that can support future
probabilistic forecasts for ERCOT demand.

## Why this project is strong for a portfolio

- DVC records source-to-interim data lineage without committing large data files
- Raw, interim, and processed data layers are separated explicitly
- Source-specific quirks are preserved until validated processing decisions are made
- The scripts are reproducible command-line entry points
- The structure is realistic for energy analytics and forecasting work

## Business framing

This project mirrors an energy analytics workflow where load is seasonal,
weather-sensitive, and dependent on careful treatment of source data contracts.

## Data

Large source and generated datasets are kept out of Git. DVC tracks the current
pipeline state and the manually downloaded county ZIP pointer.

## Project structure

```text
bayes_demand_fcst/
├── README.md
├── requirements.txt
├── .gitignore
├── data/
│   ├── external/
│   ├── interim/
│   ├── processed/
│   └── raw/
├── notebooks/
├── models/
│   └── .gitkeep
├── reports/
│   └── figures/
├── scripts/
│   ├── download_asos_weather_tx.py
│   ├── extract_ercot_backcast.py
│   └── extract_texas_counties.py
└── tests/
    ├── test_download_asos_weather_tx.py
    ├── test_extract_ercot_backcast.py
    └── test_extract_texas_counties.py
```

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
.venv/bin/dvc dag
```

## Model summary

The forecasting model will be added after the real data contracts are stable.
The intended model layer will use ERCOT load with weather covariates and will
report forecast uncertainty rather than point forecasts alone.

## Expected outputs

- raw ASOS monthly CSV files in `data/raw/weather_tx_asos/`
- interim ERCOT load Parquet files in `data/interim/ercot_backcast/`
- interim Texas county GeoJSON in `data/interim/texas_counties/`
- future processed modeling panels in `data/processed/`

## Download Texas ASOS weather data

`scripts/download_asos_weather_tx.py` downloads the selected 170 active, land-based
Texas ASOS stations with records beginning no later than 2010. It requests only
load-relevant numeric weather fields, saves raw CSV responses unchanged in the
directory where it is invoked, skips files already present, and defaults to dry-run mode.

```bash
cd data/raw/weather
python ../../../scripts/download_asos_weather_tx.py
python ../../../scripts/download_asos_weather_tx.py --execute
```

The default download spans 2014 through 2025, one month per request. The script
uses one request at a time, streams progress as it receives data, and waits at
least five seconds between requests.

## Extract ERCOT load workbooks

`scripts/extract_ercot_backcast.py` converts each `Q1_YYYY` through `Q4_YYYY`
worksheet in `data/raw/ercot_backcast/` into a separate Parquet file. The raw
workbooks remain unchanged; each output preserves the source columns and adds
`source_workbook` and `source_sheet` provenance fields. Run it from the desired
interim output directory. The 2018 through 2025 workbooks' month-named tabs are
written as individual monthly files so their boundaries can be validated before merging.

```bash
cd data/interim/ercot_backcast
../../../.venv/bin/python ../../../scripts/extract_ercot_backcast.py
../../../.venv/bin/python ../../../scripts/extract_ercot_backcast.py --year 2015
```

Outputs follow this structure:

```text
data/interim/ercot_backcast/2015/q1.parquet
data/interim/ercot_backcast/2015/q2.parquet
data/interim/ercot_backcast/2015/q3.parquet
data/interim/ercot_backcast/2015/q4.parquet
data/interim/ercot_backcast/2018/m01.parquet
data/interim/ercot_backcast/2018/m02.parquet
...
data/interim/ercot_backcast/2018/m12.parquet
```

Existing files are skipped. Use `--overwrite` to replace them.

## Extract Texas county boundaries

`scripts/extract_texas_counties.py` reads the unzipped 2025 Census TIGER/Line
county shapefile in `data/raw/texas_counties/`, filters to Texas records using
`STATEFP == "48"`, and writes a GeoJSON boundary layer for downstream spatial
joins and EDA.

```bash
.venv/bin/python scripts/extract_texas_counties.py
```

The output is:

```text
data/interim/texas_counties/texas_counties.geojson
```

## Data versioning

The project uses DVC to record data lineage while keeping large source and
derived artifacts out of Git. No public DVC remote is configured: the raw
datasets remain local because of their size and external availability.

The pipeline declared in `dvc.yaml` currently contains:

```text
download_weather_tx
    scripts/download_asos_weather_tx.py
        -> data/raw/weather_tx_asos/

extract_ercot_backcast
    data/raw/ercot_backcast/ + scripts/extract_ercot_backcast.py
        -> data/interim/ercot_backcast/

extract_texas_counties
    data/raw/texas_counties/tl_2025_us_county.{shp,shx,dbf,prj,cpg}
    + scripts/extract_texas_counties.py
        -> data/interim/texas_counties/
```

Inspect the data lineage and local artifact status with:

```bash
.venv/bin/dvc dag
.venv/bin/dvc status
```

`dvc.lock` captures the content hashes of the current raw weather archive,
ERCOT workbook inputs, and generated interim Parquet files. A future processing
stage will produce the final aligned load-weather model panel in `data/processed/`.

## Next steps

Possible extensions:

- hierarchical model by store or product family
- holiday effects
- external covariates such as price or marketing spend
- comparison with a frequentist baseline
- deployment as a Streamlit app or API
