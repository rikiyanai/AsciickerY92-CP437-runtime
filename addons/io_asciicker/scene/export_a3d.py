# ==========================================================================
# WARNING: OSM BASELINE CONFUSION HAS CAUSED 5+ FALSE CLOSURES (2026-04-30)
# ==========================================================================
# Two Z-baseline constants exist:
#   BASE_TERRAIN_HEIGHT = 0xA000 (40960) — LEGACY mesh instance baseline
#   TERRAIN_EXPORT_BASELINE = 120        — NEW terrain/OSM ground baseline
# Both live in a3d_format.py:85-86. Using the wrong one causes:
#   - Buildings floating 40840 units above ground (FL-2533)
#   - Importer decoding Z at wrong baseline (FL-2549)
#   - Round-trip proof loops that hide the real bug (FL-2550)
#
# extract_instances() z_baseline parameter controls which value is written.
# import_a3d.py reverse_instance_transform() z_baseline controls decoding.
# If you change one, you MUST change the other AND verify with a screenshot.
# Source-only verification is explicitly insufficient (FL-2550).
#
# AGENTS: if buildings look wrong after your change, check which baseline
# constant you used. If you used BASE_TERRAIN_HEIGHT for OSM content, that
# is WRONG. Use TERRAIN_EXPORT_BASELINE=120. See FL-2554 for the full
# repeated-correction audit.
# ==========================================================================
# Asciicker A3D Map Export
# Exports Blender scene to A3D format (bypasses asciiid editor)
#
# [DEPENDENCY:BLENDER] Heavy use of bpy/Blender API for scene traversal,
#     mesh evaluation, vertex-color sampling, and transform extraction.
# [DATA-CONTRACT:A3D] Produces the binary file layout defined in a3d_format.py
#     and consumed by the C++ engine's world.cpp loader.
# [FLOW:WORLD] Blender scene -> extract_terrain_patches / extract_instances /
#     extract_enemy_generators -> save_a3d -> binary .a3d -> engine world.cpp.

"""
A3D world exporter -- Blender scene to binary ``.a3d`` file.

ARCHITECTURE
============
This module is the core of the A3D export pipeline.  It bridges Blender's
scene graph to the engine's binary terrain/world format through these stages:

    1. **Terrain extraction** -- A subdivided Blender mesh named ``Terrain`` is
       sampled onto a regular grid of :class:`~a3d_format.A3DPatch` tiles.
       Height is sampled at 5x5 vertices per patch; material IDs are sampled
       at 8x8 cells from vertex colors (Red channel = material index).
    2. **Instance extraction** -- All non-terrain mesh objects become
       :class:`~a3d_format.A3DInstance` records.  Object names (minus numeric
       suffixes like ``.001``) resolve to ``.akm`` mesh files at engine load
       time.  Transforms are converted from Blender's row-major to the
       engine's column-major layout with Z-scale adjustments.
    3. **Enemy generator extraction** -- Blender Empty objects named
       ``EnemyGen*`` become :class:`~a3d_format.A3DEnemyGen` spawn points
       with equipment configured via custom properties.
    4. **Material palette** -- Sourced from ``default_materials.py`` (extracted
       from a reference ``.a3d`` or generated procedurally).
    5. **Binary write** -- All sections are written sequentially to produce a
       complete ``.a3d`` file the engine can load directly.

KEY EXPORTS
-----------
- ``save_a3d()``  -- Main entry point called by the Blender operator.

PIPELINE CONTEXT
----------------
This exporter allows artists to build worlds entirely in Blender, bypassing
the engine's built-in asciiid editor.  The resulting ``.a3d`` file is binary-
compatible with the engine's ``LoadWorldAS3D`` in ``world.cpp``.

Coordinate system notes:
    - Blender Z-up maps to engine Z-up.
    - Blender units are scaled by ``HEIGHT_SCALE`` (16) for Z translation.
    - Patch coordinates are offset by ``PATCH_OFFSET_X/Y`` to align with the
      original game map origin convention.
    - Terrain material IDs are authored in the ``MaterialID`` vertex-color
      layer's red channel; green/blue remain artist-display channels.
"""

import math
import os
import re
import struct
from io_asciicker import path_utils
from .a3d_format import (
    A3DHeader, A3DPatch, A3DInstance, A3DPlayerStart, A3DEnemyGen, A3DMinimapMarker,
    WORLD_FORMAT_VERSION,
    HEIGHT_CELLS, VISUAL_CELLS, HEIGHT_SCALE, BASE_TERRAIN_HEIGHT,
    TERRAIN_EXPORT_BASELINE,
)
from .default_materials import get_default_materials_binary

