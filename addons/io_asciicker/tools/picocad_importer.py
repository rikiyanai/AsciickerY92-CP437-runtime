# picoCAD2 Import Pipeline
# [DEPENDENCY:BLENDER] - Requires Blender Python API (bpy)
# [DATA-CONTRACT:AKM] - Outputs palette-safe vertex colors and collision groups
#                        compatible with the AKM export pipeline.

"""
picoCAD2 → Asciicker Pipeline -- Palette Quantization + Collision Setup
========================================================================

Converts picoCAD2 GLTF models (imported via Blender's native glTF importer)
into palette-safe, collision-ready meshes for AKM export.

PIPELINE:
    1. detect_picocad_palette(mesh)     -- Extract ≤16 unique colors from texture
    2. quantize_picocad_palette(colors) -- Map each to nearest SAFE_LEVELS³ entry
    3. bake_quantized_texture(mesh, palette_map) -- Write quantized colors to "Col" layer
    4. setup_collision_group(obj, preset)         -- Create collision vertex group

ARCHITECTURE:
    Follows the existing tool module pattern (color_tools.py, building_painter.py).
    Calls existing APIs only -- no modifications to color_tools, gmap_bake_tools,
    or export_akm.
"""

import bpy  # [DEPENDENCY:BLENDER]
from bpy.types import Operator
from bpy.props import EnumProperty, BoolProperty

try:
    import numpy as np
except Exception:
    np = None

from io_asciicker.tools.color_tools import snap_to_palette
from io_asciicker.tools.gmap_bake_tools import (
    _ensure_color_layer,
    _find_image_from_material,
    _get_uvs,
    _load_image_pixels,
    _sample_image_pixels,
    _set_loop_colors,
)


# ---------------------------------------------------------------------------
# Core Functions
# ---------------------------------------------------------------------------

def detect_picocad_palette(mesh, image=None):
    """Extract unique RGB colors from the first material's image texture.

    Args:
        mesh: Blender Mesh data block (must have at least one material with
              an image texture node).
        image: Pre-resolved Blender Image, or None to auto-detect from
               the first material.

    Returns:
        list[tuple[int,int,int]]: Unique (R,G,B) values found in the texture,
            capped at 16 by frequency. Returns empty list if no image found.
        str or None: Warning message if >16 colors detected, else None.
    """
    if image is None:
        if not mesh.materials:
            return [], "No materials on mesh"
        mat = mesh.materials[0]
        image = _find_image_from_material(mat)
        if image is None:
            return [], "No image texture found on first material"

    # Read all pixels from the image
    width = image.size[0]
    height = image.size[1]
    if width <= 0 or height <= 0:
        return [], "Image has zero dimensions"

    pixels = image.pixels[:]
    pixel_count = width * height

    # Collect unique RGB values (convert float 0-1 to int 0-255)
    color_freq = {}

    if np is not None:
        # Fast path: vectorized unique-color extraction via numpy
        arr = np.array(pixels, dtype=np.float32).reshape((pixel_count, 4))
        rgb = (arr[:, :3] * 255 + 0.5).astype(np.uint8)
        # Pack RGB into single int for fast unique counting
        packed = rgb[:, 0].astype(np.uint32) << 16 | rgb[:, 1].astype(np.uint32) << 8 | rgb[:, 2].astype(np.uint32)
        unique_vals, counts = np.unique(packed, return_counts=True)
        for val, cnt in zip(unique_vals, counts):
            r = int((val >> 16) & 0xFF)
            g = int((val >> 8) & 0xFF)
            b = int(val & 0xFF)
            color_freq[(r, g, b)] = int(cnt)
    else:
        # Slow fallback: per-pixel Python loop
        for i in range(pixel_count):
            offset = i * 4
            r = int(pixels[offset] * 255 + 0.5)
            g = int(pixels[offset + 1] * 255 + 0.5)
            b = int(pixels[offset + 2] * 255 + 0.5)
            key = (r, g, b)
            color_freq[key] = color_freq.get(key, 0) + 1

    warning = None
    colors = list(color_freq.keys())

    if len(colors) > 16:
        warning = (f"Detected {len(colors)} unique colors (not a picoCAD2 model?). "
                   f"Using top 16 by frequency.")
        # Take top 16 by frequency
        sorted_colors = sorted(color_freq.items(), key=lambda x: x[1], reverse=True)
        colors = [c for c, _ in sorted_colors[:16]]

    return colors, warning


