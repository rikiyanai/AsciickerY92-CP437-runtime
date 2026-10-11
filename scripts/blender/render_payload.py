"""
Render payload for MCP execution.

Generates self-contained Python code strings that can be executed via MCP
(Model Context Protocol) in a running Blender instance's Python context.

ARCHITECTURE:
  This module does **not** run inside Blender itself.  It runs in the host
  Python environment (the pipeline's process) and produces a *string* of Python
  code that will later be sent to Blender via the MCP JSON-RPC bridge.

  The generated script is fully self-contained -- it includes its own imports
  (``bpy``, ``PIL``, ``math``, ``base64``, ``numpy``, ``mathutils``).

  Execution flow:
    1. ``generator.py`` calls ``get_render_script()`` with an ``asset_def``.
    2. The returned string is sent to Blender via MCP's ``execute_code`` RPC.
    3. Inside Blender, the script:
       a. Finds the target object and all its children meshes.
       b. Calculates the combined bounding box for auto-framing.
       c. Sets up lights and an orthographic camera at the correct distance.
       d. Orbits the camera and renders N angles x M frames.
       e. Stitches frames into a sprite sheet (cols=frames, rows=angles).
       f. Converts alpha to magenta (255, 0, 255) via NumPy.
       g. Base64-encodes the final PNG and returns it as a result JSON.

RELATIONSHIP TO PIPELINE:
  [DEPENDENCY:BLENDER]       -- The *generated* script requires bpy.
  [DEPENDENCY:PIL]           -- Used for stitching and base64 encoding.
  [PIPELINE:GENERATE]        -- Produces a render payload for the MCP bridge.
  [DATA-CONTRACT:XP]         -- Output is quantized into .xp format.
  [DATA-CONTRACT:ASSET-DEF]  -- Consumes object_name, angles, anims.
"""

import base64
from typing import Any, Dict
from PIL import Image
import io