# ---------------------------------------------------------------------------
# Patch grid constants
# WHY: The original game map uses a specific origin convention.  These offsets
# shift Blender-space patch coordinates so that (0,0) in Blender corresponds
# to the expected starting position in the engine's world grid.
# ---------------------------------------------------------------------------
PATCH_SIZE = VISUAL_CELLS  # 8 units per patch
PATCH_OFFSET_X = -4  # Shift patches left to match original map origin
PATCH_OFFSET_Y = -4  # Shift patches down to match original map origin


def _export_engine_position(x, y, z, z_baseline=BASE_TERRAIN_HEIGHT):
    """Convert Blender-space XYZ to the engine/world-space A3D position contract."""
    return (
        float(x + PATCH_OFFSET_X * PATCH_SIZE),
        float(y + PATCH_OFFSET_Y * PATCH_SIZE),
        float(z * HEIGHT_SCALE + z_baseline),
    )


def matrix_to_list(matrix):
    """Convert Blender Matrix to flat list of 16 doubles (column-major for game engine).

    Blender stores matrices row-major; the C++ engine uses column-major
    (OpenGL convention) where indices [12], [13], [14] hold the translation.

    Args:
        matrix: A ``mathutils.Matrix`` (4x4) from Blender.

    Returns:
        A list of 16 floats in column-major order.
    """
    result = []
    # WHY column iteration first: column-major layout means we iterate
    # columns as the outer loop so that column 0 occupies indices 0-3,
    # column 1 occupies 4-7, etc.  Translation ends up at 12-14.
    for col in range(4):
        for row in range(4):
            result.append(float(matrix[row][col]))
    return result


def validate_scene_objects(objects, max_faces=5000, check_manifold=True):
    """Run validation on all objects to be exported.

    Attempts to import the ``validate_blosm_mesh`` script from the project's
    ``scripts/`` directory.  If the script is unavailable (e.g. running inside
    Blender without the repo on ``PYTHONPATH``), validation is silently skipped.

    Args:
        objects:        List of Blender mesh objects to validate.
        max_faces:      Maximum face count per object before flagging an error.
        check_manifold: Whether to check for non-manifold geometry.

    Returns:
        A tuple ``(is_valid, errors)`` where *is_valid* is ``True`` if all
        objects pass, and *errors* is a list of human-readable error strings.
        Returns ``(True, [])`` if the validation script cannot be imported.

    Raises:
        No exceptions are raised; import failures are caught and logged.
    """
    validate_blosm_mesh = None

    # Try importing as package first (works in dev/test env with PYTHONPATH)
    try:
        from scripts import validate_blosm_mesh as validate_blosm_mesh_module
        validate_blosm_mesh = validate_blosm_mesh_module
    except ImportError:
        pass

    # Fallback: resolve repo root then import as package
    if validate_blosm_mesh is None:
        repo_root = path_utils.ensure_repo_root(__file__)
        if not repo_root:
            print("Warning: repo root not found; skipping validation")
            return True, []
        try:
            from scripts import validate_blosm_mesh as validate_blosm_mesh_module
            validate_blosm_mesh = validate_blosm_mesh_module
        except ImportError as e:
            print(f"Warning: validate_blosm_mesh import failed. Error: {e}")
            return True, []

    all_errors = []

    for obj in objects:
        res = validate_blosm_mesh.validate_object(obj, max_faces=max_faces, check_manifold=check_manifold)
        if not res['valid']:
            for err in res['errors']:
                all_errors.append(f"{obj.name}: {err}")

    return len(all_errors) == 0, all_errors


def process_scene_objects(objects):
    """Run processing on objects (e.g. blosm texture baking).

    This is a pre-export hook that can transform objects before they are
    serialized.  Currently it delegates to ``process_blosm.process_buildings``
    for automatic texture-to-vertex-color conversion of OSM/blosm imports.

    Silently skipped if the ``process_blosm`` script is not available.

    Args:
        objects: List of Blender mesh objects to process.
    """
    process_blosm = None

    # Try importing as package first
    try:
        from scripts import process_blosm as process_blosm_module
        process_blosm = process_blosm_module
    except ImportError:
        pass

    if process_blosm is None:
        repo_root = path_utils.ensure_repo_root(__file__)
        if not repo_root:
            print("Warning: repo root not found; skipping processing")
            return
        try:
            from scripts import process_blosm as process_blosm_module
            process_blosm = process_blosm_module
        except ImportError:
            print("Warning: process_blosm script not found.")
            return

    process_blosm.process_buildings(objects)


