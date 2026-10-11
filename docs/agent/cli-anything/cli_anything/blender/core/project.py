"""Project management — open, save, new blend files."""

from cli_anything.blender.core.bridge import BlenderBridge


def open_file(path, blender_path=None):
    """Open a .blend file and return scene info."""
    bridge = BlenderBridge(blend_file=path, blender_path=blender_path)
    return bridge.execute("""
import bpy
scene = bpy.context.scene
_data = {
    "file": bpy.data.filepath,
    "scene": scene.name,
    "objects": len(bpy.data.objects),
    "meshes": len(bpy.data.meshes),
    "materials": len(bpy.data.materials),
    "cameras": len(bpy.data.cameras),
    "lights": len(bpy.data.lights),
    "collections": len(bpy.data.collections),
    "render_engine": scene.render.engine,
    "resolution": [scene.render.resolution_x, scene.render.resolution_y],
    "fps": scene.render.fps,
    "frame_range": [scene.frame_start, scene.frame_end],
}
""")


def info(blend_file, blender_path=None):
    """Return detailed info about a blend file."""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute("""
import bpy
scene = bpy.context.scene

objects_info = []
for obj in bpy.data.objects:
    entry = {
        "name": obj.name,
        "type": obj.type,
        "location": list(obj.location),
        "rotation": list(obj.rotation_euler),
        "scale": list(obj.scale),
        "visible": obj.visible_get(),
    }
    if obj.type == 'MESH' and obj.data:
        entry["vertices"] = len(obj.data.vertices)
        entry["faces"] = len(obj.data.polygons)
        entry["edges"] = len(obj.data.edges)
    if obj.active_material:
        entry["material"] = obj.active_material.name
    objects_info.append(entry)

materials_info = []
for mat in bpy.data.materials:
    materials_info.append({
        "name": mat.name,
        "use_nodes": mat.use_nodes,
        "users": mat.users,
    })

_data = {
    "file": bpy.data.filepath,
    "blender_version": list(bpy.app.version),
    "scene": scene.name,
    "scenes": [s.name for s in bpy.data.scenes],
    "render_engine": scene.render.engine,
    "resolution": [scene.render.resolution_x, scene.render.resolution_y],
    "fps": scene.render.fps,
    "frame_range": [scene.frame_start, scene.frame_end],
    "objects": objects_info,
    "materials": materials_info,
    "collections": [c.name for c in bpy.data.collections],
    "images": [i.name for i in bpy.data.images],
    "world": scene.world.name if scene.world else None,
}
""")


def new(output_path=None, blender_path=None):
    """Create a new empty blend file."""
    bridge = BlenderBridge(blender_path=blender_path)
    save_code = ""
    if output_path:
        save_code = f"""
bpy.ops.wm.save_as_mainfile(filepath={output_path!r})
"""
    return bridge.execute(f"""
import bpy
# Delete default objects for a clean slate
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()
{save_code}
_data = {{
    "file": bpy.data.filepath or "(unsaved)",
    "objects": len(bpy.data.objects),
    "status": "created",
}}
""")


def save_as(blend_file, output_path, blender_path=None):
    """Open a blend file and save as a new path."""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import bpy
bpy.ops.wm.save_as_mainfile(filepath={output_path!r})
_data = {{
    "saved_to": {output_path!r},
    "objects": len(bpy.data.objects),
    "status": "saved",
}}
""")
