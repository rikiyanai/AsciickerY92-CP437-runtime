"""OSM operations — terrain painting, building pipeline, and A3D export via Blender addon."""

from cli_anything.blender.core.bridge import BlenderBridge

# Addon module required for all OSM operations
_ADDON_MODULE = "io_asciicker"

# Default face budget per mesh for AKM export (hard cap: 4950)
DEFAULT_TARGET_FACES = 500


def import_blosm(
    blend_file,
    min_lat,
    max_lat,
    min_lon,
    max_lon,
    mode="2D",
    buildings=True,
    save=True,
    blender_path=None,
):
    """Import OpenStreetMap data via the blosm addon.

    Downloads OSM data from the server for the given bounding box and
    imports it into the blend file. Default mode is 2D (flat footprints).

    Args:
        blend_file: Path to .blend file to import into.
        min_lat: Minimum latitude of import extent.
        max_lat: Maximum latitude of import extent.
        min_lon: Minimum longitude of import extent.
        max_lon: Maximum longitude of import extent.
        mode: blosm import mode ("2D", "3Dsimple", "3Drealistic").
        buildings: Whether to import buildings.
        save: Save .blend file after import.
        blender_path: Optional path to Blender executable.

    Returns:
        BlenderBridge result dict with import status and object counts.
    """
    save_code = f"bpy.ops.wm.save_as_mainfile(filepath={blend_file!r})" if save else ""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import addon_utils
addon_utils.enable("blosm", default_set=True)
import bpy

props = bpy.context.scene.blosm
props.dataType = "osm"
props.osmSource = "server"
props.mode = {mode!r}
props.minLat = {min_lat}
props.maxLat = {max_lat}
props.minLon = {min_lon}
props.maxLon = {max_lon}
props.buildings = {buildings}

before = len(bpy.context.scene.objects)
result = bpy.ops.blosm.import_data()
after = len(bpy.context.scene.objects)

if 'FINISHED' not in result:
    raise RuntimeError("blosm.import_data returned " + str(result))

{save_code}
buildings_list = [o for o in bpy.context.scene.objects if o.type == 'MESH' and o.get('building')]
_data = {{
    "status": "imported",
    "operator_result": str(result),
    "objects_before": before,
    "objects_after": after,
    "objects_added": after - before,
    "buildings_found": len(buildings_list),
}}
""", timeout=300)


def create_terrain(blend_file, size=64.0, subdivisions=1, save=True, blender_path=None):
    """Create a terrain plane with subdivisions and vertex colors.

    The plane is snapped to the 8-unit patch grid (engine requirement)
    and initialized with a MaterialID vertex-color layer (grass default).

    Args:
        blend_file: Path to .blend file.
        size: Terrain size (rounds to 8-unit patch grid, min 8).
        subdivisions: Subdivisions per unit (1-4).
        save: Save .blend file after creation.
        blender_path: Optional path to Blender executable.

    Returns:
        BlenderBridge result dict with terrain dimensions.
    """
    save_code = f"bpy.ops.wm.save_as_mainfile(filepath={blend_file!r})" if save else ""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import addon_utils
addon_utils.enable({_ADDON_MODULE!r}, default_set=True)
import bpy

result = bpy.ops.asciicker.create_terrain(size={size}, subdivisions={subdivisions})
if 'FINISHED' not in result:
    raise RuntimeError("create_terrain returned " + str(result))

terrain = bpy.context.active_object
verts = len(terrain.data.vertices) if terrain else 0
{save_code}
_data = {{
    "status": "created",
    "operator_result": str(result),
    "terrain_name": terrain.name if terrain else None,
    "vertex_count": verts,
    "size": {size},
    "subdivisions": {subdivisions},
}}
""", timeout=120)


def scan_osm_scene(blend_file, blender_path=None):
    """Scan a blend file for blosm OSM objects and categorize them.

    Args:
        blend_file: Path to .blend file containing blosm objects.
        blender_path: Optional path to Blender executable.

    Returns:
        BlenderBridge result dict with category counts.
    """
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import addon_utils
addon_utils.enable({_ADDON_MODULE!r}, default_set=True)
import bpy

result = bpy.ops.asciicker.scan_osm_scene()
if 'FINISHED' not in result:
    raise RuntimeError("scan_osm_scene operator returned " + str(result))

props = bpy.context.scene.asciicker_osm_painter
_data = {{
    "total": props.scan_total,
    "grass": props.scan_grass,
    "road": props.scan_road,
    "concrete": props.scan_concrete,
    "residential": props.scan_residential,
    "default": props.scan_default,
}}
""")


def paint_terrain_from_osm(
    blend_file,
    scale=1.0,
    offset_x=0.0,
    offset_y=0.0,
    grass_mat=1,
    road_mat=7,
    concrete_mat=3,
    residential_mat=2,
    default_mat=1,
    simplify_tolerance=1.0,
    cmd_file="/tmp/asciiid_cmd",
    blender_path=None,
):
    """Paint terrain materials from OSM data in a blend file.

    Sets scene properties, invokes the paint operator, and returns
    the operator result. Note: the operator writes PAINT_TERRAIN_POLY
    commands to cmd_file on disk (not via MCP stdin directly).

    Args:
        blend_file: Path to .blend file containing blosm + a3d data.
        scale: blosm-to-engine scale factor.
        offset_x: Calibration offset X.
        offset_y: Calibration offset Y.
        grass_mat: Material ID for grass (0-255).
        road_mat: Material ID for roads (0-255).
        concrete_mat: Material ID for concrete (0-255).
        residential_mat: Material ID for residential (0-255).
        default_mat: Material ID for default/uncategorized (0-255).
        simplify_tolerance: Douglas-Peucker epsilon for vertex reduction.
        cmd_file: Path to MCP relay command file.
        blender_path: Optional path to Blender executable.

    Returns:
        BlenderBridge result dict with operator status and cmd_file path.
    """
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import addon_utils
addon_utils.enable({_ADDON_MODULE!r}, default_set=True)
import bpy

props = bpy.context.scene.asciicker_osm_painter
props.scale = {scale}
props.offset_x = {offset_x}
props.offset_y = {offset_y}
props.grass_mat = {grass_mat}
props.road_mat = {road_mat}
props.concrete_mat = {concrete_mat}
props.residential_mat = {residential_mat}
props.default_mat = {default_mat}
props.simplify_tolerance = {simplify_tolerance}
props.cmd_file = {cmd_file!r}

result = bpy.ops.asciicker.paint_terrain_from_osm()
if 'FINISHED' not in result:
    raise RuntimeError("paint_terrain_from_osm operator returned " + str(result))

_data = {{
    "status": "painted",
    "cmd_file": {cmd_file!r},
    "operator_result": str(result),
}}
""")


