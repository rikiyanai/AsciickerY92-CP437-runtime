#!/usr/bin/env python3
"""Gromit head v6 — sr=1..7 stepped ears + button nose on face-visible angles.

Same v5-style stepped ear pattern as sr=0:
  ly=1: BROWN tips, 1-cell transparent gap between
  ly=2: BROWN flanks (outer = MAG bg outside silhouette, inner = WARM bg inside),
        cream head-top in the gap

Button nose = single BLACK full block (219, BLACK, WARM) at the snout position
on sr=1, 2, 6, 7 (¾ and side views where the nose is visible).
sr=3, 4, 5 = rear angles, no nose.
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
N_FRAMES = 18

EAR_PLAN = {
    1: {'old': (5, 6), 'tips': (4, 6), 'outer': (3, 7), 'gap': 5},   # frnt-R
    2: {'old': (6, 7), 'tips': (5, 7), 'outer': (4, 8), 'gap': 6},   # side-R
    3: {'old': (5, 6), 'tips': (4, 6), 'outer': (3, 7), 'gap': 5},   # rear-R
    4: {'old': (4, 5), 'tips': (3, 5), 'outer': (2, 6), 'gap': 4},   # BACK
    5: {'old': (3, 4), 'tips': (3, 5), 'outer': (2, 6), 'gap': 4},   # rear-L
    6: {'old': (2, 3), 'tips': (2, 4), 'outer': (1, 5), 'gap': 3},   # side-L
    7: {'old': (3, 4), 'tips': (3, 5), 'outer': (2, 6), 'gap': 4},   # frnt-L
}

BUTTON_NOSE = {
    1: (4, 5),   # frnt-R — snout at ly=5 col 4 (replace existing 176 light-shade)
    2: (9, 4),   # side-R — snout tip at ly=4 col 9
    3: None,
    4: None,
    5: None,
    6: (0, 4),   # side-L — snout tip at ly=4 col 0
    7: (5, 5),   # frnt-L — snout at ly=5 col 5
}


def apply_ear_plan(layer, sr, plan):
    tip_L, tip_R = plan['tips']
    out_L, out_R = plan['outer']
    gap = plan['gap']
    old_L, old_R = plan['old']
    new_cols = {tip_L, tip_R, out_L, out_R, gap}

    n = 0
    for sc in range(N_FRAMES):
        bx, by = sc * SPRITE_W, sr * SPRITE_H

        def put(lx, ly, cell):
            nonlocal n
            layer.data[by + ly][bx + lx] = cell
            n += 1

        # ── ly=1: tips + transparent gap, clear stale OLD cells ──
        put(tip_L, 1, (219, BROWN, MAG))
        put(tip_R, 1, (219, BROWN, MAG))
        put(gap, 1, (32, BROWN, MAG))  # transparent gap between tips
        for old_col in (old_L, old_R):
            if old_col not in new_cols:
                put(old_col, 1, (32, BROWN, MAG))

        # ── ly=2: stepped bottoms ──
        put(out_L, 2, (219, BROWN, MAG))   # outer flank (outside silhouette)
        put(out_R, 2, (219, BROWN, MAG))
        put(tip_L, 2, (219, BROWN, WARM))  # inner bottom (inside silhouette)
        put(tip_R, 2, (219, BROWN, WARM))
        put(gap, 2, (32, WARM, WARM))      # cream head-top in the gap
        for old_col in (old_L, old_R):
            if old_col not in new_cols:
                put(old_col, 2, (32, BROWN, MAG))

    return n


def apply_button_nose(layer, sr, pos):
    if pos is None:
        return 0
    lx, ly = pos
    n = 0
    for sc in range(N_FRAMES):
        bx, by = sc * SPRITE_W, sr * SPRITE_H
        layer.data[by + ly][bx + lx] = (219, BLACK, WARM)
        n += 1
    return n


def main():
    if not TGT.exists():
        sys.exit(f"missing {TGT}")
    xp = XPFile()
    xp.load(str(TGT))
    layer = xp.layers[2]

    total_ears = 0
    total_nose = 0
    for sr in range(1, 8):
        total_ears += apply_ear_plan(layer, sr, EAR_PLAN[sr])
        total_nose += apply_button_nose(layer, sr, BUTTON_NOSE[sr])

    xp.save(str(TGT))
    print(f"gromit head v6 (sr=1..7): ears={total_ears} cells, buttons={total_nose} cells")


if __name__ == "__main__":
    main()
