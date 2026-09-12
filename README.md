# ERCOT Load and Weather Forecasting

Project for ERCOT load forecasting with weather and geography
data lineage. The current focus is building a reproducible data architecture
from public ERCOT, ASOS, and EPA eGRID sources before fitting forecasting models.

## Project goal

Create validated load, weather, and geography datasets that can support future
probabilistic forecasts for ERCOT demand.

## Project structure

```text
bayes_demand_fcst/
├── README.md
├── requirements.txt
├── .gitignore
├── data/
│   ├── external/
│   ├── interim/
│   │   ├── ercot_backcast/
│   │   └── ercot_map/
│   ├── processed/
│   └── raw/
│       ├── ercot_backcast/
│       ├── ercot_map/
│       └── weather_{tx,nm,ok,ar,la}_asos/
├── literature/
├── notebooks/
├── models/
│   └── .gitkeep
├── reports/
│   └── figures/
├── scripts/
│   ├── download_asos_weather_ar.py
│   ├── download_asos_weather_la.py
│   ├── download_asos_weather_nm.py
│   ├── download_asos_weather_ok.py
│   ├── download_asos_weather_tx.py
│   ├── extract_ercot_backcast.py
│   └── extract_ercot_map.py
└── tests/
    ├── test_download_asos_weather_ar.py
    ├── test_download_asos_weather_la.py
    ├── test_download_asos_weather_nm.py
    ├── test_download_asos_weather_ok.py
    ├── test_download_asos_weather_tx.py
    ├── test_extract_ercot_backcast.py
    └── test_extract_ercot_map.py
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

## Current data products

- raw ASOS monthly CSV files in `data/raw/weather_{tx,nm,ok,ar,la}_asos/`
- interim ERCOT load Parquet files in `data/interim/ercot_backcast/`
- interim ERCOT eGRID boundary GeoJSON in `data/interim/ercot_map/`
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

## Extract ERCOT boundary

`scripts/extract_ercot_map.py` reads the eGRID 2023 Subregions shapefile bundle
in `data/raw/ercot_map/`, filters the `Subregion == "ERCT"` feature, and writes
the ERCOT boundary GeoJSON. Run it from the desired interim output directory.

```bash
cd data/interim/ercot_map
../../../.venv/bin/python ../../../scripts/extract_ercot_map.py
```

The output is:

```text
data/interim/ercot_map/ercot_boundary.geojson
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

download_weather_{nm,ok,ar,la}
    scripts/download_asos_weather_{nm,ok,ar,la}.py
        -> data/raw/weather_{nm,ok,ar,la}_asos/

extract_ercot_backcast
    data/raw/ercot_backcast/ + scripts/extract_ercot_backcast.py
        -> data/interim/ercot_backcast/

extract_ercot_map
    data/raw/ercot_map/eGRID2023_Subregions.{shp,shx,dbf,prj}
    + scripts/extract_ercot_map.py
        -> data/interim/ercot_map/ercot_boundary.geojson
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
