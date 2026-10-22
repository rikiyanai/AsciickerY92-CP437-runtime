#!/usr/bin/env python3
# =============================================================================
# make_walk_gifs_from_sheet.py  --  FL-4547: turn the 5x7 walk MASTER SHEET
#                                    (a static contact-sheet PNG) into animated
#                                    walking GIFs.
# =============================================================================
# WHAT THIS DOES (plain language)
# -------------------------------
# The renderer `sheet_unifont_5x7.py` produced one big "contact sheet" image:
# an 8x8 grid of tiny ASCII figures. Each ROW is a facing direction (South,
# South-West, ... South-East) and each COLUMN is a moment in the walk cycle
# (frames f0, f2, f4, f7, f9, f12, f14, f17). On its own that sheet is frozen.
# This script cuts the sheet back into its 64 little pictures and plays the 8
# frames of each direction in sequence, so the figure actually WALKS. It writes:
#   * one animated GIF per direction (S_walk.gif, SW_walk.gif, ...)
#   * one combined GIF showing ALL eight directions at once (2 rows x 4 cols),
#     every mini-figure stepping in lockstep -- the headline artifact.
#
# WHY SLICE THE SHEET INSTEAD OF THE ON-DISK FRAMES?
# The operator handed us this exact sheet PNG. Slicing it guarantees the GIF
# matches precisely what they are looking at, regardless of which render
# (100-iter vs 400-iter cloud regen) the loose per-frame PNGs on disk came from.
#
# GEOMETRY (exact, verified): the sheet is 1800x2584, which is exactly the
# layout that sheet_unifont_5x7.py emits:
#   per-sprite cell  fw x fh = 216 x 296   (5 cols*40 + 16 pad) x (7 rows*40 + 16)
#   gap = 8   label strip lab = 18   outer margin = 8
#   cell (row=di, col=ci): label text at (8 + ci*224, 8 + di*322)
#                          SPRITE pasted at (8 + ci*224, 8 + di*322 + 18)
# So the sprite pixels for (di, ci) are the box:
#   left   = 8 + ci*224
#   top    = 8 + di*322 + 18
#   right  = left + 216
#   bottom = top  + 296
# =============================================================================
from __future__ import annotations
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ---- inputs / outputs -------------------------------------------------------
# The operator-provided source and its reproducible outputs are one tracked
# research package. The script resolves them from the repository root so cwd
# cannot become a second input-path owner.
REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs/research/ascii/verification/fl4547/2026-07-16-walk-gifs-from-sheet"
SHEET = OUT / "_source_master_sheet.png"
OUT.mkdir(parents=True, exist_ok=True)

# ---- grid definition (must match the sheet's row/column order) --------------
# Rows top->bottom are these 8 compass facings; columns left->right are these
# 8 walk-cycle keyframes. (Same order gen_walk_unifont / sheet_unifont_5x7 use.)
DIRS = ["S", "SW", "W", "NW", "N", "NE", "E", "SE"]
FRAMES = ["f0", "f2", "f4", "f7", "f9", "f12", "f14", "f17"]

# ---- exact cell geometry constants (see header) -----------------------------
FW, FH = 216, 296          # sprite width/height in pixels
GAP = 8                    # gap between columns
LAB = 18                   # label strip height above each sprite
MARGIN = 8                 # outer margin of the whole sheet
COL_PITCH = FW + GAP       # 224 px from one column's left edge to the next
ROW_PITCH = FH + GAP + LAB # 322 px from one row's top to the next

FRAME_MS = 130             # per-frame duration in the output GIFs (ms)


def cell_box(di: int, ci: int):
    """Return the (left, top, right, bottom) crop box of the SPRITE image for
    direction-row `di` and frame-column `ci`, using the exact layout formula."""
    left = MARGIN + ci * COL_PITCH               # 8 + ci*224
    top = MARGIN + di * ROW_PITCH + LAB          # 8 + di*322 + 18  (skip label)
    return (left, top, left + FW, top + FH)


def slice_all(sheet: Image.Image):
    """Cut the sheet into a dict: cells[direction][frame_index] = PIL image."""
    cells = {}
    for di, d in enumerate(DIRS):
        row = []
        for ci in range(len(FRAMES)):
            row.append(sheet.crop(cell_box(di, ci)))
        cells[d] = row
    return cells


def per_direction_gifs(cells):
    """Write one looping walk GIF per direction (full resolution)."""
    for d in DIRS:
        frames = cells[d]
        frames[0].save(OUT / f"{d}_walk.gif", save_all=True,
                       append_images=frames[1:], duration=FRAME_MS, loop=0,
                       disposal=2)  # disposal=2 = clear to background each frame
    print(f"wrote {len(DIRS)} per-direction GIFs -> {OUT}")


def combined_grid_gif(cells, scale=0.6):
    """Write ONE GIF showing all 8 directions in a 2-row x 4-col grid, every
    mini-figure stepping in lockstep. This is the headline artifact.

    `scale` shrinks each cell so the combined GIF stays a sensible file size."""
    cw, ch = int(FW * scale), int(FH * scale)    # scaled cell size
    cols, rows = 4, 2                             # 4 across, 2 down = 8 dirs
    labh = 16                                     # small label strip per cell
    pad = 6
    # Total canvas size for one animation frame of the grid.
    W = pad + cols * (cw + pad)
    H = pad + rows * (ch + labh + pad)
    font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 12)

    out_frames = []
    for fi in range(len(FRAMES)):                 # one grid image per walk frame
        canvas = Image.new("RGB", (W, H), (8, 8, 8))
        draw = ImageDraw.Draw(canvas)
        for idx, d in enumerate(DIRS):            # place all 8 dirs in the grid
            gc, gr = idx % cols, idx // cols      # grid column / row for this dir
            x = pad + gc * (cw + pad)
            y = pad + gr * (ch + labh + pad)
            # label like "S f7" so the viewer can read direction + frame
            draw.text((x, y), f"{d} {FRAMES[fi]}", fill=(150, 210, 150), font=font)
            cell = cells[d][fi].resize((cw, ch), Image.NEAREST)  # NEAREST keeps glyph edges crisp
            canvas.paste(cell, (x, y + labh))
        out_frames.append(canvas)

    out_frames[0].save(OUT / "walk_all_directions.gif", save_all=True,
                       append_images=out_frames[1:], duration=FRAME_MS, loop=0,
                       disposal=2)
    print(f"wrote combined grid GIF ({W}x{H}) -> {OUT / 'walk_all_directions.gif'}")


def main():
    sheet = Image.open(SHEET).convert("RGB")
    assert sheet.size == (1800, 2584), f"unexpected sheet size {sheet.size}"
    cells = slice_all(sheet)
    per_direction_gifs(cells)
    combined_grid_gif(cells)
    print("DONE")


if __name__ == "__main__":
    main()
