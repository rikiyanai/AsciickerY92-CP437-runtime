#!/usr/bin/env python3
"""Compile a connected, source-backed product AOI from a routed spine.

The scope compiler is intentionally location-agnostic.  A JSON spec names a
frozen base AOI, a Valhalla pedestrian response, public mapped landmarks, and
operator-reported search zones.  The compiler emits one metric-buffered AOI
plus a receipt that refuses disconnected geometry, missing waypoints, or route
length disagreement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

from osgeo import ogr

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline import geo_frames as GF  # noqa: E402


SCHEMA = "y92.connected_product_scope.v1"
RECEIPT_SCHEMA = "y92.connected_product_scope_receipt.v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def decode_polyline6(encoded: str) -> list[tuple[float, float]]:
    """Decode a Valhalla encoded polyline to ``(lat, lon)`` coordinates."""
    coords: list[tuple[float, float]] = []
    index = lat = lon = 0
    while index < len(encoded):
        deltas: list[int] = []
        for _axis in range(2):
            result = shift = 0
            while True:
                if index >= len(encoded):
                    raise ValueError("truncated encoded polyline")
                value = ord(encoded[index]) - 63
                index += 1
                result |= (value & 0x1F) << shift
                shift += 5
                if value < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        coords.append((lat / 1_000_000.0, lon / 1_000_000.0))
    if len(coords) < 2:
        raise ValueError("route must contain at least two coordinates")
    return coords


def _map_coordinates(value: Any, transform) -> Any:
    if (
        isinstance(value, list)
        and len(value) >= 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], (int, float))
    ):
        return list(transform(float(value[0]), float(value[1])))
    if isinstance(value, list):
        return [_map_coordinates(item, transform) for item in value]
    return value


def _project_geometry(geometry: dict[str, Any]) -> ogr.Geometry:
    projected = dict(geometry)
    projected["coordinates"] = _map_coordinates(
        geometry["coordinates"], lambda lon, lat: GF.utm_forward(lon, lat)
    )
    result = ogr.CreateGeometryFromJson(json.dumps(projected))
    if result is None:
        raise ValueError("could not construct projected geometry")
    return result


def _unproject_geometry(geometry: ogr.Geometry) -> dict[str, Any]:
    projected = json.loads(geometry.ExportToJson())
    projected["coordinates"] = _map_coordinates(
        projected["coordinates"], lambda easting, northing: GF.utm_inverse(easting, northing)
    )
    return projected


def _point(lon: float, lat: float) -> ogr.Geometry:
    easting, northing = GF.utm_forward(lon, lat)
    point = ogr.Geometry(ogr.wkbPoint)
    point.AddPoint_2D(easting, northing)
    return point


def _distance_m(left_lonlat: tuple[float, float], right_lonlat: tuple[float, float]) -> float:
    return math.dist(GF.utm_forward(*left_lonlat), GF.utm_forward(*right_lonlat))


def _polyline_length_m(points_latlon: list[tuple[float, float]]) -> float:
    projected = [GF.utm_forward(lon, lat) for lat, lon in points_latlon]
    return sum(math.dist(left, right) for left, right in zip(projected, projected[1:]))


def _polygon_components(geometry: ogr.Geometry) -> int:
    name = geometry.GetGeometryName().upper()
    if name == "POLYGON":
        return 1
    if name == "MULTIPOLYGON":
        return geometry.GetGeometryCount()
    return 0


def _verify_annotation_snapshot(spec: dict[str, Any]) -> dict[str, Any]:
    annotations_path = (REPO_ROOT / spec["path"]).resolve()
    snapshot_root = annotations_path.parent.resolve()
    digest_matches = _sha256(annotations_path) == spec["frozen_annotations_sha256"]
    document = _read_json(annotations_path)
    rows = document.get("annotations") or []
    crops_verified = 0
    failures: list[str] = []
    for row in rows:
        annotation_id = row.get("annotation_id", "<missing>")
        crop = row.get("crop") or {}
        crop_ref = crop.get("path")
        if not isinstance(crop_ref, str) or Path(crop_ref).is_absolute():
            failures.append(f"{annotation_id}: crop path is not portable")
            continue
        crop_path = (snapshot_root / crop_ref).resolve()
        if not crop_path.is_relative_to(snapshot_root):
            failures.append(f"{annotation_id}: crop path escapes snapshot")
            continue
        if not crop_path.is_file() or _sha256(crop_path) != crop.get("sha256"):
            failures.append(f"{annotation_id}: crop missing or digest mismatch")
            continue
        crops_verified += 1
    count_matches = (
        len(rows) == int(spec["annotations"])
        and crops_verified == int(spec["crops"])
    )
    return {
        "digest_matches": digest_matches,
        "count_matches": count_matches,
        "annotations": len(rows),
        "crops_verified": crops_verified,
        "failures": failures[:20],
    }


def compile_scope(
    spec_path: Path,
    output_path: Path,
    receipt_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    spec = _read_json(spec_path)
    if spec.get("schema") != SCHEMA:
        raise ValueError(f"unsupported scope schema: {spec.get('schema')!r}")

    base_path = REPO_ROOT / spec["sources"]["base_aoi"]
    route_path = REPO_ROOT / spec["sources"]["pedestrian_route"]
    base_doc = _read_json(base_path)
    route_doc = _read_json(route_path)

    base_features = base_doc.get("features") or []
    base_aoi_features = [
        feature for feature in base_features
        if (feature.get("properties") or {}).get("role") == "aoi"
    ]
    if len(base_aoi_features) != 1:
        raise ValueError("base GeoJSON must contain exactly one role=aoi feature")
    base_aoi = _project_geometry(base_aoi_features[0]["geometry"])

    trip = route_doc.get("trip") or {}
    legs = trip.get("legs") or []
    if trip.get("status") != 0 or len(legs) != 1 or not isinstance(legs[0].get("shape"), str):
        raise ValueError("pedestrian route must be one successful encoded Valhalla leg")
    provider_route_latlon = decode_polyline6(legs[0]["shape"])
    route_latlon = provider_route_latlon
    route_composition: dict[str, Any] | None = None
    preserved_route = spec.get("preserved_base_route")
    if preserved_route is not None:
        role = preserved_route["feature_role"]
        matches = [
            feature
            for feature in base_features
            if (feature.get("properties") or {}).get("role") == role
        ]
        if len(matches) != 1:
            raise ValueError(f"expected exactly one base feature with role={role}")
        geometry = matches[0].get("geometry") or {}
        base_coordinates = geometry.get("coordinates")
        if (
            geometry.get("type") != "LineString"
            or not isinstance(base_coordinates, list)
            or len(base_coordinates) < 2
        ):
            raise ValueError("preserved base route must be a LineString")
        locations = trip.get("locations") or []
        from_index = int(preserved_route["from_provider_location_index"])
        to_index = int(preserved_route["to_provider_location_index"])
        if max(from_index, to_index) >= len(locations):
            raise ValueError("preserved base route refers to an absent provider location")
        from_lonlat = (float(locations[from_index]["lon"]), float(locations[from_index]["lat"]))
        to_lonlat = (float(locations[to_index]["lon"]), float(locations[to_index]["lat"]))
        forward = [(float(point[0]), float(point[1])) for point in base_coordinates]
        reverse = list(reversed(forward))
        oriented = min(
            (forward, reverse),
            key=lambda rows: _distance_m(rows[0], from_lonlat)
            + _distance_m(rows[-1], to_lonlat),
        )
        start_distance_m = _distance_m(oriented[0], from_lonlat)
        end_distance_m = _distance_m(oriented[-1], to_lonlat)
        max_endpoint_distance_m = float(preserved_route["max_endpoint_distance_m"])
        if max(start_distance_m, end_distance_m) > max_endpoint_distance_m:
            raise ValueError("preserved base route endpoints do not match provider waypoints")
        splice_index, splice_point = min(
            enumerate(provider_route_latlon),
            key=lambda item: _distance_m(
                (item[1][1], item[1][0]), to_lonlat
            ),
        )
        splice_distance_m = _distance_m(
            (splice_point[1], splice_point[0]), to_lonlat
        )
        if splice_distance_m > max_endpoint_distance_m:
            raise ValueError("provider shape has no point at preserved-route splice")
        preserved_latlon = [(lat, lon) for lon, lat in oriented]
        route_latlon = preserved_latlon + provider_route_latlon[splice_index + 1 :]
        provider_replaced_latlon = provider_route_latlon[: splice_index + 1]
        preserved_length_m = _polyline_length_m(preserved_latlon)
        provider_replaced_length_m = _polyline_length_m(provider_replaced_latlon)
        provider_length_m = float(trip["summary"]["length"]) * 1000.0
        composed_source_length_m = (
            provider_length_m - provider_replaced_length_m + preserved_length_m
        )
        route_composition = {
            "feature_role": role,
            "from_provider_location_index": from_index,
            "to_provider_location_index": to_index,
            "max_endpoint_distance_m": max_endpoint_distance_m,
            "start_endpoint_distance_m": round(start_distance_m, 3),
            "end_endpoint_distance_m": round(end_distance_m, 3),
            "splice_shape_index": splice_index,
            "splice_distance_m": round(splice_distance_m, 3),
            "preserved_base_route_length_m": round(preserved_length_m, 3),
            "provider_replaced_segment_length_m": round(provider_replaced_length_m, 3),
            "composed_source_length_m": round(composed_source_length_m, 3),
        }
    route_xy = [GF.utm_forward(lon, lat) for lat, lon in route_latlon]
    route = ogr.Geometry(ogr.wkbLineString)
    for easting, northing in route_xy:
        route.AddPoint_2D(easting, northing)

    buffers = spec["buffers_m"]
    lateral_buffer_m = float(buffers["route_lateral"])
    endpoint_buffer_m = float(buffers["route_endpoint"])
    quadrant_segments = int(buffers.get("quadrant_segments", 16))
    scope = base_aoi.Union(route.Buffer(lateral_buffer_m, quadrant_segments))
    scope = scope.Union(_point(route_latlon[0][1], route_latlon[0][0]).Buffer(
        endpoint_buffer_m, quadrant_segments
    ))
    scope = scope.Union(_point(route_latlon[-1][1], route_latlon[-1][0]).Buffer(
        endpoint_buffer_m, quadrant_segments
    ))

    cluster_features: list[dict[str, Any]] = []
    for zone in spec.get("operator_reported_search_zones", []):
        center = zone["center_lonlat"]
        radius_m = float(zone["radius_m"])
        zone_geometry = _point(center[0], center[1]).Buffer(radius_m, quadrant_segments)
        scope = scope.Union(zone_geometry)
        cluster_features.append({
            "type": "Feature",
            "properties": {
                "role": "operator_reported_landmark_search_zone",
                "zone_id": zone["zone_id"],
                "landmark_kind": zone["landmark_kind"],
                "location_state": zone["location_state"],
                "radius_m": radius_m,
                "note": zone["note"],
            },
            "geometry": _unproject_geometry(zone_geometry),
        })

    if hasattr(scope, "MakeValid"):
        scope = scope.MakeValid()
    components = _polygon_components(scope)
    route_length_m = sum(math.dist(a, b) for a, b in zip(route_xy, route_xy[1:]))
    provider_length_m = float(trip["summary"]["length"]) * 1000.0
    comparison_length_m = (
        float(route_composition["composed_source_length_m"])
        if route_composition is not None
        else provider_length_m
    )
    mapped_points = spec.get("mapped_landmarks", [])
    point_results = []
    for landmark in mapped_points:
        lon, lat = landmark["lonlat"]
        covered = bool(scope.Contains(_point(lon, lat)) or scope.Touches(_point(lon, lat)))
        point_results.append({"landmark_id": landmark["landmark_id"], "covered": covered})

    route_landmark_results: list[dict[str, Any]] = []
    preserved_route_landmarks: list[dict[str, Any]] = []
    for requirement in spec.get("route_landmarks", []):
        matches = [
            feature for feature in base_features
            if (feature.get("properties") or {}).get("role") == requirement["base_feature_role"]
            and (feature.get("properties") or {}).get("source_ref") == requirement["source_ref"]
        ]
        if len(matches) != 1:
            raise ValueError(
                f"expected one base feature for route landmark {requirement['landmark_id']}"
            )
        distances = [
            _point(lon, lat).Distance(route)
            for lon, lat in requirement["required_route_pass_points_lonlat"]
        ]
        limit = float(requirement["max_route_distance_m"])
        route_landmark_results.append({
            "landmark_id": requirement["landmark_id"],
            "source_ref": requirement["source_ref"],
            "route_distance_m": [round(value, 3) for value in distances],
            "max_route_distance_m": limit,
            "traversed": all(value <= limit for value in distances),
        })
        preserved_route_landmarks.append(matches[0])

    annotation_verification = _verify_annotation_snapshot(spec["annotation_snapshot"])

    checks = {
        "aoi_is_single_connected_polygon": components == 1,
        "route_intersects_frozen_base_aoi": bool(route.Intersects(base_aoi)),
        "route_is_inside_aoi": route.Difference(scope).Length() < 0.01,
        "mapped_landmarks_are_inside_aoi": all(row["covered"] for row in point_results),
        "required_route_landmarks_are_traversed": all(
            row["traversed"] for row in route_landmark_results
        ),
        "route_order_matches_provider_waypoints": (
            len(spec["route_order"]) == len(trip.get("locations") or [])
        ),
        "route_length_agrees_with_composed_sources": (
            abs(route_length_m - comparison_length_m) <= 5.0
        ),
        "preserved_base_route_is_exact_prefix": (
            route_composition is None
            or route_latlon[: len(preserved_latlon)] == preserved_latlon
        ),
        "coin_laundry_not_claimed_as_mapped": all(
            zone["location_state"] != "mapped_exact"
            for zone in spec.get("operator_reported_search_zones", [])
            if zone["landmark_kind"] == "coin_laundry"
        ),
        "annotation_snapshot_digest_matches": annotation_verification["digest_matches"],
        "annotation_snapshot_counts_match": annotation_verification["count_matches"],
        "annotation_snapshot_crops_verified": not annotation_verification["failures"],
    }
    passed = all(checks.values())

    features: list[dict[str, Any]] = [{
        "type": "Feature",
        "properties": {
            "role": "aoi",
            "scope_id": spec["scope_id"],
            "area_m2": round(scope.Area(), 3),
            "route_lateral_buffer_m": lateral_buffer_m,
            "route_endpoint_buffer_m": endpoint_buffer_m,
            "connected_components": components,
        },
        "geometry": _unproject_geometry(scope),
    }, {
        "type": "Feature",
        "properties": {
            "role": "connected_playable_route",
            "scope_id": spec["scope_id"],
            "length_m": round(route_length_m, 3),
            "provider": (
                "preserved frozen base route plus Valhalla pedestrian extension over OpenStreetMap"
                if route_composition is not None
                else "Valhalla pedestrian route over OpenStreetMap"
            ),
            "ordered_roles": spec["route_order"],
        },
        "geometry": {
            "type": "LineString",
            "coordinates": [[lon, lat] for lat, lon in route_latlon],
        },
    }]
    for landmark in mapped_points:
        features.append({
            "type": "Feature",
            "properties": {
                key: value for key, value in landmark.items() if key != "lonlat"
            } | {"role": "mapped_landmark"},
            "geometry": {"type": "Point", "coordinates": landmark["lonlat"]},
        })
    features.extend(preserved_route_landmarks)
    features.extend(cluster_features)

    output = {
        "type": "FeatureCollection",
        "name": spec["scope_id"],
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }
    envelope = scope.GetEnvelope()
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "scope_id": spec["scope_id"],
        "compiled_utc": spec["compiled_utc"],
        "passed": passed,
        "checks": checks,
        "metrics": {
            "aoi_area_m2": round(scope.Area(), 3),
            "aoi_connected_components": components,
            "route_points": len(route_latlon),
            "route_length_m": round(route_length_m, 3),
            "provider_route_length_m": provider_length_m,
            "composed_source_length_m": round(comparison_length_m, 3),
            "route_length_residual_m": round(abs(route_length_m - comparison_length_m), 3),
            "bbox_epsg32654": [round(envelope[0], 3), round(envelope[2], 3),
                               round(envelope[1], 3), round(envelope[3], 3)],
        },
        "mapped_landmark_coverage": point_results,
        "route_landmark_traversal": route_landmark_results,
        "route_composition": route_composition,
        "annotation_snapshot_verification": annotation_verification,
        "source_digests": {
            "spec_sha256": _sha256(spec_path),
            "base_aoi_sha256": _sha256(base_path),
            "pedestrian_route_sha256": _sha256(route_path),
        },
        "coverage_state": spec["coverage_state"],
        "annotation_snapshot": spec["annotation_snapshot"],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not passed:
        failed = ", ".join(name for name, value in checks.items() if not value)
        raise ValueError(f"connected product scope failed: {failed}")
    return output, receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    _, receipt = compile_scope(args.spec, args.output, args.receipt)
    print(json.dumps({
        "scope_id": receipt["scope_id"],
        "passed": receipt["passed"],
        **receipt["metrics"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
