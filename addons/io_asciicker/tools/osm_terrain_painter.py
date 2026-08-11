# OSM Terrain Painter -- paint asciiid terrain from blosm OSM data
# Extracts polygon boundaries from blosm objects, converts coordinates,
# and sends PAINT_TERRAIN_POLY MCP commands via file relay.
# [DEPENDENCY:BLENDER]

import math
import os
import statistics

import bpy
from bpy.props import FloatProperty, IntProperty, StringProperty
from bpy.types import Operator, PropertyGroup


# ---------------------------------------------------------------------------
# OSM tag -> mat_id categorization
# ---------------------------------------------------------------------------

GRASS_LANDUSE = {
    'grass', 'meadow', 'recreation_ground', 'village_green',
    'farmland', 'orchard', 'vineyard', 'forest',
}
GRASS_NATURAL = {'grassland', 'scrub', 'wood', 'heath'}
GRASS_LEISURE = {'park', 'garden', 'golf_course'}
DIRT_LEISURE = {'pitch', 'track', 'sports_centre'}
CONCRETE_LANDUSE = {'commercial', 'retail', 'industrial', 'construction', 'quarry'}
WATER_LANDUSE = {'basin', 'reservoir'}


def categorize_object(obj, props):
    """Return (mat_id, category_name) for a blosm object, or None to skip."""
    # --- Custom property detection (blosm 3D modes) ---
    if obj.get('building'):
        return (props.concrete_mat, 'concrete')

    highway = obj.get('highway')
    if highway:
        return (props.road_mat, 'road')

    landuse = obj.get('landuse', '')
    if landuse in GRASS_LANDUSE:
        return (props.grass_mat, 'grass')
    if landuse in CONCRETE_LANDUSE:
        return (props.concrete_mat, 'concrete')
    if landuse == 'residential':
        return (props.residential_mat, 'residential')
    if landuse in WATER_LANDUSE:
        return (props.water_mat, 'water')

    natural = obj.get('natural', '')
    if natural in GRASS_NATURAL:
        return (props.grass_mat, 'grass')
    if natural == 'water':
        return (props.water_mat, 'water')
    if natural == 'sand':
        return (props.sand_mat, 'sand')
    if natural in {'bare_rock', 'stone', 'scree'}:
        return (props.concrete_mat, 'concrete')

    amenity = obj.get('amenity', '')
    if amenity == 'parking':
        return (props.concrete_mat, 'concrete')

    waterway = obj.get('waterway', '')
    if waterway:
        return (props.water_mat, 'water')

    leisure = obj.get('leisure', '')
    if leisure in GRASS_LEISURE:
        return (props.grass_mat, 'grass')
    if leisure in DIRT_LEISURE:
        return (props.residential_mat, 'dirt')
    if leisure == 'swimming_pool':
        return (props.water_mat, 'water')

    if landuse or obj.get('landcover') or natural or waterway or leisure:
        return (props.default_mat, 'default')

    # --- Name-based fallback (blosm 2D mode: None_<category>) ---
    name = obj.name.lower()
    if name.startswith('none_buildings'):
        return (props.concrete_mat, 'concrete')
    if name.startswith('none_roads') or name.startswith('none_paths'):
        return (props.road_mat, 'road')
    if name.startswith('none_vegetation'):
        return (props.grass_mat, 'grass')
    if name.startswith('none_water'):
        return (props.water_mat, 'water')
    if name.startswith('none_areas'):
        return (props.concrete_mat, 'concrete')

    return None  # Not an OSM terrain object


# ---------------------------------------------------------------------------
# 2D polygon extraction
# ---------------------------------------------------------------------------

