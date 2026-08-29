#!/usr/bin/env python3
"""Gromit head restyle (matches the user's 2026-06-01 screenshot vocabulary).

Across every sprite frame (18 cols x 8 rows), within head zone ly 2..5:
  - ear marker      g=30  ▲ BROWN/cream    -> g=219 █ BROWN/cream      (solid brown ear block)
  - cyan eye half   g=220 ▄ CYAN/cream     -> g=219 █ BLACK/cream      (solid black eye square)
  - cyan eye top    g=223 ▀ CYAN/cream     -> g=219 █ BLACK/cream      (solid black eye square)
  - snout dot       g=249 · BROWN/cream    -> g=219 █ DARK_SNOUT/cream (solid dark snout square)
  - down-tri jaw    g=31  ▼ CREAM/BROWN    -> g=32  space CREAM/cream  (clean cream jaw cell)
Other cells (transparent magenta, cream face, 176/177 shading) left alone.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "pipeline"))
from xp_core import XPFile

TGT = REPO / "assets" / "sprites" / "2026-05-28-gromit.xp"

WARM_CREAM = (0xFF, 0xE4, 0xB5)
BROWN      = (0xAA, 0x55, 0x00)
CYAN       = (0x00, 0xAA, 0xAA)
BLACK      = (0x00, 0x00, 0x00)
DARK_SNOUT = (0x3C, 0x28, 0x18)   # very dark brown
MAGENTA    = (0xFF, 0x00, 0xFF)

SPRITE_H = 12
HEAD_LY_LO = 2
HEAD_LY_HI = 5


def restyle(xp: XPFile) -> dict:
    layer = xp.layers[2]
    counts = {"ear": 0, "eye": 0, "snout": 0, "jaw_clear": 0}
    for ay in range(layer.height):
        ly_local = ay % SPRITE_H
        if not (HEAD_LY_LO <= ly_local <= HEAD_LY_HI):
            continue
        for ax in range(layer.width):
            g, fg, bg = layer.data[ay][ax]
            if bg == MAGENTA:
                continue
            new = (g, fg, bg)
            if g == 30 and fg == BROWN and bg == WARM_CREAM:
                new = (219, BROWN, WARM_CREAM)
                counts["ear"] += 1
            elif g in (220, 223) and fg == CYAN and bg == WARM_CREAM:
                new = (219, BLACK, WARM_CREAM)
                counts["eye"] += 1
            elif g == 249 and fg == BROWN and bg == WARM_CREAM:
                new = (219, DARK_SNOUT, WARM_CREAM)
                counts["snout"] += 1
            elif g == 31 and fg == WARM_CREAM and bg == BROWN:
                new = (32, WARM_CREAM, WARM_CREAM)
                counts["jaw_clear"] += 1
            if new != (g, fg, bg):
                layer.data[ay][ax] = new
    return counts


def main():
    if not TGT.exists():
        sys.exit(f"missing {TGT}")
    xp = XPFile()
    xp.load(str(TGT))
    c = restyle(xp)
    xp.save(str(TGT))
    print(f"gromit head restyle: {c}  total={sum(c.values())}")


if __name__ == "__main__":
    main()
