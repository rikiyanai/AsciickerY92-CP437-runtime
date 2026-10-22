#!/usr/bin/env python3
"""Postprocess SBU OSM A3D terrain into a satellite-style material/topology pass.

This is intentionally deterministic. Google Maps satellite imagery is used only
as a visual target: broad green campus lawns, darker wooded/garden bands, grey
roads/parking, blue water/fountain cells, and more varied terrain relief. The
actual editable data comes from the retained OSM file and existing A3D terrain.
"""

from __future__ import annotations

import argparse
import io
import importlib.util
import json
import math
import os
import shutil
import struct
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
A3D_EDIT_ROOT = PROJECT_ROOT / "docs" / "agent" / "cli-anything"

# Add scripts/pipeline to sys.path for osm_projection import.
_pipeline_dir = str(Path(__file__).resolve().parent)
if _pipeline_dir not in sys.path:
    sys.path.insert(0, _pipeline_dir)
from osm_projection import osm_project as _osm_project_shared
from osm_to_cell import A3D_EXPORT_OFFSET_X, A3D_EXPORT_OFFSET_Y


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fmt = _load_module("a3d_format", PROJECT_ROOT / "addons" / "io_asciicker" / "scene" / "a3d_format.py")
a3d_edit = _load_module("a3d_edit", A3D_EDIT_ROOT / "a3d_edit.py")


PATCH_SIZE = 188
MATERIAL_SIZE = 512
MATERIAL_COUNT = 256
PATCH_WORLD = float(fmt.VISUAL_CELLS)
VERTEX_STEP = PATCH_WORLD / float(fmt.HEIGHT_CELLS)

MAT_WATER = 0
MAT_GRASS = 1
MAT_PAVEMENT = 2
MAT_DIRT = MAT_PAVEMENT
MAT_STONE = 3
MAT_LAND = MAT_STONE
MAT_ROAD = 4
MAT_SAND = MAT_PAVEMENT
MAT_BUILDING = 5
PRIORITY_PARKING = 60
PRIORITY_FOOTWAY = 64
PRIORITY_PLAZA = 68
PRIORITY_ROAD = 90
# NOTE: The run-local A3D material table is the visual contract, not the
# material names.  FL-3838 proved inherited material names were misleading in
# SBU outputs. Normalize the small OSM palette below, then use IDs by visual
# role: land cream, pavement/plaza grey-beige, road white-grey, building beige.
# Renderer class readability must be material-ID-backed: the active terrain
# resolve derives shade from lighting/diffuse, so stored visual shade bits are
# not a reliable way to separate roads from paved surfaces.
MATERIAL_LEGEND = {
    "0": "water — fountains, ponds",
    "1": "grass — lawns, default ground",
    "2": "pavement/sand — non-road paved/parking/plaza/footway/steps, and satellite stone-classified concrete",
    "3": "land/background — cream OSM map land, separated from pavement because asciiid ignores stored shade bits",
    "4": "road/grey — road/asphalt class only; keep visually distinct from non-road paved material",
    "5": "building/warm-beige — direct baked building footprint material",
}
# Never assume the material name matches the rendered color — verify with:
#   python3 scripts/inspect_a3d.py <map.a3d> --terrain-colors
# Terrain visual cell layout (uint16): mat_id[7:0] | shade[14:8] | elev[15]
# (see editor/asciiid.cpp:3832-3843, inline GLSL terrain shader).
# The renderer masks matid = visual & 0xFF for the 256-entry palette lookup
# (asciiid.cpp:3841), shade = (visual >> 8) & 0x7F (asciiid.cpp:3842).
# Bit 15 is the elevation flag (asciiid.cpp:3843, asciiid.cpp:3904).
# The original Y8 map uses this flag on materials 0-3; safe for pipeline use.
ELEVATION_FLAG = 0x8000


def _material_bytes(bg: tuple[int, int, int], fg: tuple[int, int, int], glyphs: tuple[int, int, int, int]) -> bytes:
    out = io.BytesIO()
    for ramp, glyph in enumerate(glyphs):
        for shade in range(16):
            factor = 0.55 + (shade / 15.0) * 0.45
            fg_rgb = tuple(max(0, min(255, round(c * factor))) for c in fg)
            bg_rgb = tuple(max(0, min(255, round(c * factor))) for c in bg)
            fmt.MatCell(fg=fg_rgb, gl=glyph, bg=bg_rgb, flags=0).write(out)
    return out.getvalue()


def _material_bytes_constant(bg: tuple[int, int, int], fg: tuple[int, int, int], glyphs: tuple[int, int, int, int]) -> bytes:
    out = io.BytesIO()
    for ramp, glyph in enumerate(glyphs):
        for shade in range(16):
            fmt.MatCell(fg=fg, gl=glyph, bg=bg, flags=0).write(out)
    return out.getvalue()


