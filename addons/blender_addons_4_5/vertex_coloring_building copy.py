bl_info = {
    "name": "AKM Road Solidify",
    "author": "Custom",
    "version": (1, 0, 0),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > AKM",
    "description": "Add thickness to flat road meshes",
    "category": "Mesh",
}

import bpy


class AKMSolidifyProperties(bpy.types.PropertyGroup):
    thickness: bpy.props.FloatProperty(
        name="Thickness",
        description="Solidify modifier thickness",
        default=0.2,
        min=0.001,
        max=100.0
    )
    
    flat_epsilon: bpy.props.FloatProperty(
        name="Flat Epsilon",
        description="Max Z difference to consider mesh flat",
        default=0.01,
        min=0.0001,
        max=1.0
    )


class AKM_OT_solidify_roads(bpy.types.Operator):
    bl_idname = "akm.solidify_roads"
    bl_label = "Solidify Selected Mesh Roads"
    bl_description = "Add thickness to flat road meshes"
    bl_options = {'REGISTER', 'UNDO'}
    
    # Road keywords to match
    ROAD_KEYWORDS = ['road', 'street', 'path', 'highway', 'lane', 'way']
    
    def execute(self, context):
        props = context.scene.akm_solidify_props
        
        selected_meshes = [obj for obj in context.selected_objects if obj.type == 'MESH']
        
        if not selected_meshes:
            self.report({'WARNING'}, "No mesh objects selected")
            return {'CANCELLED'}
        
        processed = 0
        skipped_not_road = 0
        skipped_not_flat = 0
        failed_apply = 0
        
        for obj in selected_meshes:
            # Check if object is a road
            if not self.is_road_object(obj):
                skipped_not_road += 1
                continue
            
            # Check if mesh is flat
            if not self.is_flat_mesh(obj, props.flat_epsilon):
                skipped_not_flat += 1
                print(f"Skipped {obj.name}: not flat")
                continue
            
            # Remove existing AKM_Solidify modifier if present
            for mod in obj.modifiers:
                if mod.name == "AKM_Solidify":
                    obj.modifiers.remove(mod)
            
            # Add Solidify modifier
            modifier = obj.modifiers.new(name="AKM_Solidify", type='SOLIDIFY')
            modifier.thickness = props.thickness
            
            # Try to apply the modifier
            # Save current active object
            old_active = context.view_layer.objects.active
            
            # Set this object as active
            context.view_layer.objects.active = obj
            
            try:
                bpy.ops.object.modifier_apply(modifier="AKM_Solidify")
                processed += 1
            except:
                # Apply failed, leave modifier in place
                print(f"Could not apply modifier to {obj.name}, left in place")
                failed_apply += 1
            
            # Restore active object
            context.view_layer.objects.active = old_active
        
        # Report results
        message = f"Processed {processed} road(s)"
        if failed_apply > 0:
            message += f", {failed_apply} modifier(s) not applied"
        if skipped_not_road > 0:
            message += f", skipped {skipped_not_road} non-road mesh(es)"
        if skipped_not_flat > 0:
            message += f", skipped {skipped_not_flat} non-flat mesh(es)"
        
        self.report({'INFO'}, message)
        
        return {'FINISHED'}
    
    def is_road_object(self, obj):
        """Check if object or its materials contain road keywords"""
        # Check object name
        obj_name_lower = obj.name.lower()
        for keyword in self.ROAD_KEYWORDS:
            if keyword in obj_name_lower:
                return True
        
        # Check material names
        if obj.data.materials:
            for mat in obj.data.materials:
                if mat and keyword in mat.name.lower():
                    return True
        
        return False
    
    def is_flat_mesh(self, obj, epsilon):
        """Check if mesh is flat by Z-bounds"""
        mesh = obj.data
        
        if len(mesh.vertices) == 0:
            return False
        
        mat = obj.matrix_world
        
        # Get Z coordinates in world space
        z_coords = []
        for vert in mesh.vertices:
            v_local = vert.co
            z_world = mat[2][0] * v_local.x + mat[2][1] * v_local.y + mat[2][2] * v_local.z + mat[2][3]
            z_coords.append(z_world)
        
        min_z = min(z_coords)
        max_z = max(z_coords)
        
        z_diff = max_z - min_z
        
        return z_diff <= epsilon


class AKM_PT_solidify_panel(bpy.types.Panel):
    bl_label = "Road Solidify"
    bl_idname = "AKM_PT_solidify_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AKM"
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.akm_solidify_props
        
        layout.label(text="Settings:")
        layout.prop(props, "thickness")
        layout.prop(props, "flat_epsilon")
        
        layout.separator()
        layout.operator("akm.solidify_roads", icon='MOD_SOLIDIFY')


classes = (
    AKMSolidifyProperties,
    AKM_OT_solidify_roads,
    AKM_PT_solidify_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    
    bpy.types.Scene.akm_solidify_props = bpy.props.PointerProperty(
        type=AKMSolidifyProperties
    )


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    
    del bpy.types.Scene.akm_solidify_props


if __name__ == "__main__":
    register()
