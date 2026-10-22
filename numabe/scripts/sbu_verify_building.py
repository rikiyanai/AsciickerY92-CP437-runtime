#!/usr/bin/env python3
# WARNING: this script does NOT accept --map (FL-2564). It uses a run-root
# path, not a map path. Read the argparse below before invoking.
"""Verify that a named building exists in a generated OSM run."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNS_ROOT = PROJECT_ROOT / "assets" / "meshes" / "osm_runs"
A3D_FORMAT_PATH = PROJECT_ROOT / "addons" / "io_asciicker" / "scene" / "a3d_format.py"
PIPELINE_ROOT = PROJECT_ROOT / "scripts" / "pipeline"


def _load_a3d_format():
    spec = importlib.util.spec_from_file_location("a3d_format", A3D_FORMAT_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load A3D format library: {A3D_FORMAT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"could not parse {path}: {exc}") from exc


def _latest_run_root(runs_root: Path) -> Path:
    runs = [path for path in runs_root.iterdir() if path.is_dir()] if runs_root.exists() else []
    if not runs:
        raise SystemExit(f"no OSM runs found under {runs_root}")
    return max(runs, key=lambda path: path.stat().st_mtime)


def _run_root(args: argparse.Namespace) -> Path:
    runs_root = Path(args.runs_root).expanduser().resolve()
    if args.run_root:
        return Path(args.run_root).expanduser().resolve()
    if args.run_id:
        return (runs_root / args.run_id).resolve()
    return _latest_run_root(runs_root)


def _norm(value: object) -> str:
    return str(value or "").strip().lower().replace("_", " ")


def _building_matches(spec: dict[str, Any], needle: str) -> bool:
    normalized = _norm(needle)
    fields = [
        spec.get("inst_name"),
        spec.get("mesh_name"),
        spec.get("label"),
        spec.get("name"),
        spec.get("osm_id"),
        spec.get("source_id"),
    ]
    return any(normalized and normalized in _norm(field) for field in fields)


def _load_building_specs(run_root: Path) -> list[dict[str, Any]]:
    specs_path = run_root / "building_instances.json"
    data = _load_json(specs_path)
    if not isinstance(data, list):
        raise SystemExit(f"building specs are not a list: {specs_path}")
    return [item for item in data if isinstance(item, dict)]


def _read_akm_xy_bounds(path: Path) -> dict[str, float]:
    with path.open("r", encoding="ascii", errors="strict") as fh:
        if fh.readline().strip() != "ply":
            raise SystemExit(f"not an ASCII PLY/AKM file: {path}")
        vertex_count = 0
        props: list[str] = []
        in_vertex = False
        for raw in fh:
            line = raw.strip()
            if line == "end_header":
                break
            if line.startswith("element vertex"):
                vertex_count = int(line.split()[-1])
                in_vertex = True
                continue
            if line.startswith("element "):
                in_vertex = False
            if in_vertex and line.startswith("property "):
                props.append(line.split()[-1])
        prop_idx = {name: idx for idx, name in enumerate(props)}
        if "x" not in prop_idx or "y" not in prop_idx:
            raise SystemExit(f"AKM missing x/y vertex properties: {path}")
        xs: list[float] = []
        ys: list[float] = []
        for _ in range(vertex_count):
            parts = fh.readline().split()
            if not parts:
                break
            xs.append(float(parts[prop_idx["x"]]))
            ys.append(float(parts[prop_idx["y"]]))
    if not xs or not ys:
        raise SystemExit(f"AKM has no vertices: {path}")
    return {"min_x": min(xs), "max_x": max(xs), "min_y": min(ys), "max_y": max(ys)}


def _read_akm_mesh(path: Path) -> tuple[list[tuple[float, float, float]], list[tuple[int, ...]]]:
    with path.open("r", encoding="ascii", errors="strict") as fh:
        if fh.readline().strip() != "ply":
            raise SystemExit(f"not an ASCII PLY/AKM file: {path}")
        vertex_count = 0
        face_count = 0
        props: list[str] = []
        in_vertex = False
        for raw in fh:
            line = raw.strip()
            if line == "end_header":
                break
            if line.startswith("element vertex"):
                vertex_count = int(line.split()[-1])
                in_vertex = True
                continue
            if line.startswith("element face"):
                face_count = int(line.split()[-1])
                in_vertex = False
                continue
            if line.startswith("element "):
                in_vertex = False
            if in_vertex and line.startswith("property "):
                props.append(line.split()[-1])
        prop_idx = {name: idx for idx, name in enumerate(props)}
        if not {"x", "y", "z"}.issubset(prop_idx):
            raise SystemExit(f"AKM missing x/y/z vertex properties: {path}")
        vertices = []
        for _ in range(vertex_count):
            parts = fh.readline().split()
            vertices.append((
                float(parts[prop_idx["x"]]),
                float(parts[prop_idx["y"]]),
                float(parts[prop_idx["z"]]),
            ))
        faces = []
        for _ in range(face_count):
            parts = fh.readline().split()
            if not parts:
                continue
            n = int(parts[0])
            faces.append(tuple(int(value) for value in parts[1:1 + n]))
    return vertices, faces


def _transform_xy_bounds(bounds: dict[str, float], transform: list[float]) -> dict[str, float]:
    if len(transform) < 16:
        raise SystemExit("building transform has fewer than 16 values")
    corners = [
        (bounds["min_x"], bounds["min_y"]),
        (bounds["min_x"], bounds["max_y"]),
        (bounds["max_x"], bounds["min_y"]),
        (bounds["max_x"], bounds["max_y"]),
    ]
    world = [
        (
            transform[0] * x + transform[4] * y + transform[12],
            transform[1] * x + transform[5] * y + transform[13],
        )
        for x, y in corners
    ]
    xs = [p[0] for p in world]
    ys = [p[1] for p in world]
    return {"min_x": min(xs), "max_x": max(xs), "min_y": min(ys), "max_y": max(ys)}


def _transform_vertex(vertex: tuple[float, float, float], transform: list[float]) -> tuple[float, float, float]:
    x, y, z = vertex
    return (
        transform[0] * x + transform[4] * y + transform[8] * z + transform[12],
        transform[1] * x + transform[5] * y + transform[9] * z + transform[13],
        transform[2] * x + transform[6] * y + transform[10] * z + transform[14],
    )


def _load_osm_projection():
    path = PIPELINE_ROOT / "osm_to_cell.py"
    spec = importlib.util.spec_from_file_location("osm_to_cell", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load OSM coordinate library: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(PIPELINE_ROOT))
    sys.modules["osm_to_cell"] = module
    spec.loader.exec_module(module)
    return module


def _bounds_area(bounds: dict[str, float]) -> float:
    return max(0.0, bounds["max_x"] - bounds["min_x"]) * max(0.0, bounds["max_y"] - bounds["min_y"])


def _bounds_iou(a: dict[str, float], b: dict[str, float]) -> float:
    ix0 = max(a["min_x"], b["min_x"])
    iy0 = max(a["min_y"], b["min_y"])
    ix1 = min(a["max_x"], b["max_x"])
    iy1 = min(a["max_y"], b["max_y"])
    intersection = _bounds_area({"min_x": ix0, "min_y": iy0, "max_x": ix1, "max_y": iy1})
    union = _bounds_area(a) + _bounds_area(b) - intersection
    return 0.0 if union <= 0 else intersection / union


def _bounds_center(bounds: dict[str, float]) -> tuple[float, float]:
    return ((bounds["min_x"] + bounds["max_x"]) * 0.5, (bounds["min_y"] + bounds["max_y"]) * 0.5)


def _world_shape_for_osm_way(run_root: Path, spec: dict[str, Any]) -> dict[str, Any] | None:
    mesh_name = str(spec.get("mesh_name") or "")
    stem = Path(mesh_name).stem
    if not stem.startswith("way_"):
        return None
    way_id = stem.removeprefix("way_")
    osm_path = run_root / "osm_blosm_input.osm"
    metadata_path = run_root / "terrain_metadata.json"
    manifest_path = run_root / "manifest.json"
    if not osm_path.is_file() or not metadata_path.is_file():
        return None
    metadata = _load_json(metadata_path)
    scene_lat = metadata.get("scene_lat")
    scene_lon = metadata.get("scene_lon")
    if scene_lat is None or scene_lon is None:
        manifest = _load_json(manifest_path) if manifest_path.is_file() else {}
        bbox = manifest.get("bbox") or {}
        scene_lat = (float(bbox["min_lat"]) + float(bbox["max_lat"])) / 2.0
        scene_lon = (float(bbox["min_lon"]) + float(bbox["max_lon"])) / 2.0
    projection = _load_osm_projection()
    params = {
        "scene_lat": float(scene_lat),
        "scene_lon": float(scene_lon),
        "content_scale": float(metadata.get("content_scale") or 1.0),
        "shift_x": float((metadata.get("terrain_shift") or {}).get("x", 0.0)),
        "shift_y": float((metadata.get("terrain_shift") or {}).get("y", 0.0)),
        "engine_offset_x": float(metadata.get("engine_offset_x", projection.A3D_EXPORT_OFFSET_X)),
        "engine_offset_y": float(metadata.get("engine_offset_y", projection.A3D_EXPORT_OFFSET_Y)),
        "cal_x": float(metadata.get("calibration_offset_x", 0.0)),
        "cal_y": float(metadata.get("calibration_offset_y", 0.0)),
    }

    root = ET.parse(osm_path).getroot()
    nodes: dict[str, tuple[float, float]] = {}
    for node in root.findall("node"):
        lat = float(node.attrib["lat"])
        lon = float(node.attrib["lon"])
        nodes[node.attrib["id"]] = projection.latlon_to_world(lat, lon, params)
    way = root.find(f".//way[@id='{way_id}']")
    if way is None:
        return None
    tags = {tag.attrib.get("k", ""): tag.attrib.get("v", "") for tag in way.findall("tag")}
    points = [nodes[nd.attrib["ref"]] for nd in way.findall("nd") if nd.attrib.get("ref") in nodes]
    if not points:
        return None
    if len(points) >= 2 and points[0] == points[-1]:
        points = points[:-1]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return {
        "osm_way_id": way_id,
        "name": tags.get("name", ""),
        "point_count": len(points),
        "world_points": points,
        "world_bounds": {"min_x": min(xs), "max_x": max(xs), "min_y": min(ys), "max_y": max(ys)},
        "tags": tags,
    }


def _source_shape_proof(run_root: Path, spec: dict[str, Any], *, max_center_delta: float = 4.0, min_iou: float = 0.85) -> dict[str, Any]:
    mesh_name = str(spec.get("mesh_name") or "")
    mesh_path = run_root / "meshes" / mesh_name
    if not mesh_name or not mesh_path.is_file():
        raise SystemExit(f"missing run-local building mesh for {spec.get('inst_name')}: {mesh_path}")
    local_bounds = _read_akm_xy_bounds(mesh_path)
    akm_bounds = _transform_xy_bounds(local_bounds, spec.get("transform") or [])
    osm = _world_shape_for_osm_way(run_root, spec)
    if osm is None:
        raise SystemExit(f"could not resolve OSM source way for {spec.get('inst_name')} mesh={mesh_name}")
    osm_bounds = osm["world_bounds"]
    akm_center = _bounds_center(akm_bounds)
    osm_center = _bounds_center(osm_bounds)
    center_delta = math.hypot(akm_center[0] - osm_center[0], akm_center[1] - osm_center[1])
    iou = _bounds_iou(akm_bounds, osm_bounds)
    return {
        "mesh": str(mesh_path),
        "mesh_name": mesh_name,
        "osm_way_id": osm["osm_way_id"],
        "osm_name": osm["name"],
        "osm_point_count": osm["point_count"],
        "akm_world_bounds": akm_bounds,
        "osm_world_bounds": osm_bounds,
        "bounds_iou": round(iou, 4),
        "center_delta": round(center_delta, 3),
        "max_center_delta": max_center_delta,
        "min_iou": min_iou,
        "ok": center_delta <= max_center_delta and iou >= min_iou,
    }


def _point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    count = len(polygon)
    if count < 3:
        return False
    j = count - 1
    for i in range(count):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)):
            x_cross = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def _point_in_triangle_2d(
    x: float,
    y: float,
    tri: tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
) -> bool:
    (x0, y0), (x1, y1), (x2, y2) = tri
    denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    if abs(denom) < 1e-9:
        return False
    b0 = ((y1 - y2) * (x - x2) + (x2 - x1) * (y - y2)) / denom
    b1 = ((y2 - y0) * (x - x2) + (x0 - x2) * (y - y2)) / denom
    b2 = 1.0 - b0 - b1
    return b0 >= -1e-9 and b1 >= -1e-9 and b2 >= -1e-9


def _polygon_signed_area(points: list[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    total = 0.0
    for i, (x0, y0) in enumerate(points):
        x1, y1 = points[(i + 1) % len(points)]
        total += x0 * y1 - x1 * y0
    return total * 0.5


def _segments_intersect(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    def on_segment(p, q, r):
        return (
            min(p[0], r[0]) - 1e-9 <= q[0] <= max(p[0], r[0]) + 1e-9
            and min(p[1], r[1]) - 1e-9 <= q[1] <= max(p[1], r[1]) + 1e-9
            and abs(orient(p, q, r)) <= 1e-9
        )

    o1 = orient(a, b, c)
    o2 = orient(a, b, d)
    o3 = orient(c, d, a)
    o4 = orient(c, d, b)
    if (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0):
        return True
    return (
        on_segment(a, c, b)
        or on_segment(a, d, b)
        or on_segment(c, a, d)
        or on_segment(c, b, d)
    )


def _self_intersection_count(points: list[tuple[float, float]]) -> int:
    count = len(points)
    intersections = 0
    for i in range(count):
        a = points[i]
        b = points[(i + 1) % count]
        for j in range(i + 1, count):
            if abs(i - j) <= 1 or (i == 0 and j == count - 1):
                continue
            c = points[j]
            d = points[(j + 1) % count]
            if _segments_intersect(a, b, c, d):
                intersections += 1
    return intersections


def _reflex_vertex_count(points: list[tuple[float, float]]) -> int:
    if len(points) < 4:
        return 0
    signed_area = _polygon_signed_area(points)
    if abs(signed_area) < 1e-9:
        return 0
    ccw = signed_area > 0
    reflex = 0
    for i, curr in enumerate(points):
        prev = points[i - 1]
        nxt = points[(i + 1) % len(points)]
        cross = (curr[0] - prev[0]) * (nxt[1] - curr[1]) - (curr[1] - prev[1]) * (nxt[0] - curr[0])
        if (cross < -1e-9) if ccw else (cross > 1e-9):
            reflex += 1
    return reflex


def _shape_counts(
    bounds: dict[str, float],
    expected_polygon: list[tuple[float, float]],
    actual_contains,
    *,
    sample_step: float,
) -> dict[str, Any]:
    tp = fp = fn = tn = 0
    inside_total = outside_total = 0
    y = bounds["min_y"] + sample_step * 0.5
    while y <= bounds["max_y"]:
        x = bounds["min_x"] + sample_step * 0.5
        while x <= bounds["max_x"]:
            expected = _point_in_polygon(x, y, expected_polygon)
            actual = bool(actual_contains(x, y))
            if expected:
                inside_total += 1
            else:
                outside_total += 1
            if expected and actual:
                tp += 1
            elif expected and not actual:
                fn += 1
            elif not expected and actual:
                fp += 1
            else:
                tn += 1
            x += sample_step
        y += sample_step
    denom = tp + fp + fn
    iou = 0.0 if denom <= 0 else tp / denom
    outside_actual_pct = 0.0 if outside_total <= 0 else fp / outside_total * 100.0
    inside_missing_pct = 0.0 if inside_total <= 0 else fn / inside_total * 100.0
    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "inside_samples": inside_total,
        "outside_samples": outside_total,
        "shape_iou": round(iou, 4),
        "outside_actual_pct": round(outside_actual_pct, 2),
        "inside_missing_pct": round(inside_missing_pct, 2),
    }


def _terrain_proof(run_root: Path, output_a3d: Path, spec: dict[str, Any], *, baseline: int = 128, threshold: int = 80) -> dict[str, Any]:
    mesh_name = str(spec.get("mesh_name") or "")
    mesh_path = run_root / "meshes" / mesh_name
    if not mesh_name or not mesh_path.is_file():
        raise SystemExit(f"missing run-local building mesh for {spec.get('inst_name')}: {mesh_path}")
    local_bounds = _read_akm_xy_bounds(mesh_path)
    world_bounds = _transform_xy_bounds(local_bounds, spec.get("transform") or [])
    fmt = _load_a3d_format()
    patch_world = float(fmt.VISUAL_CELLS)
    vertex_step = patch_world / float(fmt.HEIGHT_CELLS)
    patches = {}
    with output_a3d.open("rb") as fh:
        header = fmt.A3DHeader.from_file(fh)
        for _ in range(header.num_patches):
            patch = fmt.A3DPatch.from_file(fh)
            patches[(patch.x, patch.y)] = patch
    min_px = math.floor(world_bounds["min_x"] / patch_world)
    max_px = math.floor(world_bounds["max_x"] / patch_world)
    min_py = math.floor(world_bounds["min_y"] / patch_world)
    max_py = math.floor(world_bounds["max_y"] / patch_world)
    total = 0
    elevated = 0
    min_height = None
    max_height = None
    for py in range(min_py, max_py + 1):
        for px in range(min_px, max_px + 1):
            patch = patches.get((px, py))
            if patch is None:
                continue
            for hy, row in enumerate(patch.height):
                wy = py * patch_world + hy * vertex_step
                if wy < world_bounds["min_y"] or wy > world_bounds["max_y"]:
                    continue
                for hx, raw_h in enumerate(row):
                    wx = px * patch_world + hx * vertex_step
                    if wx < world_bounds["min_x"] or wx > world_bounds["max_x"]:
                        continue
                    h = int(raw_h)
                    total += 1
                    elevated += int(h > baseline + threshold)
                    min_height = h if min_height is None else min(min_height, h)
                    max_height = h if max_height is None else max(max_height, h)
    if total <= 0:
        raise SystemExit(f"no terrain vertices overlap {spec.get('inst_name')} bounds")
    return {
        "mesh": str(mesh_path),
        "world_bounds": world_bounds,
        "patch_world": patch_world,
        "terrain_vertices": total,
        "elevated_vertices": elevated,
        "elevated_pct": round(elevated / total * 100.0, 2),
        "min_height": min_height,
        "max_height": max_height,
        "baseline": baseline,
        "threshold": threshold,
        "ok": elevated > 0 and (max_height or 0) > baseline + threshold,
    }


def _terrain_shape_proof(
    run_root: Path,
    output_a3d: Path,
    spec: dict[str, Any],
    *,
    baseline: int = 128,
    threshold: int = 512,
    min_iou: float = 0.80,
    max_outside_elevated_pct: float = 12.0,
    max_inside_missing_pct: float = 12.0,
) -> dict[str, Any]:
    """Compare final baked terrain heights against the OSM source polygon.

    The old terrain proof only checked the building AABB. That is not enough for
    ESS: an L-shaped source footprint can pass the AABB gate even if the bake
    filled the concavity or produced a jagged raster. This gate makes the final
    output contract explicit: building-height terrain must overlap the
    projected OSM polygon and avoid building-height spill outside it. The
    default threshold is high on purpose: SBU topology itself reaches the
    500-ish range, so baseline+80 is a topology detector, not a building mask.
    """
    osm = _world_shape_for_osm_way(run_root, spec)
    if osm is None:
        raise SystemExit(f"could not resolve OSM source shape for {spec.get('inst_name')}")
    polygon = osm["world_points"]
    bounds = osm["world_bounds"]
    fmt = _load_a3d_format()
    patch_world = float(fmt.VISUAL_CELLS)
    vertex_step = patch_world / float(fmt.HEIGHT_CELLS)
    patches = {}
    with output_a3d.open("rb") as fh:
        header = fmt.A3DHeader.from_file(fh)
        for _ in range(header.num_patches):
            patch = fmt.A3DPatch.from_file(fh)
            patches[(patch.x, patch.y)] = patch

    min_px = math.floor(bounds["min_x"] / patch_world)
    max_px = math.floor(bounds["max_x"] / patch_world)
    min_py = math.floor(bounds["min_y"] / patch_world)
    max_py = math.floor(bounds["max_y"] / patch_world)
    tp = fp = fn = tn = 0
    inside_total = outside_total = 0
    min_height = None
    max_height = None
    for py in range(min_py, max_py + 1):
        for px in range(min_px, max_px + 1):
            patch = patches.get((px, py))
            if patch is None:
                continue
            for hy, row in enumerate(patch.height):
                wy = py * patch_world + hy * vertex_step
                if wy < bounds["min_y"] or wy > bounds["max_y"]:
                    continue
                for hx, raw_h in enumerate(row):
                    wx = px * patch_world + hx * vertex_step
                    if wx < bounds["min_x"] or wx > bounds["max_x"]:
                        continue
                    h = int(raw_h)
                    expected = _point_in_polygon(wx, wy, polygon)
                    actual = h > baseline + threshold
                    if expected:
                        inside_total += 1
                    else:
                        outside_total += 1
                    if expected and actual:
                        tp += 1
                    elif expected and not actual:
                        fn += 1
                    elif not expected and actual:
                        fp += 1
                    else:
                        tn += 1
                    min_height = h if min_height is None else min(min_height, h)
                    max_height = h if max_height is None else max(max_height, h)

    denom = tp + fp + fn
    iou = 0.0 if denom <= 0 else tp / denom
    outside_elevated_pct = 0.0 if outside_total <= 0 else fp / outside_total * 100.0
    inside_missing_pct = 0.0 if inside_total <= 0 else fn / inside_total * 100.0
    return {
        "osm_way_id": osm["osm_way_id"],
        "osm_name": osm["name"],
        "osm_point_count": osm["point_count"],
        "world_bounds": bounds,
        "patch_world": patch_world,
        "vertex_step": vertex_step,
        "baseline": baseline,
        "threshold": threshold,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "inside_vertices": inside_total,
        "outside_vertices": outside_total,
        "shape_iou": round(iou, 4),
        "outside_elevated_pct": round(outside_elevated_pct, 2),
        "inside_missing_pct": round(inside_missing_pct, 2),
        "min_iou": min_iou,
        "max_outside_elevated_pct": max_outside_elevated_pct,
        "max_inside_missing_pct": max_inside_missing_pct,
        "min_height": min_height,
        "max_height": max_height,
        "ok": (
            iou >= min_iou
            and outside_elevated_pct <= max_outside_elevated_pct
            and inside_missing_pct <= max_inside_missing_pct
        ),
    }


def _mesh_shape_proof(
    run_root: Path,
    spec: dict[str, Any],
    *,
    sample_step: float = 4.0,
    min_iou: float = 0.88,
    max_outside_actual_pct: float = 8.0,
    max_inside_missing_pct: float = 8.0,
) -> dict[str, Any]:
    mesh_name = str(spec.get("mesh_name") or "")
    mesh_path = run_root / "meshes" / mesh_name
    if not mesh_name or not mesh_path.is_file():
        raise SystemExit(f"missing run-local building mesh for {spec.get('inst_name')}: {mesh_path}")
    osm = _world_shape_for_osm_way(run_root, spec)
    if osm is None:
        raise SystemExit(f"could not resolve OSM source shape for {spec.get('inst_name')}")
    transform = spec.get("transform") or []
    if len(transform) < 16:
        raise SystemExit("building transform has fewer than 16 values")
    vertices, faces = _read_akm_mesh(mesh_path)
    world_vertices = [_transform_vertex(vertex, transform) for vertex in vertices]
    top_z = max(vertex[2] for vertex in world_vertices)
    triangles: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]] = []
    for face in faces:
        if len(face) < 3:
            continue
        face_vertices = [world_vertices[index] for index in face]
        if any(abs(vertex[2] - top_z) > 0.5 for vertex in face_vertices):
            continue
        for i in range(1, len(face_vertices) - 1):
            tri3 = (face_vertices[0], face_vertices[i], face_vertices[i + 1])
            triangles.append(((tri3[0][0], tri3[0][1]), (tri3[1][0], tri3[1][1]), (tri3[2][0], tri3[2][1])))

    def contains(x: float, y: float) -> bool:
        return any(_point_in_triangle_2d(x, y, tri) for tri in triangles)

    counts = _shape_counts(osm["world_bounds"], osm["world_points"], contains, sample_step=sample_step)
    iou = counts["shape_iou"]
    outside = counts["outside_actual_pct"]
    missing = counts["inside_missing_pct"]
    return {
        "mesh": str(mesh_path),
        "mesh_name": mesh_name,
        "osm_way_id": osm["osm_way_id"],
        "osm_name": osm["name"],
        "osm_point_count": osm["point_count"],
        "top_z": round(top_z, 3),
        "top_triangles": len(triangles),
        "sample_step": sample_step,
        "min_iou": min_iou,
        "max_outside_actual_pct": max_outside_actual_pct,
        "max_inside_missing_pct": max_inside_missing_pct,
        **counts,
        "ok": iou >= min_iou and outside <= max_outside_actual_pct and missing <= max_inside_missing_pct,
    }


def _mesh_top_triangle_count(run_root: Path, spec: dict[str, Any]) -> int:
    mesh_name = str(spec.get("mesh_name") or "")
    mesh_path = run_root / "meshes" / mesh_name
    vertices, faces = _read_akm_mesh(mesh_path)
    transform = spec.get("transform") or []
    world_vertices = [_transform_vertex(vertex, transform) for vertex in vertices]
    top_z = max(vertex[2] for vertex in world_vertices)
    triangles = 0
    for face in faces:
        if len(face) < 3:
            continue
        face_vertices = [world_vertices[index] for index in face]
        if any(abs(vertex[2] - top_z) > 0.5 for vertex in face_vertices):
            continue
        triangles += max(0, len(face_vertices) - 2)
    return triangles


def _footprint_flow_trace(run_root: Path, output_a3d: Path, spec: dict[str, Any]) -> dict[str, Any]:
    osm = _world_shape_for_osm_way(run_root, spec)
    if osm is None:
        return {
            "deferred_building_spec": {
                "owner": "building_instances.json",
                "inst_name": spec.get("inst_name"),
                "mesh_name": spec.get("mesh_name"),
            },
            "ok": None,
            "skip_reason": "no OSM way source shape resolved for this building spec",
        }
    transform = spec.get("transform") or []
    mesh_shape = _mesh_shape_proof(run_root, spec)
    terrain_shape = _terrain_shape_proof(run_root, output_a3d, spec) if output_a3d.is_file() else None
    points = osm["world_points"]
    return {
        "osm_source_way": {
            "owner": "osm_blosm_input.osm",
            "osm_way_id": osm["osm_way_id"],
            "name": osm["name"],
            "point_count": osm["point_count"],
            "signed_area": round(_polygon_signed_area(points), 3),
            "reflex_vertices": _reflex_vertex_count(points),
            "self_intersections": _self_intersection_count(points),
            "world_bounds": osm["world_bounds"],
        },
        "deferred_building_spec": {
            "owner": "building_instances.json",
            "inst_name": spec.get("inst_name"),
            "mesh_name": spec.get("mesh_name"),
            "transform_xy": [round(float(transform[12]), 3), round(float(transform[13]), 3)] if len(transform) >= 14 else None,
        },
        "run_local_akm_roof": {
            "owner": "meshes/<way>.akm exported before terrain bake",
            "top_triangles": _mesh_top_triangle_count(run_root, spec),
            "shape_iou_vs_osm": mesh_shape["shape_iou"],
            "outside_actual_pct": mesh_shape["outside_actual_pct"],
            "inside_missing_pct": mesh_shape["inside_missing_pct"],
            "ok": mesh_shape["ok"],
        },
        "final_baked_terrain": None if terrain_shape is None else {
            "owner": "output.a3d height mask after BAKE_MESH_TO_TERRAIN",
            "shape_iou_vs_osm": terrain_shape["shape_iou"],
            "outside_elevated_pct": terrain_shape["outside_elevated_pct"],
            "inside_missing_pct": terrain_shape["inside_missing_pct"],
            "max_height": terrain_shape["max_height"],
            "ok": terrain_shape["ok"],
        },
        "historical_failure_boundary": (
            "Jagged ESS was introduced before BAKE_MESH_TO_TERRAIN: the old "
            "clean-extrude path reconstructed the footprint from Blender/blosm "
            "mesh boundary edges and created one implicit n-gon. The bake then "
            "stamped that already-wrong AKM roof. The OSM way was not the "
            "jagged source."
        ),
    }


def _marker_output(output_a3d: Path, *, human_output: bool) -> str:
    command = [
        sys.executable,
        "docs/agent/cli-anything/minimap_render.py",
        "--map",
        str(output_a3d),
        "list-markers",
    ]
    if human_output:
        print("--- Phase 2: Inspect Embedded Markers ---")
        print("+ " + " ".join(command), flush=True)
    result = subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True, check=False)
    if result.stdout and human_output:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    return result.stdout


def _print_start_card(args: argparse.Namespace, run_root: Path, output_a3d: Path) -> None:
    print("=== OSM Building Verification ===")
    print(f"  run root:    {run_root}")
    print(f"  a3d:         {output_a3d}")
    print(f"  building:    {args.building}")
    print(f"  bbox:        {args.bbox or 'not specified'}")
    print("  mutates:     writes embedded_markers.txt and building_verify_summary.json")
    print()


def _print_final_summary(run_root: Path, output_a3d: Path, args: argparse.Namespace, matches: list[dict[str, Any]]) -> None:
    print()
    print("=== Final Summary ===")
    print("  result:      OK")
    print(f"  run root:    {run_root}")
    print(f"  a3d:         {output_a3d}")
    print(f"  building:    {args.building}")
    print(f"  matches:     {len(matches)}")
    print(f"  summary:     {run_root / 'building_verify_summary.json'}")
    print("  next action: Open the run output in ASCIIID or use this run id from the launcher.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify a building in a generated OSM run.")
    parser.add_argument("--runs-root", default=str(RUNS_ROOT))
    parser.add_argument("--run-id")
    parser.add_argument("--run-root")
    parser.add_argument("--building", help="Building name, instance id, mesh id, or OSM id substring.")
    parser.add_argument("--all-buildings", action="store_true",
                        help="Run the selected proof/trace against every building in building_instances.json.")
    parser.add_argument("--bbox", help="Accepted for the target contract; currently recorded in the summary only.")
    parser.add_argument("--terrain-proof", action="store_true",
                        help="Also verify the final post-bake output.a3d contains an elevated terrain imprint under the run-local AKM bounds.")
    parser.add_argument("--source-proof", action="store_true",
                        help="Verify the run-local AKM bounds match the OSM source way bounds for way_<id>.akm buildings.")
    parser.add_argument("--shape-proof", action="store_true",
                        help="Verify the final post-bake height mask matches the projected OSM source polygon, not just the building bounds.")
    parser.add_argument("--mesh-shape-proof", action="store_true",
                        help="Verify the run-local AKM roof triangles match the projected OSM source polygon.")
    parser.add_argument("--trace-footprint-flow", action="store_true",
                        help="Report OSM way -> building spec -> AKM roof -> baked terrain shape handoff for this building.")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_root = _run_root(args)
    output_a3d = run_root / "output.a3d"
    human_output = not args.json
    if human_output:
        _print_start_card(args, run_root, output_a3d)
        print("--- Phase 1: Check Run Artifacts ---")
    if not run_root.is_dir():
        raise SystemExit(f"run root not found: {run_root}")
    needs_a3d = args.terrain_proof or args.shape_proof or not args.mesh_shape_proof
    if needs_a3d and not output_a3d.is_file():
        raise SystemExit(f"missing final A3D: {output_a3d}")

    if not args.building and not args.all_buildings:
        raise SystemExit("--building is required unless --all-buildings is used")
    specs = _load_building_specs(run_root)
    matches = specs if args.all_buildings else [spec for spec in specs if _building_matches(spec, args.building)]
    if not matches:
        raise SystemExit(f"building {args.building!r} not found in {run_root / 'building_instances.json'}")

    markers = ""
    if output_a3d.is_file():
        markers = _marker_output(output_a3d, human_output=human_output)
        marker_match = args.all_buildings or _norm(args.building) in _norm(markers) or any(
            _norm(spec.get("inst_name")) in _norm(markers) for spec in matches
        )
        if not marker_match:
            raise SystemExit(f"building {args.building!r} found in instances but not embedded minimap markers")

    terrain_proofs = []
    if args.terrain_proof:
        for spec in matches:
            proof = _terrain_proof(run_root, output_a3d, spec)
            if not proof["ok"]:
                raise SystemExit(
                    f"building {spec.get('inst_name')} has no baked terrain imprint "
                    f"(max_height={proof['max_height']} baseline={proof['baseline']})"
                )
            terrain_proofs.append(proof)

    source_proofs = []
    if args.source_proof:
        for spec in matches:
            proof = _source_shape_proof(run_root, spec)
            if not proof["ok"]:
                raise SystemExit(
                    f"building {spec.get('inst_name')} AKM/OSM source shape mismatch "
                    f"(iou={proof['bounds_iou']} center_delta={proof['center_delta']})"
                )
            source_proofs.append(proof)

    shape_proofs = []
    if args.shape_proof:
        for spec in matches:
            proof = _terrain_shape_proof(run_root, output_a3d, spec)
            if not proof["ok"]:
                raise SystemExit(
                    f"building {spec.get('inst_name')} baked terrain shape mismatch "
                    f"(iou={proof['shape_iou']} outside_elevated={proof['outside_elevated_pct']}% "
                    f"inside_missing={proof['inside_missing_pct']}%)"
                )
            shape_proofs.append(proof)

    mesh_shape_proofs = []
    if args.mesh_shape_proof:
        for spec in matches:
            proof = _mesh_shape_proof(run_root, spec)
            if not proof["ok"]:
                raise SystemExit(
                    f"building {spec.get('inst_name')} AKM roof shape mismatch "
                    f"(iou={proof['shape_iou']} outside={proof['outside_actual_pct']}% "
                    f"inside_missing={proof['inside_missing_pct']}%)"
                )
            mesh_shape_proofs.append(proof)

    footprint_flow = []
    if args.trace_footprint_flow:
        for spec in matches:
            footprint_flow.append(_footprint_flow_trace(run_root, output_a3d, spec))

    summary = {
        "ok": True,
        "run_root": str(run_root),
        "a3d": str(output_a3d),
        "building": args.building,
        "bbox": args.bbox,
        "matches": matches,
        "markers": str(run_root / "embedded_markers.txt"),
        "terrain_proofs": terrain_proofs,
        "source_proofs": source_proofs,
        "shape_proofs": shape_proofs,
        "mesh_shape_proofs": mesh_shape_proofs,
        "footprint_flow": footprint_flow,
    }
    if markers:
        (run_root / "embedded_markers.txt").write_text(markers, encoding="utf-8")
    (run_root / "building_verify_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        _print_final_summary(run_root, output_a3d, args, matches)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
