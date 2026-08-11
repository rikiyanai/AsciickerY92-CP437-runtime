# Vertex Color Applier -- apply 1/2/3 colors to mesh vertex colors
# Ported from standalone any_obj_vtex_color.py
# [DEPENDENCY:BLENDER] - Requires Blender 4.0+

import bpy
import random
from bpy.props import EnumProperty, FloatVectorProperty, IntProperty

from . import color_tools


class ASCIICKER_VertexColorProperties(bpy.types.PropertyGroup):
    color_mode: EnumProperty(
        name="Color Mode",
        description="Number of colors to apply",
        items=[
            ('1', "1 Color", "Apply single color to all vertices"),
            ('2', "2 Colors", "Apply two colors randomly"),
            ('3', "3 Colors", "Apply three colors randomly"),
        ],
        default='1',
    )
    color_1: FloatVectorProperty(
        name="Color 1",
        subtype='COLOR',
        default=(1.0, 0.0, 0.0, 1.0),
        size=4,
        min=0.0,
        max=1.0,
    )
    color_2: FloatVectorProperty(
        name="Color 2",
        subtype='COLOR',
        default=(0.0, 1.0, 0.0, 1.0),
        size=4,
        min=0.0,
        max=1.0,
    )
    color_3: FloatVectorProperty(
        name="Color 3",
        subtype='COLOR',
        default=(0.0, 0.0, 1.0, 1.0),
        size=4,
        min=0.0,
        max=1.0,
    )
    random_seed: IntProperty(
        name="Random Seed",
        description="Seed for random color selection",
        default=42,
        min=0,
    )


def _ensure_color_layer(mesh, name="Col"):
    """Create a fresh vertex-color layer, removing existing ones first."""
    if hasattr(mesh, "color_attributes"):
        while mesh.color_attributes:
            mesh.color_attributes.remove(mesh.color_attributes[0])
        return mesh.color_attributes.new(name=name, type='BYTE_COLOR', domain='CORNER')
    while mesh.vertex_colors:
        mesh.vertex_colors.remove(mesh.vertex_colors[0])
    return mesh.vertex_colors.new(name=name)


class ASCIICKER_OT_apply_vertex_colors(bpy.types.Operator):
    """Apply vertex colors to selected meshes"""
    bl_idname = "asciicker.apply_vertex_colors"
    bl_label = "Apply Vertex Colors"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.asciicker_vcolor
        random.seed(props.random_seed)

        selected_meshes = [obj for obj in context.selected_objects if obj.type == 'MESH']
        if not selected_meshes:
            self.report({'WARNING'}, "No mesh objects selected")
            return {'CANCELLED'}

        colors = [(props.color_1[0], props.color_1[1], props.color_1[2], 1.0)]
        if props.color_mode in ('2', '3'):
            colors.append((props.color_2[0], props.color_2[1], props.color_2[2], 1.0))
        if props.color_mode == '3':
            colors.append((props.color_3[0], props.color_3[1], props.color_3[2], 1.0))

        processed = 0
        for obj in selected_meshes:
            mesh = obj.data
            vcol_layer = _ensure_color_layer(mesh, name="Col")
            for poly in mesh.polygons:
                color = colors[random.randint(0, len(colors) - 1)]
                for loop_idx in poly.loop_indices:
                    vcol_layer.data[loop_idx].color = color
            processed += 1

        self.report({'INFO'}, f"Applied colors to {processed} mesh(es)")
        return {'FINISHED'}


classes = (
    ASCIICKER_VertexColorProperties,
    ASCIICKER_OT_apply_vertex_colors,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.asciicker_vcolor = bpy.props.PointerProperty(
        type=ASCIICKER_VertexColorProperties,
    )


def unregister():
    del bpy.types.Scene.asciicker_vcolor
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
