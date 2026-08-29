#!/usr/bin/env python3
"""Generate wolack-attack-body.xp -- the rider-only body for wolack mounted attack.

This script only builds a mount/rider body geometry reference. The old
standalone actor-slot overlay generator is tombstoned under FL-3991; do not use
this script as an item-overlay authoring path.

Output:
    assets/sprites/wolack-attack-body.xp

The output is a valid XP atlas with:
- Frame width=10 (160 wide), frame height=13 (104 tall), 8 angles, anims=[8]
- frame_w=10 is required to match wolack-0001.xp frame topology for composed
  mounted attack profiles.
- Layer 0: constructed with same metadata as bigbee-attack-body.xp
- Layer 1: depth channel with zero depth
- Layer 2: rider cells from wolack-0101.json placed at all anim frames 0-7, both projections;
           everywhere else transparent (glyph=32, fg=KEY_RGB, bg=KEY_RGB)
"""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.xp_core import XPFile, XPLayer, rebase_visual_layer_transparency_keys

SPRITES_DIR = REPO_ROOT / "assets" / "sprites"
ANCHOR_PATH = REPO_ROOT / "docs" / "research" / "ascii" / "semantic_maps" / "wolack-0101.json"

from scripts.pipeline.xp_core import OVERLAY_KEY_RGB as KEY_RGB
TRANSPARENT_CELL = (32, KEY_RGB, KEY_RGB)


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def load_rider_cells_by_angle(anchor_path: Path) -> dict[int, list[dict]]:
    """Return dict mapping angle -> list of rider cell dicts (x, y, glyph, fg_rgb, bg_rgb)."""
    with open(anchor_path) as f:
        anchor = json.load(f)

    result: dict[int, list[dict]] = {}
    for _key, frame in anchor["frames"].items():
        angle = int(frame["angle"])
        rider_cells = []
        for region in frame.get("regions", []):
            for cell in region.get("semantic_cells", []):
                if cell.get("role") == "rider":
                    rider_cells.append(
                        {
                            "x": int(cell["x"]),
                            "y": int(cell["y"]),
                            "glyph": int(cell["glyph"]),
                            "fg": hex_to_rgb(cell["fg"]),
                            "bg": hex_to_rgb(cell["bg"]),
                        }
                    )
        result[angle] = rider_cells
    return result


from scripts.pipeline.xp_core import encode_digit as _encode_digit


