# Asciicker Color Tools
# Palette snapping, dithering analysis, collision marking
# [DEPENDENCY:BLENDER] - Operators registered via bpy.utils.register_class

"""
Asciicker Color Tools -- Palette Snapping, Analysis, and Collision Marking
===========================================================================

ARCHITECTURE:
    This module provides four Blender operators split into two functional
    groups:

    **Palette / Dither** (vertex-color quality):
        - ``ASCIICKER_OT_snap_colors``    -- Quantize vertex colors to the
          6x6x6 terminal-safe palette.  Colors outside this palette are
          *dithered* by the engine at runtime which produces grainy
          checkerboard artifacts.
        - ``ASCIICKER_OT_analyze_colors`` -- Read-only audit: count how many
          corners already sit on the palette and how many would require
          dithering.

    **Collision** (physics markup):
        - ``ASCIICKER_OT_set_collision_solid``       -- Mark selected vertices
          as *solid* (collision weight = 0.0).
        - ``ASCIICKER_OT_set_collision_passthrough``  -- Mark selected vertices
          as *passthrough* (collision weight = 1.0).

KEY EXPORTS:
    - ``SAFE_LEVELS``            -- The six quantization levels [0, 51, 102,
      153, 204, 255] defining the 6x6x6 cube.
    - ``rgb_to_terminal``        -- Map an RGB triplet to a terminal 256-color
      palette index (indices 16-231).
    - ``snap_to_palette``        -- Snap a single 0-255 channel value.
    - ``get_color_layer``        -- Cross-version helper for Blender 3.x/4.x
      vertex color access.
    - ``_get_item_color``        -- Read RGBA from any vertex-color element.
    - ``_set_item_color``        -- Write RGBA to any vertex-color element.

PIPELINE CONTEXT:
    [DATA-CONTRACT:AKM]
    The AKM exporter writes vertex colors as-is.  If the colors are not on
    the 6x6x6 palette, the C++ terminal renderer applies ordered dithering
    at display time, which degrades visual quality.  Running ``snap_colors``
    before export eliminates this.

    The ``collision`` vertex group is read by the AKM exporter:
    weight 0.0 = solid (blocks player movement), weight 1.0 = passthrough.
"""

import bpy
from bpy.types import Operator

# WHY these specific values: the terminal 256-color palette uses a 6x6x6 RGB
# cube starting at index 16.  Each axis has 6 levels spaced 51 apart
# (0, 51, 102, 153, 204, 255).  Colors on these exact levels need no
# dithering and render crisply in the terminal.
SAFE_LEVELS = [0, 51, 102, 153, 204, 255]


def rgb_to_terminal(r, g, b):
    """Convert RGB (0-255) to terminal 256-color palette index (16-231).

    The formula maps each channel to one of 6 levels via integer division,
    then computes the linear index into the 6x6x6 cube starting at offset 16.

    Args:
        r, g, b: Integer channel values in [0, 255].

    Returns:
        int: Terminal palette index in [16, 231].
    """
    # WHY +25: rounding to nearest level.  51/2 = 25.5, so adding 25 before
    # integer-dividing by 51 gives correct nearest-neighbor quantization.
    r6 = (r + 25) // 51
    g6 = (g + 25) // 51
    b6 = (b + 25) // 51
    return 16 + 36 * r6 + 6 * g6 + b6


def snap_to_palette(value):
    """Snap a single channel value (0-255) to nearest palette-safe level.

    Args:
        value: Integer channel value in [0, 255].

    Returns:
        int: Nearest value from ``SAFE_LEVELS``.
    """
    return min(SAFE_LEVELS, key=lambda x: abs(x - value))


def get_color_layer(mesh):
    """Get the active vertex-color layer (Blender 3.x/4.x compatible).

    Blender 4.x uses ``mesh.color_attributes``; 3.x uses ``mesh.vertex_colors``.
    This helper tries 4.x first, falls back to 3.x.

    Args:
        mesh: Blender ``Mesh`` data block.

    Returns:
        The active color attribute/layer, or ``None`` if none exists.
    """
    if hasattr(mesh, 'color_attributes') and mesh.color_attributes:
        active = getattr(mesh.color_attributes, "active_color", None)
        if active:
            return active
        # Fallback: return first BYTE_COLOR or FLOAT_COLOR attribute found
        for attr in mesh.color_attributes:
            if attr.data_type in ('BYTE_COLOR', 'FLOAT_COLOR'):
                return attr
        return None
    if hasattr(mesh, 'vertex_colors') and mesh.vertex_colors:
        return mesh.vertex_colors.active
    return None


