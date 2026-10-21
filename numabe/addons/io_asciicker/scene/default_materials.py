# Default materials for A3D export
# Extracts materials from existing game maps or creates fallback
#
# [DATA-CONTRACT:A3D] Material palette -- 256 materials x 512 bytes each.
#     Must match the binary layout expected by the C++ engine after the terrain
#     patches section in the .a3d file (see a3d_format.py and world.cpp).
# [DEPENDENCY:BLENDER] Consumed by export_a3d.py during Blender addon export;
#     no direct Blender API calls here.

"""
Default material palette provider for A3D export.

ARCHITECTURE
============
The A3D file format requires a 256-entry material palette (131072 bytes total)
written immediately after the terrain patches.  This module supplies that
palette through a two-tier strategy:

    1. **Extract from reference map** -- ``extract_materials_binary()`` reads the
       raw 131072-byte block from an existing, known-good ``.a3d`` file stored
       in the ``assets/a3d/`` directory.  This preserves the hand-tuned shade ramps from
       the original game.
    2. **Procedural fallback** -- ``create_fallback_materials()`` generates a
       basic palette when no reference file is available (e.g. fresh checkout
       without game assets).

KEY EXPORTS
-----------
- ``get_default_materials_binary()`` -- Entry point called by ``export_a3d.py``.
  Returns exactly ``MATERIALS_SIZE`` bytes of raw material data.
- ``MATERIAL_NAMES`` -- Human-readable names for the first 8 material slots.

PIPELINE CONTEXT
----------------
Material authoring is still tied to the original asciiid editor's palette.
The fallback palette is a rough approximation; for production maps, the
reference ``.a3d`` file (``assets/a3d/game_map_y8_original_game_map.a3d``) should
be present.

TODO(PIPELINE-FIX): Provide a standalone material editor or Blender-based
    palette painter so artists can author shade ramps without the asciiid
    editor or a reference .a3d file.
"""

import os
import struct

from io_asciicker import path_utils

# ---------------------------------------------------------------------------
# Human-readable material slot names.
# WHY: Material IDs 0-7 map to the primary terrain types used in the original
# game.  The Red channel of Blender vertex colors encodes these IDs.
# Slots 8-255 are available for custom materials but currently unnamed.
# ---------------------------------------------------------------------------
MATERIAL_NAMES = {
    0: "Water",
    1: "Grass",
    2: "Dirt",
    3: "Stone",
    4: "Sand",
    5: "Snow",
    6: "Wood",
    7: "Steel",
}

# [DATA-CONTRACT:A3D] Total byte size of the material palette block.
# 256 materials * 512 bytes/material (4 ramps * 16 shades * 8 bytes/cell).
MATERIALS_SIZE = 256 * 512  # 256 materials * 512 bytes each


def get_default_materials_path():
    """Locate an existing ``.a3d`` file to use as the material palette source.

    Searches the project's ``assets/a3d/`` directory for known reference maps, trying
    them in priority order (Y8 original > Y8 > Y7).

    Returns:
        Absolute path to the first found ``.a3d`` file, or ``None`` if no
        reference map is available on disk.
    """
    # Try multiple strategies to find the project root with the assets/a3d/ directory.
    # The old dirname(dirname(__file__)) fails when the addon is installed
    # outside the repo (e.g. Blender's addons dir). find_repo_root() can
    # return false positives (Blender's scripts/ matches the sentinel).
    # So we validate each candidate by checking for the assets/a3d/ directory.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        path_utils.find_repo_root_from_env(),
        path_utils.find_repo_root(script_dir),
        os.path.dirname(os.path.dirname(script_dir)),
    ]
    project_root = None
    for candidate in candidates:
        if candidate and os.path.isdir(os.path.join(candidate, "assets", "a3d")):
            project_root = candidate
            break
    if not project_root:
        return None

    # WHY this priority order: The Y8 original map has the most polished
    # material palette; Y7 is a usable fallback from the previous release.
    paths_to_try = [
        os.path.join(project_root, "assets", "a3d", "game_map_y8_original_game_map.a3d"),
        os.path.join(project_root, "assets", "a3d", "game_map_y8.a3d"),
        os.path.join(project_root, "assets", "a3d", "game_map_y7.a3d"),
    ]

    for path in paths_to_try:
        if os.path.exists(path):
            return path

    return None


def extract_materials_binary(a3d_path):
    """Extract raw materials binary data from an existing A3D file.

    Seeks past the header and terrain patches to the material palette region
    and reads exactly ``MATERIALS_SIZE`` bytes.

    Args:
        a3d_path: Path to a valid ``.a3d`` file.

    Returns:
        ``bytes`` of length ``MATERIALS_SIZE`` (131072).

    Raises:
        ValueError: If the signature is not ``AS3D`` or the read is short.
    """
    with open(a3d_path, 'rb') as f:
        sig = f.read(4)
        if sig != b'AS3D':
            raise ValueError("Invalid A3D file")

        header_size = struct.unpack('<I', f.read(4))[0]
        num_patches = struct.unpack('<I', f.read(4))[0]
        f.read(4)  # reserved

        # WHY: The patch size is derived from the A3D binary layout defined in
        # a3d_format.py / terrain.h.  Each terrain patch stores:
        #   8 bytes  -- (x, y) grid coordinates (2x int32)
        #   128 bytes -- visual cell data (16x16 / 2 = 128 nibbles packed)
        #   50 bytes  -- height samples (5x10 uint8, see HEIGHT_CELLS)
        #   2 bytes   -- diagonal flags (bit-packed per quad)
        # Total = 188 bytes.  If the engine patch struct changes, this must
        # be updated in lockstep.
        # [DATA-CONTRACT:A3D]
        # TODO(PIPELINE-FIX): Import this constant from a3d_format.py instead
        # of hard-coding it, so changes to the patch struct propagate
        # automatically.
        patch_size = 188
        materials_offset = header_size + num_patches * patch_size

        f.seek(materials_offset)
        materials_data = f.read(MATERIALS_SIZE)

        if len(materials_data) != MATERIALS_SIZE:
            raise ValueError(f"Incomplete materials: {len(materials_data)} bytes")

        return materials_data