def get_render_script(asset_def: Dict[str, Any]) -> str:
    """
    Generate a self-contained render script for MCP execution in Blender.

    Features: Auto-framing based on combined bounding box, 3-point + Sun lighting,
    Standard view transform, and optimized NumPy magenta conversion.
    """
    object_name = asset_def.get("object_name", "Cube")
    angles = asset_def.get("angles", 8)
    anims = asset_def.get("anims", [4])
    total_frames_per_angle = sum(anims)
    
    # Engine requires projs=2 for multi-angle sprites
    projs = 2 if angles > 1 else 1

    script = f'''
import bpy
import io
import base64
import math
import numpy as np
from PIL import Image
import mathutils

# --- Scene setup ---
target_root = bpy.data.objects.get("{object_name}")
if not target_root:
    bpy.ops.mesh.primitive_add_cube()
    target_root = bpy.context.active_object
    target_root.name = "{object_name}"

# --- Helper: Recursive mesh finder ---
def get_all_meshes(obj):
    meshes = []
    if obj.type == 'MESH':
        meshes.append(obj)
    for child in obj.children:
        meshes.extend(get_all_meshes(child))
    return meshes

meshes = get_all_meshes(target_root)
if not meshes:
    # If root itself isn't a mesh and has no mesh children, use root for bbox
    meshes = [target_root]

# --- Helper: Combined Bounding Box ---
def get_combined_bbox(objects):
    min_v = mathutils.Vector((float('inf'), float('inf'), float('inf')))
    max_v = mathutils.Vector((float('-inf'), float('-inf'), float('-inf')))
    for obj in objects:
        for corner in [obj.matrix_world @ mathutils.Vector(v) for v in obj.bound_box]:
            for i in range(3):
                min_v[i] = min(min_v[i], corner[i])
                max_v[i] = max(max_v[i], corner[i])
    return min_v, max_v

bbox_min, bbox_max = get_combined_bbox(meshes)
center = (bbox_min + bbox_max) / 2
size = bbox_max - bbox_min
max_dim = max(size.x, size.y, size.z)

# --- Lighting setup (Auto-scaled) ---
def setup_lighting(center, max_dim):
    # Clear existing MCP lights
    for obj in bpy.data.objects:
        if obj.type == 'LIGHT' and obj.name.startswith("MCP_"):
            bpy.data.objects.remove(obj, do_unlink=True)
            
    dist = max_dim * 2
    # Key light
    key_light = bpy.data.lights.new('MCP_Key', type='POINT')
    key_obj = bpy.data.objects.new('MCP_Key', key_light)
    bpy.context.scene.collection.objects.link(key_obj)
    key_obj.location = center + mathutils.Vector((dist, -dist, dist))
    key_light.energy = max_dim * 1000

    # Fill light
    fill_light = bpy.data.lights.new('MCP_Fill', type='POINT')
    fill_obj = bpy.data.objects.new('MCP_Fill', fill_light)
    bpy.context.scene.collection.objects.link(fill_obj)
    fill_obj.location = center + mathutils.Vector((-dist, -dist, dist/2))
    fill_light.energy = max_dim * 500
    
    # Sun light (Global)
    sun_light = bpy.data.lights.new('MCP_Sun', type='SUN')
    sun_obj = bpy.data.objects.new('MCP_Sun', sun_light)
    bpy.context.scene.collection.objects.link(sun_obj)
    sun_light.energy = 2.0
    
    # Top Area light
    area_light = bpy.data.lights.new('MCP_Area', type='AREA')
    area_obj = bpy.data.objects.new('MCP_Area', area_light)
    bpy.context.scene.collection.objects.link(area_obj)
    area_obj.location = center + mathutils.Vector((0, 0, dist))
    area_light.energy = max_dim * 200
    area_light.size = max_dim * 2

setup_lighting(center, max_dim)

# --- Camera setup (Auto-framed) ---
def setup_camera(center, max_dim):
    cam_obj = bpy.data.objects.get("MCP_RenderCam")
    if not cam_obj:
        cam_data = bpy.data.cameras.new('MCP_RenderCam')
        cam_obj = bpy.data.objects.new('MCP_RenderCam', cam_data)
        bpy.context.scene.collection.objects.link(cam_obj)
    
    bpy.context.scene.camera = cam_obj
    cam_obj.data.type = 'ORTHO'
    # Auto-scale ortho to fit object exactly with some padding
    cam_obj.data.ortho_scale = max_dim * 1.2
    
    # TRACK_TO center
    empty_target = bpy.data.objects.get("MCP_Target")
    if not empty_target:
        empty_target = bpy.data.objects.new("MCP_Target", None)
        bpy.context.scene.collection.objects.link(empty_target)
    empty_target.location = center
    
    const = cam_obj.constraints.get("MCP_Track")
    if not const:
        const = cam_obj.constraints.new('TRACK_TO')
        const.name = "MCP_Track"
    const.target = empty_target
    const.track_axis = 'TRACK_NEGATIVE_Z'
    const.up_axis = 'UP_Y'
    
    return cam_obj

cam_obj = setup_camera(center, max_dim)

# --- Render Settings ---
scene = bpy.context.scene
scene.view_settings.view_transform = 'Standard'
scene.render.resolution_x = 96
scene.render.resolution_y = 96
scene.render.image_settings.file_format = 'PNG'
scene.render.image_settings.color_mode = 'RGBA'
scene.render.film_transparent = True

# --- Turntable Loop ---
angles = {angles}
anims = {anims}
total_frames = sum(anims)
frames_data = []

import tempfile
import os

dist = max_dim * 3
with tempfile.TemporaryDirectory() as tmpdir:
    for a in range(angles):
        theta = math.radians(a * (360 / angles))
        # Turntable: circle around the object center in XY plane
        cam_obj.location = center + mathutils.Vector((
            dist * math.sin(theta), 
            -dist * math.cos(theta), 
            dist * 0.5  # Slight elevation
        ))
        
        angle_frames = []
        for f in range(total_frames):
            scene.frame_set(f + 1)
            path = os.path.join(tmpdir, f"a{{a}}_f{{f}}.png")
            scene.render.filepath = path
            bpy.ops.render.render(write_still=True)
            angle_frames.append(Image.open(path))
        frames_data.append(angle_frames)

# --- Stitching (Cols=Frames, Rows=Angles) ---
fw, fh = 96, 96
projs = {projs}
sheet_w = total_frames * fw * projs
sheet_h = angles * fh
sheet = Image.new('RGBA', (sheet_w, sheet_h), (255, 0, 255, 255))

for a, angle_frames in enumerate(frames_data):
    for f, frame in enumerate(angle_frames):
        sheet.paste(frame, (f * fw, a * fh))
        if projs == 2:
            refl = frame.transpose(Image.FLIP_LEFT_RIGHT)
            sheet.paste(refl, ((total_frames + f) * fw, a * fh))

# --- Fast Magenta Conversion via NumPy ---
data = np.array(sheet)
alpha_channel = data[:, :, 3]
data[alpha_channel == 0] = [255, 0, 255, 255]
sheet = Image.fromarray(data)

# --- Encode result ---
buffer = io.BytesIO()
sheet.save(buffer, format='PNG')
image_data = base64.b64encode(buffer.getvalue()).decode('utf-8')

# Final result
import json
print(json.dumps({{
    "image": image_data,
    "width": sheet.width,
    "height": sheet.height,
    "angles": angles,
    "anims": anims,
    "projs": projs
}}))
'''
    return script.strip()


def get_metadata_from_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Extract and normalize metadata from a render result dictionary."""
    if not result or "image" not in result:
        raise ValueError("Invalid render result")

    return {{
        "image": result["image"],
        "width": result.get("width", 96),
        "height": result.get("height", 96),
        "angles": result.get("angles", 1),
        "anims": result.get("anims", [1]),
        "projs": result.get("projs", 1),
    }}

if __name__ == "__main__":
    print("Testing fixed render payload generation...")
    script = get_render_script({{"object_name": "Cube", "angles": 8, "anims": [4]}})
    print(f"Generated script ({{len(script)}} chars)")
    print(script[:300] + "...")