def extrude_buildings(blend_file, save=True, blender_path=None):
    """Extrude flat blosm building footprints to 3D.

    Skips buildings that already have Z extent > 0.5.
    Height estimated from building:levels tag or footprint area heuristic.

    Args:
        blend_file: Path to .blend file containing blosm buildings.
        save: Save .blend file after extrusion.
        blender_path: Optional path to Blender executable.

    Returns:
        BlenderBridge result dict with extruded/skipped counts.
    """
    save_code = f"bpy.ops.wm.save_as_mainfile(filepath={blend_file!r})" if save else ""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import addon_utils
addon_utils.enable({_ADDON_MODULE!r}, default_set=True)
import bpy
from io_asciicker.tools.osm_pipeline import _get_blosm_buildings

result = bpy.ops.asciicker.osm_extrude_buildings()
# Count buildings for reporting
buildings = _get_blosm_buildings(bpy.context)
extruded_3d = sum(1 for o in buildings
    if max(c.z for c in [o.matrix_world @ v.co for v in o.data.vertices])
     - min(c.z for c in [o.matrix_world @ v.co for v in o.data.vertices]) > 0.5)
{save_code}
_data = {{
    "status": str(result),
    "total_buildings": len(buildings),
    "buildings_3d": extruded_3d,
}}
""", timeout=300)


def paint_buildings(blend_file, subdivision_level=3, save=True, blender_path=None):
    """Subdivide and paint windows on all blosm buildings.

    Applies SUBSURF SIMPLE modifier then vertex-color window painting.

    Args:
        blend_file: Path to .blend file with extruded buildings.
        subdivision_level: Subdivision iterations (1-4).
        save: Save .blend file after painting.
        blender_path: Optional path to Blender executable.

    Returns:
        BlenderBridge result dict with painted count.
    """
    save_code = f"bpy.ops.wm.save_as_mainfile(filepath={blend_file!r})" if save else ""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import addon_utils
addon_utils.enable({_ADDON_MODULE!r}, default_set=True)
import bpy
from io_asciicker.tools.osm_pipeline import _get_blosm_buildings

# Set subdivision level on the building painter props
bp = bpy.context.scene.asciicker_building_painter
bp.subdivision_level = {subdivision_level}

result = bpy.ops.asciicker.osm_paint_buildings()
buildings = _get_blosm_buildings(bpy.context)
{save_code}
_data = {{
    "status": str(result),
    "total_buildings": len(buildings),
}}
""", timeout=600)


def prepare_meshes(blend_file, meshes_dir, target_faces=None, save=True, blender_path=None):
    """Inventory, reduce, and export new meshes as AKM files.

    Checks each mesh object against existing AKMs in meshes_dir.
    New meshes are decimated to target_faces and exported.

    Args:
        blend_file: Path to .blend file with prepared buildings.
        meshes_dir: Path to engine assets/meshes/ directory for AKM files.
        target_faces: Max faces per mesh (default: 500).
        save: Save .blend file after preparation.
        blender_path: Optional path to Blender executable.

    Returns:
        BlenderBridge result dict with exported/existing/failed counts.
    """
    if target_faces is None:
        target_faces = DEFAULT_TARGET_FACES
    save_code = f"bpy.ops.wm.save_as_mainfile(filepath={blend_file!r})" if save else ""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import addon_utils
addon_utils.enable({_ADDON_MODULE!r}, default_set=True)
import bpy, os
from io_asciicker.tools.osm_pipeline import _get_blosm_buildings

meshes_dir = {meshes_dir!r}
target_faces = {target_faces}

# Inventory existing AKMs
existing_akms = set()
if os.path.isdir(meshes_dir):
    for f in os.listdir(meshes_dir):
        if f.lower().endswith('.akm'):
            existing_akms.add(f.lower())

# Find building objects using the authoritative pipeline membership helper.
mesh_objects = _get_blosm_buildings(bpy.context)

if not mesh_objects:
    raise RuntimeError("No mesh objects to prepare")

if bpy.context.mode != 'OBJECT':
    bpy.ops.object.mode_set(mode='OBJECT')

old_active = bpy.context.view_layer.objects.active
exported_new = 0
reused_existing = 0
failed = 0

for obj in mesh_objects:
    akm_filename = obj.name + '.akm'
    if akm_filename.lower() in existing_akms:
        reused_existing += 1
        continue

    # Select + activate for modifier operations
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    # Decimate if over target
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

    # Export as AKM
    out_path = os.path.join(meshes_dir, akm_filename)
    try:
        bpy.ops.export_mesh.akm(
            filepath=out_path,
            use_selection=True,
            axis_forward='Y',
            axis_up='Z',
        )
        existing_akms.add(akm_filename.lower())
        exported_new += 1
    except Exception as e:
        print(f"Failed to export {{akm_filename}}: {{e}}")
        failed += 1

bpy.context.view_layer.objects.active = old_active
{save_code}
akm_count = len([f for f in os.listdir(meshes_dir) if f.lower().endswith('.akm')]) if os.path.isdir(meshes_dir) else 0
_data = {{
    "status": "prepared",
    "meshes_dir": meshes_dir,
    "target_faces": target_faces,
    "exported_new": exported_new,
    "reused_existing": reused_existing,
    "failed": failed,
    "total_buildings": exported_new + reused_existing + failed,
    "total_akms_in_dir": akm_count,
}}
""", timeout=600)


def clean_scene(blend_file, meshes_dir=None, save=True, blender_path=None):
    """Delete non-building blosm objects (roads, vegetation, empties).

    Preserves terrain, camera, light, building objects, and any mesh objects
    whose .akm file already exists in meshes_dir (inventory preservation).

    Args:
        blend_file: Path to .blend file to clean.
        meshes_dir: Optional path to engine assets/meshes/ directory. Objects with
            matching .akm files are preserved.
        save: Save .blend file after cleaning.
        blender_path: Optional path to Blender executable.

    Returns:
        BlenderBridge result dict with deleted count.
    """
    save_code = f"bpy.ops.wm.save_as_mainfile(filepath={blend_file!r})" if save else ""
    meshes_dir_repr = repr(meshes_dir) if meshes_dir else "None"
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import addon_utils
addon_utils.enable({_ADDON_MODULE!r}, default_set=True)
import bpy, os

meshes_dir = {meshes_dir_repr}

# Build set of inventory AKM names for preservation check
inventory_akms = set()
if meshes_dir and os.path.isdir(meshes_dir):
    for f in os.listdir(meshes_dir):
        if f.lower().endswith('.akm'):
            inventory_akms.add(f.lower())

from io_asciicker.tools.osm_pipeline import _is_pipeline_building, _is_pipeline_utility_object

before = len(bpy.context.scene.objects)
to_delete = []
preserved_inventory = 0

for obj in bpy.context.scene.objects:
    # Always keep terrain, camera, light
    if _is_pipeline_utility_object(obj):
        continue
    # Always keep authoritative pipeline buildings
    if _is_pipeline_building(obj):
        continue
    # Preserve mesh objects whose .akm exists in inventory
    if obj.type == 'MESH' and inventory_akms:
        akm_name = (obj.name + '.akm').lower()
        if akm_name in inventory_akms:
            preserved_inventory += 1
            continue
    # Check if it's a blosm object that should be cleaned
    is_blosm = (obj.get('highway') or obj.get('landuse') or obj.get('natural')
                 or obj.get('amenity') or obj.get('landcover')
                 or obj.get('waterway') or obj.get('leisure'))
    is_blosm_parent = (obj.type == 'EMPTY' and obj.name.startswith('None_'))
    is_blosm_named = obj.name.startswith('None_') and obj.type in ('MESH', 'CURVE')
    if is_blosm or is_blosm_parent or is_blosm_named:
        to_delete.append(obj)

if to_delete:
    bpy.ops.object.select_all(action='DESELECT')
    for obj in to_delete:
        obj.select_set(True)
    bpy.ops.object.delete()

after = len(bpy.context.scene.objects)
{save_code}
_data = {{
    "status": "cleaned",
    "objects_before": before,
    "objects_after": after,
    "deleted": len(to_delete),
    "preserved_inventory": preserved_inventory,
}}
""", timeout=120)


