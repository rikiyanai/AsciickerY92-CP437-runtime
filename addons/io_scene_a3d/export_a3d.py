# Asciicker A3D Map Export
# Exports Blender scene to A3D map format

import math
from .a3d_format import (
    A3DHeader, A3DPatch, A3DInstance, A3DEnemyGen,
    HEIGHT_CELLS, VISUAL_CELLS, HEIGHT_SCALE,
    write_a3d_file
)
from .default_materials import get_default_materials_binary


def matrix_to_list(matrix):
    """Convert Blender Matrix to flat list of 16 doubles (row-major)"""
    result = []
    for row in matrix:
        for val in row:
            result.append(float(val))
    return result


def get_terrain_object(context):
    """Find the terrain plane object"""
    for obj in context.scene.objects:
        if obj.type == 'MESH' and obj.name.lower() == 'terrain':
            return obj
    return None


def get_mesh_objects(context, use_selection=False):
    """Get all mesh objects except terrain"""
    objects = []

    if use_selection:
        source = context.selected_objects
    else:
        source = context.scene.objects

    for obj in source:
        if obj.type == 'MESH':
            name_lower = obj.name.lower()
            # Skip terrain and other non-instance objects
            if name_lower in ('terrain', 'camera', 'light', 'sun', 'lamp'):
                continue
            objects.append(obj)

    return objects


def sample_terrain_height(mesh, x, y, patch_size):
    """Sample height at a point on terrain mesh"""
    # Find closest vertex to the sample point
    min_dist = float('inf')
    height = 0.0

    for vert in mesh.vertices:
        dx = vert.co.x - x
        dy = vert.co.y - y
        dist = dx * dx + dy * dy

        if dist < min_dist:
            min_dist = dist
            height = vert.co.z

    return height


def sample_terrain_material(mesh, vertex_colors, x, y):
    """Sample material ID from vertex colors at a point"""
    if not vertex_colors:
        return 1  # Default to grass

    # Find closest vertex
    min_dist = float('inf')
    closest_idx = 0

    for i, vert in enumerate(mesh.vertices):
        dx = vert.co.x - x
        dy = vert.co.y - y
        dist = dx * dx + dy * dy

        if dist < min_dist:
            min_dist = dist
            closest_idx = i

    # Get vertex color from first polygon that uses this vertex
    for poly in mesh.polygons:
        for loop_idx in poly.loop_indices:
            if mesh.loops[loop_idx].vertex_index == closest_idx:
                color = vertex_colors[loop_idx].color
                # Red channel = material ID (0-255)
                return int(color[0] * 255) & 0xFF

    return 1  # Default to grass


def extract_terrain_patches(terrain_obj, context):
    """Extract terrain patches from Blender plane"""
    patches = []

    # Get evaluated mesh (with modifiers applied)
    depsgraph = context.evaluated_depsgraph_get()
    obj_eval = terrain_obj.evaluated_get(depsgraph)
    mesh = obj_eval.to_mesh()

    # Transform vertices to world space
    matrix = terrain_obj.matrix_world

    # Get bounding box
    min_x = min_y = float('inf')
    max_x = max_y = float('-inf')

    for vert in mesh.vertices:
        world_co = matrix @ vert.co
        min_x = min(min_x, world_co.x)
        min_y = min(min_y, world_co.y)
        max_x = max(max_x, world_co.x)
        max_y = max(max_y, world_co.y)

    # Get vertex colors if available
    vertex_colors = None
    if mesh.vertex_colors:
        vertex_colors = mesh.vertex_colors.active.data

    # Calculate patch grid
    # Each patch covers VISUAL_CELLS (8) units in each direction
    patch_size = VISUAL_CELLS  # 8 units per patch

    start_px = int(math.floor(min_x / patch_size))
    start_py = int(math.floor(min_y / patch_size))
    end_px = int(math.ceil(max_x / patch_size))
    end_py = int(math.ceil(max_y / patch_size))

    for py in range(start_py, end_py):
        for px in range(start_px, end_px):
            patch = A3DPatch(px, py)

            # Sample heights for 5x5 grid
            for hy in range(HEIGHT_CELLS + 1):  # 0-4
                for hx in range(HEIGHT_CELLS + 1):  # 0-4
                    # Map height grid to world coordinates
                    # Height samples are at corners of 2x2 visual cell groups
                    world_x = px * patch_size + hx * 2
                    world_y = py * patch_size + hy * 2

                    height = sample_terrain_height(mesh, world_x, world_y, patch_size)

                    # Convert to uint16 (scale by HEIGHT_SCALE)
                    height_val = int(height * HEIGHT_SCALE) & 0xFFFF
                    patch.height[hy][hx] = height_val

            # Sample materials for 8x8 visual grid
            for vy in range(VISUAL_CELLS):  # 0-7
                for vx in range(VISUAL_CELLS):  # 0-7
                    # Sample at center of each visual cell
                    world_x = px * patch_size + vx + 0.5
                    world_y = py * patch_size + vy + 0.5

                    mat_id = sample_terrain_material(mesh, vertex_colors, world_x, world_y)
                    patch.visual[vy][vx] = mat_id

            patches.append(patch)

    obj_eval.to_mesh_clear()

    return patches