def get_terrain_object(context):
    """Find the terrain plane object in the scene.

    [DEPENDENCY:BLENDER] Iterates ``context.scene.objects``.

    The exporter expects a single mesh object named ``Terrain`` (case-
    insensitive).  This is the plane whose geometry provides height data
    and whose vertex colors encode material IDs.

    Args:
        context: Blender context.

    Returns:
        The Blender object, or ``None`` if not found.
    """
    for obj in context.scene.objects:
        if obj.type == 'MESH' and obj.name.lower() == 'terrain':
            return obj
    return None


def get_mesh_objects(context, use_selection=False):
    """Get exportable instance objects except terrain and scene infrastructure.

    [DEPENDENCY:BLENDER] Reads ``context.selected_objects`` or
    ``context.scene.objects`` and checks ``obj.type``.

    Filters out objects whose names (case-insensitive) match known
    non-exportable items: ``terrain``, ``camera``, ``light``, ``sun``,
    ``lamp``.

    Args:
        context:       Blender context.
        use_selection: If ``True``, only consider currently selected objects.

    Returns:
        A list of Blender objects suitable for instance export.
    """
    objects = []
    source = context.selected_objects if use_selection else context.scene.objects

    # WHY this skip list: These are Blender scene infrastructure objects that
    # have no representation in the A3D world format.  Terrain is handled
    # separately via extract_terrain_patches().
    skip_names = {'terrain', 'camera', 'light', 'sun', 'lamp'}

    for obj in source:
        if obj.get('a3d_variant') == 'item':
            objects.append(obj)
        elif obj.type == 'MESH':
            if obj.name.lower() not in skip_names:
                objects.append(obj)

    return objects


class TerrainSpatialIndex:
    """Pre-built spatial index for fast terrain sampling.

    Transforms all vertices once at construction, builds a dict mapping
    grid cell -> vertex index for O(1) nearest-vertex lookups, and
    pre-computes a vertex-to-loop mapping for fast material lookups.
    """

    def __init__(self, mesh, matrix, vertex_colors=None, is_byte_color=False, cell_size=1.0):
        self.mesh = mesh
        self.world_coords = []
        self.grid = {}
        self.cell_size = cell_size
        self.vertex_colors = vertex_colors
        self.is_byte_color = is_byte_color

        # Transform all vertices to world space once
        for vert in mesh.vertices:
            co = matrix @ vert.co
            self.world_coords.append((co.x, co.y, co.z))

        # Build grid index: cell_key -> list of (vert_idx)
        inv = 1.0 / cell_size
        for i, (wx, wy, _) in enumerate(self.world_coords):
            key = (int(math.floor(wx * inv)), int(math.floor(wy * inv)))
            self.grid.setdefault(key, []).append(i)

        # Build vertex -> first loop index mapping for material lookups
        self.vert_to_loop = {}
        if vertex_colors:
            for loop in mesh.loops:
                vi = loop.vertex_index
                if vi not in self.vert_to_loop:
                    self.vert_to_loop[vi] = loop.index

    def _nearest(self, x, y):
        """Find nearest vertex index to (x, y) using grid lookup."""
        inv = 1.0 / self.cell_size
        cx = int(math.floor(x * inv))
        cy = int(math.floor(y * inv))

        min_dist = float('inf')
        best = 0

        # Search 3x3 neighborhood
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for vi in self.grid.get((cx + dx, cy + dy), ()):
                    wx, wy, _ = self.world_coords[vi]
                    d = (wx - x) ** 2 + (wy - y) ** 2
                    if d < min_dist:
                        min_dist = d
                        best = vi
        return best

    def sample_height(self, x, y):
        """O(1) height sample at world position (x, y)."""
        vi = self._nearest(x, y)
        return self.world_coords[vi][2]

    def sample_material(self, x, y):
        """O(1) material ID sample at world position (x, y)."""
        if not self.vertex_colors:
            return 1
        vi = self._nearest(x, y)
        li = self.vert_to_loop.get(vi)
        if li is None:
            return 1
        item = self.vertex_colors[li]
        if self.is_byte_color and hasattr(item, 'color_srgb'):
            color = item.color_srgb
        elif hasattr(item, 'color'):
            color = item.color
        else:
            return 1
        return int(color[0] * 255) & 0xFF