def export_a3d(blend_file, output_path, blender_path=None):
    """Export the current scene as an A3D map file.

    Args:
        blend_file: Path to .blend file to export from.
        output_path: Destination path for .a3d file.
        blender_path: Optional path to Blender executable.

    Returns:
        BlenderBridge result dict with file path and size.
    """
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import addon_utils
addon_utils.enable({_ADDON_MODULE!r}, default_set=True)
import bpy, os

os.makedirs(os.path.dirname({output_path!r}) or '.', exist_ok=True)
bpy.ops.export_scene.a3d(filepath={output_path!r})
_data = {{
    "status": "exported",
    "path": {output_path!r},
    "exists": os.path.isfile({output_path!r}),
    "size_bytes": os.path.getsize({output_path!r}) if os.path.isfile({output_path!r}) else 0,
}}
""", timeout=120)


def full_pipeline(
    blend_file,
    meshes_dir,
    a3d_output,
    target_faces=None,
    subdivision_level=3,
    content_scale=3.0,
    building_height_mult=5.0,
    road_width_mult=1.0,
    fixtures_dir=None,
    fixture_specs_output=None,
    building_specs_output=None,
    terrain_metadata_output=None,
    save=True,
    blender_path=None,
):
    """Run the complete OSM-to-engine pipeline in one Blender session.

    Corrected step order: auto-terrain (+ reposition blosm) -> paint terrain ->
    extrude buildings -> paint buildings -> separate buildings -> prepare meshes ->
    clean scene -> export A3D.

    Uses inline per-step calls for the terrain-sizing/content-scale path, while
    reusing the addon's late fixture-placement helper for the final OSM-backed
    instance pass.

    Args:
        blend_file: Path to .blend file with blosm buildings + terrain.
        meshes_dir: Path to engine assets/meshes/ directory.
        a3d_output: Output path for .a3d map file.
        target_faces: Max faces per mesh (default: 500).
        subdivision_level: Building subdivision level (1-4).
        fixtures_dir: Path to the canonical fixture AKM directory.
        fixture_specs_output: Optional JSON path for deferred fixture instances.
        building_specs_output: Optional JSON path for deferred building instances.
        terrain_metadata_output: Optional JSON path for terrain/content bounds metadata.
        save: Save .blend file after pipeline.
        blender_path: Optional path to Blender executable.

    Returns:
        BlenderBridge result dict with per-step status.
    """
    if target_faces is None:
        target_faces = DEFAULT_TARGET_FACES
    save_code = f"bpy.ops.wm.save_as_mainfile(filepath={blend_file!r})" if save else ""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import addon_utils
addon_utils.enable({_ADDON_MODULE!r}, default_set=True)
import bpy, os, math, json
from mathutils import Vector

meshes_dir = {meshes_dir!r}
a3d_output = {a3d_output!r}
target_faces = {target_faces}
content_scale = {content_scale}
building_height_mult = {building_height_mult}
road_width_mult = {road_width_mult}
subdivision_level = {subdivision_level}
fixture_source_dir = {fixtures_dir!r}
fixture_specs_output = {fixture_specs_output!r}
building_specs_output = {building_specs_output!r}
terrain_metadata_output = {terrain_metadata_output!r}

steps_done = []
steps_failed = []
bpy.context.scene['osm_content_scale'] = content_scale
content_bounds = None
terrain_bounds = None

# ── Step 1: Auto-terrain + reposition blosm ──────────────────────
# Compute blosm extent
min_x = min_y = float('inf')
max_x = max_y = float('-inf')
found_blosm = False
for obj in bpy.context.scene.objects:
    if obj.type not in ('MESH', 'CURVE', 'EMPTY'):
        continue
    is_blosm = (obj.get('building') or obj.get('highway') or obj.get('landuse')
                 or obj.get('natural') or obj.get('amenity') or obj.get('landcover')
                 or obj.get('waterway') or obj.get('leisure'))
    is_parent = (obj.type == 'EMPTY' and obj.name.startswith('None_'))
    is_blosm_named = obj.name.startswith('None_') and obj.type in ('MESH', 'CURVE')
    if not is_blosm and not is_parent and not is_blosm_named:
        continue
    found_blosm = True
    if obj.type == 'EMPTY':
        co = obj.matrix_world.translation
        min_x, min_y = min(min_x, co.x), min(min_y, co.y)
        max_x, max_y = max(max_x, co.x), max(max_y, co.y)
    else:
        for corner in obj.bound_box:
            co = obj.matrix_world @ Vector(corner)
            min_x, min_y = min(min_x, co[0]), min(min_y, co[1])
            max_x, max_y = max(max_x, co[0]), max(max_y, co[1])

if not found_blosm:
    raise RuntimeError("No blosm objects found in scene")

# Scale blosm content for better visibility vs player sprites.
# blosm uses ~1.67 Blender units per real meter. With content_scale=3,
# a 50m building becomes 250 terrain cells instead of 83.
if content_scale != 1.0:
    bpy.ops.object.select_all(action='DESELECT')
    for obj in list(bpy.context.scene.objects):
        if obj.type not in ('MESH', 'CURVE', 'EMPTY'):
            continue
        is_blosm = (obj.get('building') or obj.get('highway') or obj.get('landuse')
                     or obj.get('natural') or obj.get('amenity') or obj.get('landcover')
                     or obj.get('waterway') or obj.get('leisure'))
        is_parent = (obj.type == 'EMPTY' and obj.name.startswith('None_'))
        is_blosm_named = obj.name.startswith('None_') and obj.type in ('MESH', 'CURVE')
        if is_blosm or is_parent or is_blosm_named:
            # Only scale root objects — children inherit
            if obj.parent and (obj.parent.name.startswith('None_') or obj.parent.get('building')):
                continue
            obj.scale *= content_scale
    # Apply scale to all blosm meshes
    for obj in list(bpy.context.scene.objects):
        if obj.type == 'MESH' and (obj.name.startswith('None_') or obj.get('building')):
            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            try:
                bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
            except Exception:
                pass
    # Recompute extent after scaling
    min_x = min_y = float('inf')
    max_x = max_y = float('-inf')
    for obj in bpy.context.scene.objects:
        if obj.type not in ('MESH', 'CURVE', 'EMPTY'):
            continue
        is_blosm = (obj.get('building') or obj.get('highway') or obj.get('landuse')
                     or obj.get('natural') or obj.get('amenity') or obj.get('landcover')
                     or obj.get('waterway') or obj.get('leisure'))
        is_parent = (obj.type == 'EMPTY' and obj.name.startswith('None_'))
        is_blosm_named = obj.name.startswith('None_') and obj.type in ('MESH', 'CURVE')
        if not is_blosm and not is_parent and not is_blosm_named:
            continue
        if obj.type == 'EMPTY':
            co = obj.matrix_world.translation
            min_x, min_y = min(min_x, co.x), min(min_y, co.y)
            max_x, max_y = max(max_x, co.x), max(max_y, co.y)
        else:
            for corner in obj.bound_box:
                co = obj.matrix_world @ Vector(corner)
                min_x, min_y = min(min_x, co[0]), min(min_y, co[1])
                max_x, max_y = max(max_x, co[0]), max(max_y, co[1])
    print(f"[PIPELINE] After {{content_scale}}x scale: ({{min_x:.1f}},{{min_y:.1f}}) to ({{max_x:.1f}},{{max_y:.1f}})")

try:
    from io_asciicker.tools.osm_pipeline import _auto_terrain_from_blosm
    terrain = _auto_terrain_from_blosm(bpy.context)
    if terrain:
        terrain_size = max(terrain.dimensions.x, terrain.dimensions.y)
        print(f"[PIPELINE] terrain_size: {{terrain_size}}")
        steps_done.append("auto_terrain")
    else:
        terrain_size = 0
        steps_failed.append("auto_terrain")
except Exception as e:
    terrain_size = 0
    print(f"auto_terrain failed: {{e}}")
    steps_failed.append("auto_terrain")

# ── Step 2: Paint terrain (BEFORE extrude — footprints are 2D) ───
try:
    if road_width_mult != 1.0:
        from io_asciicker.tools import osm_terrain_painter
        osm_terrain_painter.ROAD_HALF_WIDTHS = {{
            key: width * road_width_mult
            for key, width in osm_terrain_painter.ROAD_HALF_WIDTHS.items()
        }}
        print(f"[PIPELINE] road_width_mult={{road_width_mult}}")
    result = bpy.ops.asciicker.paint_terrain_direct()
    if 'FINISHED' in result:
        steps_done.append("paint_terrain")
    else:
        steps_failed.append("paint_terrain")
except Exception as e:
    print(f"paint_terrain failed: {{e}}")
    steps_failed.append("paint_terrain")

# ── Step 2b: Separate + rename buildings ────────────────────────
# [RC-16 FIX] MUST run BEFORE extrude/paint. In file mode and 2D mode,
# blosm groups buildings into merged None_buildings meshes. Separate by
# loose parts, restore OSM names where centroid matching succeeds, then
# assign Building_NNN only to unnamed leftovers.
try:
    from io_asciicker.tools.osm_pipeline import (
        PIPELINE_BUILDING_CANDIDATE_PROP,
        _delete_marked_duplicate_buildings,
        _get_osm_filepath,
        _prune_duplicate_named_suffix_buildings,
        _rename_separated_buildings_from_osm,
        _set_pipeline_building_marker,
        _set_object_origins_to_bounds,
    )
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    # Separate any merged None_buildings objects
    grouped = [obj for obj in bpy.context.scene.objects
               if obj.type == 'MESH' and obj.name.lower().startswith('none_buildings')]
    total_separated = 0
    for buildings_obj in grouped:
        try:
            bpy.ops.object.select_all(action='DESELECT')
            buildings_obj.select_set(True)
            bpy.context.view_layer.objects.active = buildings_obj
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.mesh.separate(type='LOOSE')
            bpy.ops.object.mode_set(mode='OBJECT')
            total_separated += 1
        except Exception as sep_e:
            if bpy.context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            print(f"Separate failed for {{buildings_obj.name}}: {{sep_e}}")

    # Collect ALL building objects (3D: have 'building' prop, 2D/file: none_buildings*)
    building_list = [o for o in bpy.context.scene.objects
                     if o.type == 'MESH' and (o.get('building')
                         or o.name.lower().startswith('none_buildings'))]

    # Two-pass rename to avoid Blender auto-suffix collision.
    # Pass 1: assign unique temp names (no collision possible).
    for i, obj in enumerate(sorted(building_list, key=lambda o: o.name), start=1):
        tmp = f"_bldg_tmp_{{i:03d}}"
        obj.name = tmp
        obj.data.name = tmp
        obj[PIPELINE_BUILDING_CANDIDATE_PROP] = 1
    # Pass 2: re-enumerate sorted temps → final Building_NNN names.
    # Use enumerate instead of parsing name[-3:] — Blender may have
    # appended .001 suffixes if temps collided with existing objects.
    tmp_objs = sorted(
        [o for o in bpy.context.scene.objects
         if o.type == 'MESH' and o.name.startswith('_bldg_tmp_')],
        key=lambda o: o.name)
    osm_fp = _get_osm_filepath(bpy.context)
    renamed_from_osm = _rename_separated_buildings_from_osm(bpy.context, tmp_objs, osm_fp)
    unnamed = [o for o in tmp_objs if o.name.startswith('_bldg_tmp_')]
    for i, obj in enumerate(unnamed, start=1):
        final = f"Building_{{i:03d}}"
        obj.name = final
        obj.data.name = final
    pruned_duplicates = _delete_marked_duplicate_buildings(bpy.context)
    pruned_duplicates += _prune_duplicate_named_suffix_buildings(bpy.context)

    # Normalize origin handling for both named and generic buildings so
    # local mesh geometry and instance placement stay on one contract.
    bldg_final = [
        obj for obj in bpy.context.scene.objects
        if obj.type == 'MESH' and obj.get(PIPELINE_BUILDING_CANDIDATE_PROP)
    ]
    for obj in bldg_final:
        obj.pop(PIPELINE_BUILDING_CANDIDATE_PROP, None)
    _set_pipeline_building_marker(bldg_final)
    _set_object_origins_to_bounds(bpy.context, bldg_final)

    steps_done.append(f"separate({{len(bldg_final)}})")
    if pruned_duplicates > 0:
        steps_done.append(f"prune_duplicate_buildings({{pruned_duplicates}})")
    if renamed_from_osm > 0:
        steps_done.append(f"rename_osm({{renamed_from_osm}})")
    print(f"[PIPELINE] Separated/renamed {{len(bldg_final)}} buildings")
except Exception as e:
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    print(f"separate/rename failed: {{e}}")
    steps_failed.append("separate")

# ── Step 3: Clean-extrude buildings ─────────────────────────────
# [RC-29 FIX] Extract footprint boundary at min-Z, replace mesh with
# clean n-gon, extrude to controlled height. Runs AFTER separation so
# each building is individual. Object name/properties preserved.
try:
    import bmesh as _bm
    from io_asciicker.tools.osm_pipeline import _ground_world_z, _localize_world_footprint

    def _estimate_height(obj):
        \"\"\"Returns (height, is_measured). is_measured=True means height came
        from the actual 3D mesh extent — don't apply height_mult again.\"\"\"
        levels = obj.get('building:levels')
        if levels:
            try: return float(levels) * 3.0, False
            except (ValueError, TypeError): pass
        coords = [obj.matrix_world @ v.co for v in obj.data.vertices]
        if coords:
            z_extent = max(c.z for c in coords) - min(c.z for c in coords)
            if z_extent > 0.5:
                return z_extent, True
        return 9.0, False

    def _footprint_verts(obj):
        \"\"\"Get ordered footprint vertices at the building's ground level.

        Finds vertices at min-Z, identifies BOUNDARY edges (edges with
        exactly 1 face among ground-level faces), then chains boundary
        vertices into an ordered polygon.
        Returns list of (x, y) in world space, or None.
        \"\"\"
        mesh = obj.data
        mat = obj.matrix_world
        if len(mesh.vertices) < 3:
            return None

        # Find ground-level vertices
        world_verts = [(mat @ v.co, v.index) for v in mesh.vertices]
        z_min = min(wv[0].z for wv in world_verts)
        z_tol = 0.1
        ground = {{idx: (co.x, co.y) for co, idx in world_verts if co.z < z_min + z_tol}}
        if len(ground) < 3:
            return None

        # [BUG-4 FIX] Use bmesh to find BOUNDARY edges only — edges with
        # exactly 1 face among ground-level faces. This avoids interior
        # triangulation edges that would divert the chain walker.
        bm = _bm.new()
        bm.from_mesh(mesh)
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()

        ground_set = set(ground.keys())
        # Count how many ground-level faces each edge borders
        boundary_edges = []
        for e in bm.edges:
            v0, v1 = e.verts[0].index, e.verts[1].index
            if v0 not in ground_set or v1 not in ground_set:
                continue
            # An edge is boundary if it has exactly 1 adjacent ground-level face
            # (== 1, not <= 1: free edges with 0 faces are not true boundary)
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

        # Build adjacency from boundary edges only
        adj = {{}}
        for a, b in boundary_edges:
            adj.setdefault(a, []).append(b)
            adj.setdefault(b, []).append(a)

        # Chain into ordered loop
        start = next(iter(adj))
        ordered = [start]
        visited = {{start}}
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

    def _simplify_collinear(footprint):
        simplified = [footprint[0]]
        for i in range(1, len(footprint)):
            p = simplified[-1]
            c = footprint[i]
            n = footprint[(i + 1) % len(footprint)]
            cross = (c[0]-p[0])*(n[1]-p[1]) - (c[1]-p[1])*(n[0]-p[0])
            if abs(cross) > 0.1:
                simplified.append(c)
        if len(simplified) >= 3:
            p, c, n = simplified[-1], simplified[0], simplified[1]
            cross = (c[0]-p[0])*(n[1]-p[1]) - (c[1]-p[1])*(n[0]-p[0])
            if abs(cross) <= 0.1:
                simplified.pop(0)
        return simplified

    def _simplify_osm_building_footprint(footprint):
        # FL-2534: ESS showed that exact-collinear cleanup preserves OSM/BLOSM
        # micro-notches, then terrain bake turns them into jagged building
        # imprints. Keep the footprint owner here and simplify bounded import
        # noise before creating the clean extrusion mesh.
        if len(footprint) < 8:
            return _simplify_collinear(footprint)
        xs = [p[0] for p in footprint]
        ys = [p[1] for p in footprint]
        diag = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        epsilon = max(2.0, min(20.0, diag * 0.03))
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        start = max(range(len(footprint)), key=lambda i: math.hypot(footprint[i][0] - cx, footprint[i][1] - cy))
        rotated = footprint[start:] + footprint[:start] + [footprint[start]]
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
                    other not in {{prev_idx, idx, next_idx}}
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

    buildings = [o for o in bpy.context.scene.objects
                 if o.type == 'MESH' and o.get('asciicker_pipeline_building')]
    extruded = 0
    skipped = 0
    for obj in buildings:
        # Get height BEFORE replacing mesh. is_measured=True means the
        # height came from actual 3D extent — don't multiply again.
        raw_height, is_measured = _estimate_height(obj)
        height = raw_height if is_measured else raw_height * building_height_mult

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

        # FL-2534: ESS is a concave L-shaped footprint. Creating one bmesh
        # n-gon lets Blender/export triangulate the roof incorrectly, then the
        # terrain bake faithfully stamps that wrong triangle soup. Tessellate
        # the ordered footprint explicitly so the AKM roof mask remains the
        # source OSM polygon.
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

    steps_done.append(f"extrude({{extruded}}/{{extruded+skipped}})")
    print(f"[PIPELINE] Clean-extruded {{extruded}} buildings, skipped {{skipped}} (height_mult={{building_height_mult}})")
except Exception as e:
    print(f"extrude failed: {{e}}")
    import traceback; traceback.print_exc()
    steps_failed.append("extrude")

# ── Step 4: Paint buildings (subdivide + windows) ─────────────────
try:
    if building_specs_output:
        # FL-2534: baked/deferred OSM buildings are terrain input, not final
        # visible facade meshes. Subdividing/window-painting them before AKM
        # export feeds thousands of decorative triangles into
        # BAKE_MESH_TO_TERRAIN and makes ESS-like buildings jagged. Keep the
        # clean footprint extrusion as the single bake geometry owner.
        steps_done.append("skip_paint_buildings_for_bake")
    else:
        bp = bpy.context.scene.asciicker_building_painter
        bp.subdivision_level = subdivision_level
        result = bpy.ops.asciicker.osm_paint_buildings()
        if 'FINISHED' in result:
            steps_done.append("paint_buildings")
        else:
            steps_failed.append("paint_buildings")
except Exception as e:
    print(f"paint_buildings failed: {{e}}")
    steps_failed.append("paint_buildings")

# ── Step 5: (MOVED) Separate + rename now in Step 2b ──────────
# Separation and rename were moved before extrude (RC-16 fix).
# Fixture placement is deferred until after cleanup so the heavy mesh bake
# path runs first and point fixtures come in from the OSM file at the end.

# ── Step 6: Prepare meshes (inventory check + decimate + export AKMs) ──
try:
    os.makedirs(meshes_dir, exist_ok=True)
    existing_akms = set()
    for f in os.listdir(meshes_dir):
        if f.lower().endswith('.akm'):
            existing_akms.add(f.lower())

    from io_asciicker.tools.osm_pipeline import _is_pipeline_utility_object
    # [RC-26 FIX] Exclude ground-level blosm meshes (vegetation, pedestrian
    # areas, water) — they render as unpainted noise blobs in the engine.
    # Also exclude fixture placeholders — engine loads those from assets/meshes/fixtures/.
    ground_prefixes = ('none_vegetation', 'none_areas', 'none_water',
                       'none_natural', 'none_landuse', 'none_highway')
    fixture_prefix = 'fixture_'
    building_objects = [o for o in bpy.context.scene.objects
                        if o.type == 'MESH'
                        and not _is_pipeline_utility_object(o)
                        and not any(o.name.lower().startswith(p) for p in ground_prefixes)
                        and not o.name.lower().startswith(fixture_prefix)]

    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    exported = 0
    reused = 0
    for obj in building_objects:
        # [FL-2595 FIX] AKM filename uses osm_geom_id (e.g., way_12345.akm) when
        # available, falling back to obj.name for compatibility. This matches the
        # a3d_mesh_ref that _rename_separated_buildings_from_osm stamps on OSM
        # buildings, so extract_instances() resolves the correct mesh reference.
        osm_geom_id = obj.get('osm_geom_id', '').strip()
        akm_name = osm_geom_id.replace('/', '_') + '.akm' if osm_geom_id else obj.name + '.akm'
        if akm_name.lower() in existing_akms:
            reused += 1
            continue
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        # [RC-29] No decimation — clean-extrude produces minimal faces.
        # Only decimate if an object somehow has excessive faces (>2000).
        current_faces = len(obj.data.polygons)
        if current_faces > 2000:
            ratio = 500 / current_faces
            dec = obj.modifiers.new(name="OSM_Dec", type='DECIMATE')
            dec.decimate_type = 'COLLAPSE'
            dec.ratio = ratio
            try:
                bpy.ops.object.modifier_apply(modifier=dec.name)
            except Exception:
                pass
        try:
            bpy.ops.export_mesh.akm(
                filepath=os.path.join(meshes_dir, akm_name),
                use_selection=True, axis_forward='Y', axis_up='Z')
            existing_akms.add(akm_name.lower())
            exported += 1
        except Exception as e:
            print(f"AKM export failed for {{obj.name}}: {{e}}")

    steps_done.append("prepare_meshes")
except Exception as e:
    print(f"prepare_meshes failed: {{e}}")
    steps_failed.append("prepare_meshes")

# ── Step 7: Clean scene ───────────────────────────────────────────
try:
    # Build inventory set for preservation
    inventory_akms = set()
    if os.path.isdir(meshes_dir):
        for f in os.listdir(meshes_dir):
            if f.lower().endswith('.akm'):
                inventory_akms.add(f.lower())

    from io_asciicker.tools.osm_pipeline import _is_pipeline_building
    to_delete = []
    for obj in bpy.context.scene.objects:
        if _is_pipeline_utility_object(obj):
            continue
        if _is_pipeline_building(obj):
            continue
        if obj.type == 'MESH' and inventory_akms:
            if (obj.name + '.akm').lower() in inventory_akms:
                continue
        is_blosm = (obj.get('highway') or obj.get('landuse') or obj.get('natural')
                     or obj.get('amenity') or obj.get('landcover')
                     or obj.get('waterway') or obj.get('leisure'))
        is_blosm_parent = (obj.type == 'EMPTY' and obj.name.startswith('None_'))
        is_blosm_named = obj.name.startswith('None_') and obj.type in ('MESH', 'CURVE')
        if is_blosm or is_blosm_parent or is_blosm_named:
            to_delete.append(obj)

    if to_delete:
        bpy.ops.object.select_all(action='DESELECT')
        for obj in to_delete:
            obj.select_set(True)
        bpy.ops.object.delete()
    steps_done.append("clean")
except Exception as e:
    print(f"clean failed: {{e}}")
    steps_failed.append("clean")

# ── Step 7b: Place fixtures last from the canonical addon helper ─────────
try:
    fixture_count = 0
    fixture_skipped = 0
    if fixture_source_dir and os.path.isdir(fixture_source_dir):
        from io_asciicker.tools.osm_pipeline import (
            _export_fixture_instance_specs_from_osm,
            _get_osm_filepath,
            _place_point_fixtures,
            _recover_terrain_shift_from_osm,
        )
        if bpy.context.scene.get('terrain_shift_x') is None:
            osm_fp = _get_osm_filepath(bpy.context)
            if osm_fp:
                _recover_terrain_shift_from_osm(bpy.context, osm_fp)
        if fixture_specs_output:
            fixture_count, fixture_skipped, _ = _export_fixture_instance_specs_from_osm(
                bpy.context, fixture_source_dir, fixture_specs_output)
        else:
            fixture_count, fixture_skipped, _ = _place_point_fixtures(bpy.context, fixture_source_dir)
    if fixture_specs_output:
        if fixture_count > 0 or fixture_skipped == 0:
            steps_done.append(f"fixtures_deferred({{fixture_count}})")
        else:
            steps_failed.append(f"fixtures_deferred(0/{{fixture_count + fixture_skipped}} placed)")
    elif fixture_count > 0 or fixture_skipped == 0:
        steps_done.append(f"fixtures({{fixture_count}})")
    else:
        steps_failed.append(f"fixtures(0/{{fixture_count + fixture_skipped}} placed)")
except Exception as e:
    print(f"fixtures failed: {{e}}")
    import traceback; traceback.print_exc()
    steps_failed.append("fixtures")

# ── Step 7c: Defer building instances for baked runner ownership ─────────
try:
    if building_specs_output:
        os.makedirs(os.path.dirname(building_specs_output), exist_ok=True)
        if terrain_metadata_output:
            os.makedirs(os.path.dirname(terrain_metadata_output), exist_ok=True)

        if os.path.isdir(meshes_dir):
            inventory_akms = {{
                f.lower() for f in os.listdir(meshes_dir)
                if f.lower().endswith('.akm')
            }}
        else:
            inventory_akms = set()

        deferred_buildings = [
            obj for obj in bpy.context.scene.objects
            if obj.type == 'MESH'
            and not _is_pipeline_utility_object(obj)
            # [FL-2595 FIX] AKM filenames use osm_geom_id (e.g., way_12345.akm)
            # not obj.name (e.g., Chemistry.akm). Check both for compatibility.
            and (
                (
                    obj.get('osm_geom_id', '').replace('/', '_') + '.akm'
                    if obj.get('osm_geom_id', '')
                    else obj.name + '.akm'
                ).lower()
                in inventory_akms
            )
        ]

        from io_asciicker.scene.a3d_format import HEIGHT_SCALE, TERRAIN_EXPORT_BASELINE
        from io_asciicker.scene.export_a3d import extract_instances

        instances = extract_instances(
            deferred_buildings,
            z_baseline=TERRAIN_EXPORT_BASELINE,
        )
        payload = []
        for obj, inst in zip(deferred_buildings, instances):
            source_footprint = []
            bake_footprint = []
            for prop_name, target in (
                ('osm_footprint_xy_json', source_footprint),
                ('osm_bake_footprint_xy_json', bake_footprint),
            ):
                raw = obj.get(prop_name, '')
                if raw:
                    try:
                        parsed = json.loads(raw)
                        target.extend([[float(p[0]), float(p[1])] for p in parsed if len(p) >= 2])
                    except Exception:
                        pass
            bake_height = int(obj.get('osm_bake_height', TERRAIN_EXPORT_BASELINE + HEIGHT_SCALE))
            payload.append({{
                "variant": inst.variant,
                "mesh_name": inst.mesh_name,
                "inst_name": inst.inst_name,
                "transform": list(inst.transform),
                "flags": inst.flags,
                "story_id": inst.story_id,
                "source": obj.get('osm_geom_id', ''),
                "footprint": source_footprint,
                "bake_footprint": bake_footprint or source_footprint,
                "bake_height": bake_height,
                "bake_material_id": 5,
            }})
        with open(building_specs_output, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)

        if deferred_buildings:
            min_x = min((obj.matrix_world @ Vector(corner)).x for obj in deferred_buildings for corner in obj.bound_box)
            min_y = min((obj.matrix_world @ Vector(corner)).y for obj in deferred_buildings for corner in obj.bound_box)
            max_x = max((obj.matrix_world @ Vector(corner)).x for obj in deferred_buildings for corner in obj.bound_box)
            max_y = max((obj.matrix_world @ Vector(corner)).y for obj in deferred_buildings for corner in obj.bound_box)
            content_bounds = {{
                "min_x": float(min_x),
                "min_y": float(min_y),
                "max_x": float(max_x),
                "max_y": float(max_y),
            }}
            for obj in deferred_buildings:
                marker_obj = bpy.data.objects.new(obj.name, None)
                marker_obj.empty_display_type = 'PLAIN_AXES'
                marker_obj.matrix_world = obj.matrix_world.copy()
                marker_obj['a3d_marker_only'] = True
                bpy.context.scene.collection.objects.link(marker_obj)
        else:
            content_bounds = None

        if terrain_size:
            terrain_bounds = {{
                "min_x": 0.0,
                "min_y": 0.0,
                "max_x": float(terrain_size),
                "max_y": float(terrain_size),
            }}

        terrain_shift = {{
            "x": float(bpy.context.scene.get('terrain_shift_x', 0.0)),
            "y": float(bpy.context.scene.get('terrain_shift_y', 0.0)),
        }}
        if terrain_metadata_output:
            with open(terrain_metadata_output, "w", encoding="utf-8") as fh:
                json.dump({{
                    "content_bounds": content_bounds,
                    "terrain_bounds": terrain_bounds,
                    "terrain_shift": terrain_shift,
                    "terrain_size": float(terrain_size or 0.0),
                    "content_scale": float(content_scale),
                }}, fh, indent=2, sort_keys=True)

        for obj in deferred_buildings:
            bpy.data.objects.remove(obj, do_unlink=True)
        steps_done.append(f"buildings_deferred({{len(payload)}})")
except Exception as e:
    print(f"buildings_deferred failed: {{e}}")
    import traceback; traceback.print_exc()
    steps_failed.append("buildings_deferred")

# ── Step 8: Export A3D ────────────────────────────────────────────
try:
    a3d_dir = os.path.dirname(a3d_output)
    if a3d_dir:
        os.makedirs(a3d_dir, exist_ok=True)
    bpy.ops.export_scene.a3d(filepath=a3d_output)
    steps_done.append("export_a3d")
except Exception as e:
    print(f"A3D export failed: {{e}}")
    steps_failed.append("export_a3d")

{save_code}

a3d_size = os.path.getsize(a3d_output) if os.path.isfile(a3d_output) else 0
akm_count = len([f for f in os.listdir(meshes_dir) if f.lower().endswith('.akm')]) if os.path.isdir(meshes_dir) else 0

_data = {{
    "status": "completed" if not steps_failed else "partial",
    "steps_done": steps_done,
    "steps_failed": steps_failed,
    "a3d_output": a3d_output,
    "a3d_size_bytes": a3d_size,
    "meshes_dir": meshes_dir,
    "total_akms": akm_count,
    "terrain_size": terrain_size,
    "content_bounds": content_bounds,
    "terrain_bounds": terrain_bounds,
    "terrain_shift": {{
        "x": float(bpy.context.scene.get('terrain_shift_x', 0.0)),
        "y": float(bpy.context.scene.get('terrain_shift_y', 0.0)),
    }},
}}
""", timeout=900)


