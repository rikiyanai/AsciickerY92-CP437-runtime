#!/usr/bin/env python3
"""Bake OSM building footprints directly into an A3D terrain map.

This is the OSM building bake owner for baked-mode runs.  It intentionally
does not load AKM meshes or call asciiid's BAKE_MESH_TO_TERRAIN path: OSM
already provides the authoritative 2D footprint, and mesh triangulation is the
failure point that made concave buildings such as ESS jagged.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import shutil
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_a3d_format():
    path = PROJECT_ROOT / "addons" / "io_asciicker" / "scene" / "a3d_format.py"
    spec = importlib.util.spec_from_file_location("asciicker_a3d_format", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load A3D format module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fmt = _load_a3d_format()


PATCH_WORLD = float(fmt.VISUAL_CELLS)
HEIGHT_STEP_WORLD = PATCH_WORLD / float(fmt.HEIGHT_CELLS)


def _point_on_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> bool:
    cross = (px - ax) * (by - ay) - (py - ay) * (bx - ax)
    if abs(cross) > 1e-7:
        return False
    dot = (px - ax) * (px - bx) + (py - ay) * (py - by)
    return dot <= 1e-7


def point_in_polygon(px: float, py: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        if _point_on_segment(px, py, ax, ay, bx, by):
            return True
        if (ay > py) != (by > py):
            x_at_y = (bx - ax) * (py - ay) / (by - ay) + ax
            if px <= x_at_y:
                inside = not inside
    return inside


def _rect_contains_point(min_x: float, min_y: float, max_x: float, max_y: float, p: tuple[float, float]) -> bool:
    return min_x <= p[0] <= max_x and min_y <= p[1] <= max_y


def _segments_intersect(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], d: tuple[float, float]) -> bool:
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    o1 = orient(a, b, c)
    o2 = orient(a, b, d)
    o3 = orient(c, d, a)
    o4 = orient(c, d, b)
    if abs(o1) < 1e-7 and _point_on_segment(c[0], c[1], a[0], a[1], b[0], b[1]):
        return True
    if abs(o2) < 1e-7 and _point_on_segment(d[0], d[1], a[0], a[1], b[0], b[1]):
        return True
    if abs(o3) < 1e-7 and _point_on_segment(a[0], a[1], c[0], c[1], d[0], d[1]):
        return True
    if abs(o4) < 1e-7 and _point_on_segment(b[0], b[1], c[0], c[1], d[0], d[1]):
        return True
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def polygon_intersects_rect(poly: list[tuple[float, float]], min_x: float, min_y: float, max_x: float, max_y: float) -> bool:
    cx = (min_x + max_x) * 0.5
    cy = (min_y + max_y) * 0.5
    if point_in_polygon(cx, cy, poly):
        return True
    corners = [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)]
    if any(point_in_polygon(x, y, poly) for x, y in corners):
        return True
    if any(_rect_contains_point(min_x, min_y, max_x, max_y, p) for p in poly):
        return True
    rect_edges = list(zip(corners, corners[1:] + corners[:1]))
    for i, a in enumerate(poly):
        b = poly[(i + 1) % len(poly)]
        if any(_segments_intersect(a, b, c, d) for c, d in rect_edges):
            return True
    return False


def _distance_to_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx = bx - ax
    dy = by - ay
    denom = dx * dx + dy * dy
    if denom <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def distance_to_polygon_boundary(px: float, py: float, poly: list[tuple[float, float]]) -> float:
    if len(poly) < 2:
        return 0.0
    return min(
        _distance_to_segment(px, py, ax, ay, bx, by)
        for (ax, ay), (bx, by) in zip(poly, poly[1:] + poly[:1])
    )


def point_in_inset_polygon(px: float, py: float, poly: list[tuple[float, float]], inset: float) -> bool:
    if not point_in_polygon(px, py, poly):
        return False
    return inset <= 0.0 or distance_to_polygon_boundary(px, py, poly) >= inset


def load_a3d(path: Path):
    with path.open("rb") as fh:
        header = fmt.A3DHeader.from_file(fh)
        patches = [fmt.A3DPatch.from_file(fh) for _ in range(header.num_patches)]
        tail = fh.read()
    return header, patches, tail


def write_a3d(path: Path, header: fmt.A3DHeader, patches: list[fmt.A3DPatch], tail: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as fh:
        header.write(fh)
        for patch in patches:
            patch.write(fh)
        fh.write(tail)
    tmp.replace(path)


def _parse_footprint(spec: dict) -> list[tuple[float, float]]:
    # Source OSM footprint is the terrain authority.  `bake_footprint` may be
    # simplified for optional AKM/debug geometry, but terrain imprint must use
    # the full ordered OSM polygon so concave buildings such as ESS keep their
    # real shape.
    raw = spec.get("footprint") or spec.get("bake_footprint")
    if not isinstance(raw, list):
        return []
    points = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        points.append((float(item[0]), float(item[1])))
    if len(points) >= 2 and points[0] == points[-1]:
        points.pop()
    return points


def _align_footprint_to_instance(poly: list[tuple[float, float]], spec: dict) -> list[tuple[float, float]]:
    transform = spec.get("transform")
    if not isinstance(transform, list) or len(transform) < 14 or len(poly) < 3:
        return poly
    try:
        inst_x = float(transform[12])
        inst_y = float(transform[13])
    except Exception:
        return poly
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    bbox_cx = (min(xs) + max(xs)) * 0.5
    bbox_cy = (min(ys) + max(ys)) * 0.5
    dx = inst_x - bbox_cx
    dy = inst_y - bbox_cy
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return poly
    return [(x + dx, y + dy) for x, y in poly]


def _bake_one(
    patches_by_xy: dict[tuple[int, int], fmt.A3DPatch],
    spec: dict,
    material_id: int,
    footprint_inset: float,
    material_inset: float,
) -> dict:
    name = spec.get("inst_name") or spec.get("name") or spec.get("mesh_name") or "<unnamed>"
    poly = _align_footprint_to_instance(_parse_footprint(spec), spec)
    if len(poly) < 3:
        return {"name": name, "ok": False, "reason": "missing_footprint", "height_vertices": 0, "visual_cells": 0}
    bake_height = int(spec.get("bake_height") or spec.get("height") or 0)
    if bake_height <= 0:
        return {"name": name, "ok": False, "reason": "missing_bake_height", "height_vertices": 0, "visual_cells": 0}

    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    min_px = math.floor(min(xs) / PATCH_WORLD) - 1
    max_px = math.floor(max(xs) / PATCH_WORLD) + 1
    min_py = math.floor(min(ys) / PATCH_WORLD) - 1
    max_py = math.floor(max(ys) / PATCH_WORLD) + 1

    height_writes = 0
    visual_writes = 0
    height_covered = 0
    visual_covered = 0
    patches_touched = set()
    for py in range(min_py, max_py + 1):
        for px in range(min_px, max_px + 1):
            patch = patches_by_xy.get((px, py))
            if patch is None:
                continue
            base_x = px * PATCH_WORLD
            base_y = py * PATCH_WORLD

            patch_height = False
            for hy in range(fmt.HEIGHT_CELLS + 1):
                wy = base_y + hy * HEIGHT_STEP_WORLD
                for hx in range(fmt.HEIGHT_CELLS + 1):
                    wx = base_x + hx * HEIGHT_STEP_WORLD
                    if point_in_inset_polygon(wx, wy, poly, footprint_inset):
                        height_covered += 1
                        if patch.height[hy][hx] < bake_height:
                            patch.height[hy][hx] = bake_height
                            height_writes += 1
                        patch_height = True

            patch_visual = False
            for vy in range(fmt.VISUAL_CELLS):
                for vx in range(fmt.VISUAL_CELLS):
                    min_x = base_x + vx
                    min_y = base_y + vy
                    max_x = min_x + 1.0
                    max_y = min_y + 1.0
                    wx = min_x + 0.5
                    wy = min_y + 0.5
                    if material_inset <= 0.0:
                        material_hit = polygon_intersects_rect(poly, min_x, min_y, max_x, max_y)
                    else:
                        material_hit = point_in_inset_polygon(wx, wy, poly, material_inset)
                    if material_hit:
                        visual_covered += 1
                        old = patch.visual[vy][vx]
                        next_val = (old & 0xFF00) | (material_id & 0xFF)
                        if next_val != old:
                            patch.visual[vy][vx] = next_val
                            visual_writes += 1
                        # Terrain heights are on a 2-world-unit grid while
                        # visual cells are 1-world-unit.  If a thin footprint
                        # overlaps a cell without containing a height vertex,
                        # raise the nearest height vertices so coverage does
                        # not disappear at sub-height-grid widths.
                        fx = (wx - base_x) / HEIGHT_STEP_WORLD
                        fy = (wy - base_y) / HEIGHT_STEP_WORLD
                        for hy in {max(0, min(fmt.HEIGHT_CELLS, math.floor(fy))), max(0, min(fmt.HEIGHT_CELLS, math.ceil(fy)))}:
                            for hx in {max(0, min(fmt.HEIGHT_CELLS, math.floor(fx))), max(0, min(fmt.HEIGHT_CELLS, math.ceil(fx)))}:
                                hv_wx = base_x + hx * HEIGHT_STEP_WORLD
                                hv_wy = base_y + hy * HEIGHT_STEP_WORLD
                                if not point_in_inset_polygon(hv_wx, hv_wy, poly, footprint_inset):
                                    continue
                                height_covered += 1
                                if patch.height[hy][hx] < bake_height:
                                    patch.height[hy][hx] = bake_height
                                    height_writes += 1
                        patch_visual = True

            if patch_height or patch_visual:
                patches_touched.add((px, py))

    ok = bool(patches_touched) and height_covered > 0 and visual_covered > 0
    return {
        "name": name,
        "ok": ok,
        "reason": None if ok else "no_coverage",
        "height_vertices": height_writes,
        "visual_cells": visual_writes,
        "height_vertices_covered": height_covered,
        "visual_cells_covered": visual_covered,
        "patches_touched": len(patches_touched),
        "source": spec.get("source") or spec.get("osm_geom_id"),
        "footprint_inset": footprint_inset,
        "material_inset": material_inset,
    }


def bake(
    input_map: Path,
    building_specs: Path,
    output_map: Path,
    material_id: int,
    footprint_inset: float = 1.0,
    material_inset: float = 0.0,
) -> dict:
    header, patches, tail = load_a3d(input_map)
    patches_by_xy = {(p.x, p.y): p for p in patches}
    specs = json.loads(building_specs.read_text(encoding="utf-8"))
    if not isinstance(specs, list):
        raise ValueError(f"expected list in {building_specs}")

    per_building = [
        _bake_one(patches_by_xy, spec, material_id, footprint_inset, material_inset)
        for spec in specs
    ]
    ok = [row for row in per_building if row["ok"]]
    failed = [row for row in per_building if not row["ok"]]
    if output_map.resolve() != input_map.resolve():
        shutil.copy2(input_map, output_map)
    write_a3d(output_map, header, patches, tail)
    return {
        "ok": not failed,
        "buildings_total": len(specs),
        "buildings_baked": len(ok),
        "buildings_failed": len(failed),
        "height_vertices_written": sum(row["height_vertices"] for row in per_building),
        "visual_cells_written": sum(row["visual_cells"] for row in per_building),
        "material_id": material_id,
        "footprint_inset": footprint_inset,
        "material_inset": material_inset,
        "per_building": per_building,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--map", required=True, type=Path, help="Input A3D map")
    ap.add_argument("--buildings", required=True, type=Path, help="building_instances.json with footprint metadata")
    ap.add_argument("--output", required=True, type=Path, help="Output A3D map")
    ap.add_argument("--material-id", type=int, default=5, help="Material ID for baked building roof cells")
    ap.add_argument("--footprint-inset", type=float, default=1.0,
                    help="Inset baked height cells inside each OSM footprint so raised geometry stays inside the source polygon")
    ap.add_argument("--material-inset", type=float, default=0.0,
                    help="Inset baked material cells. Default 0 paints the full OSM footprint brick while height remains inset, avoiding green side/perimeter bleed.")
    ap.add_argument("--summary", type=Path, help="Optional JSON summary output")
    args = ap.parse_args(argv)

    summary = bake(args.map, args.buildings, args.output, args.material_id, args.footprint_inset, args.material_inset)
    text = json.dumps(summary, indent=2, sort_keys=True)
    if args.summary:
        args.summary.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if summary["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