def extract_terrain_patches(terrain_obj, context):
    """Extract terrain patches from Blender plane into A3DPatch records.

    [DEPENDENCY:BLENDER] Uses depsgraph evaluation, matrix_world transforms,
    mesh.vertices, mesh.color_attributes (Blender 4.x), and legacy
    vertex_colors API.

    [FLOW:WORLD] Terrain object -> evaluated mesh -> bounding box grid ->
    per-patch height/material sampling -> list of A3DPatch records.

    Evaluates the terrain mesh (applying modifiers), computes the world-space
    bounding box, divides it into a grid of ``PATCH_SIZE``-unit tiles, and
    samples each tile's height map and visual/material map.

    Vertex color handling is Blender-4.x-compatible: prefers a color attribute
    named ``MaterialID``, falls back to the active color attribute, and finally
    to the legacy ``vertex_colors`` API.

    Args:
        terrain_obj: The Blender object named ``Terrain``.
        context:     Blender context (used for depsgraph evaluation).

    Returns:
        A list of :class:`~a3d_format.A3DPatch` instances covering the
        terrain's bounding box.
    """
    patches = []

    depsgraph = context.evaluated_depsgraph_get()
    obj_eval = terrain_obj.evaluated_get(depsgraph)
    mesh = obj_eval.to_mesh()
    matrix = terrain_obj.matrix_world

    # Get bounding box in world space
    min_x = min_y = float('inf')
    max_x = max_y = float('-inf')

    for vert in mesh.vertices:
        world_co = matrix @ vert.co
        min_x = min(min_x, world_co.x)
        min_y = min(min_y, world_co.y)
        max_x = max(max_x, world_co.x)
        max_y = max(max_y, world_co.y)

    # Get vertex colors (Blender 4.x compatible)
    # WHY this fallback chain: Blender 4.x replaced vertex_colors with
    # color_attributes.  We try the new API first, look for a specifically
    # named attribute "MaterialID", then fall back to legacy API.
    vertex_colors = None
    is_byte_color = False
    if hasattr(mesh, 'color_attributes') and mesh.color_attributes:
        # Find MaterialID or use active
        vcol = None
        for attr in mesh.color_attributes:
            if attr.name == "MaterialID":
                vcol = attr
                break
        if vcol is None:
            vcol = mesh.color_attributes.active_color
        if vcol:
            vertex_colors = vcol.data
            is_byte_color = (vcol.data_type == 'BYTE_COLOR')
    elif hasattr(mesh, 'vertex_colors') and mesh.vertex_colors:
        vertex_colors = mesh.vertex_colors.active.data

    # Build spatial index for O(1) per-sample lookups instead of O(V)
    spatial = TerrainSpatialIndex(
        mesh, matrix, vertex_colors, is_byte_color, cell_size=1.0)

    # Calculate patch grid -- divide the terrain bounding box into tiles
    patch_size = PATCH_SIZE
    patch_offset_x = PATCH_OFFSET_X
    patch_offset_y = PATCH_OFFSET_Y

    start_px = int(math.floor(min_x / patch_size))
    start_py = int(math.floor(min_y / patch_size))
    end_px = int(math.ceil(max_x / patch_size))
    end_py = int(math.ceil(max_y / patch_size))

    for py in range(start_py, end_py):
        for px in range(start_px, end_px):
            # WHY offset coordinates: The patch x/y written to the file use
            # the engine's origin convention.  Sampling still uses original
            # Blender-space px/py so world_x/world_y match the mesh.
            patch = A3DPatch(px + patch_offset_x, py + patch_offset_y)

            # Sample heights for 5x5 grid (HEIGHT_CELLS+1 vertices per axis)
            for hy in range(HEIGHT_CELLS + 1):
                for hx in range(HEIGHT_CELLS + 1):
                    # WHY hx*2: HEIGHT_CELLS=4 means 5 vertices span 8 units
                    # (PATCH_SIZE), so vertex spacing is 8/4 = 2 world units.
                    world_x = px * patch_size + hx * 2
                    world_y = py * patch_size + hy * 2
                    height = spatial.sample_height(world_x, world_y)
                    # WHY 120 baseline: the game water level is 55 (mainmenu.cpp
                    # line 1393: `float water = 55;`). The A3D header has NO
                    # water field — it is hardcoded in the game binary, not
                    # exported. Original game_map heights: min=0, max=817,
                    # median(nonzero)=124. A flat Blender terrain at Z=0 needs
                    # to export at ~120 to sit above water=55 and match the
                    # game's natural ground-level height range.
                    # NOTE: 0x8000 appears in asciiid.cpp:6326 as the MCP ASCII
                    # RENDER command's water param — that is the text renderer
                    # path only, NOT the interactive SDL OpenGL viewport.
                    # [DATA-CONTRACT:A3D] Height must be uint16; overflow is
                    # masked with 0xFFFF.  Negative Blender Z wraps around.
                    height_val = (int(height * HEIGHT_SCALE) + TERRAIN_EXPORT_BASELINE) & 0xFFFF
                    patch.height[hy][hx] = height_val

            # Sample materials for 8x8 visual grid (VISUAL_CELLS per axis)
            for vy in range(VISUAL_CELLS):
                for vx in range(VISUAL_CELLS):
                    # WHY +0.5: Sample at cell center, not cell corner, for
                    # more representative material assignment.
                    world_x = px * patch_size + vx + 0.5
                    world_y = py * patch_size + vy + 0.5
                    mat_id = spatial.sample_material(world_x, world_y)
                    patch.visual[vy][vx] = mat_id

            patches.append(patch)

    obj_eval.to_mesh_clear()
    return patches


