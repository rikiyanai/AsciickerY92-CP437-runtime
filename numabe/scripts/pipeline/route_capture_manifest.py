#!/usr/bin/env python3
"""Build the reconstruction capture contract for a route-shaped world AOI.

``street_imagery_probe.py`` owns provider availability.  This module owns the
next boundary: converting permitted, normalized camera records into the exact
route corpus offered to SfM.  It measures coverage along the requested route,
selects deterministic keyframes, records an overlap graph, and leaves every
gap explicit.  It does not download pixels or claim that metadata is a solved
reconstruction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


SCHEMA = "y92.route_capture_manifest.v1"
SOURCE_SCHEMA = "y92.capture_source_index.v1"
EARTH_RADIUS_M = 6_371_008.8


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _local_xy(lon: float, lat: float, origin_lon: float, origin_lat: float) -> tuple[float, float]:
    lat_scale = math.pi * EARTH_RADIUS_M / 180.0
    lon_scale = lat_scale * math.cos(math.radians(origin_lat))
    return ((lon - origin_lon) * lon_scale, (lat - origin_lat) * lat_scale)


def _route_from_geojson(path: Path) -> tuple[list[list[float]], dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    routes = [
        feature for feature in document.get("features", [])
        if feature.get("properties", {}).get("role")
        in {"walking_route", "walking_corridor", "connected_playable_route"}
    ]
    if len(routes) != 1:
        raise ValueError(f"expected exactly one walking route in {path}, found {len(routes)}")
    coordinates = routes[0].get("geometry", {}).get("coordinates", [])
    if routes[0].get("geometry", {}).get("type") != "LineString" or len(coordinates) < 2:
        raise ValueError("walking route must be a LineString with at least two points")
    return coordinates, routes[0].get("properties", {})


@dataclass(frozen=True)
class RouteProjection:
    chainage_m: float
    lateral_m: float


class RouteMeasure:
    def __init__(self, coordinates: Sequence[Sequence[float]]):
        self.origin_lon = float(coordinates[0][0])
        self.origin_lat = float(coordinates[0][1])
        self.points = [
            _local_xy(float(point[0]), float(point[1]), self.origin_lon, self.origin_lat)
            for point in coordinates
        ]
        self.cumulative = [0.0]
        for left, right in zip(self.points, self.points[1:]):
            self.cumulative.append(self.cumulative[-1] + math.dist(left, right))

    @property
    def length_m(self) -> float:
        return self.cumulative[-1]

    def project(self, lon: float, lat: float) -> RouteProjection:
        point = _local_xy(lon, lat, self.origin_lon, self.origin_lat)
        best_distance = float("inf")
        best_chainage = 0.0
        for index, (left, right) in enumerate(zip(self.points, self.points[1:])):
            dx, dy = right[0] - left[0], right[1] - left[1]
            denom = dx * dx + dy * dy
            fraction = 0.0 if denom == 0 else max(
                0.0,
                min(1.0, ((point[0] - left[0]) * dx + (point[1] - left[1]) * dy) / denom),
            )
            closest = (left[0] + fraction * dx, left[1] + fraction * dy)
            distance = math.dist(point, closest)
            if distance < best_distance:
                best_distance = distance
                best_chainage = self.cumulative[index] + fraction * math.sqrt(denom)
        return RouteProjection(best_chainage, best_distance)


def _require(record: dict[str, Any], names: Iterable[str]) -> list[str]:
    return [name for name in names if record.get(name) in (None, "", [])]


def _require_keys(record: dict[str, Any], names: Iterable[str]) -> list[str]:
    return [name for name in names if name not in record]


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value.lower())


def _turn_delta(previous: dict[str, Any], current: dict[str, Any]) -> float:
    a, b = previous.get("heading_deg"), current.get("heading_deg")
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return 0.0
    difference = abs((float(b) - float(a)) % 360.0)
    return min(difference, 360.0 - difference)


def _select_keyframes(
    records: list[dict[str, Any]], *, min_spacing_m: float, max_spacing_m: float,
    turn_threshold_deg: float,
) -> list[str]:
    selected: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda row: (
        row["sequence_id"], row.get("sequence_index", 10**12), row.get("timestamp", ""), row["capture_id"]
    )):
        if not selected or selected[-1]["sequence_id"] != record["sequence_id"]:
            selected.append(record)
            continue
        distance = math.hypot(
            record["route"]["chainage_m"] - selected[-1]["route"]["chainage_m"],
            record["route"]["lateral_m"] - selected[-1]["route"]["lateral_m"],
        )
        turn = _turn_delta(selected[-1], record)
        if distance >= min_spacing_m or turn >= turn_threshold_deg:
            selected.append(record)
    # Preserve the last usable frame of each sequence even when it is close to
    # the prior selection.  It is a loop/endpoint observation, not spacing fill.
    for sequence_id in sorted({row["sequence_id"] for row in records}):
        last = sorted(
            [row for row in records if row["sequence_id"] == sequence_id],
            key=lambda row: (row.get("sequence_index", 10**12), row.get("timestamp", ""), row["capture_id"]),
        )[-1]
        if last not in selected:
            selected.append(last)
    return [row["capture_id"] for row in sorted(selected, key=lambda row: row["capture_id"])]


def _merge_intervals(intervals: list[tuple[float, float]], length_m: float) -> list[list[float]]:
    clipped = sorted((max(0.0, start), min(length_m, end)) for start, end in intervals if end > 0 and start < length_m)
    merged: list[list[float]] = []
    for start, end in clipped:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [[round(start, 3), round(end, 3)] for start, end in merged]


def _gaps(covered: list[list[float]], length_m: float) -> list[list[float]]:
    gaps: list[list[float]] = []
    cursor = 0.0
    for start, end in covered:
        if start - cursor > 0.001:
            gaps.append([round(cursor, 3), round(start, 3)])
        cursor = max(cursor, end)
    if length_m - cursor > 0.001:
        gaps.append([round(cursor, 3), round(length_m, 3)])
    return gaps


def compile_manifest(
    route_path: Path,
    source_indexes: Sequence[Path],
    output_path: Path,
    *,
    max_lateral_m: float = 25.0,
    min_keyframe_spacing_m: float = 2.0,
    max_keyframe_spacing_m: float = 4.0,
    max_overlap_gap_m: float = 12.0,
    coverage_radius_m: float = 4.0,
    required_coverage_fraction: float = 0.95,
) -> dict[str, Any]:
    route_coordinates, route_properties = _route_from_geojson(route_path)
    route = RouteMeasure(route_coordinates)
    inputs: list[dict[str, Any]] = []
    source_receipts: list[dict[str, Any]] = []
    source_parent_by_capture: dict[str, Path] = {}
    for path in source_indexes:
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("schema") != SOURCE_SCHEMA:
            raise ValueError(f"{path} is not {SOURCE_SCHEMA}")
        rows = document.get("captures", [])
        inputs.extend(rows)
        for row in rows:
            source_parent_by_capture[str(row.get("capture_id", ""))] = path.parent
        source_receipts.append({"path": str(path), "sha256": _sha256(path), "captures": len(document.get("captures", []))})

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    ids: set[str] = set()
    required = (
        "capture_id", "provider", "source_uri", "asset_uri", "timestamp", "sequence_id",
        "lon", "lat", "license", "license_uri", "attribution", "width", "height",
        "camera_model", "sha256", "size_bytes",
    )
    required_nullable = (
        "altitude_m", "horizontal_accuracy_m", "vertical_accuracy_m", "heading_deg",
        "pitch_deg", "roll_deg", "orientation_state", "focal_length_mm", "focal_35mm",
        "horizontal_fov_deg", "intrinsics_state", "local_path", "provider_detections",
        "provider_detection_state",
    )
    for raw in inputs:
        record = dict(raw)
        capture_id = str(record.get("capture_id", ""))
        reasons = [f"missing:{name}" for name in _require(record, required)]
        reasons.extend(f"missing-key:{name}" for name in _require_keys(record, required_nullable))
        if capture_id in ids:
            reasons.append("duplicate:capture_id")
        ids.add(capture_id)
        if record.get("retention_permitted") is not True:
            reasons.append("retention_not_permitted")
        if not _valid_digest(record.get("sha256")):
            reasons.append("invalid:sha256")
        if reasons:
            rejected.append({"capture_id": capture_id or None, "reasons": sorted(set(reasons))})
            continue
        local_path = record.get("local_path")
        pixel_state = "remote_digest_frozen"
        if local_path:
            resolved = (source_parent_by_capture[capture_id] / str(local_path)).resolve()
            if not resolved.is_file():
                reasons.append("local_asset_missing")
            elif _sha256(resolved) != record["sha256"]:
                reasons.append("local_asset_digest_mismatch")
            elif resolved.stat().st_size != int(record["size_bytes"]):
                reasons.append("local_asset_size_mismatch")
            else:
                pixel_state = "verified_local"
        if reasons:
            rejected.append({"capture_id": capture_id, "reasons": sorted(set(reasons))})
            continue
        projection = route.project(float(record["lon"]), float(record["lat"]))
        record["route"] = {
            "chainage_m": round(projection.chainage_m, 3),
            "lateral_m": round(projection.lateral_m, 3),
        }
        record["inside_route_capture_band"] = projection.lateral_m <= max_lateral_m
        record["pixel_state"] = pixel_state
        if not record["inside_route_capture_band"]:
            rejected.append({"capture_id": capture_id, "reasons": ["outside_route_capture_band"]})
            continue
        accepted.append(record)

    accepted.sort(key=lambda row: (row["sequence_id"], row.get("sequence_index", 10**12), row["capture_id"]))
    selected_ids = set(_select_keyframes(
        accepted,
        min_spacing_m=min_keyframe_spacing_m,
        max_spacing_m=max_keyframe_spacing_m,
        turn_threshold_deg=20.0,
    ))
    for record in accepted:
        record["selected_keyframe"] = record["capture_id"] in selected_ids

    graph_edges: list[dict[str, Any]] = []
    by_sequence: dict[str, list[dict[str, Any]]] = {}
    for record in accepted:
        by_sequence.setdefault(record["sequence_id"], []).append(record)
    for sequence_id, rows in sorted(by_sequence.items()):
        rows.sort(key=lambda row: (row.get("sequence_index", 10**12), row.get("timestamp", ""), row["capture_id"]))
        for left, right in zip(rows, rows[1:]):
            dx = right["route"]["chainage_m"] - left["route"]["chainage_m"]
            dy = right["route"]["lateral_m"] - left["route"]["lateral_m"]
            separation = math.hypot(dx, dy)
            graph_edges.append({
                "sequence_id": sequence_id,
                "from": left["capture_id"],
                "to": right["capture_id"],
                "route_separation_m": round(separation, 3),
                "overlap_candidate": separation <= max_overlap_gap_m,
            })

    # A later SfM match may join two provider sequences.  Record only spatially
    # plausible cross-sequence candidates here; pixel matching still has to
    # prove them.  This prevents a folder of unrelated singleton photographs
    # from being called one continuous route corpus.
    for left_index, left in enumerate(accepted):
        for right in accepted[left_index + 1:]:
            if left["sequence_id"] == right["sequence_id"]:
                continue
            dx = right["route"]["chainage_m"] - left["route"]["chainage_m"]
            dy = right["route"]["lateral_m"] - left["route"]["lateral_m"]
            separation = math.hypot(dx, dy)
            if separation <= max_overlap_gap_m:
                graph_edges.append({
                    "sequence_id": None,
                    "from": left["capture_id"],
                    "to": right["capture_id"],
                    "route_separation_m": round(separation, 3),
                    "overlap_candidate": True,
                    "cross_sequence": True,
                })

    adjacency = {record["capture_id"]: set() for record in accepted}
    for edge in graph_edges:
        if edge["overlap_candidate"]:
            adjacency[edge["from"]].add(edge["to"])
            adjacency[edge["to"]].add(edge["from"])
    components: list[list[str]] = []
    unseen = set(adjacency)
    while unseen:
        seed_id = min(unseen)
        stack = [seed_id]
        component: list[str] = []
        unseen.remove(seed_id)
        while stack:
            capture_id = stack.pop()
            component.append(capture_id)
            for neighbor in sorted(adjacency[capture_id], reverse=True):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    stack.append(neighbor)
        components.append(sorted(component))

    by_id = {record["capture_id"]: record for record in accepted}
    component_reports = []
    for component in components:
        component_intervals = [
            (
                by_id[capture_id]["route"]["chainage_m"] - coverage_radius_m,
                by_id[capture_id]["route"]["chainage_m"] + coverage_radius_m,
            )
            for capture_id in component
            if len(component) >= 2
            and by_id[capture_id]["selected_keyframe"]
            and by_id[capture_id]["pixel_state"] == "verified_local"
        ]
        merged = _merge_intervals(component_intervals, route.length_m)
        component_reports.append({
            "capture_ids": component,
            "size": len(component),
            "covered_intervals_m": merged,
            "covered_m": round(sum(end - start for start, end in merged), 3),
        })
    component_reports.sort(key=lambda row: (-row["covered_m"], row["capture_ids"]))

    intervals = [
        (record["route"]["chainage_m"] - coverage_radius_m, record["route"]["chainage_m"] + coverage_radius_m)
        for record in accepted
        if record["selected_keyframe"] and record["pixel_state"] == "verified_local"
    ]
    aggregate_covered = _merge_intervals(intervals, route.length_m)
    covered = component_reports[0]["covered_intervals_m"] if component_reports else []
    gaps = _gaps(covered, route.length_m)
    covered_m = sum(end - start for start, end in covered)
    fraction = covered_m / route.length_m if route.length_m else 0.0
    longest_gap = max((end - start for start, end in gaps), default=0.0)
    full_route = (
        fraction >= required_coverage_fraction
        and longest_gap <= max_overlap_gap_m
        and bool(accepted)
        and bool(component_reports)
        and component_reports[0]["size"] >= 2
        and bool(covered and covered[0][0] <= 0.001)
        and bool(covered and route.length_m - covered[-1][1] <= 0.001)
    )

    document = {
        "schema": SCHEMA,
        "route": {
            "source": str(route_path),
            "source_sha256": _sha256(route_path),
            "declared_length_m": route_properties.get("length_m"),
            "measured_length_m": round(route.length_m, 3),
            "coordinates_lonlat": route_coordinates,
        },
        "policy": {
            "keyframe_spacing_m": [min_keyframe_spacing_m, max_keyframe_spacing_m],
            "max_lateral_m": max_lateral_m,
            "max_overlap_gap_m": max_overlap_gap_m,
            "coverage_radius_m": coverage_radius_m,
            "required_coverage_fraction": required_coverage_fraction,
        },
        "source_indexes": source_receipts,
        "captures": accepted,
        "rejected": sorted(rejected, key=lambda row: str(row.get("capture_id"))),
        "overlap_graph": {
            "nodes": [record["capture_id"] for record in accepted],
            "edges": graph_edges,
            "connected_candidate_edges": sum(edge["overlap_candidate"] for edge in graph_edges),
            "components": component_reports,
        },
        "coverage": {
            "covered_intervals_m": covered,
            "aggregate_covered_intervals_m": aggregate_covered,
            "aggregate_covered_m": round(sum(end - start for start, end in aggregate_covered), 3),
            "aggregate_coverage_fraction": round(
                sum(end - start for start, end in aggregate_covered) / route.length_m, 6
            ),
            "gaps_m": gaps,
            "covered_m": round(covered_m, 3),
            "coverage_fraction": round(fraction, 6),
            "longest_gap_m": round(longest_gap, 3),
            "home_endpoint_covered": bool(aggregate_covered and aggregate_covered[0][0] <= 0.001),
            "station_endpoint_covered": bool(
                aggregate_covered and route.length_m - aggregate_covered[-1][1] <= 0.001
            ),
            "primary_component_home_endpoint_covered": bool(covered and covered[0][0] <= 0.001),
            "primary_component_station_endpoint_covered": bool(
                covered and route.length_m - covered[-1][1] <= 0.001
            ),
            "full_route_reconstruction_ready": full_route,
        },
        "counts": {
            "input_captures": len(inputs),
            "accepted_captures": len(accepted),
            "selected_keyframes": len(selected_ids),
            "locally_verified_captures": sum(row["pixel_state"] == "verified_local" for row in accepted),
            "rejected_captures": len(rejected),
            "sequences": len(by_sequence),
            "overlap_candidate_components": len(component_reports),
        },
        "verdict": "READY" if full_route else "PARTIAL_ROUTE_COVERAGE",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(_canonical_bytes(document))
    return document


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--route", required=True, type=Path)
    parser.add_argument("--source-index", required=True, type=Path, action="append")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--max-lateral-m", type=float, default=25.0)
    args = parser.parse_args(argv)
    document = compile_manifest(
        args.route, args.source_index, args.out, max_lateral_m=args.max_lateral_m
    )
    coverage = document["coverage"]
    print(
        f"{document['verdict']} captures={document['counts']['accepted_captures']} "
        f"keyframes={document['counts']['selected_keyframes']} "
        f"coverage={coverage['coverage_fraction']:.3f} "
        f"longest_gap_m={coverage['longest_gap_m']:.3f}"
    )
    return 0 if coverage["full_route_reconstruction_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