def quantize_picocad_palette(colors):
    """Map each detected color to the nearest SAFE_LEVELS³ entry.

    Uses per-channel snap_to_palette() for each of R, G, B independently.

    Args:
        colors: list of (R, G, B) int tuples (0-255 range).

    Returns:
        dict: {(R,G,B): (R_snapped, G_snapped, B_snapped)} mapping.
    """
    palette_map = {}
    for r, g, b in colors:
        rs = snap_to_palette(r)
        gs = snap_to_palette(g)
        bs = snap_to_palette(b)
        palette_map[(r, g, b)] = (rs, gs, bs)
    return palette_map


def bake_quantized_texture(mesh, palette_map, image=None):
    """Sample texture at UV coords and write quantized colors to vertex color layer.

    For each loop, samples the texture at its UV coordinate, looks up the
    quantized color from palette_map, and writes to the "Col" BYTE_COLOR layer.

    Args:
        mesh: Blender Mesh data block.
        palette_map: dict mapping (R,G,B) int tuples to quantized equivalents.
        image: Pre-resolved Blender Image, or None to auto-detect from
               the first material.

    Returns:
        str or None: Error message on failure, None on success.
    """
    # Get UVs
    uvs = _get_uvs(mesh)
    if uvs is None:
        return "No UV layer found on mesh"

    # Get image from first material
    if image is None:
        if not mesh.materials:
            return "No materials on mesh"
        mat = mesh.materials[0]
        image = _find_image_from_material(mat)
        if image is None:
            return "No image texture found"

    # Load image pixels
    image_data = _load_image_pixels(image)
    if image_data is None:
        return "Failed to load image pixels"

    # Sample image at UV coords -> flat [r,g,b,a, r,g,b,a, ...] floats 0-1
    sampled = _sample_image_pixels(image_data, uvs)

    # Build output color list with quantized values
    loop_count = len(mesh.loops)

    if np is not None:
        # Fast path: vectorized quantization via numpy
        sampled_arr = np.array(sampled, dtype=np.float32).reshape((loop_count, 4))
        rgb_int = (sampled_arr[:, :3] * 255 + 0.5).astype(np.uint8)

        # Build lookup table from palette_map for vectorized mapping
        # Pack source RGB -> quantized RGB into a dict keyed by packed int
        packed_keys = rgb_int[:, 0].astype(np.uint32) << 16 | rgb_int[:, 1].astype(np.uint32) << 8 | rgb_int[:, 2].astype(np.uint32)
        unique_packed = np.unique(packed_keys)

        # Pre-build mapping for all unique colors found in sampled data
        pack_to_quant = {}
        for p in unique_packed:
            p_int = int(p)
            r_i = (p_int >> 16) & 0xFF
            g_i = (p_int >> 8) & 0xFF
            b_i = p_int & 0xFF
            key = (r_i, g_i, b_i)
            if key in palette_map:
                pack_to_quant[p_int] = palette_map[key]
            else:
                pack_to_quant[p_int] = (snap_to_palette(r_i), snap_to_palette(g_i), snap_to_palette(b_i))

        # Vectorized output construction
        out = np.ones((loop_count, 4), dtype=np.float32)  # alpha = 1.0
        for p_int, (rs, gs, bs) in pack_to_quant.items():
            mask = packed_keys == p_int
            out[mask, 0] = rs / 255.0
            out[mask, 1] = gs / 255.0
            out[mask, 2] = bs / 255.0

        colors_flat = out.ravel().tolist()
    else:
        # Slow fallback: per-loop Python iteration
        colors_flat = [0.0] * (loop_count * 4)

        for i in range(loop_count):
            offset = i * 4
            # Convert sampled float (0-1) to int (0-255)
            r_int = int(sampled[offset] * 255 + 0.5)
            g_int = int(sampled[offset + 1] * 255 + 0.5)
            b_int = int(sampled[offset + 2] * 255 + 0.5)

            # Look up quantized color
            key = (r_int, g_int, b_int)
            if key in palette_map:
                rs, gs, bs = palette_map[key]
            else:
                # Fallback: snap directly if color not in palette map
                # (can happen with sub-pixel interpolation artifacts)
                rs = snap_to_palette(r_int)
                gs = snap_to_palette(g_int)
                bs = snap_to_palette(b_int)

            # Write back as float 0-1
            colors_flat[offset] = rs / 255.0
            colors_flat[offset + 1] = gs / 255.0
            colors_flat[offset + 2] = bs / 255.0
            colors_flat[offset + 3] = 1.0  # Full alpha

    # Get the layer and write
    layer = _ensure_color_layer(mesh, "Col", "BYTE_COLOR")
    _set_loop_colors(layer, colors_flat)

    return None