def paint_terrain_direct(blend_file, save=True, blender_path=None):
    """Paint terrain vertex colors directly from blosm OSM data.

    Paints the MaterialID vertex color layer on the Terrain object
    using OSM polygon categories (grass, road, concrete, residential).
    No asciiid relay needed — the A3D exporter reads the vertex colors.

    Args:
        blend_file: Path to .blend file with blosm objects + Terrain.
        save: Save .blend file after painting.
        blender_path: Optional path to Blender executable.

    Returns:
        BlenderBridge result dict with painted vertex count.
    """
    save_code = f"bpy.ops.wm.save_as_mainfile(filepath={blend_file!r})" if save else ""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import addon_utils
addon_utils.enable({_ADDON_MODULE!r}, default_set=True)
import bpy

result = bpy.ops.asciicker.paint_terrain_direct()
if 'FINISHED' not in result:
    raise RuntimeError("paint_terrain_direct returned " + str(result))

{save_code}
_data = {{
    "status": "painted",
    "operator_result": str(result),
}}
""", timeout=300)


def auto_terrain(blend_file, save=True, blender_path=None):
    """Auto-create terrain sized to cover all blosm objects.

    Computes blosm bounding box, snaps to 8-unit grid with margin,
    creates terrain plane with MaterialID vertex colors.

    Args:
        blend_file: Path to .blend file with blosm objects.
        save: Save .blend file after creation.
        blender_path: Optional path to Blender executable.

    Returns:
        BlenderBridge result dict with terrain info.
    """
    save_code = f"bpy.ops.wm.save_as_mainfile(filepath={blend_file!r})" if save else ""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import addon_utils
addon_utils.enable({_ADDON_MODULE!r}, default_set=True)
import bpy, math
from mathutils import Vector

# Compute blosm extent
min_x = min_y = float('inf')
max_x = max_y = float('-inf')
found = False
for obj in bpy.context.scene.objects:
    if obj.type not in ('MESH', 'CURVE', 'EMPTY'):
        continue
    is_blosm = (obj.get('building') or obj.get('highway') or obj.get('landuse')
                 or obj.get('natural') or obj.get('amenity') or obj.get('landcover')
                 or obj.get('waterway') or obj.get('leisure'))
    is_parent = (obj.type == 'EMPTY' and obj.name.startswith('None_'))
    is_blosm_named = obj.name.startswith('None_') and obj.type in ('MESH', 'CURVE')
    if not is_blosm and not is_parent and not is_blosm_named:
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
    raise RuntimeError("No blosm objects found in scene")

patch_size = 8
# [RC-22 FIX] No border padding. Still square (single size param).
grid_min_x = math.floor(min_x / patch_size)
grid_min_y = math.floor(min_y / patch_size)
grid_max_x = math.ceil(max_x / patch_size)
grid_max_y = math.ceil(max_y / patch_size)
num_x = grid_max_x - grid_min_x
num_y = grid_max_y - grid_min_y
terrain_size = max(8, max(num_x, num_y) * patch_size)

bpy.ops.asciicker.create_terrain(size=float(terrain_size), subdivisions=1)
terrain = bpy.context.active_object
verts = len(terrain.data.vertices) if terrain else 0

# Reposition blosm objects onto the terrain (terrain stays at origin)
# Only shift ROOT objects (no blosm parent) — children inherit parent transform
shift_x = -(grid_min_x * patch_size)
shift_y = -(grid_min_y * patch_size)
shifted = 0
for obj in list(bpy.context.scene.objects):
    if obj.type not in ('MESH', 'CURVE', 'EMPTY'):
        continue
    is_blosm = (obj.get('building') or obj.get('highway') or obj.get('landuse')
                 or obj.get('natural') or obj.get('amenity') or obj.get('landcover')
                 or obj.get('waterway') or obj.get('leisure'))
    is_parent = (obj.type == 'EMPTY' and obj.name.startswith('None_'))
    is_blosm_named = obj.name.startswith('None_') and obj.type in ('MESH', 'CURVE')
    if is_blosm or is_parent or is_blosm_named:
        # Skip children whose parent is also a blosm object (avoid double-shift)
        if obj.parent and (obj.parent.name.startswith('None_') or obj.parent.get('building')):
            continue
        obj.location.x += shift_x
        obj.location.y += shift_y
        shifted += 1

{save_code}
_data = {{
    "status": "created",
    "terrain_name": terrain.name if terrain else None,
    "vertex_count": verts,
    "computed_size": terrain_size,
    "blosm_extent": [min_x, min_y, max_x, max_y],
    "shift_x": shift_x,
    "shift_y": shift_y,
    "objects_shifted": shifted,
}}
""", timeout=120)


