# OSM Pipeline -- full automated workflow from blosm import to engine-ready export
# Chains: extrude buildings, paint windows, prepare meshes, clean scene, export A3D
# [DEPENDENCY:BLENDER]

import math
import os
import random
import re
import json
import xml.etree.ElementTree as ET

import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator, PropertyGroup
from mathutils import Vector

from io_asciicker import path_utils
from io_asciicker.tools.building_painter import (
    _ensure_color_layer,
    _paint_building,
)

PIPELINE_BUILDING_PROP = "asciicker_pipeline_building"
PIPELINE_BUILDING_CANDIDATE_PROP = "asciicker_pipeline_building_candidate"
PRUNE_DUPLICATE_BUILDING_PROP = "asciicker_prune_duplicate_building"


def _sanitize_building_name(name, max_len=60):
    """Sanitize an OSM building name to a filesystem-safe AKM stem (spaces→_, unsafe→_)."""
    name = (name or '').strip()
    if not name:
        return ''
    # Replace path-unsafe chars and whitespace with underscore
    name = re.sub(r'[/\\:*?"<>|]+', '_', name)
    name = re.sub(r'\s+', '_', name)
    # Strip any remaining non-alphanumeric / dash / dot / underscore chars
    name = re.sub(r'[^\w\-.]', '', name)
    name = re.sub(r'_+', '_', name).strip('_.')
    return name[:max_len] if name else ''


def _find_meshes_dir():
    """Locate the engine assets/meshes/ directory from the repo root."""
    try:
        repo = path_utils.find_repo_root(__file__)
        if not repo:
            repo = path_utils.find_repo_root_from_env()
        if repo:
            d = os.path.join(repo, "assets", "meshes")
            if os.path.isdir(d):
                return d
    except Exception:
        pass
    return ""


def _write_active_mesh_root_pointer(meshes_dir, output_subdir):
    """Write .active_mesh_root pointer so the C++ engine can resolve pipeline AKMs.

    The engine's ReadActiveMeshRoot() checks:
      1. ASCIICKER_ACTIVE_MESH_ROOT env var (preferred)
      2. {base_path}assets/meshes/osm_runs/.active_mesh_root (fallback)

    Content is a relative path from the project root to the output meshes dir.
    This ensures manual Blender pipeline runs work without sbu_e2e_run.py.
    """
    try:
        repo_root = path_utils.find_repo_root(__file__)
        if not repo_root:
            repo_root = path_utils.find_repo_root_from_env()
        if not repo_root:
            return  # Can't determine project root, skip pointer

        pointer_dir = os.path.join(meshes_dir, "osm_runs")
        pointer_path = os.path.join(pointer_dir, ".active_mesh_root")
        rel_path = os.path.relpath(output_subdir, repo_root)

        os.makedirs(pointer_dir, exist_ok=True)
        with open(pointer_path, "w", encoding="utf-8") as fh:
            fh.write(rel_path + "\n")
        print(f"  [active_mesh_root] {pointer_path} -> {rel_path}")
    except Exception:
        pass  # Non-fatal — engine still works with env var or direct assets/meshes/


def _is_source_blosm_building(obj):
    """True for building meshes before the pipeline stamps explicit membership."""
    if obj.type != 'MESH':
        return False
    if obj.get('building'):
        return True
    if obj.name.lower().startswith('none_buildings'):
        return True
    return obj.name.startswith('Building_')


def _is_pipeline_building(obj):
    """True once Step 2b stamps the post-rename authoritative building set."""
    return obj.type == 'MESH' and bool(obj.get(PIPELINE_BUILDING_PROP))


def _set_pipeline_building_marker(objects, enabled=True):
    """Stamp or clear the authoritative post-rename building membership marker."""
    value = 1 if enabled else None
    updated = 0
    for obj in objects:
        if obj is None or obj.type != 'MESH':
            continue
        if enabled:
            obj[PIPELINE_BUILDING_PROP] = value
        else:
            obj.pop(PIPELINE_BUILDING_PROP, None)
        updated += 1
    return updated


def _get_blosm_buildings(context):
    """Return building meshes, preferring the explicit post-rename pipeline set."""
    pipeline_buildings = [obj for obj in context.scene.objects if _is_pipeline_building(obj)]
    if pipeline_buildings:
        return pipeline_buildings
    return [obj for obj in context.scene.objects if _is_source_blosm_building(obj)]


def _get_nonbuilding_blosm(context):
    """Return blosm objects that are NOT buildings (roads, vegetation, empties, etc.)."""
    building_objs = set(o.name for o in _get_blosm_buildings(context))
    result = []
    for obj in context.scene.objects:
        if _is_pipeline_utility_object(obj):
            continue
        if obj.name in building_objs:
            continue
        # blosm objects have OSM custom properties
        is_blosm = (obj.get('highway') or obj.get('landuse') or obj.get('natural')
                     or obj.get('amenity') or obj.get('landcover')
                     or obj.get('waterway') or obj.get('leisure'))
        # blosm parent empties or 2D-mode named objects
        is_blosm_parent = (obj.type == 'EMPTY' and obj.name.startswith('None_'))
        is_blosm_named = obj.name.startswith('None_') and obj.type in ('MESH', 'CURVE')
        if is_blosm or is_blosm_parent or is_blosm_named:
            result.append(obj)
    return result


def _get_terrain_object(context):
    """Find the Terrain object in the scene, or None."""
    for obj in context.scene.objects:
        if obj.type == 'MESH' and obj.name.lower().startswith('terrain'):
            return obj
    return None


def _is_pipeline_utility_object(obj):
    """True for editor utility objects that should never export as gameplay meshes."""
    if obj.type in {'LIGHT', 'CAMERA'}:
        return True
    name_lower = obj.name.lower()
    if name_lower.startswith('terrain'):
        return True
    return name_lower in {'camera', 'sun', 'lamp'}


def _blosm_extent(context, buildings_only=False):
    """XY bounding box of blosm objects. Returns (min_x,min_y,max_x,max_y) or None.

    If buildings_only=True, only considers building meshes — excludes distant
    road curves and vegetation that inflate the bounding box.
    [RC-18 FIX] Terrain was 17x oversized because distant road curves inflated
    the extent from ~500x500 to 2312x2312.
    """
    min_x = min_y = float('inf')
    max_x = max_y = float('-inf')
    found = False

    for obj in context.scene.objects:
        if obj.type not in ('MESH', 'CURVE', 'EMPTY'):
            continue

        if buildings_only:
            # Only buildings and building-related objects
            is_target = (obj.get('building')
                         or obj.name.lower().startswith('none_buildings')
                         or obj.name.lower().startswith('building_'))
            if not is_target:
                continue
        else:
            is_blosm = (obj.get('building') or obj.get('highway') or obj.get('landuse')
                         or obj.get('natural') or obj.get('amenity') or obj.get('landcover')
                         or obj.get('waterway') or obj.get('leisure'))
            is_blosm_parent = (obj.type == 'EMPTY' and obj.name.startswith('None_'))
            is_blosm_named = obj.name.startswith('None_') and obj.type in ('MESH', 'CURVE')
            if not is_blosm and not is_blosm_parent and not is_blosm_named:
                continue

        found = True
        if obj.type == 'EMPTY':
            co = obj.matrix_world.translation
            min_x = min(min_x, co.x)
            min_y = min(min_y, co.y)
            max_x = max(max_x, co.x)
            max_y = max(max_y, co.y)
        else:
            for corner in obj.bound_box:
                co = obj.matrix_world @ Vector(corner)
                min_x = min(min_x, co[0])
                min_y = min(min_y, co[1])
                max_x = max(max_x, co[0])
                max_y = max(max_y, co[1])

    if not found:
        return None
    return (min_x, min_y, max_x, max_y)


def _set_object_origins_to_bounds(context, objects):
    """Set mesh origins to geometry bounds for all provided objects."""
    meshes = [obj for obj in objects if obj and obj.type == 'MESH']
    if not meshes:
        return 0
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')
    for obj in meshes:
        obj.select_set(True)
    context.view_layer.objects.active = meshes[0]
    bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')
    return len(meshes)


