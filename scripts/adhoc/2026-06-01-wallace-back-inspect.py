#!/usr/bin/env python3
"""Inspect Wallace back-view (sr=4 = facing-away/north) head zone.
Print head-zone cells for all 18 frames so we can see where 'black' is."""
import sys
from pathlib import Path

Y9 = Path("/Users/r/Downloads/asciicker-Y9-2")
sys.path.insert(0, str(Y9 / "scripts" / "pipeline"))
from xp_core import XPFile

XP = Y9 / "assets/sprites/2026-05-28-wallace.xp"
SPRITE_W = 7
SPRITE_H = 9
HEAD_LY_MAX = 2  # ly 0..2 = head zone
BLACK = (0, 0, 0)

xp = XPFile()
xp.load(str(XP))
l = xp.layers[2]
print(f"layer 2: {l.width}x{l.height}")

# Examine all sprite rows
for sr in range(8):
    label = {0:'front',1:'frnt-R',2:'side-R',3:'rear-R',4:'BACK',5:'rear-L',6:'side-L',7:'frnt-L'}.get(sr, '?')
    print(f"\n=== sr={sr} ({label})  ay={sr*SPRITE_H}..{sr*SPRITE_H+SPRITE_H-1} ===")
    # Count BLACK bg cells in head zone across all frames in this row
    n_black_bg = 0
    n_black_fg = 0
    samples = []
    for sc in range(18):
        for ly in range(HEAD_LY_MAX + 1):
            for lx in range(SPRITE_W):
                ax = sc * SPRITE_W + lx
                ay = sr * SPRITE_H + ly
                g, fg, bg = l.data[ay][ax]
                if bg == BLACK:
                    n_black_bg += 1
                    if len(samples) < 4:
                        samples.append((sc, lx, ly, g, fg, bg))
                if fg == BLACK and bg != BLACK:
                    n_black_fg += 1
    print(f"  black bg cells in head: {n_black_bg}, black fg (non-bg): {n_black_fg}")
    if samples:
        print(f"  sample black-bg cells (sc,lx,ly,g,fg,bg):")
        for s in samples:
            print(f"    {s}")

# Now dump the actual back-view first frame head zone
print("\n=== sr=4 sc=0 head zone dump ===")
for ly in range(SPRITE_H):
    row = []
    for lx in range(SPRITE_W):
        ax = 0 * SPRITE_W + lx
        ay = 4 * SPRITE_H + ly
        g, fg, bg = l.data[ay][ax]
        row.append(f"g={g:3d} fg={fg} bg={bg}")
    print(f"ly={ly}: " + " | ".join(row))
