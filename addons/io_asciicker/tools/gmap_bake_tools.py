# Asciicker GMap Bake Tools
# Bake texture colors to vertex colors and project photogrammetry onto OSM meshes.
# [DEPENDENCY:BLENDER] - Operators registered via bpy.utils.register_class

"""
Google Maps Photogrammetry Tools
================================
[DEPENDENCY:BLENDER]

A suite of tools for processing 3D Google Maps dumps (photogrammetry) for
use in Asciicker.

ARCHITECTURE:
    The module is organized into five functional sections, each separated
    by banner comments in the source:

    1. **Texture Baking** (lines ~42-250) -- Core UV-sampling pipeline.
       Transfer pixel data from image textures to per-loop vertex colors
       (VCol) for the terminal renderer.  Core function:
       ``bake_active_texture_to_vcol``.

    2. **Projection / Re-texturing** (lines ~252-478) -- Cycles
       selected-to-active bake.  Projects high-res photogrammetry textures
       onto low-poly OSM building shells, generates a temporary bake image,
       then feeds it through the texture-to-VCol pipeline.

    3. **Alignment** (lines ~480-832) -- Bounding-box alignment and
       raycast coverage.  Aligns imported GMap chunks with target OSM
       buildings using XY center offset and uniform scale, then validates
       coverage via BVHTree raycasting.

    4. **Terrain Cleanup** (lines ~904-1200) -- Footprint-based geometry
       cleanup.  Removes floating tree canopies and messy photogrammetry
       artifacts using 2D point-in-polygon tests against expanded building
       footprints.

    5. **Operator Registration** (lines ~1532-1548) -- Standard Blender
       operator class registration and unregistration.

KEY EXPORTS:
    Operators:
        - ``ASCIICKER_OT_bake_texture_to_vcol``     -- Bake active texture
          to vertex colors on selected meshes.
        - ``ASCIICKER_OT_project_gmap_to_targets``   -- Project GMap
          photogrammetry onto OSM targets via Cycles bake, then VCol.
        - ``ASCIICKER_OT_align_gmap_to_targets``     -- Align GMap sources
          to target collection bounds (XY offset + optional scale).
        - ``ASCIICKER_OT_check_gmap_coverage``       -- Raycast coverage
          validation with optional per-vertex color visualization.
        - ``ASCIICKER_OT_cleanup_gmap_terrain``      -- Multi-step terrain
          cleanup: tree removal, flattening, merge, optional separation.

    Key Functions:
        - ``bake_active_texture_to_vcol``    -- Standalone texture-to-VCol bake.
        - ``bake_sources_to_target``         -- Cycles selected-to-active bake.
        - ``compute_alignment_transform``    -- XY bounding-box alignment math.
        - ``raycast_coverage_check``         -- BVHTree raycast coverage test.
        - ``identify_floating_tree_faces``   -- Heuristic tree-canopy detection.

PIPELINE CONTEXT:
    [DATA-CONTRACT:AKM]
    The final vertex-color layer (``Col``) produced by the bake operators is
    the color data that the AKM exporter writes.  The terminal renderer
    displays these colors directly; palette-safe snapping (see
    ``color_tools.py``) should follow baking for best visual quality.

    [DATA-CONTRACT:A3D]
    The cleanup operator modifies scene-level mesh topology and can
    optionally separate buildings from ground, which affects how objects
    are placed in the A3D world map.

Dependencies:
    - numpy (optional but recommended for performance)
    - mathutils (Vector, BVHTree)
    - bpy (Blender API)

TODO(PIPELINE-FIX): The ``_sample_image_pixels`` fallback path (no numpy)
    samples one pixel at a time inside a Python loop, which is extremely
    slow for large meshes.  Consider a pure-Python batch approach using
    ``struct.unpack`` on the raw pixel buffer.
"""


import bpy
from bpy.types import Operator
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

try:
    import numpy as np
except Exception:
    np = None


def _ensure_color_layer(mesh, name="Col", data_type="BYTE_COLOR"):
    """Create or retrieve a vertex-color layer and set it as active.

    Handles both Blender 4.x (``color_attributes``) and 3.x
    (``vertex_colors``) APIs.

    Args:
        mesh:      Blender ``Mesh`` data block.
        name:      Name of the color layer (default ``"Col"``).
        data_type: ``'BYTE_COLOR'`` or ``'FLOAT_COLOR'``.

    Returns:
        The color attribute/layer.
    """
    if hasattr(mesh, "color_attributes"):
        # Blender 4.x path
        layer = mesh.color_attributes.get(name)
        if not layer:
            layer = mesh.color_attributes.new(
                name=name,
                type=data_type,
                domain='CORNER',
            )
        mesh.color_attributes.active_color = layer
        return layer

    # Blender 3.x fallback
    if not mesh.vertex_colors:
        mesh.vertex_colors.new(name=name)
    layer = mesh.vertex_colors.get(name) or mesh.vertex_colors.new(name=name)
    mesh.vertex_colors.active = layer
    return layer


def _set_loop_colors(layer, colors_flat):
    """Bulk-write a flat RGBA list to a vertex-color layer using ``foreach_set``.

    Handles the Blender 4.x ``color_srgb`` vs ``color`` attribute name
    difference for BYTE_COLOR vs FLOAT_COLOR layers.

    Args:
        layer:       Vertex-color layer (color attribute).
        colors_flat: Flat list ``[R,G,B,A, R,G,B,A, ...]`` of floats, one
                     RGBA quadruplet per loop.
    """
    if hasattr(layer, "data_type"):
        # WHY check color_srgb first: Blender 4.x BYTE_COLOR layers expose
        # ``color_srgb`` (sRGB values) rather than ``color`` (linear).  Using
        # the wrong attribute silently mis-converts gamma.
        if layer.data_type == 'BYTE_COLOR' and hasattr(layer.data[0], "color_srgb"):
            layer.data.foreach_set("color_srgb", colors_flat)
            return
        if hasattr(layer.data[0], "color"):
            layer.data.foreach_set("color", colors_flat)
            return
    layer.data.foreach_set("color", colors_flat)


def _get_uvs(mesh):
    """Extract per-loop UV coordinates from the active UV layer.

    Tries the fast ``foreach_get`` path first (requires numpy), then falls
    back to a per-element Python loop.

    Args:
        mesh: Blender ``Mesh`` data block.

    Returns:
        numpy.ndarray of shape ``(loop_count, 2)`` if numpy is available,
        otherwise a list of ``(u, v)`` tuples.  Returns ``None`` if the
        mesh has no UV layers.
    """
    if not mesh.uv_layers:
        return None

    loop_count = len(mesh.loops)
    uvs = None

    # Fast path: bulk read via foreach_get (numpy required)
    if np is not None:
        uvs = np.zeros(loop_count * 2, dtype=np.float32)
        try:
            mesh.uv_layers.active.data.foreach_get("uv", uvs)
            return uvs.reshape((loop_count, 2))
        except Exception:
            uvs = None

    # Slow fallback: per-element Python loop
    try:
        uvs = [d.uv[:] for d in mesh.uv_layers.active.data]
        return np.array(uvs, dtype=np.float32) if np is not None else uvs
    except Exception:
        return None


def _find_image_from_material(mat):
    """Find the first image texture referenced by a material's node tree.

    Checks the *active* node first (artist intent), then falls back to
    scanning all nodes for any ``TEX_IMAGE`` with an assigned image.

    Args:
        mat: Blender ``Material`` or ``None``.

    Returns:
        bpy.types.Image or ``None``.
    """
    if not mat or not mat.use_nodes or not mat.node_tree:
        return None

    # Prefer the actively selected image node (artist-chosen)
    active = mat.node_tree.nodes.active
    if active and active.type == 'TEX_IMAGE' and active.image:
        return active.image

    # Fallback: first image node found
    for node in mat.node_tree.nodes:
        if node.type == 'TEX_IMAGE' and node.image:
            return node.image

    return None


