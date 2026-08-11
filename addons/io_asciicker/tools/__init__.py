# Asciicker Tools Module
# [DEPENDENCY:BLENDER] - All submodules register Blender operators.

"""
Asciicker Tools -- Subpackage Registration Hub
===============================================

ARCHITECTURE:
    This ``__init__.py`` aggregates every tool submodule and exposes a single
    ``register()`` / ``unregister()`` surface to the parent ``io_asciicker``
    addon package.  Each submodule defines its own ``classes`` tuple plus
    ``register()`` / ``unregister()`` functions that wrap
    ``bpy.utils.register_class`` / ``unregister_class``.

KEY EXPORTS:
    - ``register()``   -- Registers all tool operators with Blender.
    - ``unregister()`` -- Unregisters all tool operators (LIFO order).

PIPELINE CONTEXT:
    Tools are the *modeling-side* utilities that prepare Blender data for
    export through the mesh ([DATA-CONTRACT:AKM]) and scene
    ([DATA-CONTRACT:A3D]) pipelines:

    color_tools       -- Palette snapping (6x6x6 terminal), dither analysis,
                         collision vertex-group assignment.
    terrain_tools     -- Terrain plane creation with subdivisions and vertex
                         color layers; vertex-paint mode entry helper.
    material_painter  -- Quick-access brush presets mapping material IDs to
                         display colors for vertex painting.
    camera_tools      -- Viewport navigation shortcuts (zoom-out, frame
                         terrain, orthographic top view).
    buildify_tools    -- Converts Buildify procedural geometry-node objects
                         into real meshes suitable for AKM export.
    gmap_bake_tools   -- Full Google Maps photogrammetry pipeline: texture-to-
                         vertex-color baking, Cycles projection bake, GMap-to-
                         OSM alignment, raycast coverage check, and terrain
                         cleanup (tree removal, exterior flattening).

SUBMODULE ORDER:
    Registration order is deliberately color -> terrain -> material_painter
    -> camera -> buildify -> gmap_bake.  Unregistration reverses this to
    respect Blender's LIFO class dependency rules.
"""

from . import color_tools
from . import terrain_tools
from . import material_painter
from . import camera_tools
from . import buildify_tools
from . import gmap_bake_tools
from . import curve_volumizer
from . import vertex_color_applier
from . import building_painter
from . import pixel_render
from . import render_to_xp
from . import osm_terrain_painter
from . import osm_pipeline
from . import picocad_importer


def register():
    """Register all tool submodule operators with Blender."""
    color_tools.register()
    terrain_tools.register()
    material_painter.register()
    camera_tools.register()
    buildify_tools.register()
    gmap_bake_tools.register()
    curve_volumizer.register()
    vertex_color_applier.register()
    building_painter.register()
    pixel_render.register()
    render_to_xp.register()
    osm_terrain_painter.register()
    osm_pipeline.register()
    picocad_importer.register()


def unregister():
    """Unregister all tool submodule operators (reverse order)."""
    # WHY reverse order: Blender requires LIFO unregistration so that
    # operators depending on earlier-registered classes are removed first.
    picocad_importer.unregister()
    osm_pipeline.unregister()
    osm_terrain_painter.unregister()
    render_to_xp.unregister()
    pixel_render.unregister()
    building_painter.unregister()
    vertex_color_applier.unregister()
    curve_volumizer.unregister()
    gmap_bake_tools.unregister()
    buildify_tools.unregister()
    camera_tools.unregister()
    material_painter.unregister()
    terrain_tools.unregister()
    color_tools.unregister()
