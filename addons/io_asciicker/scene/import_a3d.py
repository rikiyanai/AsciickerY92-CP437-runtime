# A3D Map Import
# Reconstructs Blender scene from binary .a3d world files
# [DEPENDENCY:BLENDER] [DATA-CONTRACT:A3D]

"""
A3D Scene Importer -- Binary .a3d to Blender Scene
====================================================

Reads a binary ``.a3d`` file and reconstructs the scene as Blender objects:
  - Terrain mesh from height/visual patch data
  - Mesh instances (linked to .akm files if found, else Empty placeholders)
  - Sprite/item instances as Empties with custom properties
  - Enemy generator spawn points as Empties

The parser mirrors the engine's load sequence (world.cpp, terrain.cpp,
enemygen.cpp) and reverses the coordinate transforms applied by export_a3d.py.
"""

import os
import struct
import time

import bpy
from mathutils import Matrix

from .a3d_format import (
    A3DPatch,
    HEIGHT_SCALE, HEIGHT_CELLS, VISUAL_CELLS,
)
from .a3d_import_core import (
    PATCH_OFFSET_X,
    PATCH_OFFSET_Y,
    infer_mesh_instance_z_baseline,
    infer_terrain_patch_z_baseline,
    load_a3d_file,
    reverse_world_position,
    resolve_mesh_path,
    reverse_instance_transform,
)


def create_terrain_mesh(patches, *, z_baseline=None):
    """Reconstruct a unified terrain mesh from A3DPatch array.

    Per patch: 5x5 height vertices -> 4x4 quads -> triangulated via diag bitmask.
    Boundary vertices are deduplicated across patches.
    Vertex colors encode material IDs from the 8x8 visual grid.
    """
    if z_baseline is None:
        z_baseline = infer_terrain_patch_z_baseline(patches)

    vertex_map = {}  # (world_x, world_y) -> vertex index
    vertices = []
    faces = []
    face_mat_ids = []  # material ID per face (for vertex coloring)

    for patch in patches:
        patch_x = patch.x - PATCH_OFFSET_X
        patch_y = patch.y - PATCH_OFFSET_Y

        # Build local vertex index map for this patch
        local_vi = {}
        for hy in range(HEIGHT_CELLS + 1):
            for hx in range(HEIGHT_CELLS + 1):
                wx = patch_x * 8 + hx * 2
                wy = patch_y * 8 + hy * 2
                z = (patch.height[hy][hx] - z_baseline) / HEIGHT_SCALE

                key = (wx, wy)
                if key not in vertex_map:
                    vertex_map[key] = len(vertices)
                    vertices.append((float(wx), float(wy), z))
                local_vi[(hx, hy)] = vertex_map[key]

        # Create triangulated faces from height grid quads
        for qy in range(HEIGHT_CELLS):
            for qx in range(HEIGHT_CELLS):
                v00 = local_vi[(qx, qy)]
                v10 = local_vi[(qx + 1, qy)]
                v01 = local_vi[(qx, qy + 1)]
                v11 = local_vi[(qx + 1, qy + 1)]

                # Material ID from visual grid (2x resolution of height grid)
                mat_id = patch.visual[qy * 2][qx * 2] & 0xFF

                diag_bit = (patch.diag >> (qy * HEIGHT_CELLS + qx)) & 1
                if diag_bit:
                    faces.append((v00, v10, v11))
                    faces.append((v00, v11, v01))
                else:
                    faces.append((v00, v10, v01))
                    faces.append((v10, v11, v01))
                face_mat_ids.append(mat_id)
                face_mat_ids.append(mat_id)

    # Build Blender mesh
    mesh = bpy.data.meshes.new("Terrain")
    mesh.vertices.add(len(vertices))
    mesh.vertices.foreach_set("co", [c for v in vertices for c in v])

    # Flatten faces into loops
    loops_vert_idx = []
    faces_loop_start = []
    faces_loop_total = []
    lidx = 0
    for face in faces:
        loops_vert_idx.extend(face)
        faces_loop_start.append(lidx)
        faces_loop_total.append(len(face))
        lidx += len(face)

    mesh.loops.add(len(loops_vert_idx))
    mesh.polygons.add(len(faces))
    mesh.loops.foreach_set("vertex_index", loops_vert_idx)
    mesh.polygons.foreach_set("loop_start", faces_loop_start)
    mesh.polygons.foreach_set("loop_total", faces_loop_total)

    # Vertex colors: Red channel = material_id / 255
    if hasattr(mesh, 'color_attributes'):
        vcol = mesh.color_attributes.new(
            name="MaterialID", type='FLOAT_COLOR', domain='CORNER'
        )
        for i, mat_id in enumerate(face_mat_ids):
            r = mat_id / 255.0
            # Each face has 3 loops (triangles)
            for j in range(3):
                vcol.data[i * 3 + j].color = (r, 0.0, 0.0, 1.0)

    mesh.update()
    mesh.validate()
    return mesh


def _col_major_to_matrix(vals):
    """Convert 16 column-major doubles to a Blender Matrix (row-major 4x4)."""
    return Matrix((
        (vals[0], vals[4], vals[8],  vals[12]),
        (vals[1], vals[5], vals[9],  vals[13]),
        (vals[2], vals[6], vals[10], vals[14]),
        (vals[3], vals[7], vals[11], vals[15]),
    ))