def _load_image_pixels(image):
    """Load all pixel data from a Blender image into a dict for fast sampling.

    With numpy available, pixels are stored as a ``(height, width, 4)`` array
    enabling vectorized UV sampling.  Without numpy, the raw flat tuple is
    kept and sampled per-pixel.

    Args:
        image: ``bpy.types.Image`` or ``None``.

    Returns:
        dict ``{"width": int, "height": int, "pixels": array_or_tuple}``
        or ``None`` on failure.
    """
    if image is None:
        return None

    width = image.size[0]
    height = image.size[1]
    if width <= 0 or height <= 0:
        return None

    try:
        # WHY pixels[:]: ``image.pixels`` is a lazy accessor; slicing forces
        # a full copy into Python memory for safe repeated access.
        pixels = image.pixels[:]
    except Exception:
        return None

    if np is None:
        return {
            "width": width,
            "height": height,
            "pixels": pixels,
        }

    # Reshape to (H, W, 4) for direct [y, x] indexing during UV sampling
    arr = np.array(pixels, dtype=np.float32).reshape((height, width, 4))
    return {
        "width": width,
        "height": height,
        "pixels": arr,
    }


def _sample_image_pixels(image_data, uvs):
    """
    Sample pixel colors from image data using UV coordinates.
    Supports both single-point sampling and bulk numpy sampling.
    
    Args:
        image_data (dict): Result from _load_image_pixels containing 'pixels', 'width', 'height'.
        uvs (list/array): UV coordinates [(u,v), ...]
        
    Returns:
        list: Flat list of color values [r,g,b,a, r,g,b,a, ...]
    """
    width = image_data["width"]
    height = image_data["height"]
    pixels = image_data["pixels"]

    if np is None:
        # Slow path: per-pixel Python loop (no numpy)
        colors = []
        for u, v in uvs:
            # WHY u != u: NaN != NaN is True in IEEE 754; this is the
            # fastest NaN check without importing math.
            if u != u or v != v:
                u = 0.0
                v = 0.0

            # Wrap UVs into [0, 1) range (handles tiling textures)
            u = u % 1.0
            v = v % 1.0

            # Map normalized UV to integer pixel coordinates
            x = int(u * width)
            y = int(v * height)
            x = max(0, min(width - 1, x))
            y = max(0, min(height - 1, y))

            idx = (y * width + x) * 4
            colors.extend([pixels[idx], pixels[idx + 1], pixels[idx + 2], pixels[idx + 3]])
        return colors

    # Fast path: vectorized numpy sampling
    uvs_arr = uvs.copy()
    us = uvs_arr[:, 0]
    vs = uvs_arr[:, 1]
    # Replace NaN UVs with origin
    nan_mask = np.isnan(us) | np.isnan(vs)
    us[nan_mask] = 0.0
    vs[nan_mask] = 0.0
    # Wrap to [0, 1) and convert to pixel indices
    us = us % 1.0
    vs = vs % 1.0
    xs = (us * width).astype(np.int32)
    ys = (vs * height).astype(np.int32)
    xs = np.clip(xs, 0, width - 1)
    ys = np.clip(ys, 0, height - 1)
    # WHY pixels[ys, xs]: the pixel array is shaped (H, W, 4), so we can
    # fancy-index with arrays of y and x coordinates simultaneously.
    colors = pixels[ys, xs]
    return colors.reshape(-1).tolist()


def bake_active_texture_to_vcol(obj, layer_name="Col", forced_image=None):
    """Bake image-texture pixel colors to a vertex-color layer via UV sampling.

    This is the **core bake function** used by both the direct
    ``ASCIICKER_OT_bake_texture_to_vcol`` operator and the projection
    pipeline (``ASCIICKER_OT_project_gmap_to_targets``).

    Algorithm:
        1. Read UV coordinates from the active UV layer.
        2. For each material slot, find the image texture node.
        3. For each polygon, sample the corresponding image at each loop's
           UV coordinate and write the RGBA result to the vertex-color layer.

    With numpy, per-material loops are batched into a single vectorized
    sample call.  Without numpy, each loop is sampled individually.

    Args:
        obj:          Blender mesh object.
        layer_name:   Name for the destination vertex-color layer (default
                      ``"Col"``).
        forced_image: If not ``None``, use this image for **all** polygons
                      regardless of material assignment.  Used by the
                      projection pipeline after a Cycles bake.

    Returns:
        tuple: ``(success: bool, message: str)``.

    [DATA-CONTRACT:AKM] The resulting ``Col`` layer is what the AKM exporter
    writes as per-vertex RGB data.
    """
    if not obj or obj.type != 'MESH' or not obj.data:
        return False, "Select a mesh object"

    mesh = obj.data
    uvs = _get_uvs(mesh)
    if uvs is None:
        return False, f"Mesh '{obj.name}' has no UVs"

    images = {}
    if forced_image is not None:
        image_data = _load_image_pixels(forced_image)
        if image_data is not None:
            images[0] = image_data
    else:
        for idx, mat in enumerate(mesh.materials):
            image = _find_image_from_material(mat)
            if image:
                images[idx] = _load_image_pixels(image)

    if not images:
        return False, f"No image textures found on '{obj.name}'"

    layer = _ensure_color_layer(mesh, name=layer_name, data_type="BYTE_COLOR")
    loop_count = len(mesh.loops)

    if np is None:
        colors = [0.0] * (loop_count * 4)
        for poly in mesh.polygons:
            image_data = images.get(0 if forced_image is not None else poly.material_index)
            if not image_data:
                continue
            for loop_idx in poly.loop_indices:
                uv = uvs[loop_idx]
                sample = _sample_image_pixels(image_data, [uv])
                offset = loop_idx * 4
                colors[offset:offset + 4] = sample
        _set_loop_colors(layer, colors)
        mesh.update()
        return True, f"Baked texture to '{layer.name}'"

    uvs_arr = uvs
    loop_mat_idx = np.zeros(loop_count, dtype=np.int32)
    for poly in mesh.polygons:
        loop_mat_idx[poly.loop_indices] = poly.material_index

    colors = np.zeros((loop_count, 4), dtype=np.float32)
    for mat_idx, image_data in images.items():
        if image_data is None:
            continue
        mask = loop_mat_idx == (0 if forced_image is not None else mat_idx)
        if not np.any(mask):
            continue
        colors[mask] = np.array(_sample_image_pixels(image_data, uvs_arr[mask])).reshape((-1, 4))

    _set_loop_colors(layer, colors.reshape(-1).tolist())
    mesh.update()
    return True, f"Baked texture to '{layer.name}'"


def _ensure_uvs(obj, island_margin=0.001):
    """Ensure *obj* has at least one UV layer; auto-unwrap if missing.

    Uses Blender's ``Smart UV Project`` as a fallback.  This is adequate for
    the Cycles projection bake (which only needs *some* UV mapping on the
    target) but may not produce optimal seams.

    Args:
        obj:           Blender mesh object.
        island_margin: Island margin for Smart UV Project.

    Returns:
        bool: ``True`` if UVs exist (or were created), ``False`` on failure.
    """
    if obj.type != 'MESH' or not obj.data:
        return False

    if obj.data.uv_layers:
        return True

    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(island_margin=island_margin)
    bpy.ops.object.mode_set(mode='OBJECT')
    return True


