#!/usr/bin/env python3
"""Associate facade image masks with canonical building faces.

Image detections enter World IR as observations, not world geometry.  Their
capture pose and horizontal image coordinate nevertheless define a sight ray.
This stage intersects that ray with canonical building footprints and records
the nearest visible parent building.  It does not invent a facade: it only
binds an observed colour sample to the building the camera was facing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

from scripts.pipeline import geo_frames as GF


SCHEMA = "y92.facade_evidence_associations.v1"
MAX_RAY_M = 80.0


def _load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _cross(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def _ray_edge_distance(
    ox: float, oy: float, dx: float, dy: float,
    ax: float, ay: float, bx: float, by: float,
) -> float | None:
    """Return forward ray distance to one segment, or None."""
    sx, sy = bx - ax, by - ay
    denom = _cross(dx, dy, sx, sy)
    if abs(denom) <= 1e-10:
        return None
    qx, qy = ax - ox, ay - oy
    distance = _cross(qx, qy, sx, sy) / denom
    edge_fraction = _cross(qx, qy, dx, dy) / denom
    if distance < 0.0 or distance > MAX_RAY_M:
        return None
    if edge_fraction < -1e-9 or edge_fraction > 1.0 + 1e-9:
        return None
    return distance


def _nearest_building(
    ox: float, oy: float, heading_deg: float,
    buildings: list[tuple[str, list[list[float]]]],
) -> tuple[str, int, float] | None:
    heading = math.radians(heading_deg)
    dx, dy = math.sin(heading), math.cos(heading)
    best = None
    for feature_id, ring in buildings:
        points = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
        for face_index, (first, second) in enumerate(
                zip(points, [*points[1:], points[0]])):
            distance = _ray_edge_distance(
                ox, oy, dx, dy,
                float(first[0]), float(first[1]),
                float(second[0]), float(second[1]))
            if distance is not None and (best is None or distance < best[2]):
                best = (feature_id, face_index, distance)
    return best


def _median_rgb(rows: list[dict]) -> list[int]:
    channels = []
    for index in range(3):
        values = sorted(int(row["rgb"][index]) for row in rows)
        channels.append(values[len(values) // 2])
    return channels


def build(canonical_path: Path, image_paths: list[Path]) -> dict:
    canonical = _load(canonical_path)
    buildings = []
    for feature in canonical.get("canonical_features", []):
        geometry = feature.get("geometry") or {}
        if (feature.get("semantic_class") == "building_footprint"
                and geometry.get("kind") == "polygon"
                and len(geometry.get("coordinates") or []) >= 3):
            buildings.append((str(feature["feature_id"]),
                              geometry["coordinates"]))

    associated: dict[str, list[dict]] = defaultdict(list)
    counts = defaultdict(int)
    for image_path in image_paths:
        document = _load(image_path)
        observations = document.get("source_observations", [])
        captures = {
            str(row["observation_id"]): (row.get("attributes") or {}).get(
                "capture")
            for row in observations
            if str(row.get("observation_id") or "").startswith("capture:")
        }
        for observation in observations:
            attributes = observation.get("attributes") or {}
            segmentation = attributes.get("segmentation") or {}
            candidates = segmentation.get("semantic_candidates") or []
            rgb = segmentation.get("source_palette", {}).get("median_rgb") or []
            if "facade_wall" not in candidates:
                continue
            counts["facade_masks"] += 1
            if (int(segmentation.get("mask_area_px") or 0) < 1000
                    or float(segmentation.get("segmentation_score") or 0.0) < 0.8
                    or len(rgb) != 3
                    or not all(48 <= float(channel) <= 240 for channel in rgb)
                    or max(rgb) - min(rgb) > 65):
                counts["rejected_colour_contract"] += 1
                continue
            capture = captures.get(str(attributes.get(
                "capture_observation_id") or ""))
            region = attributes.get("image_region") or {}
            bbox = region.get("bbox_xyxy_normalized") or []
            if not isinstance(capture, dict) or len(bbox) != 4:
                counts["missing_camera_or_region"] += 1
                continue
            try:
                easting, northing = GF.utm_forward(
                    float(capture["lon"]), float(capture["lat"]))
                centre_x = (float(bbox[0]) + float(bbox[2])) / 2.0
                heading = (float(capture["heading_deg"])
                           + (centre_x - 0.5)
                           * float(capture["horizontal_fov_deg"])) % 360.0
            except (KeyError, TypeError, ValueError):
                counts["missing_camera_or_region"] += 1
                continue
            hit = _nearest_building(
                easting, northing, heading, buildings)
            if hit is None:
                counts["ray_miss"] += 1
                continue
            feature_id, face_index, distance_m = hit
            quantized_rgb = [
                min(248, int(round(float(channel) / 8.0)) * 8)
                for channel in rgb]
            associated[feature_id].append({
                "observation_id": observation["observation_id"],
                "digest": observation["digest"],
                "capture_id": capture["capture_id"],
                "canonical_face_id": f"{feature_id}#f{face_index}",
                "ray_distance_m": round(distance_m, 3),
                "segmentation_score": float(
                    segmentation["segmentation_score"]),
                "mask_area_px": int(segmentation["mask_area_px"]),
                "rgb": quantized_rgb,
                "association_method": (
                    "capture_heading_plus_mask_centroid_nearest_"
                    "canonical_building_intersection"),
            })
            counts["associated"] += 1

    building_rows = {}
    for feature_id, rows in sorted(associated.items()):
        ordered = sorted(
            rows,
            key=lambda row: (-row["segmentation_score"],
                             -row["mask_area_px"], row["observation_id"]))
        building_rows[feature_id] = {
            "wall_rgb": _median_rgb(ordered),
            "source_observations": [row["observation_id"] for row in ordered],
            "source_digests": [row["digest"] for row in ordered],
            "associations": ordered,
            "colour_method": "direct camera-ray canonical-building association",
        }
    counts["buildings_with_direct_colour"] = len(building_rows)
    return {
        "schema": SCHEMA,
        "inputs": {
            "canonical_world_ir": {
                "path": str(Path(canonical_path).resolve()),
                "sha256": _sha256(canonical_path),
            },
            "image_world_ir": [
                {"path": str(Path(path).resolve()), "sha256": _sha256(path)}
                for path in image_paths],
        },
        "counts": dict(sorted(counts.items())),
        "buildings": building_rows,
    }


def write(canonical_path: Path, image_paths: list[Path], out_path: Path) -> dict:
    document = build(canonical_path, image_paths)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n", encoding="utf-8")
    return document


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--image-world-ir", type=Path, action="append",
                        required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    document = write(args.canonical, args.image_world_ir, args.out)
    print(json.dumps(document["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