def separate_buildings(blend_file, save=True, blender_path=None):
    """Separate merged blosm buildings into individual objects by loose parts.

    Finds the None_buildings mesh, separates by loose geometry, and renames
    each result to Building_001, Building_002, etc.

    Args:
        blend_file: Path to .blend file with blosm buildings.
        save: Save .blend file after separation.
        blender_path: Optional path to Blender executable.

    Returns:
        BlenderBridge result dict with building count and names.
    """
    save_code = f"bpy.ops.wm.save_as_mainfile(filepath={blend_file!r})" if save else ""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import bpy
from mathutils import Vector

# Find merged buildings object (blosm 2D mode creates None_buildings)
buildings_obj = None
for obj in bpy.context.scene.objects:
    if obj.type == 'MESH' and obj.name.lower().startswith('none_buildings'):
        buildings_obj = obj
        break

if buildings_obj is None:
    raise RuntimeError("No None_buildings mesh found in scene")

# Deselect all, select + activate the buildings mesh
bpy.ops.object.select_all(action='DESELECT')
buildings_obj.select_set(True)
bpy.context.view_layer.objects.active = buildings_obj

# Enter edit mode, select all, separate by loose parts
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
try:
    bpy.ops.mesh.separate(type='LOOSE')
except Exception as e:
    bpy.ops.object.mode_set(mode='OBJECT')
    raise RuntimeError(f"mesh.separate failed: {{e}}")
