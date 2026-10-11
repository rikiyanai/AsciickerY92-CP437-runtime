# Curve Volumizer -- ensure 2D road curves have volume for AKM export
# Ported from standalone akm_curve_volumizer.py
# [DEPENDENCY:BLENDER] - Requires Blender 4.0+

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty


class ASCIICKER_CurveVolumizerProperties(bpy.types.PropertyGroup):
    use_bevel: BoolProperty(
        name="Use Bevel",
        description="Use bevel depth mode instead of extrude",
        default=True,
    )
    bevel_depth: FloatProperty(
        name="Bevel Depth",
        description="Depth of the bevel for curve volumization",
        default=0.1,
        min=0.0,
        soft_max=1.0,
        unit='LENGTH',
    )
    bevel_resolution: IntProperty(
        name="Bevel Resolution",
        description="Resolution of the bevel (number of segments)",
        default=2,
        min=0,
        max=32,
    )
    extrude: FloatProperty(
        name="Extrude",
        description="Extrusion amount for curve volumization",
        default=0.1,
        min=0.0,
        soft_max=1.0,
        unit='LENGTH',
    )
    keep_original: BoolProperty(
        name="Keep Original",
        description="Keep original curve when converting to mesh",
        default=False,
    )


class ASCIICKER_OT_volumize_curves(bpy.types.Operator):
    """Volumize selected curve objects for AKM export"""
    bl_idname = "asciicker.volumize_curves"
    bl_label = "Volumize Curves"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.asciicker_curve_volumizer
        selected_curves = [obj for obj in context.selected_objects if obj.type == 'CURVE']
        if not selected_curves:
            self.report({'WARNING'}, "No curve objects selected")
            return {'CANCELLED'}

        processed = 0
        for obj in selected_curves:
            curve = obj.data
            curve.dimensions = '3D'
            curve.fill_mode = 'FULL'
            if props.use_bevel:
                curve.bevel_depth = props.bevel_depth
                curve.bevel_resolution = props.bevel_resolution
                curve.extrude = 0.0
            else:
                curve.extrude = props.extrude
                curve.bevel_depth = 0.0
            processed += 1

        self.report({'INFO'}, f"Volumized {processed} curve(s)")
        return {'FINISHED'}


class ASCIICKER_OT_curves_to_mesh(bpy.types.Operator):
    """Convert volumized curves to mesh objects"""
    bl_idname = "asciicker.curves_to_mesh"
    bl_label = "Convert Curves to Mesh"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.asciicker_curve_volumizer
        selected_curves = [obj for obj in context.selected_objects if obj.type == 'CURVE']
        if not selected_curves:
            self.report({'WARNING'}, "No curve objects selected")
            return {'CANCELLED'}

        converted = 0
        new_objects = []
        bpy.ops.object.select_all(action='DESELECT')

        for obj in selected_curves:
            if props.keep_original:
                obj_copy = obj.copy()
                obj_copy.data = obj.data.copy()
                context.collection.objects.link(obj_copy)
                target_obj = obj_copy
            else:
                target_obj = obj

            target_obj.select_set(True)
            context.view_layer.objects.active = target_obj
            bpy.ops.object.convert(target='MESH')
            new_objects.append(target_obj)
            target_obj.select_set(False)
            converted += 1

        for obj in new_objects:
            obj.select_set(True)
        if new_objects:
            context.view_layer.objects.active = new_objects[0]

        self.report({'INFO'}, f"Converted {converted} curve(s) to mesh")
        return {'FINISHED'}


classes = (
    ASCIICKER_CurveVolumizerProperties,
    ASCIICKER_OT_volumize_curves,
    ASCIICKER_OT_curves_to_mesh,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.asciicker_curve_volumizer = bpy.props.PointerProperty(
        type=ASCIICKER_CurveVolumizerProperties,
    )


def unregister():
    del bpy.types.Scene.asciicker_curve_volumizer
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
