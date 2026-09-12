import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest
from shapely.geometry import mapping, box


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "build_voronoi_weights.py"
SPEC = importlib.util.spec_from_file_location("build_voronoi_weights", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"Unable to import Voronoi builder from {SCRIPT_PATH}")
build_voronoi_weights = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_voronoi_weights)


def _write_inputs(tmp_path):
    stations = pd.DataFrame(
        {
            "station_id": ["A", "B", "C", "D"],
            "longitude_wgs84": [-100.5, -99.5, -99.5, -100.5],
            "latitude_wgs84": [30.5, 30.5, 29.5, 29.5],
        }
    )
    stations_path = tmp_path / "stations.parquet"
    stations.to_parquet(stations_path, index=False)
    boundary_path = tmp_path / "boundary.geojson"
    boundary_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": "EPSG:4326",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": mapping(box(-101, 29, -99, 31)),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return stations_path, boundary_path


def test_build_voronoi_weights_clips_cells_and_sums_to_one(tmp_path):
    stations_path, boundary_path = _write_inputs(tmp_path)
    output_path = tmp_path / "weights.parquet"

    weights = build_voronoi_weights.build_voronoi_weights(stations_path, boundary_path, output_path)

    assert output_path.exists()
    assert len(weights) == 4
    assert weights["area_weight"].sum() == pytest.approx(1.0)
    assert (weights["cell_area_m2"] > 0).all()
    assert weights["configuration_id"].nunique() == 1
    assert pd.read_parquet(output_path).equals(weights)


def test_boundary_geometry_is_required_to_be_wgs84(tmp_path):
    stations_path, boundary_path = _write_inputs(tmp_path)
    payload = json.loads(boundary_path.read_text(encoding="utf-8"))
    payload["crs"] = "EPSG:3857"
    boundary_path.write_text(json.dumps(payload), encoding="utf-8")

    try:
        build_voronoi_weights.build_voronoi_weights(stations_path, boundary_path, tmp_path / "weights.parquet")
    except ValueError as error:
        assert "Expected boundary CRS EPSG:4326" in str(error)
    else:
        raise AssertionError("Expected a non-WGS84 boundary to fail")


def test_build_voronoi_weights_retains_coincident_station_aliases(tmp_path):
    stations_path, boundary_path = _write_inputs(tmp_path)
    stations = pd.read_parquet(stations_path)
    stations.loc[stations["station_id"] == "D", "longitude_wgs84"] = -99.5
    stations.loc[stations["station_id"] == "D", "latitude_wgs84"] = 30.5
    stations.to_parquet(stations_path, index=False)

    weights = build_voronoi_weights.build_voronoi_weights(
        stations_path, boundary_path, tmp_path / "weights.parquet"
    )

    assert len(weights) == 3
    assert weights.loc[weights["station_id"] == "B", "station_aliases"].iloc[0] == "B,D"
