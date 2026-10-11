# DEPRECATED: Use io_asciicker instead (Blender 4.5+)
#
# This addon is superseded by the unified io_asciicker addon which provides
# the same AKM import/export plus A3D map export, terrain painting, and more.
#
# Source modules (import_akm.py, export_akm.py, color_tools.py) are preserved
# so that existing scripts using `from io_mesh_akm import import_akm` continue
# to work.  However, Blender operator registration is disabled to prevent
# duplicate bl_idname conflicts with io_asciicker.

bl_info = {
    "name": "Asciicker AKM format (Deprecated)",
    "author": "Gumix",
    "version": (1, 2, 0),
    "blender": (2, 82, 0),
    "location": "File > Import-Export",
    "description": "DEPRECATED: Use io_asciicker instead",
    "warning": "Deprecated: use io_asciicker",
    "category": "Import-Export",
}

DEPRECATION_MSG = (
    "io_mesh_akm is deprecated on Blender 4.5+. "
    "Use io_asciicker which provides the same AKM import/export "
    "plus A3D map export, terrain tools, and more."
)


def register():
    print(f"WARNING: {DEPRECATION_MSG}")


def unregister():
    pass


if __name__ == "__main__":
    register()
