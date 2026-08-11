# Asciicker UI Panels
# Sidebar panels for terrain, materials, colors, and export
# [DEPENDENCY:BLENDER] - All classes require Blender Python API (bpy)

"""
Asciicker Sidebar Panels -- Blender UI Definitions
====================================================

ARCHITECTURE:
    This module defines every UI element that appears in the Blender 3D
    Viewport sidebar under the **Asciicker** tab (View3D > Sidebar > Asciicker).

    The file is organised into three tiers:

    1. **Helper functions** (``get_project_root``, ``get_meshes_path``,
       ``get_akm_files``) -- filesystem lookups used by operators and
       property callbacks.

    2. **PropertyGroup + Operators** -- ``AsciickerProperties`` stores
       per-scene addon state; operator classes (``ASCIICKER_OT_*``)
       implement button actions.

    3. **Panel classes** (``ASCIICKER_PT_*``) -- each ``Panel`` subclass
       maps to one collapsible section in the sidebar, drawing widgets
       that expose the operators above.

    Panel ordering in the sidebar follows the order in the ``classes``
    tuple at the bottom of this file: Terrain, Camera, Materials, Colors,
    Mesh Library, GMap Bake, Export, Info.

KEY EXPORTS:
    - ``register()``   -- Registers all classes with Blender and attaches
                          ``AsciickerProperties`` to ``bpy.types.Scene``.
    - ``unregister()`` -- Reverses the above.
    - ``classes``      -- Ordered tuple of all registrable classes.

PIPELINE CONTEXT:
    The panels form the primary artist-facing interface for the export
    pipeline:

        Blender Scene
          |
          v
        [Validate + Decimate]  (ASCIICKER_OT_one_click_export)
          |
          v
        [Export AKM per mesh]  -> assets/meshes/*.akm  [DATA-CONTRACT:AKM]
          |
          v
        [Export A3D map]       -> assets/a3d/*.a3d     [DATA-CONTRACT:A3D]
          |
          v
        Copy to game_map_y8.a3d (runtime asset)

    GMap Bake panels drive a separate sub-pipeline that projects Google
    Maps satellite textures onto OSM building geometry via Blender's
    Cycles bake, then converts the result to vertex colors for the
    engine's palette-based renderer.

DATA-CONTRACT NOTES:
    [DATA-CONTRACT:AKM]
        AKM files are PLY-based meshes with engine-specific vertex
        attributes (palette-quantised vertex colors, collision flags
        encoded in alpha).  Exported by ``io_asciicker.mesh.export_akm``.

    [DATA-CONTRACT:A3D]
        A3D files are scene/world maps referencing AKM meshes plus
        terrain, spawn points, and instance transforms.  Exported by
        ``io_asciicker.scene.export_a3d``.
"""

import bpy  # [DEPENDENCY:BLENDER]
import os
import shutil
import sys
from bpy.types import Panel, Operator, PropertyGroup  # [DEPENDENCY:BLENDER]
from bpy.props import StringProperty, EnumProperty, BoolProperty, IntProperty, PointerProperty  # [DEPENDENCY:BLENDER]
from io_asciicker import path_utils


# ---------------------------------------------------------------------------
# Helper functions -- filesystem lookups
# ---------------------------------------------------------------------------

# WHY: The addon needs to locate project assets (assets/meshes/, assets/a3d/) at runtime.
# Blender addons have no guaranteed directory, so we probe several heuristics.
# Canonical implementation lives in path_utils; re-exported here for backward compat.
from io_asciicker.path_utils import get_project_root  # noqa: E402


def get_meshes_path():
    """Get path to the ``assets/meshes/`` folder inside the project root.

    Returns:
        str: Absolute path to the meshes directory, or empty string if the
        directory does not exist.

    [DATA-CONTRACT:AKM] -- The meshes directory is the canonical location
    for ``.akm`` mesh assets consumed by the engine and referenced by
    ``.a3d`` map files.
    """
    project_root = get_project_root()
    meshes_path = os.path.join(project_root, "assets", "meshes")
    if os.path.exists(meshes_path):
        return meshes_path
    return ""


def get_akm_files(self, context):
    """Build an enum items list of ``.akm`` files for the mesh selector.

    [DATA-CONTRACT:AKM] -- Scans the ``assets/meshes/`` directory for files
    matching ``*.akm`` and returns them sorted alphabetically as Blender
    ``EnumProperty`` item tuples ``(identifier, name, description)``.

    WHY this is a callback:
        Blender ``EnumProperty`` supports a dynamic ``items`` callback so
        the dropdown refreshes every time the user opens it, picking up
        newly exported meshes without requiring an addon restart.

    Args:
        self: The ``PropertyGroup`` instance (unused but required by Blender).
        context: The current Blender context (unused but required by Blender).

    Returns:
        list[tuple]: Enum items suitable for ``EnumProperty(items=...)``.
    """
    items = []
    meshes_path = get_meshes_path()
    if meshes_path and os.path.exists(meshes_path):
        for f in sorted(os.listdir(meshes_path)):
            if f.endswith('.akm'):
                name = f[:-4]  # Remove .akm extension
                items.append((name, name, f"Place {name} mesh"))
    if not items:
        items.append(('NONE', 'No meshes found', ''))
    return items


# ---------------------------------------------------------------------------
# PropertyGroup -- per-scene addon state
# ---------------------------------------------------------------------------

class AsciickerProperties(PropertyGroup):
    """Per-scene properties for the Asciicker addon.

    Stored on ``bpy.types.Scene.asciicker_props`` (attached in ``register()``).
    These properties persist with the .blend file, giving each scene its own
    configuration.

    Groups:
        - **Mesh selection**: ``selected_mesh`` -- drives the Mesh Library panel.
        - **Export toggles**: ``ignore_non_manifold``, ``auto_fill_terrain`` --
          control validation and processing behaviour during one-click export.
        - **GMap Bake settings**: ``gmap_*`` properties -- configure the Google
          Maps texture-to-vertex-color bake pipeline.

    [DATA-CONTRACT:AKM] ``selected_mesh`` references ``.akm`` filenames.
    [DATA-CONTRACT:A3D] Export toggles affect A3D scene validation.
    """
    selected_mesh: EnumProperty(
        name="Mesh",
        description="Select mesh to place",
        items=get_akm_files,
    )
    ignore_non_manifold: BoolProperty(
        name="Ignore Non-Manifold",
        description="Allow non-manifold meshes to export",
        default=True,
    )
    auto_fill_terrain: BoolProperty(
        name="Auto-Fill Terrain",
        description="Fill terrain with grass if no texture is found",
        default=True,
    )
    # WHY: GMap bake properties are grouped here (not on individual operators)
    # so the user's settings persist across button clicks and Blender sessions.
    gmap_target_collection: StringProperty(
        name="Target Collection",
        description="Collection name containing OSM building targets",
        default="",
    )
    gmap_keep_bake_image: BoolProperty(
        name="Keep Baked Image",
        description="Keep temporary bake images for inspection",
        default=True,
    )
    gmap_auto_uv_unwrap: BoolProperty(
        name="Auto UV Unwrap",
        description="Generate UVs on targets if missing",
        default=True,
    )
    gmap_bake_resolution: IntProperty(
        name="Bake Resolution",
        description="Resolution for temporary bake images",
        default=2048,
        min=256,
        max=8192,
    )
    gmap_bake_margin: IntProperty(
        name="Bake Margin",
        description="Bake margin in pixels",
        default=2,
        min=0,
        max=32,
    )
    gmap_vcol_layer: StringProperty(
        name="VCol Layer",
        description="Vertex color layer name for baked output",
        default="Col",
    )
    # WHY poll lambdas: PointerProperty poll callbacks filter the object
    # picker to only show MESH objects, since GMap bake operates exclusively
    # on mesh geometry (lights, cameras, empties are not valid targets).
    gmap_source_object: PointerProperty(
        type=bpy.types.Object,
        name="GMap Source",
        description="Google 3D Tiles mesh to clean up",
        poll=lambda self, obj: obj.type == 'MESH',  # [DEPENDENCY:BLENDER] PointerProperty poll callback
    )
    gmap_footprint_object: PointerProperty(
        type=bpy.types.Object,
        name="Building Footprints",
        description="OSM buildings or any 2D footprint mesh",
        poll=lambda self, obj: obj.type == 'MESH',  # [DEPENDENCY:BLENDER] PointerProperty poll callback
    )
    # picoCAD2 Import properties
    picocad_collision_preset: EnumProperty(
        name="Collision Preset",
        description="Collision weight preset for processed meshes",
        items=[
            ("SOLID", "Solid (walls/floor)", "Fully solid — weight 0.0"),
            ("PASSTHROUGH", "Passthrough (decorations)", "No collision — weight 1.0"),
        ],
        default="SOLID",
    )
    picocad_batch_mode: BoolProperty(
        name="Batch Mode",
        description="Process all selected mesh objects instead of active only",
        default=False,
    )
    picocad_palette_report: StringProperty(
        name="Palette Report",
        description="Last palette quantization report",
        default="",
    )
    picocad_status: StringProperty(
        name="Status",
        description="Current picoCAD2 processing status",
        default="",
    )


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class ASCIICKER_OT_quick_export(Operator):
    """Quick export to test_map.a3d and copy to game.

    This is a thin convenience wrapper that delegates entirely to the
    ``one_click_export`` operator.  It exists so the Export panel can
    offer a prominently labelled "Quick Export & Test" button with its
    own tooltip, while reusing the full pipeline logic.

    [DATA-CONTRACT:A3D] Output: ``assets/a3d/test_map.a3d`` copied to
    ``assets/a3d/game_map_y8.a3d``.
    """
    bl_idname = "asciicker.quick_export"
    bl_label = "Quick Export & Test"
    bl_description = "Export map and copy to game_map_y8.a3d for testing"

    def normalize_mesh_name(self, name):
        """Strip ``.akm`` suffix and Blender duplicate suffixes (e.g. ``.001``)."""
        return path_utils.normalize_mesh_name(name)

    def execute(self, context):
        """Delegate to the one-click export operator."""
        return bpy.ops.asciicker.one_click_export()


