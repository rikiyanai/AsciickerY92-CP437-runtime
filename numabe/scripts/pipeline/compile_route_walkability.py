#!/usr/bin/env python3
"""Restore and attest a Classic-player corridor through a direct A3D map.

The direct building bake represents gameplay mass on the A3D height lattice.
Near a narrow mapped footway, a footprint can legitimately miss the route
centre while one raised lattice vertex still interpolates into the player's
collision envelope.  This owner resolves that representation conflict against
the accepted pedestrian route: building-raised vertices and visual cells whose
support overlaps the Classic human envelope are restored from the DEM-only
terrain.  Presentation-only facade meshes continue to preserve the building
face and silhouette.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline import geo_frames as GF
from scripts.pipeline.bake_osm_building_footprints import (
    HEIGHT_STEP_WORLD,
    PATCH_WORLD,
    fmt,
    load_a3d,
    point_in_polygon,
    write_a3d,
)


# engine/physics.cpp:1051-1054:
# radius_cells / (3 * HEIGHT_CELLS) * VISUAL_CELLS.
CLASSIC_HUMAN_RADIUS_CELLS = 2.0
CLASSIC_RENDER_ZOOM = 3.0
CLASSIC_HUMAN_RADIUS_WORLD = (
    CLASSIC_HUMAN_RADIUS_CELLS
    / (CLASSIC_RENDER_ZOOM * float(fmt.HEIGHT_CELLS))
    * float(fmt.VISUAL_CELLS)
)
ROUTE_SAMPLE_STEP_WORLD = 0.5
MINIMUM_GROUND_NORMAL_Z = 0.8


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decode_polyline6(encoded: str) -> list[tuple[float, float]]:
    """Decode a Valhalla/Google encoded polyline at 1e-6 degree precision."""
    coords: list[tuple[float, float]] = []
    index = lat = lon = 0
    while index < len(encoded):
        values = []
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
            values.append(~(result >> 1) if result & 1 else result >> 1)
        lat += values[0]
        lon += values[1]
        coords.append((lat / 1_000_000.0, lon / 1_000_000.0))
    if len(coords) < 2:
        raise ValueError("pedestrian route must contain at least two coordinates")
    return coords


def _project_route(route_path: Path, metadata_path: Path) -> tuple[list[tuple[float, float]], dict]:
    route_doc = json.loads(route_path.read_text(encoding="utf-8"))
    legs = (route_doc.get("trip") or {}).get("legs") or []
    if len(legs) != 1 or not isinstance(legs[0].get("shape"), str):
        raise ValueError("route source must contain exactly one Valhalla encoded leg")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("projection_kind") != "epsg32654_affine":
        raise ValueError("route walkability currently requires the direct EPSG:32654 frame")
    origin_e = float(metadata["origin_easting"])
    origin_n = float(metadata["origin_northing"])
    scale = float(metadata["content_scale"])
    if abs(scale - GF.WORLD_UNITS_PER_METRE) > 1e-9:
        raise ValueError(f"route scale {scale} conflicts with canonical {GF.WORLD_UNITS_PER_METRE}")
    offset_x = float(metadata.get("offset_x", 0.0))
    offset_y = float(metadata.get("offset_y", 0.0))
    world = []
    for lat, lon in decode_polyline6(legs[0]["shape"]):
        easting, northing = GF.utm_forward(lon, lat)
        world.append(((easting - origin_e) * scale + offset_x,
                      (northing - origin_n) * scale + offset_y))
    return world, route_doc


def _point_segment_distance(px: float, py: float, a: tuple[float, float],
                            b: tuple[float, float]) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 <= 1e-18:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _distance_to_route(px: float, py: float, route: list[tuple[float, float]]) -> float:
    return min(_point_segment_distance(px, py, a, b) for a, b in zip(route, route[1:]))


def _patch_map(patches) -> dict[tuple[int, int], object]:
    return {(int(p.x), int(p.y)): p for p in patches}


def _height_lattice(patches) -> dict[tuple[int, int], list[tuple[object, int, int]]]:
    lattice: dict[tuple[int, int], list[tuple[object, int, int]]] = {}
    for patch in patches:
        for hy in range(fmt.HEIGHT_CELLS + 1):
            for hx in range(fmt.HEIGHT_CELLS + 1):
                key = (patch.x * fmt.HEIGHT_CELLS + hx,
                       patch.y * fmt.HEIGHT_CELLS + hy)
                lattice.setdefault(key, []).append((patch, hy, hx))
    return lattice


def _height_at(patches_by_xy, wx: float, wy: float) -> float | None:
    px = math.floor(wx / PATCH_WORLD)
    py = math.floor(wy / PATCH_WORLD)
    patch = patches_by_xy.get((px, py))
    if patch is None:
        return None
    u = (wx - px * PATCH_WORLD) / HEIGHT_STEP_WORLD
    v = (wy - py * PATCH_WORLD) / HEIGHT_STEP_WORLD
    x0 = max(0, min(fmt.HEIGHT_CELLS - 1, math.floor(u)))
    y0 = max(0, min(fmt.HEIGHT_CELLS - 1, math.floor(v)))
    fx, fy = u - x0, v - y0
    z00 = patch.height[y0][x0]
    z10 = patch.height[y0][x0 + 1]
    z01 = patch.height[y0 + 1][x0]
    z11 = patch.height[y0 + 1][x0 + 1]
    return (z00 * (1.0 - fx) * (1.0 - fy)
            + z10 * fx * (1.0 - fy)
            + z01 * (1.0 - fx) * fy
            + z11 * fx * fy)


def _route_samples(route: list[tuple[float, float]], radius: float):
    centerline = []
    corridor = []
    for a, b in zip(route, route[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            continue
        nx, ny = -dy / length, dx / length
        steps = max(1, math.ceil(length / ROUTE_SAMPLE_STEP_WORLD))
        for i in range(steps + 1):
            t = i / steps
            x, y = a[0] + t * dx, a[1] + t * dy
            centerline.append((x, y))
            for offset in (-radius, -radius * 0.5, 0.0, radius * 0.5, radius):
                corridor.append((x + nx * offset, y + ny * offset))
    return centerline, corridor


def compile_route_walkability(
    *, terrain_map: Path, building_map: Path, route_path: Path,
    terrain_metadata_path: Path, building_specs_path: Path,
    output_map: Path, receipt_path: Path,
) -> dict:
    terrain_map = Path(terrain_map)
    building_map = Path(building_map)
    output_map = Path(output_map)
    route, route_doc = _project_route(Path(route_path), Path(terrain_metadata_path))
    terrain_header, terrain_patches, _terrain_tail = load_a3d(terrain_map)
    built_header, built_patches, built_tail = load_a3d(building_map)
    _pre_header, pre_built_patches, _pre_tail = load_a3d(building_map)
    building_map_input_sha256 = _sha256(building_map)
    if terrain_header.num_patches != built_header.num_patches:
        raise ValueError("terrain/building A3D patch counts differ")
    terrain_by_xy = _patch_map(terrain_patches)
    built_by_xy = _patch_map(built_patches)
    pre_built_by_xy = _patch_map(pre_built_patches)
    if set(terrain_by_xy) != set(built_by_xy):
        raise ValueError("terrain/building A3D patch coordinates differ")

    radius = CLASSIC_HUMAN_RADIUS_WORLD
    # A height vertex affects four neighboring interpolation cells. Restore the
    # complete support of the player's corridor, rather than merely vertices
    # whose point coordinate falls inside the sphere radius.
    height_support = radius + math.sqrt(2.0) * HEIGHT_STEP_WORLD
    visual_support = radius + math.sqrt(0.5)

    terrain_lattice = _height_lattice(terrain_patches)
    built_lattice = _height_lattice(built_patches)
    restored_keys = []
    for key, entries in built_lattice.items():
        terrain_entries = terrain_lattice[key]
        terrain_values = {entry[0].height[entry[1]][entry[2]] for entry in terrain_entries}
        built_values = {entry[0].height[entry[1]][entry[2]] for entry in entries}
        if len(terrain_values) != 1 or len(built_values) != 1:
            raise ValueError(f"A3D seam mismatch at height lattice key {key}")
        terrain_value = next(iter(terrain_values))
        built_value = next(iter(built_values))
        if built_value <= terrain_value:
            continue
        wx, wy = key[0] * HEIGHT_STEP_WORLD, key[1] * HEIGHT_STEP_WORLD
        if _distance_to_route(wx, wy, route) <= height_support:
            for patch, hy, hx in entries:
                patch.height[hy][hx] = terrain_value
            restored_keys.append(key)

    visual_cells_restored = 0
    for key, patch in built_by_xy.items():
        terrain_patch = terrain_by_xy[key]
        for vy in range(fmt.VISUAL_CELLS):
            for vx in range(fmt.VISUAL_CELLS):
                wx = patch.x * PATCH_WORLD + vx + 0.5
                wy = patch.y * PATCH_WORLD + vy + 0.5
                if (_distance_to_route(wx, wy, route) <= visual_support
                        and patch.visual[vy][vx] != terrain_patch.visual[vy][vx]):
                    patch.visual[vy][vx] = terrain_patch.visual[vy][vx]
                    visual_cells_restored += 1

    original_tail_hash = hashlib.sha256(built_tail).hexdigest()
    write_a3d(output_map, built_header, built_patches, built_tail)
    _out_header, out_patches, out_tail = load_a3d(output_map)
    if hashlib.sha256(out_tail).hexdigest() != original_tail_hash:
        raise ValueError("route walkability lowering changed the A3D post-patch payload")
    out_by_xy = _patch_map(out_patches)

    centerline, corridor = _route_samples(route, radius)
    missing_samples = 0
    pre_blocked_samples = post_blocked_samples = 0
    pre_max_delta = post_max_delta = 0.0
    for wx, wy in corridor:
        baseline = _height_at(terrain_by_xy, wx, wy)
        before = _height_at(pre_built_by_xy, wx, wy)
        after = _height_at(out_by_xy, wx, wy)
        if baseline is None or before is None or after is None:
            missing_samples += 1
            continue
        pre_delta = max(0.0, before - baseline)
        post_delta = max(0.0, after - baseline)
        pre_max_delta = max(pre_max_delta, pre_delta)
        post_max_delta = max(post_max_delta, post_delta)
        pre_blocked_samples += pre_delta > 1e-6
        post_blocked_samples += post_delta > 1e-6

    center_heights = [_height_at(out_by_xy, x, y) for x, y in centerline]
    max_grade = 0.0
    for (a, za), (b, zb) in zip(zip(centerline, center_heights), zip(centerline[1:], center_heights[1:])):
        if za is None or zb is None:
            continue
        distance = math.dist(a, b)
        if distance > 1e-9:
            max_grade = max(max_grade, abs(zb - za) / GF.BAKE_UNITS_PER_WORLD_UNIT / distance)
    minimum_normal_z = 1.0 / math.sqrt(1.0 + max_grade * max_grade)

    # Identify the OSM masses whose rasterized footprint competes with the
    # accepted path. This is conflict evidence, not a claim that OSM is wrong.
    restored_world = [
        (key[0] * HEIGHT_STEP_WORLD, key[1] * HEIGHT_STEP_WORLD)
        for key in restored_keys
    ]
    affected = []
    for spec in json.loads(Path(building_specs_path).read_text(encoding="utf-8")):
        polygon = [(float(p[0]), float(p[1])) for p in spec.get("footprint") or []]
        if len(polygon) >= 3 and any(
            point_in_polygon(wx, wy, polygon) for wx, wy in restored_world
        ):
            affected.append(str(spec.get("source") or spec.get("feature_id")))

    route_length_world = sum(math.dist(a, b) for a, b in zip(route, route[1:]))
    seams: dict[tuple[int, int], int] = {}
    seam_mismatches = 0
    for patch in out_patches:
        for hy in range(fmt.HEIGHT_CELLS + 1):
            for hx in range(fmt.HEIGHT_CELLS + 1):
                key = (patch.x * fmt.HEIGHT_CELLS + hx, patch.y * fmt.HEIGHT_CELLS + hy)
                value = patch.height[hy][hx]
                if key in seams and seams[key] != value:
                    seam_mismatches += 1
                seams[key] = value

    collision_pass = post_blocked_samples == 0 and missing_samples == 0
    walkability_pass = (
        collision_pass and missing_samples == 0 and seam_mismatches == 0
        and minimum_normal_z >= MINIMUM_GROUND_NORMAL_Z
    )
    receipt = {
        "schema": "y92.classic_route_walkability.v1",
        "stage": "executed_static_artifact_attested",
        "inputs": {
            "terrain_map": str(terrain_map.resolve()),
            "terrain_map_sha256": _sha256(terrain_map),
            "building_map": str(building_map.resolve()),
            "building_map_sha256_before_clearance": building_map_input_sha256,
            "route_source": str(Path(route_path).resolve()),
            "route_source_sha256": _sha256(Path(route_path)),
            "terrain_metadata": str(Path(terrain_metadata_path).resolve()),
            "building_specs": str(Path(building_specs_path).resolve()),
        },
        "output": {"map": str(output_map.resolve()), "map_sha256": _sha256(output_map)},
        "route": {
            "source_kind": "accepted_valhalla_pedestrian_route",
            "encoded_points": len(route),
            "length_world": route_length_world,
            "length_m": route_length_world / GF.WORLD_UNITS_PER_METRE,
            "source_reported_length_m": float(
                ((route_doc.get("trip") or {}).get("summary") or {}).get("length", 0.0)
            ) * 1000.0,
            "classic_human_radius_world": radius,
            # The collision radius is engine/sprite-sized and does NOT grow with
            # the operator map scale (2026-10-13), so express it in sprite metres.
            "classic_human_radius_m": radius / GF.SPRITE_WORLD_UNITS_PER_METRE,
            "height_lattice_support_world": height_support,
            "sample_step_world": ROUTE_SAMPLE_STEP_WORLD,
        },
        "counts": {
            "corridor_samples": len(corridor),
            "missing_samples": missing_samples,
            "pre_blocked_samples": pre_blocked_samples,
            "post_blocked_samples": post_blocked_samples,
            "height_vertices_restored": len(restored_keys),
            "visual_cells_restored": visual_cells_restored,
            "affected_building_masses": len(affected),
            "terrain_seam_mismatches": seam_mismatches,
        },
        "conflicting_building_sources": sorted(affected),
        "measurements": {
            "pre_max_building_rise_raw": pre_max_delta,
            "post_max_building_rise_raw": post_max_delta,
            "max_centerline_grade": max_grade,
            "minimum_centerline_normal_z": minimum_normal_z,
            "required_minimum_ground_normal_z": MINIMUM_GROUND_NORMAL_Z,
        },
        "gates": {
            "collision_corridor_clear": collision_pass,
            "static_route_walkability_valid": walkability_pass,
            "route_inside_a3d_bounds": missing_samples == 0,
            "terrain_seams_equal": seam_mismatches == 0,
            "a3d_tail_preserved": True,
        },
        "authority": {
            "route": "Valhalla pedestrian routing over the frozen OSM network",
            "ground": "DEM-only terrain A3D",
            "building_mass": "OSM footprints lowered to the A3D heightfield",
            "clearance": "derived from the original Classic HUMAN collision radius",
            "runtime_acceptance": "FL-4816 headed Classic traversal remains required",
        },
        "code_refs": ["engine/physics.cpp:1051", "engine/physics.cpp:1054"],
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    if not walkability_pass:
        raise RuntimeError("compiled Classic route failed static walkability attestation")
    return receipt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--terrain-map", required=True, type=Path)
    ap.add_argument("--building-map", required=True, type=Path)
    ap.add_argument("--route", required=True, type=Path)
    ap.add_argument("--terrain-metadata", required=True, type=Path)
    ap.add_argument("--building-specs", required=True, type=Path)
    ap.add_argument("--output-map", required=True, type=Path)
    ap.add_argument("--receipt", required=True, type=Path)
    args = ap.parse_args(argv)
    print(json.dumps(compile_route_walkability(
        terrain_map=args.terrain_map,
        building_map=args.building_map,
        route_path=args.route,
        terrain_metadata_path=args.terrain_metadata,
        building_specs_path=args.building_specs,
        output_map=args.output_map,
        receipt_path=args.receipt,
    ), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
