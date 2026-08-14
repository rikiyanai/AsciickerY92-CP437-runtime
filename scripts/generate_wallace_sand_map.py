#!/usr/bin/env python3
"""Rebuild the canonical runtime map as the Wallace/Gromit sand scene.

The existing patch footprint remains the geographic owner.  This generator
replaces its visual and elevation content deterministically, then writes the
single requested rocket instance and the map-owned player start.
"""

from __future__ import annotations

import argparse
import math
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
PATCH_WORLD_SIZE = 8.0
HEIGHT_SAMPLE_STEP = 2.0
SAND_BASE_HEIGHT = 176.0
SAND_MIN_HEIGHT = 80
SAND_MAX_HEIGHT = 320
PLAYER_START_XY = (-4.0, -74.0)
ROCKET_XY = (-16.0, -48.0)
ROCKET_HORIZONTAL_SCALE = 4.0
# Runtime Z is stored at 16 height units per horizontal world unit. Rotating
# the rocket upright therefore needs this compensated scale to preserve the
# GLB's proportions in the user-facing projection.
ROCKET_VERTICAL_SCALE = ROCKET_HORIZONTAL_SCALE * 16.0
# After a -90-degree X rotation, AKM +Y maps to runtime -Z. This exact source
# bound owns ground seating for the rotated mesh.
ROCKET_AKM_MAX_Y = 4.409


def _raw_sand_height(x: float, y: float) -> float:
    """Return a continuous multi-frequency dune field in world coordinates."""

    # Height values are projected in units of HEIGHT_SCALE=16. These amplitudes
    # deliberately create repeated local changes larger than one projected
    # height unit so hills remain visible in the terminal-resolution viewport.
    broad_dunes = 64.0 * math.sin(x * 0.038) * math.cos(y * 0.032)
    crossing_dunes = 40.0 * math.sin((x + y) * 0.055)
    knolls = 24.0 * math.cos((1.6 * x - y) * 0.090)
    bumps = 12.0 * math.sin(x * 0.145 + y * 0.119)
    return SAND_BASE_HEIGHT + broad_dunes + crossing_dunes + knolls + bumps


def _flatten_site(
    height: float,
    x: float,
    y: float,
    center: tuple[float, float],
    inner_radius: float,
    outer_radius: float,
) -> float:
    """Blend one small usable pad into the surrounding rolling terrain."""

    distance = math.hypot(x - center[0], y - center[1])
    if distance >= outer_radius:
        return height
    center_height = _raw_sand_height(*center)
    if distance <= inner_radius:
        return center_height
    t = (distance - inner_radius) / (outer_radius - inner_radius)
    smooth_t = t * t * (3.0 - 2.0 * t)
    return center_height + (height - center_height) * smooth_t


def _sand_height(x: float, y: float) -> int:
    """Own the canonical elevation for every duplicated A3D edge vertex."""

    height = _raw_sand_height(x, y)
    height = _flatten_site(height, x, y, PLAYER_START_XY, 3.0, 8.0)
    height = _flatten_site(height, x, y, ROCKET_XY, 5.0, 12.0)
    return max(SAND_MIN_HEIGHT, min(SAND_MAX_HEIGHT, round(height)))


def _patch_diag(height: list[list[int]]) -> int:
    """Choose the shorter elevation diagonal independently for each quad."""

    diag = 0
    for row in range(4):
        for col in range(4):
            if abs(height[row][col] - height[row + 1][col + 1]) > abs(
                height[row][col + 1] - height[row + 1][col]
            ):
                diag |= 1 << (row * 4 + col)
    return diag


def _sand_material() -> A3DMaterial:
    material = A3DMaterial()
    glyphs = (46, 46, 37, 58, 46, 42, 58, 46, 37, 46, 58, 42, 46, 37, 58, 46)
    for ramp_index in range(4):
        for shade_index in range(16):
            # Preserve the operator-approved original yellow/brown ramp.
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
        # World-coordinate sampling is the continuity invariant: neighboring
        # patches independently calculate byte-identical shared edge vertices.
        patch.height = [
            [
                _sand_height(
                    patch.x * PATCH_WORLD_SIZE + col * HEIGHT_SAMPLE_STEP,
                    patch.y * PATCH_WORLD_SIZE + row * HEIGHT_SAMPLE_STEP,
                )
                for col in range(5)
            ]
            for row in range(5)
        ]
        patch.diag = _patch_diag(patch.height)
    materials[SAND_MATERIAL_ID] = _sand_material()

    player_ground_z = float(_sand_height(*PLAYER_START_XY))
    rocket_ground_z = float(_sand_height(*ROCKET_XY))
    player_start_position = (*PLAYER_START_XY, player_ground_z)
    rocket_position = (
        *ROCKET_XY,
        rocket_ground_z + ROCKET_AKM_MAX_Y * ROCKET_VERTICAL_SCALE,
    )
    rocket_transform = [
        ROCKET_HORIZONTAL_SCALE, 0.0, 0.0, 0.0,
        0.0, 0.0, -ROCKET_VERTICAL_SCALE, 0.0,
        0.0, ROCKET_HORIZONTAL_SCALE, 0.0, 0.0,
        *rocket_position, 1.0,
    ]
    rocket = A3DInstance(
        mesh_name="toy_rocket_ship.akm",
        inst_name="WallaceLaunchRocket",
        transform=rocket_transform,
        flags=0x1 | 0x2,
        story_id=-1,
    )
    player_start = A3DPlayerStart(pos=player_start_position, yaw=0.0, dir=0.0)
    marker = A3DMinimapMarker(
        name="rocket",
        label="Rocket",
        x=ROCKET_XY[0],
        y=ROCKET_XY[1],
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
