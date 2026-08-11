"""Pure-Python helpers for A3D import parsing and transform reversal.

This module deliberately avoids Blender imports so parser and transform
regression tests can run in a normal Python environment.
"""

import os
import struct

try:
    from .a3d_format import (
        A3DHeader, A3DPatch, A3DMaterial, A3DInstance, A3DPlayerStart, A3DEnemyGen, A3DMinimapMarker,
        HEIGHT_SCALE, VISUAL_CELLS, WATER_LEVEL, BASE_TERRAIN_HEIGHT,
        TERRAIN_EXPORT_BASELINE,
    )
except ImportError:
    import importlib.util
    from pathlib import Path

    _fmt_path = Path(__file__).with_name("a3d_format.py")
    _spec = importlib.util.spec_from_file_location("a3d_format", _fmt_path)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    A3DHeader = _mod.A3DHeader
    A3DPatch = _mod.A3DPatch
    A3DMaterial = _mod.A3DMaterial
    A3DInstance = _mod.A3DInstance
    A3DPlayerStart = _mod.A3DPlayerStart
    A3DEnemyGen = _mod.A3DEnemyGen
    A3DMinimapMarker = _mod.A3DMinimapMarker
    HEIGHT_SCALE = _mod.HEIGHT_SCALE
    VISUAL_CELLS = _mod.VISUAL_CELLS
    WATER_LEVEL = _mod.WATER_LEVEL
    BASE_TERRAIN_HEIGHT = _mod.BASE_TERRAIN_HEIGHT
    TERRAIN_EXPORT_BASELINE = _mod.TERRAIN_EXPORT_BASELINE


PATCH_OFFSET_X = -4
PATCH_OFFSET_Y = -4
PATCH_SIZE = VISUAL_CELLS


def infer_terrain_patch_z_baseline(patches):
    """Infer the terrain height baseline stored in ``patch.height`` values."""
    if not patches:
        return TERRAIN_EXPORT_BASELINE

    min_height = min(value for patch in patches for row in patch.height for value in row)
    return BASE_TERRAIN_HEIGHT if min_height >= WATER_LEVEL else TERRAIN_EXPORT_BASELINE


def infer_mesh_instance_z_baseline(raw_z):
    """Infer the stored mesh-instance Z baseline from a raw engine-space value."""
    return BASE_TERRAIN_HEIGHT if raw_z >= WATER_LEVEL else TERRAIN_EXPORT_BASELINE


def reverse_world_position(pos, legacy=True, z_baseline=None):
    """Convert engine/world-space XYZ back to Blender-space coordinates."""
    if z_baseline is None:
        z_offset = BASE_TERRAIN_HEIGHT if legacy else 0
    else:
        z_offset = z_baseline
    return [
        float(pos[0] - PATCH_OFFSET_X * PATCH_SIZE),
        float(pos[1] - PATCH_OFFSET_Y * PATCH_SIZE),
        float((pos[2] - z_offset) / HEIGHT_SCALE),
    ]


def load_a3d_file(filepath):
    """Parse a binary .a3d file into header, patches, materials, instances, generators, and minimap markers."""
    with open(filepath, "rb") as f:
        header = A3DHeader.from_file(f)
        patches = [A3DPatch.from_file(f) for _ in range(header.num_patches)]
        materials = [A3DMaterial.read(f) for _ in range(256)]

        first_int = struct.unpack("<i", f.read(4))[0]
        if first_int < 0:
            _format_version = -first_int
            instance_count = struct.unpack("<i", f.read(4))[0]
        else:
            _format_version = 0
            instance_count = first_int

        instances = [A3DInstance.from_file(f, _format_version) for _ in range(instance_count)]

        if _format_version >= 4:
            has_player_start = struct.unpack("<i", f.read(4))[0]
            if has_player_start:
                A3DPlayerStart.from_file(f)

        enemygen_count = struct.unpack("<i", f.read(4))[0]
        enemy_gens = [A3DEnemyGen.from_file(f) for _ in range(enemygen_count)]

        marker_count_raw = f.read(4)
        if len(marker_count_raw) < 4:
            minimap_markers = []
        else:
            marker_count = struct.unpack("<i", marker_count_raw)[0]
            minimap_markers = [A3DMinimapMarker.from_file(f) for _ in range(marker_count)]

    return header, patches, materials, instances, enemy_gens, minimap_markers


def read_player_start(filepath):
    """Read the embedded player-start record from a .a3d file, if present."""
    with open(filepath, "rb") as f:
        header = A3DHeader.from_file(f)
        for _ in range(header.num_patches):
            A3DPatch.from_file(f)
        for _ in range(256):
            A3DMaterial.read(f)

        first_int = struct.unpack("<i", f.read(4))[0]
        if first_int < 0:
            format_version = -first_int
            instance_count = struct.unpack("<i", f.read(4))[0]
        else:
            format_version = 0
            instance_count = first_int

        for _ in range(instance_count):
            A3DInstance.from_file(f, format_version)

        if format_version < 4:
            return None

        has_player_start = struct.unpack("<i", f.read(4))[0]
        if not has_player_start:
            return None
        return A3DPlayerStart.from_file(f)


def reverse_instance_transform(transform, legacy=True, z_baseline=None):
    """Convert engine-space matrix values back to Blender-space.

    Export applies:
    - XY translation offset by PATCH_OFFSET * PATCH_SIZE
    - Z translation stored in terrain-height space
    - Full third basis column scaled by HEIGHT_SCALE
    """
    t = list(transform)
    t[12], t[13], t[14] = reverse_world_position(
        t[12:15],
        legacy=legacy,
        z_baseline=z_baseline,
    )
    t[8] /= HEIGHT_SCALE
    t[9] /= HEIGHT_SCALE
    t[10] /= HEIGHT_SCALE
    return t


def resolve_mesh_path(mesh_name, search_paths, a3d_dir):
    """Resolve a mesh path for the common repo layout: assets/a3d/ and assets/meshes/ are siblings."""
    repo_root = os.path.dirname(a3d_dir)
    candidates = [
        a3d_dir,
        repo_root,
        os.path.join(repo_root, "assets", "meshes"),
    ]

    for entry in search_paths.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        if os.path.isabs(entry):
            candidates.append(entry)
        else:
            candidates.append(os.path.join(a3d_dir, entry))
            candidates.append(os.path.join(repo_root, entry))

    seen = set()
    for base_dir in candidates:
        norm_base = os.path.normpath(base_dir)
        if norm_base in seen:
            continue
        seen.add(norm_base)

        direct = os.path.join(norm_base, mesh_name)
        if os.path.exists(direct):
            return direct

        basename = os.path.basename(mesh_name)
        in_meshes = os.path.join(norm_base, "assets", "meshes", basename)
        if os.path.exists(in_meshes):
            return in_meshes

        sibling = os.path.join(norm_base, basename)
        if os.path.exists(sibling):
            return sibling

    return None
