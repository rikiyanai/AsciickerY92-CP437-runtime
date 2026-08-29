#!/usr/bin/env python3
"""Gromit head v2 pass:
  1) r0c0 (sr=0 sc=0) — move nose block from ly=4 to ly=5; ly=4 becomes cream.
  2) Globally — replace ALL remaining ▲ (g=30) and ▼ (g=31) on layer 2 with
     a clean cream cell (g=32, fg=cream, bg=cream); preserves silhouette.
  3) Globally — make ears 2-cell tall: for every (219 BROWN/cream) ear cell
     found on layer 2, add a matching (219 BROWN/MAGENTA) cell at ly-1 so the
     ear extends one cell up out of the head silhouette. MAGENTA bg keeps the
     cell outside the cream silhouette (the brown shows as the ear-top).
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "pipeline"))
from xp_core import XPFile

TGT = REPO / "assets" / "sprites" / "2026-05-28-gromit.xp"

WARM_CREAM = (0xFF, 0xE4, 0xB5)
BROWN      = (0xAA, 0x55, 0x00)
BLACK      = (0, 0, 0)
DARK_SNOUT = (0x3C, 0x28, 0x18)
MAGENTA    = (0xFF, 0x00, 0xFF)

SPRITE_W = 10
SPRITE_H = 12


def move_r0c0_nose(layer):
    """Move nose (219 DARK_SNOUT/cream) cells from ly=4 to ly=5 in sr=0 sc=0."""
    n = 0
    for lx in range(SPRITE_W):
        ax = 0 * SPRITE_W + lx
        ay4 = 0 * SPRITE_H + 4
        ay5 = 0 * SPRITE_H + 5
        g4, fg4, bg4 = layer.data[ay4][ax]
        if g4 == 219 and fg4 == DARK_SNOUT:
            layer.data[ay4][ax] = (32, WARM_CREAM, WARM_CREAM)
            layer.data[ay5][ax] = (219, DARK_SNOUT, WARM_CREAM)
            n += 1
    return n


def kill_triangles(layer):
    n = 0
    for ay in range(layer.height):
        for ax in range(layer.width):
            g, fg, bg = layer.data[ay][ax]
            if g in (30, 31):
                new_bg = WARM_CREAM if bg != MAGENTA else MAGENTA
                layer.data[ay][ax] = (32, WARM_CREAM, new_bg)
                n += 1
    return n


def grow_ears(layer):
    """For every existing ear cell (219 BROWN/cream) on layer 2, ensure the
    cell directly above (ay-1) is also a BROWN block. If that upper cell is
    currently transparent or cream, paint it BROWN on MAGENTA (sits outside
    silhouette as the new ear-top)."""
    n = 0
    H, W = layer.height, layer.width
    # Snapshot brown-ear positions BEFORE growing, so the grown-cell isn't
    # re-grown again on a second pass.
    seeds = []
    for ay in range(H):
        for ax in range(W):
            g, fg, bg = layer.data[ay][ax]
            if g == 219 and fg == BROWN and bg == WARM_CREAM:
                seeds.append((ax, ay))
    for ax, ay in seeds:
        if ay == 0:
            continue
        ay_up = ay - 1
        g_up, fg_up, bg_up = layer.data[ay_up][ax]
        if g_up == 219 and fg_up == BROWN:
            continue  # already brown above
        new_bg = MAGENTA if bg_up == MAGENTA else WARM_CREAM
        layer.data[ay_up][ax] = (219, BROWN, new_bg)
        n += 1
    return n


def main():
    if not TGT.exists():
        sys.exit(f"missing {TGT}")
    xp = XPFile()
    xp.load(str(TGT))
    layer = xp.layers[2]
    a = move_r0c0_nose(layer)
    b = kill_triangles(layer)
    c = grow_ears(layer)
    xp.save(str(TGT))
    print(f"gromit head v2:  r0c0_nose_moved={a}  triangles_killed={b}  ears_grown={c}")


if __name__ == "__main__":
    main()