def _auto_terrain_from_blosm(context):
    """Create terrain plane covering blosm buildings (8-unit patch grid, 1-patch margin).

    [RC-18 FIX] Uses buildings_only extent — distant road curves and vegetation
    were inflating the terrain from ~500x500 to 2312x2312 (17x oversized).
    Falls back to full extent if no buildings found.
    """
    extent = _blosm_extent(context, buildings_only=True)
    if extent is None:
        extent = _blosm_extent(context)
    if extent is None:
        return None

    min_x, min_y, max_x, max_y = extent
    patch_size = 8

    # Snap to patch grid with 1-patch margin
    grid_min_x = math.floor(min_x / patch_size) - 1
    grid_min_y = math.floor(min_y / patch_size) - 1
    grid_max_x = math.ceil(max_x / patch_size) + 1
    grid_max_y = math.ceil(max_y / patch_size) + 1

    num_patches_x = grid_max_x - grid_min_x
    num_patches_y = grid_max_y - grid_min_y
    # Use the larger dimension for a square terrain
    num_patches = max(num_patches_x, num_patches_y)
    terrain_size = num_patches * patch_size

    # Minimum 8 (one patch); no upper cap — real OSM extents can exceed 512
    terrain_size = max(8, terrain_size)
    max_size = int(os.environ.get("ASCIICKER_OSM_MAX_TERRAIN_SIZE", "0") or "0")
    if max_size > 0 and terrain_size > max_size:
        raise RuntimeError(
            f"auto terrain size {terrain_size} exceeds safety ceiling {max_size}; "
            "use a smaller bbox or override ASCIICKER_OSM_MAX_TERRAIN_SIZE"
        )

    result = bpy.ops.asciicker.create_terrain(size=float(terrain_size), subdivisions=1)
    if 'FINISHED' not in result:
        return None
    terrain = context.active_object
    if terrain is None:
        return None

    # Reposition blosm objects onto the terrain (terrain stays at origin).
    # Terrain covers [0, terrain_size]. Shift blosm so extent lands within that range.
    shift_x = -(grid_min_x * patch_size)
    shift_y = -(grid_min_y * patch_size)
    context.scene['terrain_shift_x'] = shift_x
    context.scene['terrain_shift_y'] = shift_y
    for obj in context.scene.objects:
        if obj.type not in ('MESH', 'CURVE', 'EMPTY'):
            continue
        is_blosm = (obj.get('building') or obj.get('highway') or obj.get('landuse')
                     or obj.get('natural') or obj.get('amenity') or obj.get('landcover')
                     or obj.get('waterway') or obj.get('leisure'))
        is_blosm_parent = (obj.type == 'EMPTY' and obj.name.startswith('None_'))
        is_blosm_named = obj.name.startswith('None_') and obj.type in ('MESH', 'CURVE')
        if is_blosm or is_blosm_parent or is_blosm_named:
            obj.location.x += shift_x
            obj.location.y += shift_y

    return terrain


def _estimate_building_height(obj):
    """Estimate building height from OSM tags, 3D mesh extent, or footprint area.

    For pre-extruded 3D buildings (blosm 3Dsimple): measures the actual Z extent
    of the existing mesh BEFORE clean-extrude replaces it.
    For flat 2D footprints: uses building:levels tag or area-based heuristic.

    Returns (height, is_measured): is_measured=True means height came from the
    actual 3D mesh extent (should NOT be multiplied again by height_mult).
    """
    levels = obj.get('building:levels')
    if levels:
        try:
            return float(levels) * 3.0, False
        except (ValueError, TypeError):
            pass
    # For 3D buildings: measure existing Z extent
    coords = [obj.matrix_world @ v.co for v in obj.data.vertices]
    if coords:
        z_extent = max(c.z for c in coords) - min(c.z for c in coords)
        if z_extent > 0.5:
            return z_extent, True  # Measured — don't apply height_mult
    # Heuristic from footprint area
    mesh = obj.data
    area = sum(p.area for p in mesh.polygons)
    if area > 2000:
        return 15.0, False
    elif area > 500:
        return 12.0, False
    elif area > 100:
        return 9.0, False
    return 6.0, False


def _footprint_verts(obj):
    """Get ordered footprint vertices at the building's ground level.

    [RC-29 FIX] For pre-extruded 3D buildings (blosm 3Dsimple), this extracts
    the boundary polygon at min-Z, discarding all interior triangulation.
    For flat 2D footprints, the entire mesh IS the footprint.

    Algorithm:
      1. Find vertices at min-Z (ground level, within tolerance)
      2. Use bmesh to find BOUNDARY edges — edges with exactly 1 adjacent
         face among ground-level faces (skips interior triangulation edges)
      3. Chain boundary edges into an ordered polygon

    Returns list of (x, y) in world space, or None if extraction fails.
    """
    mesh = obj.data
    mat = obj.matrix_world
    if len(mesh.vertices) < 3:
        return None

    # Find ground-level vertices
    world_verts = [(mat @ v.co, v.index) for v in mesh.vertices]
    z_min = min(wv[0].z for wv in world_verts)
    z_tol = 0.1
    ground = {idx: (co.x, co.y) for co, idx in world_verts if co.z < z_min + z_tol}
    if len(ground) < 3:
        return None

    # [BUG-4/CR-5 FIX] Use bmesh to find BOUNDARY edges only — edges with
    # exactly 1 face among ground-level faces. This avoids interior
    # triangulation edges that would divert the chain walker.
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.edges.ensure_lookup_table()
    bm.verts.ensure_lookup_table()

    ground_set = set(ground.keys())
    boundary_edges = []
    for e in bm.edges:
        v0, v1 = e.verts[0].index, e.verts[1].index
        if v0 not in ground_set or v1 not in ground_set:
            continue
        # Count ground-level faces adjacent to this edge
        # == 1 means true boundary; == 0 is free edge (not boundary); >= 2 is interior
        ground_faces = 0
        for f in e.link_faces:
            if all(v.index in ground_set for v in f.verts):
                ground_faces += 1
        if ground_faces == 1:
            boundary_edges.append((v0, v1))
    bm.free()

    if not boundary_edges:
        # [BUG-3 FIX] Fallback: angular sort around centroid
        pts = list(ground.values())
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        pts.sort(key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
        return pts

    # Build adjacency from boundary edges
    adj = {}
    for a, b in boundary_edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)

    # Chain into ordered loop
    start = next(iter(adj))
    ordered = [start]
    visited = {start}
    current = start
    while True:
        found = False
        for nxt in adj.get(current, []):
            if nxt == start and len(ordered) > 2:
                return [ground[i] for i in ordered]
            if nxt not in visited:
                visited.add(nxt)
                ordered.append(nxt)
                current = nxt
                found = True
                break
        if not found:
            break
    return [ground[i] for i in ordered] if len(ordered) >= 3 else None


def _simplify_collinear(footprint):
    """Remove collinear points from a footprint polygon.

    [BUG-1/BUG-2 FIX] Uses simplified[-1] as predecessor (not footprint[i-1])
    and tests last-to-first wrap for collinearity.
    """
    if len(footprint) < 3:
        return footprint
    simplified = [footprint[0]]
    for i in range(1, len(footprint)):
        p = simplified[-1]
        c = footprint[i]
        n = footprint[(i + 1) % len(footprint)]
        cross = (c[0] - p[0]) * (n[1] - p[1]) - (c[1] - p[1]) * (n[0] - p[0])
        if abs(cross) > 0.1:
            simplified.append(c)
    # Check if first point is now collinear with its neighbors
    if len(simplified) >= 3:
        p, c, n = simplified[-1], simplified[0], simplified[1]
        cross = (c[0] - p[0]) * (n[1] - p[1]) - (c[1] - p[1]) * (n[0] - p[0])
        if abs(cross) <= 0.1:
            simplified.pop(0)
    return simplified


def _point_line_distance(p, a, b):
    ax, ay = a
    bx, by = b
    px, py = p
    dx = bx - ax
    dy = by - ay
    denom = dx * dx + dy * dy
    if denom <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _douglas_peucker(points, epsilon):
    if len(points) <= 2:
        return points
    start = points[0]
    end = points[-1]
    max_dist = -1.0
    split = -1
    for i, point in enumerate(points[1:-1], 1):
        dist = _point_line_distance(point, start, end)
        if dist > max_dist:
            max_dist = dist
            split = i
    if max_dist > epsilon:
        return _douglas_peucker(points[:split + 1], epsilon)[:-1] + _douglas_peucker(points[split:], epsilon)
    return [start, end]


def _simplify_osm_building_footprint(footprint):
    """Collapse noisy OSM import notches before building bake.

    ESS/FL-2534 exposed the failure mode: a satellite-clean rectilinear
    building can arrive from OSM/BLOSM as 30-40 tiny boundary points. Exact
    collinearity cleanup preserves those notches, then terrain bake turns the
    triangulated mesh into a jagged imprint. Use a bounded Douglas-Peucker pass
    before collinearity cleanup so the bake receives the intended footprint
    owner, not import noise.
    """
    if len(footprint) < 8:
        return _simplify_collinear(footprint)
    xs = [p[0] for p in footprint]
    ys = [p[1] for p in footprint]
    diag = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    epsilon = max(2.0, min(20.0, diag * 0.03))
    open_loop = list(footprint)
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    start = max(range(len(open_loop)), key=lambda i: math.hypot(open_loop[i][0] - cx, open_loop[i][1] - cy))
    rotated = open_loop[start:] + open_loop[:start] + [open_loop[start]]
    simplified = _douglas_peucker(rotated, epsilon)[:-1]
    return _simplify_collinear(simplified)


def _polygon_area(points):
    area = 0.0
    for i, p in enumerate(points):
        q = points[(i + 1) % len(points)]
        area += p[0] * q[1] - q[0] * p[1]
    return area * 0.5


def _point_in_tri_2d(p, a, b, c):
    px, py = p
    ax, ay = a
    bx, by = b
    cx, cy = c
    denom = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(denom) < 1e-9:
        return False
    u = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / denom
    v = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / denom
    w = 1.0 - u - v
    return u >= -1e-9 and v >= -1e-9 and w >= -1e-9