def _get_item_color(item):
    """Read RGBA from a vertex-color element, handling API variations.

    Blender 4.x BYTE_COLOR uses ``color_srgb``; FLOAT_COLOR uses ``color``;
    some legacy paths expose ``vector`` (3-component).

    Args:
        item: A single vertex-color data element.

    Returns:
        list: ``[R, G, B, A]`` as floats.
    """
    if hasattr(item, 'color_srgb'):
        return list(item.color_srgb)
    if hasattr(item, 'color'):
        return list(item.color)
    if hasattr(item, 'vector'):
        return list(item.vector) + [1.0]
    return [0.0, 0.0, 0.0, 1.0]


def _set_item_color(item, rgba):
    """Write RGBA to a vertex-color element, handling API variations.

    Args:
        item: A single vertex-color data element.
        rgba: ``[R, G, B, A]`` list/tuple of floats.
    """
    if hasattr(item, 'color_srgb'):
        item.color_srgb = rgba
        return
    if hasattr(item, 'color'):
        item.color = rgba
        return
    if hasattr(item, 'vector'):
        item.vector = rgba[:3]


class ASCIICKER_OT_snap_colors(Operator):
    """Snap vertex colors to the 6x6x6 web-safe palette.

    This ensures colors match the game engine exactly without
    needing dithering (checkerboard patterns).

    [DATA-CONTRACT:AKM] Pre-export step: the AKM exporter writes vertex colors
    as-is, so snapping here prevents runtime dithering artifacts.
    """
    bl_idname = "asciicker.snap_colors"
    bl_label = "Snap to 6x6x6 Palette"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        """Allow when active object is a mesh."""
        obj = context.active_object
        return obj and obj.type == 'MESH'

    def execute(self, context):
        """Snap all vertex colors to nearest 6x6x6 palette values.

        Iterates every corner color, quantizes R/G/B to the nearest of the
        6 safe levels, and writes the corrected color back.

        Returns:
            ``{'FINISHED'}`` on success, ``{'CANCELLED'}`` if no color layer.
        """
        obj = context.active_object
        mesh = obj.data
        vcol = get_color_layer(mesh)

        if not vcol:
            self.report({'WARNING'}, "No vertex colors found")
            return {'CANCELLED'}

        snapped_count = 0

        for item in vcol.data:
            col = _get_item_color(item)
            # TODO(PIPELINE-FIX): Uses int() (truncation) to convert float->int.
            # The AKM exporter uses round(R * 255).  Should use round() here too
            # for consistency, otherwise off-by-one mismatches are possible at
            # boundary values (e.g., 0.502 * 255 = 128.01 -> int gives 128,
            # round gives 128, but 0.498 * 255 = 126.99 -> int gives 126,
            # round gives 127).
            r = int(col[0] * 255)
            g = int(col[1] * 255)
            b = int(col[2] * 255)

            r_snap = snap_to_palette(r)
            g_snap = snap_to_palette(g)
            b_snap = snap_to_palette(b)

            if r != r_snap or g != g_snap or b != b_snap:
                col[0] = r_snap / 255.0
                col[1] = g_snap / 255.0
                col[2] = b_snap / 255.0
                _set_item_color(item, col)
                snapped_count += 1

        # WHY mesh.update(): Blender caches vertex-color data internally;
        # calling update() flushes the changes so the viewport refreshes and
        # subsequent operators see the corrected values.
        # [DEPENDENCY:BLENDER] mesh.update()
        mesh.update()
        self.report({'INFO'}, f"Snapped {snapped_count} colors to palette")
        return {'FINISHED'}


class ASCIICKER_OT_analyze_colors(Operator):
    """Check if colors match the 6x6x6 palette.
    Colors not in the palette will be 'dithered' by the engine
    (mixed with other colors) which might look grainy."""
    bl_idname = "asciicker.analyze_colors"
    bl_label = "Analyze Colors"

    @classmethod
    def poll(cls, context):
        """Allow when active object is a mesh."""
        obj = context.active_object
        return obj and obj.type == 'MESH'

    def execute(self, context):
        """Count palette-safe vs dither-needed vertex colors and report.

        Scans every corner color, checks whether each channel falls on one of
        the ``SAFE_LEVELS``, and reports the percentage that would require
        runtime dithering.

        Returns:
            ``{'FINISHED'}`` on success, ``{'CANCELLED'}`` if no color layer.
        """
        obj = context.active_object
        mesh = obj.data
        vcol = get_color_layer(mesh)

        if not vcol:
            self.report({'WARNING'}, "No vertex colors found")
            return {'CANCELLED'}

        palette_usage = {}
        dither_needed = 0
        total = len(vcol.data)

        for item in vcol.data:
            col = _get_item_color(item)
            r = int(col[0] * 255)
            g = int(col[1] * 255)
            b = int(col[2] * 255)

            idx = rgb_to_terminal(r, g, b)
            palette_usage[idx] = palette_usage.get(idx, 0) + 1

            if r not in SAFE_LEVELS or g not in SAFE_LEVELS or b not in SAFE_LEVELS:
                dither_needed += 1

        unique_colors = len(palette_usage)
        
        pct = (dither_needed / total * 100) if total > 0 else 0
        
        msg = f"{unique_colors} unique colors. {dither_needed} ({pct:.1f}%) need dithering (will look grainy)."
        self.report({'INFO'}, msg)

        return {'FINISHED'}


