"""Rendering — render images and animations from blend files."""

import os

from cli_anything.blender.core.bridge import BlenderBridge


def render_image(blend_file, output, engine=None, resolution=None,
                 samples=None, format=None, camera=None, scene=None,
                 transparent=False, blender_path=None):
    """Render a single frame to an image file."""
    output = os.path.abspath(output)
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)

    setup_lines = []
    if scene:
        setup_lines.append(f"bpy.context.window.scene = bpy.data.scenes[{scene!r}]")
    if engine:
        setup_lines.append(f"bpy.context.scene.render.engine = {engine.upper()!r}")
    if resolution:
        w, h = resolution
        setup_lines.append(f"bpy.context.scene.render.resolution_x = {w}")
        setup_lines.append(f"bpy.context.scene.render.resolution_y = {h}")
    if samples:
        setup_lines.append(f"""
if bpy.context.scene.render.engine == 'CYCLES':
    bpy.context.scene.cycles.samples = {samples}
elif bpy.context.scene.render.engine in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'):
    bpy.context.scene.eevee.taa_render_samples = {samples}
""")
    if format:
        setup_lines.append(f"bpy.context.scene.render.image_settings.file_format = {format.upper()!r}")
    if camera:
        setup_lines.append(f"""
cam = bpy.data.objects.get({camera!r})
if cam and cam.type == 'CAMERA':
    bpy.context.scene.camera = cam
else:
    raise ValueError("Camera '{camera!s}' not found")
""")
    if transparent:
        setup_lines.append("bpy.context.scene.render.film_transparent = True")

    setup = "\n".join(setup_lines)

    return bridge.execute(f"""
import bpy, os
{setup}
# Auto-create camera if none exists
if bpy.context.scene.camera is None:
    cams = [o for o in bpy.data.objects if o.type == 'CAMERA']
    if cams:
        bpy.context.scene.camera = cams[0]
    else:
        bpy.ops.object.camera_add(location=(7.36, -6.93, 4.96))
        cam = bpy.context.active_object
        cam.rotation_euler = (1.11, 0, 0.79)
        bpy.context.scene.camera = cam
bpy.context.scene.render.filepath = {output!r}
bpy.ops.render.render(write_still=True)
_data = {{
    "output": {output!r},
    "exists": os.path.isfile({output!r}),
    "engine": bpy.context.scene.render.engine,
    "resolution": [bpy.context.scene.render.resolution_x, bpy.context.scene.render.resolution_y],
    "status": "rendered",
}}
""")


def render_animation(blend_file, output_dir, frame_start=None, frame_end=None,
                     engine=None, resolution=None, format=None,
                     blender_path=None):
    """Render an animation frame range."""
    output_dir = os.path.abspath(output_dir)
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)

    setup_lines = []
    if engine:
        setup_lines.append(f"bpy.context.scene.render.engine = {engine.upper()!r}")
    if resolution:
        w, h = resolution
        setup_lines.append(f"bpy.context.scene.render.resolution_x = {w}")
        setup_lines.append(f"bpy.context.scene.render.resolution_y = {h}")
    if format:
        setup_lines.append(f"bpy.context.scene.render.image_settings.file_format = {format.upper()!r}")
    if frame_start is not None:
        setup_lines.append(f"bpy.context.scene.frame_start = {frame_start}")
    if frame_end is not None:
        setup_lines.append(f"bpy.context.scene.frame_end = {frame_end}")
    setup = "\n".join(setup_lines)

    return bridge.execute(f"""
import bpy, os, glob
{setup}
outdir = {output_dir!r}
os.makedirs(outdir, exist_ok=True)
bpy.context.scene.render.filepath = os.path.join(outdir, "frame_")
bpy.ops.render.render(animation=True)
frames = sorted(glob.glob(os.path.join(outdir, "frame_*")))
_data = {{
    "output_dir": outdir,
    "frame_start": bpy.context.scene.frame_start,
    "frame_end": bpy.context.scene.frame_end,
    "frames_rendered": len(frames),
    "files": [os.path.basename(f) for f in frames[:20]],
    "status": "rendered",
}}
""", timeout=600)


def list_engines(blender_path=None):
    """List available render engines."""
    bridge = BlenderBridge(blender_path=blender_path)
    return bridge.execute("""
import bpy
engines = []
for cls in bpy.types.RenderEngine.__subclasses__():
    try:
        engines.append({"id": cls.bl_idname, "label": cls.bl_label})
    except AttributeError:
        pass
# Always include builtins
for eid, elabel in [("BLENDER_EEVEE_NEXT", "EEVEE"), ("CYCLES", "Cycles"), ("BLENDER_WORKBENCH", "Workbench")]:
    if not any(e["id"] == eid for e in engines):
        engines.append({"id": eid, "label": elabel})
_data = engines
""")