def _normalize_osm_material_palette(materials: bytes) -> bytes:
    """Write the OSM terrain visual contract into the A3D material table."""
    if len(materials) != MATERIAL_COUNT * MATERIAL_SIZE:
        raise RuntimeError(f"material table must be {MATERIAL_COUNT * MATERIAL_SIZE} bytes, got {len(materials)}")
    data = bytearray(materials)
    # Keep the canonical CP437 terrain density ramp. FL-4192 found the prior
    # OSM normal-map-style palette wrote spaces into every ramp, so large OSM
    # surfaces rendered as missing/blank glyph texture. Do not introduce
    # FL-4131 extended glyphs here; OSM maps remain CP437-only unless a later
    # explicit sidecar-authoring step opts in.
    canonical_ramp = (32, 46, 58, 35)  # ' ', '.', ':', '#'
    role_bytes = {
        MAT_GRASS: _material_bytes((207, 237, 166), (226, 247, 190), canonical_ramp),
        # Flat OSM-map classes use constant shade rows. The active asciiid terrain
        # shader chooses the material row from diffuse lighting, so ramps turn flat
        # roads/plazas/land into diagonal bands in top-down proof captures.
        MAT_LAND: _material_bytes_constant((252, 248, 218), (255, 252, 232), canonical_ramp),
        MAT_PAVEMENT: _material_bytes_constant((226, 216, 194), (246, 237, 216), canonical_ramp),
        MAT_ROAD: _material_bytes_constant((176, 188, 199), (230, 238, 245), canonical_ramp),
        MAT_BUILDING: _material_bytes_constant((217, 208, 187), (235, 226, 206), canonical_ramp),
    }
    for mat_id, mat_bytes in role_bytes.items():
        start = mat_id * MATERIAL_SIZE
        data[start:start + MATERIAL_SIZE] = mat_bytes
    return bytes(data)

SHADE_DARK_ASPHALT = 0
SHADE_GREY_PAVEMENT = 8
SHADE_LIGHT_CONCRETE = 10


@dataclass
class Feature:
    kind: str
    points: list[tuple[float, float]]
    closed: bool
    width: float
    material: int
    priority: int
    tags: dict[str, str]

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        pad = self.width if not self.closed else 0.0
        return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad


def _road_width(tags: dict[str, str]) -> float:
    """Return renderer-space road width for OSM highway centerlines.

    OSM usually stores campus roads as centerlines, not filled carriageway
    polygons. A too-small buffer reads as a grey outline after surrounding
    pavement/plaza fills are painted. Keep parking aisles narrow, but make
    service/unclassified campus roads visibly own their full drivable strip.
    """
    service = tags.get("service", "")
    if service == "parking_aisle":
        return 4.5
    if service in {"driveway", "alley"}:
        return 7.0
    try:
        lanes = float(tags.get("lanes", "") or 0.0)
    except ValueError:
        lanes = 0.0
    if lanes >= 2.0:
        return 12.0
    if lanes >= 1.0:
        return 10.0
    if tags.get("highway") in {"service", "unclassified", "residential"}:
        return 10.0
    return 7.0


def _osm_project(lat: float, lon: float, scene_lat: float, scene_lon: float) -> tuple[float, float]:
    """Delegate to canonical shared projection (osm_projection.py)."""
    return _osm_project_shared(lat, lon, scene_lat, scene_lon)


def _load_a3d(path: Path):
    with path.open("rb") as f:
        header = fmt.A3DHeader.from_file(f)
        patches = [fmt.A3DPatch.from_file(f) for _ in range(header.num_patches)]
        materials = f.read(MATERIAL_COUNT * MATERIAL_SIZE)
        if len(materials) != MATERIAL_COUNT * MATERIAL_SIZE:
            raise RuntimeError(f"truncated material section in {path}")
        raw_fmt_version = struct.unpack("<i", f.read(4))[0]
        inst_fmt = -raw_fmt_version if raw_fmt_version < 0 else raw_fmt_version
        inst_count = struct.unpack("<i", f.read(4))[0]
        instances = [fmt.A3DInstance.from_file(f, format_version=inst_fmt) for _ in range(inst_count)]
        player_start = None
        if raw_fmt_version <= -4:
            has_ps_raw = f.read(4)
            if len(has_ps_raw) == 4 and struct.unpack("<i", has_ps_raw)[0]:
                player_start = fmt.A3DPlayerStart.from_file(f)
        enemy_gens = []
        enemy_raw = f.read(4)
        if len(enemy_raw) == 4:
            enemy_count = struct.unpack("<i", enemy_raw)[0]
            enemy_gens = [fmt.A3DEnemyGen.from_file(f) for _ in range(enemy_count)]
        markers = []
        marker_raw = f.read(4)
        if len(marker_raw) == 4:
            marker_count = struct.unpack("<i", marker_raw)[0]
            markers = [fmt.A3DMinimapMarker.from_file(f) for _ in range(marker_count)]
    return header, patches, materials, raw_fmt_version, instances, player_start, enemy_gens, markers