class ASCIICKER_OT_one_click_export(Operator):
    """One-Click Export: Validate, Process, Export AKM, Export A3D.

    Implements the full automated export pipeline:

        1. **Identify** -- Split scene objects into mesh objects and terrain.
        2. **Optimise** -- Auto-decimate meshes to engine-friendly face counts
           (5 k for buildings, 20 k for terrain).
        3. **Validate** -- Check face counts and optionally manifold geometry.
        4. **Process** -- Bake/assign textures via ``process_blosm``.
        5. **Export AKM** -- Write each unique mesh to ``assets/meshes/<name>.akm``.
           [DATA-CONTRACT:AKM]
        6. **Export A3D** -- Write the scene map to ``assets/a3d/test_map.a3d``, then
           copy to ``assets/a3d/game_map_y8.a3d``.  [DATA-CONTRACT:A3D]

    WHY modules are reloaded:
        ``importlib.reload`` is called on ``export_akm``, ``export_a3d``, and
        ``process_blosm`` so that artists iterating on the addon code see
        changes immediately without restarting Blender.

    TODO(PIPELINE-FIX): The face-count limits (5000, 20000, 50000) are
    hard-coded magic numbers.  Consider exposing them as addon preferences
    or per-scene properties so artists can tune them per project.
    """
    bl_idname = "asciicker.one_click_export"
    bl_label = "Export to Asciicker"
    bl_description = "Validate, Process, and Export map to game engine"

    def normalize_mesh_name(self, name):
        """Strip ``.akm`` suffix and Blender duplicate suffixes (e.g. ``.001``)."""
        return path_utils.normalize_mesh_name(name)

    def execute(self, context):
        """Run the full export pipeline.

        Returns:
            set: ``{'FINISHED'}`` on success, ``{'CANCELLED'}`` on any
            validation or export error.
        """
        import importlib

        # Get project paths
        project_root = get_project_root()
        meshes_dir = os.path.join(project_root, "assets", "meshes")
        os.makedirs(meshes_dir, exist_ok=True)
        test_map = os.path.join(project_root, "assets", "a3d", "test_map.a3d")
        game_map = os.path.join(project_root, "assets", "a3d", "game_map_y8.a3d")

        # WHY reload: Allow hot-reloading of exporter code during addon
        # development without restarting Blender.
        if 'io_asciicker.mesh.export_akm' in sys.modules:
            importlib.reload(sys.modules['io_asciicker.mesh.export_akm'])
        if 'io_asciicker.scene.export_a3d' in sys.modules:
            importlib.reload(sys.modules['io_asciicker.scene.export_a3d'])

        from io_asciicker.mesh import export_akm
        from io_asciicker.scene import export_a3d

        props = context.scene.asciicker_props

        # --- Step 1: Identify Objects ---
        # WHY separate terrain: Terrain has different poly limits and
        # validation rules (open edges are expected on a plane).
        mesh_objects = [
            obj for obj in context.scene.objects
            if obj.type == 'MESH' and obj.name.lower() != 'terrain'
        ]

        terrain_obj = None
        for obj in context.scene.objects:
            if obj.type == 'MESH' and obj.name.lower() == 'terrain':
                terrain_obj = obj
                break

        # --- Step 2: Validation ---
        # WHY load scripts here: process_blosm lives in ``scripts/`` (outside
        # the addon package) and needs the repo root on sys.path.
        process_blosm = None
        repo_root = path_utils.ensure_repo_root(project_root or __file__)
        if not repo_root:
            self.report({'ERROR'}, "Failed to locate repo root for scripts. Set ASCIICKER_PATH or open the .blend from the repo.")
            return {'CANCELLED'}

        try:
            from scripts import process_blosm as process_blosm_module
            from scripts import validate_blosm_mesh as validate_blosm_mesh_module
            importlib.reload(process_blosm_module)
            importlib.reload(validate_blosm_mesh_module)
            process_blosm = process_blosm_module
        except ImportError as e:
            self.report({'ERROR'}, f"Failed to load scripts: {e}")
            return {'CANCELLED'}

        # --- Step 3: OPTIMIZE (Auto-Decimate) ---
        self.report({'INFO'}, "Optimizing meshes...")
        for obj in mesh_objects:
            # 5000 is target for standard buildings
            process_blosm.decimate_mesh(obj, target_faces=5000)

        if terrain_obj:
            # Terrain can be higher
            process_blosm.decimate_mesh(terrain_obj, target_faces=20000)

        # --- Step 4: Validation ---
        self.report({'INFO'}, "Validating scenes...")
        # WHY 20k limit: Engine constraint -- meshes above this count cause
        # noticeable frame drops in the ASCII renderer.
        check_manifold = not props.ignore_non_manifold
        if props.ignore_non_manifold:
            self.report({'WARNING'}, "Ignoring non-manifold validation")

        is_valid, errors = export_a3d.validate_scene_objects(
            mesh_objects,
            max_faces=20000,
            check_manifold=check_manifold,
        )

        if terrain_obj:
            # WHY 50k for terrain: a 64x64 grid = 4096 quads = 8192 tris;
            # 50 k gives comfortable headroom for higher-res terrains.
            # WHY no manifold check: terrain is typically an open plane.
            t_valid, t_errors = export_a3d.validate_scene_objects(
                [terrain_obj],
                max_faces=50000,
                check_manifold=False
            )
            if not t_valid:
                is_valid = False
                errors.extend(t_errors)

        if not is_valid:
            self.report({'ERROR'}, "Export Failed: validation errors found")
            for err in errors:
                self.report({'ERROR'}, err)
            # Stop execution
            return {'CANCELLED'}

        # --- Step 5: Processing (Auto-Texturing) ---
        self.report({'INFO'}, "Processing/Baking textures...")
        export_a3d.process_scene_objects(mesh_objects)

        # WHY separate terrain processing: export_a3d.process_scene_objects
        # only handles buildings; terrain needs process_blosm.process_terrain
        # which assigns a default material ID for untextured terrain.
        if terrain_obj and process_blosm:
            default_material_id = 1 if props.auto_fill_terrain else None
            process_blosm.process_terrain(terrain_obj, default_material_id=default_material_id)


        # --- Step 6: Export AKMs --- [DATA-CONTRACT:AKM]
        self.report({'INFO'}, "Exporting AKMs...")
        exported = 0
        skipped = 0
        seen = set()

        for obj in mesh_objects:
            mesh_name = self.normalize_mesh_name(obj.name)
            if not mesh_name:
                continue
            # WHY dedup by name: Blender duplicates (e.g. "House.001",
            # "House.002") should map to the same .akm to avoid bloat.
            if mesh_name in seen:
                continue
            seen.add(mesh_name)

            filepath = os.path.join(meshes_dir, f"{mesh_name}.akm")

            # We disable validation/warnings inside export_akm to avoid spam,
            # or we rely on our pre-validation.
            res = export_akm.save(
                self,
                context,
                filepath=filepath,
                use_selection=False,
                apply_world_transform=False,
                objects=[obj],
            )
            if res == {'FINISHED'}:
                exported += 1
            else:
                skipped += 1

        # --- Step 7: Export A3D --- [DATA-CONTRACT:A3D]
        self.report({'INFO'}, "Exporting A3D Map...")
        # WHY skip_validation: We already validated and optimised above;
        # running it again would be redundant and slow.
        result = export_a3d.save_a3d(context, test_map, skip_validation=True)

        if result == {'FINISHED'}:
            # Copy to game map for immediate playtesting
            shutil.copy(test_map, game_map)
            self.report({'INFO'}, f"SUCCESS: Exported {exported} AKMs + Map to game_map_y8.a3d")
        else:
            self.report({'ERROR'}, "A3D Export failed")
            return {'CANCELLED'}

        return {'FINISHED'}