def _ear_clip_polygon(points):
    if len(points) < 3:
        return []
    ccw = _polygon_area(points) > 0.0
    remaining = list(range(len(points)))
    triangles = []
    guard = len(points) * len(points)
    while len(remaining) > 3 and guard > 0:
        guard -= 1
        clipped = False
        for pos, idx in enumerate(remaining):
            prev_idx = remaining[pos - 1]
            next_idx = remaining[(pos + 1) % len(remaining)]
            a = points[prev_idx]
            b = points[idx]
            c = points[next_idx]
            cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if (ccw and cross <= 1e-9) or ((not ccw) and cross >= -1e-9):
                continue
            if any(
                other not in {prev_idx, idx, next_idx}
                and _point_in_tri_2d(points[other], a, b, c)
                for other in remaining
            ):
                continue
            triangles.append((prev_idx, idx, next_idx) if ccw else (prev_idx, next_idx, idx))
            del remaining[pos]
            clipped = True
            break
        if not clipped:
            return []
    if len(remaining) == 3:
        a, b, c = remaining
        triangles.append((a, b, c) if ccw else (a, c, b))
    return triangles


def _ground_world_z(obj):
    """Return the minimum world-space Z among an object's vertices."""
    coords = [obj.matrix_world @ v.co for v in obj.data.vertices]
    if not coords:
        return 0.0
    return min(c.z for c in coords)


def _localize_world_footprint(obj, footprint, ground_z_world):
    """Convert world-space footprint XY points to object-local XYZ vertices."""
    inv_mat = obj.matrix_world.inverted()
    local = []
    for x, y in footprint:
        co = inv_mat @ Vector((x, y, ground_z_world))
        local.append((co.x, co.y, co.z))
    return local


# ---------------------------------------------------------------------------
# Fixture placement: OSM point features → AKM instances
# ---------------------------------------------------------------------------

# Maps (osm_tag_key, osm_tag_value) → fixture AKM stem (filename without .akm).
# AKMs are expected in assets/meshes/fixtures/<stem>.akm.
def _osm_project(lat, lon, scene_lat, scene_lon):
    """Transverse Mercator matching blosm's util/transverse_mercator.py."""
    R = 6378137.0  # WGS-84 semi-major axis
    lr, dr, l0 = math.radians(lat), math.radians(lon - scene_lon), math.radians(scene_lat)
    B = math.sin(dr) * math.cos(lr)
    if abs(1.0 - abs(B)) < 1e-12:
        return 0.0, 0.0
    return 0.5*R*math.log((1+B)/(1-B)), R*(math.atan(math.tan(lr)/math.cos(dr)) - l0)


_FIXTURE_BASE_SCALE = 24.0


def _scene_osm_content_scale(context):
    """Return the scene's OSM content scale, defaulting to 1 for addon-only use."""
    try:
        scale = float(context.scene.get('osm_content_scale', 1.0))
    except Exception:
        return 1.0
    return scale if scale > 0 else 1.0


def _scene_osm_xy(context, lat, lon, include_shift=False):
    """Project OSM lat/lon into the scene's scaled XY space."""
    scene_lat = float(context.scene.get('lat', 0.0))
    scene_lon = float(context.scene.get('lon', 0.0))
    x, y = _osm_project(lat, lon, scene_lat, scene_lon)
    scale = _scene_osm_content_scale(context)
    x *= scale
    y *= scale
    if include_shift:
        x += float(context.scene.get('terrain_shift_x', 0.0))
        y += float(context.scene.get('terrain_shift_y', 0.0))
    return x, y


def _scene_fixture_scale(context):
    """Fixture meshes are authored tiny; scale them into the same world contract as OSM."""
    try:
        explicit = float(context.scene.get('osm_fixture_scale', 0.0))
    except Exception:
        explicit = 0.0
    if explicit > 0:
        return explicit
    # PLANNED (FL-1180): User review says fixtures 0.8x too large.
    # Plan: multiply by _FIXTURE_SIZE_MULTIPLIER = 0.8 here.
    # NOT IMPLEMENTED as of 2026-04-30.
    return _FIXTURE_BASE_SCALE * _scene_osm_content_scale(context)


def _apply_fixture_instance_scale(context, obj):
    """Scale fixture instances in the transform matrix so runtime uses the canonical root AKMs."""
    scale = _scene_fixture_scale(context)
    if abs(scale - 1.0) <= 1e-6:
        return
    obj.scale = (scale, scale, scale)


def _get_osm_filepath(context):
    """Pipeline osm_filepath prop → blosm scene prop → blosm addon prefs → ''."""
    for getter in [
        lambda: context.scene.asciicker_osm_pipeline.osm_filepath,
        # blosm stores the filepath on the scene object, not addon preferences
        lambda: context.scene.blosm.osmFilepath,
        lambda: bpy.context.preferences.addons.get("blosm").preferences.osmFilepath,
    ]:
        try:
            fp = getter()
            if fp and os.path.isfile(fp):
                return fp
        except Exception:
            pass
    return ''


def _recover_terrain_shift_from_osm(context, osm_filepath):
    """Recover terrain_shift_x/y from building world pos vs OSM way centroid TM projection."""
    slat = float(context.scene.get('lat', 0.0))
    slon = float(context.scene.get('lon', 0.0))
    if not slat and not slon:
        return
    try:
        root = ET.parse(osm_filepath).getroot()
    except Exception:
        return
    nll = {n.get('id'): (float(n.get('lat')), float(n.get('lon')))
           for n in root.iter('node') if n.get('lat')}
    for bldg in _get_blosm_buildings(context):
        wid = str(bldg.get('id', ''))
        if not wid:
            continue
        way = root.find(f'.//way[@id="{wid}"]')
        if way is None:
            continue
        refs = [nd.get('ref') for nd in way.findall('nd')]
        coords = [nll[r] for r in refs if r in nll]
        if not coords:
            continue
        clat = sum(c[0] for c in coords) / len(coords)
        clon = sum(c[1] for c in coords) / len(coords)
        tx, ty = _scene_osm_xy(context, clat, clon, include_shift=False)
        context.scene['terrain_shift_x'] = bldg.location.x - tx
        context.scene['terrain_shift_y'] = bldg.location.y - ty
        return


def _buffer_pts(pts, half_w):
    """Buffer a 2D polyline into a closed polygon via left/right offsets."""
    left, right = [], []
    n = len(pts)
    for i in range(n):
        j, k = max(0, i-1), min(n-1, i+1)
        dx, dy = pts[k][0]-pts[j][0], pts[k][1]-pts[j][1]
        ln = math.sqrt(dx*dx + dy*dy)
        if ln < 1e-6:
            continue
        nx, ny = -dy/ln*half_w, dx/ln*half_w
        left.append((pts[i][0]+nx, pts[i][1]+ny))
        right.append((pts[i][0]-nx, pts[i][1]-ny))
    return (left + list(reversed(right))) if len(left) >= 2 else None


_OSM_LANDUSE_GRASS = frozenset({
    'grass', 'meadow', 'forest', 'orchard', 'village_green', 'recreation_ground',
    'educational', 'university', 'school', 'college',  # campus grounds default to grass
})
_OSM_NATURAL_GRASS = frozenset({
    'wood', 'scrub', 'grassland', 'meadow', 'fell', 'heath'})
_OSM_LANDUSE_DIRT = frozenset({
    'residential', 'allotments', 'farmland', 'farmyard'})
_OSM_LEISURE_GRASS = frozenset({
    'park', 'garden', 'pitch', 'golf_course', 'recreation_ground', 'common',
    'nature_reserve', 'playground',
})


def _paint_terrain_from_osm_ways(context, osm_filepath):
    """Paint terrain from OSM ways (3Dsimple mode: no road curves in scene). Returns painted count."""
    from io_asciicker.tools.osm_terrain_painter import (
        paint_terrain_vertex_colors, ROAD_HALF_WIDTHS)
    terrain = next(
        (o for o in context.scene.objects
         if o.type == 'MESH' and o.name.lower().startswith('terrain')), None)
    if terrain is None:
        return 0
    pp = context.scene.asciicker_osm_painter
    try:
        root = ET.parse(osm_filepath).getroot()
    except Exception:
        return 0
    nll = {n.get('id'): (float(n.get('lat')), float(n.get('lon')))
           for n in root.iter('node') if n.get('lat')}

    def _proj(la, lo):
        return _scene_osm_xy(context, la, lo, include_shift=True)

    polys = []
    for way in root.iter('way'):
        tags = {t.get('k'): t.get('v') for t in way.findall('tag')}
        pts = [_proj(*nll[r]) for nd in way.findall('nd')
               if (r := nd.get('ref')) in nll]
        if len(pts) < 2:
            continue
        hw = tags.get('highway', '')
        nat = tags.get('natural', '')
        lu = tags.get('landuse', '')
        leis = tags.get('leisure', '')
        ww = tags.get('waterway', '')
        if hw:
            # WHY max(..., 8): terrain vertex spacing is ~13 units; road
            # half-widths < 7 units only catch isolated vertices, producing
            # single-dot artifacts.  Clamping to 8 guarantees connectivity.
            hw_base = ROAD_HALF_WIDTHS.get(hw, 3.0)
            poly = _buffer_pts(pts, max(hw_base, 8.0))
            if poly:
                polys.append((poly, pp.road_mat))
        elif nat == 'water' or ww in ('river', 'stream', 'canal', 'drain'):
            if len(pts) >= 3:
                polys.append((pts, pp.water_mat))
            elif len(pts) >= 2:
                poly = _buffer_pts(pts, 5.0)
                if poly:
                    polys.append((poly, pp.water_mat))
        elif leis == 'swimming_pool':
            if len(pts) >= 3:
                polys.append((pts, pp.water_mat))
        elif nat in _OSM_NATURAL_GRASS or lu in _OSM_LANDUSE_GRASS or leis in _OSM_LEISURE_GRASS:
            if len(pts) >= 3:
                polys.append((pts, pp.grass_mat))
        elif lu in _OSM_LANDUSE_DIRT:
            if len(pts) >= 3:
                polys.append((pts, pp.residential_mat))

    # Add full-terrain background grass polygon — it will sort LAST (largest area)
    # so it only paints vertices not covered by any OSM feature (background fill).
    terrain_mesh = terrain.data
    mat_world = terrain.matrix_world
    xs = [(mat_world @ v.co).x for v in terrain_mesh.vertices]
    ys = [(mat_world @ v.co).y for v in terrain_mesh.vertices]
    if xs and ys:
        bg_poly = [
            (min(xs), min(ys)), (max(xs), min(ys)),
            (max(xs), max(ys)), (min(xs), max(ys)),
        ]
        polys.append((bg_poly, pp.grass_mat))

    if not polys:
        return 0
    polys.sort(key=lambda pw: (max(p[0] for p in pw[0])-min(p[0] for p in pw[0]))*(max(p[1] for p in pw[0])-min(p[1] for p in pw[0])))
    return paint_terrain_vertex_colors(terrain, polys)


