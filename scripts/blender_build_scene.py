import bpy
import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
addons_root = os.path.join(repo_root, "addons")
for path in (addons_root, repo_root):
    if path not in sys.path:
        sys.path.insert(0, path)

def build_scene():
    # Reset
    bpy.ops.wm.read_factory_settings(use_empty=True)
    
    # Register addon (safety)
    import io_asciicker
    try: io_asciicker.register()
    except: pass
    
    # 1. Create Terrain (Flat)
    # 64x64, subdiv 1
    bpy.ops.asciicker.create_terrain(size=64, subdivisions=1)
    
    # 2. Import "Passthrough" Mesh
    # We'll use a simple cube mesh from primitives, export it as AKM, then re-import to place it?
    # Or just create a cube and setup collision.
    
    # Create Cube at (10, 0, 0) - This is where we want to test passthrough
    bpy.ops.mesh.primitive_cube_add(location=(10, 0, 1.0)) # Z=1 to sit on Z=0 terrain? No, terrain is Z=0. Cube size=2, so Z=1 is bottom at 0.
    obj = bpy.context.active_object
    obj.name = "PassMesh"
    
    # Set Passthrough (Collision)
    # Must be in edit mode
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.asciicker.set_collision_passthrough()
    bpy.ops.object.mode_set(mode='OBJECT')
    
    # Ensure 'collision' group is active for export
    if "collision" in obj.vertex_groups:
        obj.vertex_groups.active_index = obj.vertex_groups["collision"].index
        print(f"DEBUG: PassMesh active group: {obj.vertex_groups.active.name}")
        # Check weight of first vertex
        try:
            w = obj.vertex_groups["collision"].weight(0)
            print(f"DEBUG: PassMesh v[0] weight: {w}")
        except:
            print("DEBUG: PassMesh v[0] has no weight in 'collision'")
    else:
        print("DEBUG: PassMesh HAS NO 'collision' GROUP")
    
    # Must have vertex colors for collision export (as we learned)
    if not obj.data.vertex_colors:
        obj.data.vertex_colors.new(name="MaterialID")
        
    # Paint it Blue (Material 0 Water? No, let's use Material 1 Grass but painted blue visually)
    # Actually, let's paint it Material 0 (Water) to see if it shows up Blue.
    # Set Brush
    # This requires being in Paint Mode context, tricky from script.
    # Easier: Manually set vertex colors.
    vcol = obj.data.vertex_colors["MaterialID"]
    for i, loop in enumerate(vcol.data):
        # Set to Water (ID 0)
        # Red = 0. Green/Blue = Display
        # Alpha is controlled by Vertex Group 'collision', so RGB only matters for display
        loop.color = (0.0, 0.0, 1.0, 1.0) 
        
    # 3. Import "Solid" Mesh at (0, 10, 1.0) (North of origin)
    bpy.ops.mesh.primitive_cube_add(location=(0, 10, 1.0))
    obj = bpy.context.active_object
    obj.name = "SolidMesh"
    
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.asciicker.set_collision_solid()
    bpy.ops.object.mode_set(mode='OBJECT')

    # Ensure 'collision' group is active for export
    if "collision" in obj.vertex_groups:
        obj.vertex_groups.active_index = obj.vertex_groups["collision"].index
        print(f"DEBUG: SolidMesh active group: {obj.vertex_groups.active.name}")
        try:
            w = obj.vertex_groups["collision"].weight(0)
            print(f"DEBUG: SolidMesh v[0] weight: {w}")
        except:
            print("DEBUG: SolidMesh v[0] has no weight in 'collision'")
    else:
        print("DEBUG: SolidMesh HAS NO 'collision' GROUP")
    
    if not obj.data.vertex_colors:
        obj.data.vertex_colors.new(name="MaterialID")
        
    vcol = obj.data.vertex_colors["MaterialID"]
    for i, loop in enumerate(vcol.data):
        # Set to Stone (ID 3) -> RGB(0.5, 0.5, 0.5)
        # Red = 3/255.
        # Alpha is controlled by Vertex Group 'collision'
        loop.color = (3.0/255.0, 0.5, 0.5, 1.0) 

    # 4. Export AKMs
    # We need to export these as .akm files first because A3D references .akm files
    # The 'Export A3D' script does NOT auto-export meshes (it assumes they exist).
    # Wait, 'extract_instances' checks if mesh_name.endswith('.akm').
    
    # We must export "PassMesh" to "PassMesh.akm" in assets/meshes/
    meshes_dir = os.path.abspath("meshes")
    if not os.path.exists(meshes_dir):
        os.makedirs(meshes_dir)
        
    # Select PassMesh
    bpy.ops.object.select_all(action='DESELECT')
    bpy.data.objects["PassMesh"].select_set(True)
    bpy.context.view_layer.objects.active = bpy.data.objects["PassMesh"]
    
    bpy.ops.export_mesh.akm(
        filepath=os.path.join(meshes_dir, "PassMesh.akm"),
        use_colors=True,
        use_selection=True,
        apply_world_transform=False,
    )
    
    # Select SolidMesh
    bpy.ops.object.select_all(action='DESELECT')
    bpy.data.objects["SolidMesh"].select_set(True)
    bpy.context.view_layer.objects.active = bpy.data.objects["SolidMesh"]
    
    bpy.ops.export_mesh.akm(
        filepath=os.path.join(meshes_dir, "SolidMesh.akm"),
        use_colors=True,
        use_selection=True,
        apply_world_transform=False,
    )
    
    # 5. Export A3D
    # Select all for scene export
    bpy.ops.object.select_all(action='SELECT')
    
    export_path = os.path.abspath("assets/a3d/game_map_y8.a3d")
    bpy.ops.export_scene.a3d(filepath=export_path)

if __name__ == "__main__":
    build_scene()