class ASCIICKER_OT_place_mesh(Operator):
    """Place selected AKM mesh at the 3D cursor.

    Imports a ``.akm`` file from the ``assets/meshes/`` directory using the
    ``import_mesh.akm`` operator, repositions the resulting object to the
    current 3D cursor location, and applies default A3D instance
    properties (``a3d_flags``, ``a3d_story_id``).

    [DATA-CONTRACT:AKM] Reads ``.akm`` files via ``import_mesh.akm``.
    [DATA-CONTRACT:A3D] Sets ``a3d_flags`` and ``a3d_story_id`` custom
    properties that the A3D exporter reads at export time.
    """
    bl_idname = "asciicker.place_mesh"
    bl_label = "Place Mesh"
    bl_description = "Place selected mesh at 3D cursor location"

    def execute(self, context):
        """Import the selected AKM mesh and place it at the 3D cursor."""
        props = context.scene.asciicker_props
        mesh_name = props.selected_mesh

        if mesh_name == 'NONE':
            self.report({'WARNING'}, "No mesh selected")
            return {'CANCELLED'}

        # Get mesh path [DATA-CONTRACT:AKM]
        meshes_path = get_meshes_path()
        filepath = os.path.join(meshes_path, f"{mesh_name}.akm")

        if not os.path.exists(filepath):
            self.report({'ERROR'}, f"File not found: {filepath}")
            return {'CANCELLED'}

        # Save cursor location
        cursor_loc = context.scene.cursor.location.copy()

        # Import the mesh
        # This brings it in at origin usually, or whatever the file says.
        # We need to move it to cursor.
        res = bpy.ops.import_mesh.akm(filepath=filepath)

        if 'FINISHED' not in res:
             self.report({'ERROR'}, "Failed to import mesh")
             return {'CANCELLED'}

        # The import operator selects the new object and makes it active
        obj = context.active_object

        # Move to cursor
        obj.location = cursor_loc

        # WHY keep import name: The A3D exporter derives the .akm reference
        # from the object name, so we preserve whatever the importer set.
        # TODO(PIPELINE-FIX): Verify that the A3D exporter's name-to-akm
        # resolution actually matches this assumption; currently uncertain.

        # WHY default custom properties: The A3D exporter expects these
        # custom props on every mesh instance.  Without them, the instance
        # would be invisible or incorrectly placed in the engine.
        # a3d_flags=3 means INST_VISIBLE | INST_USE_TREE (bitfield).
        if 'a3d_flags' not in obj:
            obj['a3d_flags'] = 3  # INST_VISIBLE | INST_USE_TREE
        if 'a3d_story_id' not in obj:
            obj['a3d_story_id'] = -1

        self.report({'INFO'}, f"Placed {mesh_name} at cursor")
        return {'FINISHED'}


class ASCIICKER_OT_refresh_meshes(Operator):
    """Refresh the AKM mesh list from the meshes folder.

    Forces Blender to re-evaluate the ``get_akm_files`` callback by
    unsetting the ``selected_mesh`` enum, which clears its cached item
    list.

    [DATA-CONTRACT:AKM] Re-scans ``assets/meshes/*.akm``.
    """
    bl_idname = "asciicker.refresh_meshes"
    bl_label = "Refresh"
    bl_description = "Refresh mesh list from meshes folder"

    def execute(self, context):
        """Clear the cached mesh enum to force a directory re-scan."""
        # Force property update
        context.scene.asciicker_props.property_unset("selected_mesh")
        self.report({'INFO'}, "Mesh list refreshed")
        return {'FINISHED'}


class ASCIICKER_OT_import_all_meshes(Operator):
    """Batch import every .akm file from the meshes folder."""
    bl_idname = "asciicker.import_all_meshes"
    bl_label = "Import All from Folder"
    bl_description = "Import every .akm file from the meshes folder"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        meshes_path = get_meshes_path()
        if not meshes_path:
            self.report({'ERROR'}, "Could not find meshes folder")
            return {'CANCELLED'}

        akm_files = sorted(
            f for f in os.listdir(meshes_path) if f.lower().endswith('.akm')
        )
        if not akm_files:
            self.report({'WARNING'}, f"No .akm files found in {meshes_path}")
            return {'CANCELLED'}

        imported = 0
        failed = 0
        for filename in akm_files:
            filepath = os.path.join(meshes_path, filename)
            try:
                res = bpy.ops.import_mesh.akm(filepath=filepath)
                if 'FINISHED' in res:
                    imported += 1
                else:
                    failed += 1
            except Exception as e:
                print(f"Failed to import {filepath}: {e}")
                failed += 1

        if imported == 0:
            self.report({'ERROR'}, f"All {failed} imports failed")
            return {'CANCELLED'}

        msg = f"Imported {imported} meshes"
        if failed:
            msg += f" ({failed} failed)"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class ASCIICKER_OT_reduce_mesh(Operator):
    """Reduce mesh polygon count using Blender's Decimate modifier.

    Presents a dialog showing the current face count and a target slider,
    then applies a Collapse-type Decimate modifier.

    WHY this exists:
        The engine has per-mesh face-count limits (enforced by validation
        in the export pipeline).  This operator lets artists interactively
        reduce dense imported geometry (e.g. photogrammetry or CAD meshes)
        before export.

    [DEPENDENCY:BLENDER] Uses ``bpy.ops.object.modifier_apply``.
    """
    bl_idname = "asciicker.reduce_mesh"
    bl_label = "Reduce Mesh"
    bl_description = "Reduce selected mesh polygon count for export"
    # WHY REGISTER + UNDO: REGISTER makes the operator appear in the F3
    # search menu; UNDO enables Ctrl-Z support so artists can revert the
    # destructive decimation.  [DEPENDENCY:BLENDER]
    bl_options = {'REGISTER', 'UNDO'}

    target_faces: bpy.props.IntProperty(
        name="Target Faces",
        description="Target number of faces after reduction",
        default=50000,
        min=100,
        max=1000000,
    )

    def invoke(self, context, event):
        """Set sensible default target based on current face count.

        [DEPENDENCY:BLENDER] ``invoke_props_dialog`` opens a modal dialog
        before ``execute`` runs, letting the user adjust ``target_faces``.
        """
        obj = context.active_object
        if obj and obj.type == 'MESH':
            current_faces = len(obj.data.polygons)
            # WHY 10% / 50k / 1000: Heuristic defaults -- 10% is a reasonable
            # starting ratio, capped at 50k (engine limit) with a floor of
            # 1000 to avoid over-decimation on already-simple meshes.
            self.target_faces = min(current_faces // 10, 50000)
            if self.target_faces < 1000:
                self.target_faces = 1000
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        """Draw the dialog with current face count and target slider."""
        layout = self.layout
        obj = context.active_object
        if obj and obj.type == 'MESH':
            current = len(obj.data.polygons)
            layout.label(text=f"Current faces: {current:,}")
        layout.prop(self, "target_faces")

    def execute(self, context):
        """Apply decimation to the active mesh object.

        Returns:
            set: ``{'FINISHED'}`` on success (including no-op when already
            below target), ``{'CANCELLED'}`` if no mesh is selected.
        """
        obj = context.active_object

        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object first")
            return {'CANCELLED'}

        mesh = obj.data
        current_faces = len(mesh.polygons)

        if current_faces <= self.target_faces:
            self.report({'INFO'}, f"Mesh already has {current_faces:,} faces (target: {self.target_faces:,})")
            return {'FINISHED'}

        # Calculate decimation ratio
        ratio = self.target_faces / current_faces

        # Add decimate modifier
        decimate = obj.modifiers.new(name="Decimate_Export", type='DECIMATE')
        decimate.decimate_type = 'COLLAPSE'
        decimate.ratio = ratio

        # Apply the modifier
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=decimate.name)

        new_faces = len(obj.data.polygons)
        reduction = ((current_faces - new_faces) / current_faces) * 100

        self.report({'INFO'}, f"Reduced from {current_faces:,} to {new_faces:,} faces ({reduction:.1f}% reduction)")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Panels -- Blender sidebar sections (View3D > Sidebar > Asciicker)