def _place_point_fixtures_from_osm(context, osm_filepath, fixtures_dir):
    """Parse OSM file directly and place fixtures at node positions (blosm 3Dsimple path)."""
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    collection = context.scene.collection
    placed = skipped = 0; missing_stems = []; mesh_cache = {}
    try:
        root = ET.parse(osm_filepath).getroot()
    except Exception as e:
        print(f"[fixtures] OSM parse error: {e}")
        return 0, 0, []
    # [FL-1143 FIX] Pre-compute terrain bounds for off-map guard.
    # OSM nodes that project outside the import bbox get coordinates like
    # (-32,-32) after _scene_osm_xy, which become sentinel instances in A3D.
    # Terrain starts at (0,0); allow a small negative margin for floating-point
    # edge fixtures, but reject clearly off-map nodes like the -32,-32 sentinel.
    _OFFMAP_NEG_MARGIN = -5.0   # reject bx/by < -5 (sentinel is ~ -32)
    _OFFMAP_POS_MARGIN = 50.0   # allow up to terrain_size + 50 for edge cases
    _terrain_obj = _get_terrain_object(context)
    _terrain_size = max(_terrain_obj.dimensions[0], _terrain_obj.dimensions[1]) if _terrain_obj else float('inf')
    for node_elem in root.iter('node'):
        try:
            lat = float(node_elem.get('lat'))
            lon = float(node_elem.get('lon'))
        except (TypeError, ValueError):
            continue
        tags = {t.get('k'): t.get('v') for t in node_elem.iter('tag')}
        stem = next((s for (k, v), s in _FIXTURE_TAGS.items() if tags.get(k) == v), None)
        if stem is None:
            continue
        akm_path = os.path.join(fixtures_dir, stem + '.akm')
        if not os.path.isfile(akm_path):
            if stem not in missing_stems:
                missing_stems.append(stem)
            skipped += 1
            continue
        if stem not in mesh_cache:
            bpy.ops.object.select_all(action='DESELECT')
            try:
                bpy.ops.import_mesh.akm(filepath=akm_path)
            except Exception as e:
                print(f"[fixtures] AKM import error {stem}: {e}")
                skipped += 1
                continue
            tmpl = context.active_object
            if tmpl is None or tmpl.type != 'MESH':
                skipped += 1
                continue
            tmpl.data.name = stem
            tmpl.name = f'_fixture_tmpl_{stem}'
            mesh_cache[stem] = tmpl.data
        bx, by = _scene_osm_xy(context, lat, lon, include_shift=True)
        # [FL-1143 FIX] Skip fixtures that project off the terrain.
        if (bx < _OFFMAP_NEG_MARGIN or by < _OFFMAP_NEG_MARGIN
                or bx > _terrain_size + _OFFMAP_POS_MARGIN
                or by > _terrain_size + _OFFMAP_POS_MARGIN):
            print(f"[fixtures] skipping off-map {stem} at ({bx:.1f},{by:.1f}) [FL-1143]")
            skipped += 1
            continue
        obj = bpy.data.objects.new(stem, mesh_cache[stem])
        collection.objects.link(obj)
        # BUG (FL-1144): Z=0.0 hardcoded — exports as Z=120 (terrain base).
        # Fixtures sink below elevated terrain after topology/building bake.
        # Runner-side workaround: sbu_e2e_run.py:527 _filter_and_probe_fixtures()
        # probes terrain Z post-bake and patches fixture JSON before append.
        # 2 fix attempts (2026-04-22), both unproven in full pipeline.
        obj.location = (bx, by, 0.0)
        _apply_fixture_instance_scale(context, obj)
        direction = tags.get('direction')
        if direction is not None:
            try:
                obj.rotation_euler.z = math.radians(-float(direction))
            except (ValueError, TypeError):
                pass
        else:
            obj.rotation_euler.z = random.Random(int(bx*1000) ^ int(by*1000)).uniform(0, 2*math.pi)
        # [ROOT-28 FIX] Use flat name — engine can't match "fixtures/bench.akm"
        # from MeshScan's flat directory listing. Symlinks in assets/meshes/ handle the
        # actual file resolution (assets/meshes/bench.akm → assets/meshes/fixtures/bench.akm).
        obj['a3d_mesh_ref'] = stem
        placed += 1
    for sk in mesh_cache:
        t = bpy.data.objects.get(f'_fixture_tmpl_{sk}')
        if t:
            bpy.data.objects.remove(t, do_unlink=True)
    return placed, skipped, missing_stems


def _world_xy_bbox(obj):
    """Return world-space XY bbox center/size for a mesh object."""
    xs = []
    ys = []
    for corner in obj.bound_box:
        co = obj.matrix_world @ Vector(corner)
        xs.append(co.x)
        ys.append(co.y)
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    return {
        "center": ((min_x + max_x) * 0.5, (min_y + max_y) * 0.5),
        "size": (max_x - min_x, max_y - min_y),
        "bbox": (min_x, min_y, max_x, max_y),
    }


def _osm_tags(elem):
    return {t.get('k'): t.get('v') for t in elem.findall('tag')}


def _is_public_transport_shelter_target(tags):
    """Return True for OSM features that must not claim campus-building names."""
    if tags.get('amenity') == 'shelter':
        return True
    if tags.get('shelter_type') == 'public_transport':
        return True
    if tags.get('public_transport'):
        return True
    if tags.get('highway') == 'bus_stop':
        return True
    return False


def _project_named_building_target(context, nll, name, refs, source_kind, source_id, tags):
    coords = [nll[r] for r in refs if r in nll]
    if not coords:
        return None
    projected = [_scene_osm_xy(context, la, lo, include_shift=True) for la, lo in coords]
    if len(projected) >= 2 and projected[0] == projected[-1]:
        projected = projected[:-1]
    xs = [p[0] for p in projected]
    ys = [p[1] for p in projected]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    return {
        "name": name,
        "center": ((min_x + max_x) * 0.5, (min_y + max_y) * 0.5),
        "size": (max_x - min_x, max_y - min_y),
        "bbox": (min_x, min_y, max_x, max_y),
        "footprint": projected,
        "source": f"{source_kind}/{source_id}" if source_id else source_kind,
        "tags": tags,
    }


def _bbox_overlap_score(a, b):
    if not a or not b:
        return 0.0, 0.0
    amin_x, amin_y, amax_x, amax_y = a
    bmin_x, bmin_y, bmax_x, bmax_y = b
    overlap_w = max(0.0, min(amax_x, bmax_x) - max(amin_x, bmin_x))
    overlap_h = max(0.0, min(amax_y, bmax_y) - max(amin_y, bmin_y))
    overlap = overlap_w * overlap_h
    if overlap <= 0.0:
        return 0.0, 0.0
    area_a = max(0.0, amax_x - amin_x) * max(0.0, amax_y - amin_y)
    area_b = max(0.0, bmax_x - bmin_x) * max(0.0, bmax_y - bmin_y)
    union = area_a + area_b - overlap
    iou = overlap / union if union > 0 else 0.0
    min_area = min(area_a, area_b)
    coverage = overlap / min_area if min_area > 0 else 0.0
    return iou, coverage


def _remove_scene_object(context, obj):
    """Remove an object from Blender, with a list fallback for unit tests."""
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
        return True
    except Exception:
        pass
    try:
        context.scene.objects.remove(obj)
        return True
    except Exception:
        return False


def _mark_duplicate_building_for_prune(context, obj):
    try:
        obj[PRUNE_DUPLICATE_BUILDING_PROP] = 1
        return True
    except Exception:
        return _remove_scene_object(context, obj)


def _delete_marked_duplicate_buildings(context):
    pruned = 0
    for obj in list(context.scene.objects):
        try:
            marked = bool(obj.get(PRUNE_DUPLICATE_BUILDING_PROP))
        except Exception:
            marked = False
        if marked and _remove_scene_object(context, obj):
            pruned += 1
    return pruned


