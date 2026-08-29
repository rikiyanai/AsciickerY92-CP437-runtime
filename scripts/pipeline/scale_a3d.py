#!/usr/bin/env python3
"""Scale an entire A3D map by a uniform factor.

Scales all spatial data: terrain patch positions, height values,
mesh/sprite/item instance positions+transforms, player start,
enemy generators, and minimap markers.

Usage:
  python3 scripts/pipeline/scale_a3d.py INPUT.a3d OUTPUT.a3d 0.65
"""
import argparse
import struct
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "addons" / "io_asciicker" / "scene"))
import a3d_format as fmt


def scale_map(input_path, output_path, factor):
    with open(input_path, "rb") as f:
        header = fmt.A3DHeader.from_file(f)
        patches = [fmt.A3DPatch.from_file(f) for _ in range(header.num_patches)]

        mat_size = 256 * 512
        materials = f.read(mat_size)
        if len(materials) != mat_size:
            raise RuntimeError("Truncated material section")

        raw_fmt_version = struct.unpack("<i", f.read(4))[0]
        inst_fmt = -raw_fmt_version if raw_fmt_version < 0 else raw_fmt_version
        inst_count = struct.unpack("<i", f.read(4))[0]
        instances = [fmt.A3DInstance.from_file(f, format_version=inst_fmt) for _ in range(inst_count)]

        player_start = None
        if raw_fmt_version <= -4:
            has_ps_raw = f.read(4)
            if len(has_ps_raw) == 4 and struct.unpack("<i", has_ps_raw)[0]:
                player_start = fmt.A3DPlayerStart.from_file(f)

        enemy_gens = []
        eg_raw = f.read(4)
        if len(eg_raw) == 4:
            eg_count = struct.unpack("<i", eg_raw)[0]
            enemy_gens = [fmt.A3DEnemyGen.from_file(f) for _ in range(eg_count)]

        markers = []
        mk_raw = f.read(4)
        if len(mk_raw) == 4:
            mk_count = struct.unpack("<i", mk_raw)[0]
            markers = [fmt.A3DMinimapMarker.from_file(f) for _ in range(mk_count)]

    # === SCALE PATCHES ===
    for p in patches:
        # Patch grid position: each patch covers VISUAL_CELLS (8) world units
        # Patch (x,y) is in patch-grid coords, world = patch * VISUAL_CELLS
        # Scale the patch position
        p.x = int(round(p.x * factor))
        p.y = int(round(p.y * factor))

        # Height values: scale proportionally
        for row in range(len(p.height)):
            for col in range(len(p.height[row])):
                p.height[row][col] = int(round(p.height[row][col] * factor)) & 0xFFFF

    # === SCALE INSTANCES ===
    for inst in instances:
        if inst.variant == 'mesh':
            # Transform is 4x4 column-major. [12,13,14] = translation
            # Scale columns [0-2] contain rotation+scale
            # Scale translation
            inst.transform[12] *= factor
            inst.transform[13] *= factor
            inst.transform[14] *= factor
            # Scale the scale component of the rotation columns
            # Each column's length is the scale along that axis
            # Column 0: [0,1,2], Column 1: [4,5,6], Column 2: [8,9,10]
            for col_start in (0, 4, 8):
                for i in range(3):
                    inst.transform[col_start + i] *= factor
        elif inst.variant in ('sprite', 'item'):
            inst.pos[0] *= factor
            inst.pos[1] *= factor
            inst.pos[2] *= factor

    # === SCALE PLAYER START ===
    if player_start:
        player_start.pos[0] *= factor
        player_start.pos[1] *= factor
        player_start.pos[2] *= factor

    # === SCALE ENEMY GENS ===
    for eg in enemy_gens:
        eg.pos[0] *= factor
        eg.pos[1] *= factor
        eg.pos[2] *= factor

    # === SCALE MINIMAP MARKERS ===
    for mk in markers:
        mk.x *= factor
        mk.y *= factor

    # === WRITE OUTPUT ===
    with open(output_path, "wb") as f:
        header.write(f)
        for p in patches:
            p.write(f)
        f.write(materials)

        f.write(struct.pack("<i", raw_fmt_version))
        f.write(struct.pack("<i", len(instances)))
        for inst in instances:
            inst.write(f)

        if raw_fmt_version <= -4:
            if player_start:
                f.write(struct.pack("<i", 1))
                player_start.write(f)
            else:
                f.write(struct.pack("<i", 0))

        f.write(struct.pack("<i", len(enemy_gens)))
        for eg in enemy_gens:
            eg.write(f)

        f.write(struct.pack("<i", len(markers)))
        for mk in markers:
            mk.write(f)

    print(f"Scaled {input_path} -> {output_path} at {factor}x")
    print(f"  Patches: {len(patches)}")
    print(f"  Instances: {len(instances)}")
    print(f"  Player start: {'yes' if player_start else 'no'}")
    print(f"  Enemy gens: {len(enemy_gens)}")
    print(f"  Markers: {len(markers)}")


def main():
    parser = argparse.ArgumentParser(description="Scale an A3D map by a uniform factor")
    parser.add_argument("input", help="Input A3D file")
    parser.add_argument("output", help="Output A3D file")
    parser.add_argument("factor", type=float, help="Scale factor (e.g. 0.65)")
    args = parser.parse_args()
    scale_map(args.input, args.output, args.factor)


if __name__ == "__main__":
    main()
