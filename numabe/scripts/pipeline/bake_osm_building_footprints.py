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

# uint16 storage ceiling for a terrain height vertex (A3D FilePatch.height).
MAX_BAKE_HEIGHT = 0xFFFF


class RoofField:
    """A measured roof-elevation field evaluated in WORLD coordinates.

    Answers ``roof_height_at(world_x, world_y)`` in metres above local ground.
    The lattice shares the A3D height-vertex grid, so a vertex duplicated on
    both sides of a patch seam resolves to one value and the bake cannot open
    a crack there.  Absent the field, the caller keeps the scalar bake height:
    this is an override, never a replacement for the existing contract.
    """

    def __init__(self, doc: dict, path: Path):
        self.path = path
        self.schema = doc.get("schema")
        grid = doc.get("grid") or {}
        self.step = float(grid.get("step") or HEIGHT_STEP_WORLD)
        self.origin_x = float(grid.get("origin_x") or 0.0)
        self.origin_y = float(grid.get("origin_y") or 0.0)
        scene = doc.get("scene") or {}
        self.content_scale = float(scene.get("content_scale") or 1.0)
        self.provenance_class = doc.get("provenance_class")
        self.regions = doc.get("roof_regions") or []
        self._cells: dict[tuple[int, int], float] = {}
        for c in doc.get("cells") or []:
            self._cells[(int(c["i"]), int(c["j"]))] = float(c["roof_m"])
        if abs(self.step - HEIGHT_STEP_WORLD) > 1e-9:
            raise ValueError(
                f"roof field {path} uses step {self.step}, but A3D height vertices are "
                f"{HEIGHT_STEP_WORLD} world units apart; refusing to interpolate"
            )

    def __len__(self) -> int:
        return len(self._cells)

    def index_of(self, wx: float, wy: float) -> tuple[int, int]:
        return (
            int(round((wx - self.origin_x) / self.step)),
            int(round((wy - self.origin_y) / self.step)),
        )

    def roof_height_at(self, wx: float, wy: float) -> float | None:
        """Metres above local ground, or None where the field has no evidence."""
        return self._cells.get(self.index_of(wx, wy))


def _load_roof_field(spec: dict, specs_dir: Path) -> RoofField | None:
    ref = spec.get("roof_field")
    if not ref:
        return None
    path = Path(ref)
    if not path.is_absolute():
        path = (specs_dir / path).resolve()
    if not path.exists():
        raise SystemExit(f"spec references roof_field {ref!r} but {path} does not exist")
    doc = json.loads(path.read_text())
    field = RoofField(doc, path)
    if not len(field):
        raise SystemExit(f"roof field {path} carries no cells")
    return field


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