def _prune_duplicate_named_suffix_buildings(context):
    """Drop Blender suffix copies like Name_2 when they overlap the base mesh."""
    objects = [obj for obj in context.scene.objects if obj.type == 'MESH']
    by_name = {obj.name: obj for obj in objects}
    pruned = 0
    for obj in list(objects):
        match = re.match(r"(.+)(?:_([2-9]\d*)|\.(\d{3,}))$", obj.name)
        if not match:
            continue
        base = by_name.get(match.group(1))
        if base is None:
            continue
        try:
            base_info = _world_xy_bbox(base)
            obj_info = _world_xy_bbox(obj)
        except Exception:
            continue
        iou, coverage = _bbox_overlap_score(base_info.get("bbox"), obj_info.get("bbox"))
        base_size = base_info.get("size") or (0.0, 0.0)
        obj_size = obj_info.get("size") or (0.0, 0.0)
        max_size_delta = max(
            abs(base_size[0] - obj_size[0]),
            abs(base_size[1] - obj_size[1]),
        )
        max_size = max(base_size[0], base_size[1], obj_size[0], obj_size[1], 1.0)
        if (iou >= 0.95 or coverage >= 0.98) and max_size_delta <= max_size * 0.01:
            if _remove_scene_object(context, obj):
                pruned += 1
    return pruned


def _prune_duplicate_renamed_buildings(context, renamed_infos):
    """Drop exact overlapping duplicate named buildings from relation+way imports."""
    by_target = {}
    for info in renamed_infos:
        by_target.setdefault(info["target_name"], []).append(info)

    pruned = 0
    for target_name, infos in by_target.items():
        if len(infos) < 2:
            continue
        infos = sorted(
            infos,
            key=lambda info: (
                0 if info["obj"].name == target_name else 1,
                info.get("score", 0.0),
                info["obj"].name,
            ),
        )
        kept = []
        for info in infos:
            duplicate = False
            for prior in kept:
                iou, coverage = _bbox_overlap_score(prior.get("bbox"), info.get("bbox"))
                if iou >= 0.95 or coverage >= 0.98:
                    duplicate = True
                    break
            if duplicate and _mark_duplicate_building_for_prune(context, info["obj"]):
                pruned += 1
            else:
                kept.append(info)
    return pruned


def _named_osm_building_targets(context, osm_filepath, include_unnamed=False):
    """Project OSM building centroids into scaled/shifted scene space.

    Named targets drive labels.  Direct footprint bake also needs unnamed
    buildings so arbitrary OSM locations do not fall back to mesh-roof bake
    geometry for generic Building_### objects.
    """
    try:
        root = ET.parse(osm_filepath).getroot()
    except Exception:
        return []
    nll = {n.get('id'): (float(n.get('lat')), float(n.get('lon')))
           for n in root.iter('node') if n.get('lat')}
    ways = {way.get('id'): way for way in root.iter('way')}
    targets = []
    for way_id, way in ways.items():
        tags = _osm_tags(way)
        if 'building' not in tags:
            continue
        if _is_public_transport_shelter_target(tags):
            continue
        name = _sanitize_building_name(tags.get('name', ''))
        if not name and not include_unnamed:
            continue
        refs = [nd.get('ref') for nd in way.findall('nd')]
        target = _project_named_building_target(context, nll, name, refs, 'way', way_id, tags)
        if target:
            targets.append(target)

    for rel in root.iter('relation'):
        tags = _osm_tags(rel)
        if tags.get('type') != 'multipolygon' or 'building' not in tags:
            continue
        if _is_public_transport_shelter_target(tags):
            continue
        name = _sanitize_building_name(tags.get('name', ''))
        if not name and not include_unnamed:
            continue

        members = [m for m in rel.findall('member') if m.get('type') == 'way']
        outer_members = [m for m in members if (m.get('role') or '') == 'outer']
        footprint_members = outer_members or [m for m in members if (m.get('role') or '') != 'inner']
        refs = []
        for member in footprint_members:
            way = ways.get(member.get('ref'))
            if way is None:
                continue
            refs.extend(nd.get('ref') for nd in way.findall('nd'))
        target = _project_named_building_target(
            context, nll, name, refs, 'relation', rel.get('id'), tags,
        )
        if target:
            targets.append(target)
    return targets


def _rename_separated_buildings_from_osm(context, separated, osm_filepath=''):
    """Name separated 3Dsimple building parts by nearest named OSM building centroid."""
    if not separated:
        return 0
    osm_filepath = osm_filepath or _get_osm_filepath(context)
    # WARNING (FL-1175/FL-1179): If the run came from Overpass server import
    # and no local .osm file was materialized onto scene.blosm.osmFilepath,
    # this returns 0 immediately. The fallback Building_NNN names are later
    # filtered by extract_minimap_markers(), so the baked A3D ends up with
    # zero embedded building markers and therefore no minimap labels.
    if not osm_filepath:
        return 0
    targets = _named_osm_building_targets(context, osm_filepath, include_unnamed=True)
    if not targets:
        return 0

    objects = [{"obj": obj, **_world_xy_bbox(obj)} for obj in separated if obj.type == 'MESH']
    if not objects:
        return 0

    scene_names = {o.name for o in context.scene.objects if o not in separated}
    pairs = []
    for ti, target in enumerate(targets):
        tcx, tcy = target["center"]
        tsx, tsy = target["size"]
        tdiag = math.hypot(tsx, tsy)
        for oi, obj_info in enumerate(objects):
            ocx, ocy = obj_info["center"]
            osx, osy = obj_info["size"]
            diag = math.hypot(osx, osy)
            dist = math.hypot(ocx - tcx, ocy - tcy)
            threshold = max(24.0, min(128.0, 0.5 * max(diag, tdiag) + 20.0))
            iou, coverage = _bbox_overlap_score(target.get("bbox"), obj_info.get("bbox"))
            if dist > threshold and iou < 0.10 and coverage < 0.35:
                continue
            size_penalty = 0.15 * (abs(osx - tsx) + abs(osy - tsy))
            overlap_bonus = (iou * 512.0) + (coverage * 128.0)
            pairs.append((dist + size_penalty - overlap_bonus, dist, ti, oi))

    pairs.sort()
    used_targets = set()
    used_objects = set()
    renamed_infos = []
    renamed = 0
    for score, _, ti, oi in pairs:
        if ti in used_targets or oi in used_objects:
            continue
        target = targets[ti]
        obj = objects[oi]["obj"]
        if target["name"]:
            base_candidate = target["name"]
        elif obj.name.startswith("Building_"):
            base_candidate = _sanitize_building_name(obj.name) or f"Building_{renamed + 1:03d}"
        else:
            base_candidate = f"Building_{renamed + 1:03d}"
        candidate = base_candidate
        suffix = 2
        while candidate in scene_names:
            candidate = f"{base_candidate}_{suffix}"
            suffix += 1
        scene_names.add(candidate)
        obj.name = candidate
        obj.data.name = candidate
        # [FL-2595 FIX] Stamp OSM geometry ID for collision-free AKM naming.
        # Must also set a3d_mesh_ref so extract_instances() resolves the correct
        # mesh name for deferred building_specs.json (a3d_mesh_ref takes priority
        # over obj.name in extract_instances; obj.name is also set but may differ
        # from the AKM filename constructed here).
        osm_geom_id = target['source']
        obj['osm_geom_id'] = osm_geom_id
        if target.get('footprint'):
            obj['osm_footprint_xy_json'] = json.dumps(target['footprint'])
        akm_stem = osm_geom_id.replace('/', '_')
        obj['a3d_mesh_ref'] = akm_stem + '.akm'
        used_targets.add(ti)
        used_objects.add(oi)
        renamed_infos.append({
            **objects[oi],
            "target_name": target["name"],
            "assigned_name": candidate,
            "score": score,
        })
        renamed += 1
    if renamed_infos:
        renamed -= _prune_duplicate_renamed_buildings(context, renamed_infos)
    return renamed


_FIXTURE_TAGS = {
    ('highway', 'street_lamp'): 'street_lamp',
    ('amenity', 'bench'): 'bench',
    ('amenity', 'waste_basket'): 'trash_can',
    ('man_made', 'planter'): 'planter',
    ('natural', 'stone'): 'stone',
    ('leisure', 'picnic_table'): 'picnic_table',
}


def _get_fixture_stem(obj):
    """Return fixture stem (e.g. 'bench') for a blosm point-feature object, or None."""
    for (tag_key, tag_val), stem in _FIXTURE_TAGS.items():
        if obj.get(tag_key) == tag_val:
            return stem
    return None


