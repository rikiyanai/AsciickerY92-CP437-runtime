# Asciicker Material Painter
# Set vertex paint brush to material preset colors
# [DEPENDENCY:BLENDER] - Operators registered via bpy.utils.register_class

"""
Asciicker Material Painter -- Brush Presets for Material-ID Painting
=====================================================================

ARCHITECTURE:
    This module provides Blender operators that configure the vertex-paint
    brush to paint specific *material IDs* onto terrain meshes.  The main
    operator (``ASCIICKER_OT_set_material_brush``) takes a ``material_id``
    integer, encodes it into the brush's red channel, and sets green/blue to
    human-friendly display colors.  Four quick-access operators (Water, Grass,
    Dirt, Stone) call the main operator with hard-coded IDs.

KEY EXPORTS:
    - ``ASCIICKER_OT_set_material_brush`` -- Set brush to any material ID.
    - ``ASCIICKER_OT_quick_water``        -- Shortcut for material ID 0.
    - ``ASCIICKER_OT_quick_grass``        -- Shortcut for material ID 1.
    - ``ASCIICKER_OT_quick_dirt``         -- Shortcut for material ID 2.
    - ``ASCIICKER_OT_quick_stone``        -- Shortcut for material ID 3.
    - ``MATERIALS``                       -- List of ``(id, name)`` tuples.
    - ``MATERIAL_DISPLAY_COLORS``         -- Dict mapping IDs to RGB tuples.
    - ``ensure_material_color_layer``     -- Helper to create/activate the
      ``MaterialID`` vertex-color layer.
    - ``ensure_vertex_color_view``        -- Helper to set viewport shading.
    - ``disable_paint_masks``             -- Helper to turn off paint masks.

PIPELINE CONTEXT:
    [DATA-CONTRACT:AKM]
    The brush color encodes the material index in the **red channel** as
    ``material_id / 255.0``.  The AKM exporter reconstructs the integer ID
    via ``round(R * 255)``.  Green and blue channels are purely cosmetic
    display colors and are not exported.

TODO(PIPELINE-FIX): MATERIAL_DISPLAY_COLORS is duplicated in
    terrain_tools.py (as 4-tuples with alpha).  Should be consolidated into a
    shared constants module.
"""

import bpy
from bpy.props import IntProperty
from bpy.types import Operator

# Material definitions: (id, human-readable name).
# IDs 0-7 are the engine's built-in terrain types.
MATERIALS = [
    (0, "Water"),
    (1, "Grass"),
    (2, "Dirt"),
    (3, "Stone"),
    (4, "Sand"),
    (5, "Snow"),
    (6, "Wood"),
    (7, "Steel"),
]

# Visual display colors (RGB, no alpha) for each material ID.
# WHY separate from terrain_tools: terrain_tools needs RGBA 4-tuples for
# vertex color init; this module needs RGB 3-tuples for the brush color.
MATERIAL_DISPLAY_COLORS = {
    0: (0.2, 0.4, 0.8),    # Water - blue
    1: (0.2, 0.6, 0.2),    # Grass - green
    2: (0.5, 0.3, 0.1),    # Dirt - brown
    3: (0.5, 0.5, 0.5),    # Stone - gray
    4: (0.9, 0.8, 0.5),    # Sand - tan
    5: (0.95, 0.95, 1.0),  # Snow - white
    6: (0.4, 0.25, 0.1),   # Wood - dark brown
    7: (0.6, 0.6, 0.7),    # Steel - light gray
}

def ensure_material_color_layer(obj):
    """Ensure a ``MaterialID`` BYTE_COLOR color attribute exists and is active.

    Creates the attribute if missing and sets it as the active color layer so
    the vertex-paint brush targets it.  Handles both Blender 4.x
    (``color_attributes``) and 3.x (``vertex_colors``) APIs.

    Args:
        obj: Blender object (must be a MESH type).

    Returns:
        The color attribute/layer, or ``None`` if *obj* is not a mesh.
    """
    if not obj or obj.type != 'MESH':
        return None

    mesh = obj.data
    if hasattr(mesh, 'color_attributes'):
        # Blender 4.x path
        vcol = None
        for attr in mesh.color_attributes:
            if attr.name == "MaterialID":
                vcol = attr
                break
        if vcol is None:
            vcol = mesh.color_attributes.new(
                name="MaterialID",
                type='BYTE_COLOR',
                domain='CORNER',
            )
        mesh.color_attributes.active_color = vcol
        return vcol

    # Blender 3.x fallback
    if not mesh.vertex_colors:
        mesh.vertex_colors.new(name="MaterialID")
    return mesh.vertex_colors.active

def ensure_vertex_color_view(context):
    """Set viewport shading to show vertex colors."""
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            for space in area.spaces:
                if space.type == 'VIEW_3D':
                    space.shading.type = 'SOLID'
                    space.shading.color_type = 'VERTEX'
                    return