def _build_pre_bytes(header, patches, materials: bytes) -> bytes:
    from io import BytesIO

    out = BytesIO()
    header.num_patches = len(patches)
    header.write(out)
    for patch in patches:
        patch.write(out)
    out.write(materials)
    return out.getvalue()


def _write_a3d(path: Path, header, patches, materials, raw_fmt_version, instances, player_start, enemy_gens, markers):
    pre = _build_pre_bytes(header, patches, materials)
    a3d_edit.write_a3d_sections(path, pre, raw_fmt_version, instances, player_start, enemy_gens, markers)


def _load_features(osm_path: Path, manifest_path: Path, metadata_path: Path) -> list[Feature]:
    manifest = json.loads(manifest_path.read_text())
    metadata = json.loads(metadata_path.read_text())
    # Prefer pipeline-embedded scene center; fall back to bbox midpoint.
    scene_lat = metadata.get("scene_lat")
    scene_lon = metadata.get("scene_lon")
    if scene_lat is None or scene_lon is None:
        bbox = manifest["bbox"]
        scene_lat = (float(bbox["min_lat"]) + float(bbox["max_lat"])) / 2.0
        scene_lon = (float(bbox["min_lon"]) + float(bbox["max_lon"])) / 2.0
    scale = float(metadata.get("content_scale") or 1.0)
    shift = metadata.get("terrain_shift") or {"x": 0.0, "y": 0.0}

    root = ET.parse(osm_path).getroot()
    nodes: dict[str, tuple[float, float]] = {}
    for node in root.findall("node"):
        lat = float(node.attrib["lat"])
        lon = float(node.attrib["lon"])
        x, y = _osm_project(lat, lon, scene_lat, scene_lon)
        # Convert to engine-space A3D coordinates. export_a3d.py applies the
        # same -32/-32 patch-grid offset to terrain patches and instances; OSM
        # material painting must use that coordinate frame or it lands one
        # patch-grid offset away from the baked buildings.
        nodes[node.attrib["id"]] = (
            x * scale + float(shift.get("x", 0.0)) + A3D_EXPORT_OFFSET_X,
            y * scale + float(shift.get("y", 0.0)) + A3D_EXPORT_OFFSET_Y,
        )

    features: list[Feature] = []
    for way in root.findall("way"):
        refs = [nd.attrib.get("ref") for nd in way.findall("nd")]
        pts = [nodes[r] for r in refs if r in nodes]
        if len(pts) < 2:
            continue
        tags = {tag.attrib.get("k", ""): tag.attrib.get("v", "") for tag in way.findall("tag")}
        is_ring = len(pts) >= 4 and refs[0] == refs[-1]

        if tags.get("natural") == "water" or tags.get("amenity") == "fountain" or tags.get("waterway"):
            features.append(Feature("water", pts, is_ring, 0.0, MAT_WATER, 80, tags))
        elif tags.get("building") or tags.get("building:part"):
            # Direct footprint bake owns building material and height.  Painting
            # OSM building polygons here creates green edge rings when the later
            # bake is inset to keep walls inside the footprint.
            continue
        elif tags.get("amenity") in {"parking", "parking_space"} or tags.get("parking"):
            features.append(Feature("parking", pts, is_ring, 0.0, MAT_PAVEMENT, PRIORITY_PARKING, tags))
        elif tags.get("place") == "square" or tags.get("man_made") == "courtyard":
            # Plazas and courtyards — paved open areas (concrete at SBU).
            features.append(Feature("plaza", pts, is_ring, 0.0, MAT_PAVEMENT, PRIORITY_PLAZA, tags))
        elif tags.get("highway"):
            hw = tags.get("highway")
            surface = tags.get("surface", "")
            if hw == "footway":
                fw = tags.get("footway", "")
                width = 5.0 if fw == "crossing" else 3.25
                # Non-road pedestrian concrete must stay distinct from roads.
                mat = MAT_PAVEMENT
                features.append(Feature("footway", pts, False, width, mat, PRIORITY_FOOTWAY, tags))
            elif hw == "steps":
                features.append(Feature("steps", pts, is_ring, 4.0, MAT_PAVEMENT, 66, tags))
            elif hw == "pedestrian":
                features.append(Feature("road", pts, is_ring, 8.0, MAT_ROAD, PRIORITY_ROAD, tags))
            elif hw in {"service", "unclassified"}:
                features.append(Feature("road", pts, False, _road_width(tags), MAT_ROAD, PRIORITY_ROAD, tags))
            elif hw in {"cycleway", "path"}:
                features.append(Feature("road", pts, False, 4.0, MAT_ROAD, PRIORITY_ROAD, tags))
            else:
                features.append(Feature("road", pts, False, _road_width(tags), MAT_ROAD, PRIORITY_ROAD, tags))
        elif tags.get("man_made") == "planter" or tags.get("garden:type") in {"flowerbed", "rock_garden"}:
            features.append(Feature("garden", pts, is_ring, 0.0, MAT_DIRT, 55, tags))
        elif tags.get("surface") in {"asphalt", "concrete", "concrete:plates", "paved", "paving_stones", "brick"}:
            features.append(Feature("paved", pts, is_ring, 0.0, MAT_PAVEMENT, 50, tags))
        elif tags.get("barrier") == "hedge":
            features.append(Feature("hedge", pts, is_ring, 2.0, MAT_DIRT | ELEVATION_FLAG, 45, tags))
        elif tags.get("natural") == "wood" or tags.get("landuse") == "forest":
            features.append(Feature("wood", pts, is_ring, 0.0, MAT_DIRT | ELEVATION_FLAG, 40, tags))
        elif tags.get("landuse") == "grass" or tags.get("leisure") in {"garden", "outdoor_seating"}:
            features.append(Feature("grass", pts, is_ring, 0.0, MAT_GRASS, 30, tags))

    # Individual trees from OSM nodes — paint a small dirt circle under each.
    for node in root.findall("node"):
        node_tags = {tag.attrib.get("k", ""): tag.attrib.get("v", "") for tag in node.findall("tag")}
        if node_tags.get("natural") != "tree":
            continue
        nid = node.attrib["id"]
        if nid not in nodes:
            continue
        wx, wy = nodes[nid]
        # 3-cell diameter disc (radius=ceil(1.5)=2 cells) centered on the tree.
        features.append(Feature("tree", [(wx, wy)], False, 3.0, MAT_DIRT, 42, node_tags))

    return features