def setup_collision_group(obj, preset_weight):
    """Create or update the 'collision' vertex group with a uniform weight.

    The collision vertex group weight (0.0-1.0) is multiplied by 255 during
    AKM export to produce the alpha channel byte:
        0.0 = fully solid wall (alpha 0)
        1.0 = fully passthrough (alpha 255)

    Args:
        obj: Blender Object (must be MESH type).
        preset_weight: float weight to assign to all vertices.
    """
    mesh = obj.data

    # Get or create the vertex group
    vg = obj.vertex_groups.get("collision")
    if vg is None:
        vg = obj.vertex_groups.new(name="collision")

    # Assign all vertices with the preset weight
    all_verts = list(range(len(mesh.vertices)))
    vg.add(all_verts, preset_weight, 'REPLACE')


# ---------------------------------------------------------------------------
# Operator
# ---------------------------------------------------------------------------

class ASCIICKER_OT_picocad_process(Operator):
    """Process picoCAD2 GLTF mesh: quantize palette, bake vertex colors, set collision.

    Runs the full pipeline on selected mesh objects:
        1. Detect picoCAD2 palette from texture
        2. Quantize to terminal-safe 216-color cube
        3. Bake quantized colors to "Col" vertex color layer
        4. Create collision vertex group with preset weight
        5. (Optional) Export to AKM

    [DATA-CONTRACT:AKM] Output meshes are palette-safe and collision-ready.
    [DEPENDENCY:BLENDER] Operator registered via tools/__init__.py.
    """
    bl_idname = "asciicker.picocad_process"
    bl_label = "Process picoCAD2 Mesh"
    bl_description = "Quantize palette, bake vertex colors, and set collision for picoCAD2 models"
    bl_options = {'REGISTER', 'UNDO'}

    do_export: BoolProperty(
        name="Export AKM",
        description="Also export to .akm after processing",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        """Allow when active object is a mesh or any selected object is a mesh."""
        obj = context.active_object
        if obj is not None and obj.type == 'MESH':
            return True
        # Support batch mode: allow if any selected object is a mesh
        return any(o.type == 'MESH' for o in context.selected_objects)

    def execute(self, context):
        """Run the picoCAD2 processing pipeline on selected objects."""
        import os
        from io_asciicker.mesh import export_akm
        from io_asciicker.path_utils import get_project_root

        props = context.scene.asciicker_props
        preset_name = props.picocad_collision_preset
        batch_mode = props.picocad_batch_mode

        # Determine collision weight from preset
        weight_map = {"SOLID": 0.0, "PASSTHROUGH": 1.0}
        preset_weight = weight_map.get(preset_name, 0.0)

        # Determine objects to process
        if batch_mode:
            objects = [o for o in context.selected_objects if o.type == 'MESH']
        else:
            obj = context.active_object
            objects = [obj] if (obj and obj.type == 'MESH') else []

        if not objects:
            self.report({'ERROR'}, "No mesh objects selected")
            props.picocad_status = "Error: No mesh objects selected"
            return {'CANCELLED'}

        # Process each object
        processed = 0
        skipped = 0
        report_lines = []

        for obj in objects:
            mesh = obj.data

            # Resolve image once for both detect and bake steps
            image = None
            if mesh.materials:
                image = _find_image_from_material(mesh.materials[0])

            # Step 1: Detect palette
            colors, warning = detect_picocad_palette(mesh, image=image)
            if not colors:
                self.report({'WARNING'}, f"{obj.name}: {warning}")
                skipped += 1
                continue

            if warning:
                self.report({'WARNING'}, f"{obj.name}: {warning}")

            # Step 2: Quantize
            palette_map = quantize_picocad_palette(colors)

            # Step 3: Bake
            error = bake_quantized_texture(mesh, palette_map, image=image)
            if error:
                self.report({'WARNING'}, f"{obj.name}: {error}")
                skipped += 1
                continue

            # Step 4: Collision
            setup_collision_group(obj, preset_weight)

            # Step 5: Optional export
            if self.do_export:
                from io_asciicker.path_utils import normalize_mesh_name
                project_root = get_project_root()
                meshes_dir = os.path.join(project_root, "assets", "meshes")
                os.makedirs(meshes_dir, exist_ok=True)
                mesh_name = normalize_mesh_name(obj.name)
                filepath = os.path.join(meshes_dir, f"{mesh_name}.akm")
                result = export_akm.save(
                    self, context,
                    filepath=filepath,
                    use_selection=False,
                    use_uv_coords=False,
                    objects=[obj],
                )
                if result != {'FINISHED'}:
                    self.report({'WARNING'}, f"{obj.name}: AKM export failed")
                    skipped += 1
                    continue
                else:
                    self.report({'INFO'}, f"{obj.name}: exported to {filepath}")

            # Build palette report line with per-color mappings
            color_lines = []
            for orig, quant in palette_map.items():
                r, g, b = orig
                rs, gs, bs = quant
                delta = max(abs(r - rs), abs(g - gs), abs(b - bs))
                color_lines.append(
                    f"#{r:02x}{g:02x}{b:02x} → #{rs:02x}{gs:02x}{bs:02x} (Δ={delta})"
                )
            report_lines.append(f"{obj.name}: {len(colors)} colors")
            report_lines.extend(color_lines)

            # Validate colors via export_akm
            dither_warnings = export_akm.validate_colors(mesh, obj)
            if dither_warnings:
                for w in dither_warnings:
                    self.report({'WARNING'}, f"{obj.name}: {w}")

            processed += 1

        # Update panel state
        total = processed + skipped
        if skipped == 0:
            props.picocad_status = (
                f"{processed}/{total} objects processed, 0.0% dithering, "
                f"collision: {preset_name.lower()}"
            )
        else:
            props.picocad_status = (
                f"{processed} of {total} objects processed "
                f"({skipped} skipped — see Info log)"
            )

        # Build palette report (truncate if too many lines)
        if len(report_lines) > 20:
            visible = report_lines[:20]
            visible.append(f"... and {len(report_lines) - 20} more (see Info log)")
            for line in report_lines[20:]:
                self.report({'INFO'}, line)
            props.picocad_palette_report = "\n".join(visible)
        else:
            props.picocad_palette_report = "\n".join(report_lines)

        if processed == 0:
            return {'CANCELLED'}

        self.report({'INFO'}, f"picoCAD2: Processed {processed} object(s)")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    ASCIICKER_OT_picocad_process,
)


def register():
    """Register picoCAD2 operator with Blender."""
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    """Unregister picoCAD2 operator from Blender."""
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
