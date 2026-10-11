# DEPRECATED: Use io_asciicker instead (Blender 4.5+)
#
# This addon is superseded by the unified io_asciicker addon which provides
# A3D map export via io_asciicker/scene/export_a3d.py.
#
# Source modules (export_a3d.py, a3d_format.py, default_materials.py) are
# preserved for direct Python imports.  Blender operator registration is
# disabled to prevent duplicate bl_idname conflicts with io_asciicker.

bl_info = {
    "name": "Asciicker A3D Map Export (Deprecated)",
    "author": "Asciicker Project",
    "version": (1, 0, 0),
    "blender": (2, 82, 0),
    "location": "File > Export",
    "description": "DEPRECATED: Use io_asciicker instead",
    "warning": "Deprecated: use io_asciicker",
    "category": "Import-Export",
}

DEPRECATION_MSG = (
    "io_scene_a3d is deprecated on Blender 4.5+. "
    "Use io_asciicker which provides A3D map export "
    "via File > Export > Asciicker Map (.a3d)."
)


def register():
    print(f"WARNING: {DEPRECATION_MSG}")


def unregister():
    pass


if __name__ == "__main__":
    register()
