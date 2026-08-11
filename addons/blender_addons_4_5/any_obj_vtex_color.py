
bl_info = {
    "name": "Simple Vertex Color Applier",
    "author": "Custom",
    "version": (1, 0, 0),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > VColor",
    "description": "Apply 1, 2, or 3 colors to mesh vertex colors",
    "category": "Paint",
}

import bpy
import random


def get_color_layer(mesh, name="Col"):
    if hasattr(mesh, "color_attributes"):
        while mesh.color_attributes:
            mesh.color_attributes.remove(mesh.color_attributes[0])
        return mesh.color_attributes.new(name=name, type='BYTE_COLOR', domain='CORNER')

    while mesh.vertex_colors:
        mesh.vertex_colors.remove(mesh.vertex_colors[0])
    return mesh.vertex_colors.new(name=name)


class VColorProperties(bpy.types.PropertyGroup):
    color_mode: bpy.props.EnumProperty(
        name="Color Mode",
        description="Number of colors to apply",
        items=[
            ('1', "1 Color", "Apply single color to all vertices"),
            ('2', "2 Colors", "Apply two colors randomly"),
            ('3', "3 Colors", "Apply three colors randomly"),
        ],
        default='1'
    )
    
    color_1: bpy.props.FloatVectorProperty(
        name="Color 1",
        subtype='COLOR',
        default=(1.0, 0.0, 0.0, 1.0),
        size=4,
        min=0.0,
        max=1.0
    )
    
    color_2: bpy.props.FloatVectorProperty(
        name="Color 2",
        subtype='COLOR',
        default=(0.0, 1.0, 0.0, 1.0),
        size=4,
        min=0.0,
        max=1.0
    )
    
    color_3: bpy.props.FloatVectorProperty(
        name="Color 3",
        subtype='COLOR',
        default=(0.0, 0.0, 1.0, 1.0),
        size=4,
        min=0.0,
        max=1.0
    )
    
    random_seed: bpy.props.IntProperty(
        name="Random Seed",
        description="Seed for random color selection",
        default=42,
        min=0
    )


class VCOLOR_OT_apply_colors(bpy.types.Operator):
    bl_idname = "vcolor.apply_colors"
    bl_label = "Apply Colors"
    bl_description = "Apply vertex colors to selected meshes"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        props = context.scene.vcolor_props
        
        random.seed(props.random_seed)
        
        selected_meshes = [obj for obj in context.selected_objects if obj.type == 'MESH']
        
        if not selected_meshes:
            self.report({'WARNING'}, "No mesh objects selected")
            return {'CANCELLED'}
        
        # Build color list based on mode
        colors = [(props.color_1[0], props.color_1[1], props.color_1[2], 1.0)]
        if props.color_mode == '2':
            colors.append((props.color_2[0], props.color_2[1], props.color_2[2], 1.0))
        elif props.color_mode == '3':
            colors.append((props.color_2[0], props.color_2[1], props.color_2[2], 1.0))
            colors.append((props.color_3[0], props.color_3[1], props.color_3[2], 1.0))
        
        processed = 0
        
        for obj in selected_meshes:
            mesh = obj.data

            vcol_layer = get_color_layer(mesh, name="Col")
            
            # Apply colors to each face
            for poly in mesh.polygons:
                # Pick random color index
                color_idx = random.randint(0, len(colors) - 1)
                color = colors[color_idx]
                
                # Apply to all loops in this face
                for loop_idx in poly.loop_indices:
                    vcol_layer.data[loop_idx].color = color
            
            processed += 1
        
        self.report({'INFO'}, f"Applied colors to {processed} mesh(es)")
        
        return {'FINISHED'}


class VCOLOR_PT_panel(bpy.types.Panel):
    bl_label = "Vertex Colors"
    bl_idname = "VCOLOR_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "VColor"
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.vcolor_props
        
        layout.prop(props, "color_mode", expand=True)
        
        layout.separator()
        
        layout.prop(props, "color_1")
        
        if props.color_mode in ['2', '3']:
            layout.prop(props, "color_2")
        
        if props.color_mode == '3':
            layout.prop(props, "color_3")
        
        layout.separator()
        layout.prop(props, "random_seed")
        
        layout.separator()
        layout.operator("vcolor.apply_colors", icon='BRUSH_DATA')


classes = (
    VColorProperties,
    VCOLOR_OT_apply_colors,
    VCOLOR_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    
    bpy.types.Scene.vcolor_props = bpy.props.PointerProperty(
        type=VColorProperties
    )


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    
    del bpy.types.Scene.vcolor_props


if __name__ == "__main__":
    register()