# ---------------------------------------------------------------------------
# WHY all panels share ``bl_category = 'Asciicker'``:
#     This groups them into a single sidebar tab.  Blender sorts panels
#     within a tab by their registration order (the ``classes`` tuple below).

class ASCIICKER_PT_terrain(Panel):
    """Terrain creation and editing panel.

    Provides buttons for:
        - Creating a new terrain grid (``asciicker.create_terrain``).
        - Entering vertex-color paint mode for material assignment
          (``asciicker.enter_paint_mode``).

    [DEPENDENCY:BLENDER] Operators are defined in ``io_asciicker.tools``.
    """
    bl_label = "Terrain"
    bl_idname = "ASCIICKER_PT_terrain"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Asciicker'

    def draw(self, context):
        """Draw terrain creation and paint-mode buttons."""
        layout = self.layout

        col = layout.column(align=True)
        col.operator("asciicker.create_terrain", icon='MESH_GRID')
        col.operator("asciicker.enter_paint_mode", icon='BRUSH_DATA')


class ASCIICKER_PT_camera(Panel):
    """Camera navigation controls.

    Quick-access buttons for common viewport orientations used when
    editing terrain or placing meshes top-down.

    [DEPENDENCY:BLENDER] Operators are defined in ``io_asciicker.tools``.
    """
    bl_label = "Camera"
    bl_idname = "ASCIICKER_PT_camera"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Asciicker'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        """Draw zoom, frame-terrain, and top-view shortcut buttons."""
        layout = self.layout

        col = layout.column(align=True)
        col.operator("asciicker.zoom_out", icon='ZOOM_OUT')
        col.operator("asciicker.frame_terrain", icon='PIVOT_BOUNDBOX')
        col.operator("asciicker.top_view", icon='AXIS_TOP')


class ASCIICKER_PT_materials(Panel):
    """Material brush presets panel.

    Displays a 2-column grid of preset material buttons.  Each button
    invokes ``asciicker.set_material_brush`` with a fixed ``material_id``
    integer.

    WHY material IDs are integers 0-7:
        The Asciicker engine encodes material type in the **red channel**
        of vertex colors (see the info box at the bottom of the panel).
        Each integer maps to a physical surface in the engine's material
        table (water=0, grass=1, dirt=2, stone=3, sand=4, snow=5, wood=6,
        steel=7).

    [DATA-CONTRACT:AKM] Vertex color red channel encodes material ID.
    [DEPENDENCY:BLENDER] Operator defined in ``io_asciicker.tools``.
    """
    bl_label = "Material Brush"
    bl_idname = "ASCIICKER_PT_materials"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Asciicker'

    def draw(self, context):
        """Draw a 2-column grid of material preset buttons (water through steel)."""
        layout = self.layout

        # Quick presets
        box = layout.box()
        box.label(text="Presets:")

        grid = box.grid_flow(columns=2, align=True)

        # Water
        op = grid.operator("asciicker.set_material_brush", text="Water", icon='MATFLUID')
        op.material_id = 0

        # Grass
        op = grid.operator("asciicker.set_material_brush", text="Grass", icon='OUTLINER_OB_GREASEPENCIL')
        op.material_id = 1

        # Dirt
        op = grid.operator("asciicker.set_material_brush", text="Dirt", icon='MATERIAL')
        op.material_id = 2

        # Stone
        op = grid.operator("asciicker.set_material_brush", text="Stone", icon='MOD_SOLIDIFY')
        op.material_id = 3

        # Sand
        op = grid.operator("asciicker.set_material_brush", text="Sand", icon='PARTICLES')
        op.material_id = 4

        # Snow
        op = grid.operator("asciicker.set_material_brush", text="Snow", icon='FREEZE')
        op.material_id = 5

        # Wood
        op = grid.operator("asciicker.set_material_brush", text="Wood", icon='FILE_3D')
        op.material_id = 6

        # Steel
        op = grid.operator("asciicker.set_material_brush", text="Steel", icon='META_CUBE')
        op.material_id = 7

        # WHY info box: Reminds artists of the colour-channel encoding
        # convention that the engine expects.
        box = layout.box()
        box.label(text="Red channel = Material ID")
        box.label(text="Green/Blue = Display color")


class ASCIICKER_PT_colors(Panel):
    """Color tools panel -- palette snapping and collision flags.

    Two sections:
        1. **Palette Tools** -- Snap vertex colors to the engine's 6x6x6
           terminal-safe palette and analyse colour distribution.
        2. **Collision (Edit Mode)** -- Set per-face collision flags
           (solid vs passthrough) encoded in vertex color alpha.

    WHY terminal palette matters:
        The Asciicker engine renders everything in a text terminal using
        the xterm-256 colour set, which provides a 6x6x6 RGB cube
        (channel values: 0, 51, 102, 153, 204, 255).  Colours outside
        this gamut are quantised at load time, so snapping in Blender
        gives artists WYSIWYG feedback.

    [DATA-CONTRACT:AKM] Vertex color alpha encodes collision flags.
    [DEPENDENCY:BLENDER] Operators defined in ``io_asciicker.tools``.
    """
    bl_label = "Color Tools"
    bl_idname = "ASCIICKER_PT_colors"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Asciicker'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        """Draw palette-snap tools and per-face collision flag buttons."""
        layout = self.layout
        obj = context.active_object

        # Palette tools
        col = layout.column(align=True)
        col.label(text="Palette Tools:")
        col.operator("asciicker.snap_colors", icon='SNAP_ON')
        col.operator("asciicker.analyze_colors", icon='INFO')

        row = col.row()
        row.label(text="Dithering: Mixing pixels to")
        row.label(text="simulate missing colors.")

        layout.separator()

        # Collision tools -- only active in edit mode because collision
        # flags are assigned per-face.
        box = layout.box()
        box.label(text="Collision (Edit Mode):")

        if obj and obj.mode == 'EDIT':
            row = box.row(align=True)
            row.operator("asciicker.set_collision_solid", text="Solid", icon='CUBE')
            row.operator("asciicker.set_collision_passthrough", text="Pass", icon='GHOST_ENABLED')
        else:
            box.label(text="Enter Edit Mode first")

        # WHY info box: Quick reference for the palette values so artists
        # do not have to memorise them.
        box = layout.box()
        box.label(text="Terminal Palette: 6x6x6")
        box.label(text="Safe: 0, 51, 102, 153, 204, 255")


class ASCIICKER_PT_mesh_library(Panel):
    """Mesh library panel for browsing and placing AKM assets.

    Provides:
        - An enum dropdown populated dynamically from ``assets/meshes/*.akm``.
        - A refresh button to re-scan the directory.
        - A "Place Mesh" button that imports the selected AKM at the
          3D cursor.
        - An info box showing the resolved meshes path.

    [DATA-CONTRACT:AKM] Scans and imports ``.akm`` mesh files.
    """
    bl_label = "Mesh Library"
    bl_idname = "ASCIICKER_PT_mesh_library"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Asciicker'

    def draw(self, context):
        """Draw the AKM mesh dropdown, refresh button, and place-mesh action."""
        layout = self.layout
        props = context.scene.asciicker_props

        # WHY row layout: Puts the dropdown and refresh button side by side,
        # keeping the panel compact.
        row = layout.row(align=True)
        row.prop(props, "selected_mesh", text="")
        row.operator("asciicker.refresh_meshes", text="", icon='FILE_REFRESH')

        # Place / Import buttons
        layout.operator("asciicker.place_mesh", icon='IMPORT')
        layout.operator("asciicker.import_all_meshes", icon='PACKAGE')

        # Info -- shows resolved path so artists can verify they are
        # pointing at the correct project.
        box = layout.box()
        meshes_path = get_meshes_path()
        if meshes_path:
            box.label(text=f"Path: .../{os.path.basename(meshes_path)}")
        else:
            box.label(text="Meshes folder not found", icon='ERROR')


