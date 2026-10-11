#!/usr/bin/env python3
"""Gromit head v3 — sr=0 sc=0 (front, frame 0):
  - Stepped ears at corners:
      ly=1: brown TIP at cols 3, 6 (was unwanted "grown" ears at cols 4,5)
      ly=2: brown BOTTOM at cols 3, 6 + outward STEP at cols 2, 7
            cream face at cols 4, 5 (was brown ears)
  - Eyes pushed apart with cream gap:
      ly=3 col 3 / col 6 = BLACK eyes; cols 4, 5 = cream gap
      (previously eyes were adjacent at cols 4, 5 = one black bar)
  - Nose dropped to ly=6:
      ly=5: cream face (was nose)
      ly=6: dark snout block at cols 4, 5
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


def patch(xp):
    layer = xp.layers[2]
    sr, sc = 0, 0
    base_x = sc * SPRITE_W
    base_y = sr * SPRITE_H

    def put(lx, ly, cell):
        layer.data[base_y + ly][base_x + lx] = cell

    # --- ears: clear old, draw new stepped corner ears ---
    put(4, 1, (32, BROWN, MAG))       # clear grown ear
    put(5, 1, (32, BROWN, MAG))
    put(4, 2, (32, WARM, WARM))       # original ear -> cream face top
    put(5, 2, (32, WARM, WARM))

    put(3, 1, (219, BROWN, MAG))      # left ear TOP (corner, sits outside silhouette)
    put(6, 1, (219, BROWN, MAG))      # right ear TOP
    put(2, 2, (219, BROWN, MAG))      # left ear outward STEP at col 2 (outside silhouette)
    put(3, 2, (219, BROWN, WARM))     # left ear bottom inside silhouette (cream bg)
    put(6, 2, (219, BROWN, WARM))     # right ear bottom
    put(7, 2, (219, BROWN, MAG))      # right ear outward STEP at col 7

    # --- eyes: spread to corners with cream gap ---
    put(3, 3, (219, BLACK, WARM))     # left eye at cheek
    put(4, 3, (32, WARM, WARM))       # cream gap (was eye)
    put(5, 3, (32, WARM, WARM))       # cream gap (was eye)
    put(6, 3, (219, BLACK, WARM))     # right eye at cheek

    # --- nose: drop from ly=5 to ly=6 ---
    put(4, 5, (32, WARM, WARM))       # clear nose at ly=5
    put(5, 5, (32, WARM, WARM))
    put(4, 6, (219, DARK_SNOUT, WARM))  # nose now at ly=6
    put(5, 6, (219, DARK_SNOUT, WARM))


def main():
    if not TGT.exists():
        sys.exit(f"missing {TGT}")
    xp = XPFile()
    xp.load(str(TGT))
    patch(xp)
    xp.save(str(TGT))
    print("gromit head v3 (r0c0): stepped ears + spaced eyes + nose dropped to ly=6")


if __name__ == "__main__":
    main()