def _place_point_fixtures(context, fixtures_dir):
    """Replace blosm point-feature objects with shared-mesh fixtures. Returns (placed,skipped,[missing])."""
    if not os.path.isdir(fixtures_dir):
        return 0, 0, []

    # Group point-feature objects by fixture stem
    by_stem = {}
    for obj in list(context.scene.objects):
        stem = _get_fixture_stem(obj)
        if stem is None:
            continue
        akm_path = os.path.join(fixtures_dir, stem + '.akm')
        if not os.path.isfile(akm_path):
            continue
        by_stem.setdefault(stem, []).append(obj)

    if not by_stem:
        osm_path = _get_osm_filepath(context)
        if osm_path:
            return _place_point_fixtures_from_osm(context, osm_path, fixtures_dir)
        return 0, 0, []

    placed = 0
    skipped = 0
    collection = context.scene.collection

    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    for stem, source_objs in by_stem.items():
        akm_path = os.path.join(fixtures_dir, stem + '.akm')

        # Import the AKM once; reuse mesh data for all copies.
        template_obj = None
        bpy.ops.object.select_all(action='DESELECT')
        try:
            bpy.ops.import_mesh.akm(filepath=akm_path)
        except Exception:
            skipped += len(source_objs)
            continue
        template_obj = context.active_object
        if template_obj is None or template_obj.type != 'MESH':
            skipped += len(source_objs)
            continue
        # Keep template_obj alive until after all copies are created so mesh_data
        # always has at least one user (avoids 0-user orphan).
        template_obj.data.name = stem
        template_obj.name = stem
        mesh_data = template_obj.data

        # Create one object per OSM node
        for src in source_objs:
            loc = src.matrix_world.translation.copy()
            direction = src.get('direction', None)

            new_obj = bpy.data.objects.new(stem, mesh_data)
            collection.objects.link(new_obj)
            new_obj.location = loc
            _apply_fixture_instance_scale(context, new_obj)

            # Rotation: use OSM direction tag (compass degrees, 0=N, CW) if present,
            # else a deterministic pseudo-random Y-rotation seeded by position.
            if direction is not None:
                try:
                    deg = float(direction)
                    new_obj.rotation_euler.z = math.radians(-deg)
                except (ValueError, TypeError):
                    pass
            else:
                rng = random.Random(int(loc.x * 1000) ^ int(loc.y * 1000))
                new_obj.rotation_euler.z = rng.uniform(0, 2 * math.pi)

            # [ROOT-28 FIX] Use flat name — engine can't match subdirectory paths
            # from MeshScan's flat directory listing. Symlinks handle file resolution.
            new_obj['a3d_mesh_ref'] = stem

            # Delete original blosm node
            bpy.data.objects.remove(src, do_unlink=True)
            placed += 1

        # Remove template after all copies have been created (mesh_data now has N users)
        if template_obj is not None:
            bpy.data.objects.remove(template_obj, do_unlink=True)

    return placed, skipped, []