class ASCIICKER_PT_curve_volumizer(Panel):
    """Curve volumizer panel -- bevel/extrude curves for AKM export."""
    bl_label = "Curve Volumizer"
    bl_idname = "ASCIICKER_PT_curve_volumizer"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Asciicker'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.asciicker_curve_volumizer

        layout.prop(props, "use_bevel")
        if props.use_bevel:
            layout.prop(props, "bevel_depth")
            layout.prop(props, "bevel_resolution")
        else:
            layout.prop(props, "extrude")

        layout.prop(props, "keep_original")

        col = layout.column(align=True)
        col.operator("asciicker.volumize_curves", icon='FORCE_CURVE')
        col.operator("asciicker.curves_to_mesh", icon='MESH_DATA')


class ASCIICKER_PT_vertex_colors(Panel):
    """Vertex color applier -- paint 1/2/3 random colors on meshes."""
    bl_label = "Vertex Colors"
    bl_idname = "ASCIICKER_PT_vertex_colors"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Asciicker'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.asciicker_vcolor

        layout.prop(props, "color_mode")
        layout.prop(props, "color_1")
        if props.color_mode in ('2', '3'):
            layout.prop(props, "color_2")
        if props.color_mode == '3':
            layout.prop(props, "color_3")

        layout.prop(props, "random_seed")
        layout.operator("asciicker.apply_vertex_colors", icon='BRUSH_DATA')


class ASCIICKER_PT_building_painter(Panel):
    """Building painter -- wall/window vertex color patterns."""
    bl_label = "Building Painter"
    bl_idname = "ASCIICKER_PT_building_painter"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Asciicker'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.asciicker_building_painter

        box = layout.box()
        box.label(text="Colors:")
        box.prop(props, "base_wall_color")
        box.prop(props, "window_color")

        box = layout.box()
        box.label(text="Window Pattern:")
        box.prop(props, "floor_height")
        box.prop(props, "sill_height")
        box.prop(props, "window_width")
        box.prop(props, "window_gap")
        box.prop(props, "vertical_threshold")

        layout.prop(props, "subdivision_level")
        layout.prop(props, "random_seed")

        col = layout.column(align=True)
        col.operator("asciicker.subdivide_building", icon='MOD_SUBSURF')
        col.operator("asciicker.paint_building", icon='BRUSH_DATA')


class ASCIICKER_PT_pixel_render(Panel):
    """Pixel art render settings and materials."""
    bl_label = "Pixel Art Render"
    bl_idname = "ASCIICKER_PT_pixel_render"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Asciicker'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout

        layout.operator("asciicker.pixel_render_settings", icon='SCENE')

        layout.separator()
        col = layout.column(align=True)
        col.label(text="Materials:")
        col.operator("asciicker.pixel_single_material", icon='MATERIAL')
        col.operator("asciicker.pixel_multiple_material", icon='MATERIAL')
        col.operator("asciicker.pixel_lights_setup", icon='LIGHT')


class ASCIICKER_PT_render_to_xp(Panel):
    """Render to XP -- multi-angle render and XP conversion pipeline."""
    bl_label = "Render to XP"
    bl_idname = "ASCIICKER_PT_render_to_xp"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Asciicker'

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="Render Selected Objects", icon='RENDER_ANIMATION')
        box.operator("asciicker.render_sprite_xp", icon='PLAY')

        layout.separator()
        box = layout.box()
        box.label(text="Convert Existing PNG", icon='IMAGE_DATA')
        box.operator("asciicker.convert_png_to_xp", icon='FILE_IMAGE')


class ASCIICKER_PT_gmap_bake(Panel):
    """Google Maps texture bake tools.

    Implements a multi-step workflow for projecting Google Maps 3D Tiles
    textures onto OSM-derived building meshes:

        1. **Alignment** -- Align the GMap mesh to target buildings.
        2. **Coverage Check** -- Verify texture coverage quality.
        3a. **Texture -> VCol (Direct)** -- Bake texture directly to
            vertex colors on the source mesh.
        3b. **Project GMap -> Targets** -- Full bake pipeline: UV unwrap
            targets, Cycles bake from GMap source, convert to vertex colors.
        4. **Terrain Cleanup** -- Clean GMap terrain geometry by removing
           building footprint areas.

    WHY two bake paths (3a vs 3b):
        Path 3a is simpler and works when the GMap mesh itself has good
        UVs.  Path 3b is needed when transferring textures from one mesh
        (GMap source) to a different mesh (OSM target) that may lack UVs.

    [DATA-CONTRACT:AKM] Resulting vertex colors feed into AKM export.
    [DEPENDENCY:BLENDER] Operators defined in ``io_asciicker.tools.gmap_bake_tools``.

    TODO(PIPELINE-FIX): The panel passes property values to operator props
    one-by-one (e.g. ``op.bake_resolution = props.gmap_bake_resolution``).
    This manual wiring is fragile -- if a new property is added to the
    operator but not forwarded here, the default is silently used instead.
    Consider having the operators read directly from ``scene.asciicker_props``.
    """
    bl_label = "GMap Bake"
    bl_idname = "ASCIICKER_PT_gmap_bake"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Asciicker'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        """Draw the multi-step GMap bake workflow: alignment, coverage, bake, and cleanup."""
        layout = self.layout
        props = context.scene.asciicker_props

        # Step 1: Alignment
        box = layout.box()
        box.label(text="1. Alignment", icon='ORIENTATION_GLOBAL')
        box.prop(props, "gmap_target_collection")
        row = box.row(align=True)
        op = row.operator("asciicker.align_gmap_to_targets", text="Align", icon='SNAP_ON')
        op.target_collection = props.gmap_target_collection
        op.scale_mode = 'uniform_xy'
        op_preview = row.operator("asciicker.align_gmap_to_targets", text="Preview")
        op_preview.target_collection = props.gmap_target_collection
        op_preview.preview_only = True

        layout.separator()

        # Step 2: Coverage Check
        box = layout.box()
        box.label(text="2. Coverage Check", icon='OUTLINER_OB_LIGHTPROBE')
        row = box.row(align=True)
        op = row.operator("asciicker.check_gmap_coverage", text="Check", icon='VIEWZOOM')
        op.target_collection = props.gmap_target_collection
        op.visualize = True

        layout.separator()

        # Step 3a: Texture -> VCol (direct) -- simpler path for meshes
        # that already have good UVs on the source geometry.
        box = layout.box()
        box.label(text="3a. Texture -> VCol (Direct)", icon='IMAGE')
        box.prop(props, "gmap_vcol_layer")
        op = box.operator("asciicker.bake_texture_to_vcol", icon='IMAGE')
        op.layer_name = props.gmap_vcol_layer

        layout.separator()

        # Step 3b: Project GMap -> Targets (bake pipeline) -- full path
        # for projecting textures from one mesh onto another.
        box = layout.box()
        box.label(text="3b. Project GMap -> Targets", icon='MOD_UVPROJECT')
        box.prop(props, "gmap_auto_uv_unwrap")
        box.prop(props, "gmap_keep_bake_image")
        box.prop(props, "gmap_bake_resolution")
        box.prop(props, "gmap_bake_margin")
        op = box.operator("asciicker.project_gmap_to_targets", icon='RENDER_STILL')
        op.target_collection = props.gmap_target_collection
        op.auto_uv_unwrap = props.gmap_auto_uv_unwrap
        op.keep_bake_image = props.gmap_keep_bake_image
        op.bake_resolution = props.gmap_bake_resolution
        op.bake_margin = props.gmap_bake_margin

        layout.separator()

        # GMap Terrain Cleanup section
        box = layout.box()
        box.label(text="Terrain Cleanup", icon='MESH_GRID')

        col = box.column(align=True)
        col.prop(props, "gmap_source_object", text="GMap Mesh")
        col.prop(props, "gmap_footprint_object", text="Footprints")

        col.separator()
        col.operator("asciicker.cleanup_gmap_terrain", icon='BRUSH_DATA')


