"""
Import a 3D mesh file into Blender and save as .blend.

Usage:
    blender -b --factory-startup -P scripts/blender/import_mesh.py -- \
        --input /path/to/model.obj \
        --output /path/to/output.blend

Supports: .obj, .stl, .fbx, .gltf, .glb, .ply

Outputs JSON to stdout (after MESH_IMPORT_JSON marker) with scene info:
    {"object_name": "...", "vertex_count": N, "bbox": {...}, "blend_path": "..."}
"""
import bpy
import sys
import os
import json
import argparse


def parse_args():
    """Parse arguments after the '--' separator in Blender's argv."""
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []

    parser = argparse.ArgumentParser(description="Import mesh into Blender")
    parser.add_argument("--input", required=True, help="Path to mesh file")
    parser.add_argument("--output", required=True, help="Output .blend path")
    return parser.parse_args(argv)


def clear_scene():
    """Remove all default objects from the scene."""
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)

    for mesh in bpy.data.meshes:
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    for mat in bpy.data.materials:
        if mat.users == 0:
            bpy.data.materials.remove(mat)
    for light in bpy.data.lights:
        if light.users == 0:
            bpy.data.lights.remove(light)
    for cam in bpy.data.cameras:
        if cam.users == 0:
            bpy.data.cameras.remove(cam)


IMPORT_OPERATORS = {
    '.obj': lambda p: bpy.ops.wm.obj_import(filepath=p),
    '.stl': lambda p: bpy.ops.wm.stl_import(filepath=p),
    '.fbx': lambda p: bpy.ops.import_scene.fbx(filepath=p),
    '.gltf': lambda p: bpy.ops.import_scene.gltf(filepath=p),
    '.glb': lambda p: bpy.ops.import_scene.gltf(filepath=p),
    '.ply': lambda p: bpy.ops.wm.ply_import(filepath=p),
}


def import_mesh(mesh_path):
    """Import mesh file using the appropriate Blender operator."""
    ext = os.path.splitext(mesh_path)[1].lower()
    op = IMPORT_OPERATORS.get(ext)
    if op is None:
        raise ValueError(f"Unsupported mesh format: {ext}")

    print(f"Importing {ext} file: {mesh_path}")
    op(mesh_path)

    imported = [o for o in bpy.context.selected_objects if o.type == 'MESH']
    if not imported:
        imported = [o for o in bpy.data.objects if o.type == 'MESH']

    print(f"Imported {len(imported)} mesh objects")
    return imported


def get_combined_bbox(objects):
    """Calculate combined world-space bounding box for all objects."""
    import mathutils
    min_v = [float('inf')] * 3
    max_v = [float('-inf')] * 3
    for obj in objects:
        for corner in obj.bound_box:
            world_corner = obj.matrix_world @ mathutils.Vector(corner)
            for i in range(3):
                min_v[i] = min(min_v[i], world_corner[i])
                max_v[i] = max(max_v[i], world_corner[i])
    return min_v, max_v


def setup_material(objects):
    """Assign a visible material to objects that have none or blank materials."""
    mat = bpy.data.materials.new(name="MeshImportMat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    nodes.clear()

    bsdf = nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.location = (0, 0)
    bsdf.inputs['Base Color'].default_value = (0.6, 0.5, 0.4, 1.0)
    bsdf.inputs['Roughness'].default_value = 0.7
    bsdf.inputs['Metallic'].default_value = 0.0

    output = nodes.new('ShaderNodeOutputMaterial')
    output.location = (300, 0)
    links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])

    for obj in objects:
        if obj.type != 'MESH':
            continue
        has_good_mat = False
        for slot in obj.material_slots:
            if slot.material and slot.material.use_nodes:
                for node in slot.material.node_tree.nodes:
                    if node.type == 'BSDF_PRINCIPLED':
                        color = node.inputs['Base Color'].default_value
                        if sum(color[:3]) > 0.1:
                            has_good_mat = True
                            break
            if has_good_mat:
                break

        if not has_good_mat:
            obj.data.materials.clear()
            obj.data.materials.append(mat)

    return mat


def get_scene_info(objects, blend_path):
    """Collect scene info for reporting."""
    total_verts = sum(len(o.data.vertices) for o in objects if o.type == 'MESH')
    bbox_min, bbox_max = get_combined_bbox(objects)
    size = [bbox_max[i] - bbox_min[i] for i in range(3)]

    # Pick the largest mesh object as the primary object name
    primary = max(objects, key=lambda o: len(o.data.vertices) if o.type == 'MESH' else 0)

    return {
        "object_name": primary.name,
        "object_count": len(objects),
        "vertex_count": total_verts,
        "bbox_min": [round(v, 3) for v in bbox_min],
        "bbox_max": [round(v, 3) for v in bbox_max],
        "size": [round(v, 3) for v in size],
        "blend_path": blend_path,
    }


def main():
    args = parse_args()

    if not os.path.exists(args.input):
        print(f"ERROR: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    clear_scene()
    meshes = import_mesh(args.input)

    if not meshes:
        print("ERROR: No meshes imported!", file=sys.stderr)
        sys.exit(1)

    setup_material(meshes)

    # Save as .blend
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.output))
    print(f"Saved: {args.output}")

    # Output scene info as JSON (after marker so caller can parse it)
    info = get_scene_info(meshes, os.path.abspath(args.output))
    print(f"MESH_IMPORT_JSON:{json.dumps(info)}")


if __name__ == "__main__":
    main()