def extract_boundary_2d(obj):
    """Extract ordered 2D boundary vertices from a MESH object. Returns [(x,y),...] or None."""
    mesh = obj.data
    if not mesh.polygons and not mesh.vertices:
        return None

    mat = obj.matrix_world

    # Single-face mesh: use polygon vertex loop directly
    if len(mesh.polygons) == 1:
        poly = mesh.polygons[0]
        verts = []
        for vi in poly.vertices:
            co = mat @ mesh.vertices[vi].co
            verts.append((co.x, co.y))
        return verts

    # Multi-face (triangulated): find boundary edges and chain them
    if mesh.edges:
        edge_face_count = {}
        for poly in mesh.polygons:
            for ek in poly.edge_keys:
                edge_face_count[ek] = edge_face_count.get(ek, 0) + 1

        boundary_edges = [e for e, c in edge_face_count.items() if c == 1]
        if boundary_edges:
            chain = _chain_edges(boundary_edges)
            if chain:
                verts = []
                for vi in chain:
                    co = mat @ mesh.vertices[vi].co
                    verts.append((co.x, co.y))
                return verts

    # Fallback: convex hull of all vertices
    from mathutils import Vector as Vec2
    from mathutils.geometry import convex_hull_2d
    pts_2d = []
    pts_tuples = []
    for v in mesh.vertices:
        co = mat @ v.co
        pts_2d.append(Vec2((co.x, co.y)))
        pts_tuples.append((co.x, co.y))
    if len(pts_2d) < 3:
        return None
    hull_indices = convex_hull_2d(pts_2d)
    return [pts_tuples[i] for i in hull_indices]


def _chain_edges(edges):
    """Chain boundary edge tuples into an ordered vertex loop."""
    if not edges:
        return None
    adjacency = {}
    for a, b in edges:
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)

    start = edges[0][0]
    chain = [start]
    visited = {start}
    current = start
    while True:
        neighbors = adjacency.get(current, [])
        next_v = None
        for n in neighbors:
            if n not in visited:
                next_v = n
                break
        if next_v is None:
            break
        chain.append(next_v)
        visited.add(next_v)
        current = next_v
    return chain if len(chain) >= 3 else None


def _buffer_centerline(centerline, half_width, cyclic=False):
    """Buffer a centerline into a closed polygon. Returns [(x,y),...] or None."""
    if len(centerline) < 2:
        return None

    left_side = []
    right_side = []
    n = len(centerline)
    for i in range(n):
        # Direction vector at this point (wrap for cyclic)
        if cyclic:
            dx = centerline[(i + 1) % n][0] - centerline[(i - 1) % n][0]
            dy = centerline[(i + 1) % n][1] - centerline[(i - 1) % n][1]
        elif i == 0:
            dx = centerline[1][0] - centerline[0][0]
            dy = centerline[1][1] - centerline[0][1]
        elif i == n - 1:
            dx = centerline[-1][0] - centerline[-2][0]
            dy = centerline[-1][1] - centerline[-2][1]
        else:
            dx = centerline[i + 1][0] - centerline[i - 1][0]
            dy = centerline[i + 1][1] - centerline[i - 1][1]

        length = math.sqrt(dx * dx + dy * dy)
        if length < 1e-6:
            continue
        nx = -dy / length * half_width
        ny = dx / length * half_width

        cx, cy = centerline[i]
        left_side.append((cx + nx, cy + ny))
        right_side.append((cx - nx, cy - ny))

    if len(left_side) < 2:
        return None

    # Closed polygon: left forward, right backward
    return left_side + list(reversed(right_side))


def extract_road_polygon(obj, half_width=3.0):
    """Extract buffered polygons from ALL splines in a CURVE road object.

    Returns a list of [(x,y),...] polygons (one per spline), or None if empty.
    blosm packs many road segments into one curve object, so we must iterate
    all splines — not just splines[0].
    """
    if not obj.data.splines:
        return None

    mat = obj.matrix_world
    # Collect polygons from all splines
    all_polygons = []
    for spline in obj.data.splines:
        centerline = []
        if spline.type == 'BEZIER':
            for bp in spline.bezier_points:
                co = mat @ bp.co
                centerline.append((co.x, co.y))
        else:
            for pt in spline.points:
                co = mat @ pt.co.to_3d()
                centerline.append((co.x, co.y))

        poly = _buffer_centerline(centerline, half_width, cyclic=spline.use_cyclic_u)
        if poly:
            all_polygons.append(poly)

    return all_polygons if all_polygons else None


# Half-widths in blosm units (~1.67 units/meter). Doubled from original
# values to compensate for blosm's coordinate scale and produce visible
# road paint on the terrain grid.
ROAD_HALF_WIDTHS = {
    'motorway': 16.0, 'trunk': 14.0,
    'primary': 12.0, 'secondary': 10.0, 'tertiary': 8.0,
    'residential': 6.0, 'service': 5.0, 'unclassified': 6.0,
    'footway': 3.0, 'path': 2.0, 'cycleway': 3.0,
    'pedestrian': 4.0, 'track': 4.0,
}


