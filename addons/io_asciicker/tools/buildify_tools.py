# Asciicker Buildify Tools
# Convert Buildify procedural objects into real meshes for AKM export.
# [DEPENDENCY:BLENDER] - Operators registered via bpy.utils.register_class

"""
Asciicker Buildify Tools -- Procedural-to-Real Mesh Conversion
================================================================

ARCHITECTURE:
    This module contains a single Blender operator
    (``ASCIICKER_OT_convert_buildify``) plus supporting helper functions.
    Its purpose is to convert *Buildify* procedural building objects (which
    use Blender Geometry Nodes) into real mesh objects that can be exported
    through the AKM pipeline.

    Buildify objects are identified heuristically by checking:
    1. Object name contains ``buildify``.
    2. Custom properties contain ``buildify``.
    3. Any modifier named ``buildify`` or referencing a ``buildify`` node group.

    Conversion supports two paths:
    - **Instance realization** (default): copies the object, appends a
      ``Realize Instances`` geometry-node modifier, then runs
      ``bpy.ops.object.convert(target='MESH')``.  This correctly flattens
      instanced geometry that ``new_from_object`` would miss.
    - **Evaluated mesh extraction**: uses ``bpy.data.meshes.new_from_object``
      on the depsgraph-evaluated object.  A temporary ``Realize Instances``
      modifier is prepended to handle instances.

KEY EXPORTS:
    - ``ASCIICKER_OT_convert_buildify``        -- Main conversion operator.
    - ``is_buildify_object(obj)``              -- Heuristic detection.
    - ``create_realize_instances_nodegroup()``  -- Temporary GN node group.
    - ``link_to_collections(obj, new, scene)``  -- Collection membership copy.
    - ``copy_parenting(obj, new)``             -- Parent/bone relationship copy.

PIPELINE CONTEXT:
    [DATA-CONTRACT:AKM]
    The AKM exporter only works with real mesh data (vertices, loops,
    polygons).  Procedural Buildify objects are purely Geometry-Node
    instances that have no concrete mesh until realized.  This operator
    bridges the gap.

TODO(PIPELINE-FIX): After conversion, vertex colors and UV layers from the
    original Buildify material may not be fully transferred if the node group
    generates new geometry.  Consider adding a post-conversion vertex-color
    validation step.
"""

import bpy
from bpy.types import Operator
from bpy.props import BoolProperty, StringProperty


def is_buildify_object(obj):
    """Detect whether *obj* is a Buildify procedural building asset.

    Uses a multi-signal heuristic: object name, custom properties, and
    modifier / geometry-node-group names are all checked for the substring
    ``buildify`` (case-insensitive).

    Args:
        obj: Blender object.

    Returns:
        bool: ``True`` if the object appears to be a Buildify asset.
    """
    name = obj.name.lower()
    if "buildify" in name:
        return True

    for key in obj.keys():
        if "buildify" in str(key).lower():
            return True

    for mod in obj.modifiers:
        if "buildify" in mod.name.lower():
            return True
        if mod.type == 'NODES' and mod.node_group:
            if "buildify" in mod.node_group.name.lower():
                return True

    return False


def link_to_collections(obj, new_obj, scene):
    """Link *new_obj* to the same collections as *obj*.

    If *obj* is not in any collection, link *new_obj* to the scene root
    collection instead.

    Args:
        obj:     Original object whose collection membership is copied.
        new_obj: Newly created object to be linked.
        scene:   Blender scene (fallback target).
    """
    collections = obj.users_collection or []
    if not collections:
        scene.collection.objects.link(new_obj)
        return

    for col in collections:
        col.objects.link(new_obj)


