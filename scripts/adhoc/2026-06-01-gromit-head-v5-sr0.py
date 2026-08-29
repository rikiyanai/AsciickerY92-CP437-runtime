#!/usr/bin/env python3
"""Gromit head v5 — sr=0 all 18 frames, consistent bottom-heavy face.

Layout (per sprite cell, head zone ly=0..6, 10 cols):
  ly=0: . . . . . . . . . .                  (clear, transparent)
  ly=1: . . . B . . . B . .                  tips at cols 3, 7
  ly=2: . . B B . . . B B .                  ear bottoms step-outward at cols 2,3 / 7,8;
                                              cream face top between at cols 4,5,6
  ly=3: . . . . _ _ _ . . .                  narrow head top (3 cream cells, cols 4-6)
  ly=4: . . . _ K _ K _ . .                  face widens to 5 (cols 3-7);
                                              eyes at cols 4, 6 with 1-cell cream gap at col 5
  ly=5: . . _ _ _ _ _ _ _ .                  widest face: 7 cream cells (cols 2-8)
  ly=6: . . _ _ N N _ _ _ .                  7-wide bottom with full-block nose at cols 4,5

All 18 frames in sr=0 get the same head (no bob). Leaves ly=7+ body alone.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "pipeline"))
from xp_core import XPFile

TGT = REPO / "assets" / "sprites" / "2026-05-28-gromit.xp"

WARM       = (0xFF, 0xE4, 0xB5)
BROWN      = (0xAA, 0x55, 0x00)
BLACK      = (0, 0, 0)
DARK_SNOUT = (0x3C, 0x28, 0x18)
MAG        = (0xFF, 0x00, 0xFF)

SPRITE_W = 10
SPRITE_H = 12

# Per-row painting plan. None = leave the cell alone.
# 'T' = magenta-transparent (32, BROWN, MAG)
# 'C' = cream face         (32, WARM, WARM)
# 'B' = brown ear          (219, BROWN, ...)  bg depends on whether cell is inside silhouette
# 'K' = black eye          (219, BLACK, WARM)
# 'N' = dark snout         (219, DARK_SNOUT, WARM)
# Each row uses 10 chars, one per col.
PLAN = {
    0: "TTTTTTTTTT",        # clear top
    1: "TTTBTTTBTT",        # ear tips at cols 3, 7
    2: "TTBBCCCBBT",        # ear bottoms + cream top between ears
    3: "TTTTCCCTTT",        # narrow head top (3 cream at cols 4-6)
    4: "TTTCKCKCTT",        # face 5-wide, eyes at cols 4, 6, gap at col 5
    5: "TTCCCCCCCT",        # widest cream (cols 2-8)
    6: "TTCCNNCCCT",        # nose row, nose at cols 4-5
}


def cell_for(symbol, ly):
    if symbol == 'T':
        return (32, BROWN, MAG)
    if symbol == 'C':
        return (32, WARM, WARM)
    if symbol == 'B':
        # ear cell: bg = WARM if it's inside the silhouette (i.e., the cell to
        # the right or left at this row is cream); otherwise MAG. Simple rule:
        # the "inner" ear cells (at cols where neighbour is C in PLAN) → cream;
        # the "outer" ear cells (neighbour is T) → magenta.
        # For our PLAN this works out:
        #   ly=1: cols 3,7 always have T on both sides → MAG
        #   ly=2: col 2 (left outer, T on left) → MAG; col 3 (cream on right) → WARM;
        #         col 7 (cream on left) → WARM; col 8 (T on right) → MAG
        return None  # handled with explicit bg lookup
    if symbol == 'K':
        return (219, BLACK, WARM)
    if symbol == 'N':
        return (219, DARK_SNOUT, WARM)
    raise ValueError(symbol)


def ear_bg_for(plan_row, lx):
    # Look at neighbours in the same plan row
    left = plan_row[lx - 1] if lx > 0 else 'T'
    right = plan_row[lx + 1] if lx < len(plan_row) - 1 else 'T'
    if left == 'C' or right == 'C':
        return WARM
    return MAG


def apply_sr0(layer):
    SR = 0
    base_y = SR * SPRITE_H
    n = 0
    for sc in range(18):
        base_x = sc * SPRITE_W
        for ly, plan_row in PLAN.items():
            for lx, sym in enumerate(plan_row):
                if sym == 'B':
                    bg = ear_bg_for(plan_row, lx)
                    cell = (219, BROWN, bg)
                else:
                    cell = cell_for(sym, ly)
                layer.data[base_y + ly][base_x + lx] = cell
                n += 1
    return n


def main():
    if not TGT.exists():
        sys.exit(f"missing {TGT}")
    xp = XPFile()
    xp.load(str(TGT))
    n = apply_sr0(xp.layers[2])
    xp.save(str(TGT))
    print(f"gromit head v5 (sr=0 all 18 frames): {n} cells written")


if __name__ == "__main__":
    main()
