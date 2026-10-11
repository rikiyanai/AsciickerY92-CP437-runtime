#!/usr/bin/env python3
"""2026-10-08 -- Generate assets/a3d/preview_flat_green.a3d.

A flat, scenery-free, grass-green preview map used by the offscreen render
harness (tools/render_harness) to record skin turntables.  Modeled on
scripts/generate_wallace_sand_map.py:

  * patches are written with the repo's own A3D writer (addons/io_asciicker),
  * terrain sits at the canonical export baseline Z=128, well above the
    default water plane (55) -- FAILURE_LOG P0C-07 recorded that Z=0 renders
    underwater-tinted,
  * the 256-entry material table is copied from the shipped game map, and one
    material id is replaced with a mid-green grass ramp,
  * v4 world trailer: no instances, one map-owned player start at the
    origin, no enemy generators, no minimap markers.

This does NOT touch the default game map; it only reads game_map_y8.a3d for
its material table.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "addons" / "io_asciicker" / "scene"))

from a3d_format import (  # noqa: E402
    A3DHeader,
    A3DMaterial,
    A3DPatch,
    A3DPlayerStart,
    MatCell,
    TERRAIN_EXPORT_BASELINE,
)

FORMAT_VERSION = 4
GREEN_MATERIAL_ID = 1
FLAT_HEIGHT = TERRAIN_EXPORT_BASELINE  # 128
PATCH_RADIUS = 8  # patches -8..7 on each axis -> 128x128 world units
DEFAULT_MATERIAL_SOURCE = REPO_ROOT / "assets" / "a3d" / "game_map_y8.a3d"
DEFAULT_OUTPUT = REPO_ROOT / "assets" / "a3d" / "preview_flat_green.a3d"

# Sparse grass texture: mostly blank cells with a few blades/tufts.
GRASS_GLYPHS = (32, 44, 32, 39, 32, 46, 34, 32, 44, 32, 96, 32, 39, 32, 46, 32)


def _green_material() -> A3DMaterial:
    """Mid-green ramp.  Colors sit on the 51-step xterm cube so the
    renderer's palette quantisation keeps them green at every shade."""

    material = A3DMaterial()
    # (bg, fg) per shade bucket, darkest -> brightest.
    bg_ramp = [(0, 51, 0), (0, 102, 0), (51, 153, 51), (51, 153, 51), (102, 204, 51)]
    fg_ramp = [(0, 102, 0), (51, 153, 0), (102, 204, 51), (102, 204, 51), (153, 255, 102)]
    for ramp_index in range(4):
        for shade_index in range(16):
            bucket = min(len(bg_ramp) - 1, shade_index * len(bg_ramp) // 16)
            material.shade[ramp_index][shade_index] = MatCell(
                fg=fg_ramp[bucket],
                gl=GRASS_GLYPHS[(shade_index + ramp_index * 5) % 16],
                bg=bg_ramp[bucket],
                flags=0,
            )
    return material


def _read_materials(path: Path) -> list[A3DMaterial]:
    with path.open("rb") as stream:
        header = A3DHeader.from_file(stream)
        for _ in range(header.num_patches):
            A3DPatch.from_file(stream)
        return [A3DMaterial.read(stream) for _ in range(256)]


def build(output: Path, material_source: Path) -> None:
    materials = _read_materials(material_source)
    materials[GREEN_MATERIAL_ID] = _green_material()

    patches = []
    for py in range(-PATCH_RADIUS, PATCH_RADIUS):
        for px in range(-PATCH_RADIUS, PATCH_RADIUS):
            patch = A3DPatch(px, py)
            patch.visual = [[GREEN_MATERIAL_ID] * 8 for _ in range(8)]
            patch.height = [[FLAT_HEIGHT] * 5 for _ in range(5)]
            patch.diag = 0
            patches.append(patch)

    player_start = A3DPlayerStart(pos=(0.0, 0.0, float(FLAT_HEIGHT)), yaw=0.0, dir=0.0)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        A3DHeader(len(patches)).write(stream)
        for patch in patches:
            patch.write(stream)
        for material in materials:
            material.write(stream)
        stream.write(struct.pack("<i", -FORMAT_VERSION))
        stream.write(struct.pack("<i", 0))  # no instances (no scenery)
        stream.write(struct.pack("<i", 1))
        player_start.write(stream)
        stream.write(struct.pack("<i", 0))  # no enemy generators
        stream.write(struct.pack("<i", 0))  # no minimap markers
    print(f"wrote {output} ({len(patches)} patches, Z={FLAT_HEIGHT}, material {GREEN_MATERIAL_ID})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", nargs="?", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--material-source", type=Path, default=DEFAULT_MATERIAL_SOURCE)
    args = parser.parse_args()
    build(args.output.resolve(), args.material_source.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
