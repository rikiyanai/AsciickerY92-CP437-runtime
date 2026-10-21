import bpy
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

def bake_texture_to_vertex_colors(obj=None, layer_name="Col"):
    """
    Bakes the color from the active image texture of the active material
    into a Vertex Color attribute (loop indices) for the given object.
    """
    if obj is None:
        obj = bpy.context.active_object
        
    if not obj or obj.type != 'MESH':
        print("Error: No valid mesh object selected.")
        return

    mesh = obj.data
    
    # 1. Get the active material and its image texture
    mat = obj.active_material
    if not mat or not mat.use_nodes:
        print(f"Error: Object '{obj.name}' has no active material with nodes.")
        return

    # Find the active texture node (or the first image texture selected)
    # Strategy: look for active node first, if it's an image. Else find first image node.
    tex_node = mat.node_tree.nodes.active
    if not (tex_node and tex_node.type == 'TEX_IMAGE'):
        # Fallback: find first image texture node
        for node in mat.node_tree.nodes:
            if node.type == 'TEX_IMAGE':
                tex_node = node
                break
    
    if not tex_node or not tex_node.image:
        print(f"Error: No image texture found in material '{mat.name}'.")
        return
        
    image = tex_node.image
    width = image.size[0]
    height = image.size[1]
    
    # Ensure we have access to pixels
    if not image.pixels:
        print(f"Error: Image '{image.name}' has no pixel data loaded.")
        return
        
    # Flattened RGBA float array (0.0 to 1.0)
    # Length is width * height * 4
    # We convert to numpy for faster sampling
    try:
        pixels = np.array(image.pixels[:], dtype=np.float32)
    except Exception as e:
        print(f"Error reading pixels: {e}")
        return
    
    pixels = pixels.reshape((height, width, 4)) # Row-major: [y, x, rgba]
    print(f"DEBUG: Pixels sample (first 2 pixels): {pixels[0, :2]}")

    # 2. Get UV coordinates from active UV layer
    if not mesh.uv_layers:
        print(f"Error: Mesh '{mesh.name}' has no UV layers.")
        return
    
    uv_layer = mesh.uv_layers.active.data
    
    # 3. Create or get Vertex Color Attribute (Blender 4.x compatible)
    vcol_layer = ensure_color_layer(mesh, layer_name)
    if hasattr(vcol_layer, "active_render"):
        vcol_layer.active_render = True
    
    print(f"Baking texture '{image.name}' to vertex color layer '{vcol_layer.name}'...")

    # 4. Sample and Assign
    # We iterate over loops (corners of faces) because UVs are stored per loop.
    # Vertex Colors are also stored per loop in the traditional 'Col' system.
    
    # Pre-allocate array for colors
    # Flattened array of RGBA per loop: len(mesh.loops) * 4
    loop_count = len(mesh.loops)
    
    # Extract UVs
    uvs = None
    
    # Try generic attribute API first (modern blender)
    uv_attr = mesh.attributes.active
    if uv_attr and uv_attr.domain == 'CORNER' and uv_attr.data_type == 'FLOAT2':
        try:
            # We can use foreach_get on attributes usually safely
            raw_uvs = np.zeros(loop_count * 2, dtype=np.float32)
            uv_attr.data.foreach_get("vector", raw_uvs)
            uvs = raw_uvs
            print("DEBUG: Used Attributes API for UVs")
        except Exception as e:
            print(f"DEBUG: Attribute API failed: {e}")
            
    if uvs is None:
        # Fallback to UV layers (legacy/standard)
        try:
            uv_layer = mesh.uv_layers.active.data
            raw_uvs = [d.uv[:] for d in uv_layer]
            uvs = np.array(raw_uvs, dtype=np.float32).flatten()
            print("DEBUG: Used UV Layer API")
        except Exception as e:
            print(f"Error reading UVs: {e}")
            return

    
    if uvs is None:
        print("Error: Failed to retrieve UVs.")
        return

    # Check for NaNs
    if np.any(np.isnan(uvs)):
        print("Error: NaNs found in UVs.")
        return
    
    uvs = uvs.reshape((loop_count, 2))
    
    # Map to indices
    us = uvs[:, 0]
    vs = uvs[:, 1]
    xs = (us * width).astype(np.int32)
    ys = (vs * height).astype(np.int32)
    
    # Clamp
    xs = np.clip(xs, 0, width - 1)
    ys = np.clip(ys, 0, height - 1)
    
    # Sample from pixels array
    # pixels is [height, width, 4]
    # We want pixels[ys, xs]
    sampled_colors = pixels[ys, xs] # Shape (loop_count, 4)
    
    # Flatten for foreach_set
    sampled_colors_flat = sampled_colors.flatten()
    
    # Write to vertex color layer
    set_loop_colors(vcol_layer, sampled_colors_flat)
    
    print(f"Done! Baked {loop_count} loops.")
    
    # Update mesh
    mesh.update()

if __name__ == "__main__":
    bake_texture_to_vertex_colors()
