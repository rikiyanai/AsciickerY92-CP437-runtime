# Asciicker Camera Tools
# Viewport navigation helpers for terrain editing
# [DEPENDENCY:BLENDER] - Operators registered via bpy.utils.register_class

"""
Asciicker Camera Tools -- Viewport Navigation Helpers
======================================================

ARCHITECTURE:
    Three simple Blender operators that provide one-click viewport navigation
    shortcuts.  These are wired to the Asciicker sidebar panel buttons and are
    used most often during terrain painting sessions where the user needs to
    quickly re-orient the viewport.

KEY EXPORTS:
    - ``ASCIICKER_OT_zoom_out``       -- Frame all scene objects in viewport.
    - ``ASCIICKER_OT_frame_terrain``   -- Select + frame the terrain mesh.
    - ``ASCIICKER_OT_top_view``        -- Switch to orthographic top-down view.

PIPELINE CONTEXT:
    These operators have no data-format side effects.  They exist purely as
    viewport convenience for the terrain painting workflow that feeds into the
    [DATA-CONTRACT:AKM] vertex-color export path.
"""

import bpy
from bpy.types import Operator


class ASCIICKER_OT_zoom_out(Operator):
    """Zoom out to see all objects in viewport."""
    bl_idname = "asciicker.zoom_out"
    bl_label = "Zoom Out"
    bl_description = "Frame all visible objects in viewport"
    bl_options = {'REGISTER'}

    def execute(self, context):
        """Frame all visible objects in the 3D viewport."""
        # Frame all visible objects
        bpy.ops.view3d.view_all(center=True)
        self.report({'INFO'}, "Framed all objects")
        return {'FINISHED'}


class ASCIICKER_OT_frame_terrain(Operator):
    """Frame terrain mesh in viewport.

    Searches for an object whose name starts with ``Terrain`` (case-insensitive),
    selects it, makes it active, and frames it with ``view_selected``.
    """
    bl_idname = "asciicker.frame_terrain"
    bl_label = "Frame Terrain"
    bl_description = "Frame the terrain mesh in viewport"
    bl_options = {'REGISTER'}

    def execute(self, context):
        """Find the terrain object by name, select it, and frame it in the viewport."""
        # Find terrain object by name convention (must start with "terrain")
        terrain = None
        for obj in context.scene.objects:
            if obj.name.lower() == 'terrain' or obj.name.lower().startswith('terrain'):
                terrain = obj
                break

        if terrain is None:
            self.report({'WARNING'}, "No terrain found (name should start with 'Terrain')")
            return {'CANCELLED'}

        # Select and frame terrain
        bpy.ops.object.select_all(action='DESELECT')
        terrain.select_set(True)
        context.view_layer.objects.active = terrain
        bpy.ops.view3d.view_selected(use_all_regions=False)

        self.report({'INFO'}, f"Framed {terrain.name}")
        return {'FINISHED'}


class ASCIICKER_OT_top_view(Operator):
    """Switch to orthographic top-down view.

    This is the recommended viewport orientation for vertex-color terrain
    painting because it provides a map-like, undistorted view of the
    terrain plane.
    """
    bl_idname = "asciicker.top_view"
    bl_label = "Top View"
    bl_description = "Switch to orthographic top-down view (good for painting)"
    bl_options = {'REGISTER'}

    def execute(self, context):
        """Set viewport to top-down axis and switch projection to orthographic."""
        # Switch to top view
        bpy.ops.view3d.view_axis(type='TOP')

        # WHY iterate areas: Blender has no direct API to set the 3D viewport
        # projection; we must locate the VIEW_3D space and flip its
        # region_3d.view_perspective to 'ORTHO'.
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.region_3d.view_perspective = 'ORTHO'
                        break
                break

        self.report({'INFO'}, "Switched to top orthographic view")
        return {'FINISHED'}


classes = (
    ASCIICKER_OT_zoom_out,
    ASCIICKER_OT_frame_terrain,
    ASCIICKER_OT_top_view,
)


def register():
    """Register camera tool operators with Blender."""
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    """Unregister camera tool operators (reverse order)."""
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