# ---------------------------------------------------------------------------
# Douglas-Peucker simplification
# ---------------------------------------------------------------------------

def _point_line_distance(px, py, ax, ay, bx, by):
    """Perpendicular distance from point (px,py) to line segment (ax,ay)-(bx,by)."""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.sqrt((px - ax) ** 2 + (py - ay) ** 2)
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    proj_x = ax + t * dx
    proj_y = ay + t * dy
    return math.sqrt((px - proj_x) ** 2 + (py - proj_y) ** 2)


def douglas_peucker(points, epsilon):
    """Ramer-Douglas-Peucker polyline simplification."""
    if len(points) <= 2:
        return points

    # Find point with max distance from line between first and last
    ax, ay = points[0]
    bx, by = points[-1]
    max_dist = 0
    max_idx = 0
    for i in range(1, len(points) - 1):
        d = _point_line_distance(points[i][0], points[i][1], ax, ay, bx, by)
        if d > max_dist:
            max_dist = d
            max_idx = i

    if max_dist > epsilon:
        left = douglas_peucker(points[:max_idx + 1], epsilon)
        right = douglas_peucker(points[max_idx:], epsilon)
        return left[:-1] + right
    else:
        return [points[0], points[-1]]


# ---------------------------------------------------------------------------
# Ear-clipping convex decomposition
# ---------------------------------------------------------------------------

def _cross_2d(ox, oy, ax, ay, bx, by):
    """2D cross product of vectors (O->A) x (O->B)."""
    return (ax - ox) * (by - oy) - (ay - oy) * (bx - ox)


def _is_convex_polygon(poly):
    """Check if a polygon (list of (x,y)) is convex."""
    n = len(poly)
    if n < 3:
        return True
    sign = None
    for i in range(n):
        o = poly[i]
        a = poly[(i + 1) % n]
        b = poly[(i + 2) % n]
        cross = _cross_2d(o[0], o[1], a[0], a[1], b[0], b[1])
        if abs(cross) < 1e-10:
            continue
        s = cross > 0
        if sign is None:
            sign = s
        elif s != sign:
            return False
    return True


def _point_in_triangle(px, py, ax, ay, bx, by, cx, cy):
    """Check if point is inside triangle using barycentric coordinates."""
    d1 = _cross_2d(ax, ay, bx, by, px, py)
    d2 = _cross_2d(bx, by, cx, cy, px, py)
    d3 = _cross_2d(cx, cy, ax, ay, px, py)
    has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (has_neg and has_pos)


def ear_clip_triangulate(polygon):
    """Ear-clipping triangulation. Returns list of [(v0,v1,v2), ...] triangles."""
    if len(polygon) < 3:
        return []
    if len(polygon) == 3:
        return [list(polygon)]

    # Work with indices into a mutable list
    verts = list(polygon)
    indices = list(range(len(verts)))
    triangles = []

    max_iterations = len(indices) * 3  # safety limit
    iteration = 0
    while len(indices) > 3 and iteration < max_iterations:
        iteration += 1
        ear_found = False
        n = len(indices)
        for i in range(n):
            i0 = indices[(i - 1) % n]
            i1 = indices[i]
            i2 = indices[(i + 1) % n]
            ax, ay = verts[i0]
            bx, by = verts[i1]
            cx, cy = verts[i2]

            # Check if this is a convex vertex (ear candidate)
            cross = _cross_2d(ax, ay, bx, by, cx, cy)
            if cross <= 0:
                continue  # reflex vertex, skip

            # Check no other vertex is inside this triangle
            is_ear = True
            for j in range(n):
                idx = indices[j]
                if idx in (i0, i1, i2):
                    continue
                if _point_in_triangle(verts[idx][0], verts[idx][1],
                                      ax, ay, bx, by, cx, cy):
                    is_ear = False
                    break

            if is_ear:
                triangles.append([verts[i0], verts[i1], verts[i2]])
                indices.pop(i)
                ear_found = True
                break

        if not ear_found:
            # Degenerate polygon or wrong winding — try reversing
            if iteration == 1:
                indices = list(range(len(verts)))
                indices.reverse()
            else:
                break

    # Last triangle
    if len(indices) == 3:
        triangles.append([verts[indices[0]], verts[indices[1]], verts[indices[2]]])

    return triangles