def create_realize_instances_nodegroup():
    """Create (or reuse) a temporary Geometry Nodes group that realizes instances.

    The node graph is trivial: ``Group Input -> Realize Instances -> Group Output``.

    WHY this exists: Blender's ``bpy.ops.object.convert(target='MESH')`` and
    ``bpy.data.meshes.new_from_object`` do not fully flatten instanced
    geometry produced by Geometry Nodes.  Appending this modifier before
    evaluation forces instance realization.

    Returns:
        bpy.types.GeometryNodeTree: The ``_TempRealizeInstances`` node group.
    """
    ng_name = "_TempRealizeInstances"

    # Reuse existing if available (avoids accumulating duplicates)
    existing = bpy.data.node_groups.get(ng_name)
    if existing:
        return existing

    # Create new node group
    ng = bpy.data.node_groups.new(ng_name, 'GeometryNodeTree')
    ng.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')

    # Add nodes
    input_node = ng.nodes.new('NodeGroupInput')
    output_node = ng.nodes.new('NodeGroupOutput')
    realize_node = ng.nodes.new('GeometryNodeRealizeInstances')

    input_node.location = (-200, 0)
    realize_node.location = (0, 0)
    output_node.location = (200, 0)

    # Link: Input -> Realize -> Output
    ng.links.new(input_node.outputs['Geometry'], realize_node.inputs['Geometry'])
    ng.links.new(realize_node.outputs['Geometry'], output_node.inputs['Geometry'])

    return ng


def copy_parenting(obj, new_obj):
    """Copy parent and bone relationships from *obj* to *new_obj*.

    Args:
        obj:     Source object.
        new_obj: Target object.
    """
    if not obj.parent:
        return

    new_obj.parent = obj.parent
    new_obj.parent_type = obj.parent_type
    new_obj.matrix_parent_inverse = obj.matrix_parent_inverse.copy()
    if obj.parent_type == 'BONE':
        new_obj.parent_bone = obj.parent_bone


