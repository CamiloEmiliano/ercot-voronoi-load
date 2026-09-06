"""Download archived Texas ASOS weather observations from Iowa Mesonet."""

from __future__ import annotations

import argparse
import time
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ASOS_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
DEFAULT_START_YEAR = 2014
DEFAULT_END_YEAR = 2025
COURTESY_DELAY_SECONDS = 5.0
RETRY_DELAYS_SECONDS = (30.0, 60.0, 120.0)
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
WEATHER_COLUMNS = (
    "tmpf", "dwpf",  "relh",
    "feel", "skyc1", "skyl1",
    "p01i", "mslp",  "vsby"
)

# Active land stations whose IEM archive begins on or before 2010-12-31.
LAND_STATION_IDS = (
    "0F2", "11R", "3T5", "5C1", "6R3", "6R6", "ABI", "ACT", "ADS", "AFW", "ALI", "AMA",
    "APY", "AQO", "ARM", "ATT", "AUS", "AXH", "BAZ", "BBD", "BEA", "BGD", "BIF", "BKD",
    "BKS", "BMQ", "BPC", "BPG", "BPT", "BRO", "BWD", "BYY", "CDS", "CLL", "CNW", "COM",
    "COT", "CPT", "CRP", "CRS", "CVB", "CWC", "CXO", "DAL", "DFW", "DHT", "DKR", "DLF",
    "DRT", "DTO", "DUX", "DWH", "DYS", "DZB", "E38", "EBG", "ECU", "EDC", "EFD", "ELP",
    "ERV", "F05", "F17", "FST", "FTN", "FTW", "FWS", "GDJ", "GGG", "GKY", "GLE", "GLS",
    "GNC", "GOP", "GPM", "GRK", "GTU", "GVT", "GVW", "GYB", "GYI", "HBV", "HDO", "HHF",
    "HLR", "HOU", "HQZ", "HRL", "HRX", "HYI", "IAH", "ILE", "INJ", "INK", "JAS", "JCT",
    "JDD", "JSO", "JWY", "JXI", "LBB", "LBX", "LFK", "LHB", "LNC", "LRD", "LUD", "LVJ",
    "LZZ", "MAF", "MCJ", "MDD", "MFE", "MKN", "MNZ", "MRF", "MWL", "NFW", "NGP", "NMT",
    "NOG", "NQI", "OCH", "ODO", "ORG", "OSA", "OZA", "PEQ", "PEZ", "PIL", "PKV", "PPA",
    "PRX", "PSN", "PSX", "PVW", "PWG", "PYX", "RAS", "RBD", "RBO", "RFI", "RKP", "RND",
    "RPH", "RWV", "RYW", "SAT", "SEP", "SGR", "SJT", "SKF", "SLR", "SNK", "SOA", "SPS",
    "SSF", "SWW", "T82", "T89", "TFP", "TKI", "TME", "TPL", "TRL", "TYR", "UTS", "UVA",
    "VCT", "XBP",
)

def build_request_url(start_date: date, end_date: date) -> str:
    """Build a UTC request for the selected Texas land stations."""
    parameters = [("station", station_id) for station_id in LAND_STATION_IDS]
    parameters.extend(("data", column) for column in WEATHER_COLUMNS)
    parameters.extend(
        (
            ("year1", str(start_date.year)),
            ("month1", str(start_date.month)),
            ("day1", str(start_date.day)),
            ("year2", str(end_date.year)),
            ("month2", str(end_date.month)),
            ("day2", str(end_date.day)),
            ("tz", "UTC"),
            ("format", "onlycomma"),
            ("missing", "empty"),
            ("trace", "empty"),
            ("latlon", "yes"),
            ("elev", "yes"),
            ("report_type", "3"),
            ("report_type", "4"),
            ("direct", "yes"),
        )
    )
    return f"{ASOS_URL}?{urlencode(parameters)}"

def download_period(start_date: date, end_date: date, destination: Path) -> None:
    """Download one period atomically, backing off on server-load responses."""
    request = Request(
        build_request_url(start_date, end_date),
        headers={"User-Agent": "bayes-demand-fcst research downloader"},
    )
    for delay in (*RETRY_DELAYS_SECONDS, None):
        try:
            with urlopen(request, timeout=120) as response, NamedTemporaryFile(
                mode="wb", dir=destination.parent, delete=False
            ) as temporary_file:
                bytes_written = 0
                while chunk := response.read(DOWNLOAD_CHUNK_BYTES):
                    temporary_file.write(chunk)
                    bytes_written += len(chunk)
                    print(f"{start_date:%Y-%m}: received {bytes_written / 1024 / 1024:.1f} MiB.", flush=True)
                temporary_path = Path(temporary_file.name)
            temporary_path.replace(destination)
            return
        except HTTPError as error:
            if error.code != 503 or delay is None:
                raise
            print(f"{start_date:%Y-%m}: Mesonet returned 503; retrying in {delay:.0f} seconds.")
        except URLError:
            if delay is None:
                raise
            print(f"{start_date:%Y-%m}: network error; retrying in {delay:.0f} seconds.")
        time.sleep(delay)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=DEFAULT_START_YEAR)
    parser.add_argument("--end-year", type=int, default=DEFAULT_END_YEAR)
    parser.add_argument("--delay-seconds", type=float, default=COURTESY_DELAY_SECONDS)
    parser.add_argument("--execute", action="store_true", help="Perform downloads; otherwise only list planned requests.")
    return parser.parse_args()

def main() -> None:
    args = parse_args()
    if args.start_year > args.end_year:
        raise ValueError("start-year must not be later than end-year.")
    if args.delay_seconds < COURTESY_DELAY_SECONDS:
        raise ValueError(f"delay-seconds must be at least {COURTESY_DELAY_SECONDS:g}.")

    output_dir = Path.cwd()
    periods = [
        (date(year, month, 1), date(year + (month == 12), (month % 12) + 1, 1))
        for year in range(args.start_year, args.end_year + 1)
        for month in range(1, 13)
    ]
    for period_index, (start_date, end_date) in enumerate(periods):
        destination = output_dir / f"tx_asos_land_{start_date:%Y_%m}.csv"
        if destination.exists():
            print(f"{start_date:%Y-%m}: already downloaded; skipping {destination}.")
            continue
        if not args.execute:
            print(f"{start_date:%Y-%m}: would download {destination}.")
            continue
        print(f"{start_date:%Y-%m}: downloading {len(LAND_STATION_IDS)} stations.")
        download_period(start_date, end_date, destination)
        print(f"{start_date:%Y-%m}: saved {destination}.")
        if period_index != len(periods) - 1:
            time.sleep(args.delay_seconds)

if __name__ == "__main__":
    main()