
bl_info = {
    "name": "Vtex Color Building Painter",
    "author": "Custom",
    "version": (1, 0, 0),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > Vtex Color Bldg",
    "description": "Paint buildings with vertex colors for .akm export",
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


class VtexColorBldgProperties(bpy.types.PropertyGroup):
    subdivision_level: bpy.props.IntProperty(
        name="Subdivision Level",
        description="Number of subdivision steps to apply before painting",
        default=2,
        min=0,
        max=6
    )
    face_limit: bpy.props.IntProperty(
        name="Face Limit",
        description="Skip meshes with more faces than this",
        default=5000,
        min=1
    )
    
    random_seed: bpy.props.IntProperty(
        name="Random Seed",
        description="Seed for random color generation",
        default=42,
        min=0
    )
    
    vertical_threshold: bpy.props.FloatProperty(
        name="Vertical Threshold",
        description="abs(normal.z) below this = vertical face",
        default=0.3,
        min=0.0,
        max=1.0
    )
    
    base_wall_color: bpy.props.FloatVectorProperty(
        name="Base Wall Color",
        subtype='COLOR',
        default=(0.8, 0.75, 0.7, 1.0),
        size=4,
        min=0.0,
        max=1.0
    )
    
    window_color: bpy.props.FloatVectorProperty(
        name="Window Color",
        subtype='COLOR',
        default=(0.2, 0.3, 0.4, 1.0),
        size=4,
        min=0.0,
        max=1.0
    )
    
    floor_height: bpy.props.FloatProperty(
        name="Floor Height",
        description="Height between floors (world units)",
        default=3.0,
        min=0.1
    )
    
    sill_height: bpy.props.FloatProperty(
        name="Sill Height",
        description="Window sill height from floor",
        default=0.9,
        min=0.0
    )
    
    window_width: bpy.props.FloatProperty(
        name="Window Width",
        description="Width of window in world units",
        default=1.2,
        min=0.1
    )
    
    window_gap: bpy.props.FloatProperty(
        name="Window Gap",
        description="Gap between windows",
        default=0.8,
        min=0.1
    )


class VTEX_OT_paint_selected(bpy.types.Operator):
    bl_idname = "vtex.paint_selected"
    bl_label = "Paint Selected"
    bl_description = "Paint selected building meshes with vertex colors"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        props = context.scene.vtex_color_bldg_props
        
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
                print(f"Skipped {obj.name}: {len(mesh.polygons)} faces >= {props.face_limit}")
                skipped += 1
                continue
            
            vcol_layer = get_color_layer(mesh, name="Col")
            
            # Paint the mesh
            self.paint_building(obj, vcol_layer, props)
            
            processed += 1
        
        if skipped > 0:
            self.report({'INFO'}, f"Processed {processed}, skipped {skipped} meshes (face limit)")
        else:
            self.report({'INFO'}, f"Processed {processed} mesh(es)")
        
        return {'FINISHED'}


class VTEX_OT_subdivide_selected(bpy.types.Operator):
    bl_idname = "vtex.subdivide_selected"
    bl_label = "Subdivide Selected"
    bl_description = "Apply a subdivision modifier to selected meshes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.vtex_color_bldg_props
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
            modifier = obj.modifiers.new(name="VTEX_Subdivide", type='SUBSURF')
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

        message = f"Subdivided {processed} mesh(es)"
        if failed_apply > 0:
            message += f", {failed_apply} modifier(s) not applied"
        self.report({'INFO'}, message)

        return {'FINISHED'}

    def paint_building(self, obj, vcol_layer, props):
        mesh = obj.data
        mat = obj.matrix_world
        
        for poly in mesh.polygons:
            # Get face normal in world space
            normal_local = poly.normal
            normal_world_x = mat[0][0] * normal_local.x + mat[1][0] * normal_local.y + mat[2][0] * normal_local.z
            normal_world_y = mat[0][1] * normal_local.x + mat[1][1] * normal_local.y + mat[2][1] * normal_local.z
            normal_world_z = mat[0][2] * normal_local.x + mat[1][2] * normal_local.y + mat[2][2] * normal_local.z
            
            is_vertical = abs(normal_world_z) < props.vertical_threshold
            
            if is_vertical:
                # Paint walls with window pattern
                for loop_idx in poly.loop_indices:
                    loop = mesh.loops[loop_idx]
                    vert = mesh.vertices[loop.vertex_index]
                    
                    # Transform vertex to world space
                    v_local = vert.co
                    vert_world_x = mat[0][0] * v_local.x + mat[0][1] * v_local.y + mat[0][2] * v_local.z + mat[0][3]
                    vert_world_y = mat[1][0] * v_local.x + mat[1][1] * v_local.y + mat[1][2] * v_local.z + mat[1][3]
                    vert_world_z = mat[2][0] * v_local.x + mat[2][1] * v_local.y + mat[2][2] * v_local.z + mat[2][3]
                    
                    # Check if vertex is in window position
                    z_in_floor = vert_world_z % props.floor_height
                    window_top = props.floor_height - 0.3
                    window_bottom = props.sill_height
                    
                    is_window = False
                    if window_bottom <= z_in_floor <= window_top:
                        horizontal = max(abs(vert_world_x), abs(vert_world_y))
                        pattern_period = props.window_width + props.window_gap
                        h_in_pattern = horizontal % pattern_period
                        if h_in_pattern < props.window_width:
                            is_window = True
                    
                    if is_window:
                        color = props.window_color
                    else:
                        color = props.base_wall_color
                    
                    vcol_layer.data[loop_idx].color = (color[0], color[1], color[2], 1.0)
            else:
                # Non-vertical faces (roofs)
                for loop_idx in poly.loop_indices:
                    vcol_layer.data[loop_idx].color = (0.6, 0.5, 0.4, 1.0)


class VTEX_PT_color_bldg_panel(bpy.types.Panel):
    bl_label = "Vtex Color Bldg"
    bl_idname = "VTEX_PT_color_bldg_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Vtex Color Bldg"
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.vtex_color_bldg_props
        
        layout.label(text="Settings:")
        layout.prop(props, "face_limit")
        layout.prop(props, "random_seed")
        layout.prop(props, "vertical_threshold")
        
        layout.separator()
        layout.label(text="Colors:")
        layout.prop(props, "base_wall_color")
        layout.prop(props, "window_color")
        
        layout.separator()
        layout.label(text="Window Pattern:")
        layout.prop(props, "floor_height")
        layout.prop(props, "sill_height")
        layout.prop(props, "window_width")
        layout.prop(props, "window_gap")

        layout.separator()
        layout.label(text="Subdivision:")
        layout.prop(props, "subdivision_level")
        layout.operator("vtex.subdivide_selected", icon='MOD_SUBSURF')
        
        layout.separator()
        layout.operator("vtex.paint_selected", icon='BRUSH_DATA')


classes = (
    VtexColorBldgProperties,
    VTEX_OT_paint_selected,
    VTEX_OT_subdivide_selected,
    VTEX_PT_color_bldg_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    
    bpy.types.Scene.vtex_color_bldg_props = bpy.props.PointerProperty(
        type=VtexColorBldgProperties
    )


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    
    del bpy.types.Scene.vtex_color_bldg_props


if __name__ == "__main__":
    register()
