"""Scene management — list scenes, switch active scene, settings."""

from cli_anything.blender.core.bridge import BlenderBridge


def list_scenes(blend_file, blender_path=None):
    """List all scenes in a blend file."""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute("""
import bpy
_data = []
for scene in bpy.data.scenes:
    _data.append({
        "name": scene.name,
        "objects": len(scene.objects),
        "collections": len(scene.collection.children),
        "render_engine": scene.render.engine,
        "resolution": [scene.render.resolution_x, scene.render.resolution_y],
        "fps": scene.render.fps,
        "frame_range": [scene.frame_start, scene.frame_end],
        "camera": scene.camera.name if scene.camera else None,
        "world": scene.world.name if scene.world else None,
        "is_active": scene == bpy.context.scene,
    })
""")


def get_scene(blend_file, scene_name=None, blender_path=None):
    """Get detailed info about a scene."""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    scene_sel = f"scene = bpy.data.scenes[{scene_name!r}]" if scene_name else "scene = bpy.context.scene"
    return bridge.execute(f"""
import bpy
{scene_sel}
r = scene.render
_data = {{
    "name": scene.name,
    "objects": [o.name for o in scene.objects],
    "camera": scene.camera.name if scene.camera else None,
    "world": scene.world.name if scene.world else None,
    "render": {{
        "engine": r.engine,
        "resolution_x": r.resolution_x,
        "resolution_y": r.resolution_y,
        "resolution_percentage": r.resolution_percentage,
        "fps": r.fps,
        "file_format": r.image_settings.file_format,
        "color_mode": r.image_settings.color_mode,
        "film_transparent": r.film_transparent,
        "filepath": r.filepath,
    }},
    "frame_start": scene.frame_start,
    "frame_end": scene.frame_end,
    "frame_current": scene.frame_current,
    "gravity": list(scene.gravity),
    "unit_system": scene.unit_settings.system,
    "unit_scale": scene.unit_settings.scale_length,
}}
""")


def set_render_settings(blend_file, engine=None, resolution=None, fps=None,
                        format=None, samples=None, save=True, blender_path=None):
    """Update render settings on the active scene."""
    save_code = "bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)" if save else ""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)

    lines = []
    if engine:
        lines.append(f"scene.render.engine = {engine.upper()!r}")
    if resolution:
        lines.append(f"scene.render.resolution_x = {resolution[0]}")
        lines.append(f"scene.render.resolution_y = {resolution[1]}")
    if fps:
        lines.append(f"scene.render.fps = {fps}")
    if format:
        lines.append(f"scene.render.image_settings.file_format = {format.upper()!r}")
    if samples:
        lines.append(f"""
if scene.render.engine == 'CYCLES':
    scene.cycles.samples = {samples}
""")
    setup = "\n".join(lines)

    return bridge.execute(f"""
import bpy
scene = bpy.context.scene
{setup}
{save_code}
_data = {{
    "engine": scene.render.engine,
    "resolution": [scene.render.resolution_x, scene.render.resolution_y],
    "fps": scene.render.fps,
    "format": scene.render.image_settings.file_format,
    "status": "updated",
}}
""")