class ASCIICKER_PT_osm_terrain_painter(Panel):
    """OSM terrain material painting -- sends PAINT_TERRAIN_POLY commands
    to asciiid from blosm-imported OSM data (landuse, roads, parking).
    """
    bl_label = "OSM Terrain Painter"
    bl_idname = "ASCIICKER_PT_osm_terrain_painter"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Asciicker'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.asciicker_osm_painter

        # Calibration
        box = layout.box()
        box.label(text="Calibration", icon='ORIENTATION_GLOBAL')
        box.operator("asciicker.auto_calibrate_osm", icon='TRACKING')
        box.prop(props, "scale")
        row = box.row(align=True)
        row.prop(props, "offset_x")
        row.prop(props, "offset_y")

        # Material IDs
        box = layout.box()
        box.label(text="Material IDs", icon='MATERIAL')
        col = box.column(align=True)
        col.prop(props, "grass_mat")
        col.prop(props, "road_mat")
        col.prop(props, "concrete_mat")
        col.prop(props, "residential_mat")
        col.prop(props, "default_mat")

        # Settings
        box = layout.box()
        box.label(text="Settings", icon='PREFERENCES')
        box.prop(props, "simplify_tolerance")
        box.prop(props, "cmd_file")

        # Execution
        box = layout.box()
        box.label(text="Execute", icon='PLAY')
        box.operator("asciicker.scan_osm_scene", icon='VIEWZOOM')
        if props.scan_total > 0:
            box.label(text=f"{props.scan_total} objects "
                      f"({props.scan_grass}g {props.scan_road}r "
                      f"{props.scan_concrete}c {props.scan_residential}res "
                      f"{props.scan_default}def)")
        box.operator("asciicker.paint_terrain_direct", icon='BRUSH_DATA')


class ASCIICKER_PT_osm_pipeline(Panel):
    """OSM Pipeline -- full automated workflow from blosm import to engine export."""
    bl_label = "OSM Pipeline"
    bl_idname = "ASCIICKER_PT_osm_pipeline"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Asciicker'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.asciicker_osm_pipeline

        # Full pipeline button (prominent)
        box = layout.box()
        box.label(text="One-Click Pipeline", icon='PLAY')
        box.operator("asciicker.osm_full_pipeline", icon='GHOST_ENABLED')
        box.label(text="Runs all steps below in order", icon='INFO')

        # Settings
        box = layout.box()
        box.label(text="Settings", icon='PREFERENCES')
        box.prop(props, "meshes_dir")
        box.prop(props, "target_faces")
        box.prop(props, "a3d_output")

        # Individual steps
        box = layout.box()
        box.label(text="Individual Steps", icon='LINENUMBERS_ON')
        col = box.column(align=True)
        col.operator("asciicker.osm_extrude_buildings", icon='MOD_SOLIDIFY')
        col.operator("asciicker.osm_paint_buildings", icon='BRUSH_DATA')
        col.operator("asciicker.scan_osm_scene", icon='VIEWZOOM')
        col.operator("asciicker.paint_terrain_direct", icon='NODE_TEXTURE')
        col.operator("asciicker.osm_prepare_meshes", icon='EXPORT')
        col.operator("asciicker.osm_clean_scene", icon='TRASH')
        col.operator("export_scene.a3d", text="Export A3D Map", icon='FILE_TICK')


class ASCIICKER_PT_picocad_import(Panel):
    """picoCAD2 Import -- palette quantization and collision setup.

    Post-import processing for picoCAD2 GLTF models: detects the 16-color
    palette, quantizes to the terminal-safe 216-color cube, bakes to vertex
    colors, and assigns collision weights.

    [DATA-CONTRACT:AKM] Output meshes are palette-safe and collision-ready.
    [DEPENDENCY:BLENDER] Operator ``asciicker.picocad_process`` defined in
    ``io_asciicker.tools.picocad_importer``.
    """
    bl_label = "PicoCAD Import"
    bl_idname = "ASCIICKER_PT_picocad_import"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Asciicker'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        """Draw picoCAD2 processing panel: preset, batch, buttons, status, report."""
        layout = self.layout
        props = context.scene.asciicker_props

        # Collision preset dropdown
        layout.prop(props, "picocad_collision_preset", text="Collision")

        # Batch checkbox
        layout.prop(props, "picocad_batch_mode", text="Batch (all selected)")

        # Action buttons
        row = layout.row(align=True)
        row.operator("asciicker.picocad_process", text="Process Selected").do_export = False
        row.operator("asciicker.picocad_process", text="Process + Export").do_export = True

        # Status line (always visible)
        if props.picocad_status:
            layout.label(text=props.picocad_status, icon='INFO')
        else:
            layout.label(text="Select mesh(es) and click Process", icon='MESH_DATA')

        # Palette report (shown after successful processing)
        if props.picocad_palette_report:
            box = layout.box()
            box.label(text="Palette Report:")
            for line in props.picocad_palette_report.split("\n"):
                if line.strip():
                    box.label(text=line)


class ASCIICKER_PT_export(Panel):
    """Export panel -- primary interface for writing AKM and A3D files.

    Layout sections:
        1. **Quick Export** -- Prominent one-click button that runs the
           full validate-decimate-export pipeline.
        2. **Export toggles** -- ``ignore_non_manifold``, ``auto_fill_terrain``.
        3. **Mesh Tools** -- Face count display and decimation.
        4. **Manual exports** -- Separate A3D and AKM export buttons with
           finer control (selection-only, raw/no-modifiers).
        5. **Import** -- Legacy AKM import.

    [DATA-CONTRACT:AKM] ``export_mesh.akm`` writes ``.akm`` files.
    [DATA-CONTRACT:A3D] ``export_scene.a3d`` writes ``.a3d`` map files.
    [DEPENDENCY:BLENDER] Export/import operators referenced by string bl_idname.
    """
    bl_label = "Export"
    bl_idname = "ASCIICKER_PT_export"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Asciicker'

    def draw(self, context):
        """Draw quick-export button, export toggles, mesh tools, and manual export/import actions."""
        layout = self.layout

        # WHY prominent placement: Quick Export is the primary action most
        # artists use, so it gets the top spot with a PLAY icon.
        layout.operator("asciicker.quick_export", text="Quick Export & Test", icon='PLAY')

        layout.prop(context.scene.asciicker_props, "ignore_non_manifold")
        layout.prop(context.scene.asciicker_props, "auto_fill_terrain")

        layout.separator()

        # Mesh tools -- show face count of active object for quick reference
        box = layout.box()
        box.label(text="Mesh Tools:")
        obj = context.active_object
        if obj and obj.type == 'MESH':
            box.label(text=f"Selected: {obj.name}")
            box.label(text=f"Faces: {len(obj.data.polygons):,}")
        box.operator("asciicker.reduce_mesh", text="Reduce Mesh", icon='MOD_DECIM')
        # WHY conditional: convert_buildify is only available when the
        # Buildify addon is installed alongside Asciicker.
        if hasattr(bpy.ops.asciicker, "convert_buildify"):
            box.operator("asciicker.convert_buildify", text="Convert Buildify to Mesh", icon='MESH_DATA')

        layout.separator()

        col = layout.column(align=True)

        # A3D Map export [DATA-CONTRACT:A3D]
        col.operator("export_scene.a3d", text="Export A3D Map", icon='WORLD_DATA')

        # AKM Mesh export (local space, selection default) [DATA-CONTRACT:AKM]
        akm_op = col.operator("export_mesh.akm", text="Export AKM Mesh", icon='MESH_DATA')
        akm_op.use_selection = True

        # WHY "Raw" option: Exports without applying modifiers, useful for
        # exporting the base mesh when modifiers are used for preview only.
        raw_op = col.operator("export_mesh.akm", text="Export Raw AKM (No Mods)", icon='MESH_ICOSPHERE')
        raw_op.use_selection = True
        raw_op.use_mesh_modifiers = False

        layout.separator()

        # Import section
        box = layout.box()
        box.label(text="Import:")
        box.operator("import_mesh.akm", text="Import AKM (Legacy)", icon='IMPORT')


