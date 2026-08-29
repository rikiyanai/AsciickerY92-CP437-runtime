#!/usr/bin/env python3
"""Gromit ears v4 (r0c0) — match image #7 exactly:
  each ear = 1 top tip + 2 bottom flanks with cream gap directly below the tip.
  6 brown cells across both ears, no adjacent-pair "single block" look.

sr=0 sc=0 layer 2 head zone, post-patch:
  ly=1:  . . . B . . B . . .         (tips at cols 3, 6)
  ly=2:  . . B _ B B _ B . .         (flanks at 2,4,5,7; cream at 3,6 under tips)
  ly=3:  . . . K _ _ K . . .         (eyes at corners with cream gap)
  ly=4:  . . . _ _ _ _ . . .         (cream face)
  ly=5:  . . . . _ _ . . . .
  ly=6:  . . . . N N . . . .         (nose)
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
    bx, by = 0 * SPRITE_W, 0 * SPRITE_H

    def put(lx, ly, cell):
        layer.data[by + ly][bx + lx] = cell

    # ── ly=1: top tips at cols 3, 6 only ──
    put(2, 1, (32, BROWN, MAG))       # clear any prior ear at col 2
    put(3, 1, (219, BROWN, MAG))      # left tip
    put(4, 1, (32, BROWN, MAG))       # clear
    put(5, 1, (32, BROWN, MAG))       # clear
    put(6, 1, (219, BROWN, MAG))      # right tip
    put(7, 1, (32, BROWN, MAG))       # clear

    # ── ly=2: split-bottom flanks at cols 2, 4, 5, 7; cream under tips at 3, 6 ──
    put(2, 2, (219, BROWN, MAG))      # left outer flank
    put(3, 2, (32, WARM, WARM))       # cream below left tip
    put(4, 2, (219, BROWN, WARM))     # left inner flank (inside silhouette)
    put(5, 2, (219, BROWN, WARM))     # right inner flank
    put(6, 2, (32, WARM, WARM))       # cream below right tip
    put(7, 2, (219, BROWN, MAG))      # right outer flank

    # ── ly=3: eyes at corners with cream gap (idempotent w/ v3) ──
    put(3, 3, (219, BLACK, WARM))
    put(4, 3, (32, WARM, WARM))
    put(5, 3, (32, WARM, WARM))
    put(6, 3, (219, BLACK, WARM))

    # ── ly=5, 6: nose dropped (idempotent w/ v3) ──
    put(4, 5, (32, WARM, WARM))
    put(5, 5, (32, WARM, WARM))
    put(4, 6, (219, DARK_SNOUT, WARM))
    put(5, 6, (219, DARK_SNOUT, WARM))


def main():
    if not TGT.exists():
        sys.exit(f"missing {TGT}")
    xp = XPFile()
    xp.load(str(TGT))
    patch(xp)
    xp.save(str(TGT))
    print("gromit ears v4 (r0c0): split-bottom ears, 6 brown cells")


if __name__ == "__main__":
    main()
