"""
Create a "Cooker" test asset in Blender (Suzanne monkey head).

[DEPENDENCY:BLENDER] -- Requires bpy; executed headless or inside Blender GUI.
[PIPELINE:GENERATE]  -- Produces a .blend file consumed by render_turntable.py
                        or walk_anim_tool.py for sprite sheet generation.
[DATA-CONTRACT:XP]   -- Indirect: the .blend file is rendered to PNGs, then
                        quantized into .xp sprite sheets by the asset_gen pipeline.

ARCHITECTURE
============
Self-contained scene-builder script.  Resets Blender to factory defaults,
then constructs a minimal scene with:

    - Suzanne mesh ("Cooker") at (0,0,1) with orange/gold Principled BSDF.
    - 8-frame Z-rotation animation (full 360-degree spin).
    - Sun light at 45-degree angle.

The resulting .blend is saved to ``cooker.blend`` in the current working
directory (see TODO below).

KEY EXPORTS
===========
None -- this is a top-level script, not a library module.

PIPELINE CONTEXT
================
1. **create_cooker_asset.py** (this file)  -->  Build .blend scene
2. motion_template.py                      -->  (optional) rig + animate
3. walk_anim_tool.py                       -->  Orchestrate animated render
4. render_turntable.py / render_sprite.py  -->  Turntable render to PNG frames
5. asset_gen pipeline                      -->  Quantize -> XP sprite sheets
"""

import bpy
import math
import os

# Clear all scene data and start from Blender factory defaults.
# WHY use_empty=True: gives a truly blank scene (no default cube/camera/light)
# so the script has full control over what is present.
bpy.ops.wm.read_factory_settings(use_empty=True)

# Add Suzanne (The "Cooker").
# WHY Suzanne: Blender's built-in monkey head is a quick, recognisable test
# mesh with enough geometric detail to validate the render pipeline.
bpy.ops.mesh.primitive_monkey_add(location=(0,0,1))
obj = bpy.context.object
obj.name = "Cooker"

# Add Material (Orange/Gold).
# WHY orange: distinctive colour that is easy to verify visually in rendered
# sprite output and contrasts well against the magenta transparency key.
mat = bpy.data.materials.new(name="CookerMat")
mat.use_nodes = True
nodes = mat.node_tree.nodes
bsdf = nodes.get("Principled BSDF")
if bsdf:
    # RGBA tuple -- fully opaque orange/gold.
    bsdf.inputs['Base Color'].default_value = (1, 0.5, 0, 1)
obj.data.materials.append(mat)

# Animation (Spin) -- full 360-degree Z rotation over 8 frames.
# WHY 8 frames: matches the asciicker sprite sheet convention (8 angles).
# WHY frame 9 for end keyframe: Blender interpolates between frame 1 and 9,
# giving 8 distinct poses at frames 1-8.  Frame 9 itself is one past the
# end so the loop wraps seamlessly.
scene = bpy.context.scene
scene.frame_start = 1
scene.frame_end = 8

obj.rotation_mode = 'XYZ'
obj.keyframe_insert(data_path="rotation_euler", frame=1)
obj.rotation_euler.z = math.radians(360)
obj.keyframe_insert(data_path="rotation_euler", frame=9)

# Add Light -- single sun lamp for consistent directional lighting.
# WHY 45-degree angles: classic 3/4 top-right illumination that gives good
# depth cues on the monkey head geometry.
bpy.ops.object.light_add(type='SUN', location=(10, -10, 10))
light = bpy.context.object
light.rotation_euler = (math.radians(45), 0, math.radians(45))
light.data.energy = 2.0

# Save the .blend file.
# TODO(PIPELINE-FIX): The makedirs call creates "scripts/blender/assets" but
# the save path is "cooker.blend" (cwd-relative).  Either save into the
# assets directory or remove the unused makedirs.
os.makedirs("scripts/blender/assets", exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath="cooker.blend")
