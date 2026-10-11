# Asciicker Scene Import/Export (A3D Maps)
#
# [DEPENDENCY:BLENDER] Blender addon submodule for scene-level A3D map export.
# [DATA-CONTRACT:A3D] Produces binary .a3d files consumable by the C++ engine
#                      (world.cpp, terrain.cpp, asciiid.cpp).

"""
Scene submodule -- A3D map export operator and Blender UI integration.

ARCHITECTURE
============
This ``__init__.py`` registers the export operator with Blender so that
*File > Export > Asciicker Map (.a3d)* appears in the top bar.  It delegates
all serialization work to :mod:`export_a3d`, which in turn relies on
:mod:`a3d_format` for binary struct definitions and :mod:`default_materials`
for material palette lookup.

KEY EXPORTS
-----------
- ``ASCIICKER_OT_export_a3d`` -- Blender ``Operator + ExportHelper`` that
  writes the current scene (terrain + mesh instances + enemy generators)
  to a single ``.a3d`` file.
- ``register()`` / ``unregister()`` -- Standard Blender addon lifecycle
  hooks called by the parent ``io_asciicker`` package.

PIPELINE CONTEXT
----------------
The A3D export pipeline is an alternative to the in-engine asciiid editor.
Artists author terrain as a subdivided Blender plane named *Terrain*, paint
material IDs into vertex-color channels (Red = material index 0-255), and
place mesh instances whose names resolve to ``.akm`` files at runtime.

    Blender Scene  --(this exporter)-->  .a3d file  --(engine loader)-->  world.cpp

See also:
    - ``asciiid.cpp`` line 129: ``[DATA-CONTRACT:A3D]`` engine-side loader.
    - ``terrain.h``: authoritative ``HEIGHT_SCALE``, ``HEIGHT_CELLS``,
      ``VISUAL_CELLS`` constants mirrored in ``a3d_format.py``.
"""

import bpy
from bpy.props import StringProperty, BoolProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import export_a3d
from . import import_a3d


class ASCIICKER_OT_export_a3d(bpy.types.Operator, ExportHelper):
    """Export scene to Asciicker A3D map format (bypasses asciiid editor)"""
    bl_idname = "export_scene.a3d"
    bl_label = "Export A3D Map"
    bl_options = {'PRESET'}

    filename_ext = ".a3d"
    filter_glob: StringProperty(default="*.a3d", options={'HIDDEN'})

    use_selection: BoolProperty(
        name="Selection Only",
        description="Export only selected objects (terrain is always exported if present)",
        default=False,
    )

    # TODO(PIPELINE-FIX): The terrain object is located by the hard-coded name
    # "Terrain".  If the artist renames it, the export silently omits terrain
    # data.  Consider using a custom property or an object-pointer property to
    # make the association explicit.
    export_terrain: BoolProperty(
        name="Export Terrain",
        description="Export terrain from object named 'Terrain'",
        default=True,
    )

    def execute(self, context):
        """Invoke the A3D serialization pipeline.

        [DATA-CONTRACT:A3D] This is the Blender-side boundary where the scene
        graph is serialized into the binary A3D format consumed by the C++
        engine (``world.cpp``).

        Delegates entirely to :func:`export_a3d.save_a3d`, which handles
        terrain extraction, instance gathering, validation, and binary write.
        """
        return export_a3d.save_a3d(
            context,
            self.filepath,
            use_selection=self.use_selection,
            export_terrain=self.export_terrain,
        )

    def draw(self, context):
        """Draw the export option panel in Blender's file-browser sidebar.

        Shows scene-setup hints so artists know the expected conventions:
        vertex-color encoding, object naming, and terrain requirements.
        """
        layout = self.layout

        box = layout.box()
        box.label(text="Export Options:")
        box.prop(self, "use_selection")
        box.prop(self, "export_terrain")

        # WHY: These hints document the implicit data contract between the
        # Blender scene layout and the engine's A3D loader.  Without them,
        # artists may not know that the Red channel encodes material IDs
        # or that object names become .akm mesh references.
        box = layout.box()
        box.label(text="Scene Setup:")
        box.label(text="  - Name terrain plane 'Terrain'")
        box.label(text="  - Paint materials as vertex colors")
        box.label(text="  - Red channel = Material ID")
        box.label(text="  - Object names become .akm refs")


class ASCIICKER_OT_import_a3d(bpy.types.Operator, ImportHelper):
    """Import Asciicker A3D map (terrain, instances, enemy generators)"""
    bl_idname = "import_scene.a3d"
    bl_label = "Import A3D Map"
    bl_options = {'UNDO', 'PRESET'}

    filename_ext = ".a3d"
    filter_glob: StringProperty(default="*.a3d", options={'HIDDEN'})

    import_terrain: BoolProperty(
        name="Import Terrain",
        description="Reconstruct terrain mesh from height/visual data",
        default=True,
    )
    import_instances: BoolProperty(
        name="Import Instances",
        description="Import mesh/sprite/item instances",
        default=True,
    )
    import_enemy_gens: BoolProperty(
        name="Import Enemy Generators",
        description="Import enemy spawn points as Empties",
        default=True,
    )
    mesh_search_paths: StringProperty(
        name="Mesh Search Paths",
        description="Semicolon-separated paths to search for .akm files",
        default="./meshes",
    )

    def execute(self, context):
        return import_a3d.load(
            self, context, self.filepath,
            import_terrain=self.import_terrain,
            import_instances=self.import_instances,
            import_enemy_gens=self.import_enemy_gens,
            mesh_search_paths=self.mesh_search_paths,
        )

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Import Options:")
        box.prop(self, "import_terrain")
        box.prop(self, "import_instances")
        box.prop(self, "import_enemy_gens")
        box.prop(self, "mesh_search_paths")


def menu_export(self, context):
    """Append the A3D export entry to Blender's File > Export menu."""
    self.layout.operator(ASCIICKER_OT_export_a3d.bl_idname, text="Asciicker Map (.a3d)")


def menu_import(self, context):
    """Append the A3D import entry to Blender's File > Import menu."""
    self.layout.operator(ASCIICKER_OT_import_a3d.bl_idname, text="Asciicker Map (.a3d)")


# --- Blender addon registration machinery ---

classes = (
    ASCIICKER_OT_export_a3d,
    ASCIICKER_OT_import_a3d,
)


def register():
    """Register all operator classes and add menu entries."""
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.TOPBAR_MT_file_export.append(menu_export)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)


def unregister():
    """Remove menu entries and unregister all operator classes."""
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