class ASCIICKER_PT_info(Panel):
    """Help & Info panel -- comprehensive user guide.

    [DATA-CONTRACT:AKM] Documents naming conventions.
    [DATA-CONTRACT:A3D] Documents scene structure and export behavior.
    [DEPENDENCY:BLENDER] Panel subclass registered with ``bpy.utils``.
    """
    bl_label = "Help & Info"
    bl_idname = "ASCIICKER_PT_info"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Asciicker'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout

        # Overview
        box = layout.box()
        box.label(text="Asciicker Tools v2.0", icon='INFO')
        box.label(text="Blender addon for the Asciicker ASCII")
        box.label(text="game engine. Create terrain, paint")
        box.label(text="materials, place meshes, bake textures,")
        box.label(text="and export maps (.a3d) and meshes (.akm).")

        # Workflow
        box = layout.box()
        box.label(text="Typical Workflow:", icon='SORTTIME')
        box.label(text="1. Create Terrain grid")
        box.label(text="2. Paint materials (vertex paint)")
        box.label(text="3. Place/import meshes from library")
        box.label(text="4. Add EnemyGen empties for spawns")
        box.label(text="5. Quick Export (or manual A3D/AKM)")
        box.label(text="Optional: GMap bake, OSM paint,")
        box.label(text="  sprite sheets, building painter")

        # --- Sprite Sheet ---
        box = layout.box()
        box.label(text="Sprite Sheet", icon='RENDER_ANIMATION')
        col = box.column(align=True)
        col.label(text="Renders 3D objects as 2D sprite sheets")
        col.label(text="for the engine's sprite system (.xp).")
        col.separator()
        col.label(text="Add Strip: Add an animation strip (idle,")
        col.label(text="  walk, attack) with frame range + angles.")
        col.label(text="Duplicate Strip: Copy strip settings.")
        col.label(text="Move Strip: Reorder strips up/down.")
        col.label(text="Remove Strip: Delete a strip.")
        col.label(text="Add/Remove Capture: Objects to render.")
        col.label(text="Create Auto Camera: Isometric camera")
        col.label(text="  matching the engine's projection.")
        col.label(text="Create Single Sprite: Render one frame")
        col.label(text="  from current view as .xp file.")
        col.label(text="Create Sheet: Batch render all strips")
        col.label(text="  at all angles into a sprite sheet.")
        col.label(text="Pixelate Image: Downsample a rendered")
        col.label(text="  image to target pixel resolution.")
        col.label(text="Combine Sprites: Merge multiple .xp")
        col.label(text="  files into one multi-frame sprite.")

        # --- Terrain ---
        box = layout.box()
        box.label(text="Terrain", icon='MESH_GRID')
        col = box.column(align=True)
        col.label(text="Create Terrain: Generates a subdivided")
        col.label(text="  plane named 'Terrain' with vertex")
        col.label(text="  colors. Size rounds to 8-unit patches.")
        col.label(text="Enter Paint Mode: Selects terrain and")
        col.label(text="  switches to Vertex Paint mode for")
        col.label(text="  material painting.")

        # --- Material Brush ---
        box = layout.box()
        box.label(text="Material Brush", icon='BRUSH_DATA')
        col = box.column(align=True)
        col.label(text="Preset buttons set the vertex paint")
        col.label(text="brush color. The Red channel encodes")
        col.label(text="the material ID (0-255):")
        col.label(text="  0=Water, 1=Grass, 2=Dirt, 3=Stone,")
        col.label(text="  4=Sand, 5=Snow, 6=Wood, 7=Steel")
        col.label(text="Green/Blue channels = display color.")

        # --- Mesh Library ---
        box = layout.box()
        box.label(text="Mesh Library", icon='IMPORT')
        col = box.column(align=True)
        col.label(text="Browse and place .akm mesh assets.")
        col.label(text="Dropdown: Select from assets/meshes/ folder.")
        col.label(text="Refresh: Re-scan assets/meshes/ directory.")
        col.label(text="Place Mesh: Import selected .akm at")
        col.label(text="  3D cursor. Sets a3d_flags=3 and")
        col.label(text="  a3d_story_id=-1 custom properties.")
        col.label(text="Import All: Batch import every .akm")
        col.label(text="  from the assets/meshes/ folder at once.")

        # --- Curve Volumizer ---
        box = layout.box()
        box.label(text="Curve Volumizer", icon='FORCE_CURVE')
        col = box.column(align=True)
        col.label(text="Convert Blender curves into exportable")
        col.label(text="mesh geometry for the engine.")
        col.label(text="Bevel mode: Adds circular cross-section")
        col.label(text="  (depth + resolution controls).")
        col.label(text="Extrude mode: Flat extrusion along normal.")
        col.label(text="Keep Original: Preserve source curve.")
        col.label(text="Volumize Curves: Apply bevel/extrude.")
        col.label(text="Curves to Mesh: Convert to mesh object.")

        # --- Vertex Colors ---
        box = layout.box()
        box.label(text="Vertex Colors", icon='BRUSH_DATA')
        col = box.column(align=True)
        col.label(text="Randomly apply 1-3 colors to selected")
        col.label(text="mesh faces for quick vertex coloring.")
        col.label(text="Color Mode: 1, 2, or 3 color mix.")
        col.label(text="Color Pickers: Choose each color.")
        col.label(text="Random Seed: Reproducible randomness.")
        col.label(text="Apply Vertex Colors: Paint faces with")
        col.label(text="  random distribution of chosen colors.")

        # --- Building Painter ---
        box = layout.box()
        box.label(text="Building Painter", icon='HOME')
        col = box.column(align=True)
        col.label(text="Procedural window patterns on buildings.")
        col.label(text="Base Wall Color: Main wall vertex color.")
        col.label(text="Window Color: Window area color.")
        col.label(text="Floor Height: Distance between floors.")
        col.label(text="Sill Height: Window bottom offset.")
        col.label(text="Window Width/Gap: Horizontal spacing.")
        col.label(text="Vertical Threshold: Min wall angle to")
        col.label(text="  receive windows (skips roofs/floors).")
        col.label(text="Subdivision Level: Subdivide mesh for")
        col.label(text="  finer window detail resolution.")
        col.label(text="Subdivide Building: Add geometry detail.")
        col.label(text="Paint Building: Apply window pattern.")

        # --- Pixel Art Render ---
        box = layout.box()
        box.label(text="Pixel Art Render", icon='SCENE')
        col = box.column(align=True)
        col.label(text="Configure Blender for pixel art style")
        col.label(text="rendering (low-res, nearest-neighbor).")
        col.label(text="Pixel Render Settings: Set resolution,")
        col.label(text="  filter size, color management for")
        col.label(text="  crisp pixel output.")
        col.label(text="Single Material: Flat-colored material.")
        col.label(text="Multiple Material: Vertex-color-driven")
        col.label(text="  material using engine palette.")
        col.label(text="Pixel Lights: Add standard 3-point")
        col.label(text="  lighting setup for sprite renders.")

        # --- Render to XP ---
        box = layout.box()
        box.label(text="Render to XP", icon='RENDER_ANIMATION')
        col = box.column(align=True)
        col.label(text="Render 3D objects to .xp sprite files")
        col.label(text="(REXPaint format) for the engine.")
        col.label(text="Render Selected Objects: Renders at")
        col.label(text="  multiple angles, converts PNG to .xp")
        col.label(text="  with palette quantization.")
        col.label(text="Convert Existing PNG: Takes an existing")
        col.label(text="  PNG image and converts it to .xp format")
        col.label(text="  using the engine's terminal palette.")

        # --- Color Tools ---
        box = layout.box()
        box.label(text="Color Tools", icon='COLOR')
        col = box.column(align=True)
        col.label(text="Palette and collision flag utilities.")
        col.label(text="Snap to Palette: Quantize all vertex")
        col.label(text="  colors to the 6x6x6 terminal palette")
        col.label(text="  (values: 0, 51, 102, 153, 204, 255).")
        col.label(text="Analyze Colors: Report how many colors")
        col.label(text="  are off-palette and need snapping.")
        col.label(text="Collision Solid (Edit Mode): Mark")
        col.label(text="  selected faces as physics-solid.")
        col.label(text="Collision Pass (Edit Mode): Mark faces")
        col.label(text="  as passthrough (alpha channel flag).")

        # --- Camera ---
        box = layout.box()
        box.label(text="Camera", icon='VIEW_CAMERA')
        col = box.column(align=True)
        col.label(text="Quick viewport navigation shortcuts.")
        col.label(text="Zoom Out: Pull camera back for overview.")
        col.label(text="Frame Terrain: Fit terrain in viewport.")
        col.label(text="Top View: Switch to top-down orthographic")
        col.label(text="  view for terrain editing.")

        # --- GMap Bake ---
        box = layout.box()
        box.label(text="GMap Bake", icon='MOD_UVPROJECT')
        col = box.column(align=True)
        col.label(text="Project Google Maps 3D Tiles textures")
        col.label(text="onto OSM building meshes via Cycles bake,")
        col.label(text="then convert to engine vertex colors.")
        col.separator()
        col.label(text="1. Align: Match GMap mesh position/scale")
        col.label(text="   to target buildings. Preview first.")
        col.label(text="2. Coverage Check: Verify texture quality")
        col.label(text="   on targets before baking.")
        col.label(text="3a. Texture->VCol (Direct): Bake texture")
        col.label(text="   to vertex colors on the source mesh.")
        col.label(text="3b. Project GMap->Targets: Full pipeline")
        col.label(text="   UV unwrap, Cycles bake from source")
        col.label(text="   mesh to target mesh, convert to vcol.")
        col.label(text="4. Terrain Cleanup: Remove building")
        col.label(text="   footprint areas from GMap terrain mesh.")
        col.separator()
        col.label(text="Settings: Target collection, bake res,")
        col.label(text="  margin, UV unwrap, keep bake image.")

        # --- OSM Terrain Painter ---
        box = layout.box()
        box.label(text="OSM Terrain Painter", icon='WORLD')
        col = box.column(align=True)
        col.label(text="Paint engine terrain materials from blosm")
        col.label(text="OSM data (landuse, roads, buildings).")
        col.label(text="Outputs PAINT_TERRAIN_POLY commands to")
        col.label(text="a cmd file read by the asciiid editor.")
        col.separator()
        col.label(text="Auto Calibrate: Match blosm coords to")
        col.label(text="  engine coords using building footprints.")
        col.label(text="  Computes scale + offset from matched")
        col.label(text="  A3D instances vs blosm buildings.")
        col.label(text="Scale/Offset: Manual calibration override.")
        col.label(text="Material IDs: Map OSM categories to")
        col.label(text="  engine material IDs (grass, road, etc).")
        col.label(text="Simplify Tolerance: Douglas-Peucker")
        col.label(text="  epsilon for polygon vertex reduction.")
        col.label(text="Cmd File: Output path for MCP commands.")
        col.label(text="Scan Scene: Count blosm OSM objects by")
        col.label(text="  category (grass/road/concrete/etc).")
        col.label(text="Paint Terrain: Generate PAINT_TERRAIN_POLY")
        col.label(text="  commands for all scanned OSM polygons.")

        # --- Export ---
        box = layout.box()
        box.label(text="Export", icon='EXPORT')
        col = box.column(align=True)
        col.label(text="Quick Export & Test: Full automated")
        col.label(text="  pipeline -- validate, auto-decimate,")
        col.label(text="  export all AKMs + A3D map, copy to")
        col.label(text="  game_map_y8.a3d for playtesting.")
        col.label(text="Ignore Non-Manifold: Skip manifold check.")
        col.label(text="Auto-Fill Terrain: Default grass material")
        col.label(text="  on untextured terrain faces.")
        col.separator()
        col.label(text="Mesh Tools:")
        col.label(text="  Reduce Mesh: Decimate active object to")
        col.label(text="  a target face count (Collapse modifier).")
        col.label(text="  Convert Buildify: Convert Buildify")
        col.label(text="  generated objects to plain mesh.")
        col.separator()
        col.label(text="Manual Export:")
        col.label(text="  Export A3D Map: File > Export dialog")
        col.label(text="  for .a3d scene files (terrain +")
        col.label(text="  instances + enemy generators).")
        col.label(text="  Export AKM Mesh: Export selected mesh")
        col.label(text="  to .akm with engine vertex format.")
        col.label(text="  Export Raw AKM: No modifiers applied.")
        col.label(text="  Import AKM: Load a .akm mesh file.")

        # --- File Menu ---
        box = layout.box()
        box.label(text="File Menu (Import/Export):", icon='FILE')
        col = box.column(align=True)
        col.label(text="File > Import > Asciicker Map (.a3d)")
        col.label(text="  Import terrain, mesh instances, and")
        col.label(text="  enemy generators from .a3d map file.")
        col.label(text="  Options: import terrain, instances,")
        col.label(text="  enemy gens, mesh search paths.")
        col.label(text="File > Export > Asciicker Map (.a3d)")
        col.label(text="  Export scene to .a3d map file.")
        col.label(text="File > Import/Export > AKM Mesh")
        col.label(text="  Import/export individual .akm meshes.")

        # Naming conventions
        box = layout.box()
        box.label(text="Naming Conventions:", icon='SORTALPHA')
        col = box.column(align=True)
        col.label(text="Terrain: must be named 'Terrain'")
        col.label(text="Meshes: object name becomes .akm ref")
        col.label(text="  (tree-1 -> tree-1.akm)")
        col.label(text="Spawns: Empty named 'EnemyGen*'")
        col.label(text="Materials: Red channel = ID (0-255)")
        col.label(text="Collision: Alpha channel = solid/pass")
        col.label(text="Colors: 6x6x6 terminal palette only")

        # Troubleshooting
        box = layout.box()
        box.label(text="Troubleshooting:", icon='ERROR')
        col = box.column(align=True)
        col.label(text="'No Terrain found': Create one first")
        col.label(text="'No .akm files': Check assets/meshes/ path")
        col.label(text="0 instances exported: Check scene has")
        col.label(text="  mesh objects (not just terrain)")
        col.label(text="A3D import missing: Addon must be")
        col.label(text="  enabled (Prefs > Add-ons > Asciicker)")
        col.label(text="OSM paint no output: Check cmd_file")
        col.label(text="  path is writable, run Scan first")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