def extract_instances(objects, *, z_baseline=BASE_TERRAIN_HEIGHT):
    """Extract mesh instances from Blender objects into A3DInstance records.

    [FLOW:WORLD] Blender mesh objects -> name normalization + transform
    adjustment -> A3DInstance records written after the material palette.

    [DEPENDENCY:BLENDER] Reads ``obj.name``, ``obj.matrix_world``, and
    optional custom properties ``a3d_flags`` / ``a3d_story_id``.

    Each Blender mesh object becomes an instance referencing an ``.akm`` file.
    Numeric suffixes (e.g. ``tree.001``) are stripped so that duplicates share
    the same mesh asset.

    Transform adjustments:
      - Patch offset is applied to XY translation to match engine grid origin.
      - Z translation is scaled by HEIGHT_SCALE and shifted by ``z_baseline``.
        Ordinary world exports keep the legacy mesh-instance offset
        (``BASE_TERRAIN_HEIGHT``). Deferred OSM building payloads should use
        ``TERRAIN_EXPORT_BASELINE`` so the saved instances match the terrain
        baseline in the same prebake/buildings-only handoff.
      - Imported A3D mesh objects can also carry ``a3d_instance_z_baseline``
        to preserve the original stored Z contract on round-trip export.
      - Z scale component of the matrix is also scaled by HEIGHT_SCALE.

    Args:
        objects: List of Blender mesh objects.

    Returns:
        A list of :class:`~a3d_format.A3DInstance` records.
    """
    instances = []

    for obj in objects:
        if obj.get('a3d_variant') == 'item':
            inst = A3DInstance(variant='item')
            inst.item_definition_id = int(obj.get('a3d_item_definition_id', 0))
            inst.visual_style_id = int(obj.get('a3d_visual_style_id', 0))
            inst.presentation_kind_id = int(obj.get('a3d_presentation_kind_id', 603))
            inst.item_count = int(obj.get('a3d_item_count', 1))
            inst.pos = [float(obj.location.x), float(obj.location.y), float(obj.location.z)]
            inst.yaw = float(obj.rotation_euler.z)
            inst.flags = int(obj.get('a3d_flags', 3))
            inst.story_id = int(obj.get('a3d_story_id', -1))
            instances.append(inst)
            continue

        # WHY a3d_mesh_ref: Blender object names cannot contain '/', so
        # objects placed in subdirectories (e.g. fixtures/bench.akm) use
        # this custom property to supply the exact mesh_name string.
        explicit_ref = obj.get('a3d_mesh_ref', '')
        if explicit_ref:
            mesh_name = explicit_ref
            if not mesh_name.endswith('.akm'):
                mesh_name = mesh_name + '.akm'
        else:
            mesh_name = obj.name

            # WHY strip numeric suffix: Blender auto-names duplicates as
            # "tree.001", "tree.002" etc.  All duplicates should reference the
            # same .akm asset file.
            # TODO(PIPELINE-FIX): This only strips a purely-numeric final suffix.
            #     Names like "wall.damaged.001" work fine, but "v2.0" would be
            #     incorrectly stripped to "v2".  Consider a regex for \.\d{3,}$.
            if '.' in mesh_name:
                parts = mesh_name.rsplit('.', 1)
                if parts[1].isdigit():
                    mesh_name = parts[0]

            # WHY append .akm: The engine expects mesh references to include
            # the file extension; the Blender object name is just the base.
            if not mesh_name.endswith('.akm'):
                mesh_name = mesh_name + '.akm'

        inst_name = obj.name
        transform = matrix_to_list(obj.matrix_world)

        # Apply patch grid offset to XY translation (column-major indices 12, 13)
        # WHY configurable Z baseline: most mesh-instance exports still use the
        # legacy BASE_TERRAIN_HEIGHT contract, but deferred OSM building specs
        # must line up with the terrain baseline in the same run-local A3D. If
        # they keep the legacy 0xA000 offset while the terrain stays near 120,
        # buildings float in buildings-only maps and the later building bake
        # can inject absurd 40k-height spikes into the final terrain.
        instance_z_baseline = float(obj.get('a3d_instance_z_baseline', z_baseline))
        transform[12], transform[13], transform[14] = _export_engine_position(
            transform[12],
            transform[13],
            transform[14],
            z_baseline=instance_z_baseline,
        )

        # Apply Z scale factor to the scale component of the matrix
        # WHY index 10: In column-major 4x4, [10] is the Z-axis Z-component
        # (the "scale Z" element when no shear is present).
        transform[10] *= HEIGHT_SCALE

        # WHY default flags=3: INST_VISIBLE (1) | INST_USE_TREE (2) means
        # the instance is rendered and participates in the spatial tree.
        flags = obj.get('a3d_flags', 3)  # INST_VISIBLE | INST_USE_TREE
        # WHY default story_id=-1: Negative means the instance is not
        # gated behind any story/quest progression.
        story_id = obj.get('a3d_story_id', -1)  # -1 = not in story

        inst = A3DInstance(
            mesh_name=mesh_name,
            inst_name=inst_name,
            transform=transform,
            flags=flags,
            story_id=story_id
        )

        instances.append(inst)

    return instances


