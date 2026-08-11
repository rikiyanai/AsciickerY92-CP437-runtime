# Default materials for A3D export
# Materials are extracted from game_map_y8.a3d at runtime if available

import os
import struct

# Material names for reference
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

# Size of materials section
MATERIALS_SIZE = 256 * 512  # 256 materials * 512 bytes each


def get_default_materials_path():
    """Get path to existing a3d file for material extraction"""
    # Try to find game_map_y8.a3d relative to this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)

    paths_to_try = [
        os.path.join(project_root, "a3d", "game_map_y8.a3d"),
        os.path.join(project_root, "a3d", "game_map_y7.a3d"),
    ]

    for path in paths_to_try:
        if os.path.exists(path):
            return path

    return None


def extract_materials_binary(a3d_path):
    """Extract raw materials binary data from A3D file"""
    with open(a3d_path, 'rb') as f:
        # Read header
        sig = f.read(4)
        if sig != b'AS3D':
            raise ValueError("Invalid A3D file")

        header_size = struct.unpack('<I', f.read(4))[0]
        num_patches = struct.unpack('<I', f.read(4))[0]
        f.read(4)  # reserved

        # Calculate patch size and skip patches
        # Each patch: 8 (xy) + 128 (visual) + 50 (height) + 2 (diag) = 188 bytes
        patch_size = 188
        materials_offset = header_size + num_patches * patch_size

        f.seek(materials_offset)

        # Read all 256 materials (512 bytes each)
        materials_data = f.read(MATERIALS_SIZE)

        if len(materials_data) != MATERIALS_SIZE:
            raise ValueError(f"Incomplete materials data: {len(materials_data)} bytes")

        return materials_data


def get_default_materials_binary():
    """Get default materials as raw binary data"""
    default_path = get_default_materials_path()
    if default_path:
        try:
            return extract_materials_binary(default_path)
        except Exception as e:
            print(f"Warning: Could not extract materials from {default_path}: {e}")

    # Fall back to empty/black materials
    return create_fallback_materials()


def create_fallback_materials():
    """Create basic fallback materials if no template available"""
    data = bytearray(MATERIALS_SIZE)

    # Create a simple grass material (material 1) as default
    for mat_idx in range(256):
        mat_offset = mat_idx * 512

        # For each of 4 ramps
        for ramp in range(4):
            ramp_offset = mat_offset + ramp * 128

            # For each of 16 shades
            for shade in range(16):
                cell_offset = ramp_offset + shade * 8

                # Darker shades as shade increases
                brightness = 255 - shade * 12

                # fg color
                data[cell_offset + 0] = brightness  # R
                data[cell_offset + 1] = brightness  # G
                data[cell_offset + 2] = brightness  # B

                # glyph (space = 32)
                data[cell_offset + 3] = 32

                # bg color (slightly darker)
                bg = max(0, brightness - 50)
                data[cell_offset + 4] = bg  # R
                data[cell_offset + 5] = bg  # G
                data[cell_offset + 6] = bg  # B

                # flags
                data[cell_offset + 7] = 0

    return bytes(data)
