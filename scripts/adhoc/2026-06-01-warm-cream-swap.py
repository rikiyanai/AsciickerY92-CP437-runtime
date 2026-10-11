#!/usr/bin/env python3
"""
2026-06-01 -- Replace CGA light-grey 'CREAM' (0xAA,0xAA,0xAA) with WARM cream
(0xFF,0xE4,0xB5) in Wallace + Gromit XP. Hits both fg and bg across all layers.
The previous CREAM was actually grey; this is real cream.
"""
import sys
from pathlib import Path

REPO = Path("/Users/r/Downloads/asciicker-Y9-2")
sys.path.insert(0, str(REPO / "scripts" / "pipeline"))
from xp_core import XPFile

GREY_CREAM = (0xAA, 0xAA, 0xAA)
WARM_CREAM = (0xFF, 0xE4, 0xB5)  # moccasin, clearly warm cream not grey

TARGETS = [
    REPO / "assets/sprites/2026-05-28-wallace.xp",
    REPO / "assets/sprites/2026-05-28-gromit.xp",
]


def swap(xp: XPFile) -> int:
    n = 0
    for layer in xp.layers:
        for y, row in enumerate(layer.data):
            for x, (g, fg, bg) in enumerate(row):
                new_fg = WARM_CREAM if fg == GREY_CREAM else fg
                new_bg = WARM_CREAM if bg == GREY_CREAM else bg
                if (new_fg, new_bg) != (fg, bg):
                    layer.data[y][x] = (g, new_fg, new_bg)
                    n += 1
    return n


def main():
    for t in TARGETS:
        if not t.exists():
            print(f"skip missing {t}")
            continue
        xp = XPFile(); xp.load(str(t))
        n = swap(xp)
        xp.save(str(t))
        print(f"{t.name}: {n} cells swapped to warm cream")


if __name__ == "__main__":
    main()