def extract_enemy_generators(context):
    """Extract enemy generators from Empty objects named ``EnemyGen*``.

    [DEPENDENCY:BLENDER] Iterates ``context.scene.objects``, reads
    ``obj.location`` and custom properties (``alive_max``, ``armor``, etc.).

    [FLOW:WORLD] Blender Empties -> A3DEnemyGen records -> final section
    of the .a3d binary -> engine enemygen.cpp.

    Each qualifying Empty object's custom properties are read to populate
    the :class:`~a3d_format.A3DEnemyGen` fields (equipment loadout,
    respawn timing, maximum alive count).

    Args:
        context: Blender context.

    Returns:
        A list of :class:`~a3d_format.A3DEnemyGen` instances.
    """
    generators = []

    for obj in context.scene.objects:
        if obj.type == 'EMPTY' and obj.name.lower().startswith('enemygen'):
            gen = A3DEnemyGen()
            gen.pos = list(_export_engine_position(
                obj.location.x,
                obj.location.y,
                obj.location.z,
            ))
            gen.alive_max = obj.get('alive_max', 1)
            gen.revive_min = obj.get('revive_min', 0)
            gen.revive_max = obj.get('revive_max', 0)
            gen.armor = obj.get('armor', 0)
            gen.helmet = obj.get('helmet', 0)
            gen.shield = obj.get('shield', 0)
            gen.sword = obj.get('sword', 0)
            gen.crossbow = obj.get('crossbow', 0)
            generators.append(gen)

    return generators


_BLENDER_SUFFIX_RE = re.compile(r"\.\d{3}$")
_GENERIC_BUILDING_RE = re.compile(r"^Building_\d+$")
_FIXTURE_MARKER_NAMES = {
    "bench",
    "bollard",
    "bus_stop",
    "picnic_table",
    "planter",
    "stone",
    "street_lamp",
    "trash_can",
}


def _strip_blender_suffix(name):
    """Remove Blender's duplicated-object numeric suffixes from object names."""
    cleaned = (name or "").strip()
    while _BLENDER_SUFFIX_RE.search(cleaned):
        cleaned = _BLENDER_SUFFIX_RE.sub("", cleaned)
    return cleaned


def _marker_display_name(name):
    base_name = _strip_blender_suffix(name)
    display = re.sub(r"[_\-\s]+", " ", base_name).strip()
    return re.sub(r"\s+", " ", display)


def _marker_label_parts(name):
    display = _marker_display_name(name)
    for ch in display:
        if ch.isalnum():
            return ch.upper(), display
    return "X", display


def _marker_export_objects(context, mesh_objects):
    """Return objects eligible to emit embedded minimap markers.

    Marker-only empties let baked-mode exports preserve named-building labels
    after the authoritative mesh instances are deferred out of the base map.
    """
    seen = {obj.as_pointer() for obj in mesh_objects}
    marker_objects = list(mesh_objects)
    for obj in context.scene.objects:
        if obj.as_pointer() in seen:
            continue
        if obj.get("a3d_marker_only"):
            marker_objects.append(obj)
    return marker_objects


def extract_minimap_markers(objects):
    """Extract embedded minimap markers from preserved named building objects."""
    markers = []
    for obj in objects:
        if obj.type != "MESH" and not obj.get("a3d_marker_only"):
            continue

        name = _strip_blender_suffix(obj.name)
        # WARNING (FL-1175/FL-1179): generic Building_NNN names are skipped on
        # purpose so unnamed leftovers do not pollute the minimap. If the OSM
        # rename pass fails upstream (for example Overpass import without a
        # materialized local .osm file), a whole run can collapse to zero
        # embedded building markers here even though the exporter itself is fine.
        if not name or _GENERIC_BUILDING_RE.match(name):
            continue

        explicit_ref = (obj.get("a3d_mesh_ref", "") or "").strip().lower()
        if explicit_ref in _FIXTURE_MARKER_NAMES or name.lower() in _FIXTURE_MARKER_NAMES:
            continue

        transform = matrix_to_list(obj.matrix_world)
        x = transform[12] + PATCH_OFFSET_X * PATCH_SIZE
        y = transform[13] + PATCH_OFFSET_Y * PATCH_SIZE
        glyph, label = _marker_label_parts(name)
        markers.append(A3DMinimapMarker(
            name=name,
            label=label,
            x=x,
            y=y,
            fg=226,
            glyph=glyph,
            marker_type=A3DMinimapMarker.TYPE_BUILDING,
        ))
    return markers


