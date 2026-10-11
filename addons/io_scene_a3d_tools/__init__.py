# DEPRECATED: Use io_asciicker instead (Blender 4.5+)
#
# This addon is superseded by the unified io_asciicker addon which provides
# terrain creation, material painting, mesh library, and bulk mesh import.
#
# Blender operator registration is disabled to prevent duplicate bl_idname
# conflicts with io_asciicker (which registers the same operator IDs:
# asciicker.create_terrain, asciicker.enter_paint_mode,
# asciicker.set_material_brush, etc.).

bl_info = {
    "name": "Asciicker Map Tools (Deprecated)",
    "author": "Asciicker Project",
    "version": (1, 0, 0),
    "blender": (2, 82, 0),
    "location": "View3D > Sidebar > Asciicker",
    "description": "DEPRECATED: Use io_asciicker instead",
    "warning": "Deprecated: use io_asciicker",
    "category": "3D View",
}

DEPRECATION_MSG = (
    "io_scene_a3d_tools is deprecated on Blender 4.5+. "
    "Use io_asciicker which provides terrain tools, material painting, "
    "mesh library, and bulk mesh import."
)


def register():
    print(f"WARNING: {DEPRECATION_MSG}")


def unregister():
    pass


if __name__ == "__main__":
    register()
