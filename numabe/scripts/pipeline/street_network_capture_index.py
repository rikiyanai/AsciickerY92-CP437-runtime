#!/usr/bin/env python3
"""Retain Mapillary frames across an AOI street network, not one route spine.

The playable route remains a synchronization reference.  Eligibility and
coverage are owned by the AOI polygon and mapped OSM street/path segments.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from PIL import Image

from scripts.pipeline.mapillary_capture_source_index import (
    SCHEMA,
    SURVEY_SCHEMA,
    _canonical_bytes,
    _download,
    _sha256,
)
from scripts.pipeline.route_capture_manifest import RouteMeasure, _route_from_geojson
from scripts.pipeline.street_imagery_probe import haversine_m, point_in_ring


NETWORK_HIGHWAYS = {
    "primary", "secondary", "tertiary", "unclassified", "residential",
    "living_street", "service", "pedestrian", "footway", "path", "steps",
    "cycleway",
}


def _aoi_ring(path: Path, role: str = "aoi") -> list[list[float]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    features = [
        feature for feature in document.get("features", [])
        if (feature.get("properties") or {}).get("role") == role
    ]
    if len(features) != 1:
        raise ValueError(f"AOI must contain exactly one role={role!r} feature")
    geometry = features[0].get("geometry") or {}
    coordinates = geometry.get("coordinates") or []
    if geometry.get("type") != "Polygon" or not coordinates or len(coordinates[0]) < 4:
        raise ValueError("AOI feature must be a Polygon with an exterior ring")
    return [[float(point[0]), float(point[1])] for point in coordinates[0]]


def _xy(lon: float, lat: float, latitude_origin: float) -> tuple[float, float]:
    return (
        math.radians(lon) * 6371008.8 * math.cos(math.radians(latitude_origin)),
        math.radians(lat) * 6371008.8,
    )


def _project_segment(
    lon: float, lat: float, start: list[float], end: list[float], latitude_origin: float,
) -> tuple[float, float]:
    px, py = _xy(lon, lat, latitude_origin)
    ax, ay = _xy(start[0], start[1], latitude_origin)
    bx, by = _xy(end[0], end[1], latitude_origin)
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy)), t


def load_street_network(osm_path: Path, ring: list[list[float]]) -> list[dict[str, Any]]:
    root = ET.parse(osm_path).getroot()
    nodes = {
        element.attrib["id"]: [float(element.attrib["lon"]), float(element.attrib["lat"])]
        for element in root.findall("node")
    }
    latitude_origin = sum(point[1] for point in ring) / len(ring)
    segments: list[dict[str, Any]] = []
    for way in root.findall("way"):
        tags = {row.attrib["k"]: row.attrib["v"] for row in way.findall("tag")}
        highway = tags.get("highway")
        if highway not in NETWORK_HIGHWAYS or tags.get("access") == "no":
            continue
        refs = [row.attrib["ref"] for row in way.findall("nd") if row.attrib["ref"] in nodes]
        chainage = 0.0
        for segment_index, (left_ref, right_ref) in enumerate(zip(refs, refs[1:])):
            start, end = nodes[left_ref], nodes[right_ref]
            length_m = haversine_m(start[1], start[0], end[1], end[0])
            midpoint = [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2]
            if (
                point_in_ring(start[0], start[1], ring)
                or point_in_ring(end[0], end[1], ring)
                or point_in_ring(midpoint[0], midpoint[1], ring)
            ):
                segments.append({
                    "way_id": str(way.attrib["id"]),
                    "segment_index": segment_index,
                    "highway": highway,
                    "name": tags.get("name"),
                    "start_lonlat": start,
                    "end_lonlat": end,
                    "start_chainage_m": round(chainage, 3),
                    "length_m": round(length_m, 3),
                })
            chainage += length_m
    if not segments:
        raise ValueError("OSM extract has no AOI street/path segments")
    segments.sort(key=lambda row: (int(row["way_id"]), row["segment_index"]))
    return segments


def project_network(
    lon: float, lat: float, segments: list[dict[str, Any]], latitude_origin: float,
) -> dict[str, Any]:
    distance_m, fraction, segment = min(
        (
            (
                *_project_segment(
                    lon, lat, row["start_lonlat"], row["end_lonlat"], latitude_origin
                ),
                row,
            )
            for row in segments
        ),
        key=lambda item: (
            item[0], item[1], int(item[2]["way_id"]), item[2]["segment_index"]
        ),
    )
    return {
        "way_id": segment["way_id"],
        "segment_index": segment["segment_index"],
        "highway": segment["highway"],
        "name": segment["name"],
        "way_chainage_m": round(
            segment["start_chainage_m"] + fraction * segment["length_m"], 3
        ),
        "segment_fraction": round(fraction, 6),
        "lateral_m": round(distance_m, 3),
    }


def _spaced_rows(rows: list[dict[str, Any]], spacing_m: float) -> list[dict[str, Any]]:
    by_sequence: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_sequence.setdefault(str(row["picture"]["sequence_id"]), []).append(row)
    retained: list[dict[str, Any]] = []
    for sequence_id in sorted(by_sequence):
        ordered = sorted(
            by_sequence[sequence_id],
            key=lambda item: (
                str(item["picture"].get("capture_date") or ""),
                str(item["picture"]["picture_id"]),
            ),
        )
        previous = None
        for row in ordered:
            picture = row["picture"]
            if previous is None or haversine_m(
                previous["lat"], previous["lon"], picture["lat"], picture["lon"]
            ) >= spacing_m:
                retained.append(row)
                previous = picture
        if ordered and retained[-1] is not ordered[-1] and len(ordered) > 1:
            last = ordered[-1]
            if last not in retained:
                retained.append(last)
    return retained


def acquire_area(
    *, survey_path: Path, aoi_geojson_path: Path, osm_path: Path,
    route_path: Path, output_dir: Path, output_index: Path,
    max_street_lateral_m: float = 22.0, min_frame_spacing_m: float = 8.0,
    max_frames: int = 400, coverage_radius_m: float = 18.0, workers: int = 8,
) -> dict[str, Any]:
    survey = json.loads(survey_path.read_text(encoding="utf-8"))
    if survey.get("schema_version") != SURVEY_SCHEMA:
        raise ValueError(f"survey is not {SURVEY_SCHEMA}")
    provider = survey.get("providers", {}).get("mapillary") or {}
    if provider.get("state") != "PRESENT":
        raise ValueError("Mapillary survey does not contain present coverage")
    ring = _aoi_ring(aoi_geojson_path)
    latitude_origin = sum(point[1] for point in ring) / len(ring)
    segments = load_street_network(osm_path, ring)
    route_coordinates, _ = _route_from_geojson(route_path)
    route = RouteMeasure(route_coordinates)

    candidates = []
    for picture in provider.get("pictures", []):
        if not picture.get("asset_uri") or not picture.get("sequence_id"):
            continue
        lon, lat = float(picture["lon"]), float(picture["lat"])
        if not point_in_ring(lon, lat, ring):
            continue
        network_projection = project_network(lon, lat, segments, latitude_origin)
        if network_projection["lateral_m"] > max_street_lateral_m:
            continue
        route_projection = route.project(lon, lat)
        candidates.append({
            "picture": picture,
            "network_projection": network_projection,
            "route_projection": route_projection,
        })
    sampled = _spaced_rows(candidates, min_frame_spacing_m)
    sampled.sort(key=lambda row: (
        str(row["picture"]["sequence_id"]),
        str(row["picture"].get("capture_date") or ""),
        str(row["picture"]["picture_id"]),
    ))
    if max_frames > 0 and len(sampled) > max_frames:
        stride = len(sampled) / max_frames
        sampled = [sampled[min(len(sampled) - 1, int(index * stride))] for index in range(max_frames)]
    if not sampled:
        raise ValueError("survey has no downloadable frames on the AOI street network")

    user_agent = str(survey.get("user_agent") or "asciicker-Y9-2-area-acquisition/1")
    downloaded: dict[str, Path] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(_download, row["picture"], output_dir, user_agent): str(row["picture"]["picture_id"])
            for row in sampled
        }
        for future in as_completed(futures):
            picture_id, path = future.result()
            downloaded[picture_id] = path

    sequence_ordinals: dict[str, int] = {}
    captures = []
    for item in sampled:
        row = item["picture"]
        picture_id = str(row["picture_id"])
        path = downloaded[picture_id]
        with Image.open(path) as image:
            width, height = image.size
        sequence_id = str(row["sequence_id"])
        sequence_index = sequence_ordinals.get(sequence_id, 0)
        sequence_ordinals[sequence_id] = sequence_index + 1
        author = str(row.get("author") or "Mapillary contributor")
        page_uri = str(row.get("page_uri") or f"https://www.mapillary.com/app/?pKey={picture_id}")
        captures.append({
            "capture_id": f"mapillary_{picture_id}",
            "provider": "Mapillary",
            "source_uri": page_uri,
            "asset_uri": row["asset_uri"],
            "timestamp": row.get("capture_date"),
            "sequence_id": sequence_id,
            "sequence_index": sequence_index,
            "lon": float(row["lon"]), "lat": float(row["lat"]),
            "altitude_m": None,
            "horizontal_accuracy_m": None,
            "vertical_accuracy_m": None,
            "heading_deg": row.get("heading_deg"),
            "pitch_deg": None, "roll_deg": None,
            "orientation_state": "heading_provider_pitch_roll_unavailable",
            "license": "CC-BY-SA-4.0",
            "license_uri": "https://creativecommons.org/licenses/by-sa/4.0/",
            "attribution": f"{author} / Mapillary, CC BY-SA 4.0",
            "width": width, "height": height,
            "camera_model": str(row.get("camera") or row.get("extra", {}).get("camera_type") or "Mapillary camera"),
            "projection_model": str(row.get("extra", {}).get("camera_type") or "unknown"),
            "source_dimensions_px": [int(row.get("width") or width), int(row.get("height") or height)],
            "focal_length_mm": None, "focal_35mm": None, "horizontal_fov_deg": None,
            "intrinsics_state": "unknown_mapillary_thumbnail",
            "sha256": _sha256(path), "size_bytes": path.stat().st_size,
            "local_path": str(path.resolve().relative_to(output_index.parent.resolve())),
            "retention_permitted": True,
            "provider_detections": [],
            "provider_detection_state": "available_not_fetched",
            "acquisition_area_membership": {"inside_aoi": True},
            "street_network_projection": item["network_projection"],
            "acquisition_route_projection": {
                "chainage_m": round(item["route_projection"].chainage_m, 3),
                "lateral_m": round(item["route_projection"].lateral_m, 3),
            },
        })

    covered_by_way: dict[str, list[tuple[float, float]]] = {}
    for capture in captures:
        projection = capture["street_network_projection"]
        covered_by_way.setdefault(projection["way_id"], []).append((capture["lon"], capture["lat"]))
    network_length = sum(row["length_m"] for row in segments)
    covered_length = 0.0
    way_rows = []
    for way_id in sorted({row["way_id"] for row in segments}, key=int):
        way_segments = [row for row in segments if row["way_id"] == way_id]
        length = sum(row["length_m"] for row in way_segments)
        cameras = covered_by_way.get(way_id, [])
        covered = 0.0
        for segment in way_segments:
            sample_count = max(1, math.ceil(segment["length_m"] / 5.0))
            covered_samples = 0
            for sample_index in range(sample_count):
                fraction = (sample_index + 0.5) / sample_count
                start, end = segment["start_lonlat"], segment["end_lonlat"]
                sample_lon = start[0] + (end[0] - start[0]) * fraction
                sample_lat = start[1] + (end[1] - start[1]) * fraction
                if any(
                    haversine_m(sample_lat, sample_lon, camera_lat, camera_lon)
                    <= coverage_radius_m
                    for camera_lon, camera_lat in cameras
                ):
                    covered_samples += 1
            covered += segment["length_m"] * covered_samples / sample_count
        covered_length += covered
        way_rows.append({
            "way_id": way_id,
            "highway": way_segments[0]["highway"],
            "name": way_segments[0]["name"],
            "length_m": round(length, 3),
            "capture_count": len(cameras),
            "camera_proximity_covered_m": round(covered, 3),
            "camera_proximity_coverage_fraction": round(covered / length, 6),
        })
    document = {
        "schema": SCHEMA,
        "source_index_id": "mapillary_aoi_street_network_capture",
        "source_survey": {"path": str(survey_path), "sha256": _sha256(survey_path)},
        "policy": {
            "selection_owner": "AOI polygon plus mapped OSM street/path network",
            "route_role": "synchronization spine only; not an eligibility filter",
            "max_street_lateral_m": max_street_lateral_m,
            "min_frame_spacing_m": min_frame_spacing_m,
            "max_frames": max_frames,
            "coverage_radius_m": coverage_radius_m,
            "license": "CC-BY-SA-4.0",
            "token_retained": False,
        },
        "area_coverage": {
            "aoi": {"path": str(aoi_geojson_path), "sha256": _sha256(aoi_geojson_path), "ring_lonlat": ring},
            "osm": {"path": str(osm_path), "sha256": _sha256(osm_path)},
            "street_network_length_m": round(network_length, 3),
            "camera_proximity_covered_length_m": round(covered_length, 3),
            "camera_proximity_coverage_fraction": round(covered_length / network_length, 6),
            "coverage_semantics": "five-metre network samples within coverage_radius_m of a retained camera assigned to the same OSM way; not facade visibility",
            "ways": way_rows,
            "segments": segments,
        },
        "counts": {
            "survey_pictures": len(provider.get("pictures", [])),
            "pictures_inside_aoi": sum(
                point_in_ring(float(row["lon"]), float(row["lat"]), ring)
                for row in provider.get("pictures", [])
            ),
            "street_network_candidates": len(candidates),
            "eligible_area_frames": len(captures),
            "sequences": len(sequence_ordinals),
            "street_ways": len(way_rows),
            "street_segments": len(segments),
        },
        "captures": captures,
    }
    output_index.parent.mkdir(parents=True, exist_ok=True)
    output_index.write_bytes(_canonical_bytes(document))
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--survey", type=Path, required=True)
    parser.add_argument("--aoi", type=Path, required=True)
    parser.add_argument("--osm", type=Path, required=True)
    parser.add_argument("--route", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-street-lateral-m", type=float, default=22.0)
    parser.add_argument("--min-frame-spacing-m", type=float, default=8.0)
    parser.add_argument("--max-frames", type=int, default=400)
    parser.add_argument("--coverage-radius-m", type=float, default=18.0)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    document = acquire_area(
        survey_path=args.survey, aoi_geojson_path=args.aoi, osm_path=args.osm,
        route_path=args.route, output_dir=args.output_dir, output_index=args.out,
        max_street_lateral_m=args.max_street_lateral_m,
        min_frame_spacing_m=args.min_frame_spacing_m, max_frames=args.max_frames,
        coverage_radius_m=args.coverage_radius_m, workers=args.workers,
    )
    print(
        "[street-network-capture-index] "
        f"frames={document['counts']['eligible_area_frames']} "
        f"ways={document['counts']['street_ways']} "
        f"sequences={document['counts']['sequences']}"
    )
    print(f"[street-network-capture-index] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
