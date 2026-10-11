# Asciicker Terrain Tools
# Create terrain planes with proper subdivisions
# [DEPENDENCY:BLENDER] - Operators registered via bpy.utils.register_class

"""
Asciicker Terrain Tools -- Terrain Creation and Paint-Mode Entry
=================================================================

ARCHITECTURE:
    Two Blender operators that handle terrain mesh creation and vertex-paint
    mode setup.  The terrain is a subdivided plane whose per-corner vertex
    colors encode *material IDs* (grass, stone, water, etc.) in the red
    channel.  This is the starting point for the AKM export path.

KEY EXPORTS:
    - ``ASCIICKER_OT_create_terrain``    -- Create a subdivided terrain plane
      with an 8-unit patch grid and a ``MaterialID`` vertex color layer.
    - ``ASCIICKER_OT_enter_paint_mode``  -- Enter vertex-paint mode on the
      terrain or active mesh and configure viewport shading to show vertex
      colors.
    - ``MATERIAL_DISPLAY_COLORS``        -- Dict mapping material IDs (0-7) to
      RGBA display colors used during painting.

PIPELINE CONTEXT:
    [DATA-CONTRACT:AKM]
    The ``MaterialID`` vertex color layer is the primary data channel for
    terrain materials.  The AKM exporter reads the *red channel* of each
    corner color as the quantized material index (0-255).  The green/blue
    channels carry approximate display colors for artist feedback but are
    **not** exported.

    The terrain size rounds to an 8-unit patch grid because the engine
    divides the world into 8x8 patches for LOD and culling.

TODO(PIPELINE-FIX): The MATERIAL_DISPLAY_COLORS dict is duplicated in
    material_painter.py (as 3-tuples) and here (as 4-tuples).  Should be
    consolidated into a shared constants module to avoid drift.
"""

import os

import bpy
from bpy.props import FloatProperty, IntProperty
from bpy.types import Operator

# Material display colors (for visibility while painting).
# WHY RGBA 4-tuples: Blender's BYTE_COLOR vertex color layer requires 4
# components.  The alpha channel is always 1.0 for fully opaque.
# [DATA-CONTRACT:AKM] The red channel value encodes the material index;
# green/blue are cosmetic display colors only.
MATERIAL_DISPLAY_COLORS = {
    0: (0.2, 0.4, 0.8, 1.0),    # Water - blue
    1: (0.2, 0.6, 0.2, 1.0),    # Grass - green
    2: (0.5, 0.3, 0.1, 1.0),    # Dirt - brown
    3: (0.5, 0.5, 0.5, 1.0),    # Stone - gray
    4: (0.9, 0.8, 0.5, 1.0),    # Sand - tan
    5: (0.95, 0.95, 1.0, 1.0),  # Snow - white
    6: (0.4, 0.25, 0.1, 1.0),   # Wood - dark brown
    7: (0.6, 0.6, 0.7, 1.0),    # Steel - light gray
}


class ASCIICKER_OT_create_terrain(Operator):
    """Create a terrain plane with subdivisions and vertex colors.

    The plane is snapped to an 8-unit patch grid (engine requirement) and
    initialized with a ``MaterialID`` BYTE_COLOR vertex-color layer.  Every
    corner is set to *grass* (material ID 1) by default.

    [DATA-CONTRACT:AKM] The red channel of each corner color encodes the
    material index (0-255).  ``1.0/255.0`` in the red channel represents
    material ID 1 (grass).
    """
    bl_idname = "asciicker.create_terrain"
    bl_label = "Create Terrain"
    bl_options = {'REGISTER', 'UNDO'}

    size: FloatProperty(
        name="Size",
        description="Terrain size (rounds to 8-unit patch grid)",
        default=64.0,
        min=8.0,
        max=8192.0,
    )

    subdivisions: IntProperty(
        name="Subdivisions",
        description="Subdivisions per unit (higher = more detail)",
        default=1,
        min=1,
        max=4,
    )

    def execute(self, context):
        """Create a subdivided terrain plane and initialize its vertex colors.

        Snaps the requested size to the 8-unit patch grid, adds a plane with
        the required subdivision cuts, and fills the ``MaterialID`` color layer
        with grass (material ID 1) as the default surface.

        Returns:
            ``{'FINISHED'}`` on success.
        """
        # WHY patch_size=8: The Asciicker engine divides the world into 8x8
        # terrain patches for LOD and culling.  Snapping the plane to this
        # grid ensures clean patch boundaries on export.
        patch_size = 8
        num_patches = max(1, int(self.size / patch_size))
        actual_size = num_patches * patch_size

        # WHY primitive_grid_add: bpy.ops.mesh.subdivide is capped at 101
        # cuts in Blender regardless of input, giving only 102x102=10404
        # vertices for any large terrain (spacing ~13 units for 1312-unit
        # terrain).  Road buffers of 3-8 units only catch isolated vertices
        # at that density, producing single-dot artifacts.
        # primitive_grid_add has no such cap and supports the full range
        # needed for road painting (subdivisions=1 → 1 vert/unit spacing).
        # WHY location = size/2: the engine uses a bottom-left origin, so
        # centering the grid at (size/2, size/2) places vertex (0,0) at the
        # world origin and vertex (size,size) at the top-right corner.
        grid_segs = actual_size * self.subdivisions
        max_grid_segs = int(os.environ.get("ASCIICKER_OSM_MAX_GRID_SEGS", "0") or "0")
        if max_grid_segs > 0 and grid_segs > max_grid_segs:
            grid_segs = max_grid_segs
        bpy.ops.mesh.primitive_grid_add(
            x_subdivisions=grid_segs,
            y_subdivisions=grid_segs,
            size=actual_size,
            location=(actual_size / 2, actual_size / 2, 0),
        )

        terrain = context.active_object
        terrain.name = "Terrain"
        terrain["grid_segments"] = int(grid_segs)
        terrain["grid_spacing"] = float(actual_size / grid_segs) if grid_segs else 0.0

        # [DEPENDENCY:BLENDER] Add vertex color layer (Blender 4.x compatible)
        # TODO(PIPELINE-FIX): Once Blender 3.x is no longer supported, remove
        # the legacy vertex_colors fallback branch below.
        mesh = terrain.data
        grass_color = MATERIAL_DISPLAY_COLORS[1]

        if hasattr(mesh, 'color_attributes'):
            # Blender 4.x - use color_attributes API
            vcol = None
            for attr in mesh.color_attributes:
                if attr.name == "MaterialID":
                    vcol = attr
                    break

            if vcol is None:
                vcol = mesh.color_attributes.new(
                    name="MaterialID",
                    type='BYTE_COLOR',
                    domain='CORNER'
                )

            # Make it active so the vertex-paint brush targets this layer
            mesh.color_attributes.active_color = vcol

            # WHY 1.0/255.0 in red: the red channel stores the material index
            # as a normalized float.  Material ID 1 (grass) = 1/255 ~= 0.0039.
            # The AKM exporter reads this back as round(R * 255) = 1.
            for i in range(len(vcol.data)):
                vcol.data[i].color_srgb = (1.0/255.0, grass_color[1], grass_color[2], 1.0)
        else:
            # Blender 3.x fallback - use legacy vertex_colors API
            if not mesh.vertex_colors:
                mesh.vertex_colors.new(name="MaterialID")
            vcol = mesh.vertex_colors.active

            for item in vcol.data:
                item.color = (1.0/255.0, grass_color[1], grass_color[2], 1.0)

        terrain.select_set(True)
        context.view_layer.objects.active = terrain

        self.report({'INFO'},
            f"Created terrain: {actual_size}x{actual_size} ({num_patches}x{num_patches} patches)")
        return {'FINISHED'}


