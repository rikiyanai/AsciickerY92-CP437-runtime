# Asciicker Color Grading Tools for Blender
# Helps artists work within the terminal 6x6x6 palette constraints

import bpy
from bpy.props import FloatVectorProperty, BoolProperty, EnumProperty
from bpy.types import Panel, Operator

# Palette-safe RGB values (no dithering needed)
SAFE_LEVELS = [0, 51, 102, 153, 204, 255]

def rgb_to_terminal(r, g, b):
    """Convert RGB (0-255) to terminal 6x6x6 palette index"""
    r6 = (r + 25) // 51
    g6 = (g + 25) // 51
    b6 = (b + 25) // 51
    return 16 + 36 * r6 + 6 * g6 + b6

def terminal_to_rgb(index):
    """Convert terminal palette index (16-231) to RGB"""
    index -= 16
    r = (index // 36) * 51
    g = ((index % 36) // 6) * 51
    b = (index % 6) * 51
    return r, g, b

def snap_to_palette(value):
    """Snap a single channel (0-255) to nearest palette-safe value"""
    return min(SAFE_LEVELS, key=lambda x: abs(x - value))

def get_dither_glyph(blend_ratio):
    """Get expected dithering glyph for a blend ratio"""
    if blend_ratio < 0.08:
        return ' '  # 0%
    elif blend_ratio < 0.25:
        return '.'  # 17%
    elif blend_ratio < 0.58:
        return ':'  # 33%
    else:
        return '%'  # 83%


class ASCIICKER_OT_snap_colors_to_palette(Operator):
    """Snap all vertex colors to palette-safe values (0,51,102,153,204,255)"""
    bl_idname = "asciicker.snap_colors_to_palette"
    bl_label = "Snap to Palette"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'MESH' and obj.data.vertex_colors

    def execute(self, context):
        obj = context.active_object
        mesh = obj.data

        if not mesh.vertex_colors:
            self.report({'WARNING'}, "No vertex colors found")
            return {'CANCELLED'}

        vcol = mesh.vertex_colors.active.data
        snapped_count = 0

        for loop_color in vcol:
            r = int(loop_color.color[0] * 255)
            g = int(loop_color.color[1] * 255)
            b = int(loop_color.color[2] * 255)

            r_snap = snap_to_palette(r)
            g_snap = snap_to_palette(g)
            b_snap = snap_to_palette(b)

            if r != r_snap or g != g_snap or b != b_snap:
                loop_color.color[0] = r_snap / 255.0
                loop_color.color[1] = g_snap / 255.0
                loop_color.color[2] = b_snap / 255.0
                snapped_count += 1

        mesh.update()
        self.report({'INFO'}, f"Snapped {snapped_count} vertex colors to palette")
        return {'FINISHED'}


class ASCIICKER_OT_set_collision_solid(Operator):
    """Set selected faces as solid (alpha < 128) for collision"""
    bl_idname = "asciicker.set_collision_solid"
    bl_label = "Set Solid (Collision)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'MESH' and obj.mode == 'EDIT'

    def execute(self, context):
        import bmesh
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)

        # Get or create vertex group for collision
        vg_name = "collision"
        if vg_name not in obj.vertex_groups:
            obj.vertex_groups.new(name=vg_name)
        vg_index = obj.vertex_groups[vg_name].index

        # Set weight to 0 (solid) for selected verts
        deform_layer = bm.verts.layers.deform.verify()

        count = 0
        for v in bm.verts:
            if v.select:
                v[deform_layer][vg_index] = 0.0  # 0 = solid
                count += 1

        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, f"Set {count} vertices as solid")
        return {'FINISHED'}


class ASCIICKER_OT_set_collision_passthrough(Operator):
    """Set selected faces as passthrough (alpha >= 128) - no collision"""
    bl_idname = "asciicker.set_collision_passthrough"
    bl_label = "Set Passthrough (No Collision)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'MESH' and obj.mode == 'EDIT'

    def execute(self, context):
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
                v[deform_layer][vg_index] = 1.0  # 1 = passthrough (255 alpha)
                count += 1

        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, f"Set {count} vertices as passthrough")
        return {'FINISHED'}


class ASCIICKER_OT_analyze_colors(Operator):
    """Analyze vertex colors and show terminal palette distribution"""
    bl_idname = "asciicker.analyze_colors"
    bl_label = "Analyze Colors"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'MESH' and obj.data.vertex_colors

    def execute(self, context):
        obj = context.active_object
        mesh = obj.data
        vcol = mesh.vertex_colors.active.data

        palette_usage = {}
        dither_needed = 0
        total = 0

        for loop_color in vcol:
            r = int(loop_color.color[0] * 255)
            g = int(loop_color.color[1] * 255)
            b = int(loop_color.color[2] * 255)

            idx = rgb_to_terminal(r, g, b)
            palette_usage[idx] = palette_usage.get(idx, 0) + 1

            # Check if dithering will be needed
            if r not in SAFE_LEVELS or g not in SAFE_LEVELS or b not in SAFE_LEVELS:
                dither_needed += 1

            total += 1

        unique_colors = len(palette_usage)

        self.report({'INFO'},
            f"Colors: {unique_colors} unique terminal indices, "
            f"{dither_needed}/{total} will need dithering")

        return {'FINISHED'}


class ASCIICKER_PT_color_tools(Panel):
    """Asciicker Color Tools Panel"""
    bl_label = "Asciicker Colors"
    bl_idname = "ASCIICKER_PT_color_tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Asciicker'

    def draw(self, context):
        layout = self.layout
        obj = context.active_object

        # Info box
        box = layout.box()
        box.label(text="Terminal Palette: 6x6x6 (216 colors)")
        box.label(text="Safe values: 0, 51, 102, 153, 204, 255")

        layout.separator()

        # Color operations
        col = layout.column(align=True)
        col.label(text="Vertex Colors:")
        col.operator("asciicker.snap_colors_to_palette", icon='SNAP_ON')
        col.operator("asciicker.analyze_colors", icon='INFO')

        layout.separator()

        # Collision operations (only in edit mode)
        col = layout.column(align=True)
        col.label(text="Collision (Alpha Channel):")
        col.label(text="  0-127: Solid    128-255: Pass")

        if obj and obj.mode == 'EDIT':
            row = col.row(align=True)
            row.operator("asciicker.set_collision_solid", text="Solid", icon='CUBE')
            row.operator("asciicker.set_collision_passthrough", text="Pass", icon='GHOST_ENABLED')
        else:
            col.label(text="(Enter Edit Mode)")

        layout.separator()

        # Quick reference
        box = layout.box()
        box.label(text="Dithering Glyphs:")
        row = box.row()
        row.label(text="' '=0%  '.'=17%  ':'=33%  '%'=83%")


classes = (
    ASCIICKER_OT_snap_colors_to_palette,
    ASCIICKER_OT_set_collision_solid,
    ASCIICKER_OT_set_collision_passthrough,
    ASCIICKER_OT_analyze_colors,
    ASCIICKER_PT_color_tools,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
