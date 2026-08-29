#!/usr/bin/env python3
"""Wallace: make the back of head cream (bald) for rear-facing sprite rows.

Affected: sprite rows sr in {3 rear-R, 4 back, 5 rear-L}, head zone ly 0..2.
Action:
  - bg == BLACK              -> bg = WARM_CREAM (kill the black back-of-head)
  - bg == HAIR_GREY (5,5,5)  -> bg = WARM_CREAM (kill the grey top-hair restore)
  - glyph 219 fg=BLACK       -> glyph 32  (solid black block becomes blank)
  - half-blocks 220/221/222/223 with fg in {BLACK, BROWN, HAIR_GREY}
                              -> fg = WARM_CREAM (texture blends into cream skin)
Preserve magenta (transparent) bg.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "pipeline"))
from xp_core import XPFile

TGT = REPO / "assets" / "sprites" / "2026-05-28-wallace.xp"

WARM_CREAM = (0xFF, 0xE4, 0xB5)
BLACK = (0, 0, 0)
HAIR_GREY = (0x55, 0x55, 0x55)
BROWN = (0xAA, 0x55, 0x00)
MAGENTA = (0xFF, 0x00, 0xFF)
HALF_BLOCKS = {220, 221, 222, 223}
HAIR_FG_TONES = {BLACK, BROWN, HAIR_GREY}
DARK_BGS = {BLACK, HAIR_GREY}

SPRITE_H = 9
HEAD_LY_MAX = 2
REAR_SR = {3, 4, 5}


def patch(xp: XPFile) -> dict:
    layer = xp.layers[2]
    counts = {"bg_dark_to_cream": 0, "solid_black_clear": 0, "halfblock_fg_to_cream": 0}
    for ay in range(layer.height):
        sr = ay // SPRITE_H
        if sr not in REAR_SR:
            continue
        ly_local = ay % SPRITE_H
        if ly_local > HEAD_LY_MAX:
            continue
        for ax in range(layer.width):
            g, fg, bg = layer.data[ay][ax]
            if bg == MAGENTA:
                continue
            new_g, new_fg, new_bg = g, fg, bg
            if bg in DARK_BGS:
                new_bg = WARM_CREAM
                counts["bg_dark_to_cream"] += 1
            if g == 219 and fg == BLACK:
                new_g, new_fg = 32, WARM_CREAM
                counts["solid_black_clear"] += 1
            elif g in HALF_BLOCKS and fg in HAIR_FG_TONES:
                new_fg = WARM_CREAM
                counts["halfblock_fg_to_cream"] += 1
            if (new_g, new_fg, new_bg) != (g, fg, bg):
                layer.data[ay][ax] = (new_g, new_fg, new_bg)
    return counts


def main():
    if not TGT.exists():
        sys.exit(f"missing {TGT}")
    xp = XPFile()
    xp.load(str(TGT))
    c = patch(xp)
    xp.save(str(TGT))
    print(f"wallace bald-back pass: {c}  total={sum(c.values())}")


if __name__ == "__main__":
    main()
