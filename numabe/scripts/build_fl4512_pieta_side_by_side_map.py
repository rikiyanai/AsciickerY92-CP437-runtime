#!/usr/bin/env python3
"""Build the FL-4512 Pieta acceptance map with baked terrain plus live mesh."""

from __future__ import annotations

import argparse
import importlib.util
import struct
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_MAP = ROOT / "assets/a3d/frog_pieta_4x4.a3d"
PIETA_MESH = ROOT / "assets/meshes/pieta.akm"
GRID_PATCH_X = range(-8, 8)
GRID_PATCH_Y = range(-5, 7)
BASELINE_HEIGHT = 40960
RELIEF_MATERIAL = 8
GROUND_MATERIAL = 1
LEFT_CENTER = (-16.0, 0.0)
RIGHT_CENTER = (8.0, 0.0)
MESH_SCALE_XY = 60.0
MESH_SCALE_Z = 960.0
RELIEF_EDGE_RADIUS = 0.85
RELIEF_VERTEX_RADIUS = 1.35
HEIGHT_SAMPLE_RADIUS = 2.25


def load_codec():
    path = ROOT / "scripts/fl4588/a3d_document_codec.py"
    spec = importlib.util.spec_from_file_location("a3d_document_codec", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_akm_mesh(
    path: Path,
) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    vertex_count = 0
    face_count = 0
    props: list[str] = []
    in_vertex = False
    header_end = 0
    for index, line in enumerate(lines):
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "element" and parts[1] == "vertex":
            vertex_count = int(parts[2])
            in_vertex = True
        elif parts[0] == "element" and parts[1] == "face":
            face_count = int(parts[2])
            in_vertex = False
        elif parts[0] == "property" and in_vertex:
            props.append(parts[-1])
        elif parts[0] == "end_header":
            header_end = index + 1
            break
    x_index = props.index("x")
    y_index = props.index("y")
    z_index = props.index("z")
    vertices: list[tuple[float, float, float]] = []
    for line in lines[header_end:header_end + vertex_count]:
        parts = line.split()
        vertices.append((
            float(parts[x_index]),
            float(parts[y_index]),
            float(parts[z_index]),
        ))
    if not vertices:
        raise RuntimeError(f"mesh has no vertices: {path}")
    faces: list[tuple[int, int, int]] = []
    face_start = header_end + vertex_count
    for line in lines[face_start:face_start + face_count]:
        parts = line.split()
        if not parts:
            continue
        count = int(parts[0])
        indices = [int(value) for value in parts[1:1 + count]]
        if len(indices) < 3:
            continue
        for offset in range(1, len(indices) - 1):
            faces.append((indices[0], indices[offset], indices[offset + 1]))
    if not faces:
        raise RuntimeError(f"mesh has no faces: {path}")
    return vertices, faces


def transformed_pieta_points(
    vertices: list[tuple[float, float, float]], center: tuple[float, float]
) -> list[tuple[float, float, float]]:
    return [
        (
            center[0] + x * MESH_SCALE_XY,
            center[1] + y * MESH_SCALE_XY,
            z * MESH_SCALE_Z,
        )
        for x, y, z in vertices
    ]


def triangle_sample_height(
    ax: float,
    ay: float,
    az: float,
    bx: float,
    by: float,
    bz: float,
    cx: float,
    cy: float,
    cz: float,
    px: float,
    py: float,
) -> tuple[bool, float]:
    v0x = bx - ax
    v0y = by - ay
    v1x = cx - ax
    v1y = cy - ay
    v2x = px - ax
    v2y = py - ay
    den = v0x * v1y - v1x * v0y
    if abs(den) < 1.0e-9:
        return False, max(az, bz, cz)
    inv_den = 1.0 / den
    u = (v2x * v1y - v1x * v2y) * inv_den
    v = (v0x * v2y - v2x * v0y) * inv_den
    w = 1.0 - u - v
    inside = u >= -0.02 and v >= -0.02 and w >= -0.02
    return inside, az * w + bz * u + cz * v


def point_segment_distance2(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> float:
    vx = bx - ax
    vy = by - ay
    denom = vx * vx + vy * vy
    if denom <= 1.0e-12:
        dx = px - ax
        dy = py - ay
        return dx * dx + dy * dy
    t = ((px - ax) * vx + (py - ay) * vy) / denom
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    qx = ax + vx * t
    qy = ay + vy * t
    dx = px - qx
    dy = py - qy
    return dx * dx + dy * dy


class ReliefBake:
    def __init__(
        self,
        points: list[tuple[float, float, float]],
        faces: list[tuple[int, int, int]],
    ) -> None:
        self.cell_height: dict[tuple[int, int], float] = {}
        self._rasterize_faces(points, faces)
        self._rasterize_vertices(points)

    def _mark(self, cell_x: int, cell_y: int, height: float) -> None:
        key = (cell_x, cell_y)
        previous = self.cell_height.get(key)
        if previous is None or height > previous:
            self.cell_height[key] = height

    def _rasterize_faces(
        self,
        points: list[tuple[float, float, float]],
        faces: list[tuple[int, int, int]],
    ) -> None:
        edge_radius2 = RELIEF_EDGE_RADIUS * RELIEF_EDGE_RADIUS
        for ia, ib, ic in faces:
            ax, ay, az = points[ia]
            bx, by, bz = points[ib]
            cx, cy, cz = points[ic]
            min_x = int(min(ax, bx, cx) - RELIEF_EDGE_RADIUS - 1.0)
            max_x = int(max(ax, bx, cx) + RELIEF_EDGE_RADIUS + 1.0)
            min_y = int(min(ay, by, cy) - RELIEF_EDGE_RADIUS - 1.0)
            max_y = int(max(ay, by, cy) + RELIEF_EDGE_RADIUS + 1.0)
            for cell_y in range(min_y, max_y + 1):
                py = cell_y + 0.5
                for cell_x in range(min_x, max_x + 1):
                    px = cell_x + 0.5
                    inside, height = triangle_sample_height(
                        ax, ay, az, bx, by, bz, cx, cy, cz, px, py
                    )
                    if not inside:
                        near_edge = (
                            point_segment_distance2(px, py, ax, ay, bx, by)
                            <= edge_radius2
                            or point_segment_distance2(px, py, bx, by, cx, cy)
                            <= edge_radius2
                            or point_segment_distance2(px, py, cx, cy, ax, ay)
                            <= edge_radius2
                        )
                        if not near_edge:
                            continue
                        height = max(az, bz, cz)
                    self._mark(cell_x, cell_y, height)

    def _rasterize_vertices(self, points: list[tuple[float, float, float]]) -> None:
        radius2 = RELIEF_VERTEX_RADIUS * RELIEF_VERTEX_RADIUS
        for px, py, pz in points:
            min_x = int(px - RELIEF_VERTEX_RADIUS - 1.0)
            max_x = int(px + RELIEF_VERTEX_RADIUS + 1.0)
            min_y = int(py - RELIEF_VERTEX_RADIUS - 1.0)
            max_y = int(py + RELIEF_VERTEX_RADIUS + 1.0)
            for cell_y in range(min_y, max_y + 1):
                cy = cell_y + 0.5
                for cell_x in range(min_x, max_x + 1):
                    cx = cell_x + 0.5
                    dx = cx - px
                    dy = cy - py
                    d2 = dx * dx + dy * dy
                    if d2 <= radius2:
                        self._mark(cell_x, cell_y, pz)

    def material(self, wx: float, wy: float) -> int:
        cell_x = int(wx)
        cell_y = int(wy)
        return (
            RELIEF_MATERIAL
            if (cell_x, cell_y) in self.cell_height
            else GROUND_MATERIAL
        )

    def height(self, wx: float, wy: float) -> int:
        best = 0.0
        radius2 = HEIGHT_SAMPLE_RADIUS * HEIGHT_SAMPLE_RADIUS
        min_x = int(wx - HEIGHT_SAMPLE_RADIUS - 1.0)
        max_x = int(wx + HEIGHT_SAMPLE_RADIUS + 1.0)
        min_y = int(wy - HEIGHT_SAMPLE_RADIUS - 1.0)
        max_y = int(wy + HEIGHT_SAMPLE_RADIUS + 1.0)
        for cell_y in range(min_y, max_y + 1):
            cy = cell_y + 0.5
            for cell_x in range(min_x, max_x + 1):
                height = self.cell_height.get((cell_x, cell_y))
                if height is None:
                    continue
                cx = cell_x + 0.5
                dx = wx - cx
                dy = wy - cy
                d2 = dx * dx + dy * dy
                if d2 <= radius2:
                    weight = 1.0 - d2 / radius2
                    best = max(best, height * (0.7 + 0.3 * weight))
        if best <= 0.0:
            return BASELINE_HEIGHT
        return min(65535, BASELINE_HEIGHT + int(round(best)))


def projected_bbox(points: list[tuple[float, float, float]]) -> dict[str, list[float]]:
    return {
        "x": [min(point[0] for point in points), max(point[0] for point in points)],
        "y": [min(point[1] for point in points), max(point[1] for point in points)],
    }


def map_cell_bounds() -> dict[str, list[int]]:
    return {
        "x": [GRID_PATCH_X.start * 8, GRID_PATCH_X.stop * 8 - 1],
        "y": [GRID_PATCH_Y.start * 8, GRID_PATCH_Y.stop * 8 - 1],
    }


def relief_height(bake: ReliefBake, wx: float, wy: float) -> int:
    height = bake.height(wx, wy)
    if height <= BASELINE_HEIGHT:
        return BASELINE_HEIGHT
    return height


def relief_material(bake: ReliefBake, wx: float, wy: float) -> int:
    return bake.material(wx, wy)


def patch_payload(bake: ReliefBake, patch_x: int, patch_y: int) -> bytes:
    visual = bytearray()
    origin_x = patch_x * 8
    origin_y = patch_y * 8
    for row in range(8):
        for col in range(8):
            wx = origin_x + col + 0.5
            wy = origin_y + row + 0.5
            visual += struct.pack("<H", relief_material(bake, wx, wy))

    heights = bytearray()
    for row in range(5):
        for col in range(5):
            wx = origin_x + col * 2.0
            wy = origin_y + row * 2.0
            heights += struct.pack("<H", relief_height(bake, wx, wy))
    return bytes(visual + heights + struct.pack("<H", 0))


def mesh_transform(center: tuple[float, float]) -> bytes:
    return struct.pack(
        "<16d",
        MESH_SCALE_XY, 0.0, 0.0, 0.0,
        0.0, MESH_SCALE_XY, 0.0, 0.0,
        0.0, 0.0, MESH_SCALE_Z, 0.0,
        center[0], center[1], float(BASELINE_HEIGHT), 1.0,
    )


def build_map(source: Path, mesh: Path, output: Path, force: bool) -> dict[str, object]:
    if output.exists() and not force:
        raise RuntimeError(f"refusing to overwrite {output}; pass --force")
    codec = load_codec()
    source_doc = codec.parse_document(source.read_bytes())
    vertices, faces = parse_akm_mesh(mesh)
    relief_points = transformed_pieta_points(vertices, LEFT_CENTER)
    relief_bake = ReliefBake(relief_points, faces)

    source_doc.patches = [
        (px, py, patch_payload(relief_bake, px, py))
        for py in GRID_PATCH_Y
        for px in GRID_PATCH_X
    ]
    source_doc.world_format_version = 6
    source_doc.instances = [{
        "kind": "mesh",
        "mesh": b"pieta.akm",
        "name": b"pieta_live_right",
        "tm": mesh_transform(RIGHT_CENTER),
        "flags": 3,
        "story": -1,
        "material_key": 200,
        "override": 0,
        "uid": 1,
    }]
    source_doc.player_start = struct.pack(
        "<fffff",
        2.0, -25.0, float(BASELINE_HEIGHT + 130), 45.0, 0.0,
    )
    output.write_bytes(codec.serialize_canonical(source_doc))
    heights = [
        relief_height(relief_bake, px * 8 + hx * 2.0, py * 8 + hy * 2.0)
        for py in GRID_PATCH_Y
        for px in GRID_PATCH_X
        for hy in range(5)
        for hx in range(5)
    ]
    relief_cells = sum(
        1
        for py in GRID_PATCH_Y
        for px in GRID_PATCH_X
        for row in range(8)
        for col in range(8)
        if relief_material(relief_bake, px * 8 + col + 0.5, py * 8 + row + 0.5)
        == RELIEF_MATERIAL
    )
    bounds = map_cell_bounds()
    bbox = projected_bbox(relief_points)
    return {
        "output": str(output.relative_to(ROOT)),
        "patches": len(source_doc.patches),
        "world_cells": [len(GRID_PATCH_X) * 8, len(GRID_PATCH_Y) * 8],
        "left_baked_center": list(LEFT_CENTER),
        "right_live_mesh_center": list(RIGHT_CENTER),
        "map_cell_bounds": bounds,
        "left_baked_bbox": bbox,
        "relief_cells": relief_cells,
        "height_min": min(heights),
        "height_max": max(heights),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_MAP)
    parser.add_argument("--mesh", type=Path, default=PIETA_MESH)
    parser.add_argument("--out", type=Path, default=SOURCE_MAP)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        summary = build_map(
            args.source.resolve(),
            args.mesh.resolve(),
            args.out.resolve(),
            args.force,
        )
    except Exception as exc:
        print(f"FL4512_PIETA_SIDE_BY_SIDE_MAP_FAIL reason={exc}", file=sys.stderr)
        return 1
    print(
        "FL4512_PIETA_SIDE_BY_SIDE_MAP_OK "
        f"output={summary['output']} patches={summary['patches']} "
        f"world_cells={summary['world_cells'][0]}x{summary['world_cells'][1]} "
        f"relief_cells={summary['relief_cells']} "
        f"height={summary['height_min']}..{summary['height_max']} "
        f"left={summary['left_baked_center']} right={summary['right_live_mesh_center']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
