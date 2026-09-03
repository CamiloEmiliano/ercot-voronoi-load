import json

import shapefile

from scripts.extract_texas_counties import extract_texas_counties


def _write_county(writer, statefp, geoid, name, offset):
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
    writer.record(statefp, geoid, name)


def test_extract_texas_counties_filters_texas_and_writes_geojson(tmp_path):
    source = tmp_path / "counties.shp"
    output = tmp_path / "texas_counties.geojson"

    with shapefile.Writer(str(source), shapeType=shapefile.POLYGON) as writer:
        writer.field("STATEFP", "C")
        writer.field("GEOID", "C")
        writer.field("NAME", "C")
        _write_county(writer, "48", "48001", "Anderson", 0)
        _write_county(writer, "06", "06001", "Alameda", 10)

    county_count = extract_texas_counties(source, output)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert county_count == 1
    assert payload["type"] == "FeatureCollection"
    assert payload["crs"] == "EPSG:4269"
    assert payload["features"][0]["properties"]["GEOID"] == "48001"
    assert payload["features"][0]["geometry"]["type"] == "Polygon"