import bpy
import math

# Palette definition (RGB 0-1) matching default_materials.py
MATERIAL_COLORS = {
    0: (50/255, 100/255, 200/255),   # Water
    1: (80/255, 160/255, 60/255),    # Grass
    2: (140/255, 100/255, 60/255),   # Dirt
    3: (130/255, 130/255, 140/255),  # Stone
    4: (220/255, 200/255, 140/255),  # Sand
    5: (240/255, 245/255, 255/255),  # Snow
    6: (100/255, 70/255, 40/255),    # Wood
    7: (160/255, 160/255, 180/255),  # Steel
}

def find_nearest_material(rgb):
    """Find nearest material ID for a given RGB color"""
    min_dist = float('inf')
    best_id = 1 # Default Grass
    
    for mat_id, color in MATERIAL_COLORS.items():
        # Euclidean distance squared
        dr = rgb[0] - color[0]
        dg = rgb[1] - color[1]
        db = rgb[2] - color[2]
        dist = dr*dr + dg*dg + db*db
        if dist < min_dist:
            min_dist = dist
            best_id = mat_id
            
    return best_id

def get_color_layer(mesh):
    """Get or create active color layer (compatible with Blender 3.x and 4.x)"""
    if hasattr(mesh, "color_attributes"): # Blender 3.6+
        # Search for existing color attribute
        for attr in mesh.color_attributes:
            if attr.data_type in ('BYTE_COLOR', 'FLOAT_COLOR'):
                # Set as active
                if hasattr(mesh.attributes, "active"):
                    mesh.attributes.active = attr
                return attr
        
        # Create new if none found
        return mesh.color_attributes.new(name="Col", type='BYTE_COLOR', domain='CORNER')
    else:
        if not mesh.vertex_colors:
            mesh.vertex_colors.new()
        return mesh.vertex_colors.active


def fill_terrain_material(mesh, material_id):
    color = MATERIAL_COLORS.get(material_id, MATERIAL_COLORS[1])
    layer = get_color_layer(mesh)
    red = material_id / 255.0

    if hasattr(layer, "data_type") and layer.data_type == 'BYTE_COLOR':
        for item in layer.data:
            if hasattr(item, "color_srgb"):
                item.color_srgb = (red, color[1], color[2], 1.0)
            else:
                item.color = (red, color[1], color[2], 1.0)
    else:
        for item in layer.data:
            item.color = (red, color[1], color[2], 1.0)


def get_triangle_count(mesh):
    mesh.calc_loop_triangles()
    return len(mesh.loop_triangles)


def _apply_modifier(obj, modifier_name):
    context = bpy.context

    if hasattr(context, "temp_override"):
        override = {
            "active_object": obj,
            "object": obj,
            "selected_objects": [obj],
            "selected_editable_objects": [obj],
        }
        with context.temp_override(**override):
            if obj.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            bpy.ops.object.modifier_apply(modifier=modifier_name)
        return

    view_layer = context.view_layer
    prev_active = view_layer.objects.active
    prev_selected = [o for o in view_layer.objects if o.select_get()]
    try:
        for other in prev_selected:
            other.select_set(False)
        obj.select_set(True)
        view_layer.objects.active = obj
        if obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.modifier_apply(modifier=modifier_name)
    finally:
        for other in prev_selected:
            other.select_set(True)
        if obj not in prev_selected:
            obj.select_set(False)
        view_layer.objects.active = prev_active


def decimate_mesh(obj, target_faces=5000, max_passes=3):
    """
    Decimate mesh if it exceeds target face count.
    Uses 'COLLAPSE' mode of Decimate modifier.
    """
    if obj.type != 'MESH' or not obj.data:
        return
        
    mesh = obj.data
    for pass_idx in range(max_passes):
        current_faces = len(mesh.polygons)
        tri_faces = get_triangle_count(mesh)
        print(
            f"DEBUG_DECIMATE: Pass {pass_idx + 1}/{max_passes} {obj.name}. "
            f"Faces={current_faces} Tris={tri_faces} Target={target_faces}"
        )

        if current_faces <= target_faces:
            return

        if tri_faces == 0:
            print(f"WARNING: Decimation skipped for {obj.name}; no triangles found.")
            return

        # Decimate operates on triangles; compute ratio against triangulated count.
        ratio = (target_faces / tri_faces) * 0.95
        ratio = max(min(ratio, 1.0), 0.001)

        print(
            f"Decimating {obj.name}: {current_faces} -> target {target_faces} "
            f"(ratio {ratio:.4f})"
        )

        mod_name = "AutoDecimate"
        mod = obj.modifiers.get(mod_name)
        if not mod:
            mod = obj.modifiers.new(name=mod_name, type='DECIMATE')

        mod.decimate_type = 'COLLAPSE'
        mod.ratio = ratio

        try:
            _apply_modifier(obj, mod_name)
        except Exception as e:
            print(f"Failed to apply decimate modifier on {obj.name}: {e}")
            return

        mesh.update()

    final_faces = len(mesh.polygons)
    if final_faces > target_faces:
        print(f"WARNING: Decimation missed target for {obj.name}: {final_faces} > {target_faces}")