def create_instance_objects(instances, context, mesh_search_paths, a3d_dir):
    """Create Blender objects for each A3DInstance."""
    from ..mesh import import_akm

    created = []
    for inst in instances:
        if inst.variant == 'mesh':
            # Try to load the actual mesh
            obj = None
            mesh_path = resolve_mesh_path(inst.mesh_name, mesh_search_paths, a3d_dir)
            if mesh_path:
                mesh = import_akm.load_mesh(mesh_path, inst.inst_name or inst.mesh_name)
                if mesh:
                    obj = bpy.data.objects.new(inst.inst_name or inst.mesh_name, mesh)

            if obj is None:
                # Placeholder Empty
                obj = bpy.data.objects.new(inst.inst_name or inst.mesh_name, None)
                obj.empty_display_type = 'CUBE'
                obj.empty_display_size = 0.5
                obj['a3d_missing_mesh'] = inst.mesh_name

            # Apply reversed transform
            instance_z_baseline = infer_mesh_instance_z_baseline(inst.transform[14])
            t = reverse_instance_transform(
                inst.transform,
                z_baseline=instance_z_baseline,
            )
            obj.matrix_world = _col_major_to_matrix(t)

            obj['a3d_instance_z_baseline'] = float(instance_z_baseline)
            obj['a3d_flags'] = inst.flags
            obj['a3d_story_id'] = inst.story_id

        elif inst.variant == 'sprite':
            obj = bpy.data.objects.new(inst.inst_name or "Sprite", None)
            obj.empty_display_type = 'IMAGE'
            obj.empty_display_size = 0.5
            obj.location = inst.pos
            obj.rotation_euler = (0, 0, inst.yaw)
            obj['a3d_variant'] = 'sprite'
            obj['a3d_anim'] = inst.anim
            obj['a3d_frame'] = inst.frame
            obj['a3d_reps'] = inst.reps
            obj['a3d_flags'] = inst.flags
            obj['a3d_story_id'] = inst.story_id

        elif inst.variant == 'item':
            obj = bpy.data.objects.new(f"ItemDef_{inst.item_definition_id}", None)
            obj.empty_display_type = 'PLAIN_AXES'
            obj.empty_display_size = 0.3
            obj.location = inst.pos
            obj.rotation_euler = (0, 0, inst.yaw)
            obj['a3d_variant'] = 'item'
            obj['a3d_item_definition_id'] = inst.item_definition_id
            obj['a3d_visual_style_id'] = inst.visual_style_id
            obj['a3d_presentation_kind_id'] = inst.presentation_kind_id
            obj['a3d_item_count'] = inst.item_count
            obj['a3d_flags'] = inst.flags
            obj['a3d_story_id'] = inst.story_id

        obj['a3d_instance'] = True  # tag for OSM terrain painter calibration
        context.collection.objects.link(obj)
        created.append(obj)

    return created


def create_enemy_gen_empties(enemy_gens, context):
    """Create Empty objects for each enemy generator spawn point."""
    created = []
    for i, gen in enumerate(enemy_gens):
        obj = bpy.data.objects.new(f"EnemyGen_{i:03d}", None)
        obj.empty_display_type = 'SPHERE'
        obj.empty_display_size = 1.0
        obj.location = reverse_world_position(gen.pos)

        obj['alive_max'] = gen.alive_max
        obj['revive_min'] = gen.revive_min
        obj['revive_max'] = gen.revive_max
        obj['armor'] = gen.armor
        obj['helmet'] = gen.helmet
        obj['shield'] = gen.shield
        obj['sword'] = gen.sword
        obj['crossbow'] = gen.crossbow

        context.collection.objects.link(obj)
        created.append(obj)

    return created


def load(operator, context, filepath, **kwargs):
    """Top-level A3D import entry point called by the Blender operator.

    Args:
        operator: The calling bpy.types.Operator (for report()).
        context:  Current Blender context.
        filepath: Absolute path to the .a3d file.
        **kwargs: import_terrain, import_instances, import_enemy_gens,
                  mesh_search_paths.
    Returns:
        {'FINISHED'} on success, {'CANCELLED'} on failure.
    """
    t = time.time()
    a3d_dir = bpy.path.abspath("//") if filepath.startswith("//") else os.path.dirname(filepath)

    try:
        header, patches, materials, instances, enemy_gens, minimap_markers = load_a3d_file(filepath)
    except (ValueError, struct.error) as e:
        operator.report({'ERROR'}, f"Failed to parse {filepath}: {e}")
        return {'CANCELLED'}

    import_terrain = kwargs.get('import_terrain', True)
    import_instances = kwargs.get('import_instances', True)
    import_enemy_gens = kwargs.get('import_enemy_gens', True)
    mesh_search_paths = kwargs.get('mesh_search_paths', './meshes')

    if import_terrain and patches:
        terrain_mesh = create_terrain_mesh(patches)
        terrain_obj = bpy.data.objects.new("Terrain", terrain_mesh)
        context.collection.objects.link(terrain_obj)

    if import_instances and instances:
        create_instance_objects(instances, context, mesh_search_paths, a3d_dir)

    if import_enemy_gens and enemy_gens:
        create_enemy_gen_empties(enemy_gens, context)

    elapsed = time.time() - t
    operator.report({'INFO'},
                    f"Imported {filepath}: {len(patches)} patches, "
                    f"{len(instances)} instances, {len(enemy_gens)} enemy gens, "
                    f"{len(minimap_markers)} minimap markers "
                    f"in {elapsed:.2f}s")
    return {'FINISHED'}
