# This script combines two major operations:
# 1. Cleaning up Google 3D Map Tiles (deduplication, merging, UV unwrapping, baking to texture)
# 2. Baking that resulting Texture to Vertex Colors (for Asciicker compatibility)

import bpy
import math
import time
import datetime
import numpy as np


def ensure_color_layer(mesh, layer_name):
    if hasattr(mesh, "color_attributes"):
        layer = mesh.color_attributes.get(layer_name)
        if not layer:
            layer = mesh.color_attributes.new(
                name=layer_name,
                type='BYTE_COLOR',
                domain='CORNER',
            )
        mesh.color_attributes.active_color = layer
        return layer

    if not mesh.vertex_colors:
        mesh.vertex_colors.new(name=layer_name)
    layer = mesh.vertex_colors.get(layer_name) or mesh.vertex_colors.new(name=layer_name)
    mesh.vertex_colors.active = layer
    return layer


def set_loop_colors(layer, colors_flat):
    if hasattr(layer, "data_type"):
        if layer.data_type == 'BYTE_COLOR' and hasattr(layer.data[0], "color_srgb"):
            layer.data.foreach_set("color_srgb", colors_flat)
            return
        if hasattr(layer.data[0], "color"):
            layer.data.foreach_set("color", colors_flat)
            return
    layer.data.foreach_set("color", colors_flat)