def build_wolack_attack_body(output_path: Path) -> None:
    """Build wolack-attack-body.xp with wolack-0001 frame topology.

    Mounted attack composition uses wolack-0001.xp for mount rear/front/weapon.
    The rider body must share that atlas frame grid. Do not size this layer from
    deleted standalone attack overlay parity rules; those are not the production
    ActorVisualProfile composition owner.

    Layer 0 metadata cells:
        [0][0] = encode_digit(8)  -- angles=8
        [0][1] = encode_digit(8)  -- anim[0]=8
        [1][0] = encode_digit(2)  -- ref_y for proj0 = 2
        [1][1] = encode_digit(2*13 - 2) = encode_digit(24) = 'O'  -- ref_y for refl = 2
        [2][0] = encode_digit(2)  -- -ref_z = 2 → ref_z = -2
        [2][1] = encode_digit(21) = 'L'  -- -ref_z_refl = 21 → ref_z_refl = -21
    """
    angles = 8
    projs = 2
    anim_count = 8  # anims=[8]
    frame_w = 10    # match wolack-0001.xp
    frame_h = 13    # same frame height as wolack-body.xp
    sheet_w = frame_w * projs * anim_count   # 10 * 2 * 8 = 160
    sheet_h = frame_h * angles               # 13 * 8 = 104

    ref_y_proj0 = 2    # even ✓, in-bounds ✓ for attack overlay proj0 (ref[1]=2, sh=10)
    ref_y_refl = 16    # even ✓, in-bounds ✓ for attack overlay refl (ref[1]=16, sh=10)
    ref_z_proj0 = -2   # same as wolack-body.xp proj0
    ref_z_refl = -21   # same as bigbee-attack-body.xp refl

    print(f"Output: {sheet_w}x{sheet_h}, angles={angles}, projs={projs}, anims=[{anim_count}]")
    print(f"frame size: {frame_w}x{frame_h}, ref_y=[{ref_y_proj0},{ref_y_refl}]")

    # Load rider cells per angle from anchor
    rider_by_angle = load_rider_cells_by_angle(ANCHOR_PATH)
    print(f"Loaded rider cells for {len(rider_by_angle)} angles")

    # Build output XPFile
    out_xp = XPFile()
    out_xp.version = -1

    # Layer 0: construct metadata encoding
    new_layer0 = XPLayer(sheet_w, sheet_h)
    WHITE = (255, 255, 255)
    # Fill with default transparent cells
    for y in range(sheet_h):
        for x in range(sheet_w):
            new_layer0.data[y][x] = (32, WHITE, KEY_RGB)
    # Encode metadata
    new_layer0.data[0][0] = (_encode_digit(angles), WHITE, KEY_RGB)   # angles
    new_layer0.data[0][1] = (_encode_digit(anim_count), WHITE, KEY_RGB)  # anim[0]
    new_layer0.data[1][0] = (_encode_digit(ref_y_proj0), WHITE, KEY_RGB)  # ref_y proj0
    new_layer0.data[1][1] = (_encode_digit(2 * frame_h - ref_y_refl), WHITE, KEY_RGB)  # ref_y refl encoded
    new_layer0.data[2][0] = (_encode_digit(-ref_z_proj0), WHITE, KEY_RGB)  # -ref_z proj0
    new_layer0.data[2][1] = (_encode_digit(-ref_z_refl), WHITE, KEY_RGB)   # -ref_z refl
    out_xp.layers.append(new_layer0)

    # Layer 1: depth channel — use encode_digit(0)='0' with black-on-black.
    new_layer1 = XPLayer(sheet_w, sheet_h)
    for y in range(sheet_h):
        for x in range(sheet_w):
            new_layer1.data[y][x] = (_encode_digit(0), (255, 255, 255), (0, 0, 0))
    out_xp.layers.append(new_layer1)

    # Layer 2: fill with transparent, then place rider cells
    new_layer2 = XPLayer(sheet_w, sheet_h)
    for y in range(sheet_h):
        for x in range(sheet_w):
            new_layer2.data[y][x] = TRANSPARENT_CELL
    out_xp.layers.append(new_layer2)

    # Place rider cells for each angle, both projections, all anim frames
    # The XP visual layer stores frames with y=0 of logical frame at the BOTTOM of the strip.
    # Historical XP writer convention: raw_y = y0 + (frame_h - 1 - ly).
    # So cy=0 (top of person) -> raw_y = sheet_row + (frame_h - 1) (bottom of frame strip)
    cells_placed = 0
    warnings = 0
    for angle in range(angles):
        rider_cells = rider_by_angle.get(angle, [])
        if not rider_cells:
            print(f"  WARNING: no rider cells for angle {angle}")
            continue

        for proj in range(projs):
            proj_col_offset = proj * anim_count  # offset in frame units

            for anim_f in range(anim_count):
                # Frame origin in sheet coordinates
                sheet_col = (proj_col_offset + anim_f) * frame_w
                sheet_row = angle * frame_h

                for cell in rider_cells:
                    cx, cy = cell["x"], cell["y"]
                    raw_y = sheet_row + (frame_h - 1 - cy)
                    raw_x = sheet_col + cx

                    if 0 <= raw_y < sheet_h and 0 <= raw_x < sheet_w:
                        new_layer2.data[raw_y][raw_x] = (cell["glyph"], cell["fg"], cell["bg"])
                        cells_placed += 1
                    else:
                        print(f"  WARNING: cell ({cx},{cy}) -> ({raw_x},{raw_y}) out of bounds "
                              f"for angle={angle} proj={proj} anim_f={anim_f}")
                        warnings += 1

    print(f"Total cells placed in layer 2: {cells_placed}")
    if warnings:
        print(f"Total out-of-bounds warnings: {warnings}")

    rebase_visual_layer_transparency_keys(new_layer2, None, new_layer0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_xp.save(str(output_path))
    print(f"Saved to {output_path}")


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        default=SPRITES_DIR / "wolack-attack-body.xp",
        help="Output path for the generated body XP (default: assets/sprites/wolack-attack-body.xp)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate inputs and print what would be written without creating files.",
    )
    args = parser.parse_args()
    if args.dry_run:
        rider_by_angle = load_rider_cells_by_angle(ANCHOR_PATH)
        total = sum(len(cells) for cells in rider_by_angle.values())
        print(f"[dry-run] Would write {args.output}")
        print(f"[dry-run] Rider cells: {total} across {len(rider_by_angle)} angles")
        return 0
    build_wolack_attack_body(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