def _ensure_bake_material(obj, image):
    """Ensure *obj* has a material with a Tex Image node set to *image*.

    Creates material, Principled BSDF, Material Output, and Image Texture
    nodes as needed, then makes the image texture node **active** so that
    Blender's Cycles bake operator writes into it.

    WHY the active node matters: ``bpy.ops.object.bake`` writes bake results
    to the image assigned to the *active* Image Texture node in the first
    material slot.  Without this setup, the bake silently fails.

    Args:
        obj:   Blender mesh object (target of the bake).
        image: ``bpy.types.Image`` to receive the baked pixels.

    Returns:
        The ``ShaderNodeTexImage`` node that was made active.
    """
    if not obj.data.materials:
        mat = bpy.data.materials.new(name=f"{obj.name}_bake")
        mat.use_nodes = True
        obj.data.materials.append(mat)
    else:
        mat = obj.data.materials[0]
        if not mat:
            mat = bpy.data.materials.new(name=f"{obj.name}_bake")
            mat.use_nodes = True
            obj.data.materials[0] = mat

    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    # Find or create the Image Texture node for the bake target
    tex_node = None
    for node in nodes:
        if node.type == 'TEX_IMAGE' and node.image == image:
            tex_node = node
            break
    if tex_node is None:
        tex_node = nodes.new(type='ShaderNodeTexImage')
        tex_node.image = image
        tex_node.location = (-400, 200)

    # Make it active so Cycles bake writes to this image
    nodes.active = tex_node

    # Ensure a minimal Principled BSDF -> Material Output chain exists
    if "Principled BSDF" in nodes:
        bsdf = nodes["Principled BSDF"]
    else:
        bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
        bsdf.location = (0, 0)
    if "Material Output" in nodes:
        out = nodes["Material Output"]
    else:
        out = nodes.new(type='ShaderNodeOutputMaterial')
        out.location = (400, 0)
    if not bsdf.outputs['BSDF'].is_linked:
        links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
    if not tex_node.outputs['Color'].is_linked:
        links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])

    return tex_node


def bake_sources_to_target(context, sources, target, image, bake_margin=2, cage_extrusion=0.01):
    """
    Perform a Selected-to-Active texture bake using Cycles.
    
    1. Selects 'sources' (high-res GMap meshes).
    2. Activates 'target' (low-poly OSM mesh).
    3. Ensures target has a material with the destination 'image' node active.
    4. Runs `bpy.ops.object.bake(type='DIFFUSE')`.
    """
    scene = context.scene
    # WHY Cycles: only Cycles supports the selected-to-active bake mode
    # needed for projecting source textures onto a different target mesh.
    scene.render.engine = 'CYCLES'

    # WHY disable direct/indirect, enable color only: we want the raw
    # albedo/texture color transferred, not lighting-baked output.
    scene.render.bake.use_pass_direct = False
    scene.render.bake.use_pass_indirect = False
    scene.render.bake.use_pass_color = True
    scene.render.bake.use_selected_to_active = True
    scene.render.bake.margin = bake_margin
    scene.render.bake.cage_extrusion = cage_extrusion

    prev_active = context.view_layer.objects.active
    prev_selected = [o for o in context.view_layer.objects if o.select_get()]
    try:
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.select_all(action='DESELECT')
        
        # Select Sources
        for obj in sources:
            obj.select_set(True)
            
        # Activate Target
        target.select_set(True)
        context.view_layer.objects.active = target
        
        # Setup Material
        _ensure_bake_material(target, image)
        
        # Execute Bake
        bpy.ops.object.bake(type='DIFFUSE')
    finally:
        # Restore Selection
        bpy.ops.object.select_all(action='DESELECT')
        for obj in prev_selected:
            obj.select_set(True)
        context.view_layer.objects.active = prev_active


class ASCIICKER_OT_bake_texture_to_vcol(Operator):
    """Bake active texture to vertex colors on selected meshes.

    [DEPENDENCY:BLENDER] [PIPELINE:PROCESS] [DATA-CONTRACT:AKM]
    Iterates over selected mesh objects and calls
    ``bake_active_texture_to_vcol`` on each.  The resulting ``Col`` layer is
    the vertex-color data consumed by the AKM exporter.
    """
    bl_idname = "asciicker.bake_texture_to_vcol"
    bl_label = "Bake Texture to VCol"
    bl_options = {'REGISTER', 'UNDO'}

    layer_name: StringProperty(
        name="VCol Layer",
        description="Vertex color layer name",
        default="Col",
    )

    @classmethod
    def poll(cls, context):
        """Return True if any objects are selected in the viewport."""
        return context.selected_objects is not None

    def execute(self, context):
        """Bake the active image texture to a vertex-color layer on each selected mesh."""
        targets = [obj for obj in context.selected_objects if obj.type == 'MESH']
        if not targets:
            self.report({'WARNING'}, "Select at least one mesh")
            return {'CANCELLED'}

        for obj in targets:
            ok, msg = bake_active_texture_to_vcol(obj, layer_name=self.layer_name)
            if not ok:
                self.report({'WARNING'}, msg)

        return {'FINISHED'}


class ASCIICKER_OT_project_gmap_to_targets(Operator):
    """Project photogrammetry textures onto target meshes and bake to vertex colors"""
    bl_idname = "asciicker.project_gmap_to_targets"
    bl_label = "Project GMap to Targets"
    bl_options = {'REGISTER', 'UNDO'}

    target_collection: StringProperty(
        name="Target Collection",
        description="Collection name containing target meshes (OSM buildings)",
        default="",
    )
    keep_bake_image: BoolProperty(
        name="Keep Baked Image",
        description="Keep the baked image datablock for inspection",
        default=True,
    )
    auto_uv_unwrap: BoolProperty(
        name="Auto UV Unwrap Targets",
        description="Generate UVs for targets if none are found",
        default=True,
    )
    bake_resolution: IntProperty(
        name="Bake Resolution",
        description="Resolution for temporary bake images",
        default=2048,
        min=256,
        max=8192,
    )
    bake_margin: IntProperty(
        name="Bake Margin",
        description="Bake margin in pixels",
        default=2,
        min=0,
        max=32,
    )

    @classmethod
    def poll(cls, context):
        """Return True if any objects are selected in the viewport."""
        return context.selected_objects is not None

    def execute(self, context):
        """Run Cycles selected-to-active bake from GMap sources onto each target, then convert the baked image to vertex colors.

        [DEPENDENCY:BLENDER] Requires Cycles render engine for selected-to-active bake.
        """
        sources = [obj for obj in context.selected_objects if obj.type == 'MESH']
        if not sources:
            self.report({'WARNING'}, "Select source meshes to project from")
            return {'CANCELLED'}

        targets = []
        if self.target_collection:
            collection = bpy.data.collections.get(self.target_collection)
            if not collection:
                self.report({'ERROR'}, f"Collection '{self.target_collection}' not found")
                return {'CANCELLED'}
            targets = [obj for obj in collection.objects if obj.type == 'MESH']
        else:
            targets = [obj for obj in context.selected_objects if obj.type == 'MESH']

        if not targets:
            self.report({'WARNING'}, "No target meshes found")
            return {'CANCELLED'}

        for target in targets:
            if target in sources:
                continue
            if self.auto_uv_unwrap:
                _ensure_uvs(target)
            image = bpy.data.images.new(
                name=f"{target.name}_gmap_bake",
                width=self.bake_resolution,
                height=self.bake_resolution,
                alpha=True,
            )
            bake_sources_to_target(
                context,
                sources,
                target,
                image,
                bake_margin=self.bake_margin,
            )
            image.reload()
            ok, msg = bake_active_texture_to_vcol(
                target,
                layer_name="Col",
                forced_image=image,
            )
            if not ok:
                self.report({'WARNING'}, msg)
            if not self.keep_bake_image:
                bpy.data.images.remove(image)

        return {'FINISHED'}


# =============================================================================
# ALIGNMENT HELPERS
# =============================================================================