class ASCIICKER_OT_convert_buildify(Operator):
    """Convert Buildify procedural objects to real mesh objects.

    Iterates over selected objects, filters for Buildify assets (via
    ``is_buildify_object``), and converts each to a concrete mesh.
    Supports instance realization, modifier application, optional joining
    of realized instances, and preservation or removal of originals.

    [DATA-CONTRACT:AKM] Produces real mesh data required by the AKM exporter.
    """
    bl_idname = "asciicker.convert_buildify"
    bl_label = "Convert Buildify to Mesh"
    bl_options = {'REGISTER', 'UNDO'}

    apply_modifiers: BoolProperty(
        name="Apply Modifiers",
        description="Apply modifiers/geometry nodes when converting",
        default=True,
    )
    keep_originals: BoolProperty(
        name="Keep Originals",
        description="Keep original Buildify objects after conversion",
        default=True,
    )
    only_buildify: BoolProperty(
        name="Only Buildify Objects",
        description="Only convert objects that look like Buildify assets",
        default=True,
    )
    realize_instances: BoolProperty(
        name="Realize Instances",
        description="Use Blender convert to realize instanced geometry",
        default=True,
    )
    join_realized: BoolProperty(
        name="Join Realized Instances",
        description="Join realized objects into a single mesh",
        default=True,
    )
    select_new: BoolProperty(
        name="Select New Meshes",
        description="Select converted mesh objects after creation",
        default=True,
    )
    mesh_suffix: StringProperty(
        name="Mesh Suffix",
        description="Suffix for converted mesh object names",
        default="_mesh",
    )

    @classmethod
    def poll(cls, context):
        """Return True if there are selected objects available for conversion."""
        return context.selected_objects is not None

    def _make_mesh_name(self, name):
        """Generate the name for the converted mesh object.

        If ``keep_originals`` is True, appends ``mesh_suffix`` to avoid name
        collisions with the original Buildify object.
        """
        if not self.keep_originals:
            return name
        if self.mesh_suffix and not name.endswith(self.mesh_suffix):
            return f"{name}{self.mesh_suffix}"
        return name

    def _create_mesh_data(self, obj, depsgraph, context):
        """Create a concrete ``Mesh`` datablock from *obj* via depsgraph evaluation.

        If the object has Geometry Node modifiers, a temporary *Realize
        Instances* modifier is injected so that ``new_from_object`` captures
        instanced geometry.  The temporary modifier is always removed in the
        ``finally`` block to avoid polluting the original object.

        Args:
            obj:       Original Blender object.
            depsgraph: Current dependency graph.
            context:   Blender context.

        Returns:
            bpy.types.Mesh: New mesh datablock with evaluated geometry.
        """
        if obj.type == 'MESH' and not self.apply_modifiers:
            return obj.data.copy()

        # WHY temporary Realize Instances modifier: Geometry Nodes can output
        # instanced geometry (e.g. scattered windows/balconies).
        # ``new_from_object`` on the evaluated object does NOT flatten
        # instances unless a Realize Instances node precedes the output.
        temp_mod = None
        has_geonodes = any(m.type == 'NODES' for m in obj.modifiers)

        if has_geonodes and self.apply_modifiers:
            realize_ng = create_realize_instances_nodegroup()
            temp_mod = obj.modifiers.new("_TempRealizeInstances", 'NODES')
            temp_mod.node_group = realize_ng
            # Refresh depsgraph after adding modifier
            context.view_layer.update()
            depsgraph = context.evaluated_depsgraph_get()

        try:
            eval_obj = obj.evaluated_get(depsgraph) if self.apply_modifiers else obj
            mesh = bpy.data.meshes.new_from_object(
                eval_obj,
                preserve_all_data_layers=True,
                depsgraph=depsgraph if self.apply_modifiers else None,
            )
        finally:
            # Always clean up the temporary modifier
            if temp_mod:
                obj.modifiers.remove(temp_mod)

        return mesh

    def _convert_with_ops(self, context, obj):
        """Convert *obj* using ``bpy.ops.object.convert`` with instance realization.

        This path duplicates the object, appends a Realize Instances modifier,
        runs the convert operator, and optionally joins the resulting objects
        into a single mesh.

        WHY this path exists: ``bpy.ops.object.convert`` handles more edge
        cases (curves, fonts, meta-balls) than ``new_from_object`` but does
        not realize GN instances on its own.

        Args:
            context: Blender context.
            obj:     Original Blender object.

        Returns:
            list: Newly created mesh objects.
        """
        new_obj = obj.copy()
        if obj.data:
            new_obj.data = obj.data.copy()

        link_to_collections(obj, new_obj, context.scene)
        copy_parenting(obj, new_obj)
        new_obj.matrix_world = obj.matrix_world.copy()

        # WHY prepend Realize Instances: bpy.ops.object.convert alone does
        # not flatten instanced geometry from Geometry Nodes.
        realize_ng = create_realize_instances_nodegroup()
        realize_mod = new_obj.modifiers.new("_TempRealizeInstances", 'NODES')
        realize_mod.node_group = realize_ng

        # WHY: bpy.ops.object.convert requires OBJECT mode; calling it from
        # EDIT or SCULPT mode raises a RuntimeError.
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        bpy.ops.object.select_all(action='DESELECT')
        new_obj.select_set(True)
        context.view_layer.objects.active = new_obj

        bpy.ops.object.convert(target='MESH')

        converted = list(context.selected_objects)

        if self.join_realized and len(converted) > 1:
            context.view_layer.objects.active = converted[0]
            bpy.ops.object.join()
            converted = [context.view_layer.objects.active]

        return converted

    def execute(self, context):
        """Convert selected Buildify objects to real meshes for AKM export.

        Iterates over ``context.selected_objects``, filters by object type and
        Buildify heuristic, then converts each target via the instance-
        realization path or the depsgraph-evaluation path depending on
        ``self.realize_instances``.

        Args:
            context: Blender operator execution context.

        Returns:
            set: ``{'FINISHED'}`` on success, ``{'CANCELLED'}`` if nothing
            was eligible for conversion.
        """
        selected = list(context.selected_objects or [])
        if not selected:
            self.report({'WARNING'}, "No objects selected")
            return {'CANCELLED'}

        targets = []
        for obj in selected:
            # WHY: only these five types can be meaningfully converted to MESH.
            # Other types (EMPTY, LIGHT, CAMERA, etc.) have no geometry.
            if obj.type not in {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META'}:
                continue
            if self.only_buildify and not is_buildify_object(obj):
                continue
            targets.append(obj)

        if not targets:
            self.report({'WARNING'}, "No Buildify objects found in selection")
            return {'CANCELLED'}

        depsgraph = context.evaluated_depsgraph_get()
        prev_selected = list(context.selected_objects or [])
        prev_active = context.view_layer.objects.active
        created = []
        failed = 0

        for obj in targets:
            try:
                if self.realize_instances:
                    new_objs = self._convert_with_ops(context, obj)
                    base_name = self._make_mesh_name(obj.name)
                    if len(new_objs) == 1:
                        new_objs[0].name = base_name
                    else:
                        for idx, inst_obj in enumerate(new_objs, start=1):
                            inst_obj.name = f"{base_name}_{idx}"
                    created.extend(new_objs)
                    if not self.keep_originals:
                        bpy.data.objects.remove(obj, do_unlink=True)
                    continue

                mesh = self._create_mesh_data(obj, depsgraph, context)
            except Exception:
                # TODO(PIPELINE-FIX): bare except silently swallows all errors.
                # Consider logging the traceback or the object name so artists
                # can diagnose which Buildify objects failed conversion.
                failed += 1
                continue

            new_obj = bpy.data.objects.new(self._make_mesh_name(obj.name), mesh)
            copy_parenting(obj, new_obj)
            new_obj.matrix_world = obj.matrix_world.copy()

            link_to_collections(obj, new_obj, context.scene)

            # TODO(PIPELINE-FIX): material copy only handles the simple case
            # where the original is a MESH with material slots and the new mesh
            # has none.  If Geometry Nodes generate new material assignments or
            # the original has mixed slot types, materials may be incorrect.
            if obj.type == 'MESH' and obj.data.materials and not new_obj.data.materials:
                for mat in obj.data.materials:
                    new_obj.data.materials.append(mat)

            created.append(new_obj)
            if not self.keep_originals:
                bpy.data.objects.remove(obj, do_unlink=True)

        # WHY: restore or update Blender's selection state so the user sees
        # either the freshly created meshes (select_new) or their original
        # selection.  The identity check (``bpy.data.objects[name] is obj``)
        # guards against stale references from objects removed by
        # ``keep_originals=False``.
        if self.select_new and created:
            bpy.ops.object.select_all(action='DESELECT')
            for obj in created:
                obj.select_set(True)
            context.view_layer.objects.active = created[-1]
        else:
            bpy.ops.object.select_all(action='DESELECT')
            for obj in prev_selected:
                if obj.name in bpy.data.objects and bpy.data.objects[obj.name] is obj:
                    obj.select_set(True)
            if prev_active and prev_active.name in bpy.data.objects and bpy.data.objects[prev_active.name] is prev_active:
                context.view_layer.objects.active = prev_active

        message = f"Converted {len(created)} object(s) to mesh"
        if failed:
            message += f", {failed} failed"
        self.report({'INFO'}, message)
        return {'FINISHED'}


# [DEPENDENCY:BLENDER] - classes tuple consumed by register()/unregister()
classes = (
    ASCIICKER_OT_convert_buildify,
)


def register():
    """Register buildify tool operators with Blender.

    [DEPENDENCY:BLENDER] Uses ``bpy.utils.register_class`` for each operator
    in the ``classes`` tuple.
    """
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    """Unregister buildify tool operators (reverse order).

    [DEPENDENCY:BLENDER] Uses ``bpy.utils.unregister_class`` in reverse order
    to ensure clean teardown.
    """
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