def decompose_to_convex(polygon):
    """Decompose a polygon into convex sub-polygons via ear-clipping."""
    if len(polygon) < 3:
        return []
    if _is_convex_polygon(polygon):
        return [polygon]
    triangles = ear_clip_triangulate(polygon)
    return triangles if triangles else [polygon]


# ---------------------------------------------------------------------------
# Coordinate transform
# ---------------------------------------------------------------------------

def transform_polygon(polygon, scale, offset_x, offset_y):
    """Transform blosm vertices to engine coords: engine = blosm * scale + offset - 32."""
    result = []
    for bx, by in polygon:
        ex = bx * scale + offset_x - 32.0
        ey = by * scale + offset_y - 32.0
        result.append((ex, ey))
    return result


# ---------------------------------------------------------------------------
# Command generation
# ---------------------------------------------------------------------------

def polygon_to_commands(polygon, mat_id, scale, offset_x, offset_y, tolerance):
    """Convert a polygon to PAINT_TERRAIN_POLY commands. Returns (commands, stats)."""
    commands = []
    stats = {'pieces': 0, 'bbox_fallbacks': 0}

    # Simplify if needed
    simplified = polygon
    if len(polygon) > 32:
        simplified = douglas_peucker(polygon, tolerance)

    # Bbox fallback if still too complex
    if len(simplified) > 32:
        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        simplified = [
            (min(xs), min(ys)), (max(xs), min(ys)),
            (max(xs), max(ys)), (min(xs), max(ys)),
        ]
        stats['bbox_fallbacks'] = 1

    # Transform to engine coordinates
    transformed = transform_polygon(simplified, scale, offset_x, offset_y)

    # Decompose into convex pieces
    pieces = decompose_to_convex(transformed)

    for piece in pieces:
        if len(piece) < 3 or len(piece) > 32:
            continue
        n = len(piece)
        coords = ' '.join(f'{x:.1f} {y:.1f}' for x, y in piece)
        commands.append(f'PAINT_TERRAIN_POLY {mat_id} {n} {coords}')
        stats['pieces'] += 1

    return commands, stats


# ---------------------------------------------------------------------------
# Auto-calibration: match A3D buildings with blosm buildings
# ---------------------------------------------------------------------------

def _centroid_2d(obj):
    """Return (cx, cy) world-space centroid of a mesh object's bounding box."""
    from mathutils import Vector
    corners = [obj.matrix_world @ Vector(obj.bound_box[i]) for i in range(8)]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)


def _footprint_area(obj):
    """Approximate XY footprint area from bounding box."""
    from mathutils import Vector
    corners = [obj.matrix_world @ Vector(obj.bound_box[i]) for i in range(8)]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


