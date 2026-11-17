"""Download historical ERCOT hourly Native Load archives (2014-2025)."""

from __future__ import annotations

import argparse
from pathlib import Path
from tempfile import NamedTemporaryFile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data/raw/ercot_hourly_load"
COURTESY_DELAY_SECONDS = 2.0
RETRY_DELAYS_SECONDS = (10.0, 30.0, 60.0)
DOWNLOAD_CHUNK_BYTES = 1024 * 1024

ARCHIVE_URLS: dict[int, str] = {
    2014: "https://www.ercot.com/files/docs/2015/10/22/2014_ercot_hourly_load_data.xls",
    2015: "https://www.ercot.com/files/docs/2016/01/07/native_load_2015.xls",
    2016: "https://www.ercot.com/files/docs/2017/01/10/native_Load_2016.zip",
    2017: "https://www.ercot.com/files/docs/2018/01/09/native_load_2017.zip",
    2018: "https://www.ercot.com/files/docs/2019/01/07/native_load_2018.zip",
    2019: "https://www.ercot.com/files/docs/2020/01/09/Native_Load_2019.zip",
    2020: "https://www.ercot.com/files/docs/2021/01/12/Native_Load_2020.zip",
    2021: "https://www.ercot.com/files/docs/2021/11/12/Native_Load_2021.zip",
    2022: "https://www.ercot.com/files/docs/2022/02/08/Native_Load_2022.zip",
    2023: "https://www.ercot.com/files/docs/2023/02/09/Native_Load_2023.zip",
    2024: "https://www.ercot.com/files/docs/2024/02/06/Native_Load_2024.zip",
    2025: "https://www.ercot.com/files/docs/2025/02/11/Native_Load_2025.zip",
}


def target_filename(year: int) -> str:
    url = ARCHIVE_URLS[year]
    return url.rsplit("/", 1)[-1]


def download_file(url: str, destination: Path) -> None:
    """Download one archive atomically to destination."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": "bayes-demand-fcst research downloader"})
    for delay in (*RETRY_DELAYS_SECONDS, None):
        try:
            with urlopen(request, timeout=120) as response, NamedTemporaryFile(
                mode="wb", dir=destination.parent, delete=False
            ) as temporary_file:
                while chunk := response.read(DOWNLOAD_CHUNK_BYTES):
                    temporary_file.write(chunk)
                temporary_path = Path(temporary_file.name)
            temporary_path.replace(destination)
            return
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or delay is None:
                raise
            print(f"{destination.name}: HTTP {error.code}; retrying in {delay:.0f}s.")
        except (URLError, TimeoutError, OSError):
            if delay is None:
                raise
            print(f"{destination.name}: network error; retrying in {delay:.0f}s.")
        time.sleep(delay)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=2014)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--delay-seconds", type=float, default=COURTESY_DELAY_SECONDS)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--execute", action="store_true", help="Perform downloads; otherwise dry-run.")
    args = parser.parse_args()
    if args.start_year > args.end_year:
        raise ValueError("start-year must not exceed end-year.")
    if args.delay_seconds < 0:
        raise ValueError("delay-seconds must be non-negative.")
    missing = [y for y in range(args.start_year, args.end_year + 1) if y not in ARCHIVE_URLS]
    if missing:
        raise ValueError(f"Unsupported year(s): {missing}")
    return args


def main() -> None:
    args = parse_args()
    years = list(range(args.start_year, args.end_year + 1))
    for idx, year in enumerate(years):
        url = ARCHIVE_URLS[year]
        destination = args.output_dir / target_filename(year)
        if destination.exists() and not args.overwrite:
            print(f"{year}: already present; skipping {destination}.")
            continue
        if not args.execute:
            print(f"{year}: would download {url} -> {destination}.")
            continue
        print(f"{year}: downloading {url} -> {destination}.")
        download_file(url, destination)
        print(f"{year}: saved {destination} ({destination.stat().st_size} bytes).")
        if idx != len(years) - 1 and args.delay_seconds > 0:
            time.sleep(args.delay_seconds)


if __name__ == "__main__":
    main()
