# Asciicker Blender Addon
# Unified tools for Asciicker game development
# [DEPENDENCY:BLENDER] - Requires Blender 4.0+

"""
Asciicker Blender Tools - Addon Registration Entry Point
=========================================================

ARCHITECTURE:
    This module is the top-level ``__init__.py`` for the ``io_asciicker`` Blender
    addon package.  Blender discovers it via ``bl_info`` and calls ``register()``
    / ``unregister()`` at addon enable/disable time.  All real functionality is
    delegated to four submodule packages which are imported lazily inside
    ``register_submodules()`` to avoid import-time side effects.

KEY EXPORTS:
    - ``register()``   -- Called by Blender when the addon is enabled.
    - ``unregister()`` -- Called by Blender when the addon is disabled.
    - ``bl_info``      -- Addon metadata dictionary required by Blender.

PIPELINE CONTEXT:
    The addon bridges Blender and the Asciicker C++ engine:

    mesh   -- [DATA-CONTRACT:AKM] Import/Export for .akm (Asciicker Mesh)
              files, which use PLY-based encoding with engine-specific vertex
              attribute conventions (palette-safe colors, collision alpha).
    scene  -- [DATA-CONTRACT:A3D] Scene-level management for .a3d world maps.
    tools  -- Specialized modeling/baking utilities:
              - gmap_bake_tools:   Bake Google Maps textures to vertex colors.
              - terrain_tools:     Custom terrain painting and manipulation.
              - material_painter:  Material assignment utilities.
    ui     -- Sidebar panels (View3D > Sidebar > Asciicker).

Modules:
    - mesh: Import/Export logic for .akm (Asciicker Mesh) format.
    - scene: Scene-level management for .a3d (Asciicker Map) format.
    - tools: specialized modeling and baking utilities.
        - gmap_bake_tools: Baking Google Maps textures to vertex colors.
        - terrain_tools: Custom terrain painting and manipulation.
        - material_painter: Material assignment utilities.
    - ui: Panel definitions and interface logic.

Registration:
    The addon uses a submodule registration pattern. ``register_submodules``
    iterates through the main packages (mesh, scene, tools, ui) and calls
    their respective ``register()`` functions.  Unregistration walks the list
    in reverse order to respect Blender's LIFO class dependency rules.
"""

# [DEPENDENCY:BLENDER] bl_info is parsed by Blender at startup (even before
# the module body runs) to populate Edit > Preferences > Add-ons metadata.
bl_info = {
    "name": "Asciicker Tools",
    "author": "Asciicker Project",
    "version": (2, 0, 0),
    "blender": (4, 0, 0),
    "location": "File > Import-Export, View3D > Sidebar > Asciicker",
    "description": "Import-Export AKM meshes, A3D maps, and terrain painting tools",
    "category": "Import-Export",
}

import bpy  # [DEPENDENCY:BLENDER] core API -- all operator registration flows through this

# Module-level list that tracks successfully imported submodule packages.
# Populated inside register_submodules(); consumed (in reverse) by
# unregister_submodules() to respect Blender's LIFO dependency ordering.
submodules = []


def register_submodules():
    """Import and register every submodule package.

    Imports are deferred to this function (rather than module-level) so that
    missing optional packages (scene, tools, ui) do not prevent the core
    mesh I/O from loading.

    Raises:
        ImportError: If a required subpackage is missing from the addon tree.
    """
    global submodules

    from . import mesh
    from . import scene
    from . import tools
    from . import ui

    submodules = [mesh, scene, tools, ui]

    try:
        from . import sprite_sheet
    except Exception as exc:
        # Sprite sheet export is optional; keep core terrain/import/export tools live
        # when Pillow or another sprite-sheet dependency is unavailable in Blender.
        print(f"Asciicker Tools: sprite_sheet disabled: {exc}")
    else:
        submodules.insert(-1, sprite_sheet)

    for mod in submodules:
        mod.register()


def unregister_submodules():
    """Unregister all submodules in reverse order.

    Reverse iteration ensures that classes registered last (which may depend
    on earlier ones) are removed first, matching Blender's expected teardown
    sequence.
    """
    global submodules

    # WHY reversed: Blender requires LIFO unregistration for class dependencies
    for mod in reversed(submodules):
        mod.unregister()


def register():
    """Main addon registration -- called by Blender when the user enables the addon."""
    # TODO(PIPELINE-FIX): Broad except swallows all registration errors and only
    # prints to console; Blender shows no user-visible notification.  Consider
    # re-raising after logging or using self.report() in a wrapper operator.
    try:
        register_submodules()
        print(f"Asciicker Tools {'.'.join(map(str, bl_info['version']))} registered successfully")
    except Exception as e:
        print(f"Asciicker Tools: Registration failed: {e}")
        import traceback
        traceback.print_exc()


def unregister():
    """Main addon unregistration -- called by Blender when the user disables the addon.

    Note:
        Errors are caught and printed but not re-raised so that Blender can
        continue its shutdown sequence even if one submodule fails to clean up.
    """
    try:
        unregister_submodules()
        print("Asciicker Tools unregistered")
    except Exception as e:
        print(f"Asciicker Tools: Unregistration failed: {e}")


# WHY: __main__ guard is conventional but Blender never executes addon
# __init__.py as a script; kept for manual testing via `blender --python`.
if __name__ == "__main__":
    register()
