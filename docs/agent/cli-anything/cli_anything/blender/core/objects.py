"""Object operations — list, add, delete, transform, duplicate."""

from cli_anything.blender.core.bridge import BlenderBridge


def list_objects(blend_file, blender_path=None, type_filter=None):
    """List all objects in the blend file."""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    filter_code = ""
    if type_filter:
        filter_code = f"objs = [o for o in objs if o.type == {type_filter.upper()!r}]"
    return bridge.execute(f"""
import bpy
objs = list(bpy.data.objects)
{filter_code}
_data = [{{
    "name": o.name,
    "type": o.type,
    "location": [round(v, 4) for v in o.location],
    "rotation": [round(v, 4) for v in o.rotation_euler],
    "scale": [round(v, 4) for v in o.scale],
    "visible": o.visible_get(),
    "parent": o.parent.name if o.parent else None,
    "collections": [c.name for c in o.users_collection],
}} for o in objs]
""")


def add_object(blend_file, obj_type, name=None, location=None, save=True, blender_path=None):
    """Add a primitive object to the scene."""
    loc = location or [0, 0, 0]
    add_ops = {
        "cube": "bpy.ops.mesh.primitive_cube_add(location=loc)",
        "sphere": "bpy.ops.mesh.primitive_uv_sphere_add(location=loc)",
        "cylinder": "bpy.ops.mesh.primitive_cylinder_add(location=loc)",
        "plane": "bpy.ops.mesh.primitive_plane_add(location=loc)",
        "cone": "bpy.ops.mesh.primitive_cone_add(location=loc)",
        "torus": "bpy.ops.mesh.primitive_torus_add(location=loc)",
        "monkey": "bpy.ops.mesh.primitive_monkey_add(location=loc)",
        "circle": "bpy.ops.mesh.primitive_circle_add(location=loc)",
        "empty": "bpy.ops.object.empty_add(location=loc)",
        "camera": "bpy.ops.object.camera_add(location=loc)",
        "light_point": "bpy.ops.object.light_add(type='POINT', location=loc)",
        "light_sun": "bpy.ops.object.light_add(type='SUN', location=loc)",
        "light_spot": "bpy.ops.object.light_add(type='SPOT', location=loc)",
        "light_area": "bpy.ops.object.light_add(type='AREA', location=loc)",
    }
    op_call = add_ops.get(obj_type.lower())
    if not op_call:
        return {"ok": False, "error": f"Unknown type '{obj_type}'. Valid: {', '.join(sorted(add_ops))}"}

    save_code = f"bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)" if save else ""
    name_code = f"bpy.context.active_object.name = {name!r}" if name else ""

    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import bpy
loc = {loc}
{op_call}
{name_code}
obj = bpy.context.active_object
{save_code}
_data = {{
    "name": obj.name,
    "type": obj.type,
    "location": list(obj.location),
    "status": "added",
}}
""")


def delete_object(blend_file, name, save=True, blender_path=None):
    """Delete an object by name."""
    save_code = "bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)" if save else ""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import bpy
obj = bpy.data.objects.get({name!r})
if obj is None:
    raise ValueError(f"Object '{{name}}' not found")
bpy.data.objects.remove(obj, do_unlink=True)
{save_code}
_data = {{"deleted": {name!r}, "remaining": len(bpy.data.objects), "status": "deleted"}}
""")


def transform(blend_file, name, location=None, rotation=None, scale=None,
              save=True, blender_path=None):
    """Set transform on an object."""
    save_code = "bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)" if save else ""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    loc_code = f"obj.location = {location}" if location else ""
    rot_code = f"obj.rotation_euler = {rotation}" if rotation else ""
    sc_code = f"obj.scale = {scale}" if scale else ""
    return bridge.execute(f"""
import bpy
obj = bpy.data.objects.get({name!r})
if obj is None:
    raise ValueError("Object '{name!s}' not found")
{loc_code}
{rot_code}
{sc_code}
{save_code}
_data = {{
    "name": obj.name,
    "location": [round(v, 4) for v in obj.location],
    "rotation": [round(v, 4) for v in obj.rotation_euler],
    "scale": [round(v, 4) for v in obj.scale],
    "status": "transformed",
}}
""")


def duplicate(blend_file, name, new_name=None, offset=None, save=True, blender_path=None):
    """Duplicate an object."""
    save_code = "bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)" if save else ""
    offset_code = ""
    if offset:
        offset_code = f"new_obj.location.x += {offset[0]}; new_obj.location.y += {offset[1]}; new_obj.location.z += {offset[2]}"
    name_code = f"new_obj.name = {new_name!r}" if new_name else ""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import bpy
src = bpy.data.objects.get({name!r})
if src is None:
    raise ValueError("Object '{name!s}' not found")
new_obj = src.copy()
if src.data:
    new_obj.data = src.data.copy()
bpy.context.collection.objects.link(new_obj)
{name_code}
{offset_code}
{save_code}
_data = {{
    "original": src.name,
    "duplicate": new_obj.name,
    "location": list(new_obj.location),
    "status": "duplicated",
}}
""")
