"""Build clipped ERCT Voronoi cells and persist compact area weights."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np
from pyproj import Transformer
from scipy.spatial import Voronoi
from shapely.geometry import Point, Polygon, shape
from shapely.ops import transform, unary_union

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATIONS = PROJECT_ROOT / "data/interim/station_metadata/station_metadata.parquet"
DEFAULT_BOUNDARY = PROJECT_ROOT / "data/interim/ercot_map/ercot_boundary.geojson"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/interim/voronoi_weights/voronoi_weights.parquet"
TARGET_CRS = "EPSG:3083"
SOURCE_CRS = "EPSG:4326"


def _voronoi_finite_polygons_2d(voronoi: Voronoi, radius: float) -> list[list[int]]:
    """Reconstruct finite 2D Voronoi regions from scipy's infinite regions."""
    if voronoi.points.shape[1] != 2:
        raise ValueError("Voronoi reconstruction requires two-dimensional points")

    center = voronoi.points.mean(axis=0)
    ridge_map: dict[int, list[tuple[int, int]]] = {}
    for (point_a, point_b), (vertex_a, vertex_b) in zip(voronoi.ridge_points, voronoi.ridge_vertices):
        ridge_map.setdefault(point_a, []).append((point_b, vertex_a, vertex_b))
        ridge_map.setdefault(point_b, []).append((point_a, vertex_a, vertex_b))

    regions: list[list[int]] = []
    vertices = voronoi.vertices.tolist()
    for point_index, region_index in enumerate(voronoi.point_region):
        region = voronoi.regions[region_index]
        if all(vertex >= 0 for vertex in region):
            region_vertices = [vertices[vertex] for vertex in region]
            region_center = np.mean(region_vertices, axis=0)
            ordered_region = sorted(
                region,
                key=lambda vertex: np.arctan2(
                    vertices[vertex][1] - region_center[1], vertices[vertex][0] - region_center[0]
                ),
            )
            regions.append(ordered_region)
            continue

        new_region = [vertex for vertex in region if vertex >= 0]
        for neighbor_index, vertex_a, vertex_b in ridge_map[point_index]:
            if vertex_b < 0:
                vertex_a, vertex_b = vertex_b, vertex_a
            if vertex_a >= 0:
                continue

            tangent = voronoi.points[neighbor_index] - voronoi.points[point_index]
            tangent /= (tangent**2).sum() ** 0.5
            normal = np.array([-tangent[1], tangent[0]])
            midpoint = voronoi.points[[point_index, neighbor_index]].mean(axis=0)
            direction = 1 if (midpoint - center) @ normal > 0 else -1
            far_point = voronoi.vertices[vertex_b] + direction * radius * normal
            vertices.append(far_point.tolist())
            new_region.append(len(vertices) - 1)

        region_vertices = [vertices[vertex] for vertex in new_region]
        region_center = np.mean(region_vertices, axis=0)
        new_region.sort(key=lambda vertex: np.arctan2(vertices[vertex][1] - region_center[1], vertices[vertex][0] - region_center[0]))
        regions.append(new_region)

    voronoi.vertices = np.asarray(vertices)
    return regions


def _configuration_id(station_ids: list[str]) -> str:
    digest = hashlib.sha256("\n".join(station_ids).encode("utf-8")).hexdigest()
    return f"all-stations-{digest[:16]}"


def _read_boundary(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("crs") not in {None, SOURCE_CRS}:
        raise ValueError(f"Expected boundary CRS {SOURCE_CRS}, found {payload.get('crs')}")
    geometries = [shape(feature["geometry"]) for feature in payload["features"]]
    boundary = unary_union(geometries)
    if boundary.is_empty or not boundary.is_valid:
        raise ValueError("ERCT boundary is empty or invalid")
    return boundary


def build_voronoi_weights(stations_path: Path, boundary_path: Path, output_path: Path) -> pd.DataFrame:
    """Build projected, clipped Voronoi area weights for all station metadata."""
    stations = pd.read_parquet(stations_path)
    required = {"station_id", "longitude_wgs84", "latitude_wgs84"}
    missing = sorted(required - set(stations.columns))
    if missing:
        raise ValueError(f"Station metadata is missing columns: {', '.join(missing)}")
    stations = stations.sort_values("station_id", ignore_index=True)
    if stations["station_id"].duplicated().any():
        raise ValueError("Station metadata contains duplicate station IDs")
    if len(stations) < 4:
        raise ValueError("At least four stations are required for a 2D Voronoi diagram")

    boundary_wgs84 = _read_boundary(boundary_path)
    project = Transformer.from_crs(SOURCE_CRS, TARGET_CRS, always_xy=True).transform
    boundary = transform(project, boundary_wgs84)
    points = [Point(float(row.longitude_wgs84), float(row.latitude_wgs84)) for row in stations.itertuples()]
    projected_points = [transform(project, point) for point in points]
    coordinates = [[point.x, point.y] for point in projected_points]
    coordinate_columns = ["longitude_wgs84", "latitude_wgs84"]
    stations["station_aliases"] = stations["station_id"]
    stations = (
        stations.groupby(coordinate_columns, as_index=False, sort=True)
        .agg(
            station_id=("station_id", "first"),
            station_aliases=("station_aliases", lambda values: ",".join(sorted(values))),
        )
    )
    points = [Point(float(row.longitude_wgs84), float(row.latitude_wgs84)) for row in stations.itertuples()]
    projected_points = [transform(project, point) for point in points]
    coordinates = [[point.x, point.y] for point in projected_points]
    voronoi = Voronoi(coordinates)
    span = max(boundary.bounds[2] - boundary.bounds[0], boundary.bounds[3] - boundary.bounds[1])
    regions = _voronoi_finite_polygons_2d(voronoi, radius=span * 10)

    configuration_id = _configuration_id(stations["station_id"].astype(str).tolist())
    boundary_area = boundary.area
    records: list[dict[str, Any]] = []
    for row, region in zip(stations.itertuples(), regions):
        polygon = Polygon(voronoi.vertices[region])
        clipped = polygon.intersection(boundary)
        area = float(clipped.area) if not clipped.is_empty else 0.0
        records.append(
            {
                "configuration_id": configuration_id,
                "station_id": row.station_id,
                "station_aliases": row.station_aliases,
                "cell_area_m2": area,
                "area_weight": area / boundary_area,
                "cell_intersects_erct": bool(area > 0),
            }
        )

    weights = pd.DataFrame(records)
    weights["configuration_area_sum_m2"] = boundary_area
    if not (weights["area_weight"] >= 0).all() or abs(weights["area_weight"].sum() - 1.0) > 1e-9:
        raise ValueError(f"Voronoi area weights do not sum to one: {weights['area_weight'].sum()}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    weights.to_parquet(output_path, index=False, engine="pyarrow", compression="zstd")
    return weights


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stations", type=Path, default=DEFAULT_STATIONS)
    parser.add_argument("--boundary", type=Path, default=DEFAULT_BOUNDARY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    weights = build_voronoi_weights(args.stations, args.boundary, args.output)
    print(f"Wrote {len(weights)} station weights to {args.output}; sum={weights['area_weight'].sum():.12f}")


if __name__ == "__main__":
    main()
