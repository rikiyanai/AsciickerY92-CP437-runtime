"""Import/Export — convert between file formats via Blender."""

import os

from cli_anything.blender.core.bridge import BlenderBridge

# Supported formats and their bpy.ops calls
IMPORT_FORMATS = {
    "fbx": "bpy.ops.import_scene.fbx(filepath={path!r})",
    "gltf": "bpy.ops.import_scene.gltf(filepath={path!r})",
    "glb": "bpy.ops.import_scene.gltf(filepath={path!r})",
    "obj": "bpy.ops.wm.obj_import(filepath={path!r})",
    "stl": "bpy.ops.wm.stl_import(filepath={path!r})",
    "ply": "bpy.ops.wm.ply_import(filepath={path!r})",
    "svg": "bpy.ops.import_curve.svg(filepath={path!r})",
    "bvh": "bpy.ops.import_anim.bvh(filepath={path!r})",
    "a3d": "bpy.ops.import_scene.a3d(filepath={path!r})",
    "akm": "bpy.ops.import_mesh.akm(filepath={path!r})",
}

EXPORT_FORMATS = {
    "fbx": "bpy.ops.export_scene.fbx(filepath={path!r})",
    "gltf": "bpy.ops.export_scene.gltf(filepath={path!r}, export_format='GLTF_SEPARATE')",
    "glb": "bpy.ops.export_scene.gltf(filepath={path!r}, export_format='GLB')",
    "obj": "bpy.ops.wm.obj_export(filepath={path!r})",
    "stl": "bpy.ops.wm.stl_export(filepath={path!r})",
    "ply": "bpy.ops.wm.ply_export(filepath={path!r})",
    "a3d": "bpy.ops.export_scene.a3d(filepath={path!r})",
    "akm": "bpy.ops.export_mesh.akm(filepath={path!r}, axis_forward='Y', axis_up='Z')",
}

# Formats requiring addon auto-enable before operator invocation
ADDON_REQUIRED = {
    "a3d": "io_asciicker",
    "akm": "io_asciicker",
}


def import_file(path, blend_file=None, output_blend=None, blender_path=None):
    """Import a file into Blender."""
    path = os.path.abspath(path)
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    fmt_template = IMPORT_FORMATS.get(ext)
    if not fmt_template:
        return {"ok": False, "error": f"Unsupported import format '.{ext}'. Supported: {', '.join(sorted(IMPORT_FORMATS))}"}

    import_call = fmt_template.format(path=path)
    save_code = f"bpy.ops.wm.save_as_mainfile(filepath={output_blend!r})" if output_blend else ""

    addon_enable = ""
    addon_module = ADDON_REQUIRED.get(ext)
    if addon_module:
        addon_enable = f"import addon_utils; addon_utils.enable({addon_module!r}, default_set=True)"

    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import bpy, os
{addon_enable}
before = set(o.name for o in bpy.data.objects)
{import_call}
after = set(o.name for o in bpy.data.objects)
new_objects = sorted(after - before)
{save_code}
_data = {{
    "imported_from": {path!r},
    "format": {ext!r},
    "new_objects": new_objects,
    "total_objects": len(bpy.data.objects),
    "status": "imported",
}}
""")


def export_file(blend_file, output, format=None, selected_only=False, blender_path=None):
    """Export from a blend file to another format."""
    output = os.path.abspath(output)
    ext = format or os.path.splitext(output)[1].lstrip(".").lower()
    fmt_template = EXPORT_FORMATS.get(ext)
    if not fmt_template:
        return {"ok": False, "error": f"Unsupported export format '.{ext}'. Supported: {', '.join(sorted(EXPORT_FORMATS))}"}

    export_call = fmt_template.format(path=output)
    # Add use_selection for formats that support it
    if selected_only and ext in ("fbx", "obj", "stl", "ply", "a3d", "akm"):
        export_call = export_call[:-1] + ", use_selection=True)"

    addon_enable = ""
    addon_module = ADDON_REQUIRED.get(ext)
    if addon_module:
        addon_enable = f"import addon_utils; addon_utils.enable({addon_module!r}, default_set=True)"

    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import bpy, os
{addon_enable}
os.makedirs(os.path.dirname({output!r}) or '.', exist_ok=True)
{export_call}
_data = {{
    "exported_to": {output!r},
    "format": {ext!r},
    "exists": os.path.isfile({output!r}),
    "size_bytes": os.path.getsize({output!r}) if os.path.isfile({output!r}) else 0,
    "status": "exported",
}}
""")


def convert(input_path, output_path, blender_path=None):
    """Convert between 3D file formats (import then export)."""
    input_path = os.path.abspath(input_path)
    output_path = os.path.abspath(output_path)

    in_ext = os.path.splitext(input_path)[1].lstrip(".").lower()
    out_ext = os.path.splitext(output_path)[1].lstrip(".").lower()

    in_template = IMPORT_FORMATS.get(in_ext)
    out_template = EXPORT_FORMATS.get(out_ext)

    if not in_template:
        return {"ok": False, "error": f"Cannot import '.{in_ext}'"}
    if not out_template:
        return {"ok": False, "error": f"Cannot export '.{out_ext}'"}

    import_call = in_template.format(path=input_path)
    export_call = out_template.format(path=output_path)

    # Collect distinct addon modules needed by either side
    addon_modules = set()
    for ext in (in_ext, out_ext):
        mod = ADDON_REQUIRED.get(ext)
        if mod:
            addon_modules.add(mod)
    addon_enable = "\n".join(
        f"import addon_utils; addon_utils.enable({m!r}, default_set=True)"
        for m in sorted(addon_modules)
    )

    bridge = BlenderBridge(blender_path=blender_path)
    return bridge.execute(f"""
import bpy, os
{addon_enable}
# Clear default scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()
# Import
{import_call}
# Export
os.makedirs(os.path.dirname({output_path!r}) or '.', exist_ok=True)
{export_call}
_data = {{
    "input": {input_path!r},
    "output": {output_path!r},
    "input_format": {in_ext!r},
    "output_format": {out_ext!r},
    "objects": len(bpy.data.objects),
    "exists": os.path.isfile({output_path!r}),
    "status": "converted",
}}
""")


def list_formats():
    """List supported import/export formats."""
    return {
        "ok": True,
        "data": {
            "import": sorted(IMPORT_FORMATS.keys()),
            "export": sorted(EXPORT_FORMATS.keys()),
        },
    }
