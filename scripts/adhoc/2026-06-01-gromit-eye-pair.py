#!/usr/bin/env python3
"""Gromit eye-pair completion: the original wolfie design had each eye as a
cyan-half-block (which we converted to BLACK in 2026-06-01-gromit-head-restyle)
paired with an invisible half-block (cream fg on cream bg) for the second eye.
Convert those invisible partners into solid BLACK eye squares so Gromit gets
a full pair in every facing frame.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "pipeline"))
from xp_core import XPFile

TGT = REPO / "assets" / "sprites" / "2026-05-28-gromit.xp"

WARM_CREAM = (0xFF, 0xE4, 0xB5)
BLACK = (0, 0, 0)

SPRITE_H = 12
HEAD_LY_LO = 2
HEAD_LY_HI = 5


def fix(xp):
    layer = xp.layers[2]
    n = 0
    for ay in range(layer.height):
        ly_local = ay % SPRITE_H
        if not (HEAD_LY_LO <= ly_local <= HEAD_LY_HI):
            continue
        for ax in range(layer.width):
            g, fg, bg = layer.data[ay][ax]
            if g in (220, 223) and fg == WARM_CREAM and bg == WARM_CREAM:
                layer.data[ay][ax] = (219, BLACK, WARM_CREAM)
                n += 1
    return n


def main():
    if not TGT.exists():
        sys.exit(f"missing {TGT}")
    xp = XPFile()
    xp.load(str(TGT))
    n = fix(xp)
    xp.save(str(TGT))
    print(f"gromit eye-pair completion: {n} invisible half-blocks promoted to BLACK eyes")


if __name__ == "__main__":
    main()
