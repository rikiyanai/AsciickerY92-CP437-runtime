#!/usr/bin/env python3
"""
2026-05-31 -- Gromit cream-purge (idempotent)
  - Purge dark grey (85,85,85) and light grey (170,170,170) bgs -> CREAM (170,170,170)
  - Collapse heavy brown blocks on cream face/body to space glyph 32 with cream fg
Keep dog markings (brown FG outlines) intact.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "pipeline"))
from xp_core import XPFile

TGT = REPO / "assets" / "sprites" / "2026-05-28-gromit.xp"

CREAM      = (0xAA, 0xAA, 0xAA)
DARK_GREY  = (0x55, 0x55, 0x55)
BROWN      = (0xAA, 0x55, 0x00)
MAGENTA    = (0xFF, 0x00, 0xFF)
WHITE      = (0xFF, 0xFF, 0xFF)
HEAVY_BLOCKS = {219, 220, 221, 222, 223}


def purge(xp: XPFile) -> dict:
    counts = {"bg_grey_to_cream": 0, "heavy_brown_on_cream": 0}
    for layer in xp.layers:
        for y, row in enumerate(layer.data):
            for x, cell in enumerate(row):
                g, fg, bg = cell
                if bg in (MAGENTA, WHITE):
                    continue
                new_g, new_fg, new_bg = g, fg, bg
                if bg == DARK_GREY:
                    new_bg = CREAM
                    counts["bg_grey_to_cream"] += 1
                if new_bg == CREAM and g in HEAVY_BLOCKS and fg == BROWN:
                    new_g, new_fg = 32, CREAM
                    counts["heavy_brown_on_cream"] += 1
                if (new_g, new_fg, new_bg) != (g, fg, bg):
                    layer.data[y][x] = (new_g, new_fg, new_bg)
    return counts


def main():
    if not TGT.exists():
        sys.exit(f"missing {TGT}")
    xp = XPFile(); xp.load(str(TGT))
    c = purge(xp)
    xp.save(str(TGT))
    print(f"gromit cream pass: {c}  total={sum(c.values())}")


if __name__ == "__main__":
    main()
