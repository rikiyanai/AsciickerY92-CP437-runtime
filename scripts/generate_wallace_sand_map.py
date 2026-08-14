#!/usr/bin/env python3
"""Rebuild the canonical runtime map as the Wallace/Gromit sand scene.

The existing patch footprint remains the geographic owner.  This generator
replaces its visual and elevation content deterministically, then writes the
single requested rocket instance and the map-owned player start.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCENE_MODULES = REPO_ROOT / "addons" / "io_asciicker" / "scene"
sys.path.insert(0, str(SCENE_MODULES))

from a3d_format import (  # noqa: E402
    A3DHeader,
    A3DInstance,
    A3DMaterial,
    A3DMinimapMarker,
    A3DPatch,
    A3DPlayerStart,
    MatCell,
)


FORMAT_VERSION = 4
SAND_MATERIAL_ID = 4
SAND_HEIGHT = 128
PLAYER_START = (-2.8, -73.6, 128.0)
ROCKET_POSITION = (7.2, -68.0, 128.0)


def _sand_material() -> A3DMaterial:
    material = A3DMaterial()
    glyphs = (46, 46, 37, 58, 46, 42, 58, 46, 37, 46, 58, 42, 46, 37, 58, 46)
    for ramp_index in range(4):
        for shade_index in range(16):
            # Both cell colors stay in the yellow/brown range. The four ramps
            # vary only luminance, keeping every terrain-facing cell sandy.
            light = max(0, 28 - ramp_index * 8 - shade_index * 2)
            background = (
                max(0, 186 + light),
                max(0, 140 + light),
                max(0, 28 + light // 2),
            )
            foreground = (
                min(255, background[0] + 34),
                min(255, background[1] + 31),
                min(255, background[2] + 18),
            )
            material.shade[ramp_index][shade_index] = MatCell(
                fg=foreground,
                gl=glyphs[shade_index],
                bg=background,
                flags=0,
            )
    return material


def _read_base(path: Path) -> tuple[A3DHeader, list[A3DPatch], list[A3DMaterial]]:
    with path.open("rb") as stream:
        header = A3DHeader.from_file(stream)
        patches = [A3DPatch.from_file(stream) for _ in range(header.num_patches)]
        materials = [A3DMaterial.read(stream) for _ in range(256)]
    return header, patches, materials


def build(path: Path) -> None:
    header, patches, materials = _read_base(path)
    for patch in patches:
        patch.visual = [[SAND_MATERIAL_ID] * 8 for _ in range(8)]
        patch.height = [[SAND_HEIGHT] * 5 for _ in range(5)]
        patch.diag = 0
    materials[SAND_MATERIAL_ID] = _sand_material()

    rocket_transform = [
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        *ROCKET_POSITION, 1.0,
    ]
    rocket = A3DInstance(
        mesh_name="toy_rocket_ship.akm",
        inst_name="WallaceLaunchRocket",
        transform=rocket_transform,
        flags=0x1 | 0x2,
        story_id=-1,
    )
    player_start = A3DPlayerStart(pos=PLAYER_START, yaw=0.0, dir=0.0)
    marker = A3DMinimapMarker(
        name="rocket",
        label="Rocket",
        x=ROCKET_POSITION[0],
        y=ROCKET_POSITION[1],
        fg=226,
        glyph="R",
        marker_type=A3DMinimapMarker.TYPE_CUSTOM,
    )

    with path.open("wb") as stream:
        header.write(stream)
        for patch in patches:
            patch.write(stream)
        for material in materials:
            material.write(stream)
        stream.write(struct.pack("<i", -FORMAT_VERSION))
        stream.write(struct.pack("<i", 1))
        rocket.write(stream)
        stream.write(struct.pack("<i", 1))
        player_start.write(stream)
        stream.write(struct.pack("<i", 0))  # no hostile enemy generators
        stream.write(struct.pack("<i", 1))
        marker.write(stream)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=REPO_ROOT / "assets" / "a3d" / "game_map_y8.a3d",
    )
    args = parser.parse_args()
    build(args.path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
