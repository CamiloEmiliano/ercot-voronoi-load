"""Extract Texas county boundaries from the Census TIGER/Line county shapefile."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import shapefile


RAW_SHAPEFILE = Path("data/raw/texas_counties/tl_2025_us_county.shp")
DEFAULT_OUTPUT = Path("data/interim/texas_counties/texas_counties.geojson")
TEXAS_STATEFP = "48"


def _shape_geometry(shape: shapefile.Shape) -> dict[str, Any]:
    if shape.shapeType not in {shapefile.POLYGON, shapefile.POLYGONZ, shapefile.POLYGONM}:
        raise ValueError(f"Expected polygon county geometry, found shape type {shape.shapeType}")

    return dict(shape.__geo_interface__)


def extract_texas_counties(source: Path, output: Path) -> int:
    reader = shapefile.Reader(str(source))
    field_names = [field[0] for field in reader.fields[1:]]

    features: list[dict[str, Any]] = []
    for shape_record in reader.iterShapeRecords():
        record = shape_record.record
        shape = shape_record.shape
        if record is None or shape is None:
            continue

        properties: dict[str, Any] = {
            field_name: value for field_name, value in zip(field_names, record)
        }
        if str(properties.get("STATEFP")) != TEXAS_STATEFP:
            continue

        features.append(
            {
                "type": "Feature",
                "properties": properties,
                "geometry": _shape_geometry(shape),
            }
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "name": "texas_counties_2025_tiger_line",
                "source": str(source),
                "crs": "EPSG:4269",
                "features": features,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return len(features)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract Texas county boundaries from a Census county shapefile."
    )
    parser.add_argument("--source", type=Path, default=RAW_SHAPEFILE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    county_count = extract_texas_counties(args.source, args.output)
    print(f"Wrote {county_count} Texas county boundaries to {args.output}")


if __name__ == "__main__":
    main()