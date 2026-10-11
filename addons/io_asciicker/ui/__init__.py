# Asciicker UI Panels
# [DEPENDENCY:BLENDER] - Blender addon UI subpackage

"""
Asciicker UI Subpackage -- Panel Registration Gateway
======================================================

ARCHITECTURE:
    This ``__init__.py`` is the entry point for the ``io_asciicker.ui``
    subpackage.  It exists solely to delegate ``register()`` /
    ``unregister()`` calls to the ``panels`` module, which contains all
    concrete Blender ``Panel``, ``Operator``, and ``PropertyGroup``
    definitions for the Asciicker sidebar tab.

    The two-level indirection (addon ``__init__`` -> ``ui/__init__`` ->
    ``ui/panels``) follows the standard Blender addon submodule pattern so
    that each functional area (mesh, scene, tools, ui) can be
    registered/unregistered independently.

KEY EXPORTS:
    - ``register()``   -- Registers all UI panels, operators, and the
                          scene-level ``AsciickerProperties`` property group.
    - ``unregister()`` -- Tears down the above in reverse order.

PIPELINE CONTEXT:
    The UI layer sits at the top of the addon stack and references
    operators defined in *every* other subpackage:

        [mesh]  -- AKM import/export operators   [DATA-CONTRACT:AKM]
        [scene] -- A3D map export operators       [DATA-CONTRACT:A3D]
        [tools] -- Terrain, material-paint, GMap bake, color-snap operators

    Panel draw methods invoke these operators by ``bl_idname`` strings,
    meaning the UI subpackage has a *soft* (string-based) dependency on
    all other subpackages but no direct Python import coupling.
"""

# TODO(PIPELINE-FIX): if panels.py is missing or has import errors, the
# entire addon registration will fail with an opaque ImportError.  Consider
# wrapping in a try/except with a clear error message for artists.
from . import panels


def register():
    """Register all Asciicker sidebar panels and operators with Blender.

    [DEPENDENCY:BLENDER] Delegates to ``panels.register()`` which calls
    ``bpy.utils.register_class`` for each panel, operator, and property group.
    """
    # WHY: single delegation point -- keeps this __init__ thin so that adding
    # new UI modules only requires updating panels.py (or adding a new import).
    panels.register()


def unregister():
    """Unregister all Asciicker sidebar panels and operators from Blender.

    [DEPENDENCY:BLENDER] Delegates to ``panels.unregister()`` which calls
    ``bpy.utils.unregister_class`` in reverse order.
    """
    panels.unregister()
