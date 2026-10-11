#!/usr/bin/env python3
"""Wallace: restore BLACK fg on ear half-blocks (221 left-ear, 222 right-ear)
in rear-view head zones. The earlier bald-back pass over-corrected — it
collapsed every dark-fg half-block to cream/cream, which erased the ear
outline and made the ears look like solid cream blocks.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "pipeline"))
from xp_core import XPFile

TGT = REPO / "assets" / "sprites" / "2026-05-28-wallace.xp"

WARM_CREAM = (0xFF, 0xE4, 0xB5)
BLACK = (0, 0, 0)
EAR_GLYPHS = {221, 222}

SPRITE_H = 9
HEAD_LY_MAX = 2
REAR_SR = {3, 4, 5}


def patch(xp: XPFile) -> int:
    layer = xp.layers[2]
    n = 0
    for ay in range(layer.height):
        sr = ay // SPRITE_H
        if sr not in REAR_SR:
            continue
        ly_local = ay % SPRITE_H
        if ly_local > HEAD_LY_MAX:
            continue
        for ax in range(layer.width):
            g, fg, bg = layer.data[ay][ax]
            if g in EAR_GLYPHS and bg == WARM_CREAM and fg == WARM_CREAM:
                layer.data[ay][ax] = (g, BLACK, WARM_CREAM)
                n += 1
    return n


def main():
    if not TGT.exists():
        sys.exit(f"missing {TGT}")
    xp = XPFile()
    xp.load(str(TGT))
    n = patch(xp)
    xp.save(str(TGT))
    print(f"wallace back-ear restore: {n} half-block cells set to BLACK fg / cream bg")


if __name__ == "__main__":
    main()
