# Building Painter -- paint buildings with vertex colors for .akm export
# Ported from standalone vertex_coloring_building.py
# [DEPENDENCY:BLENDER] - Requires Blender 4.0+

import bpy
import random
from bpy.props import FloatProperty, FloatVectorProperty, IntProperty


def _ensure_color_layer(mesh, name="Col"):
    """Create a fresh vertex-color layer, removing existing ones first."""
    if hasattr(mesh, "color_attributes"):
        while mesh.color_attributes:
            mesh.color_attributes.remove(mesh.color_attributes[0])
        return mesh.color_attributes.new(name=name, type='BYTE_COLOR', domain='CORNER')
    while mesh.vertex_colors:
        mesh.vertex_colors.remove(mesh.vertex_colors[0])
    return mesh.vertex_colors.new(name=name)


class ASCIICKER_BuildingPainterProperties(bpy.types.PropertyGroup):
    subdivision_level: IntProperty(
        name="Subdivision Level",
        description="Number of subdivision steps to apply before painting",
        default=3,
        min=0,
        max=6,
    )
    face_limit: IntProperty(
        name="Face Limit",
        description="Skip meshes with more faces than this",
        default=10000000,
        min=1,
    )
    random_seed: IntProperty(
        name="Random Seed",
        description="Seed for random color generation",
        default=42,
        min=0,
    )
    vertical_threshold: FloatProperty(
        name="Vertical Threshold",
        description="abs(normal.z) below this = vertical face",
        default=0.3,
        min=0.0,
        max=1.0,
    )
    base_wall_color: FloatVectorProperty(
        name="Base Wall Color",
        subtype='COLOR',
        default=(0.8, 0.75, 0.7, 1.0),
        size=4,
        min=0.0,
        max=1.0,
    )
    window_color: FloatVectorProperty(
        name="Window Color",
        subtype='COLOR',
        default=(0.2, 0.3, 0.4, 1.0),
        size=4,
        min=0.0,
        max=1.0,
    )
    floor_height: FloatProperty(
        name="Floor Height",
        description="Height between floors (world units)",
        default=3.0,
        min=0.1,
    )
    sill_height: FloatProperty(
        name="Sill Height",
        description="Window sill height from floor",
        default=0.9,
        min=0.0,
    )
    window_width: FloatProperty(
        name="Window Width",
        description="Width of window in world units",
        default=1.2,
        min=0.1,
    )
    window_gap: FloatProperty(
        name="Window Gap",
        description="Gap between windows",
        default=0.8,
        min=0.1,
    )


def _paint_building(obj, vcol_layer, props):
    """Paint a building mesh with wall/window pattern based on vertex positions."""
    mesh = obj.data
    mat = obj.matrix_world

    for poly in mesh.polygons:
        normal_local = poly.normal
        nwx = mat[0][0] * normal_local.x + mat[1][0] * normal_local.y + mat[2][0] * normal_local.z
        nwy = mat[0][1] * normal_local.x + mat[1][1] * normal_local.y + mat[2][1] * normal_local.z
        nwz = mat[0][2] * normal_local.x + mat[1][2] * normal_local.y + mat[2][2] * normal_local.z

        is_vertical = abs(nwz) < props.vertical_threshold

        if is_vertical:
            for loop_idx in poly.loop_indices:
                loop = mesh.loops[loop_idx]
                vert = mesh.vertices[loop.vertex_index]
                v = vert.co
                vwx = mat[0][0] * v.x + mat[0][1] * v.y + mat[0][2] * v.z + mat[0][3]
                vwy = mat[1][0] * v.x + mat[1][1] * v.y + mat[1][2] * v.z + mat[1][3]
                vwz = mat[2][0] * v.x + mat[2][1] * v.y + mat[2][2] * v.z + mat[2][3]

                z_in_floor = vwz % props.floor_height
                window_top = props.floor_height - 0.3
                window_bottom = props.sill_height

                is_window = False
                if window_bottom <= z_in_floor <= window_top:
                    horizontal = max(abs(vwx), abs(vwy))
                    pattern_period = props.window_width + props.window_gap
                    h_in_pattern = horizontal % pattern_period
                    if h_in_pattern < props.window_width:
                        is_window = True

                color = props.window_color if is_window else props.base_wall_color
                vcol_layer.data[loop_idx].color = (color[0], color[1], color[2], 1.0)
        else:
            for loop_idx in poly.loop_indices:
                vcol_layer.data[loop_idx].color = (0.6, 0.5, 0.4, 1.0)


class ASCIICKER_OT_paint_building(bpy.types.Operator):
    """Paint selected building meshes with vertex colors"""
    bl_idname = "asciicker.paint_building"
    bl_label = "Paint Building"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.asciicker_building_painter
        random.seed(props.random_seed)

        selected_meshes = [obj for obj in context.selected_objects if obj.type == 'MESH']
        if not selected_meshes:
            self.report({'WARNING'}, "No mesh objects selected")
            return {'CANCELLED'}

        processed = 0
        skipped = 0
        for obj in selected_meshes:
            mesh = obj.data
            if len(mesh.polygons) >= props.face_limit:
                skipped += 1
                continue
            vcol_layer = _ensure_color_layer(mesh, name="Col")
            _paint_building(obj, vcol_layer, props)
            processed += 1

        msg = f"Processed {processed} mesh(es)"
        if skipped > 0:
            msg += f", skipped {skipped} (face limit)"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class ASCIICKER_OT_subdivide_building(bpy.types.Operator):
    """Apply subdivision modifier to selected meshes"""
    bl_idname = "asciicker.subdivide_building"
    bl_label = "Subdivide Building"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.asciicker_building_painter
        level = max(0, int(props.subdivision_level))

        selected_meshes = [obj for obj in context.selected_objects if obj.type == 'MESH']
        if not selected_meshes:
            self.report({'WARNING'}, "No mesh objects selected")
            return {'CANCELLED'}
        if level == 0:
            self.report({'WARNING'}, "Subdivision level is 0")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        processed = 0
        failed_apply = 0
        old_active = context.view_layer.objects.active

        for obj in selected_meshes:
            modifier = obj.modifiers.new(name="ASCIICKER_Subdivide", type='SUBSURF')
            modifier.levels = level
            modifier.render_levels = level
            modifier.subdivision_type = 'SIMPLE'
            context.view_layer.objects.active = obj
            try:
                bpy.ops.object.modifier_apply(modifier=modifier.name)
                processed += 1
            except Exception:
                failed_apply += 1

        context.view_layer.objects.active = old_active

        msg = f"Subdivided {processed} mesh(es)"
        if failed_apply > 0:
            msg += f", {failed_apply} modifier(s) not applied"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


classes = (
    ASCIICKER_BuildingPainterProperties,
    ASCIICKER_OT_paint_building,
    ASCIICKER_OT_subdivide_building,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.asciicker_building_painter = bpy.props.PointerProperty(
        type=ASCIICKER_BuildingPainterProperties,
    )


def unregister():
    del bpy.types.Scene.asciicker_building_painter
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