bpy.ops.object.mode_set(mode='OBJECT')

# Collect all objects that came from the separation (None_buildings, None_buildings.001, etc.)
separated = []
for obj in bpy.context.scene.objects:
    if obj.type == 'MESH' and obj.name.lower().startswith('none_buildings'):
        separated.append(obj)

# Rename sequentially to Building_NNN
buildings_info = []
for i, obj in enumerate(sorted(separated, key=lambda o: o.name), start=1):
    new_name = f"Building_{{i:03d}}"
    obj.name = new_name
    obj.data.name = new_name
    # Record world-space bounding box center
    bbox = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    center_x = sum(v.x for v in bbox) / 8.0
    center_y = sum(v.y for v in bbox) / 8.0
    center_z = sum(v.z for v in bbox) / 8.0
    buildings_info.append({{"name": new_name, "center": [center_x, center_y, center_z]}})

{save_code}
_data = {{
    "status": "separated",
    "total_buildings": len(buildings_info),
    "buildings": buildings_info,
}}
""", timeout=300)


def auto_calibrate_osm(blend_file, blender_path=None):
    """Auto-calibrate OSM-to-engine coordinate mapping.

    Matches blosm buildings against A3D instances to compute
    scale and offset. Returns computed values from scene properties.

    Args:
        blend_file: Path to .blend file with both blosm and A3D data.
        blender_path: Optional path to Blender executable.

    Returns:
        BlenderBridge result dict with scale, offset_x, offset_y.
    """
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import addon_utils
addon_utils.enable({_ADDON_MODULE!r}, default_set=True)
import bpy

result = bpy.ops.asciicker.auto_calibrate_osm()
if 'FINISHED' not in result:
    raise RuntimeError("auto_calibrate_osm operator returned " + str(result))

props = bpy.context.scene.asciicker_osm_painter
_data = {{
    "scale": props.scale,
    "offset_x": props.offset_x,
    "offset_y": props.offset_y,
}}
""")
