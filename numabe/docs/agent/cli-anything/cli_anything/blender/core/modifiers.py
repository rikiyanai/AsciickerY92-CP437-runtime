"""Modifier operations — add, remove, apply modifiers on objects."""

from cli_anything.blender.core.bridge import BlenderBridge


def list_modifiers(blend_file, object_name, blender_path=None):
    """List modifiers on an object."""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import bpy
obj = bpy.data.objects.get({object_name!r})
if obj is None:
    raise ValueError("Object '{object_name!s}' not found")
_data = []
for mod in obj.modifiers:
    entry = {{
        "name": mod.name,
        "type": mod.type,
        "show_viewport": mod.show_viewport,
        "show_render": mod.show_render,
    }}
    # Add type-specific properties
    if mod.type == 'SUBSURF':
        entry["levels"] = mod.levels
        entry["render_levels"] = mod.render_levels
    elif mod.type == 'MIRROR':
        entry["use_axis"] = [mod.use_axis[0], mod.use_axis[1], mod.use_axis[2]]
    elif mod.type == 'ARRAY':
        entry["count"] = mod.count
    elif mod.type == 'SOLIDIFY':
        entry["thickness"] = mod.thickness
    elif mod.type == 'BEVEL':
        entry["width"] = mod.width
        entry["segments"] = mod.segments
    _data.append(entry)
""")


def add_modifier(blend_file, object_name, mod_type, name=None,
                 save=True, blender_path=None, **kwargs):
    """Add a modifier to an object.

    Common types: SUBSURF, MIRROR, ARRAY, SOLIDIFY, BEVEL, BOOLEAN,
    DECIMATE, SMOOTH, WIREFRAME, SHRINKWRAP, ARMATURE.
    """
    save_code = "bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)" if save else ""
    props_code = ""
    for k, v in kwargs.items():
        props_code += f"mod.{k} = {v!r}\n    "

    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    mod_name = name or mod_type.title()
    return bridge.execute(f"""
import bpy
obj = bpy.data.objects.get({object_name!r})
if obj is None:
    raise ValueError("Object '{object_name!s}' not found")
mod = obj.modifiers.new(name={mod_name!r}, type={mod_type.upper()!r})
{props_code}
{save_code}
_data = {{
    "object": obj.name,
    "modifier": mod.name,
    "type": mod.type,
    "status": "added",
}}
""")


def remove_modifier(blend_file, object_name, modifier_name, save=True, blender_path=None):
    """Remove a modifier from an object."""
    save_code = "bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)" if save else ""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import bpy
obj = bpy.data.objects.get({object_name!r})
if obj is None:
    raise ValueError("Object '{object_name!s}' not found")
mod = obj.modifiers.get({modifier_name!r})
if mod is None:
    raise ValueError("Modifier '{modifier_name!s}' not found on '{object_name!s}'")
obj.modifiers.remove(mod)
{save_code}
_data = {{
    "object": obj.name,
    "removed": {modifier_name!r},
    "remaining": len(obj.modifiers),
    "status": "removed",
}}
""")


def apply_modifier(blend_file, object_name, modifier_name, save=True, blender_path=None):
    """Apply (bake) a modifier on an object."""
    save_code = "bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)" if save else ""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import bpy
obj = bpy.data.objects.get({object_name!r})
if obj is None:
    raise ValueError("Object '{object_name!s}' not found")
bpy.context.view_layer.objects.active = obj
mod = obj.modifiers.get({modifier_name!r})
if mod is None:
    raise ValueError("Modifier '{modifier_name!s}' not found")
verts_before = len(obj.data.vertices) if obj.data and hasattr(obj.data, 'vertices') else 0
bpy.ops.object.modifier_apply(modifier={modifier_name!r})
verts_after = len(obj.data.vertices) if obj.data and hasattr(obj.data, 'vertices') else 0
{save_code}
_data = {{
    "object": obj.name,
    "applied": {modifier_name!r},
    "vertices_before": verts_before,
    "vertices_after": verts_after,
    "status": "applied",
}}
""")