class ASCIICKER_OT_set_collision_solid(Operator):
    """Set selected vertices as solid (collision enabled).

    Creates or updates the ``collision`` vertex group, assigning weight
    **0.0** to each selected vertex.  Weight 0.0 means the engine treats
    the surface as solid for player collision.

    [DATA-CONTRACT:AKM] The ``collision`` vertex group is exported and read
    by the C++ physics system: weight 0.0 = solid, weight 1.0 = passthrough.

    Must be executed in Edit Mode (uses bmesh).
    """
    bl_idname = "asciicker.set_collision_solid"
    bl_label = "Set Solid"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        """Allow when active object is a mesh in Edit Mode."""
        obj = context.active_object
        return obj and obj.type == 'MESH' and obj.mode == 'EDIT'

    def execute(self, context):
        """Assign weight 0.0 (solid) to selected verts in 'collision' group.

        Returns:
            ``{'FINISHED'}`` on success.
        """
        # [DEPENDENCY:BLENDER] bmesh API for edit-mode vertex access
        import bmesh

        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)

        # Get or create the "collision" vertex group
        vg_name = "collision"
        if vg_name not in obj.vertex_groups:
            obj.vertex_groups.new(name=vg_name)
        vg_index = obj.vertex_groups[vg_name].index

        deform_layer = bm.verts.layers.deform.verify()

        count = 0
        for v in bm.verts:
            if v.select:
                # WHY 0.0: the AKM exporter interprets 0.0 as "solid" and
                # 1.0 as "passthrough".  This convention comes from the engine
                # treating the weight as a transparency/passability factor.
                v[deform_layer][vg_index] = 0.0
                count += 1

        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, f"Set {count} vertices as solid")
        return {'FINISHED'}


class ASCIICKER_OT_set_collision_passthrough(Operator):
    """Set selected vertices as passthrough (no collision).

    Creates or updates the ``collision`` vertex group, assigning weight
    **1.0** to each selected vertex.  Weight 1.0 means the engine treats
    the surface as non-solid (player can pass through).

    [DATA-CONTRACT:AKM] See ``ASCIICKER_OT_set_collision_solid`` for the
    full collision weight convention.

    Must be executed in Edit Mode (uses bmesh).
    """
    bl_idname = "asciicker.set_collision_passthrough"
    bl_label = "Set Passthrough"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        """Allow when active object is a mesh in Edit Mode."""
        obj = context.active_object
        return obj and obj.type == 'MESH' and obj.mode == 'EDIT'

    def execute(self, context):
        """Assign weight 1.0 (passthrough) to selected verts in 'collision' group.

        Returns:
            ``{'FINISHED'}`` on success.
        """
        # [DEPENDENCY:BLENDER] bmesh API for edit-mode vertex access
        import bmesh

        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)

        vg_name = "collision"
        if vg_name not in obj.vertex_groups:
            obj.vertex_groups.new(name=vg_name)
        vg_index = obj.vertex_groups[vg_name].index

        deform_layer = bm.verts.layers.deform.verify()

        count = 0
        for v in bm.verts:
            if v.select:
                v[deform_layer][vg_index] = 1.0  # 1.0 = passthrough
                count += 1

        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, f"Set {count} vertices as passthrough")
        return {'FINISHED'}


classes = (
    ASCIICKER_OT_snap_colors,
    ASCIICKER_OT_analyze_colors,
    ASCIICKER_OT_set_collision_solid,
    ASCIICKER_OT_set_collision_passthrough,
)


def register():
    """Register color tool operators with Blender.

    [DEPENDENCY:BLENDER] Uses ``bpy.utils.register_class``.
    """
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    """Unregister color tool operators (reverse order).

    [DEPENDENCY:BLENDER] Uses ``bpy.utils.unregister_class``.
    """
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