def create_fallback_materials():
    """Create a basic procedural material palette when no reference map exists.

    Generates 256 materials (131072 bytes) with hard-coded color ramps for
    the 8 named material types.  Unnamed slots (8-255) default to neutral gray.

    Each material has:
      - 4 elevation ramps (flat -> steep), progressively darker.
      - 16 shade steps per ramp (light -> dark).
      - Glyph selection: ' ' for flat, '.' gentle, ':' moderate, '#' steep.

    Returns:
        ``bytes`` of length ``MATERIALS_SIZE``.

    .. note::
        This palette is a rough approximation.  Production maps should use
        materials extracted from a reference ``.a3d`` file for accurate
        rendering.
    """
    data = bytearray(MATERIALS_SIZE)

    # WHY these specific RGB tuples: They approximate the original game's
    # terrain colors so that fallback-exported maps are at least recognizable
    # even without the hand-tuned palette from the original editor.
    material_colors = {
        0: ((50, 100, 200), (30, 60, 120)),   # Water - blue
        1: ((80, 160, 60), (40, 100, 30)),    # Grass - green
        2: ((140, 100, 60), (80, 60, 30)),    # Dirt - brown
        3: ((130, 130, 140), (80, 80, 90)),   # Stone - gray
        4: ((220, 200, 140), (180, 160, 100)), # Sand - tan
        5: ((240, 245, 255), (200, 210, 230)), # Snow - white
        6: ((100, 70, 40), (60, 40, 20)),     # Wood - brown
        7: ((160, 160, 180), (120, 120, 140)), # Steel - silver
    }

    for mat_idx in range(256):
        mat_offset = mat_idx * 512

        # Get colors for this material
        if mat_idx in material_colors:
            fg_base, bg_base = material_colors[mat_idx]
        else:
            # Default gray for unnamed material slots
            fg_base = (150, 150, 150)
            bg_base = (100, 100, 100)

        # For each of 4 elevation ramps (flat, gentle, moderate, steep)
        for ramp in range(4):
            ramp_offset = mat_offset + ramp * 128  # 16 shades * 8 bytes
            ramp_darken = ramp * 15  # Steeper slopes render darker

            # For each of 16 shade steps (light to dark)
            for shade in range(16):
                cell_offset = ramp_offset + shade * 8
                shade_darken = shade * 8

                # Calculate colors -- progressively darker with ramp and shade
                fg = tuple(max(0, c - ramp_darken - shade_darken) for c in fg_base)
                bg = tuple(max(0, c - ramp_darken - shade_darken - 20) for c in bg_base)

                # [DATA-CONTRACT:A3D] Per-cell layout (8 bytes):
                #   [0] fg_r  [1] fg_g  [2] fg_b  [3] glyph
                #   [4] bg_r  [5] bg_g  [6] bg_b  [7] flags
                # This must match the Cell struct in render.cpp / terrain.h.
                # Foreground RGB (bytes 0-2)
                data[cell_offset + 0] = fg[0]
                data[cell_offset + 1] = fg[1]
                data[cell_offset + 2] = fg[2]

                # Glyph -- visual density increases with slope steepness
                # WHY: The ASCII renderer uses these glyphs to convey slope
                # angle.  Flat terrain is blank; steep terrain is dense '#'.
                glyphs = [32, 46, 58, 35]  # ' ', '.', ':', '#'
                data[cell_offset + 3] = glyphs[ramp]

                # Background RGB (bytes 4-6)
                data[cell_offset + 4] = bg[0]
                data[cell_offset + 5] = bg[1]
                data[cell_offset + 6] = bg[2]

                # Flags (byte 7) -- reserved, currently unused
                data[cell_offset + 7] = 0

    return bytes(data)


def get_default_materials_binary():
    """Get default materials as raw binary data.

    This is the single entry point called by :func:`export_a3d.save_a3d`.
    It attempts extraction from a reference ``.a3d`` first, falling back to
    the procedural generator on failure.

    Returns:
        ``bytes`` of length ``MATERIALS_SIZE`` (131072).
    """
    default_path = get_default_materials_path()

    if default_path:
        try:
            print(f"Extracting materials from: {default_path}")
            return extract_materials_binary(default_path)
        except Exception as e:
            # TODO(PIPELINE-FIX): This blanket except silently falls through
            # to the procedural palette for *any* error (corrupt file, wrong
            # struct version, permission denied).  Consider logging the full
            # traceback and/or distinguishing recoverable vs. fatal errors.
            print(f"Warning: Could not extract materials: {e}")

    print("Using fallback materials")
    return create_fallback_materials()
