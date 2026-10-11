#!/usr/bin/env python3
"""Regenerate mount-wrapper rear/front XPs from upstream sources via subtraction.

PROCEDURE (per user-supplied algorithm):
  1. Load monolithic mounted XP, e.g. wolfie-0100.xp.
  2. Load standalone base mount XP, e.g. wolfie.xp.
  3. Load rider/player reference XP, e.g. player-0100.xp.
  4. Solve offsets:
       - base mount  -> mounted monolith
       - rider       -> mounted monolith
  5. Place base mount and rider grids into mounted-frame coordinates.
  6. Compute residual = mounted_monolith - aligned_base_mount.
  7. Classify each visible cell of the mounted monolith using rider/base
     visibility and equality (mount_rear_surface, mount_front_surface,
     rider_visible_match) per the existing classifier in
     scripts/pipeline/mounted_rider_residual_compare.py.
  8. Build a new XP atlas with the SAME layout as the mount-base sprite
     (wolfie.xp / bigbee.xp) — layers 0 and 1 copied verbatim; layer 2
     contains only the cells classified as the requested surface role
     (rear OR front), all other cells transparent.

Tombstone discipline (memory project_bundle_refactor_lane_state.md):
  - Inputs are upstream-source-owned: upstream/master:sprites/*.xp (Y9-2
    copies have been verified identical for the files used here).
  - No old-commit recovery, no pipeline-v3 import, no resurrected scripts.
  - Output paths must NOT collide with the 96 orphaned combo XPs; new
    wrapper files land at assets/sprites/<prefix>-body-{rear,front}.xp.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline import mounted_rider_offset as mro
from scripts.pipeline.mounted_rider_residual_compare import (
    _classify_surface_cells,
    _frame_grid,
    _place_grid,
    _transparent_cell,
)
from scripts.pipeline.xp_core import XPFile, XPLayer


def _make_transparent_layer(width: int, height: int) -> XPLayer:
    """Construct a layer of pure transparent cells with magenta key."""
    transparent_glyph = 32  # space
    transparent_fg = (0, 0, 0)
    transparent_bg = (255, 0, 255)  # SPRITE_TRANSPARENT_INDEX magenta
    data = [
        [(transparent_glyph, transparent_fg, transparent_bg) for _ in range(width)]
        for _ in range(height)
    ]
    return XPLayer(width=width, height=height, data=data)


def generate_wrapper(
    *,
    mounted_xp_path: Path,
    mount_base_xp_path: Path,
    player_xp_path: Path,
    output_dir: Path,
    output_prefix: str,
    surface: str,  # "rear" or "front"
    offset_search: int = 10,
    mount_base_anim_map: dict[int, int] | None = None,
) -> Path:
    """Generate a wrapper XP for one surface role across all (angle, anim, frame, proj).

    Returns the output path.
    """
    assert surface in ("rear", "front"), f"surface must be rear|front, got {surface!r}"

    mounted_xp = mro._load_xp(mounted_xp_path)
    mount_base_xp = mro._load_xp(mount_base_xp_path)
    player_xp = mro._load_xp(player_xp_path)
    mounted_layout = mro._parse_layout(mounted_xp)
    mount_base_layout = mro._parse_layout(mount_base_xp)
    player_layout = mro._parse_layout(player_xp)

    # Output atlas matches mount-base atlas dimensions (which equals the wrapper's
    # expected layout in the deleted authoring).
    out_layer = _make_transparent_layer(
        width=mount_base_xp.layers[2].width,
        height=mount_base_xp.layers[2].height,
    )

    angles = mounted_layout.angles
    anim_lengths = mounted_layout.anims
    projs = mounted_layout.projs

    total_rear = 0
    total_front = 0
    total_rider = 0
    frames_processed = 0

    bucket_key = f"mount_{surface}_surface"

    for angle in range(angles):
        for anim_index in range(len(anim_lengths)):
            anim_length = anim_lengths[anim_index]
            # Map mounted anim_index to mount_base anim_index (defaults to identity,
            # clamped to mount-base's available anim count).
            if mount_base_anim_map is not None and anim_index in mount_base_anim_map:
                base_anim_index = mount_base_anim_map[anim_index]
            else:
                base_anim_index = min(anim_index, len(mount_base_layout.anims) - 1)
            base_anim_length = mount_base_layout.anims[base_anim_index]
            for frame_index in range(anim_length):
                # Clamp/wrap frame index into mount-base's available frames
                base_frame_index = frame_index % base_anim_length
                for proj in range(projs):
                    # Solve offsets for this frame
                    mount_base_cells = list(mro.frame_cells(
                        mount_base_xp, mount_base_layout,
                        angle=angle, anim_index=base_anim_index, frame_index=base_frame_index,
                        proj=proj, layer_index=2,
                    ))
                    mounted_cells = list(mro.frame_cells(
                        mounted_xp, mounted_layout,
                        angle=angle, anim_index=anim_index, frame_index=frame_index,
                        proj=proj, layer_index=2,
                    ))
                    player_cells = list(mro.frame_cells(
                        player_xp, player_layout,
                        angle=angle, anim_index=anim_index, frame_index=frame_index,
                        proj=proj, layer_index=2,
                    ))

                    if not mounted_cells:
                        continue

                    mount_offset = mro.best_offset(
                        mount_base_cells, mounted_cells,
                        min_dx=-offset_search, max_dx=offset_search,
                        min_dy=-offset_search, max_dy=offset_search,
                    )
                    rider_offset = mro.best_offset(
                        player_cells, mounted_cells,
                        min_dx=-offset_search, max_dx=offset_search,
                        min_dy=-offset_search, max_dy=offset_search,
                    )

                    mount_base_grid = _frame_grid(
                        mount_base_xp, mount_base_layout,
                        angle=angle, anim_index=base_anim_index, frame_index=base_frame_index,
                        proj=proj, layer_index=2,
                    )
                    mounted_visual_grid = _frame_grid(
                        mounted_xp, mounted_layout,
                        angle=angle, anim_index=anim_index, frame_index=frame_index,
                        proj=proj, layer_index=2,
                    )
                    player_grid = _frame_grid(
                        player_xp, player_layout,
                        angle=angle, anim_index=anim_index, frame_index=frame_index,
                        proj=proj, layer_index=2,
                    )

                    aligned_mount_base = _place_grid(
                        mount_base_grid,
                        out_width=mounted_layout.frame_width,
                        out_height=mounted_layout.frame_height,
                        dx=int(mount_offset["dx"]),
                        dy=int(mount_offset["dy"]),
                    )
                    shifted_rider = _place_grid(
                        player_grid,
                        out_width=mounted_layout.frame_width,
                        out_height=mounted_layout.frame_height,
                        dx=int(rider_offset["dx"]),
                        dy=int(rider_offset["dy"]),
                    )

                    buckets = _classify_surface_cells(
                        mounted_visual_grid,
                        aligned_mount_base,
                        shifted_rider,
                    )
                    total_rear += len(buckets["mount_rear_surface"])
                    total_front += len(buckets["mount_front_surface"])
                    total_rider += len(buckets["rider_visible_match"])
                    frames_processed += 1

                    # Output position: write cells into MOUNT-BASE atlas coordinates
                    # (subtract mount_offset to translate mounted-frame coords -> mount-base
                    # coords, since the output atlas matches the mount-base sprite).
                    surface_cells = buckets[bucket_key]
                    base_frame_x = (proj * mount_base_layout.anim_sum + sum(mount_base_layout.anims[:base_anim_index]) + base_frame_index) * mount_base_layout.frame_width
                    base_frame_y = angle * mount_base_layout.frame_height
                    mounted_frame_origin_x = 0  # we placed mounted_visual_grid at frame_width*0 in _frame_grid
                    mounted_frame_origin_y = 0
                    # _frame_grid returns a frame_w x frame_h grid for that specific frame
                    # (already extracted). So the cells in buckets are at coords relative
                    # to that frame's top-left. To place into mount-base atlas, add base_frame_*.
                    for cell in surface_cells:
                        row = cell["row"]  # within mounted frame
                        col = cell["col"]
                        # Translate from mounted-frame coords to mount-base atlas coords by
                        # subtracting the mount-base alignment offset (which is how the mount
                        # base was placed INTO the mounted frame).
                        base_col = col - int(mount_offset["dx"])
                        base_row = row - int(mount_offset["dy"])
                        if not (0 <= base_col < mount_base_layout.frame_width):
                            continue
                        if not (0 <= base_row < mount_base_layout.frame_height):
                            continue
                        out_x = base_frame_x + base_col
                        out_y = base_frame_y + base_row
                        if not (0 <= out_x < out_layer.width):
                            continue
                        if not (0 <= out_y < out_layer.height):
                            continue
                        glyph = int(cell["glyph_id"])
                        fg = cell.get("fg")
                        bg = cell.get("bg")
                        if fg is None:
                            fg = (0, 0, 0)
                        if bg is None:
                            bg = (255, 0, 255)
                        out_layer.data[out_y][out_x] = (glyph, tuple(fg), tuple(bg))

    # Build output XPFile by cloning mount-base structure, replacing layer 2
    out_xp = XPFile.__new__(XPFile)
    out_xp.version = mount_base_xp.version
    out_xp.layers = list(mount_base_xp.layers)
    out_xp.layers[2] = out_layer

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{output_prefix}-body-{surface}.xp"
    out_xp.save(str(out_path))

    print(f"  frames_processed={frames_processed}")
    print(f"  total_rear={total_rear}  total_front={total_front}  total_rider={total_rider}")
    print(f"  wrote {out_path}")
    return out_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate mount-wrapper rear/front XPs from upstream sources via subtraction.",
    )
    parser.add_argument("--mounted", required=True, type=Path,
                        help="Mounted monolith XP, e.g. assets/sprites/wolfie-0100.xp")
    parser.add_argument("--mount-base", required=True, type=Path,
                        help="Standalone mount XP, e.g. assets/sprites/wolfie.xp")
    parser.add_argument("--player", required=True, type=Path,
                        help="Rider reference XP matching mounted loadout, e.g. assets/sprites/player-0100.xp")
    parser.add_argument("--output-dir", default="assets/sprites", type=Path)
    parser.add_argument("--output-prefix", required=True,
                        help="Output filename prefix, e.g. 'wolfie' produces wolfie-body-rear.xp / wolfie-body-front.xp")
    parser.add_argument("--offset-search", type=int, default=10)
    parser.add_argument("--mount-base-anim-map", default=None,
                        help="Comma-separated mounted_anim:base_anim pairs, e.g. '0:1' to map mounted anim 0 -> mount-base anim 1. Use when mounted/mount-base have different anim track layouts (e.g. wolack [8] vs wolfie [1,8]).")
    args = parser.parse_args(argv)

    anim_map = None
    if args.mount_base_anim_map:
        anim_map = {}
        for pair in args.mount_base_anim_map.split(","):
            k, v = pair.split(":")
            anim_map[int(k.strip())] = int(v.strip())

    print(f"Generating rear wrapper...")
    generate_wrapper(
        mounted_xp_path=args.mounted,
        mount_base_xp_path=args.mount_base,
        player_xp_path=args.player,
        output_dir=args.output_dir,
        output_prefix=args.output_prefix,
        surface="rear",
        offset_search=args.offset_search,
        mount_base_anim_map=anim_map,
    )

    print(f"Generating front wrapper...")
    generate_wrapper(
        mounted_xp_path=args.mounted,
        mount_base_xp_path=args.mount_base,
        player_xp_path=args.player,
        output_dir=args.output_dir,
        output_prefix=args.output_prefix,
        surface="front",
        offset_search=args.offset_search,
        mount_base_anim_map=anim_map,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
