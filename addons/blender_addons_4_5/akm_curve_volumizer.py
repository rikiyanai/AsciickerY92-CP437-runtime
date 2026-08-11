bl_info = {
    "name": "AKM Curve Volumizer",
    "author": "AKM Tools",
    "version": (1, 0, 0),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > AKM Tab",
    "description": "Ensure 2D road curves have volume for AKM export",
    "category": "Object",
}

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty


class AKM_OT_VolumizeCurves(bpy.types.Operator):
    """Volumize selected curve objects for AKM export"""
    bl_idname = "akm.volumize_curves"
    bl_label = "Volumize Curves"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        scene = context.scene
        akm_props = scene.akm_volumizer
        
        # Get selected curve objects
        selected_curves = [obj for obj in context.selected_objects if obj.type == 'CURVE']
        
        if not selected_curves:
            self.report({'WARNING'}, "No curve objects selected")
            return {'CANCELLED'}
        
        processed_count = 0
        
        for obj in selected_curves:
            curve = obj.data
            
            # Set to 3D and FULL fill mode
            curve.dimensions = '3D'
            curve.fill_mode = 'FULL'
            
            # Apply bevel or extrude based on user choice
            if akm_props.use_bevel:
                curve.bevel_depth = akm_props.bevel_depth
                curve.bevel_resolution = akm_props.bevel_resolution
                curve.extrude = 0.0
            else:
                curve.extrude = akm_props.extrude
                curve.bevel_depth = 0.0
            
            processed_count += 1
        
        self.report({'INFO'}, f"Volumized {processed_count} curve(s)")
        return {'FINISHED'}


class AKM_OT_ConvertCurvesToMesh(bpy.types.Operator):
    """Convert volumized curves to mesh objects"""
    bl_idname = "akm.convert_curves_to_mesh"
    bl_label = "Convert Curves to Mesh"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        scene = context.scene
        akm_props = scene.akm_volumizer
        
        # Get selected curve objects
        selected_curves = [obj for obj in context.selected_objects if obj.type == 'CURVE']
        
        if not selected_curves:
            self.report({'WARNING'}, "No curve objects selected")
            return {'CANCELLED'}
        
        converted_count = 0
        new_objects = []
        
        # Deselect all
        bpy.ops.object.select_all(action='DESELECT')
        
        for obj in selected_curves:
            # Duplicate if keep_original is enabled
            if akm_props.keep_original:
                # Duplicate the object
                obj_copy = obj.copy()
                obj_copy.data = obj.data.copy()
                context.collection.objects.link(obj_copy)
                target_obj = obj_copy
            else:
                target_obj = obj
            
            # Select and make active
            target_obj.select_set(True)
            context.view_layer.objects.active = target_obj
            
            # Convert to mesh
            bpy.ops.object.convert(target='MESH')
            
            new_objects.append(target_obj)
            target_obj.select_set(False)
            converted_count += 1
        
        # Select the new/converted objects
        for obj in new_objects:
            obj.select_set(True)
        
        if new_objects:
            context.view_layer.objects.active = new_objects[0]
        
        self.report({'INFO'}, f"Converted {converted_count} curve(s) to mesh")
        return {'FINISHED'}


class AKM_PT_CurveVolumizer(bpy.types.Panel):
    """Panel for AKM Curve Volumizer"""
    bl_label = "AKM Curve Volumizer"
    bl_idname = "AKM_PT_curve_volumizer"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'AKM'
    
    def draw(self, context):
        layout = self.layout
        scene = context.scene
        akm_props = scene.akm_volumizer
        
        # Volumize mode selection
        box = layout.box()
        box.label(text="Volumize Mode:")
        box.prop(akm_props, "use_bevel", text="Use Bevel Depth")
        
        # Bevel settings
        if akm_props.use_bevel:
            col = box.column(align=True)
            col.prop(akm_props, "bevel_depth")
            col.prop(akm_props, "bevel_resolution")
        else:
            # Extrude settings
            box.prop(akm_props, "extrude")
        
        # Main button
        layout.separator()
        layout.operator("akm.volumize_curves", icon='CURVE_DATA')
        
        # Conversion settings
        layout.separator()
        box = layout.box()
        box.label(text="Mesh Conversion:")
        box.prop(akm_props, "keep_original")
        box.operator("akm.convert_curves_to_mesh", icon='MESH_DATA')


class AKM_VolumizerProperties(bpy.types.PropertyGroup):
    """Properties for AKM Curve Volumizer"""
    
    use_bevel: BoolProperty(
        name="Use Bevel",
        description="Use bevel depth mode instead of extrude",
        default=True
    )
    
    bevel_depth: FloatProperty(
        name="Bevel Depth",
        description="Depth of the bevel for curve volumization",
        default=0.1,
        min=0.0,
        soft_max=1.0,
        unit='LENGTH'
    )
    
    bevel_resolution: IntProperty(
        name="Bevel Resolution",
        description="Resolution of the bevel (number of segments)",
        default=2,
        min=0,
        max=32
    )
    
    extrude: FloatProperty(
        name="Extrude",
        description="Extrusion amount for curve volumization",
        default=0.1,
        min=0.0,
        soft_max=1.0,
        unit='LENGTH'
    )
    
    keep_original: BoolProperty(
        name="Keep Original",
        description="Keep original curve when converting to mesh",
        default=False
    )


classes = (
    AKM_VolumizerProperties,
    AKM_OT_VolumizeCurves,
    AKM_OT_ConvertCurvesToMesh,
    AKM_PT_CurveVolumizer,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    
    bpy.types.Scene.akm_volumizer = bpy.props.PointerProperty(type=AKM_VolumizerProperties)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    
    del bpy.types.Scene.akm_volumizer


if __name__ == "__main__":
    register()
