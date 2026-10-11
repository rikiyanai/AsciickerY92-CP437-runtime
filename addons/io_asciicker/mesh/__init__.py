# Asciicker Mesh Import/Export
# [DEPENDENCY:BLENDER] [DATA-CONTRACT:AKM]

"""
AKM Mesh Module -- Blender Operator Registration
==================================================

ARCHITECTURE:
    This ``__init__.py`` wires the AKM import/export back-ends (``import_akm``
    and ``export_akm``) into Blender's operator system so they appear under
    File > Import / Export.  It defines two operators:

    * ``ASCIICKER_OT_import_akm``  -- File > Import > Asciicker Mesh (.akm)
    * ``ASCIICKER_OT_export_akm``  -- File > Export > Asciicker Mesh (.akm)

    The export operator uses ``orientation_helper`` to let the user choose
    axis conventions and applies an ``axis_conversion`` matrix combined with
    ``global_scale`` before handing data to the back-end.

KEY EXPORTS:
    - ``register()``   -- Registers operator classes and appends menu entries.
    - ``unregister()`` -- Removes menu entries and unregisters classes (LIFO).

PIPELINE CONTEXT:
    [DATA-CONTRACT:AKM] AKM files are ASCII PLY files with Asciicker-specific
    conventions:
      - Vertex colors use the engine's 216-color terminal-safe palette.
      - The alpha channel encodes collision weight (0 = solid, 255 = passthrough).
      - Freestyle-marked faces get a negative vertex count in the face list,
        signaling the C++ renderer to treat them as wireframe / debug geometry.

    The default axis orientation (Y-forward, Z-up) matches the Asciicker
    engine's world coordinate system.
"""

import bpy
from bpy.props import (
    StringProperty,
    BoolProperty,
    FloatProperty,
    CollectionProperty,
)
from bpy_extras.io_utils import (
    ImportHelper,
    ExportHelper,
    orientation_helper,
    axis_conversion,
)

from . import import_akm
from . import export_akm


# ---------------------------------------------------------------------------
# Import operator
# ---------------------------------------------------------------------------

class ASCIICKER_OT_import_akm(bpy.types.Operator, ImportHelper):
    """Load an AKM mesh file.

    Supports multi-file selection through the ``files`` CollectionProperty.
    Each selected file is loaded sequentially via ``import_akm.load()``.
    """
    bl_idname = "import_mesh.akm"
    bl_label = "Import AKM"
    bl_options = {'UNDO', 'PRESET'}

    files: CollectionProperty(
        name="File Path",
        type=bpy.types.OperatorFileListElement,
    )

    directory: StringProperty()

    filename_ext = ".akm"
    filter_glob: StringProperty(default="*.akm", options={'HIDDEN'})

    def execute(self, context):
        """Import one or more AKM files from the selected file list."""
        import os

        paths = [os.path.join(self.directory, f.name) for f in self.files]
        if not paths:
            paths.append(self.filepath)

        # TODO(PIPELINE-FIX): no existence check on paths before calling load().
        # If the user somehow passes a stale file reference, import_akm.load()
        # will raise an unhandled FileNotFoundError.  Consider wrapping in
        # try/except and using operator.report({'ERROR'}, ...).
        for path in paths:
            import_akm.load(self, context, path)

        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Export operator
# ---------------------------------------------------------------------------

# WHY axis_forward='Y', axis_up='Z': the Asciicker C++ engine uses a
# Y-forward / Z-up coordinate system.  orientation_helper injects
# axis_forward / axis_up properties so the user can override if needed.
# [DEPENDENCY:BLENDER] orientation_helper is a Blender-specific decorator
# that injects axis_forward/axis_up enum properties into the operator class.
@orientation_helper(axis_forward='Y', axis_up='Z')
class ASCIICKER_OT_export_akm(bpy.types.Operator, ExportHelper):
    """Export the scene (or selection) as an AKM mesh file.

    The operator builds a combined axis-conversion + scale matrix and passes
    it to ``export_akm.save()`` as ``global_matrix``.  Individual export
    toggles (normals, UVs, vertex colors, modifiers) are forwarded as keyword
    arguments.
    """
    bl_idname = "export_mesh.akm"
    bl_label = "Export AKM"
    bl_options = {'PRESET'}

    filename_ext = ".akm"
    filter_glob: StringProperty(default="*.akm", options={'HIDDEN'})

    use_selection: BoolProperty(
        name="Selection Only",
        description="Export selected objects only",
        default=False,
    )

    use_mesh_modifiers: BoolProperty(
        name="Apply Modifiers",
        description="Apply modifiers to exported mesh",
        default=True,
    )

    use_normals: BoolProperty(
        name="Normals",
        description="Export normals",
        default=True,
    )

    use_uv_coords: BoolProperty(
        name="UVs",
        description="Export UV coordinates",
        default=True,
    )

    use_colors: BoolProperty(
        name="Vertex Colors",
        description="Export vertex colors",
        default=True,
    )

    apply_world_transform: BoolProperty(
        name="Apply World Transform",
        description="Bake object transforms into mesh vertices",
        default=False,
    )

    global_scale: FloatProperty(
        name="Scale",
        min=0.01,
        max=1000.0,
        default=1.0,
    )

    def execute(self, context):
        """Build the axis-conversion matrix and delegate to export_akm.save()."""
        from mathutils import Matrix

        # WHY ignore these keys: they are consumed here to build
        # global_matrix and are not needed by export_akm.save().
        keywords = self.as_keywords(
            ignore=(
                "axis_forward",
                "axis_up",
                "global_scale",
                "check_existing",
                "filter_glob",
            )
        )

        # Build the combined axis-conversion + uniform-scale matrix.
        # This single matrix is applied to the merged mesh before writing.
        global_matrix = axis_conversion(
            to_forward=self.axis_forward,
            to_up=self.axis_up,
        ).to_4x4() @ Matrix.Scale(self.global_scale, 4)

        keywords["global_matrix"] = global_matrix
        filepath = bpy.path.ensure_ext(self.filepath, self.filename_ext)

        return export_akm.save(self, context, **keywords)


# ---------------------------------------------------------------------------
# Menu draw callbacks
# ---------------------------------------------------------------------------

def menu_import(self, context):
    """Append the AKM importer entry to File > Import."""
    self.layout.operator(ASCIICKER_OT_import_akm.bl_idname, text="Asciicker Mesh (.akm)")


def menu_export(self, context):
    """Append the AKM exporter entry to File > Export."""
    self.layout.operator(ASCIICKER_OT_export_akm.bl_idname, text="Asciicker Mesh (.akm)")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

# WHY tuple ordering matters: unregister() reverses this list for LIFO
# cleanup.  If a class depends on another (e.g. a panel referencing an
# operator), the dependent class must appear later in this tuple.
# [DEPENDENCY:BLENDER] Blender's register/unregister lifecycle.
classes = (
    ASCIICKER_OT_import_akm,
    ASCIICKER_OT_export_akm,
)


def register():
    """Register operator classes and inject menu entries into Blender's top bar."""
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.TOPBAR_MT_file_import.append(menu_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)


def unregister():
    """Remove menu entries and unregister classes (reverse order for LIFO safety)."""
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