def auto_calibrate(context, min_matches=5, area_ratio_max=3.0):
    """Compute scale/offset from A3D↔blosm building matches. Returns (scale,ox,oy,n,res) or None."""
    a3d_buildings = []
    blosm_buildings = []

    for obj in context.scene.objects:
        if obj.type != 'MESH':
            continue
        if obj.get('a3d_instance') and obj.get('building'):
            cx, cy = _centroid_2d(obj)
            a3d_buildings.append((obj, cx, cy, _footprint_area(obj)))
        elif obj.get('building') and not obj.get('a3d_instance'):
            cx, cy = _centroid_2d(obj)
            blosm_buildings.append((obj, cx, cy, _footprint_area(obj)))

    if len(a3d_buildings) < min_matches or len(blosm_buildings) < min_matches:
        return None

    # Match each A3D building to nearest blosm building by centroid distance
    matched = []  # list of (a3d_cx, a3d_cy, blosm_cx, blosm_cy)
    used_blosm = set()
    for a_obj, acx, acy, a_area in a3d_buildings:
        best_dist = float('inf')
        best_idx = -1
        for i, (b_obj, bcx, bcy, b_area) in enumerate(blosm_buildings):
            if i in used_blosm:
                continue
            # Area ratio filter: reject if areas differ too much
            if a_area > 0 and b_area > 0:
                ratio = max(a_area, b_area) / min(a_area, b_area)
                if ratio > area_ratio_max:
                    continue
            dist = math.sqrt((acx - bcx) ** 2 + (acy - bcy) ** 2)
            if dist < best_dist:
                best_dist = dist
                best_idx = i
        if best_idx >= 0:
            _, bcx, bcy, _ = blosm_buildings[best_idx]
            matched.append((acx, acy, bcx, bcy))
            used_blosm.add(best_idx)

    if len(matched) < min_matches:
        return None

    # Compute scale from inter-pair distances
    scale_samples = []
    for i in range(len(matched)):
        for j in range(i + 1, len(matched)):
            a3d_dist = math.sqrt(
                (matched[i][0] - matched[j][0]) ** 2 +
                (matched[i][1] - matched[j][1]) ** 2)
            blosm_dist = math.sqrt(
                (matched[i][2] - matched[j][2]) ** 2 +
                (matched[i][3] - matched[j][3]) ** 2)
            if blosm_dist > 1e-3:
                scale_samples.append(a3d_dist / blosm_dist)

    if not scale_samples:
        return None

    scale = statistics.median(scale_samples)

    # Compute offset = mean(a3d_pos - blosm_pos * scale)
    offsets_x = [a[0] - a[2] * scale for a in matched]
    offsets_y = [a[1] - a[3] * scale for a in matched]
    offset_x = statistics.mean(offsets_x)
    offset_y = statistics.mean(offsets_y)

    # Residual: how well do matched pairs align after transform?
    residuals = []
    for acx, acy, bcx, bcy in matched:
        pred_x = bcx * scale + offset_x
        pred_y = bcy * scale + offset_y
        residuals.append(math.sqrt((acx - pred_x) ** 2 + (acy - pred_y) ** 2))

    residual_std = statistics.stdev(residuals) if len(residuals) > 1 else 0.0

    return (scale, offset_x, offset_y, len(matched), residual_std)


class ASCIICKER_OT_auto_calibrate(Operator):
    """Auto-calibrate blosm-to-engine transform by matching building positions."""
    bl_idname = "asciicker.auto_calibrate_osm"
    bl_label = "Auto Calibrate"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = context.scene.asciicker_osm_painter
        result = auto_calibrate(context)
        if result is None:
            self.report({'WARNING'},
                        "Calibration failed: need >= 5 matched A3D + blosm buildings. "
                        "Import an .a3d file first, then ensure blosm buildings are in scene.")
            return {'CANCELLED'}

        scale, off_x, off_y, n_matches, residual_std = result
        props.scale = scale
        props.offset_x = off_x
        props.offset_y = off_y

        msg = (f"Calibrated from {n_matches} building pairs: "
               f"scale={scale:.4f}, offset=({off_x:.2f}, {off_y:.2f}), "
               f"residual_std={residual_std:.2f}")
        if residual_std > 5.0:
            self.report({'WARNING'}, msg + " — high residual, verify manually!")
        else:
            self.report({'INFO'}, msg)

        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Scene scanning
# ---------------------------------------------------------------------------

def scan_blosm_objects(context, props):
    """Scan scene for blosm objects. Returns {cat_name: [(obj, mat_id), ...]}."""
    categories = {}
    for obj in context.scene.objects:
        if obj.get('a3d_instance'):
            continue  # Skip A3D-imported objects
        if obj.type not in ('MESH', 'CURVE'):
            continue

        result = categorize_object(obj, props)
        if result is None:
            continue
        mat_id, cat_name = result
        categories.setdefault(cat_name, []).append((obj, mat_id))

    return categories


# ---------------------------------------------------------------------------
# Main operator
# ---------------------------------------------------------------------------