def run_cleanup_and_bake():
    starttime = datetime.datetime.now()
    print("Cleanup + VCol Bake started at ", starttime)

    # ==========================================
    # PART 1: GOOGLE 3D TILE CLEANUP
    # ==========================================
    
    # User Controls (Cleanup)
    bake = True
    res = 4096 # Reduced from 8192 for speed/safety, user can adjust
    rem_doub = True
    emission_shader = False 
    hide_old_data = True
    delete_old_data = False
    uv_margin = 0.001
    bake_margin = 2
    cage_extrusion = 0.01
    shade_flat = True
    
    # 1. Setup Collections
    act_coll = bpy.context.view_layer.active_layer_collection.collection

    # check if empty
    if not act_coll.objects:
        print("Error: Active collection is empty!")
        return

    comb_coll_name = f'{act_coll.name}_combined'
    clean_coll_name = f'{act_coll.name}_cleanup'

    # Create collections if they don't exist
    if comb_coll_name not in bpy.data.collections:
        comb_coll = bpy.data.collections.new(comb_coll_name)
        bpy.context.scene.collection.children.link(comb_coll)
    else:
        comb_coll = bpy.data.collections[comb_coll_name]

    if clean_coll_name not in bpy.data.collections:
        clean_coll = bpy.data.collections.new(clean_coll_name)
        bpy.context.scene.collection.children.link(clean_coll)
    else:
        clean_coll = bpy.data.collections[clean_coll_name]

    # Helper to traverse layer collections
    def recurLayerCollection(layerColl, collName):
        found = None
        if (layerColl.name == collName):
            return layerColl
        for layer in layerColl.children:
            found = recurLayerCollection(layer, collName)
            if found:
                return found

    # 2. Clean out non-mesh objects
    print("Step 0 | Cleaning non-mesh objects from active collection...")
    for ob in act_coll.objects:
        if ob.type != 'MESH':
            bpy.data.objects.remove(ob, do_unlink=True)

    # 3. Copy to Combined Collection
    print("Step 1 | Copying to Combined Collection...")
    for ob in act_coll.objects:
        obj_copy = ob.copy()
        obj_copy.name = 'Mesh Combined'
        obj_copy.data = ob.data.copy()
        try:
            comb_coll.objects.link(obj_copy)
        except RuntimeError:
            pass # Already linked

    # 4. Copy to Cleanup Collection
    print("Step 1.1 | Copying to Cleanup Collection...")
    for ob in act_coll.objects:
        obj_copy = ob.copy()
        obj_copy.name = 'Mesh Cleanup'
        obj_copy.data = ob.data.copy()
        try:
            clean_coll.objects.link(obj_copy)
        except RuntimeError:
            pass

    # 5. Join 'Combined' Objects (High Res Source)
    print("Step 2 | Joining Combined objects...")
    bpy.ops.object.select_all(action='DESELECT')
    
    # Select objects in comb_coll
    for ob in comb_coll.objects:
        ob.select_set(True)
    
    if comb_coll.objects:
        bpy.context.view_layer.objects.active = comb_coll.objects[0]
        bpy.ops.object.join()
        bpy.context.object.name = f'{act_coll.name}_combined'
        # bpy.context.object.data.name = f'{act_coll.name}_combined'
    else:
        print(f"Warning: Combined collection '{comb_coll.name}' is empty. Check if objects were copied correctly.")
        return

    # 6. Join 'Cleanup' Objects (Target Mesh)
    print("Step 3 | Joining Cleanup objects...")
    bpy.ops.object.select_all(action='DESELECT')
    for ob in clean_coll.objects:
        ob.select_set(True)
        
    if clean_coll.objects:
        bpy.context.view_layer.objects.active = clean_coll.objects[0]
        bpy.ops.object.join()
        cleanup_obj = bpy.context.object
        cleanup_obj.name = f'{act_coll.name}_cleanup'
        # cleanup_obj.data.name = f'{act_coll.name}_cleanup'
        
        # Remove Doubles
        if rem_doub:
            print('Step 3.1 | Removing doubles...')
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.mesh.remove_doubles()
            bpy.ops.object.mode_set(mode='OBJECT')
    else:
        print("Error: No objects in cleanup collection!")
        return

    # 7. Material Setup on Cleanup Mesh
    print('Step 4 | setting up materials...')
    cleanup_obj = clean_coll.objects[0] # Should only be one now
    
    # Remove existing materials
    cleanup_obj.data.materials.clear()
    
    # Create new material
    mat_name = f'{act_coll.name}_material'
    mat = bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True
    cleanup_obj.data.materials.append(mat)
    
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    
    output_node = nodes.new(type='ShaderNodeOutputMaterial')
    output_node.location = (400, 0)
    
    bsdf_node = nodes.new(type='ShaderNodeBsdfPrincipled')
    bsdf_node.location = (0, 0)
    links.new(bsdf_node.outputs['BSDF'], output_node.inputs['Surface'])
    
    # Create Image Texture
    tex_name = f'{act_coll.name}_texture'
    if tex_name in bpy.data.images:
        bpy.data.images.remove(bpy.data.images[tex_name])
        
    texture = bpy.data.images.new(name=tex_name, width=res, height=res, alpha=True)
    
    texture_node = nodes.new(type='ShaderNodeTexImage')
    texture_node.image = texture
    texture_node.location = (-400, 200)
    
    links.new(texture_node.outputs['Color'], bsdf_node.inputs['Base Color'])
    
    # Set as active for baking (Selected to Active / Bake to Texture)
    nodes.active = texture_node
    
    # 8. UV Unwrap
    print('Step 5 | UV Unwrapping...')
    bpy.context.view_layer.objects.active = cleanup_obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(island_margin=uv_margin)
    bpy.ops.uv.pack_islands(margin=uv_margin)
    bpy.ops.object.mode_set(mode='OBJECT')
    
    # ... (previous code) ...
    # 9. Bake Texture (Selected to Active)
    if bake:
        print('Step 6 | Baking Texture (Cycles)...')
        bpy.context.scene.render.engine = 'CYCLES'
        # Optimizations
        bpy.context.scene.cycles.samples = 16 
        bpy.context.scene.cycles.adaptive_threshold = 0.5
        
        # Bake Settings
        bake_type = 'DIFFUSE' # Bake Albedo/Color
        bpy.context.scene.render.bake.use_pass_direct = False
        bpy.context.scene.render.bake.use_pass_indirect = False
        bpy.context.scene.render.bake.use_pass_color = True
        bpy.context.scene.render.bake.margin = bake_margin
        
        # Selected to Active
        bpy.context.scene.render.bake.use_selected_to_active = True
        bpy.context.scene.render.bake.input_active = 0
        bpy.context.scene.render.bake.cage_extrusion = cage_extrusion
        
        # Ensure correct selection: Source (Combined) -> Active (Cleanup)
        bpy.ops.object.select_all(action='DESELECT')
        
        # Select Source(s)
        for obj in comb_coll.objects:
             obj.select_set(True)
             
        # Select Target (Active)
        cleanup_obj.select_set(True)
        bpy.context.view_layer.objects.active = cleanup_obj
        
        print(f"Baking {bake_type} from {len(comb_coll.objects)} sources to {cleanup_obj.name}...")
        
        bpy.ops.object.bake(type=bake_type)
        
        # DEBUG: Save image to check if bake worked
        import os
        debug_path = os.path.join(bpy.context.scene.render.filepath or "/tmp", f"{tex_name}_debug.png")
        try:
            texture.filepath_raw = debug_path
            texture.file_format = 'PNG'
            texture.save()
            print(f"DEBUG: Saved baked texture to {debug_path}")
        except Exception as e:
            print(f"DEBUG: Failed to save image: {e}")

        # Ensure pixels are loaded
        texture.reload() 

    # ... (collection hiding) ...

    # ==========================================
    # PART 2: BAKE TEXTURE TO VERTEX COLORS
    # ==========================================
    print("\nStarting Part 2: Baking Texture to Vertex Colors...")
    
    # We work on the cleanup_obj
    obj = cleanup_obj
    mesh = obj.data
    
    # Ensure in Object Mode
    if bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    # 1. Access Texture Data
    image = texture
    width = image.size[0]
    height = image.size[1]
    
    if not image.pixels:
        print("Error: Baked image has no pixels!")
        return
        
    print(f"Sampling from image {width}x{height}...")
    try:
        pixels = np.array(image.pixels[:], dtype=np.float32)
    except Exception as e:
        print(f"Error reading pixels: {e}")
        return
        
    pixels = pixels.reshape((height, width, 4))
    
    # DEBUG: Check pixel stats
    avg_col = np.mean(pixels, axis=(0,1))
    print(f"DEBUG: Image Average Color: {avg_col}")
    if np.all(pixels == 0):
        print("WARNING: Image is completely black/transparent!")
    
    # 2. Get UVs
    layer_name = "Col"
    
    # Create or get Vertex Color Attribute
    vcol_layer = ensure_color_layer(mesh, layer_name)
    if hasattr(vcol_layer, "active_render"):
        vcol_layer.active_render = True

    loop_count = len(mesh.loops)
    
    # Extract UVs using foreach_get for speed
    uv_layer = mesh.uv_layers.active.data
    uvs = np.zeros(loop_count * 2, dtype=np.float32)
    uv_layer.foreach_get("uv", uvs)
    uvs = uvs.reshape((loop_count, 2))
    
    # 3. Map UVs to Pixel Coordinates
    us = uvs[:, 0]
    vs = uvs[:, 1]
    
    # Handle NaNs
    nan_mask = np.isnan(us) | np.isnan(vs)
    us[nan_mask] = 0.0
    vs[nan_mask] = 0.0
    
    # Wrap UVs
    us = us % 1.0
    vs = vs % 1.0
    
    # Scale to Image Size
    xs = (us * width).astype(np.int32)
    ys = (vs * height).astype(np.int32)
    
    # Clamp
    xs = np.clip(xs, 0, width - 1)
    ys = np.clip(ys, 0, height - 1)
    
    # 4. Sample Pixels
    # pixels is (H, W, 4)
    # We want to sample using (ys, xs)
    # Result is (loop_count, 4)
    sampled_colors = pixels[ys, xs] 
    
    # Flatten for foreach_set
    sampled_flat = sampled_colors.flatten()
    
    # 6. Set Colors
    print(f"Writing {loop_count} colors to '{layer_name}'...")
    set_loop_colors(vcol_layer, sampled_flat)
    
    # DEBUG: Read back a few colors
    print(f"DEBUG: First 5 vertex colors: {sampled_flat[:20]}")
    
    print("DONE! Cleanup and Vertex Color Bake complete.")

# if __name__ == "__main__":
try:
    # AUTO-SELECT "Google 3D Tiles" collection if active is empty/wrong for testing
    target_coll_name = "Google 3D Tiles"
    if target_coll_name in bpy.data.collections:
        target_coll = bpy.data.collections[target_coll_name]
        layer_coll = bpy.context.view_layer.layer_collection.children.get(target_coll_name)
        if layer_coll:
            bpy.context.view_layer.active_layer_collection = layer_coll
            print(f"DEBUG: Auto-selected collection '{target_coll_name}'")

    run_cleanup_and_bake()
except Exception as e:
    import traceback
    traceback.print_exc()