# WHY tuple ordering matters: Blender registers Panel classes in this order,
# which determines their top-to-bottom position in the sidebar tab.
# PropertyGroup and Operators must be registered *before* Panels that
# reference them (though here the panels only reference operators by string
# bl_idname, so the ordering is more about sidebar layout aesthetics).
classes = (
    AsciickerProperties,
    ASCIICKER_OT_quick_export,
    ASCIICKER_OT_one_click_export,
    ASCIICKER_OT_place_mesh,
    ASCIICKER_OT_import_all_meshes,
    ASCIICKER_OT_refresh_meshes,
    ASCIICKER_OT_reduce_mesh,
    ASCIICKER_PT_terrain,
    ASCIICKER_PT_materials,
    ASCIICKER_PT_mesh_library,
    ASCIICKER_PT_curve_volumizer,
    ASCIICKER_PT_vertex_colors,
    ASCIICKER_PT_building_painter,
    ASCIICKER_PT_pixel_render,
    ASCIICKER_PT_render_to_xp,
    ASCIICKER_PT_colors,
    ASCIICKER_PT_camera,
    ASCIICKER_PT_gmap_bake,
    ASCIICKER_PT_osm_terrain_painter,
    ASCIICKER_PT_osm_pipeline,
    ASCIICKER_PT_picocad_import,
    ASCIICKER_PT_export,
    ASCIICKER_PT_info,
)


def register():
    """Register all Asciicker UI classes and attach scene properties.

    WHY ``PointerProperty``: Attaching ``AsciickerProperties`` to
    ``bpy.types.Scene`` ensures one property group per scene, persisted
    with the .blend file.

    [DEPENDENCY:BLENDER] ``bpy.utils.register_class`` and
    ``bpy.types.Scene`` are Blender Python API entrypoints.
    """
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.asciicker_props = bpy.props.PointerProperty(type=AsciickerProperties)


def unregister():
    """Unregister all Asciicker UI classes and remove scene properties.

    WHY reversed: Blender requires LIFO order for class unregistration to
    avoid dependency errors (panels that reference property groups must be
    removed before the property group itself).

    [DEPENDENCY:BLENDER] ``bpy.utils.unregister_class``.
    """
    del bpy.types.Scene.asciicker_props
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