def disable_paint_masks(context):
    """Ensure paint masks are off so brush affects all faces."""
    ts = context.tool_settings
    if hasattr(ts, "vertex_paint") and ts.vertex_paint:
        ts.vertex_paint.use_paint_mask = False
        ts.vertex_paint.use_paint_mask_vertex = False


class ASCIICKER_OT_set_material_brush(Operator):
    """Set vertex paint brush to a material color.

    Encodes the material ID into the brush's red channel as
    ``material_id / 255.0`` and sets the green/blue channels to the
    corresponding display color from ``MATERIAL_DISPLAY_COLORS``.

    [DATA-CONTRACT:AKM] The red channel is the authoritative material index.
    """
    bl_idname = "asciicker.set_material_brush"
    bl_label = "Set Material"
    bl_options = {'REGISTER'}

    material_id: IntProperty(
        name="Material ID",
        default=0,
        min=0,
        max=255,
    )

    def execute(self, context):
        """Configure the vertex-paint brush color for the chosen material ID."""
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'WARNING'}, "Select a mesh to paint")
            return {'CANCELLED'}

        ensure_material_color_layer(obj)

        if obj.mode != 'VERTEX_PAINT':
            bpy.ops.object.mode_set(mode='VERTEX_PAINT')

        ensure_vertex_color_view(context)
        disable_paint_masks(context)

        # Get brush from tool settings
        if not hasattr(context, 'tool_settings'):
            self.report({'WARNING'}, "No tool settings available")
            return {'CANCELLED'}

        ts = context.tool_settings
        if not hasattr(ts, 'vertex_paint') or not ts.vertex_paint:
            self.report({'WARNING'}, "Enter vertex paint mode first")
            return {'CANCELLED'}

        brush = ts.vertex_paint.brush
        if not brush:
            self.report({'WARNING'}, "No brush selected")
            return {'CANCELLED'}

        mat_id = self.material_id

        # Get display color or generate one for unknown IDs
        if mat_id in MATERIAL_DISPLAY_COLORS:
            display = MATERIAL_DISPLAY_COLORS[mat_id]
        else:
            # WHY fallback hue: for material IDs beyond the predefined set,
            # generate a hue proportional to the ID so different IDs are
            # visually distinguishable.
            display = (mat_id / 255.0, 0.5, 0.5)

        # WHY encode in red channel: the AKM exporter reads R * 255 to
        # recover the integer material index.  G/B are cosmetic only.
        brush.color = (mat_id / 255.0, display[1], display[2])

        # Get material name for user feedback
        mat_name = "Custom"
        for m_id, m_name in MATERIALS:
            if m_id == mat_id:
                mat_name = m_name
                break

        self.report({'INFO'}, f"Brush: {mat_name} (ID {mat_id})")
        return {'FINISHED'}


class ASCIICKER_OT_quick_water(Operator):
    """Set brush to Water (ID 0)"""
    bl_idname = "asciicker.quick_water"
    bl_label = "Water"
    bl_options = {'REGISTER'}

    def execute(self, context):
        """Invoke set_material_brush with Water (ID 0)."""
        return bpy.ops.asciicker.set_material_brush(material_id=0)


class ASCIICKER_OT_quick_grass(Operator):
    """Set brush to Grass (ID 1)"""
    bl_idname = "asciicker.quick_grass"
    bl_label = "Grass"
    bl_options = {'REGISTER'}

    def execute(self, context):
        """Invoke set_material_brush with Grass (ID 1)."""
        return bpy.ops.asciicker.set_material_brush(material_id=1)


class ASCIICKER_OT_quick_dirt(Operator):
    """Set brush to Dirt (ID 2)"""
    bl_idname = "asciicker.quick_dirt"
    bl_label = "Dirt"
    bl_options = {'REGISTER'}

    def execute(self, context):
        """Invoke set_material_brush with Dirt (ID 2)."""
        return bpy.ops.asciicker.set_material_brush(material_id=2)


class ASCIICKER_OT_quick_stone(Operator):
    """Set brush to Stone (ID 3)"""
    bl_idname = "asciicker.quick_stone"
    bl_label = "Stone"
    bl_options = {'REGISTER'}

    def execute(self, context):
        """Invoke set_material_brush with Stone (ID 3)."""
        return bpy.ops.asciicker.set_material_brush(material_id=3)


classes = (
    ASCIICKER_OT_set_material_brush,
    ASCIICKER_OT_quick_water,
    ASCIICKER_OT_quick_grass,
    ASCIICKER_OT_quick_dirt,
    ASCIICKER_OT_quick_stone,
)


def register():
    """Register material painter operators with Blender."""
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    """Unregister material painter operators (reverse order)."""
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