def _export_fixture_instance_specs_from_osm(context, fixtures_dir, output_path):
    """Dump fixture A3D instance specs to JSON without leaving fixture objects in the scene."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    osm_path = _get_osm_filepath(context)
    if not osm_path or not os.path.isdir(fixtures_dir):
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump([], fh, indent=2, sort_keys=True)
        return 0, 0, []

    if context.scene.get('terrain_shift_x') is None:
        _recover_terrain_shift_from_osm(context, osm_path)

    before_ptrs = {obj.as_pointer() for obj in context.scene.objects}
    placed, skipped, missing = _place_point_fixtures_from_osm(context, osm_path, fixtures_dir)

    created = [
        obj for obj in context.scene.objects
        if obj.as_pointer() not in before_ptrs
        and obj.type == 'MESH'
        and obj.get('a3d_mesh_ref')
    ]

    from io_asciicker.scene.a3d_format import TERRAIN_EXPORT_BASELINE
    from io_asciicker.scene.export_a3d import extract_instances

    instances = extract_instances(created, z_baseline=TERRAIN_EXPORT_BASELINE)
    payload = []
    for inst in instances:
        payload.append({
            "variant": inst.variant,
            "mesh_name": inst.mesh_name,
            "inst_name": inst.inst_name,
            "transform": list(inst.transform),
            "flags": inst.flags,
            "story_id": inst.story_id,
        })

    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)

    for obj in created:
        bpy.data.objects.remove(obj, do_unlink=True)

    return placed, skipped, missing


# ---------------------------------------------------------------------------
# Operator: Flatten 3DSIMPLE buildings to 2D footprints (minimal faces)
# ---------------------------------------------------------------------------

class ASCIICKER_OT_osm_flatten_buildings(Operator):
    """Flatten 3DSIMPLE 3D buildings to 2D footprints, preserving names.

    For 3DSIMPLE imports, buildings arrive as full 3D meshes (20-200 faces).
    This step collapses them to flat footprints so the extrude step can
    re-extrude with minimal geometry (N+2 faces for N-edge footprint).
    Building names (from OSM tags) are preserved on the Blender objects.
    """
    bl_idname = "asciicker.osm_flatten_buildings"
    bl_label = "Flatten 3D Buildings to 2D"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        buildings = _get_blosm_buildings(context)
        if not buildings:
            self.report({'WARNING'}, "No blosm buildings found")
            return {'CANCELLED'}

        flattened = 0
        skipped = 0

        for obj in buildings:
            mesh = obj.data
            if len(mesh.vertices) < 3:
                skipped += 1
                continue

            # Only flatten buildings that ARE 3D (z extent > 0.5)
            coords = [obj.matrix_world @ v.co for v in mesh.vertices]
            z_min = min(c.z for c in coords)
            z_max = max(c.z for c in coords)
            if z_max - z_min <= 0.5:
                skipped += 1  # Already flat
                continue

            # Flatten: project all vertices to z_min, merge duplicates
            bm = bmesh.new()
            bm.from_mesh(mesh)

            # Set all vertex Z to the base height
            inv_mat = obj.matrix_world.inverted()
            base_z_local = (inv_mat @ Vector((0, 0, z_min))).z
            for v in bm.verts:
                v.co.z = base_z_local

            # Merge vertices that collapsed onto each other
            bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.01)

            # Remove degenerate faces (zero-area after flatten)
            degenerate = [f for f in bm.faces if f.calc_area() < 0.001]
            bmesh.ops.delete(bm, geom=degenerate, context='FACES')

            bm.to_mesh(mesh)
            bm.free()
            mesh.update()
            flattened += 1

        self.report({'INFO'},
                    f"Flattened {flattened} 3D buildings to 2D, "
                    f"skipped {skipped} (already flat)")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operator: Extrude all blosm buildings
# ---------------------------------------------------------------------------

class ASCIICKER_OT_osm_extrude_buildings(Operator):
    """Clean-extrude all blosm buildings: extract footprint → n-gon → extrude.

    [RC-29 FIX] Handles BOTH flat 2D footprints AND pre-extruded 3D buildings.
    For 3D buildings: measures height from existing mesh, extracts boundary
    polygon at ground level, replaces mesh with clean n-gon, extrudes.
    Produces minimal faces (N+2 for N-edge footprint) instead of the 200+
    triangulated faces from raw blosm geometry.
    """
    bl_idname = "asciicker.osm_extrude_buildings"
    bl_label = "Clean-Extrude Buildings"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        buildings = _get_blosm_buildings(context)
        if not buildings:
            self.report({'WARNING'}, "No blosm buildings found in scene")
            return {'CANCELLED'}

        props = context.scene.asciicker_osm_pipeline
        height_mult = props.building_height_mult

        extruded = 0
        skipped = 0
        _set_object_origins_to_bounds(context, buildings)

        for obj in buildings:
            # Measure height BEFORE replacing the mesh.
            # is_measured=True means height came from actual 3D extent — don't
            # apply height_mult again (it's already in real units).
            raw_height, is_measured = _estimate_building_height(obj)
            height = raw_height if is_measured else raw_height * height_mult

            # Extract footprint boundary polygon
            footprint = None
            source_footprint = obj.get('osm_footprint_xy_json', '')
            if source_footprint:
                try:
                    parsed = json.loads(source_footprint)
                    footprint = [(float(p[0]), float(p[1])) for p in parsed if len(p) >= 2]
                except Exception:
                    footprint = None
            if not footprint:
                footprint = _footprint_verts(obj)
            if not footprint or len(footprint) < 3:
                skipped += 1
                continue

            # Simplify noisy OSM import boundaries before extrusion/bake.
            simplified = _simplify_osm_building_footprint(footprint)
            if len(simplified) < 3:
                skipped += 1
                continue

            ground_z_world = _ground_world_z(obj)
            try:
                from io_asciicker.scene.a3d_format import HEIGHT_SCALE, TERRAIN_EXPORT_BASELINE
                bake_height = int(round(TERRAIN_EXPORT_BASELINE + (ground_z_world + height) * HEIGHT_SCALE))
                obj['osm_bake_footprint_xy_json'] = json.dumps(simplified)
                obj['osm_bake_height'] = max(TERRAIN_EXPORT_BASELINE + HEIGHT_SCALE, bake_height)
            except Exception:
                pass
            local_footprint = _localize_world_footprint(obj, simplified, ground_z_world)

            # FL-2534: concave buildings such as ESS must not rely on Blender's
            # implicit n-gon triangulation. The terrain bake stamps triangles,
            # so generate source-polygon roof triangles explicitly.
            n = len(local_footprint)
            tris = _ear_clip_polygon([(p[0], p[1]) for p in local_footprint])
            if not tris:
                skipped += 1
                continue
            verts = [(p[0], p[1], 0.0) for p in local_footprint]
            verts.extend((p[0], p[1], height) for p in local_footprint)
            faces = []
            for a, b, c in tris:
                faces.append((c, b, a))
                faces.append((a + n, b + n, c + n))
            for i in range(n):
                j = (i + 1) % n
                faces.append((i, j, j + n, i + n))
            obj.data.clear_geometry()
            obj.data.from_pydata(verts, [], faces)
            obj.data.update()
            extruded += 1

        self.report({'INFO'},
                    f"Clean-extruded {extruded} buildings, "
                    f"skipped {skipped} (height_mult={height_mult})")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operator: Batch subdivide + paint windows on all buildings
# ---------------------------------------------------------------------------

class ASCIICKER_OT_osm_paint_buildings(Operator):
    """Subdivide and paint windows on all blosm buildings"""
    bl_idname = "asciicker.osm_paint_buildings"
    bl_label = "Paint All Buildings"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        buildings = _get_blosm_buildings(context)
        if not buildings:
            self.report({'WARNING'}, "No blosm buildings found in scene")
            return {'CANCELLED'}

        bp_props = context.scene.asciicker_building_painter
        level = max(1, int(bp_props.subdivision_level))

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        old_active = context.view_layer.objects.active
        painted = 0

        for obj in buildings:
            # Subdivide -- must select + set active for modifier_apply
            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            context.view_layer.objects.active = obj

            modifier = obj.modifiers.new(name="OSM_Subdivide", type='SUBSURF')
            modifier.levels = level
            modifier.render_levels = level
            modifier.subdivision_type = 'SIMPLE'
            try:
                bpy.ops.object.modifier_apply(modifier=modifier.name)
            except Exception:
                continue

            # Paint
            vcol_layer = _ensure_color_layer(obj.data, name="Col")
            _paint_building(obj, vcol_layer, bp_props)
            painted += 1

        context.view_layer.objects.active = old_active
        self.report({'INFO'}, f"Subdivided and painted {painted} buildings")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operator: Prepare meshes -- inventory, reduce new, export AKMs
# ---------------------------------------------------------------------------

class ASCIICKER_OT_osm_prepare_meshes(Operator):
    """Check meshes against assets/meshes/ dir, reduce and export new ones as AKM"""
    bl_idname = "asciicker.osm_prepare_meshes"
    bl_label = "Prepare & Export Meshes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.asciicker_osm_pipeline
        meshes_dir = props.meshes_dir
        if not meshes_dir or not os.path.isdir(meshes_dir):
            self.report({'ERROR'}, f"Meshes directory not found: {meshes_dir}")
            return {'CANCELLED'}

        target_faces = props.target_faces
        if target_faces > 4950:
            self.report({'ERROR'}, f"Target faces {target_faces} exceeds 4950 limit")
            return {'CANCELLED'}

        # [ROOT-11 FIX] Export building AKMs to a dedicated output subdirectory.
        # NEVER check against existing game AKMs in assets/meshes/ — hand-made game
        # meshes (buildified.akm, Buildings_Export.akm, house-3.akm) must not
        # be confused with pipeline-generated buildings.
        output_subdir = os.path.join(meshes_dir, "osm_e2e_map_output_meshes")
        os.makedirs(output_subdir, exist_ok=True)

        # Get all mesh objects that would become instances
        mesh_objects = []
        for obj in context.scene.objects:
            if obj.type == 'MESH' and not _is_pipeline_utility_object(obj):
                mesh_objects.append(obj)

        if not mesh_objects:
            self.report({'WARNING'}, "No mesh objects to prepare")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        old_active = context.view_layer.objects.active
        exported = 0
        failed = 0

        for obj in mesh_objects:
            # [FL-2595 FIX] Use OSM geometry ID for AKM naming when available.
            # OSM building names ('Chemistry', 'Physics') collide with game ROOT
            # fixtures in assets/meshes/.  Use unique OSM way/relation IDs instead.
            akm_base = obj.get('osm_geom_id', '').strip() or ''
            if akm_base:
                # Replace path-unsafe chars (OSM source IDs use 'way/12345' format)
                akm_base = akm_base.replace('/', '_')
            else:
                # Fallback: use object name, stripped of Blender numeric suffix.
                akm_base = obj.name
                if '.' in akm_base:
                    parts = akm_base.rsplit('.', 1)
                    if parts[1].isdigit():
                        akm_base = parts[0]
            akm_filename = akm_base + '.akm'

            # Always export fresh — no collision check against existing AKMs.
            # Each pipeline run starts clean (GR-2 deletes output_subdir first).

            # Select + activate for modifier operations
            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            context.view_layer.objects.active = obj

            # Reduce if over target
            current_faces = len(obj.data.polygons)
            if current_faces > target_faces:
                ratio = target_faces / current_faces
                decimate = obj.modifiers.new(name="OSM_Decimate", type='DECIMATE')
                decimate.decimate_type = 'COLLAPSE'
                decimate.ratio = ratio
                try:
                    bpy.ops.object.modifier_apply(modifier=decimate.name)
                except Exception:
                    pass

            # Export to output subdirectory (not assets/meshes/ root)
            out_path = os.path.join(output_subdir, akm_filename)

            try:
                bpy.ops.export_mesh.akm(
                    filepath=out_path,
                    use_selection=True,
                    axis_forward='Y',
                    axis_up='Z',
                )
                # [FL-2595 FIX] Use OSM geometry ID as a3d_mesh_ref so the engine
                # never resolves to a game fixture AKM by name.  OSM IDs like
                # 'way/12345' are unique per building and won't collide with
                # hand-made game fixtures (buildified.akm, Buildings_Export.akm).
                obj['a3d_mesh_ref'] = akm_base
                exported += 1
            except Exception as e:
                print(f"Failed to export {akm_filename}: {e}")
                failed += 1

        context.view_layer.objects.active = old_active
        # [ENGINE-POINTER] Write .active_mesh_root pointer file so the C++ engine's
        # ReadActiveMeshRoot() can resolve these AKMs without ASCIICKER_ACTIVE_MESH_ROOT env var.
        # Engine reads from {base_path}assets/meshes/osm_runs/.active_mesh_root.
        _write_active_mesh_root_pointer(meshes_dir, output_subdir)

        msg = f"Exported {exported} AKMs to osm_e2e_map_output_meshes/"
        if failed > 0:
            msg += f", {failed} failed"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operator: Clean non-building objects
# ---------------------------------------------------------------------------

class ASCIICKER_OT_osm_clean_scene(Operator):
    """Delete non-building blosm objects (roads, vegetation, empties)"""
    bl_idname = "asciicker.osm_clean_scene"
    bl_label = "Clean Non-Buildings"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        to_delete = _get_nonbuilding_blosm(context)
        if not to_delete:
            self.report({'INFO'}, "No non-building blosm objects found")
            return {'FINISHED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        bpy.ops.object.select_all(action='DESELECT')
        for obj in to_delete:
            obj.select_set(True)
        bpy.ops.object.delete()

        self.report({'INFO'}, f"Deleted {len(to_delete)} non-building objects")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operator: Full pipeline
# ---------------------------------------------------------------------------

class ASCIICKER_OT_osm_full_pipeline(Operator):
    """Run complete OSM-to-engine pipeline: extrude, paint, prepare, clean, export"""
    bl_idname = "asciicker.osm_full_pipeline"
    bl_label = "Full OSM Pipeline"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.asciicker_osm_pipeline
        steps_done = []
        steps_failed = []

        # Step 1: Auto-create terrain if none exists (+ reposition blosm)
        if _get_terrain_object(context) is None:
            try:
                terrain = _auto_terrain_from_blosm(context)
                if terrain:
                    steps_done.append("auto_terrain")
                else:
                    steps_failed.append("auto_terrain")
            except Exception as e:
                print(f"auto_terrain failed: {e}")
                steps_failed.append("auto_terrain")

        # Ensure terrain_shift is stored (may be absent if scene was reloaded)
        if context.scene.get('terrain_shift_x') is None:
            osm_fp = _get_osm_filepath(context)
            if osm_fp:
                _recover_terrain_shift_from_osm(context, osm_fp)

        # Step 2: Paint terrain (BEFORE extrude — 2D footprints are ground truth)
        # In 2D mode: road curves in scene → paint_terrain_direct covers all features.
        # In 3DSIMPLE mode: no road curves → also paint roads/water from OSM file.
        result = bpy.ops.asciicker.paint_terrain_direct()
        if 'FINISHED' in result:
            steps_done.append("paint_terrain")
        else:
            steps_failed.append("paint_terrain")

        if props.blosm_import_mode == '3DSIMPLE':
            osm_fp = _get_osm_filepath(context)
            if osm_fp:
                n = _paint_terrain_from_osm_ways(context, osm_fp)
                steps_done.append(f"paint_osm_ways({n}v)")

        # Step 2.75: Separate grouped building meshes into individual objects.
        # MUST run BEFORE flatten/extrude/paint — blosm 3DSIMPLE groups 117
        # buildings into ~4 mesh objects. Without separation, extrude/paint
        # operates on grouped meshes (wrong height, face explosion on subdiv).
        # [RC-16 FIX] Moved from step 5b to here. Now separates ALL grouped
        # meshes, not just the first one.
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        grouped_buildings = [obj for obj in context.scene.objects
                             if obj.type == 'MESH'
                             and obj.name.lower().startswith('none_buildings')]
        total_separated = 0
        for buildings_obj in grouped_buildings:
            try:
                bpy.ops.object.select_all(action='DESELECT')
                buildings_obj.select_set(True)
                context.view_layer.objects.active = buildings_obj
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='SELECT')
                bpy.ops.mesh.separate(type='LOOSE')
                bpy.ops.object.mode_set(mode='OBJECT')
                total_separated += 1
            except Exception as e:
                if context.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')
                print(f"Separate failed for {buildings_obj.name}: {e}")

        # Rename all separated pieces to Building_NNN using two-pass rename
        # to avoid Blender's silent auto-suffix on name collision (e.g.,
        # Building_001 → Building_001.001 if Building_001 already exists).
        if total_separated > 0:
            separated = [o for o in context.scene.objects
                         if o.type == 'MESH'
                         and o.name.lower().startswith('none_buildings')]
            # Pass 1: assign temporary names to avoid collision
            for i, obj in enumerate(sorted(separated, key=lambda o: o.name), start=1):
                tmp_name = f"_bldg_tmp_{i:03d}"
                obj.name = tmp_name
                obj.data.name = tmp_name
                obj[PIPELINE_BUILDING_CANDIDATE_PROP] = 1
            # Pass 2: re-enumerate sorted temps → final Building_NNN names.
            # Use enumerate instead of parsing name[-3:] — Blender may have
            # appended .001 suffixes if temps collided with existing objects.
            tmp_objs = sorted(
                [o for o in context.scene.objects
                 if o.type == 'MESH' and o.name.startswith('_bldg_tmp_')],
                key=lambda o: o.name)
            osm_fp = _get_osm_filepath(context)
            renamed_from_osm = _rename_separated_buildings_from_osm(context, tmp_objs, osm_fp)
            unnamed = [o for o in tmp_objs if o.name.startswith('_bldg_tmp_')]
            for i, obj in enumerate(unnamed, start=1):
                final_name = f"Building_{i:03d}"
                obj.name = final_name
                obj.data.name = final_name
            pruned_duplicates = _delete_marked_duplicate_buildings(context)
            pruned_duplicates += _prune_duplicate_named_suffix_buildings(context)
            separated = [
                o for o in context.scene.objects
                if o.type == 'MESH' and o.get(PIPELINE_BUILDING_CANDIDATE_PROP)
            ]
            for obj in separated:
                obj.pop(PIPELINE_BUILDING_CANDIDATE_PROP, None)
            _set_pipeline_building_marker(separated)
            steps_done.append(f"separate({len(separated)})")
            if pruned_duplicates > 0:
                steps_done.append(f"prune_duplicate_buildings({pruned_duplicates})")
            if renamed_from_osm > 0:
                steps_done.append(f"rename_osm({renamed_from_osm})")
        else:
            # 3D mode buildings may already be individual objects
            steps_done.append("separate(already-split)")

        # Step 2.8: Rename blosm 3D buildings using OSM name tag.
        # In blosm 3D mode, buildings are already individual objects with
        # obj.get('name') set to the OSM name.  Names may contain '/' (OSM IDs
        # like "way/...") which would break AKM file paths.  Sanitize them here.
        # Blender object names must be unique — deduplicate with _2, _3 suffix.
        used_names = set()
        renamed_3d = 0
        for bldg in _get_blosm_buildings(context):
            if bldg.name.lower().startswith('building_'):
                continue  # Already renamed by separation step above
            osm_name = _sanitize_building_name(bldg.get('name', ''))
            # [FL-2595 FIX] Stamp OSM geometry ID for collision-free AKM naming
            osm_id = bldg.get('id', '')
            if osm_id:
                osm_geom_id = f"way/{osm_id}"
                bldg['osm_geom_id'] = osm_geom_id
                bldg['a3d_mesh_ref'] = osm_geom_id.replace('/', '_') + '.akm'
            if osm_name:
                # Deduplicate
                candidate = osm_name
                n = 2
                while candidate in used_names:
                    candidate = f"{osm_name}_{n}"
                    n += 1
                used_names.add(candidate)
                if bldg.name != candidate:
                    bldg.name = candidate
                    bldg.data.name = candidate
                    renamed_3d += 1
            else:
                used_names.add(bldg.name)
        if renamed_3d > 0:
            steps_done.append(f"rename_3d({renamed_3d})")
        pruned_3d_duplicates = _prune_duplicate_named_suffix_buildings(context)
        if pruned_3d_duplicates > 0:
            steps_done.append(f"prune_duplicate_3d({pruned_3d_duplicates})")
        _set_pipeline_building_marker(_get_blosm_buildings(context))

        # Step 3: (SKIPPED) Flatten step no longer needed.
        # [RC-29 FIX] Clean-extrude now handles both flat 2D and pre-extruded 3D
        # buildings directly — it extracts the footprint boundary, measures height
        # from the 3D mesh extent, then replaces with a clean n-gon + extrude.
        steps_done.append("flatten_3d(skipped-clean-extrude)")

        # Step 4: Clean-extrude buildings (handles both flat and 3D)
        result = bpy.ops.asciicker.osm_extrude_buildings()
        if 'FINISHED' in result:
            steps_done.append("extrude")
        else:
            steps_failed.append("extrude")

        # Step 5: Paint buildings (subdivide + windows, now individual objects)
        result = bpy.ops.asciicker.osm_paint_buildings()
        if 'FINISHED' in result:
            steps_done.append("paint_buildings")
        else:
            steps_failed.append("paint_buildings")

        # Step 6: Prepare meshes (inventory, reduce, export AKMs)
        result = bpy.ops.asciicker.osm_prepare_meshes()
        if 'FINISHED' in result:
            steps_done.append("meshes")
        else:
            steps_failed.append("meshes")

        # Step 7: Clean non-building objects
        result = bpy.ops.asciicker.osm_clean_scene()
        if 'FINISHED' in result:
            steps_done.append("clean")
        else:
            steps_failed.append("clean")

        # Step 7.5: Place fixtures last so the heavy building bake/export path
        # can run first. In 3DSIMPLE mode this falls back to the OSM file after
        # the scene cleanup has removed blosm helper objects.
        fixtures_dir = os.path.join(props.meshes_dir, "fixtures") if props.meshes_dir else ""
        placed, skipped, _ = _place_point_fixtures(context, fixtures_dir)
        if placed > 0 or skipped == 0:
            steps_done.append(f"fixtures({placed})")
        else:
            steps_failed.append(f"fixtures(0/{placed+skipped} placed)")

        # Step 8: Export A3D
        a3d_path = props.a3d_output
        if a3d_path:
            a3d_dir = os.path.dirname(a3d_path)
            if a3d_dir:
                os.makedirs(a3d_dir, exist_ok=True)
            try:
                bpy.ops.export_scene.a3d(filepath=a3d_path)
                steps_done.append("a3d")
            except Exception as e:
                print(f"A3D export failed: {e}")
                steps_failed.append("a3d")

        msg = f"Pipeline done: {', '.join(steps_done)}"
        if steps_failed:
            msg += f" | failed: {', '.join(steps_failed)}"
            self.report({'WARNING'}, msg)
        else:
            self.report({'INFO'}, msg)

        if not steps_done:
            return {'CANCELLED'}
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

_DEFAULT_MESHES_DIR = _find_meshes_dir()


class ASCIICKER_OSMPipelineProperties(PropertyGroup):
    meshes_dir: StringProperty(
        name="Meshes Dir",
        description="Path to engine assets/meshes/ directory for AKM files",
        default=_DEFAULT_MESHES_DIR,
        subtype='DIR_PATH',
    )
    target_faces: IntProperty(
        name="Target Faces",
        description="Decimate new meshes to this face count",
        default=500,
        min=50,
        max=4950,
    )
    a3d_output: StringProperty(
        name="A3D Output",
        description="Output path for A3D map file",
        default="",
        subtype='FILE_PATH',
    )
    osm_filepath: StringProperty(
        name="OSM File",
        description="Source .osm file for fixture placement and OSM-direct terrain painting. Auto-detected from blosm prefs if blank.",
        default="", subtype='FILE_PATH',
    )
    blosm_import_mode: EnumProperty(
        name="Blosm Mode", description="Import mode used before running pipeline",
        items=[('2D', '2D', 'Flat footprints + road/water curves'),
               ('3DSIMPLE', '3D Simple', 'Pre-extruded buildings, roads from OSM file')],
        default='3DSIMPLE',
    )
    building_height_mult: bpy.props.FloatProperty(
        name="Building Height Mult",
        description="Multiplier for building extrude height (compensates for content_scale)",
        default=5.0,
        min=1.0,
        max=20.0,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    ASCIICKER_OSMPipelineProperties,
    ASCIICKER_OT_osm_flatten_buildings,
    ASCIICKER_OT_osm_extrude_buildings,
    ASCIICKER_OT_osm_paint_buildings,
    ASCIICKER_OT_osm_prepare_meshes,
    ASCIICKER_OT_osm_clean_scene,
    ASCIICKER_OT_osm_full_pipeline,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.asciicker_osm_pipeline = bpy.props.PointerProperty(
        type=ASCIICKER_OSMPipelineProperties,
    )


def unregister():
    del bpy.types.Scene.asciicker_osm_pipeline
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
