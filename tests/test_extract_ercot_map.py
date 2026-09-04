import json
import subprocess
import sys
from pathlib import Path

import pytest
import shapefile

from scripts.extract_ercot_map import extract_ercot_map


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "extract_ercot_map.py"


def _write_subregion(writer, subregion, offset):
    writer.poly(
        [
            [
                [offset, offset],
                [offset + 1, offset],
                [offset + 1, offset + 1],
                [offset, offset + 1],
                [offset, offset],
            ]
        ]
    )
    writer.record(subregion, 4.0, 1.0)


def test_extract_ercot_map_filters_erct_and_writes_geojson(tmp_path):
    source = tmp_path / "eGRID2023_Subregions.shp"
    output = tmp_path / "ercot_boundary.geojson"

    with shapefile.Writer(str(source), shapeType=shapefile.POLYGON) as writer:
        writer.field("Subregion", "C")
        writer.field("Shape_Leng", "F", decimal=11)
        writer.field("Shape_Area", "F", decimal=11)
        _write_subregion(writer, "ERCT", 0)
        _write_subregion(writer, "MROE", 10)

    source.with_suffix(".prj").write_text(
        'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984"]]',
        encoding="utf-8",
    )

    feature_count = extract_ercot_map(source, output)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert feature_count == 1
    assert payload["type"] == "FeatureCollection"
    assert payload["name"] == "egrid2023_subregion_erct"
    assert payload["crs"] == "EPSG:4326"
    assert payload["features"][0]["properties"]["Subregion"] == "ERCT"
    assert payload["features"][0]["geometry"]["type"] == "Polygon"


def test_cli_writes_default_output_to_current_directory(tmp_path):
    source = tmp_path / "eGRID2023_Subregions.shp"
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    with shapefile.Writer(str(source), shapeType=shapefile.POLYGON) as writer:
        writer.field("Subregion", "C")
        writer.field("Shape_Leng", "F", decimal=11)
        writer.field("Shape_Area", "F", decimal=11)
        _write_subregion(writer, "ERCT", 0)

    source.with_suffix(".prj").write_text(
        'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984"]]',
        encoding="utf-8",
    )

    subprocess.run(
        [sys.executable, SCRIPT_PATH, "--source", str(source)],
        check=True,
        cwd=output_dir,
    )

    assert (output_dir / "ercot_boundary.geojson").exists()


def test_extract_ercot_map_requires_complete_shapefile_bundle(tmp_path):
    source = tmp_path / "eGRID2023_Subregions.shp"

    with pytest.raises(FileNotFoundError, match="Missing required eGRID shapefile"):
        extract_ercot_map(source, tmp_path / "ercot_boundary.geojson")