def _point_in_poly(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            cross_x = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
            if x < cross_x:
                inside = not inside
        j = i
    return inside


def _distance_to_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx = bx - ax
    dy = by - ay
    denom = dx * dx + dy * dy
    if denom <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _feature_visual(feature: Feature) -> int:
    """Return the exact terrain visual value for a feature.

    Material ID is the readability contract. The active renderer derives the
    final shade from lighting/diffuse, so fixed visual shade bits cannot be the
    owner for road-vs-pavement separation.
    """
    return feature.material & 0xFF


def _paint_cell(patches_by_xy, wx: float, wy: float, visual: int) -> bool:
    px = math.floor(wx / PATCH_WORLD)
    py = math.floor(wy / PATCH_WORLD)
    patch = patches_by_xy.get((px, py))
    if patch is None:
        return False
    cx = int(max(0, min(fmt.VISUAL_CELLS - 1, math.floor(wx - px * PATCH_WORLD))))
    cy = int(max(0, min(fmt.VISUAL_CELLS - 1, math.floor(wy - py * PATCH_WORLD))))
    # Don't paint over building-baked walls/roofs.
    if not _is_ground_cell(patch, cy, cx):
        return False
    if patch.visual[cy][cx] != visual:
        patch.visual[cy][cx] = visual
        return True
    return False


def _is_ground_cell(patch, vy: int, vx: int, baseline: int = 128, wall_threshold: int = 80) -> bool:
    """Return True if the cell is at ground level (safe to repaint).

    Building walls/roofs have steep height gradients baked by asciiid.
    We detect them by checking if ANY adjacent height vertex is significantly
    above baseline.  Cells near tall structures are left alone.
    """
    # Map visual cell to height vertices (HEIGHT_CELLS=4 for VISUAL_CELLS=8).
    hx = vx * fmt.HEIGHT_CELLS // fmt.VISUAL_CELLS
    hy = vy * fmt.HEIGHT_CELLS // fmt.VISUAL_CELLS
    for dy in range(2):
        for dx in range(2):
            nx = min(fmt.HEIGHT_CELLS, hx + dx)
            ny = min(fmt.HEIGHT_CELLS, hy + dy)
            if patch.height[ny][nx] > baseline + wall_threshold:
                return False
    return True


def _paint_features(patches, features: list[Feature]) -> dict[str, int]:
    patches_by_xy = {(p.x, p.y): p for p in patches}
    counts: dict[str, int] = {"base_grass": 0, "bake_preserved": 0}

    # Reset ground-level cells to grass; preserve building-baked walls/roofs.
    for p in patches:
        for y in range(fmt.VISUAL_CELLS):
            for x in range(fmt.VISUAL_CELLS):
                if _is_ground_cell(p, y, x):
                    if p.visual[y][x] != MAT_GRASS:
                        counts["base_grass"] += 1
                        p.visual[y][x] = MAT_GRASS
                else:
                    counts["bake_preserved"] += 1

    for feature in sorted(features, key=lambda f: f.priority):
        if feature.material == MAT_GRASS:
            continue
        visual = _feature_visual(feature)
        if not feature.closed:
            radius = max(1, int(math.ceil(feature.width * 0.5)))
            if len(feature.points) == 1:
                # Single-point feature (e.g. tree node) -- paint a disc.
                wx, wy = feature.points[0]
                for oy in range(-radius, radius + 1):
                    for ox in range(-radius, radius + 1):
                        cx = math.floor(wx + ox) + 0.5
                        cy = math.floor(wy + oy) + 0.5
                        if math.hypot(cx - wx, cy - wy) <= feature.width * 0.5:
                            if _paint_cell(patches_by_xy, cx, cy, visual):
                                counts[feature.kind] = counts.get(feature.kind, 0) + 1
            else:
                for a, b in zip(feature.points, feature.points[1:]):
                    seg_len = max(1.0, math.hypot(b[0] - a[0], b[1] - a[1]))
                    steps = max(1, int(math.ceil(seg_len * 2.0)))
                    for step in range(steps + 1):
                        t = step / steps
                        wx = a[0] + (b[0] - a[0]) * t
                        wy = a[1] + (b[1] - a[1]) * t
                        for oy in range(-radius, radius + 1):
                            for ox in range(-radius, radius + 1):
                                cx = math.floor(wx + ox) + 0.5
                                cy = math.floor(wy + oy) + 0.5
                                if math.hypot(cx - wx, cy - wy) <= feature.width * 0.5:
                                    if _paint_cell(patches_by_xy, cx, cy, visual):
                                        counts[feature.kind] = counts.get(feature.kind, 0) + 1
            continue

        min_x, min_y, max_x, max_y = feature.bounds
        cx0 = math.floor(min_x)
        cy0 = math.floor(min_y)
        cx1 = math.ceil(max_x)
        cy1 = math.ceil(max_y)
        for cy in range(cy0, cy1 + 1):
            wy = cy + 0.5
            for cx in range(cx0, cx1 + 1):
                wx = cx + 0.5
                hit = False
                if feature.closed:
                    hit = _point_in_poly(wx, wy, feature.points)
                else:
                    for a, b in zip(feature.points, feature.points[1:]):
                        if _distance_to_segment(wx, wy, a[0], a[1], b[0], b[1]) <= feature.width * 0.5:
                            hit = True
                            break
                if hit and _paint_cell(patches_by_xy, wx, wy, visual):
                    counts[feature.kind] = counts.get(feature.kind, 0) + 1
    return counts


def _topology_height(wx: float, wy: float) -> int:
    broad = 72.0 * math.sin(wx / 420.0) + 64.0 * math.cos(wy / 380.0)
    diagonal = 52.0 * math.sin((wx + wy) / 610.0)
    medium = 28.0 * math.sin(wx / 93.0) * math.cos(wy / 117.0)
    fine = (6.0 * math.sin(wx / 17.3) * math.cos(wy / 13.7)
            + 4.0 * math.sin((wx + 2 * wy) / 23.1)
            + 3.0 * math.cos((3 * wx - wy) / 19.7))
    h = 288.0 + broad + diagonal + medium + fine
    h = max(128.0, min(576.0, h))
    return int(round(h))


def _clamp_empty_default_heights(patches, empty_default: int = 0xA000) -> int:
    """Replace 0xA000 (empty default) height vertices with baseline.

    The topology mesh bake can leave uninitialized vertices at 0xA000.
    These render as massive spikes.  Clamp them to TERRAIN_EXPORT_BASELINE.
    """
    clamped = 0
    baseline = 128  # TERRAIN_EXPORT_BASELINE
    for patch in patches:
        for hy in range(fmt.HEIGHT_CELLS + 1):
            for hx in range(fmt.HEIGHT_CELLS + 1):
                if int(patch.height[hy][hx]) == empty_default:
                    patch.height[hy][hx] = baseline
                    clamped += 1
    return clamped


def _vary_ground_heights(patches) -> dict[str, int]:
    changed = 0
    protected = 0
    for patch in patches:
        for hy in range(fmt.HEIGHT_CELLS + 1):
            wy = patch.y * PATCH_WORLD + hy * VERTEX_STEP
            for hx in range(fmt.HEIGHT_CELLS + 1):
                old = int(patch.height[hy][hx])
                if old > 640:
                    protected += 1
                    continue
                wx = patch.x * PATCH_WORLD + hx * VERTEX_STEP
                # Broad rolling hills (campus-scale undulation)
                broad = 72.0 * math.sin(wx / 420.0) + 64.0 * math.cos(wy / 380.0)
                diagonal = 52.0 * math.sin((wx + wy) / 610.0)
                # Medium detail (building-block scale)
                medium = 28.0 * math.sin(wx / 93.0) * math.cos(wy / 117.0)
                # Fine per-vertex noise (patch-scale variation for lighting)
                fine = (6.0 * math.sin(wx / 17.3) * math.cos(wy / 13.7)
                        + 4.0 * math.sin((wx + 2 * wy) / 23.1)
                        + 3.0 * math.cos((3 * wx - wy) / 19.7))
                h = 288.0 + broad + diagonal + medium + fine
                h = max(128.0, min(576.0, h))
                new = int(round(h))
                if old != new:
                    patch.height[hy][hx] = new
                    changed += 1
    return {"changed_ground_vertices": changed, "protected_high_vertices": protected}


def _apply_ground_visual_variation(patches) -> dict[str, int]:
    """Encode visible ground variation from the generated height field.

    The upstream map's terrain reads as varied because material ramps/glyphs are
    exercised, not because every grass cell is the same shade/elevation bit.
    Height variation alone can still look flat in asciiid if visual cells stay
    at `mat=grass, shade=0, elev=0`. Keep OSM roads/paved/water/building cells
    under their material owners and only vary natural ground slots.
    """
    changed = 0
    preserved = 0
    for patch in patches:
        for vy in range(fmt.VISUAL_CELLS):
            for vx in range(fmt.VISUAL_CELLS):
                visual = int(patch.visual[vy][vx])
                mat = visual & 0xFF
                if mat not in {MAT_GRASS, MAT_DIRT}:
                    preserved += 1
                    continue
                # This pass runs after _vary_ground_heights(), where normal
                # campus terrain can be 128..576. The feature-paint baseline
                # test would classify that as non-ground, so protect only the
                # true building-bake range here.
                hx = vx * fmt.HEIGHT_CELLS // fmt.VISUAL_CELLS
                hy = vy * fmt.HEIGHT_CELLS // fmt.VISUAL_CELLS
                if any(
                    patch.height[min(fmt.HEIGHT_CELLS, hy + dy)][min(fmt.HEIGHT_CELLS, hx + dx)] > 640
                    for dy in range(2)
                    for dx in range(2)
                ):
                    preserved += 1
                    continue

                wx = patch.x * PATCH_WORLD + vx + 0.5
                wy = patch.y * PATCH_WORLD + vy + 0.5
                h00 = int(patch.height[vy // 2][vx // 2])
                h11 = int(patch.height[min(fmt.HEIGHT_CELLS, vy // 2 + 1)][min(fmt.HEIGHT_CELLS, vx // 2 + 1)])
                local_delta = abs(h11 - h00)
                ripple = (
                    2.5 * math.sin(wx / 11.0)
                    + 2.0 * math.cos(wy / 9.0)
                    + 1.5 * math.sin((wx + wy) / 15.0)
                )
                shade = int(max(2, min(13, round(7 + ripple + local_delta / 20.0))))
                elev = 0x8000 if (local_delta >= 10 or math.sin((wx - wy) / 21.0) > 0.55) else 0
                new_visual = mat | (shade << 8) | elev
                if new_visual != visual:
                    patch.visual[vy][vx] = new_visual
                    changed += 1
    return {"changed_ground_visual_cells": changed, "preserved_non_ground_visual_cells": preserved}


def _sample_height(patches_by_xy, x: float, y: float) -> float | None:
    px = math.floor(x / PATCH_WORLD)
    py = math.floor(y / PATCH_WORLD)
    patch = patches_by_xy.get((px, py))
    if patch is None:
        return None
    lx = max(0.0, min(PATCH_WORLD, x - px * PATCH_WORLD))
    ly = max(0.0, min(PATCH_WORLD, y - py * PATCH_WORLD))
    fx = lx / VERTEX_STEP
    fy = ly / VERTEX_STEP
    x0 = min(fmt.HEIGHT_CELLS - 1, max(0, int(math.floor(fx))))
    y0 = min(fmt.HEIGHT_CELLS - 1, max(0, int(math.floor(fy))))
    x1 = min(fmt.HEIGHT_CELLS, x0 + 1)
    y1 = min(fmt.HEIGHT_CELLS, y0 + 1)
    tx = max(0.0, min(1.0, fx - x0))
    ty = max(0.0, min(1.0, fy - y0))
    h00 = patch.height[y0][x0]
    h10 = patch.height[y0][x1]
    h01 = patch.height[y1][x0]
    h11 = patch.height[y1][x1]
    h0 = h00 + (h10 - h00) * tx
    h1 = h01 + (h11 - h01) * tx
    return h0 + (h1 - h0) * ty


def _write_feature_sidecar(map_path: Path, features: list[Feature], metadata_path: Path):
    """Write a JSON sidecar mapping every painted OSM feature to world/cell/latlon coords.

    This file is the bridge between OSM data and the A3D world.  Future agents
    using asciiid MCP commands can load this to know:
    - What OSM feature is at any world coordinate
    - The lat/lon of any feature for satellite cross-reference
    - The material ID painted for each feature
    - All original OSM tags for context (surface type, name, etc.)

    Output: <map_path>.features.json alongside the A3D file.
    """
    from osm_projection import osm_project_inverse

    metadata = json.loads(metadata_path.read_text())
    scene_lat = metadata.get("scene_lat", 0)
    scene_lon = metadata.get("scene_lon", 0)
    content_scale = metadata.get("content_scale", 1)
    shift_x = metadata.get("terrain_shift", {}).get("x", 0)
    shift_y = metadata.get("terrain_shift", {}).get("y", 0)
    cal_x = metadata.get("calibration_offset_x", 0)
    cal_y = metadata.get("calibration_offset_y", 0)

    def world_to_ll(wx, wy):
        x_m = (wx - A3D_EXPORT_OFFSET_X - cal_x - shift_x) / content_scale
        y_m = (wy - A3D_EXPORT_OFFSET_Y - cal_y - shift_y) / content_scale
        return osm_project_inverse(x_m, y_m, scene_lat, scene_lon)

    mat_names = {0: "water", 1: "grass", 2: "pavement", 3: "stone", 4: "road", 5: "building"}

    entries = []
    for i, f in enumerate(features):
        bx0, by0, bx1, by1 = f.bounds
        cx = (bx0 + bx1) / 2
        cy = (by0 + by1) / 2
        clat, clon = world_to_ll(cx, cy)
        sw_lat, sw_lon = world_to_ll(bx0, by0)
        ne_lat, ne_lon = world_to_ll(bx1, by1)

        entries.append({
            "id": i,
            "kind": f.kind,
            "material_id": f.material & 0xFF,
            "material_name": mat_names.get(f.material & 0xFF, f"id{f.material & 0xFF}"),
            "elevation_flag": bool(f.material & ELEVATION_FLAG),
            "priority": f.priority,
            "is_polygon": f.closed,
            "width": f.width,
            "world_bounds": {"min_x": round(bx0, 1), "min_y": round(by0, 1),
                             "max_x": round(bx1, 1), "max_y": round(by1, 1)},
            "world_centroid": {"x": round(cx, 1), "y": round(cy, 1)},
            "cell_bounds": {"min_x": int(math.floor(bx0)), "min_y": int(math.floor(by0)),
                            "max_x": int(math.ceil(bx1)), "max_y": int(math.ceil(by1))},
            "latlon_centroid": {"lat": round(clat, 7), "lon": round(clon, 7)},
            "latlon_bounds": {"sw_lat": round(sw_lat, 7), "sw_lon": round(sw_lon, 7),
                              "ne_lat": round(ne_lat, 7), "ne_lon": round(ne_lon, 7)},
            "osm_tags": f.tags,
            "vertex_count": len(f.points),
            "world_vertices": [[round(x, 2), round(y, 2)] for x, y in f.points],
        })

    sidecar = {
        "_doc": ("OSM feature to A3D cell mapping. Generated by sbu_satellite_style_postprocess.py. "
                 "Each entry describes one OSM feature painted onto the terrain with its world coordinates, "
                 "cell bounds, lat/lon, material assignment, and original OSM tags. "
                 "Use world_bounds or cell_bounds to find which feature is at a given position. "
                 "Use latlon_centroid to cross-reference with satellite imagery. "
                 "Use osm_tags for surface type, name, and other OSM metadata. "
                 "Material IDs are palette IDs, not semantic color names: "
                 "0=water, 1=grass, 2=pavement/sand, 3=stone/green palette slot, 4=road/grey, 5=building/brick."),
        "_projection": {
            "scene_lat": scene_lat,
            "scene_lon": scene_lon,
            "content_scale": content_scale,
            "terrain_shift": {"x": shift_x, "y": shift_y},
            "calibration_offset": {"x": cal_x, "y": cal_y},
        },
        "_material_legend": MATERIAL_LEGEND,
        "_mcp_usage": {
            "query_cell": "GET_CELL_VISUAL <x> <y> -- returns mat_id at cell",
            "paint_cell": "SET_CELL_MATERIAL <x> <y> <mat_id> -- paint single cell",
            "paint_batch": "BATCH_SET_CELLS <mat_id> <N> <x1> <y1> ... -- paint up to 10k cells",
            "place_mesh": "PLACE_MESH <file> <x> <y> <z> <scale> -- place fixture at world coords",
            "query_height": "QUERY_TERRAIN_HEIGHT <x> <y> -- get Z for mesh placement",
            "satellite": "SATELLITE_VIEW [x y] -- open satellite comparison view in browser",
        },
        "feature_count": len(entries),
        "features": entries,
    }

    out_path = map_path.with_suffix(map_path.suffix + ".features.json")
    out_path.write_text(json.dumps(sidecar, indent=2, sort_keys=False))
    print(f"[satellite-style] feature sidecar: {out_path} ({len(entries)} features)")


def postprocess(map_path: Path, osm_path: Path, manifest_path: Path, metadata_path: Path, *, no_height: bool = False):
    t0 = time.time()
    header, patches, materials, raw_fmt_version, instances, player_start, enemy_gens, markers = _load_a3d(map_path)
    materials = _normalize_osm_material_palette(materials)
    print(f"[satellite-style] loaded patches={len(patches)} elapsed={time.time() - t0:.1f}s", flush=True)
    features = _load_features(osm_path, manifest_path, metadata_path)
    print(f"[satellite-style] loaded features={len(features)} elapsed={time.time() - t0:.1f}s", flush=True)
    paint_counts = _paint_features(patches, features)
    print(f"[satellite-style] painted materials elapsed={time.time() - t0:.1f}s", flush=True)
    clamped = _clamp_empty_default_heights(patches)
    if clamped:
        print(f"[satellite-style] clamped {clamped} empty-default (0xA000) height vertices", flush=True)
    height_counts = {"changed_ground_vertices": 0, "protected_high_vertices": 0}
    visual_variation_counts = {"changed_ground_visual_cells": 0, "preserved_non_ground_visual_cells": 0}
    if not no_height:
        height_counts = _vary_ground_heights(patches)
        visual_variation_counts = _apply_ground_visual_variation(patches)
        print(f"[satellite-style] varied heights elapsed={time.time() - t0:.1f}s", flush=True)

    if player_start is not None:
        h = _sample_height({(p.x, p.y): p for p in patches}, float(player_start.pos[0]), float(player_start.pos[1]))
        if h is not None:
            player_start.pos[2] = float(h) + 16.0

    _write_a3d(map_path, header, patches, materials, raw_fmt_version, instances, player_start, enemy_gens, markers)
    print(f"[satellite-style] wrote map elapsed={time.time() - t0:.1f}s", flush=True)

    # Write feature sidecar for MCP agents
    _write_feature_sidecar(map_path, features, metadata_path)
    print(f"[satellite-style] wrote feature sidecar elapsed={time.time() - t0:.1f}s", flush=True)

    heights = [int(h) for p in patches for row in p.height for h in row]
    visual_counts: dict[int, int] = {}
    for p in patches:
        for row in p.visual:
            for val in row:
                mat_id = int(val) & 0xFF
                visual_counts[mat_id] = visual_counts.get(mat_id, 0) + 1

    return {
        "map": str(map_path),
        "features": len(features),
        "paint": paint_counts,
        "height": height_counts,
        "visual_variation": visual_variation_counts,
        "height_min": min(heights),
        "height_max": max(heights),
        "height_range": max(heights) - min(heights),
        "visual_top": sorted(visual_counts.items(), key=lambda kv: kv[1], reverse=True)[:12],
        "player_start": list(player_start.pos) if player_start is not None else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", required=True, type=Path)
    parser.add_argument("--osm", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--no-height", action="store_true")
    args = parser.parse_args()

    path = args.map
    bak = path.with_suffix(path.suffix + ".satellite-style-input.bak")
    if not bak.exists():
        shutil.copy2(path, bak)

    result = postprocess(path, args.osm, args.manifest, args.metadata, no_height=args.no_height)
    print(json.dumps(result, indent=2, sort_keys=True))
    print(f"rollback_input={bak}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
