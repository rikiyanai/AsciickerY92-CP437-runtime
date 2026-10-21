#!/usr/bin/env python3
"""generate_plydie_mount_md.py

Generates placeholder MD (Mounted Death) stamp XP files for mounted plydie
animations.  These are production sprite assets — not 4TEST fixtures.

The original game had no mounted death sprites; these "MD" stamps satisfy the
bundle compiler's coverage contract so the selector-reachable mounted plydie
state space compiles without rejection.

Generated files (in assets/sprites/ by default):
    WOLF_MOUNTABLE_PLYDIE_MD.xp          parity reference (full mount)
    WOLF_MOUNTABLE_PLYDIE_MD-rear.xp     rear surface (behind rider)
    WOLF_MOUNTABLE_PLYDIE_MD-front.xp    front surface (in front of rider)
    BEE_MOUNTABLE_PLYDIE_MD.xp           parity reference
    BEE_MOUNTABLE_PLYDIE_MD-rear.xp      rear surface
    BEE_MOUNTABLE_PLYDIE_MD-front.xp     front surface

Contract: plydie_mount / plydie_mount
    angles=8, projs=2, anims=[5], anchor_mode=mount_character
    frame: 13x13  (wolf and bee share same frame geometry for MD stamps)
    row1: [54, 54]  (copied from plydie-body.xp — proj_ref_y=6, refl_ref_y=6)
    row2: [53, 70]  (copied from plydie-body.xp — proj_ref_z=5, refl_ref_z=15)

Usage:
    python3 scripts/pipeline/generate_plydie_mount_md.py [--out-dir DIR] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.xp_core import XPFile, XPLayer, rebase_visual_layer_transparency_keys

# ---------------------------------------------------------------------------
# Contract constants — match plydie-body.xp seam anchor exactly
# ---------------------------------------------------------------------------

ANGLES = 8
PROJS = 2
ANIMS = [5]
FRAME_W = 13
FRAME_H = 13

# row1/row2 copied from plydie-body.xp layer0 metadata
# row1: ord('6')=54, ord('6')=54  → proj_ref_y=6, refl_ref_y=6
# row2: ord('5')=53, ord('F')=70  → proj_ref_z=5, refl_ref_z=15
PLYDIE_ROW1: List[int] = [54, 54]
PLYDIE_ROW2: List[int] = [53, 70]

ANGLE_NAMES: List[str] = ["S", "SW", "W", "NW", "N", "NE", "E", "SE"]

# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------

from scripts.pipeline.xp_core import OVERLAY_KEY_RGB as KEY_RGB
TRANSPARENT_BG: Tuple[int, int, int] = (255, 0, 255)
WHITE: Tuple[int, int, int] = (255, 255, 255)
BLACK: Tuple[int, int, int] = (0, 0, 0)
WOLF_BG: Tuple[int, int, int] = (96, 64, 32)
WOLF_FG: Tuple[int, int, int] = (255, 238, 204)
BEE_BG: Tuple[int, int, int] = (204, 170, 0)
BEE_FG: Tuple[int, int, int] = (32, 24, 0)

SPACE = 32

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

from scripts.pipeline.xp_core import encode_digit


def make_layer0(sheet_w: int, sheet_h: int) -> XPLayer:
    """Build layer0 metadata using plydie_mount contract."""
    layer = XPLayer(sheet_w, sheet_h)
    for y in range(sheet_h):
        for x in range(sheet_w):
            layer.data[y][x] = (SPACE, WHITE, KEY_RGB)

    # angle count
    layer.data[0][0] = (encode_digit(ANGLES), WHITE, KEY_RGB)
    # animation frame counts (anims=[5] → one entry)
    layer.data[0][1] = (encode_digit(ANIMS[0]), WHITE, KEY_RGB)
    # projection-offset refs
    for col, glyph in enumerate(PLYDIE_ROW1):
        layer.data[1][col] = (glyph, WHITE, KEY_RGB)
    for col, glyph in enumerate(PLYDIE_ROW2):
        layer.data[2][col] = (glyph, WHITE, KEY_RGB)

    return layer


def make_layer1(sheet_w: int, sheet_h: int) -> XPLayer:
    """Build layer1 depth map (constant depth=0 for placeholder)."""
    layer = XPLayer(sheet_w, sheet_h)
    zero = encode_digit(0)
    for y in range(sheet_h):
        for x in range(sheet_w):
            layer.data[y][x] = (zero, WHITE, BLACK)
    return layer


def _draw_label_row(
    layer: XPLayer,
    x0: int,
    row_y: int,
    text: str,
    fg: Tuple[int, int, int],
    bg: Tuple[int, int, int],
) -> None:
    """Write centered text into one row of a frame."""
    start = max(0, (FRAME_W - len(text)) // 2)
    for fx in range(FRAME_W):
        col = x0 + fx
        i = fx - start
        if 0 <= i < len(text):
            layer.data[row_y][col] = (ord(text[i]), fg, bg)
        else:
            layer.data[row_y][col] = (SPACE, fg, bg)


def draw_md_parity_frame(
    layer: XPLayer,
    x0: int,
    y0: int,
    direction: str,
    frame_idx: int,
    fill_bg: Tuple[int, int, int],
    fill_fg: Tuple[int, int, int],
) -> None:
    """Full-frame suit with 'MD' center label and direction/frame tiling.

    Every body cell tiles [height][frame][dir1][dir2] across columns so the
    reviewer can verify angle selection and frame progression.
    Center rows carry the 'MD' stamp (white-on-black) and an F# frame label.
    """
    d1 = ord(direction[0])
    d2 = ord(direction[1]) if len(direction) > 1 else SPACE
    f_glyph = encode_digit(frame_idx)
    label_center = (FRAME_H - 1) // 2

    for fy in range(FRAME_H):
        if fy == label_center - 1:
            _draw_label_row(layer, x0, y0 + fy, "MD", WHITE, BLACK)
        elif fy == label_center:
            frame_text = f"F{chr(encode_digit(frame_idx))}" if FRAME_W >= 3 else chr(encode_digit(frame_idx))
            _draw_label_row(layer, x0, y0 + fy, frame_text, WHITE, BLACK)
        else:
            h_glyph = encode_digit(fy)
            pattern = [h_glyph, f_glyph, d1, d2]
            for fx in range(FRAME_W):
                layer.data[y0 + fy][x0 + fx] = (pattern[fx % 4], fill_fg, fill_bg)


def draw_md_rear_frame(
    layer: XPLayer,
    x0: int,
    y0: int,
    direction: str,
    frame_idx: int,
    fill_bg: Tuple[int, int, int],
    fill_fg: Tuple[int, int, int],
) -> None:
    """Rear surface: left 2/3 of frame visible (rear mount body behind rider),
    right 1/3 transparent (rider occupies this zone at runtime).

    MD label and frame tiling appear only in the visible region.
    """
    split_x = (FRAME_W * 2) // 3  # cols 0..split_x-1 visible, rest transparent
    d1 = ord(direction[0])
    d2 = ord(direction[1]) if len(direction) > 1 else SPACE
    f_glyph = encode_digit(frame_idx)
    label_center = (FRAME_H - 1) // 2

    for fy in range(FRAME_H):
        for fx in range(FRAME_W):
            col = x0 + fx
            if fx >= split_x:
                # rider overlap zone → transparent
                layer.data[y0 + fy][col] = (SPACE, BLACK, TRANSPARENT_BG)
            elif fy == label_center - 1:
                # "MD" label centered within visible region
                label = "MD"
                start = max(0, (split_x - len(label)) // 2)
                i = fx - start
                if 0 <= i < len(label):
                    layer.data[y0 + fy][col] = (ord(label[i]), WHITE, BLACK)
                else:
                    layer.data[y0 + fy][col] = (SPACE, WHITE, BLACK)
            elif fy == label_center:
                frame_text = f"F{chr(encode_digit(frame_idx))}"
                start = max(0, (split_x - len(frame_text)) // 2)
                i = fx - start
                if 0 <= i < len(frame_text):
                    layer.data[y0 + fy][col] = (ord(frame_text[i]), WHITE, BLACK)
                else:
                    layer.data[y0 + fy][col] = (SPACE, WHITE, BLACK)
            else:
                h_glyph = encode_digit(fy)
                pattern = [h_glyph, f_glyph, d1, d2]
                layer.data[y0 + fy][col] = (pattern[fx % 4], fill_fg, fill_bg)


def draw_transparent_frame(layer: XPLayer, x0: int, y0: int) -> None:
    """Front surface: entirely transparent (no art in front of rider for MD stamps)."""
    for fy in range(FRAME_H):
        for fx in range(FRAME_W):
            layer.data[y0 + fy][x0 + fx] = (SPACE, BLACK, TRANSPARENT_BG)


# ---------------------------------------------------------------------------
# XP file builders
# ---------------------------------------------------------------------------

def build_md_xp(
    surface: str,  # "parity" | "rear" | "front"
    fill_bg: Tuple[int, int, int],
    fill_fg: Tuple[int, int, int],
) -> XPFile:
    """Build a 3-layer XPFile for one MD stamp surface."""
    anim_sum = sum(ANIMS)
    fr_num_x = PROJS * anim_sum   # 2 * 5 = 10
    fr_num_y = ANGLES             # 8
    sheet_w = FRAME_W * fr_num_x  # 13 * 10 = 130
    sheet_h = FRAME_H * fr_num_y  # 13 * 8  = 104

    layer0 = make_layer0(sheet_w, sheet_h)
    layer1 = make_layer1(sheet_w, sheet_h)

    layer2 = XPLayer(sheet_w, sheet_h)
    for y in range(sheet_h):
        for x in range(sheet_w):
            layer2.data[y][x] = (SPACE, BLACK, TRANSPARENT_BG)

    if surface != "front":
        for angle_idx, direction in enumerate(ANGLE_NAMES):
            y0 = angle_idx * FRAME_H
            for frame_col in range(fr_num_x):
                x0 = frame_col * FRAME_W
                frame_idx = frame_col % anim_sum
                if surface == "parity":
                    draw_md_parity_frame(layer2, x0, y0, direction, frame_idx,
                                         fill_bg, fill_fg)
                elif surface == "rear":
                    draw_md_rear_frame(layer2, x0, y0, direction, frame_idx,
                                       fill_bg, fill_fg)
    # front: layer2 stays all-transparent (initialized above)

    xp = XPFile()
    xp.version = -1
    xp.layers = [layer0, layer1, layer2]
    rebase_visual_layer_transparency_keys(layer2, None, layer0)
    return xp


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

TARGETS = [
    ("WOLF_MOUNTABLE_PLYDIE_MD",        "parity", WOLF_BG, WOLF_FG),
    ("WOLF_MOUNTABLE_PLYDIE_MD-rear",   "rear",   WOLF_BG, WOLF_FG),
    ("WOLF_MOUNTABLE_PLYDIE_MD-front",  "front",  WOLF_BG, WOLF_FG),
    ("BEE_MOUNTABLE_PLYDIE_MD",         "parity", BEE_BG,  BEE_FG),
    ("BEE_MOUNTABLE_PLYDIE_MD-rear",    "rear",   BEE_BG,  BEE_FG),
    ("BEE_MOUNTABLE_PLYDIE_MD-front",   "front",  BEE_BG,  BEE_FG),
    ("WOLACK_MOUNTABLE_PLYDIE_MD",      "parity", WOLF_BG, WOLF_FG),
    ("WOLACK_MOUNTABLE_PLYDIE_MD-rear", "rear",   WOLF_BG, WOLF_FG),
    ("WOLACK_MOUNTABLE_PLYDIE_MD-front","front",  WOLF_BG, WOLF_FG),
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate MD stamp XP files for mounted plydie sprites."
    )
    parser.add_argument(
        "--out-dir", type=Path,
        default=REPO_ROOT / "assets" / "sprites",
        help="Output directory (default: assets/sprites/)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be generated without writing files.",
    )
    args = parser.parse_args()

    anim_sum = sum(ANIMS)
    sw = FRAME_W * PROJS * anim_sum
    sh = FRAME_H * ANGLES

    header = f"{'File':<45} {'sheet':<12} {'surface'}"
    print(header)
    print("-" * 70)

    for name, surface, fill_bg, fill_fg in TARGETS:
        row = f"{name + '.xp':<45} {sw}x{sh:<8} {surface}"
        print(row)

        if not args.dry_run:
            out_path = args.out_dir / f"{name}.xp"
            xp = build_md_xp(surface, fill_bg, fill_fg)
            xp.save(str(out_path))

    if args.dry_run:
        print("\n(dry-run — no files written)")
    else:
        print(f"\nWrote {len(TARGETS)} MD stamp files to {args.out_dir}/")


if __name__ == "__main__":
    main()