def _sample_patch_height(patch, world_x, world_y):
    patch_world = float(PATCH_SIZE)
    vertex_step = patch_world / float(HEIGHT_CELLS)
    patch_origin_x = float(patch.x) * patch_world
    patch_origin_y = float(patch.y) * patch_world
    local_x = max(0.0, min(patch_world, world_x - patch_origin_x))
    local_y = max(0.0, min(patch_world, world_y - patch_origin_y))
    fx = local_x / vertex_step
    fy = local_y / vertex_step
    x0 = min(HEIGHT_CELLS - 1, max(0, int(math.floor(fx))))
    y0 = min(HEIGHT_CELLS - 1, max(0, int(math.floor(fy))))
    x1 = min(HEIGHT_CELLS, x0 + 1)
    y1 = min(HEIGHT_CELLS, y0 + 1)
    tx = max(0.0, min(1.0, fx - x0))
    ty = max(0.0, min(1.0, fy - y0))
    h00 = float(patch.height[y0][x0])
    h10 = float(patch.height[y0][x1])
    h01 = float(patch.height[y1][x0])
    h11 = float(patch.height[y1][x1])
    hx0 = h00 + (h10 - h00) * tx
    hx1 = h01 + (h11 - h01) * tx
    return hx0 + (hx1 - hx0) * ty


def derive_player_start(patches, minimap_markers, objects):
    """Synthesize a map-local player-start when no explicit authoring seam exists."""
    if not patches:
        return None

    target_x = None
    target_y = None
    if minimap_markers:
        xs = [float(marker.x) for marker in minimap_markers]
        ys = [float(marker.y) for marker in minimap_markers]
        target_x = (min(xs) + max(xs)) * 0.5
        target_y = (min(ys) + max(ys)) * 0.5
    elif objects:
        xs = []
        ys = []
        for obj in objects:
            world_x, world_y, _world_z = _export_engine_position(
                float(obj.location.x),
                float(obj.location.y),
                0.0,
                z_baseline=0.0,
            )
            xs.append(world_x)
            ys.append(world_y)
        if xs and ys:
            target_x = (min(xs) + max(xs)) * 0.5
            target_y = (min(ys) + max(ys)) * 0.5

    if target_x is None or target_y is None:
        patch_xs = [int(patch.x) for patch in patches]
        patch_ys = [int(patch.y) for patch in patches]
        target_x = ((min(patch_xs) + max(patch_xs) + 1) * PATCH_SIZE) * 0.5
        target_y = ((min(patch_ys) + max(patch_ys) + 1) * PATCH_SIZE) * 0.5

    selected_patch = None
    for patch in patches:
        px0 = float(patch.x) * float(PATCH_SIZE)
        py0 = float(patch.y) * float(PATCH_SIZE)
        if px0 <= target_x <= px0 + PATCH_SIZE and py0 <= target_y <= py0 + PATCH_SIZE:
            selected_patch = patch
            break

    if selected_patch is None:
        selected_patch = min(
            patches,
            key=lambda patch: (
                (float(patch.x) * PATCH_SIZE + PATCH_SIZE * 0.5 - target_x) ** 2 +
                (float(patch.y) * PATCH_SIZE + PATCH_SIZE * 0.5 - target_y) ** 2
            ),
        )

    height = _sample_patch_height(selected_patch, target_x, target_y)
    return A3DPlayerStart(pos=[target_x, target_y, height + 200.0], yaw=0.0, dir=0.0)