from mathutils import Vector


def get_mesh_bounds(obj):
    """Get world-space axis-aligned bounding box for a mesh object.

    [PIPELINE:PROCESS] Used by alignment and coverage operators to compute
    spatial relationships between GMap source chunks and OSM target buildings.

    Args:
        obj: Blender mesh object.

    Returns:
        dict with ``'min'``, ``'max'``, ``'center'``, ``'size'`` (all
        ``mathutils.Vector``), or ``None`` if *obj* is not a valid mesh.
    """
    if not obj or obj.type != 'MESH':
        return None

    mesh = obj.data
    if not mesh.vertices:
        return None

    world_matrix = obj.matrix_world

    if np is not None:
        # WHY foreach_get + list comprehension: foreach_get bulk-reads local
        # coordinates into a flat numpy array (fast C path), but the
        # world-space transform still requires a Python loop because
        # ``matrix_world @`` operates on mathutils.Vector objects.
        # TODO(PIPELINE-FIX): Replace per-vertex Python loop with a numpy
        # matmul (coords @ M.T + translation) for large meshes.
        coords = np.zeros(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", coords)
        coords = coords.reshape(-1, 3)
        world_coords = np.array([
            (world_matrix @ Vector(co)).to_tuple()
            for co in coords
        ], dtype=np.float32)
        min_co = world_coords.min(axis=0)
        max_co = world_coords.max(axis=0)
    else:
        world_coords = [(world_matrix @ v.co) for v in mesh.vertices]
        min_co = [min(c[i] for c in world_coords) for i in range(3)]
        max_co = [max(c[i] for c in world_coords) for i in range(3)]

    min_v = Vector(min_co)
    max_v = Vector(max_co)
    center = (min_v + max_v) / 2
    size = max_v - min_v

    return {
        'min': min_v,
        'max': max_v,
        'center': center,
        'size': size,
    }


def get_combined_bounds(objects):
    """Get combined world-space axis-aligned bounding box for multiple objects.

    [PIPELINE:PROCESS] Computes the union AABB across all mesh objects in
    *objects*.  Used by alignment operators to find the overall extent of
    source or target collections.

    Args:
        objects: Iterable of Blender objects (non-meshes are silently skipped).

    Returns:
        dict with ``'min'``, ``'max'``, ``'center'``, ``'size'`` (all
        ``mathutils.Vector``), or ``None`` if no valid meshes are found.
    """
    if not objects:
        return None

    all_min = None
    all_max = None

    for obj in objects:
        bounds = get_mesh_bounds(obj)
        if not bounds:
            continue
        if all_min is None:
            all_min = bounds['min'].copy()
            all_max = bounds['max'].copy()
        else:
            all_min.x = min(all_min.x, bounds['min'].x)
            all_min.y = min(all_min.y, bounds['min'].y)
            all_min.z = min(all_min.z, bounds['min'].z)
            all_max.x = max(all_max.x, bounds['max'].x)
            all_max.y = max(all_max.y, bounds['max'].y)
            all_max.z = max(all_max.z, bounds['max'].z)

    if all_min is None:
        return None

    center = (all_min + all_max) / 2
    size = all_max - all_min

    return {
        'min': all_min,
        'max': all_max,
        'center': center,
        'size': size,
    }


def compute_alignment_transform(source_bounds, target_bounds, scale_mode='uniform_xy'):
    """Compute XY offset and scale to align source bounds to target bounds.

    Args:
        source_bounds: dict from get_mesh_bounds or get_combined_bounds
        target_bounds: dict from get_mesh_bounds or get_combined_bounds
        scale_mode: 'uniform_xy' (fit both axes), 'none' (offset only)

    Returns:
        dict with 'offset' (Vector), 'scale' (float), 'center_offset' (Vector)
    """
    if not source_bounds or not target_bounds:
        return {'offset': Vector((0, 0, 0)), 'scale': 1.0, 'center_offset': Vector((0, 0, 0))}

    # Center offset (move source center to target center in XY)
    offset = Vector((
        target_bounds['center'].x - source_bounds['center'].x,
        target_bounds['center'].y - source_bounds['center'].y,
        0,  # Keep Z unchanged
    ))

    # WHY uniform scale via min(): using the smaller of the X/Y scale
    # factors ensures the source fits entirely within the target bounds
    # without exceeding either axis.  Aspect ratio is preserved.
    scale = 1.0
    if scale_mode == 'uniform_xy':
        src_size_x = source_bounds['size'].x
        src_size_y = source_bounds['size'].y
        tgt_size_x = target_bounds['size'].x
        tgt_size_y = target_bounds['size'].y

        if src_size_x > 0 and src_size_y > 0:
            scale_x = tgt_size_x / src_size_x
            scale_y = tgt_size_y / src_size_y
            scale = min(scale_x, scale_y)  # Uniform scale to fit

    return {
        'offset': offset,
        'scale': scale,
        'center_offset': offset,
    }


def apply_alignment_to_objects(objects, offset, scale, pivot):
    """Apply offset and uniform scale to objects around a pivot point.

    [PIPELINE:PROCESS] The scale is applied relative to *pivot* (typically the
    combined source center) so that objects scale outward/inward from their
    collective center rather than from each object's own origin.

    Args:
        objects: list of Blender objects to transform.
        offset:  ``mathutils.Vector`` XY translation (Z component ignored).
        scale:   float for uniform XY scale factor.
        pivot:   ``mathutils.Vector`` for scale pivot (usually source center).
    """
    for obj in objects:
        # Move to pivot, scale, move back, then offset
        loc = obj.location.copy()

        # Scale around pivot
        if scale != 1.0:
            rel = loc - pivot
            rel.x *= scale
            rel.y *= scale
            loc = pivot + rel
            obj.scale.x *= scale
            obj.scale.y *= scale

        # Apply offset
        loc.x += offset.x
        loc.y += offset.y

        obj.location = loc


# =============================================================================
# COVERAGE VALIDATION (RAYCAST)
# =============================================================================

def raycast_coverage_check(context, sources, target, ray_direction='Z_DOWN'):
    """Check how well GMap source meshes cover a target by raycasting.

    [PIPELINE:PROCESS] Builds a unified BVHTree from all source meshes, then
    casts a ray from each target vertex in the specified direction.  A "hit"
    means the source geometry exists above/below/around that target point.

    WHY BVHTree from combined sources: merging all source triangles into one
    BVHTree avoids per-source ray tests and handles overlapping source chunks
    seamlessly.

    Args:
        context:       Blender context (provides evaluated depsgraph).
        sources:       list of GMap source mesh objects.
        target:        OSM target mesh object.
        ray_direction: ``'Z_DOWN'``, ``'Z_UP'``, or ``'NORMAL'``.

    Returns:
        dict with ``'hit_count'`` (int), ``'miss_count'`` (int),
        ``'coverage_ratio'`` (float 0-1), ``'hit_mask'`` (list of bools,
        one per target vertex).

    TODO(PIPELINE-FIX): The per-vertex Python loop over target vertices is
        O(V * log(T)) where T = source triangles.  For large targets consider
        batching rays or using numpy to build origin/direction arrays.
    """
    from mathutils.bvhtree import BVHTree

    if not target or target.type != 'MESH':
        return {'hit_count': 0, 'miss_count': 0, 'coverage_ratio': 0.0, 'hit_mask': []}

    # Build BVH tree from sources
    depsgraph = context.evaluated_depsgraph_get()
    combined_verts = []
    combined_tris = []
    vert_offset = 0

    for src in sources:
        if src.type != 'MESH':
            continue
        eval_obj = src.evaluated_get(depsgraph)
        mesh = eval_obj.to_mesh()
        world_matrix = src.matrix_world

        for v in mesh.vertices:
            world_co = world_matrix @ v.co
            combined_verts.append(world_co)

        mesh.calc_loop_triangles()
        for tri in mesh.loop_triangles:
            combined_tris.append([idx + vert_offset for idx in tri.vertices])

        vert_offset += len(mesh.vertices)
        eval_obj.to_mesh_clear()

    if not combined_verts or not combined_tris:
        return {'hit_count': 0, 'miss_count': 0, 'coverage_ratio': 0.0, 'hit_mask': []}

    bvh = BVHTree.FromPolygons(combined_verts, combined_tris)

    # Raycast from target vertices
    target_mesh = target.data
    target_world = target.matrix_world
    hit_mask = []
    hit_count = 0
    miss_count = 0

    # TODO(PIPELINE-FIX): ray_dist is a hardcoded magic number; scenes with
    # extreme Z-range (e.g. mountainous terrain) could exceed this distance.
    ray_dist = 1000.0  # Max ray distance

    for v in target_mesh.vertices:
        world_pos = target_world @ v.co

        if ray_direction == 'Z_DOWN':
            ray_dir = Vector((0, 0, -1))
            ray_origin = world_pos + Vector((0, 0, ray_dist / 2))
        elif ray_direction == 'Z_UP':
            ray_dir = Vector((0, 0, 1))
            ray_origin = world_pos - Vector((0, 0, ray_dist / 2))
        else:  # NORMAL
            ray_dir = -(target_world.to_3x3() @ v.normal).normalized()
            ray_origin = world_pos - ray_dir * 0.01

        hit_loc, hit_normal, hit_idx, hit_dist = bvh.ray_cast(ray_origin, ray_dir, ray_dist)

        if hit_loc is not None:
            hit_mask.append(True)
            hit_count += 1
        else:
            hit_mask.append(False)
            miss_count += 1

    total = hit_count + miss_count
    coverage_ratio = hit_count / total if total > 0 else 0.0

    return {
        'hit_count': hit_count,
        'miss_count': miss_count,
        'coverage_ratio': coverage_ratio,
        'hit_mask': hit_mask,
    }


def visualize_coverage_mask(obj, hit_mask, hit_color=(0.0, 1.0, 0.0, 1.0), miss_color=(1.0, 0.0, 0.0, 1.0)):
    """Create a vertex-color layer visualizing per-vertex raycast hit/miss.

    [PIPELINE:PROCESS] Writes green (hit) or red (miss) to a ``_coverage_mask``
    color layer.  Intended for artist inspection in Blender's Solid viewport
    mode with vertex-color display.

    WHY per-loop from per-vertex: Blender stores vertex colors per-loop
    (corner), not per-vertex.  Each loop belonging to a vertex inherits that
    vertex's hit/miss color.

    Args:
        obj:        Target mesh object.
        hit_mask:   list of bools, one per vertex (from ``raycast_coverage_check``).
        hit_color:  RGBA tuple for covered vertices (default green).
        miss_color: RGBA tuple for uncovered vertices (default red).

    Returns:
        The created/updated color attribute layer, or ``None`` on failure.
    """
    if not obj or obj.type != 'MESH':
        return None

    mesh = obj.data
    layer_name = "_coverage_mask"

    # Create or get the layer
    layer = _ensure_color_layer(mesh, name=layer_name, data_type="BYTE_COLOR")

    # Build per-loop colors from per-vertex hit_mask
    loop_count = len(mesh.loops)
    colors = [0.0] * (loop_count * 4)

    for loop_idx, loop in enumerate(mesh.loops):
        vert_idx = loop.vertex_index
        if vert_idx < len(hit_mask) and hit_mask[vert_idx]:
            col = hit_color
        else:
            col = miss_color
        offset = loop_idx * 4
        colors[offset:offset + 4] = col

    _set_loop_colors(layer, colors)
    mesh.update()

    return layer


# =============================================================================
# ALIGNMENT OPERATOR
# =============================================================================

class ASCIICKER_OT_align_gmap_to_targets(Operator):
    """Align selected GMap sources to target collection bounds"""
    bl_idname = "asciicker.align_gmap_to_targets"
    bl_label = "Align GMap to Targets"
    bl_options = {'REGISTER', 'UNDO'}

    target_collection: StringProperty(
        name="Target Collection",
        description="Collection containing target meshes (OSM buildings)",
        default="",
    )
    scale_mode: EnumProperty(
        name="Scale Mode",
        items=[
            ('uniform_xy', "Uniform XY", "Scale uniformly to fit target bounds"),
            ('none', "Offset Only", "Only apply XY offset, no scaling"),
        ],
        default='uniform_xy',
    )
    preview_only: BoolProperty(
        name="Preview Only",
        description="Only show alignment info, don't apply",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        """Return True if any objects are selected in the viewport."""
        return context.selected_objects is not None

    def execute(self, context):
        """Compute and apply XY offset and optional uniform scale to align selected GMap sources to the target collection bounds."""
        sources = [obj for obj in context.selected_objects if obj.type == 'MESH']
        if not sources:
            self.report({'WARNING'}, "Select source meshes to align")
            return {'CANCELLED'}

        # Get targets
        targets = []
        if self.target_collection:
            collection = bpy.data.collections.get(self.target_collection)
            if collection:
                targets = [obj for obj in collection.objects if obj.type == 'MESH']

        if not targets:
            self.report({'WARNING'}, "No targets found in collection")
            return {'CANCELLED'}

        source_bounds = get_combined_bounds(sources)
        target_bounds = get_combined_bounds(targets)

        if not source_bounds or not target_bounds:
            self.report({'ERROR'}, "Could not compute bounds")
            return {'CANCELLED'}

        alignment = compute_alignment_transform(source_bounds, target_bounds, self.scale_mode)

        if self.preview_only:
            self.report({'INFO'},
                f"Alignment: offset=({alignment['offset'].x:.2f}, {alignment['offset'].y:.2f}), scale={alignment['scale']:.3f}")
            return {'FINISHED'}

        apply_alignment_to_objects(sources, alignment['offset'], alignment['scale'], source_bounds['center'])

        self.report({'INFO'},
            f"Aligned {len(sources)} sources: offset=({alignment['offset'].x:.2f}, {alignment['offset'].y:.2f}), scale={alignment['scale']:.3f}")
        return {'FINISHED'}


# =============================================================================
# COVERAGE VALIDATION OPERATOR
# =============================================================================

class ASCIICKER_OT_check_gmap_coverage(Operator):
    """Check raycast coverage from targets to GMap sources"""
    bl_idname = "asciicker.check_gmap_coverage"
    bl_label = "Check GMap Coverage"
    bl_options = {'REGISTER', 'UNDO'}

    target_collection: StringProperty(
        name="Target Collection",
        description="Collection containing target meshes",
        default="",
    )
    visualize: BoolProperty(
        name="Visualize",
        description="Create a vertex color mask showing hit/miss",
        default=True,
    )
    ray_direction: EnumProperty(
        name="Ray Direction",
        items=[
            ('Z_DOWN', "Z Down", "Cast rays downward"),
            ('Z_UP', "Z Up", "Cast rays upward"),
            ('NORMAL', "Normal", "Cast rays along vertex normals"),
        ],
        default='Z_DOWN',
    )

    @classmethod
    def poll(cls, context):
        """Return True if any objects are selected in the viewport."""
        return context.selected_objects is not None

    def execute(self, context):
        """Raycast from each target vertex toward GMap sources and report hit/miss coverage ratio, optionally visualizing results as vertex colors."""
        sources = [obj for obj in context.selected_objects if obj.type == 'MESH']
        if not sources:
            self.report({'WARNING'}, "Select source meshes")
            return {'CANCELLED'}

        targets = []
        if self.target_collection:
            collection = bpy.data.collections.get(self.target_collection)
            if collection:
                targets = [obj for obj in collection.objects if obj.type == 'MESH' and obj not in sources]

        if not targets:
            self.report({'WARNING'}, "No targets found")
            return {'CANCELLED'}

        total_hits = 0
        total_misses = 0

        for target in targets:
            result = raycast_coverage_check(context, sources, target, self.ray_direction)
            total_hits += result['hit_count']
            total_misses += result['miss_count']

            if self.visualize:
                visualize_coverage_mask(target, result['hit_mask'])

        total = total_hits + total_misses
        ratio = total_hits / total if total > 0 else 0.0

        self.report({'INFO'},
            f"Coverage: {total_hits}/{total} vertices ({ratio * 100:.1f}%)")
        return {'FINISHED'}


# =============================================================================
# GMAP TERRAIN CLEANUP HELPERS
# =============================================================================

def expand_polygon_2d(poly_verts, offset):
    """Expand 2D polygon outward from centroid by offset distance.

    Args:
        poly_verts: list of (x, y) tuples
        offset: distance to expand

    Returns:
        list of expanded (x, y) tuples
    """
    if not poly_verts or len(poly_verts) < 3:
        return poly_verts

    # WHY centroid-based expansion rather than true polygon offset:
    # a mathematically correct polygon offset (Minkowski sum with a disk)
    # is complex and can produce self-intersections.  Expanding each vertex
    # radially from the centroid is a simple approximation that works well
    # for convex-ish building footprints and is sufficient for the
    # inside/outside classification tolerance we need.
    cx = sum(v[0] for v in poly_verts) / len(poly_verts)
    cy = sum(v[1] for v in poly_verts) / len(poly_verts)

    expanded = []
    for x, y in poly_verts:
        dx = x - cx
        dy = y - cy
        length = (dx * dx + dy * dy) ** 0.5
        if length > 0.0001:
            nx = dx / length
            ny = dy / length
            expanded.append((x + nx * offset, y + ny * offset))
        else:
            expanded.append((x, y))

    return expanded


def point_in_polygon_2d(point, poly_verts):
    """Check if 2D point is inside polygon using ray casting.

    Args:
        point: (x, y) tuple
        poly_verts: list of (x, y) tuples

    Returns:
        True if point is inside polygon
    """
    if not poly_verts or len(poly_verts) < 3:
        return False

    # WHY ray-casting algorithm: this is the standard Jordan curve theorem
    # implementation.  A horizontal ray is cast from the test point to the
    # right; the number of polygon edge crossings determines inside/outside.
    # Odd crossings = inside, even = outside.  O(n) per point.
    x, y = point
    n = len(poly_verts)
    inside = False

    p1x, p1y = poly_verts[0]
    for i in range(1, n + 1):
        p2x, p2y = poly_verts[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y

    return inside


def build_expanded_footprints(footprint_obj, margin):
    """Build list of expanded 2D polygons with bboxes for fast rejection.

    Args:
        footprint_obj: Blender mesh object containing footprint polygons
        margin: distance to expand footprints

    Returns:
        list of dicts with 'verts' (2D polygon), 'bbox' (min_x, min_y, max_x, max_y)
    """
    if not footprint_obj or footprint_obj.type != 'MESH':
        return []

    mesh = footprint_obj.data
    world_matrix = footprint_obj.matrix_world
    expanded_polys = []

    for poly in mesh.polygons:
        # Get 2D polygon vertices (XY projection)
        poly_verts_2d = []
        for loop_idx in poly.loop_indices:
            vert_idx = mesh.loops[loop_idx].vertex_index
            world_co = world_matrix @ mesh.vertices[vert_idx].co
            poly_verts_2d.append((world_co.x, world_co.y))

        if len(poly_verts_2d) < 3:
            continue

        # Expand polygon
        expanded = expand_polygon_2d(poly_verts_2d, margin)

        # Calculate bounding box
        xs = [v[0] for v in expanded]
        ys = [v[1] for v in expanded]
        bbox = (min(xs), min(ys), max(xs), max(ys))

        expanded_polys.append({
            'verts': expanded,
            'bbox': bbox,
        })

    return expanded_polys


def classify_gmap_vertices(gmap_obj, expanded_polys):
    """Classify vertices as inside/outside building footprints.

    Args:
        gmap_obj: GMap mesh object
        expanded_polys: list from build_expanded_footprints

    Returns:
        (inside_indices, outside_indices) - sets of vertex indices
    """
    if not gmap_obj or gmap_obj.type != 'MESH':
        return set(), set()

    mesh = gmap_obj.data
    world_matrix = gmap_obj.matrix_world
    inside_indices = set()
    outside_indices = set()

    for vert_idx, vert in enumerate(mesh.vertices):
        world_co = world_matrix @ vert.co
        point = (world_co.x, world_co.y)

        is_inside = False
        for poly_data in expanded_polys:
            bbox = poly_data['bbox']
            # Fast bbox rejection
            if point[0] < bbox[0] or point[0] > bbox[2]:
                continue
            if point[1] < bbox[1] or point[1] > bbox[3]:
                continue
            # Detailed point-in-polygon test
            if point_in_polygon_2d(point, poly_data['verts']):
                is_inside = True
                break

        if is_inside:
            inside_indices.add(vert_idx)
        else:
            outside_indices.add(vert_idx)

    return inside_indices, outside_indices


def identify_floating_tree_faces(gmap_obj, expanded_polys, ground_z, height_threshold, variance_threshold):
    """
    Identify floating tree canopy faces.
    
    Heuristic:
    1. Vertex MUST be outside building footprint (we keep building roofs).
    2. Face average Z > ground + threshold (floating).
    3. Normal Variance Check:
       - Trees have chaotic normals ("leaves" point everywhere).
       - Ground/Roofs are usually planar (normals point Up).
       
    Args:
        gmap_obj: GMap mesh object
        expanded_polys: list from build_expanded_footprints
        ground_z: reference ground level Z
        height_threshold: minimum height above ground_z to consider as floating
        variance_threshold: normal variance threshold for tree detection

    Returns:
        list of face indices to delete
    """
    if not gmap_obj or gmap_obj.type != 'MESH':
        return []

    mesh = gmap_obj.data
    world_matrix = gmap_obj.matrix_world

    # First classify all vertices
    inside_verts, outside_verts = classify_gmap_vertices(gmap_obj, expanded_polys)

    tree_faces = []

    for face_idx, face in enumerate(mesh.polygons):
        # Check if all vertices are outside footprints
        all_outside = all(mesh.loops[li].vertex_index in outside_verts for li in face.loop_indices)
        if not all_outside:
            continue

        # Calculate average face height
        avg_z = 0.0
        for loop_idx in face.loop_indices:
            vert_idx = mesh.loops[loop_idx].vertex_index
            world_co = world_matrix @ mesh.vertices[vert_idx].co
            avg_z += world_co.z
        avg_z /= len(face.loop_indices)

        # Check if floating above ground
        if avg_z < ground_z + height_threshold:
            continue

        # Check normal variance (tree canopy has chaotic normals)
        face_normal = (world_matrix.to_3x3() @ face.normal).normalized()

        # Compare face normal to up vector (trees tend to have varied normals)
        up_dot = abs(face_normal.z)

        # Check adjacent face normals for variance
        variance_check = False
        if up_dot < (1.0 - variance_threshold):
            # Face normal is not pointing mostly up/down - likely tree
            variance_check = True
        else:
            # Face is mostly horizontal - could be building roof or tree platform
            # Check if it's very high above ground (more likely tree)
            if avg_z > ground_z + height_threshold * 1.5:
                variance_check = True

        if variance_check:
            tree_faces.append(face_idx)

    return tree_faces


def flatten_exterior_vertices(gmap_obj, outside_indices, ground_z, strength):
    """Flatten exterior vertices toward ground level.

    Args:
        gmap_obj: GMap mesh object
        outside_indices: set of vertex indices to flatten
        ground_z: target ground Z level
        strength: 0.0 = no change, 1.0 = fully flatten to ground_z
    """
    if not gmap_obj or gmap_obj.type != 'MESH':
        return

    if strength <= 0:
        return

    mesh = gmap_obj.data
    world_matrix = gmap_obj.matrix_world
    world_matrix_inv = world_matrix.inverted()

    for vert_idx in outside_indices:
        if vert_idx >= len(mesh.vertices):
            continue

        vert = mesh.vertices[vert_idx]
        world_co = world_matrix @ vert.co

        # Interpolate Z toward ground
        new_z = world_co.z + (ground_z - world_co.z) * strength
        world_co.z = new_z

        # Convert back to local space
        local_co = world_matrix_inv @ world_co
        vert.co = local_co

    mesh.update()


def delete_faces_by_index(gmap_obj, face_indices):
    """Delete faces by index list using bmesh.

    Args:
        gmap_obj: GMap mesh object
        face_indices: list of face indices to delete
    """
    if not gmap_obj or gmap_obj.type != 'MESH' or not face_indices:
        return

    import bmesh

    # Enter edit mode with bmesh
    bm = bmesh.new()
    bm.from_mesh(gmap_obj.data)

    bm.faces.ensure_lookup_table()

    # Collect faces to delete (reverse sort to maintain indices)
    faces_to_delete = []
    for idx in sorted(face_indices, reverse=True):
        if idx < len(bm.faces):
            faces_to_delete.append(bm.faces[idx])

    # Delete faces
    bmesh.ops.delete(bm, geom=faces_to_delete, context='FACES')

    # Write back to mesh
    bm.to_mesh(gmap_obj.data)
    bm.free()

    gmap_obj.data.update()


def merge_exterior_vertices(gmap_obj, outside_indices, threshold):
    """Merge nearby exterior vertices.

    Args:
        gmap_obj: GMap mesh object
        outside_indices: set of vertex indices to consider for merging
        threshold: merge distance
    """
    if not gmap_obj or gmap_obj.type != 'MESH' or threshold <= 0:
        return

    import bmesh

    bm = bmesh.new()
    bm.from_mesh(gmap_obj.data)

    bm.verts.ensure_lookup_table()

    # Select only exterior vertices for merging
    for vert in bm.verts:
        vert.select = vert.index in outside_indices

    # Merge by distance (only selected)
    bmesh.ops.remove_doubles(bm, verts=[v for v in bm.verts if v.select], dist=threshold)

    bm.to_mesh(gmap_obj.data)
    bm.free()

    gmap_obj.data.update()


def separate_mesh_by_footprints(context, gmap_obj, expanded_polys):
    """Separate mesh into buildings (inside footprints) and ground (outside).

    Args:
        context: Blender context
        gmap_obj: GMap mesh object to separate
        expanded_polys: list from build_expanded_footprints

    Returns:
        tuple of (buildings_obj, ground_obj) or (None, None) on failure
    """
    import bmesh

    if not gmap_obj or gmap_obj.type != 'MESH':
        return None, None

    # Classify vertices
    inside_verts, outside_verts = classify_gmap_vertices(gmap_obj, expanded_polys)

    if not inside_verts or not outside_verts:
        return None, None

    mesh = gmap_obj.data

    # Classify faces: a face is "inside" if majority of its verts are inside
    inside_faces = set()
    outside_faces = set()

    for face_idx, face in enumerate(mesh.polygons):
        inside_count = 0
        outside_count = 0
        for loop_idx in face.loop_indices:
            vert_idx = mesh.loops[loop_idx].vertex_index
            if vert_idx in inside_verts:
                inside_count += 1
            else:
                outside_count += 1

        if inside_count > outside_count:
            inside_faces.add(face_idx)
        else:
            outside_faces.add(face_idx)

    # Create buildings object (faces inside footprints)
    bm_buildings = bmesh.new()
    bm_buildings.from_mesh(mesh)
    bm_buildings.faces.ensure_lookup_table()

    # Delete outside faces to get buildings
    faces_to_delete = [bm_buildings.faces[i] for i in outside_faces if i < len(bm_buildings.faces)]
    bmesh.ops.delete(bm_buildings, geom=faces_to_delete, context='FACES')

    # Create new mesh and object for buildings
    buildings_mesh = bpy.data.meshes.new(f"{gmap_obj.name}_buildings")
    bm_buildings.to_mesh(buildings_mesh)
    bm_buildings.free()

    buildings_obj = bpy.data.objects.new(f"{gmap_obj.name}_buildings", buildings_mesh)
    buildings_obj.matrix_world = gmap_obj.matrix_world.copy()
    context.collection.objects.link(buildings_obj)

    # Create ground object (faces outside footprints)
    bm_ground = bmesh.new()
    bm_ground.from_mesh(mesh)
    bm_ground.faces.ensure_lookup_table()

    # Delete inside faces to get ground
    faces_to_delete = [bm_ground.faces[i] for i in inside_faces if i < len(bm_ground.faces)]
    bmesh.ops.delete(bm_ground, geom=faces_to_delete, context='FACES')

    # Create new mesh and object for ground
    ground_mesh = bpy.data.meshes.new(f"{gmap_obj.name}_ground")
    bm_ground.to_mesh(ground_mesh)
    bm_ground.free()

    ground_obj = bpy.data.objects.new(f"{gmap_obj.name}_ground", ground_mesh)
    ground_obj.matrix_world = gmap_obj.matrix_world.copy()
    context.collection.objects.link(ground_obj)

    # Copy materials
    for mat in gmap_obj.data.materials:
        buildings_mesh.materials.append(mat)
        ground_mesh.materials.append(mat)

    # Copy vertex colors if present
    if mesh.color_attributes:
        for attr in mesh.color_attributes:
            # Copy to buildings
            if not buildings_mesh.color_attributes.get(attr.name):
                buildings_mesh.color_attributes.new(name=attr.name, type=attr.data_type, domain=attr.domain)
            # Copy to ground
            if not ground_mesh.color_attributes.get(attr.name):
                ground_mesh.color_attributes.new(name=attr.name, type=attr.data_type, domain=attr.domain)

    return buildings_obj, ground_obj


def estimate_ground_z(gmap_obj, expanded_polys):
    """Estimate ground level Z from exterior vertices.

    Args:
        gmap_obj: GMap mesh object
        expanded_polys: list from build_expanded_footprints

    Returns:
        estimated ground Z level
    """
    if not gmap_obj or gmap_obj.type != 'MESH':
        return 0.0

    mesh = gmap_obj.data
    world_matrix = gmap_obj.matrix_world

    inside_verts, outside_verts = classify_gmap_vertices(gmap_obj, expanded_polys)

    if not outside_verts:
        # No exterior vertices, use minimum Z
        if mesh.vertices:
            return min((world_matrix @ v.co).z for v in mesh.vertices)
        return 0.0

    # WHY 10th percentile: using the minimum Z would be sensitive to
    # outlier vertices pushed underground by photogrammetry artifacts.
    # The 10th percentile is robust against a few bad vertices while still
    # representing the actual ground plane.
    z_values = []
    for vert_idx in outside_verts:
        world_co = world_matrix @ mesh.vertices[vert_idx].co
        z_values.append(world_co.z)

    z_values.sort()
    percentile_idx = max(0, len(z_values) // 10)
    return z_values[percentile_idx]


# =============================================================================
# GMAP TERRAIN CLEANUP OPERATOR
# =============================================================================

class ASCIICKER_OT_cleanup_gmap_terrain(Operator):
    """Clean up GMap 3D tiles using building footprints as mask.

    Multi-step cleanup pipeline:
        1. Expand building footprints by ``footprint_margin`` meters.
        2. Estimate ground Z from the 10th percentile of exterior vertices.
        3. Identify and delete floating tree-canopy faces (outside footprints,
           above height threshold, chaotic normals).
        4. Classify remaining vertices as inside/outside footprints.
        5. Flatten exterior vertices toward ground Z.
        6. Second-pass deletion of stretched vegetation after flattening.
        7. Merge nearby exterior vertices to reduce poly count.
        8. Optionally separate the mesh into ``_buildings`` and ``_ground``
           objects for independent export.

    [DATA-CONTRACT:A3D] Separation into buildings/ground affects the A3D
    world-map object layout.

    WHY footprint expansion: photogrammetry mesh boundaries rarely align
    perfectly with 2D footprint outlines.  A margin of 4-8 meters prevents
    building edges from being classified as "exterior" and flattened.
    """
    bl_idname = "asciicker.cleanup_gmap_terrain"
    bl_label = "Cleanup GMap Terrain"
    bl_description = "Remove tree canopy, flatten exterior, preserve buildings"
    bl_options = {'REGISTER', 'UNDO'}

    footprint_margin: FloatProperty(
        name="Footprint Margin",
        description="Expand footprints by this distance (meters)",
        default=6.0,
        min=0.0,
        max=20.0,
    )
    ground_threshold: FloatProperty(
        name="Ground Threshold",
        description="Height above ground to keep terrain",
        default=8.0,
        min=0.0,
        max=30.0,
    )
    floating_threshold: FloatProperty(
        name="Floating Threshold",
        description="Height above flattened ground to delete",
        default=3.0,
        min=0.0,
        max=20.0,
    )
    flatten_strength: FloatProperty(
        name="Flatten Strength",
        description="How much to flatten exterior (0=none, 1=full)",
        default=0.8,
        min=0.0,
        max=1.0,
    )
    merge_distance: FloatProperty(
        name="Merge Distance",
        description="Merge exterior vertices within this distance",
        default=0.5,
        min=0.0,
        max=2.0,
    )
    variance_threshold: FloatProperty(
        name="Variance Threshold",
        description="Normal variance threshold for tree detection",
        default=0.3,
        min=0.0,
        max=1.0,
    )
    separate_buildings: BoolProperty(
        name="Separate Buildings & Ground",
        description="Split into two objects: buildings (inside footprints) and ground (outside)",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        """Return True if both gmap_source_object and gmap_footprint_object are set and distinct."""
        props = context.scene.asciicker_props
        if not hasattr(props, 'gmap_source_object') or not hasattr(props, 'gmap_footprint_object'):
            return False
        return (props.gmap_source_object and
                props.gmap_footprint_object and
                props.gmap_source_object != props.gmap_footprint_object)

    def invoke(self, context, event):
        """Show a properties dialog before executing the cleanup pipeline."""
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        """Draw the operator properties panel with cleanup parameters and advanced options."""
        layout = self.layout
        layout.prop(self, "footprint_margin")
        layout.prop(self, "ground_threshold")
        layout.prop(self, "flatten_strength")
        layout.separator()
        layout.prop(self, "separate_buildings")
        layout.separator()
        layout.label(text="Advanced:")
        layout.prop(self, "floating_threshold")
        layout.prop(self, "merge_distance")
        layout.prop(self, "variance_threshold")

    def execute(self, context):
        """Run the multi-step terrain cleanup: expand footprints, remove tree canopy, flatten exterior, merge vertices, and optionally separate buildings from ground."""
        props = context.scene.asciicker_props
        gmap = props.gmap_source_object
        footprint = props.gmap_footprint_object

        if not gmap or not footprint:
            self.report({'ERROR'}, "Select both GMap mesh and footprint objects")
            return {'CANCELLED'}

        if gmap.type != 'MESH' or footprint.type != 'MESH':
            self.report({'ERROR'}, "Both objects must be mesh type")
            return {'CANCELLED'}

        initial_faces = len(gmap.data.polygons)

        # Step 1: Build expanded footprints
        self.report({'INFO'}, "Building expanded footprints...")
        expanded_polys = build_expanded_footprints(footprint, self.footprint_margin)

        if not expanded_polys:
            self.report({'WARNING'}, "No valid footprint polygons found")
            return {'CANCELLED'}

        # Step 2: Estimate ground level
        ground_z = estimate_ground_z(gmap, expanded_polys)
        self.report({'INFO'}, f"Estimated ground Z: {ground_z:.2f}")

        # Step 3: Identify and delete floating tree canopy
        self.report({'INFO'}, "Identifying floating tree canopy...")
        tree_faces = identify_floating_tree_faces(
            gmap, expanded_polys, ground_z,
            self.ground_threshold, self.variance_threshold
        )
        if tree_faces:
            self.report({'INFO'}, f"Deleting {len(tree_faces)} tree canopy faces...")
            delete_faces_by_index(gmap, tree_faces)

        # Step 4: Classify remaining vertices
        inside_verts, outside_verts = classify_gmap_vertices(gmap, expanded_polys)

        # Step 5: Flatten exterior vertices
        if self.flatten_strength > 0:
            self.report({'INFO'}, f"Flattening {len(outside_verts)} exterior vertices...")
            flatten_exterior_vertices(gmap, outside_verts, ground_z, self.flatten_strength)

        # Step 6: Delete stretched vegetation (second pass after flattening)
        stretched_faces = identify_floating_tree_faces(
            gmap, expanded_polys, ground_z + 2.0,
            self.floating_threshold, self.variance_threshold
        )
        if stretched_faces:
            self.report({'INFO'}, f"Deleting {len(stretched_faces)} stretched faces...")
            delete_faces_by_index(gmap, stretched_faces)

        # Step 7: Merge exterior vertices
        if self.merge_distance > 0:
            # Re-classify after face deletion
            inside_verts, outside_verts = classify_gmap_vertices(gmap, expanded_polys)
            self.report({'INFO'}, f"Merging nearby exterior vertices...")
            merge_exterior_vertices(gmap, outside_verts, self.merge_distance)

        final_faces = len(gmap.data.polygons)
        removed = initial_faces - final_faces
        percent = (removed / initial_faces * 100) if initial_faces > 0 else 0

        # Step 8: Separate buildings and ground if requested
        if self.separate_buildings:
            self.report({'INFO'}, "Separating buildings and ground...")
            buildings_obj, ground_obj = separate_mesh_by_footprints(context, gmap, expanded_polys)
            if buildings_obj and ground_obj:
                buildings_faces = len(buildings_obj.data.polygons)
                ground_faces = len(ground_obj.data.polygons)
                # Hide original
                gmap.hide_set(True)
                self.report({'INFO'}, f"Cleanup complete: Created '{buildings_obj.name}' ({buildings_faces} faces) and '{ground_obj.name}' ({ground_faces} faces)")
            else:
                self.report({'WARNING'}, f"Separation failed. Cleanup complete: {final_faces} faces remaining")
        else:
            self.report({'INFO'}, f"Cleanup complete: {final_faces} faces remaining ({removed} removed, {percent:.1f}%)")

        return {'FINISHED'}


classes = (
    ASCIICKER_OT_bake_texture_to_vcol,
    ASCIICKER_OT_project_gmap_to_targets,
    ASCIICKER_OT_align_gmap_to_targets,
    ASCIICKER_OT_check_gmap_coverage,
    ASCIICKER_OT_cleanup_gmap_terrain,
)


def register():
    """Register GMap bake tool operators with Blender."""
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    """Unregister GMap bake tool operators (reverse order)."""
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