def process_mesh_textures(obj):
    """
    Sample active image texture and assign to vertex colors.
    """
    if obj.type != 'MESH' or not obj.data:
        return False
        
    mesh = obj.data
    
    # Needs UVs for texture-driven paint
    if not mesh.uv_layers:
        return False
        
    uv_layer = mesh.uv_layers.active.data
    
    # Prepare VCol
    layer = get_color_layer(mesh)
    
    # Track which loops we've processed if we have multi-material
    processed_loops = [False] * len(mesh.loops)
    
    # Access pixels per material
    for mat_idx, mat in enumerate(mesh.materials):
        if not mat or not mat.use_nodes or not mat.node_tree:
            continue
            
        image = None
        # Find Image Texture node
        for node in mat.node_tree.nodes:
            if node.type == 'TEX_IMAGE' and node.image:
                image = node.image
                break
        
        if not image:
            continue

        try:
            # image.pixels is flat RGBA floats
            pixels = image.pixels[:]
            width = image.size[0]
            height = image.size[1]
        except Exception as e:
            print(f"Error loading pixels for {image.name}: {e}")
            continue
            
        # Iterate polys with this material
        for poly in mesh.polygons:
            if poly.material_index != mat_idx:
                continue
                
            for loop_idx in poly.loop_indices:
                # Get UV
                uv = uv_layer[loop_idx].uv
                
                # Sample image (nearest neighbor)
                u = uv[0] % 1.0
                v = uv[1] % 1.0
                
                if math.isnan(u) or math.isnan(v):
                    u = 0.0
                    v = 0.0
                
                x = max(0, min(width - 1, int(u * width)))
                y = max(0, min(height - 1, int(v * height)))
                
                idx = (y * width + x) * 4
                rgba = (pixels[idx], pixels[idx+1], pixels[idx+2], pixels[idx+3])
                
                layer.data[loop_idx].color = rgba
                processed_loops[loop_idx] = True

    return any(processed_loops)

def process_terrain(obj, default_material_id=None):
    """
    Process terrain object: bake satellite texture to vertex color Material IDs.
    """
    if obj.type != 'MESH' or not obj.data:
        return
        
    mesh = obj.data
    
    # Needs UVs for texture-driven paint
    if not mesh.uv_layers:
        if default_material_id is not None:
            fill_terrain_material(mesh, default_material_id)
        return
        
    uv_layer = mesh.uv_layers.active.data
    
    # Get active image from material
    image = None
    if mesh.materials and mesh.materials[0]:
        mat = mesh.materials[0]
        if mat.use_nodes and mat.node_tree:
            # Find Image Texture node
            for node in mat.node_tree.nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    image = node.image
                    break
    
    if not image:
        if default_material_id is not None:
            fill_terrain_material(mesh, default_material_id)
        return

    # Prepare VCol
    layer = get_color_layer(mesh)
    
    # Access pixels
    try:
        # Try using numpy for speed
        import numpy as np
        # image.pixels is flat RGBA floats
        pixels = np.array(image.pixels).reshape((image.size[1], image.size[0], 4))
        width = image.size[0]
        height = image.size[1]
        use_numpy = True
    except ImportError:
        pixels = image.pixels[:] 
        width = image.size[0]
        height = image.size[1]
        use_numpy = False
    
    # Iterate loops
    for poly in mesh.polygons:
        for loop_idx in poly.loop_indices:
            # Get UV
            uv = uv_layer[loop_idx].uv
            
            # Sample image (nearest neighbor)
            u = uv[0]
            v = uv[1]
            
            if math.isnan(u) or math.isnan(v):
                u = 0.0
                v = 0.0
            
            u = u % 1.0
            v = v % 1.0
            
            try:
                x = int(u * width)
                y = int(v * height)
            except ValueError:
                print(f"DEBUG: NaN error. u={u}, v={v}, uv={uv}")
                x = 0
                y = 0
            
            # Clamp
            x = max(0, min(width - 1, x))
            y = max(0, min(height - 1, y))
            
            if use_numpy:
                r = pixels[y, x, 0]
                g = pixels[y, x, 1]
                b = pixels[y, x, 2]
            else:
                idx = (y * width + x) * 4
                r = pixels[idx]
                g = pixels[idx+1]
                b = pixels[idx+2]
            
            mat_id = find_nearest_material((r, g, b))
            
            # Assign ID to Red channel (0.0 - 1.0 mapping)
            val = mat_id / 255.0
            col = (val, 0.0, 0.0, 1.0)
            
            layer.data[loop_idx].color = col

def process_buildings(objects):
    """
    Process objects to assign vertex colors based on materials.
    Required for AKM export where materials are baked into vertex colors.
    """
    for obj in objects:
        if obj.type != 'MESH' or not obj.data:
            continue
            
        # First try to bake textures
        if process_mesh_textures(obj):
            continue
            
        # Fallback to material colors
        mesh = obj.data
        layer = get_color_layer(mesh)
        
        if not mesh.materials:
            continue
            
        # Cache material colors
        mat_colors = []
        for mat in mesh.materials:
            if mat:
                # Default to viewport color
                col = mat.diffuse_color
                
                # Try to get Principled BSDF color if nodes are used
                if mat.use_nodes and mat.node_tree:
                    bsdf = mat.node_tree.nodes.get("Principled BSDF")
                    if bsdf and bsdf.inputs['Base Color'].is_linked is False:
                         # Use base color if not textured
                         col = bsdf.inputs['Base Color'].default_value
                
                mat_colors.append(col)
            else:
                mat_colors.append((1.0, 1.0, 1.0, 1.0))
                
        # Assign colors to loops
        is_byte = (getattr(layer, 'data_type', '') == 'BYTE_COLOR')
        
        for poly in mesh.polygons:
            mat_idx = poly.material_index
            if mat_idx >= 0 and mat_idx < len(mat_colors):
                rgba = mat_colors[mat_idx]
                
                for loop_idx in poly.loop_indices:
                    layer.data[loop_idx].color = rgba
