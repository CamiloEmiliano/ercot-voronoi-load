"""Build station metadata from the raw ASOS weather archives."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_CODES = ("tx", "nm", "ok", "ar", "la")
REQUIRED_COLUMNS = ("station", "valid", "lon", "lat", "elevation")
DEFAULT_OUTPUT = Path("station_metadata.parquet")
CSV_CHUNK_SIZE = 100_000


def weather_files(raw_dir: Path, state_code: str) -> tuple[Path, ...]:
    return tuple(sorted((raw_dir / f"weather_{state_code}_asos").glob(f"{state_code}_asos_land_*.csv")))


def _update_station_summaries(
    summaries: dict[str, dict[str, Any]], paths: Iterable[Path], state_code: str
) -> None:
    for path in paths:
        try:
            chunks = pd.read_csv(path, usecols=REQUIRED_COLUMNS, chunksize=CSV_CHUNK_SIZE)
        except ValueError as error:
            raise ValueError(f"{path} does not contain the required ASOS metadata columns") from error

        for chunk in chunks:
            chunk["valid"] = pd.to_datetime(chunk["valid"], utc=True)
            for station_id, rows in chunk.groupby("station", sort=False):
                station_key = str(station_id)
                coordinate_values = rows[["lon", "lat", "elevation"]].drop_duplicates()
                if len(coordinate_values) != 1:
                    raise ValueError(f"Station metadata changes over time: {station_key}")
                coordinates = tuple(coordinate_values.iloc[0])
                summary = summaries.get(station_key)
                if summary is None:
                    summaries[station_key] = {
                        "source_state": state_code.upper(),
                        "coordinates": coordinates,
                        "first_observed_utc": rows["valid"].min(),
                        "last_observed_utc": rows["valid"].max(),
                        "observation_count": len(rows),
                    }
                    continue

                if summary["source_state"] != state_code.upper():
                    raise ValueError(f"A station ID appears in more than one source-state archive: {station_key}")
                if summary["coordinates"] != coordinates:
                    raise ValueError(f"Station metadata changes over time: {station_key}")
                summary["first_observed_utc"] = min(summary["first_observed_utc"], rows["valid"].min())
                summary["last_observed_utc"] = max(summary["last_observed_utc"], rows["valid"].max())
                summary["observation_count"] += len(rows)


def build_station_metadata(raw_dir: Path, output: Path, state_codes: Iterable[str] = STATE_CODES) -> pd.DataFrame:
    summaries: dict[str, dict[str, Any]] = {}
    for state_code in state_codes:
        paths = weather_files(raw_dir, state_code)
        if not paths:
            raise FileNotFoundError(f"No ASOS CSV files found for {state_code.upper()}")
        _update_station_summaries(summaries, paths, state_code)

    metadata = pd.DataFrame(
        [
            {
                "station_id": station_id,
                "source_state": summary["source_state"],
                "longitude_wgs84": summary["coordinates"][0],
                "latitude_wgs84": summary["coordinates"][1],
                "elevation_m": summary["coordinates"][2],
                "first_observed_utc": summary["first_observed_utc"],
                "last_observed_utc": summary["last_observed_utc"],
                "observation_count": summary["observation_count"],
            }
            for station_id, summary in summaries.items()
        ]
    ).sort_values("station_id", ignore_index=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    metadata.to_parquet(output, index=False, engine="pyarrow", compression="zstd")
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=PROJECT_ROOT / "data/raw")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    metadata = build_station_metadata(args.raw_dir, args.output)
    print(f"Wrote {len(metadata)} station records to {args.output}")


if __name__ == "__main__":
    main()