def save_a3d(context, filepath, use_selection=False, export_terrain=True, skip_validation=False):
    """Main A3D export function -- serializes the Blender scene to a binary ``.a3d`` file.

    [FLOW:WORLD] Top-level orchestrator: Blender scene -> terrain patches +
    instances + enemy generators + materials -> binary .a3d on disk.

    This is the entry point called by :class:`~scene.ASCIICKER_OT_export_a3d`.
    It orchestrates the full export pipeline:

        1. Extract terrain patches (if enabled and a ``Terrain`` object exists).
        2. Gather mesh objects and run validation / processing hooks.
        3. Extract mesh instances with adjusted transforms.
        4. Extract enemy generators from ``EnemyGen*`` Empties.
        5. Obtain the 256-material palette.
        6. Write the binary file in the order defined by :mod:`a3d_format`.

    [DATA-CONTRACT:A3D] The binary layout written here must match the engine's
    ``LoadWorldAS3D`` reader in ``world.cpp``.

    Args:
        context:         Blender context.
        filepath:        Output ``.a3d`` file path.
        use_selection:   If ``True``, only export selected mesh objects
                         (terrain is always exported if present).
        export_terrain:  If ``True``, look for and export the ``Terrain`` object.
        skip_validation: If ``True``, bypass the pre-export mesh validation.

    Returns:
        ``{'FINISHED'}`` on success.

    Raises:
        RuntimeError: If pre-export mesh validation fails (non-manifold
            geometry, excessive face count, etc.) and *skip_validation*
            is ``False``.
    """

    # --- Stage 1: Terrain patches ---
    patches = []
    if export_terrain:
        terrain_obj = get_terrain_object(context)
        if terrain_obj:
            patches = extract_terrain_patches(terrain_obj, context)
            print(f"Exported {len(patches)} terrain patches")

            # Debug: Show material distribution
            mat_counts = {}
            for patch in patches:
                for row in patch.visual:
                    for mat_id in row:
                        mat_counts[mat_id] = mat_counts.get(mat_id, 0) + 1
            print(f"Material distribution: {mat_counts}")
        else:
            print("Warning: No 'Terrain' object found")

    # --- Stage 2: Gather and validate mesh objects ---
    objects = get_mesh_objects(context, use_selection)

    # Validation Hook
    if not skip_validation:
        is_valid, errors = validate_scene_objects(objects)
        if not is_valid:
            print("EXPORT CANCELLED due to validation errors:")
            for err in errors:
                print(f"  - {err}")
            raise RuntimeError("Validation Failed:\n" + "\n".join(errors[:5]))
    else:
        print("Skipping validation (caller requested)")

    # Processing Hook (Auto-Texture) -- e.g. blosm building bake
    process_scene_objects(objects)

    # --- Stage 3: Instance extraction ---
    instances = extract_instances(objects)
    print(f"Exported {len(instances)} instances")
    # Debug: show instance positions (translation is in matrix indices 12, 13, 14 for column-major)
    for inst in instances[:5]:  # Show first 5
        tx, ty, tz = inst.transform[12], inst.transform[13], inst.transform[14]
        print(f"  {inst.mesh_name}: pos=({tx:.1f}, {ty:.1f}, {tz:.1f})")

    # --- Stage 4: Enemy generators ---
    enemy_gens = extract_enemy_generators(context)
    print(f"Exported {len(enemy_gens)} enemy generators")

    # --- Stage 5: Embedded minimap markers ---
    minimap_markers = extract_minimap_markers(_marker_export_objects(context, objects))
    print(f"Exported {len(minimap_markers)} minimap markers")

    # --- Stage 5b: Map-owned player start ---
    player_start = derive_player_start(patches, minimap_markers, objects)
    if player_start:
        print(
            "Exported player start "
            f"at ({player_start.pos[0]:.1f}, {player_start.pos[1]:.1f}, {player_start.pos[2]:.1f})"
        )

    # --- Stage 6: Material palette ---
    # TODO(PIPELINE-FIX): Materials are always sourced from the default
    #     palette (default_materials.py), not from the Blender scene.  A
    #     future revision could allow per-scene material customization.
    materials_binary = get_default_materials_binary()

    # --- Stage 7: Write binary file ---
    # [DATA-CONTRACT:A3D] Section order must be: header, patches, materials,
    # instances (with format_version prefix), optional player-start, enemy
    # generators, minimap markers.
    with open(filepath, 'wb') as f:
        # Header (16 bytes)
        header = A3DHeader(len(patches))
        header.write(f)

        # Terrain patches (188 bytes each)
        for patch in patches:
            patch.write(f)

        # Materials (256 * 512 = 131072 bytes)
        f.write(materials_binary)

        # World instances (variable length)
        # WHY format_version = -4: v4 keeps the v3 item-bundle ids and adds an
        # optional map-owned player-start record after the instance stream.
        f.write(struct.pack('<i', WORLD_FORMAT_VERSION))
        f.write(struct.pack('<i', len(instances)))
        for inst in instances:
            inst.write(f)

        f.write(struct.pack('<i', 1 if player_start else 0))
        if player_start:
            player_start.write(f)

        # Enemy generators (44 bytes each)
        f.write(struct.pack('<i', len(enemy_gens)))
        for gen in enemy_gens:
            gen.write(f)

        # Embedded minimap markers (optional in older maps)
        f.write(struct.pack('<i', len(minimap_markers)))
        for marker in minimap_markers:
            marker.write(f)

    print(f"Saved A3D map: {filepath}")
    return {'FINISHED'}