class ASCIICKER_OT_enter_paint_mode(Operator):
    """Enter vertex paint mode on terrain or active mesh.

    If the active object is not a mesh, the operator falls back to searching
    for an object named ``Terrain``.  It also:

    1. Ensures a ``MaterialID`` vertex-color layer exists.
    2. Switches viewport shading to **Solid / Vertex Color** so the artist
       can see painted material IDs immediately.
    3. Disables paint masks so the brush affects all faces uniformly.
    """
    bl_idname = "asciicker.enter_paint_mode"
    bl_label = "Enter Paint Mode"
    bl_options = {'REGISTER'}

    def execute(self, context):
        """Activate the target mesh and switch Blender into vertex-paint mode.

        Resolves the paint target (active object or fallback ``Terrain``),
        ensures a ``MaterialID`` color layer exists, enters vertex-paint mode,
        and configures viewport shading so painted colors are visible.

        Returns:
            ``{'FINISHED'}`` on success, ``{'CANCELLED'}`` if no mesh is found.
        """
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            # Fallback: find terrain if no active mesh
            obj = None
            for candidate in context.scene.objects:
                if candidate.name.lower() == 'terrain' and candidate.type == 'MESH':
                    obj = candidate
                    break

        if not obj:
            self.report({'ERROR'}, "No mesh object found to paint")
            return {'CANCELLED'}

        # Select mesh and make it active
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        context.view_layer.objects.active = obj

        # [DEPENDENCY:BLENDER] Ensure MaterialID vertex color layer exists
        # before entering paint mode (handles 3.x/4.x API differences).
        mesh = obj.data
        if hasattr(mesh, 'color_attributes'):
            if not mesh.color_attributes:
                mesh.color_attributes.new(
                    name="MaterialID",
                    type='BYTE_COLOR',
                    domain='CORNER'
                )
        else:
            if not mesh.vertex_colors:
                mesh.vertex_colors.new(name="MaterialID")

        # Enter vertex paint mode
        bpy.ops.object.mode_set(mode='VERTEX_PAINT')

        # WHY set shading: without Solid + VERTEX color mode, the painted
        # vertex colors are invisible, making the painting workflow useless.
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.shading.type = 'SOLID'
                        space.shading.color_type = 'VERTEX'
                        break

        # WHY disable paint masks: with masks enabled, the brush only
        # affects selected faces/vertices which confuses new users.
        ts = context.tool_settings
        if hasattr(ts, "vertex_paint") and ts.vertex_paint:
            ts.vertex_paint.use_paint_mask = False
            ts.vertex_paint.use_paint_mask_vertex = False

        self.report({'INFO'}, "Entered vertex paint mode (Vertex Color shading)")
        return {'FINISHED'}


classes = (
    ASCIICKER_OT_create_terrain,
    ASCIICKER_OT_enter_paint_mode,
)


def register():
    """Register terrain tool operators with Blender.

    [DEPENDENCY:BLENDER] Uses ``bpy.utils.register_class``.
    """
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    """Unregister terrain tool operators (reverse order).

    [DEPENDENCY:BLENDER] Uses ``bpy.utils.unregister_class``.
    """
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