def terrain_support_inside_footprint(wx: float, wy: float,
                                     poly: list[tuple[float, float]]) -> bool:
    """FL-4814: contain every incident height cell, including either diagonal.

    A raised vertex affects four cells. Testing only the vertex (or shrinking
    by an arbitrary metre) allows its interpolation ramp outside the facade.
    The circumscribed disc is conservative for concave and narrow footprints.
    Never relax this condition to hide a missing gameplay mass.
    """
    return point_in_inset_polygon(
        wx, wy, poly, math.sqrt(2.0) * HEIGHT_STEP_WORLD + 1e-7)

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
    roof_field: "RoofField | None" = None,
    fallback_roof_height_m: float = 0.0,
    fallback_content_scale: float = 2.25,
    roof_material_id: int | None = None,
    wall_material_id: int | None = None,
    wall_band: float = 1.5,
    contain_terrain_support: bool = False,
) -> dict:
    name = spec.get("inst_name") or spec.get("name") or spec.get("mesh_name") or "<unnamed>"
    raw_poly = _parse_footprint(spec)
    poly = _align_footprint_to_instance(raw_poly, spec)
    # The bake may translate the footprint onto the instance origin.  A roof
    # field is compiled against the UNALIGNED source footprint (that is the
    # frame the LiDAR was sampled in), so lookups must undo the same shift or
    # the measured roof lands next to the building instead of on it.
    if len(poly) == len(raw_poly) and poly and raw_poly:
        field_dx = poly[0][0] - raw_poly[0][0]
        field_dy = poly[0][1] - raw_poly[0][1]
    else:
        field_dx = field_dy = 0.0
    if len(poly) < 3:
        return {"name": name, "ok": False, "reason": "missing_footprint", "height_vertices": 0, "visual_cells": 0}
    bake_height = int(spec.get("bake_height") or spec.get("height") or 0)
    spec_roof_height_m = float(spec.get("roof_height_m") or fallback_roof_height_m or 0.0)
    if bake_height <= 0 and spec_roof_height_m <= 0.0:
        return {"name": name, "ok": False, "reason": "missing_bake_height", "height_vertices": 0, "visual_cells": 0}

    def height_vertex_allowed(wx: float, wy: float) -> bool:
        return (point_in_inset_polygon(wx, wy, poly, footprint_inset)
                and (not contain_terrain_support
                     or terrain_support_inside_footprint(wx, wy, poly)))

    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    min_px = math.floor(min(xs) / PATCH_WORLD) - 1
    max_px = math.floor(max(xs) / PATCH_WORLD) + 1
    min_py = math.floor(min(ys) / PATCH_WORLD) - 1
    max_py = math.floor(max(ys) / PATCH_WORLD) + 1

    # ------------------------------------------------------------------
    # Measured roof field (optional).  The scalar bake_height stays the
    # compatibility fallback and is used unchanged where the field has no
    # evidence, so buildings without a field bake byte-identically.
    #
    # Ground reference is the PRE-BAKE terrain under the footprint, taken
    # once per building, not per vertex: a roof tier is a horizontal plane,
    # so it must not follow terrain undulation.  Re-referencing to the
    # terrain here is what keeps the source vertical datum (NAVD88/geoid)
    # out of the game world, and is why a terrain change can no longer
    # silently sink or float the building the way blosm's absolute mesh Z
    # did (FL-4787).
    # ------------------------------------------------------------------
    ground_bake_ref = None
    roof_stats: dict[str, int] = {}
    need_ground_ref = roof_field is not None or spec_roof_height_m > 0.0
    if need_ground_ref:
        pre = []
        for py in range(min_py, max_py + 1):
            for px in range(min_px, max_px + 1):
                patch = patches_by_xy.get((px, py))
                if patch is None:
                    continue
                for hy in range(fmt.HEIGHT_CELLS + 1):
                    wy = py * PATCH_WORLD + hy * HEIGHT_STEP_WORLD
                    for hx in range(fmt.HEIGHT_CELLS + 1):
                        wx = px * PATCH_WORLD + hx * HEIGHT_STEP_WORLD
                        if height_vertex_allowed(wx, wy):
                            pre.append(patch.height[hy][hx])
        if not pre:
            return {"name": name, "ok": False, "reason": ("no_contained_height_support" if contain_terrain_support else "no_ground_reference"),
                    "height_vertices": 0, "visual_cells": 0}
        pre.sort()
        ground_bake_ref = float(pre[len(pre) // 2])
    roof_stats = {"field_hits": 0, "field_misses": 0, "fallback_vertices": 0}

    def _terrain_relative(roof_m: float, scale: float) -> int:
        value = ground_bake_ref + roof_m * scale * float(fmt.HEIGHT_SCALE)
        return max(0, min(MAX_BAKE_HEIGHT, int(round(value))))

    def target_at(wx: float, wy: float) -> int:
        """Bake-unit roof height for one world-space height vertex."""
        if roof_field is None or ground_bake_ref is None:
            # The legacy scalar is blosm's absolute max mesh Z against a
            # pre-bake datum. Measured against LiDAR across this run the
            # implied datum ranges -59..+29 world units (stdev 19.3), so it
            # cannot be re-based onto live terrain and is kept only as the
            # unchanged compatibility path.
            if spec_roof_height_m > 0.0 and ground_bake_ref is not None:
                roof_stats["fallback_vertices"] += 1
                return _terrain_relative(spec_roof_height_m, fallback_content_scale)
            return bake_height
        roof_m = roof_field.roof_height_at(wx - field_dx, wy - field_dy)
        if roof_m is None:
            roof_stats["field_misses"] += 1
            return bake_height
        roof_stats["field_hits"] += 1
        return _terrain_relative(roof_m, roof_field.content_scale)

    height_writes = 0
    visual_writes = 0
    height_covered = 0
    visual_covered = 0
    roof_cells = 0
    wall_cells = 0
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
                    if height_vertex_allowed(wx, wy):
                        height_covered += 1
                        target = target_at(wx, wy)
                        if patch.height[hy][hx] < target:
                            patch.height[hy][hx] = target
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
                        # Roof vs wall. In the Classic CP437 renderer the
                        # stored elevation bit is discarded
                        # (render_resolve.cpp:3448), so a building's vertical
                        # face cannot be coloured by the material's slope ramp.
                        # Give the footprint's outer band its own material
                        # instead: that band is exactly the run of cells whose
                        # terrain rises, so it reads as the facade.
                        cell_mat = material_id
                        if roof_material_id is not None:
                            if wall_material_id is not None and not point_in_inset_polygon(
                                wx, wy, poly, wall_band
                            ):
                                cell_mat = wall_material_id
                                wall_cells += 1
                            else:
                                cell_mat = roof_material_id
                                roof_cells += 1
                        next_val = (old & 0xFF00) | (cell_mat & 0xFF)
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
                                if not height_vertex_allowed(hv_wx, hv_wy):
                                    continue
                                height_covered += 1
                                target = target_at(hv_wx, hv_wy)
                                if patch.height[hy][hx] < target:
                                    patch.height[hy][hx] = target
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
        "roof_cells": roof_cells,
        "wall_cells": wall_cells,
        "roof_material_id": roof_material_id,
        "wall_material_id": wall_material_id,
        "source": spec.get("source") or spec.get("osm_geom_id"),
        "terrain_support_contained": contain_terrain_support,
        "footprint_inset": footprint_inset,
        "material_inset": material_inset,
        "height_model": (
            "lidar_roof_field" if roof_field is not None
            else ("terrain_relative_height_claim" if roof_stats.get("fallback_vertices")
                  else "scalar_bake_height")
        ),
        "roof_height_m": spec_roof_height_m or None,
        "height_method": spec.get("height_method"),
        "fallback_vertices": roof_stats.get("fallback_vertices"),
        "provenance_class": (roof_field.provenance_class if roof_field is not None
                             else spec.get("height_provenance_class") or "unlabelled"),
        "roof_field": str(roof_field.path) if roof_field is not None else None,
        "roof_field_hits": roof_stats.get("field_hits"),
        "roof_field_misses": roof_stats.get("field_misses"),
        "ground_bake_ref": ground_bake_ref,
        "roof_field_offset": ([field_dx, field_dy] if roof_field is not None else None),
        "roof_field_offset_on_lattice": (
            None if roof_field is None
            else (abs(field_dx / HEIGHT_STEP_WORLD - round(field_dx / HEIGHT_STEP_WORLD)) < 1e-3
                  and abs(field_dy / HEIGHT_STEP_WORLD - round(field_dy / HEIGHT_STEP_WORLD)) < 1e-3)
        ),
        "roof_regions": (
            [
                {"elevation_m_above_ground": r.get("elevation_m_above_ground"),
                 "vertex_fraction": r.get("vertex_fraction")}
                for r in roof_field.regions
            ]
            if roof_field is not None else None
        ),
    }




def audit_terrain_support(ground_map: Path, built_map: Path, specs_path: Path) -> list[tuple[float,float]]:
    """Read actual raised vertices and reject support beyond every footprint."""
    _,ground,_=load_a3d(ground_map)
    _,built,_=load_a3d(built_map)
    original={(p.x,p.y):p for p in ground}
    allowed=set()
    for spec in json.loads(specs_path.read_text()):
        poly=_align_footprint_to_instance(_parse_footprint(spec),spec)
        if len(poly)<3:continue
        for ix in range(math.floor(min(p[0] for p in poly)/HEIGHT_STEP_WORLD),
                        math.ceil(max(p[0] for p in poly)/HEIGHT_STEP_WORLD)+1):
            for iy in range(math.floor(min(p[1] for p in poly)/HEIGHT_STEP_WORLD),
                            math.ceil(max(p[1] for p in poly)/HEIGHT_STEP_WORLD)+1):
                x,y=ix*HEIGHT_STEP_WORLD,iy*HEIGHT_STEP_WORLD
                if terrain_support_inside_footprint(x,y,poly):allowed.add((x,y))
    outside=set()
    for patch in built:
        source=original.get((patch.x,patch.y))
        if source is None:raise ValueError('built terrain has a patch absent from ground evidence')
        for iy,row in enumerate(patch.height):
            for ix,height in enumerate(row):
                if height>source.height[iy][ix]:
                    point=(patch.x*PATCH_WORLD+ix*HEIGHT_STEP_WORLD,
                           patch.y*PATCH_WORLD+iy*HEIGHT_STEP_WORLD)
                    if point not in allowed:outside.add(point)
    return sorted(outside)

def _mass_glass_fallback(wall_rgb):
    """Glazing colour for a mass whose palette entry carries no facade profile.

    build_shell's banded fallback draws a window band per storey, but a band
    painted in the wall colour is invisible, which is how these masses came to
    read as blank slabs. Darkening and cooling the wall keeps the building's
    own palette identity while making the storeys legible. write_akm snaps the
    result onto the terminal-safe cube, so no near-miss colour survives.
    """
    red, green, blue = (int(channel) for channel in wall_rgb)
    return (max(0, int(red * 0.45)),
            max(0, int(green * 0.52)),
            min(255, int(blue * 0.62) + 24))


def _pitched_roof_faces(poly, roof_form, scale, shift, wall_rgb, roof_rgb):
    """Gable / hip / shed roof over a mass footprint from a fitted LiDAR roof model (FL-4876).

    ``roof_form`` is roof_form_fit.py's record: the model is in the run's world frame in metres
    (x_m = world_x / content_scale) on the UNALIGNED source footprint, so ``shift`` (aligned minus raw
    footprint origin) is undone before evaluating it.  Returns (eave_m, triangles), each triangle
    ((x, y, z_m) * 3, rgb) in footprint world units / metres.  Gable and hip roofs are drawn as planes
    from every footprint edge up to the ridge segment: on a gable the short-edge triangles are vertical
    and so become the gable-end walls (wall colour); on a hip they slope (roof colour).
    """
    m = roof_form["model"]; form = roof_form["form"]; eave = float(roof_form["eave_m"])
    th = float(m["th_rad"]); ua = (math.sin(th), math.cos(th)); ub = (math.cos(th), -math.sin(th))
    s = float(roof_form.get("content_scale") or scale)
    dx, dy = shift

    def ab(x, y):
        xm, ym = (x - dx) / s - m["cx_m"], (y - dy) / s - m["cy_m"]
        return xm * ua[0] + ym * ua[1], xm * ub[0] + ym * ub[1]

    def z_at(x, y):
        a, b = ab(x, y)
        if form == "shed":
            z = m["c"] + m["slope"] * b
        elif form == "gable":
            z = m["r"] - m["k"] * abs(b - m["b0"])
        else:
            z = m["r"] - m["k"] * max(abs(b - m["b0"]), abs(a - m["a0"]) - m["d"])
        return max(eave, z)

    def world(a, b):
        return ((m["cx_m"] + ua[0] * a + ub[0] * b) * s + dx, (m["cy_m"] + ua[1] * a + ub[1] * b) * s + dy)

    tris = []
    if form == "shed":
        return eave, None, z_at
    a_vals = [ab(x, y)[0] for x, y in poly]
    lo, hi = min(a_vals), max(a_vals)
    if form == "hip":
        lo, hi = max(lo, m["a0"] - m["d"]), min(hi, m["a0"] + m["d"])
        if hi < lo:
            lo = hi = m["a0"]
    ridge_z = float(m["r"])
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        ai, aj = min(hi, max(lo, ab(*p)[0])), min(hi, max(lo, ab(*q)[0]))
        Ri, Rj = world(ai, m["b0"]), world(aj, m["b0"])
        P = (p[0], p[1], z_at(*p)); Q = (q[0], q[1], z_at(*q))
        A = (Rj[0], Rj[1], ridge_z); B = (Ri[0], Ri[1], ridge_z)
        for tri in ((P, Q, A), (P, A, B)):
            if math.dist(tri[1][:2], tri[2][:2]) < 1e-6 or math.dist(tri[0][:2], tri[2][:2]) < 1e-6:
                continue
            tris.append(tri)
    return eave, tris, z_at


def _compile_small_collision_mass(spec, ground_map: Path, mesh_root: Path,
                                  palette_entry: dict, palette: dict, roof_field_doc: dict | None = None) -> dict:
    """Use supported colliding AKM faces when the terrain lattice cannot fit.

    Gameplay mass stays separate from the noncolliding presentation shell.
    This fallback never inflates an OSM footprint or changes terrain relief.
    """
    from scripts.pipeline.compile_facade_shell import build_shell, write_akm
    from scripts.pipeline.probe_instance_terrain_z import load_height_grid, terrain_z
    module_spec = importlib.util.spec_from_file_location(
        "polygon_geometry", PROJECT_ROOT / "addons/io_asciicker/tools/polygon_geometry.py")
    geometry = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(geometry)
    raw_poly = _parse_footprint(spec)
    poly = _align_footprint_to_instance(raw_poly, spec)
    # same shift the terrain bake undoes for roof-field lookups (fields are on the unaligned footprint)
    shift = ((poly[0][0] - raw_poly[0][0], poly[0][1] - raw_poly[0][1])
             if poly and raw_poly and len(poly) == len(raw_poly) else (0.0, 0.0))
    signed_area = sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(poly,poly[1:]+poly[:1]))
    if signed_area < 0: poly.reverse()
    roof_m = float(spec['roof_height_m'])
    scale = float(spec.get('content_scale', 2.25))
    # FL-4876: a fitted pitched LiDAR roof (roof_form_fit.py) puts the walls at the eave and a real
    # gable / hip / shed roof above them; without one the flat lid at roof_height_m stays.
    roof_form = (roof_field_doc or {}).get('roof_form')
    wall_top_m = float(roof_form['eave_m']) if roof_form else roof_m
    mats = palette['materials']
    wall = tuple(mats[str(palette_entry['wall'])]['bg'])
    roof = tuple(mats[str(palette_entry['roof'])]['bg'])
    # FL-4809: use the per-building facade profile the palette already carries.
    #
    # This call used to be build_shell(poly, roof_m, wall, wall, roof, scale):
    # glass_rgb was passed AS wall, so every window band matched its wall and
    # vanished, and no profile was passed, so build_shell took its flat-band
    # fallback instead of the discrete-bay branch. The result was 733 masses
    # that render as untextured slabs -- the barren map.
    #
    # compile_source_palette already resolves a complete facade_profile for
    # every one of those 733 buildings, carrying exactly the keys the bay
    # branch reads (wall_rgb, glass_rgb, trim_rgb, roof_rgb, storey_m,
    # bay_width_m, window_fraction) plus its own provenance (colour_method,
    # provenance_class, source_digest, source_observations). Nothing new is
    # synthesized here; the profile is passed through to the owner that can
    # use it. A building without a profile keeps the previous banded shell,
    # but still gets a distinct glass colour so its storeys read.
    facade_profile = palette_entry.get('facade_profile') or None
    if facade_profile:
        glass = tuple(facade_profile['glass_rgb'])
        storey_m = float(facade_profile['storey_m'])
        vertices, faces, (ox, oy) = build_shell(
            poly, wall_top_m, tuple(facade_profile['wall_rgb']), glass,
            tuple(facade_profile['roof_rgb']), scale,
            storey_m=storey_m, profile=facade_profile)
        wall_paint = tuple(facade_profile['wall_rgb']); roof_paint = tuple(facade_profile['roof_rgb'])
    else:
        vertices, faces, (ox, oy) = build_shell(
            poly, wall_top_m, wall, _mass_glass_fallback(wall), roof, scale)
        wall_paint, roof_paint = wall, roof
    vertices = [(*v[:-1], 0) for v in vertices]  # physics FaceCollect accepts alpha <=128
    triangles = geometry.ear_clip_triangulate(poly)
    area = abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(poly,poly[1:]+poly[:1])))/2
    emitted_area = sum(abs(geometry._cross_2d(*a,*b,*c))/2 for a,b,c in triangles)
    if not triangles or abs(area-emitted_area)>max(1e-6,area*1e-8):
        raise ValueError(f"incomplete collision roof triangulation: {spec['source']}")
    pitched = None
    shed_top_m = None
    if roof_form:
        _eave, pitched, z_at = _pitched_roof_faces(poly, roof_form, scale, shift, wall_paint, roof_paint)
        if roof_form.get('form') == 'shed':
            # A shed is one fitted plane; its high edge is where that plane meets THIS footprint, i.e. the
            # plane's maximum over the baked polygon's vertices. roof_form_fit's ridge_m is the plane's maximum
            # over the LiDAR sample cells, whose extent is the eroded/raw source footprint, so it differed from
            # the mesh by -6..+30% (build_reg_70 object-metric FAILs on ways 441951069, 477599555, 725138281).
            shed_top_m = max(z_at(x, y) for x, y in poly)
        if pitched is None:  # shed: one plane over the footprint triangulation
            pitched = [tuple((x, y, z_at(x, y)) for x, y in t) for t in triangles]
        cx0 = sum(p[0] for p in poly) / len(poly); cy0 = sum(p[1] for p in poly) / len(poly)
        for tri in pitched:
            (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = tri
            ux, uy, uz = x2 - x1, y2 - y1, (z2 - z1) * scale
            vx, vy, vz = x3 - x1, y3 - y1, (z3 - z1) * scale
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            nx, ny, nz = nx / ln, ny / ln, nz / ln
            vertical = abs(nz) < 0.2
            # roof planes face up; gable-end walls face away from the footprint centre
            mx, my = (x1 + x2 + x3) / 3 - cx0, (y1 + y2 + y3) / 3 - cy0
            if (vertical and nx * mx + ny * my < 0) or (not vertical and nz < 0):
                tri = (tri[0], tri[2], tri[1]); nx, ny, nz = -nx, -ny, -nz
            rgb = wall_paint if vertical else roof_paint
            first = len(vertices)
            vertices.extend((x - ox, y - oy, z * scale, nx, ny, nz, *rgb, 0) for x, y, z in tri)
            faces.append([first, first + 1, first + 2])
    else:
        for triangle in triangles:
            first=len(vertices)
            vertices.extend((x-ox,y-oy,roof_m*scale,0,0,1,*roof,0) for x,y in triangle)
            faces.append([first,first+1,first+2])
    ground=terrain_z(load_height_grid(ground_map),ox,oy)
    if ground is None: raise ValueError('collision mass lies outside terrain')
    # FL-4809 METRIC-02: the collision fallback previously recorded no baked
    # height, leaving the object-metric verifier with nothing to check. The
    # realized height is measured from the emitted mesh vertices themselves:
    # local Z is metres above local ground times content_scale, so native
    # Z-extent over content_scale is the realized roof height in metres. The
    # instance transform keeps row2 scale HEIGHT_SCALE, so the transform
    # emits native_dz * |t10| / (HEIGHT_SCALE * scale) == native_dz / scale:
    # the same value. Both legs are recorded so the verifier can prove it.
    native_zs=[v[2] for v in vertices]
    native_dz=max(native_zs)-min(native_zs)
    realized_roof_m=native_dz/scale
    transform=[1,0,0,0,0,1,0,0,0,0,float(fmt.HEIGHT_SCALE),0,ox,oy,ground,1]
    transform_emitted_dz=abs(transform[10])*native_dz/(float(fmt.HEIGHT_SCALE)*scale)
    if abs(transform_emitted_dz-realized_roof_m)>1e-9:
        raise ValueError(f"collision mass transform does not emit measured height: {spec['source']}")
    mesh_root.mkdir(parents=True,exist_ok=True)
    name='mass_'+spec['source'].replace('/','_')+'.akm'
    # the building's own plain wall/roof/trim paint stays exact (cube cannot express cream; FL-4876)
    from scripts.pipeline.compile_facade_shell import _building_paint
    write_akm(mesh_root/name,vertices,faces,comment='FL-4814 gameplay building mass; alpha=0 collides',
              exact_rgb=_building_paint(wall, roof, palette_entry))
    return {'mesh_name':name,'inst_name':spec['inst_name'],
            'transform':transform,
            'flags':3,'story_id':-1,'feature_id':spec.get('feature_id'),
            'roof_area_world2':emitted_area,'collision_alpha':0,
            # a pitched mass realises the fitted LiDAR ridge (roof_form_fit.py), not the p90 scalar claim,
            # which also counts chimneys, trees and rooftop equipment (FL-4876)
            'intended_roof_m':(shed_top_m if shed_top_m is not None
                               else float(roof_form['ridge_m']) if roof_form else roof_m),
            'intended_roof_basis':('fitted shed plane maximum over the baked footprint (TMG LiDAR roof field)'
                                   if shed_top_m is not None
                                   else 'fitted %s roof ridge (TMG LiDAR roof field)' % roof_form['form'] if roof_form
                                   else 'spec roof_height_m'),
            'content_scale':scale,
            'roof_form':(roof_form or {}).get('form','flat'),'wall_top_m':wall_top_m,
            'native_height_dz':native_dz,'realized_roof_m':realized_roof_m,
            'transform_emitted_roof_m':transform_emitted_dz,
            'realized_height_basis':'emitted_akm_native_dz_over_content_scale_with_transform_crosscheck'}

def _authored_mesh_record(palette_entry: dict) -> dict | None:
    """AUTHORED-MESH-01: the hand-authored mesh record of one building, or None.

    The owner is authored_building_appearance.json buildings[<feature>]["authored_mesh"], carried through
    compile_source_palette as palette[building_material][<way>]["authored_appearance"]["authored_mesh"] with
    "file" resolved to an absolute path.
    """
    rec = ((palette_entry or {}).get("authored_appearance") or {}).get("authored_mesh")
    return rec if isinstance(rec, dict) and rec.get("file") else None


def _load_authored_akm(path: Path) -> tuple[list[tuple], list[list[int]]]:
    """Vertices ((x, y, z, nx, ny, nz, r, g, b, a), metres) and faces of an ASCII-PLY .akm."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    nv = nf = 0
    for index, line in enumerate(lines):
        if line.startswith("element vertex"):
            nv = int(line.split()[-1])
        elif line.startswith("element face"):
            nf = int(line.split()[-1])
        elif line == "end_header":
            body = lines[index + 1:]
            break
    else:
        raise ValueError(f"{path}: no end_header")
    if len(body) < nv + nf:
        raise ValueError(f"{path}: truncated body")
    vertices = []
    for row in body[:nv]:
        p = row.split()
        vertices.append((*(float(x) for x in p[:6]), *(int(x) for x in p[6:10])))
    faces = [[int(x) for x in row.split()[1:]] for row in body[nv:nv + nf]]
    return vertices, faces


def _compile_authored_mesh_mass(spec, ground_map: Path, mesh_root: Path, authored: dict) -> dict:
    """AUTHORED-MESH-01: emit a hand-authored mesh as this building's mass.

    The mesh is in metres in an east/north/up frame whose origin is the footprint area centroid (generator:
    authored["generator"]); this scales it by the run's content_scale and places it on the lowest terrain under the
    footprint.  It takes the name and receipt shape of a gameplay mass (mass_<source>.akm, collision_instance), so every
    consumer of building_mass_receipt.json reads it as that building's mass.  The caller skips the terrain box and roof
    field for this footprint.
    """
    import hashlib
    from scripts.pipeline.compile_facade_shell import write_akm
    from scripts.pipeline.probe_instance_terrain_z import load_height_grid, terrain_z
    mesh_path = Path(authored["file"])
    if not mesh_path.is_file():
        raise ValueError(f"authored mesh missing for {spec['source']}: {mesh_path}")
    poly = _align_footprint_to_instance(_parse_footprint(spec), spec)
    scale = float(spec.get("content_scale", 2.25))
    area2 = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1]))
    area_world2 = abs(area2) / 2
    want = authored.get("footprint_area_m2")
    if want is not None and abs(area_world2 / (scale * scale) - float(want)) > 0.03 * float(want):
        raise ValueError(
            f"authored mesh for {spec['source']} was authored for a {want} m2 footprint; this build's footprint is "
            f"{area_world2 / (scale * scale):.1f} m2")
    cx = sum((a[0] + b[0]) * (a[0] * b[1] - b[0] * a[1]) for a, b in zip(poly, poly[1:] + poly[:1])) / (3 * area2)
    cy = sum((a[1] + b[1]) * (a[0] * b[1] - b[0] * a[1]) for a, b in zip(poly, poly[1:] + poly[:1])) / (3 * area2)
    grid = load_height_grid(ground_map)
    samples = [terrain_z(grid, x, y) for x, y in [*poly, (cx, cy)]]
    samples = [s for s in samples if s is not None]
    if not samples:
        raise ValueError(f"authored mesh footprint lies outside terrain: {spec['source']}")
    ground = min(samples)          # z = 0 of the mesh sits on the lowest terrain under the footprint
    metres, faces = _load_authored_akm(mesh_path)
    vertices = [(x * scale, y * scale, z * scale, nx, ny, nz, r, g, b, a)
                for (x, y, z, nx, ny, nz, r, g, b, a) in metres]
    native_dz = max(v[2] for v in vertices) - min(v[2] for v in vertices)
    realized_roof_m = native_dz / scale
    transform = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, float(fmt.HEIGHT_SCALE), 0, cx, cy, ground, 1]
    transform_emitted_dz = abs(transform[10]) * native_dz / (float(fmt.HEIGHT_SCALE) * scale)
    mesh_root.mkdir(parents=True, exist_ok=True)
    name = 'mass_' + spec['source'].replace('/', '_') + '.akm'
    write_akm(mesh_root / name, vertices, faces,
              comment='FL-4876 AUTHORED-MESH-01 hand-authored building mass; alpha<=128 collides, 255 presentation',
              exact_rgb={tuple(v[6:9]) for v in vertices})
    return {'mesh_name': name, 'inst_name': spec['inst_name'], 'transform': transform, 'flags': 3, 'story_id': -1,
            'feature_id': spec.get('feature_id'), 'roof_area_world2': area_world2, 'collision_alpha': 0,
            'intended_roof_m': float(authored.get('height_m') or realized_roof_m),
            'intended_roof_basis': 'authored mesh height: ' + str(authored.get('height_basis') or 'owner record'),
            'content_scale': scale, 'roof_form': 'authored_mesh', 'wall_top_m': None,
            'native_height_dz': native_dz, 'realized_roof_m': realized_roof_m,
            'transform_emitted_roof_m': transform_emitted_dz,
            'realized_height_basis': 'emitted_akm_native_dz_over_content_scale_with_transform_crosscheck',
            'authored_mesh_source': str(mesh_path),
            'authored_mesh_sha256': hashlib.sha256(mesh_path.read_bytes()).hexdigest(),
            'authored_mesh_generator': authored.get('generator')}


def _emit_authored_scene_meshes(fixtures_path: Path, terrain_metadata_path: Path, ground_map: Path,
                                mesh_root: Path) -> tuple[list[dict], list[dict]]:
    """AUTHORED-SCENE-MESH-01: instances for authored_scene_fixtures.json rows with emission = authored_scene_mesh.

    These are accepted hand-placed scenery fixtures that the population planner cannot carry (no road, rail or
    waterway carrier within its 12 m association tolerance: the 有慶山東光院 graveyard terraces).  Each row is built with
    the same geometry builder the population asset plan uses for its (module, variant) -- population_asset_geometry
    .build_custom_geometry -- and placed at its E/N, yaw_deg (math angle) on the terrain at that point.  Presentation
    only (the builder writes alpha 255).  Returns (A3D instance dicts, receipt rows).
    """
    from scripts.pipeline.compile_facade_shell import write_akm
    from scripts.pipeline.population_asset_geometry import CUSTOM_VARIANTS, _sanitize, build_custom_geometry
    from scripts.pipeline.probe_instance_terrain_z import load_height_grid, terrain_z
    if not Path(fixtures_path).is_file():
        return [], []
    rows = [f for f in json.loads(Path(fixtures_path).read_text(encoding="utf-8")).get("fixtures", [])
            if f.get("emission") == "authored_scene_mesh" and (f.get("manual_review") or {}).get("status") == "accepted"]
    if not rows:
        return [], []
    meta = json.loads(Path(terrain_metadata_path).read_text(encoding="utf-8"))
    scale = float(meta["content_scale"])
    grid = load_height_grid(ground_map)
    mesh_root.mkdir(parents=True, exist_ok=True)
    meshes: dict[tuple, str] = {}
    instances, receipts = [], []
    for fx in rows:
        key = (str(fx["module_id"]), fx.get("variant"))
        if key not in CUSTOM_VARIANTS:
            raise ValueError(f"authored scene fixture {fx['fixture_id']}: {key} has no custom geometry")
        if key not in meshes:
            vertices, faces = build_custom_geometry(key[0], key[1], scale)
            name = "scene_" + _sanitize(f"{key[0]}__{key[1]}") + ".akm"
            write_akm(mesh_root / name, vertices, faces,
                      comment="FL-4876 AUTHORED-SCENE-MESH-01 authored scenery; presentation only, alpha=255",
                      exact_rgb={tuple(v[6:9]) for v in vertices})
            meshes[key] = name
        wx = (float(fx["E"]) - float(meta["origin_easting"])) * scale + float(meta.get("offset_x", 0.0))
        wy = (float(fx["N"]) - float(meta["origin_northing"])) * scale + float(meta.get("offset_y", 0.0))
        ground = terrain_z(grid, wx, wy)
        if ground is None:
            raise ValueError(f"authored scene fixture {fx['fixture_id']} lies outside the terrain")
        yaw = math.radians(float(fx["yaw_deg"]))
        c, s = math.cos(yaw), math.sin(yaw)
        inst = {"mesh_name": meshes[key], "inst_name": "authored_scene_" + _sanitize(str(fx["fixture_id"])),
                "transform": [c, s, 0, 0, -s, c, 0, 0, 0, 0, float(fmt.HEIGHT_SCALE), 0, wx, wy, ground, 1],
                "flags": 3, "story_id": -1}
        instances.append(inst)
        receipts.append({"fixture_id": fx["fixture_id"], "mesh_name": inst["mesh_name"], "inst_name": inst["inst_name"],
                         "E": fx["E"], "N": fx["N"], "yaw_deg": fx["yaw_deg"], "ground_bake_z": ground})
    return instances, receipts


def bake(
    input_map: Path,
    building_specs: Path,
    output_map: Path,
    material_id: int,
    footprint_inset: float = 1.0,
    material_inset: float = 0.0,
    palette: dict | None = None,
    roof_field_dir: Path | None = None,
    wall_band: float = 1.5,
    fallback_roof_height_m: float = 0.0,
    contain_terrain_support: bool = False,
    collision_mesh_root: Path | None = None,
    representation_plan: Path | None = None,
    authored_scene_fixtures: Path | None = None,
    terrain_metadata: Path | None = None,
) -> dict:
    header, patches, tail = load_a3d(input_map)
    patches_by_xy = {(p.x, p.y): p for p in patches}
    specs = json.loads(building_specs.read_text(encoding="utf-8"))
    if not isinstance(specs, list):
        raise ValueError(f"expected list in {building_specs}")

    specs_dir = building_specs.resolve().parent
    building_palette = (palette or {}).get("building_material", {})

    # FL-4809 R8: which buildings are real geometry rather than a
    # height-lattice extrusion is the representation plan's decision, read
    # here, never re-derived here.
    #
    # RETRACTED -- an earlier revision of this function took a route_importance
    # path, parsed the receipt itself, and promoted a building when its
    # importance was route_visible, landmark, plus gameplay_critical. That made
    # this stage a second authority over reconstruction, downstream of the plan
    # and invisible to it: the plan recorded terrain_mass for all 733 buildings
    # while this code promoted 322 of them. Two owners answering one question
    # is the defect, whichever answer either gives, because the plan stops
    # being the record of what the world contains.
    #
    # It also read the receipt with a bare json.loads, so none of
    # load_route_importance's schema, duplicate-id, plus exact-canonical-
    # membership checks applied, and a truncated receipt degraded silently to
    # "promote nothing".
    #
    # The join is now spec["feature_id"], which every spec already carries in
    # the plan's exact spelling. The old code matched a stripped feature id
    # against spec["source"] and needed a second "plateau/" spelling to cover
    # the 83 PLATEAU-only buildings, compensating for a mismatch that does not
    # exist on the field beside the one it read.
    shell_feature_ids: set[str] = set()
    if representation_plan is not None:
        _plan = json.loads(
            representation_plan.read_text(encoding="utf-8"))
        for _row in _plan["rows"]:
            if (_row.get("primary")
                    and _row.get("disposition") == "terrain_mass"
                    and _row.get("mass_realization") == "mesh_shell"):
                shell_feature_ids.add(str(_row["feature_id"]))

    def _wants_mesh(spec: dict) -> bool:
        """True when the plan realizes this building as a whole shell."""
        return str(spec.get("feature_id") or "") in shell_feature_ids

    per_building = []
    collision_instances = []
    field_docs: dict[str, dict] = {}  # source -> roof-field document (mesh masses read its roof_form)
    for spec in specs:
        source = spec.get("source") or spec.get("osm_geom_id") or ""
        entry = building_palette.get(source) or {}
        if _authored_mesh_record(entry) is not None:
            # AUTHORED-MESH-01: a hand-authored mesh replaces the terrain box and the roof field for this footprint,
            # so nothing is stamped into the height lattice; the mesh is emitted in the promotion loop below.
            if collision_mesh_root is None:
                raise ValueError(f"{source} has an authored mesh but no mesh output root was given")
            per_building.append({
                "name": spec.get("inst_name") or spec.get("name") or "<unnamed>", "ok": True, "reason": None,
                "height_vertices": 0, "visual_cells": 0, "source": source, "authored_mesh": True,
                "terrain_support_contained": contain_terrain_support,
                "height_model": "authored_akm_mesh", "roof_field": None, "roof_height_m": spec.get("roof_height_m")})
            continue
        field = _load_roof_field(spec, specs_dir)
        if field is None and roof_field_dir is not None and source:
            candidate = roof_field_dir / (source.replace("/", "_") + ".roof_field.json")
            if candidate.is_file():
                _doc = json.loads(candidate.read_text())
                field = RoofField(_doc, candidate)
                field_docs[source] = _doc
        per_building.append(
            _bake_one(
                patches_by_xy,
                spec,
                material_id,
                footprint_inset,
                material_inset,
                field,
                fallback_roof_height_m=fallback_roof_height_m,
                fallback_content_scale=float(spec.get("content_scale", 2.25)),
                roof_material_id=entry.get("roof"),
                wall_material_id=entry.get("wall"),
                wall_band=wall_band,
                contain_terrain_support=contain_terrain_support,
            )
        )
    # FL-4814: a sub-lattice footprint needs supported mesh collision, not
    # an unsafe reduced inset and not a silently omitted building.
    for spec, row in zip(specs, per_building):
        # Two independent reasons a building becomes a mesh, and a row can
        # carry both. Recording only the winner of a ternary made the receipt
        # unable to answer "did every building the plan asked for get a
        # shell": 4 of the 322 the plan requested were already being rescued
        # by the FL-4814 sub-lattice rule, so a reader saw 318 and could not
        # tell a relabelling from a dropped building without re-deriving the
        # union from the plan. Re-deriving a decision downstream is the
        # failure this repair exists to remove, so the row states both.
        if row.get('authored_mesh'):
            _authored = _authored_mesh_record(building_palette[spec['source']])
            instance = _compile_authored_mesh_mass(spec, input_map, collision_mesh_root, _authored)
            collision_instances.append(instance)
            row.update(collision_instance=instance, plan_requested_shell=_wants_mesh(spec),
                       intended_roof_height_m=float(instance['intended_roof_m']),
                       intended_roof_basis=instance['intended_roof_basis'],
                       realized_roof_height_m=instance['realized_roof_m'],
                       realized_height_basis=instance['realized_height_basis'],
                       height_basis='collision_akm_emitted_geometry', height_method=spec.get('height_method'),
                       mesh_promotion_basis='authored_mesh', authored_mesh_sha256=instance['authored_mesh_sha256'],
                       provenance_class=spec.get('height_provenance_class') or 'unlabelled')
            continue
        _plan_shell = _wants_mesh(spec)
        row['plan_requested_shell'] = _plan_shell
        _sub_lattice = row.get('reason') == 'no_contained_height_support'
        _bases = ([_b for _b, _hit in
                   (('sub_lattice_footprint', _sub_lattice),
                    ('representation_plan', _plan_shell)) if _hit])
        _promote = bool(_bases)
        if _promote and collision_mesh_root is not None:
            instance = _compile_small_collision_mass(
                spec, input_map, collision_mesh_root,
                building_palette[spec['source']], palette,
                roof_field_doc=field_docs.get(spec.get('source') or ''))
            collision_instances.append(instance)
            row.update(ok=True,reason=None,height_model='colliding_akm_mass',
                       collision_instance=instance,terrain_support_contained=True,
                       roof_height_m=float(spec['roof_height_m']),
                       intended_roof_height_m=float(instance['intended_roof_m']),
                       intended_roof_basis=instance.get('intended_roof_basis'),
                       realized_roof_height_m=instance['realized_roof_m'],
                       realized_height_basis=instance['realized_height_basis'],
                       height_basis='collision_akm_emitted_geometry',
                       height_method=spec.get('height_method'),
                       mesh_promotion_basis='+'.join(_bases),
                       provenance_class=spec.get('height_provenance_class') or 'unlabelled')
    # FL-4876: hand-authored balconies / railings / grilles (authored_building_appearance attachments, passed
    # through the palette) become one non-colliding presentation mesh per building.
    attachment_instances, attachment_receipts = [], []
    if collision_mesh_root is not None:
        from scripts.pipeline.compile_building_attachments import build_attachments
        from scripts.pipeline.compile_facade_shell import write_akm
        from scripts.pipeline.probe_instance_terrain_z import load_height_grid, terrain_z
        _grid = None
        for spec, row in zip(specs, per_building):
            entry = building_palette.get(spec.get('source') or '') or {}
            items = (entry.get('authored_appearance') or {}).get('attachments') or []
            if not items:
                continue
            _grid = _grid or load_height_grid(input_map)
            poly = _align_footprint_to_instance(_parse_footprint(spec), spec)
            scale = float(spec.get('content_scale', 2.25))
            prof = entry.get('facade_profile') or {}
            inst = row.get('collision_instance') or {}
            doc = field_docs.get(spec.get('source') or '') or {}
            roof_top = (inst.get('wall_top_m') or ((doc.get('roof_form') or {}).get('eave_m'))
                        or spec.get('roof_height_m'))
            wall_rgb = tuple(prof.get('wall_rgb') or (200, 200, 200))
            verts, faces, (ox, oy), rec = build_attachments(
                poly, items, content_scale=scale, storey_m=float(prof.get('storey_m') or 3.0),
                roof_m=float(roof_top) if roof_top else None, wall_rgb=wall_rgb)
            attachment_receipts.append({'source': spec.get('source'), 'items': rec})
            if not faces:
                continue
            ground = terrain_z(_grid, ox, oy)
            if ground is None:
                continue
            name = 'attach_' + spec['source'].replace('/', '_') + '.akm'
            write_akm(collision_mesh_root / name, verts, faces,
                      comment='FL-4876 hand-authored building attachments; presentation only, alpha=255',
                      exact_rgb={tuple(v[6:9]) for v in verts})
            attachment_instances.append({
                'mesh_name': name, 'inst_name': 'attach_' + str(spec.get('inst_name') or spec['source']),
                'transform': [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, float(fmt.HEIGHT_SCALE), 0, ox, oy, ground, 1],
                'flags': 3, 'story_id': -1, 'feature_id': spec.get('feature_id')})
    # FL-4876 AUTHORED-SCENE-MESH-01: terrain-grounded scenery with no carrier (the temple graveyard), owned by
    # authored_scene_fixtures.json rows with emission = authored_scene_mesh.
    scene_instances, scene_receipts = [], []
    if collision_mesh_root is not None and authored_scene_fixtures is not None and terrain_metadata is not None:
        scene_instances, scene_receipts = _emit_authored_scene_meshes(
            authored_scene_fixtures, terrain_metadata, input_map, collision_mesh_root)
    ok = [row for row in per_building if row["ok"]]
    failed = [row for row in per_building if not row["ok"]]
    if output_map.resolve() != input_map.resolve():
        shutil.copy2(input_map, output_map)
    write_a3d(output_map, header, patches, tail)
    if collision_instances or attachment_instances or scene_instances:
        from scripts.pipeline.compile_facade_shell import _load_a3d_edit
        editor = _load_a3d_edit()
        pre,version,existing,start,gens,markers=editor.read_a3d_sections(output_map)
        additions=[editor.a3d_format.A3DInstance(
            mesh_name=r['mesh_name'],inst_name=r['inst_name'],transform=r['transform'],
            flags=r['flags'],story_id=r['story_id'])
            for r in [*collision_instances, *attachment_instances, *scene_instances]]
        editor.write_a3d_sections(output_map,pre,version,[*existing,*additions],start,gens,markers)
    return {
        "ok": not failed,
        "terrain_support_contained": contain_terrain_support,
        "buildings_mesh_collision": len(collision_instances),
        "authored_scene_meshes": scene_receipts,
        "building_attachment_meshes": len(attachment_instances),
        "building_attachments": attachment_receipts,
        "buildings_total": len(specs),
        "buildings_baked": len(ok),
        "buildings_failed": len(failed),
        "height_vertices_written": sum(row["height_vertices"] for row in per_building),
        "visual_cells_written": sum(row["visual_cells"] for row in per_building),
        "buildings_measured_roof": sum(
            1 for row in per_building if row.get("height_model") == "lidar_roof_field"
        ),
        "material_id": material_id,
        "buildings_with_palette": sum(
            1 for row in per_building if row.get("roof_material_id") is not None
        ),
        "roof_cells_written": sum(row.get("roof_cells") or 0 for row in per_building),
        "wall_cells_written": sum(row.get("wall_cells") or 0 for row in per_building),
        "wall_band": wall_band,
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
    ap.add_argument("--palette", type=Path,
                    help="source_palette.json; enables per-building roof/wall materials")
    ap.add_argument("--roof-field-dir", type=Path,
                    help="directory of <source>.roof_field.json measured roof fields")
    ap.add_argument("--fallback-roof-height-m", type=float, default=0.0,
                    help="synthesized terrain-relative roof height, in metres, for buildings "
                         "with no measured roof field. 0 keeps the legacy scalar path unchanged.")
    ap.add_argument("--wall-band", type=float, default=1.5,
                    help="width in world units of the footprint outer band stamped as facade")
    ap.add_argument("--material-inset", type=float, default=0.0,
                    help="Inset baked material cells. Default 0 paints the full OSM footprint brick while height remains inset, avoiding green side/perimeter bleed.")
    ap.add_argument("--summary", type=Path, help="Optional JSON summary output")
    args = ap.parse_args(argv)

    palette = json.loads(args.palette.read_text()) if args.palette else None
    summary = bake(
        args.map, args.buildings, args.output, args.material_id,
        args.footprint_inset, args.material_inset,
        palette=palette,
        roof_field_dir=args.roof_field_dir,
        wall_band=args.wall_band,
        fallback_roof_height_m=args.fallback_roof_height_m,
    )
    text = json.dumps(summary, indent=2, sort_keys=True)
    if args.summary:
        args.summary.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if summary["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
