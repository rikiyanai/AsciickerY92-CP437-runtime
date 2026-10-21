"""Blender addon management — enable, disable, list, and query addons."""

from cli_anything.blender.core.bridge import BlenderBridge


def enable_addon(module_name, blend_file=None, blender_path=None):
    """Enable a Blender addon by module name.

    Uses addon_utils.enable() which works in --background mode.
    Idempotent: enabling an already-enabled addon returns success.

    Args:
        module_name: Addon module name (e.g. 'io_asciicker').
        blend_file: Optional .blend file to open first.
        blender_path: Optional path to Blender executable.

    Returns:
        BlenderBridge result dict with addon info.
    """
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import addon_utils

mod = addon_utils.enable({module_name!r}, default_set=True)
is_loaded, is_enabled = addon_utils.check({module_name!r})

bl_info = {{}}
for m in addon_utils.modules():
    if m.__name__ == {module_name!r}:
        bl_info = getattr(m, 'bl_info', {{}})
        break

_data = {{
    "module": {module_name!r},
    "status": "enabled" if is_enabled else "failed",
    "loaded": is_loaded,
    "enabled": is_enabled,
    "bl_info": dict(bl_info) if bl_info else {{}},
}}
""")


def disable_addon(module_name, blend_file=None, blender_path=None):
    """Disable a Blender addon by module name.

    Args:
        module_name: Addon module name (e.g. 'io_asciicker').
        blend_file: Optional .blend file to open first.
        blender_path: Optional path to Blender executable.

    Returns:
        BlenderBridge result dict.
    """
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import addon_utils

addon_utils.disable({module_name!r}, default_set=True)
is_loaded, is_enabled = addon_utils.check({module_name!r})

_data = {{
    "module": {module_name!r},
    "status": "disabled" if not is_enabled else "still_enabled",
    "loaded": is_loaded,
    "enabled": is_enabled,
}}
""")


def list_addons(addon_state=None, blend_file=None, blender_path=None):
    """List Blender addons, optionally filtered by state.

    Args:
        addon_state: Filter by 'ENABLED' or 'DISABLED'. None returns all.
        blend_file: Optional .blend file to open first.
        blender_path: Optional path to Blender executable.

    Returns:
        BlenderBridge result dict with addon list.
    """
    state_filter = repr(addon_state) if addon_state else "None"
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import addon_utils

state_filter = {state_filter}
addons = []
for mod in addon_utils.modules():
    name = mod.__name__
    is_loaded, is_enabled = addon_utils.check(name)
    if state_filter == 'ENABLED' and not is_enabled:
        continue
    if state_filter == 'DISABLED' and is_enabled:
        continue
    bl_info = getattr(mod, 'bl_info', {{}})
    addons.append({{
        "module": name,
        "enabled": is_enabled,
        "loaded": is_loaded,
        "name": bl_info.get("name", name),
        "version": list(bl_info.get("version", [])),
        "category": bl_info.get("category", ""),
    }})

addons.sort(key=lambda a: a["module"])
_data = {{"addons": addons, "count": len(addons)}}
""")


def addon_status(module_name, blend_file=None, blender_path=None):
    """Query the status of a specific addon.

    Args:
        module_name: Addon module name (e.g. 'io_asciicker').
        blend_file: Optional .blend file to open first.
        blender_path: Optional path to Blender executable.

    Returns:
        BlenderBridge result dict with addon state and bl_info.
    """
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import addon_utils

is_loaded, is_enabled = addon_utils.check({module_name!r})

bl_info = {{}}
found = False
for m in addon_utils.modules():
    if m.__name__ == {module_name!r}:
        bl_info = getattr(m, 'bl_info', {{}})
        found = True
        break

_data = {{
    "module": {module_name!r},
    "found": found,
    "enabled": is_enabled,
    "loaded": is_loaded,
    "bl_info": dict(bl_info) if bl_info else {{}},
}}
""")