def extract_instances(objects):
    """Extract mesh instances from Blender objects"""
    instances = []

    for obj in objects:
        # Get mesh name (object name should match .akm filename)
        mesh_name = obj.name

        # Remove any numeric suffix from duplicates (e.g., "tree-3.001" -> "tree-3")
        if '.' in mesh_name:
            parts = mesh_name.rsplit('.', 1)
            if parts[1].isdigit():
                mesh_name = parts[0]

        # Add .akm extension if not present
        if not mesh_name.endswith('.akm'):
            mesh_name = mesh_name + '.akm'

        # Get instance name (optional)
        inst_name = obj.name

        # Get world transform matrix
        transform = matrix_to_list(obj.matrix_world)

        # Get flags from custom properties if available
        flags = obj.get('a3d_flags', 0)
        story_id = obj.get('a3d_story_id', 0)

        inst = A3DInstance(
            mesh_name=mesh_name,
            inst_name=inst_name,
            transform=transform,
            flags=flags,
            story_id=story_id
        )

        instances.append(inst)

    return instances


def extract_enemy_generators(context):
    """Extract enemy generators from empty objects named 'EnemyGen*'"""
    generators = []

    for obj in context.scene.objects:
        if obj.type == 'EMPTY' and obj.name.lower().startswith('enemygen'):
            gen = A3DEnemyGen()

            # Get position from object location
            gen.pos = [obj.location.x, obj.location.y, obj.location.z]

            # Get properties from custom properties
            gen.alive_max = obj.get('alive_max', 1)
            gen.revive_min = obj.get('revive_min', 0)
            gen.revive_max = obj.get('revive_max', 0)
            gen.armor = obj.get('armor', 0)
            gen.helmet = obj.get('helmet', 0)
            gen.shield = obj.get('shield', 0)
            gen.sword = obj.get('sword', 0)
            gen.crossbow = obj.get('crossbow', 0)

            generators.append(gen)

    return generators


def save_a3d(context, filepath, use_selection=False, export_terrain=True):
    """Main export function"""
    import struct

    # Get terrain patches
    patches = []
    if export_terrain:
        terrain_obj = get_terrain_object(context)
        if terrain_obj:
            patches = extract_terrain_patches(terrain_obj, context)
            print(f"Exported {len(patches)} terrain patches")
        else:
            print("Warning: No 'Terrain' object found, exporting without terrain")

    # Get mesh instances
    objects = get_mesh_objects(context, use_selection)
    instances = extract_instances(objects)
    print(f"Exported {len(instances)} mesh instances")

    # Get enemy generators
    enemy_gens = extract_enemy_generators(context)
    print(f"Exported {len(enemy_gens)} enemy generators")

    # Get default materials
    materials_binary = get_default_materials_binary()

    # Write file
    with open(filepath, 'wb') as f:
        # 1. Write header
        header = A3DHeader(len(patches))
        header.write(f)

        # 2. Write terrain patches
        for patch in patches:
            patch.write(f)

        # 3. Write materials (raw binary from template)
        f.write(materials_binary)

        # 4. Write world header and instances
        f.write(struct.pack('<i', -1))  # format_version = -1
        f.write(struct.pack('<i', len(instances)))

        for inst in instances:
            inst.write(f)

        # 5. Write enemy generators
        f.write(struct.pack('<i', len(enemy_gens)))
        for gen in enemy_gens:
            gen.write(f)

    print(f"Saved A3D map to: {filepath}")
    return {'FINISHED'}