class ASCIICKER_OT_scan_osm_scene(Operator):
    """Scan the scene for blosm OSM objects and count by category."""
    bl_idname = "asciicker.scan_osm_scene"
    bl_label = "Scan Scene"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = context.scene.asciicker_osm_painter
        categories = scan_blosm_objects(context, props)

        total = sum(len(v) for v in categories.values())
        props.scan_total = total
        props.scan_grass = len(categories.get('grass', []))
        props.scan_road = len(categories.get('road', []))
        props.scan_concrete = len(categories.get('concrete', []))
        props.scan_residential = len(categories.get('residential', []))
        props.scan_default = len(categories.get('default', []))

        self.report({'INFO'}, f"Found {total} blosm objects "
                    f"({props.scan_grass} grass, {props.scan_road} roads, "
                    f"{props.scan_concrete} concrete, "
                    f"{props.scan_residential} residential, "
                    f"{props.scan_default} default)")
        return {'FINISHED'}


class ASCIICKER_OT_paint_terrain_from_osm(Operator):
    """Paint terrain (legacy name — delegates to paint_terrain_direct)."""
    bl_idname = "asciicker.paint_terrain_from_osm"
    bl_label = "Paint Terrain"
    bl_options = {'REGISTER'}
    def execute(self, context):
        return bpy.ops.asciicker.paint_terrain_direct()


# ---------------------------------------------------------------------------
# Direct terrain vertex-color painting (from-scratch path)
# ---------------------------------------------------------------------------

