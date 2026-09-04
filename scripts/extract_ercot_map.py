"""Extract the ERCOT eGRID subregion boundary from the eGRID shapefile."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import shapefile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_SHAPEFILE = PROJECT_ROOT / "data/raw/ercot_map/eGRID2023_Subregions.shp"
DEFAULT_OUTPUT = Path("ercot_boundary.geojson")
ERCOT_SUBREGION = "ERCT"
WGS84_CRS = "EPSG:4326"


def _required_sidecars(source: Path) -> list[Path]:
    return [source.with_suffix(suffix) for suffix in (".shp", ".shx", ".dbf", ".prj")]


def _validate_shapefile_bundle(source: Path) -> None:
    missing = [path for path in _required_sidecars(source) if not path.exists()]
    if missing:
        missing_paths = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"Missing required eGRID shapefile component(s): {missing_paths}")


def _shape_geometry(shape: shapefile.Shape) -> dict[str, Any]:
    if shape.shapeType not in {shapefile.POLYGON, shapefile.POLYGONZ, shapefile.POLYGONM}:
        raise ValueError(f"Expected polygon eGRID geometry, found shape type {shape.shapeType}")

    return dict(shape.__geo_interface__)


def extract_ercot_map(source: Path, output: Path, subregion: str = ERCOT_SUBREGION) -> int:
    _validate_shapefile_bundle(source)

    reader = shapefile.Reader(str(source))
    features: list[dict[str, Any]] = []
    target_subregion = subregion.upper()

    for shape_record in reader.iterShapeRecords():
        record = shape_record.record
        shape = shape_record.shape
        if record is None or shape is None:
            continue

        properties = record.as_dict()
        if str(properties.get("Subregion", "")).upper() != target_subregion:
            continue

        features.append(
            {
                "type": "Feature",
                "properties": properties,
                "geometry": _shape_geometry(shape),
            }
        )

    if not features:
        raise ValueError(f"No eGRID subregion record found for {subregion!r}")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "name": f"egrid2023_subregion_{target_subregion.lower()}",
                "source": str(source),
                "crs": WGS84_CRS,
                "features": features,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return len(features)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract the ERCOT eGRID subregion boundary to GeoJSON."
    )
    parser.add_argument("--source", type=Path, default=RAW_SHAPEFILE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--subregion", default=ERCOT_SUBREGION)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    feature_count = extract_ercot_map(args.source, args.output, args.subregion)
    print(f"Wrote {feature_count} eGRID {args.subregion.upper()} boundary to {args.output}")


if __name__ == "__main__":
    main()