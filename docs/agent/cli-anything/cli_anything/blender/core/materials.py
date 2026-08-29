"""Material operations — list, create, assign, modify."""

from cli_anything.blender.core.bridge import BlenderBridge


def list_materials(blend_file, blender_path=None):
    """List all materials in the blend file."""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute("""
import bpy
_data = []
for mat in bpy.data.materials:
    entry = {
        "name": mat.name,
        "use_nodes": mat.use_nodes,
        "users": mat.users,
    }
    if mat.use_nodes and mat.node_tree:
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bc = bsdf.inputs.get("Base Color")
            if bc and hasattr(bc, 'default_value'):
                entry["base_color"] = [round(v, 3) for v in bc.default_value]
            metal = bsdf.inputs.get("Metallic")
            if metal:
                entry["metallic"] = round(metal.default_value, 3)
            rough = bsdf.inputs.get("Roughness")
            if rough:
                entry["roughness"] = round(rough.default_value, 3)
    _data.append(entry)
""")


def create_material(blend_file, name, color=None, metallic=None, roughness=None,
                    save=True, blender_path=None):
    """Create a new Principled BSDF material."""
    save_code = "bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)" if save else ""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)

    color_code = ""
    if color:
        if len(color) == 3:
            color = list(color) + [1.0]
        color_code = f"""
bc = bsdf.inputs.get("Base Color")
if bc:
    bc.default_value = {color}
"""
    metallic_code = ""
    if metallic is not None:
        metallic_code = f"""
m = bsdf.inputs.get("Metallic")
if m:
    m.default_value = {metallic}
"""
    roughness_code = ""
    if roughness is not None:
        roughness_code = f"""
r = bsdf.inputs.get("Roughness")
if r:
    r.default_value = {roughness}
"""

    return bridge.execute(f"""
import bpy
mat = bpy.data.materials.new(name={name!r})
mat.use_nodes = True
mat.use_fake_user = True
bsdf = mat.node_tree.nodes.get("Principled BSDF")
{color_code}
{metallic_code}
{roughness_code}
{save_code}
_data = {{
    "name": mat.name,
    "use_nodes": mat.use_nodes,
    "status": "created",
}}
""")


def assign_material(blend_file, object_name, material_name, save=True, blender_path=None):
    """Assign a material to an object."""
    save_code = "bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)" if save else ""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import bpy
obj = bpy.data.objects.get({object_name!r})
if obj is None:
    raise ValueError("Object '{object_name!s}' not found")
mat = bpy.data.materials.get({material_name!r})
if mat is None:
    raise ValueError("Material '{material_name!s}' not found")
if obj.data and hasattr(obj.data, 'materials'):
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
{save_code}
_data = {{
    "object": obj.name,
    "material": mat.name,
    "status": "assigned",
}}
""")