def _point_in_polygon_2d(px, py, polygon):
    """Ray-casting point-in-polygon test. Returns True if (px,py) is inside polygon."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _polygon_bbox(polygon):
    """Return (min_x, min_y, max_x, max_y) for a polygon."""
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return (min(xs), min(ys), max(xs), max(ys))


def _polygon_bbox_area(polygon):
    """Return bbox area (width * height) for a polygon. Used for paint priority sorting."""
    bmin_x, bmin_y, bmax_x, bmax_y = _polygon_bbox(polygon)
    return (bmax_x - bmin_x) * (bmax_y - bmin_y)


# Display colors for terrain material IDs (R, G, B, A)
_TERRAIN_DISPLAY_COLORS = {
    0: (0.2, 0.4, 0.8, 1.0), 1: (0.2, 0.6, 0.2, 1.0), 2: (0.5, 0.3, 0.1, 1.0), 3: (0.5, 0.5, 0.5, 1.0),
    4: (0.9, 0.8, 0.5, 1.0), 5: (0.95, 0.95, 1.0, 1.0), 6: (0.4, 0.25, 0.1, 1.0), 7: (0.6, 0.6, 0.7, 1.0),
}


def _vertex_hits_polygon(vx, vy, polygon):
    """Test if a vertex should be painted for a polygon, using cell-center sampling.

    ROOT-14 FIX: The A3D exporter (export_a3d.py, extract_terrain_patches) samples
    material IDs at cell centers offset +0.5 from integer grid positions:
        world_x = px * PATCH_SIZE + cell_x + 0.5
    It then finds the nearest vertex and reads that vertex's color.

    If we only test the vertex position (vx, vy), narrow features (roads 2-4 units
    wide, water edges) get scattered dots: the cell center may be inside the polygon
    but its nearest vertex is outside, so the vertex never gets painted.

    Fix: test the vertex position and the full 3x3 neighborhood at 0.5-cell
    increments around it. The diagonal-only check fixed the common case, but
    narrow axis-aligned strips can still miss when the winning sample sits on
    the left/right/top/bottom center around a vertex rather than a diagonal.
    If ANY nearby sample point is inside the polygon, the vertex should be painted.
    """
    # Test the vertex position itself first (fast path for interior vertices)
    if _point_in_polygon_2d(vx, vy, polygon):
        return True
    # Test the surrounding half-cell samples that the nearest-vertex lookup
    # can plausibly collapse back onto this vertex.
    for dx in (-0.5, 0.0, 0.5):
        for dy in (-0.5, 0.0, 0.5):
            if dx == 0.0 and dy == 0.0:
                continue
            if _point_in_polygon_2d(vx + dx, vy + dy, polygon):
                return True
    return False


def paint_terrain_vertex_colors(terrain_obj, polygons_with_mat):
    """Paint MaterialID vertex colors from (polygon, mat_id) pairs. Returns painted count.

    Uses cell-center sampling to match the A3D exporter's query positions, fixing
    ROOT-14 (scattered dots on narrow features like roads and water edges).

    [RC-17 FIX] Performance: per-vertex result cache avoids redundant PIP tests
    across shared loop corners (4x speedup on quad meshes). Polygons are iterated
    inner, vertices outer, with bbox pre-filter on each polygon.
    """
    mesh = terrain_obj.data
    mat_world = terrain_obj.matrix_world

    # Get or create MaterialID color attribute
    vcol = None
    if hasattr(mesh, 'color_attributes'):
        for attr in mesh.color_attributes:
            if attr.name == "MaterialID":
                vcol = attr
                break
        if vcol is None:
            vcol = mesh.color_attributes.new(
                name="MaterialID", type='BYTE_COLOR', domain='CORNER',
            )
    elif hasattr(mesh, 'vertex_colors'):
        if not mesh.vertex_colors:
            mesh.vertex_colors.new(name="MaterialID")
        vcol = mesh.vertex_colors.active

    if vcol is None:
        return 0

    # Pre-compute bounding boxes for fast rejection
    # ROOT-14: expand bbox by 0.5 in each direction to account for cell-center
    # offset tests — a vertex at the bbox edge may have a cell center inside.
    polys_with_bbox = []
    for polygon, mat_id in polygons_with_mat:
        bmin_x, bmin_y, bmax_x, bmax_y = _polygon_bbox(polygon)
        polys_with_bbox.append((polygon, mat_id,
                                (bmin_x - 0.5, bmin_y - 0.5,
                                 bmax_x + 0.5, bmax_y + 0.5)))

    # Build vertex world-space XY cache
    num_verts = len(mesh.vertices)
    vert_xy = [None] * num_verts
    for v in mesh.vertices:
        co = mat_world @ v.co
        vert_xy[v.index] = (co.x, co.y)

    # Per-vertex result cache: each vertex is shared by ~4 loop corners (quads).
    # Computing PIP once per vertex instead of once per loop corner = 4x speedup.
    # Value: mat_id if hit, -1 if tested and no match, None if untested.
    vert_result = [None] * num_verts

    # Phase 1: Resolve per-vertex material by testing against all polygons.
    for vi in range(num_verts):
        if vert_xy[vi] is None:
            vert_result[vi] = -1
            continue
        vx, vy = vert_xy[vi]
        for polygon, mat_id, (bmin_x, bmin_y, bmax_x, bmax_y) in polys_with_bbox:
            if vx < bmin_x or vx > bmax_x or vy < bmin_y or vy > bmax_y:
                continue
            if _vertex_hits_polygon(vx, vy, polygon):
                vert_result[vi] = mat_id
                break
        if vert_result[vi] is None:
            vert_result[vi] = -1  # Tested, no match

    # Phase 2: Apply cached results to all loop corners (fast — no PIP tests).
    painted = 0
    for li, loop in enumerate(mesh.loops):
        mat_id = vert_result[loop.vertex_index]
        if mat_id >= 0:
            r = mat_id / 255.0
            display = _TERRAIN_DISPLAY_COLORS.get(mat_id, (r, 0.5, 0.5, 1.0))
            vcol.data[li].color_srgb = (r, display[1], display[2], display[3])
            painted += 1

    return painted


class ASCIICKER_OT_paint_terrain_direct(Operator):
    """Paint terrain vertex colors directly from blosm OSM data (no asciiid needed)."""
    bl_idname = "asciicker.paint_terrain_direct"
    bl_label = "Paint Terrain (Direct)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.asciicker_osm_painter

        # Find terrain object
        terrain_obj = None
        for obj in context.scene.objects:
            if obj.type == 'MESH' and obj.name.lower().startswith('terrain'):
                terrain_obj = obj
                break

        if terrain_obj is None:
            self.report({'ERROR'}, "No Terrain object found in scene")
            return {'CANCELLED'}

        # Scan and categorize blosm objects
        categories = scan_blosm_objects(context, props)
        if not categories:
            self.report({'WARNING'}, "No blosm OSM objects found in scene")
            return {'CANCELLED'}

        # Extract polygons with mat_ids (in Blender world space — no transform needed)
        polygons_with_mat = []
        total_polygons = 0
        cat_counts = {}

        for cat_name, objects in categories.items():
            cat_count = 0
            for obj, mat_id in objects:
                if obj.type == 'MESH':
                    polygon = extract_boundary_2d(obj)
                    if polygon is None or len(polygon) < 3:
                        continue
                    if len(polygon) > 64:
                        polygon = douglas_peucker(polygon, props.simplify_tolerance)
                    polygons_with_mat.append((polygon, mat_id))
                    total_polygons += 1
                    cat_count += 1

                elif obj.type == 'CURVE':
                    hw_type = obj.get('highway', 'residential')
                    half_w = ROAD_HALF_WIDTHS.get(hw_type, 3.0)
                    road_polys = extract_road_polygon(obj, half_w)
                    if not road_polys:
                        continue
                    for polygon in road_polys:
                        if len(polygon) < 3:
                            continue
                        if len(polygon) > 64:
                            polygon = douglas_peucker(polygon, props.simplify_tolerance)
                        polygons_with_mat.append((polygon, mat_id))
                        total_polygons += 1
                        cat_count += 1

            cat_counts[cat_name] = cat_count

        if not polygons_with_mat:
            self.report({'WARNING'}, "No paintable polygons extracted")
            return {'CANCELLED'}

        # Sort by polygon bbox area ascending: smallest area first = highest priority.
        # "First polygon wins" in paint_terrain_vertex_colors means specific features
        # (narrow roads, small water bodies) must appear before large background areas
        # (landuse=grass covering the whole campus) to avoid being overpainted.
        polygons_with_mat.sort(key=lambda item: _polygon_bbox_area(item[0]))

        # Paint
        painted = paint_terrain_vertex_colors(terrain_obj, polygons_with_mat)

        # Report
        breakdown = ', '.join(f'{c} {n}' for n, c in cat_counts.items() if c > 0)
        self.report({'INFO'},
                    f"Painted {painted} vertices from {total_polygons} polygons "
                    f"({breakdown})")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# PropertyGroup and registration
# ---------------------------------------------------------------------------

class ASCIICKER_OSMTerrainPainterProperties(PropertyGroup):
    # Calibration
    scale: FloatProperty(
        name="Scale", description="blosm-to-engine scale factor",
        default=1.0, min=0.01, max=100.0,
    )
    offset_x: FloatProperty(
        name="Offset X", description="Calibration offset X (Blender units)",
        default=0.0,
    )
    offset_y: FloatProperty(
        name="Offset Y", description="Calibration offset Y (Blender units)",
        default=0.0,
    )
    # Material IDs
    grass_mat: IntProperty(name="Grass", default=1, min=0, max=255)
    # [RC-27 FIX] Changed from 7 (cobblestone, black in game palette) to 4
    # (sand/grey, renders as visible grey in the game palette).
    road_mat: IntProperty(name="Road", default=4, min=0, max=255)
    concrete_mat: IntProperty(name="Concrete", default=3, min=0, max=255)
    residential_mat: IntProperty(name="Residential", default=2, min=0, max=255)
    default_mat: IntProperty(name="Default", default=1, min=0, max=255)
    water_mat: IntProperty(name="Water", default=0, min=0, max=255)
    sand_mat: IntProperty(name="Sand", default=4, min=0, max=255)
    # Settings
    simplify_tolerance: FloatProperty(
        name="Simplify", description="Douglas-Peucker epsilon for vertex reduction",
        default=1.0, min=0.1, max=10.0,
    )
    cmd_file: StringProperty(
        name="Cmd File", description="Path to MCP relay command file",
        default="/tmp/asciiid_cmd",
    )
    # Scan results (read-only display)
    scan_total: IntProperty(name="Total", default=0)
    scan_grass: IntProperty(name="Grass", default=0)
    scan_road: IntProperty(name="Roads", default=0)
    scan_concrete: IntProperty(name="Concrete", default=0)
    scan_residential: IntProperty(name="Residential", default=0)
    scan_default: IntProperty(name="Default", default=0)


classes = (
    ASCIICKER_OSMTerrainPainterProperties,
    ASCIICKER_OT_auto_calibrate,
    ASCIICKER_OT_scan_osm_scene,
    ASCIICKER_OT_paint_terrain_from_osm,
    ASCIICKER_OT_paint_terrain_direct,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.asciicker_osm_painter = bpy.props.PointerProperty(
        type=ASCIICKER_OSMTerrainPainterProperties,
    )


def unregister():
    del bpy.types.Scene.asciicker_osm_painter
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
