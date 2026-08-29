#!/usr/bin/env python3
"""
2026-05-31 -- Wallace face fixes
  - " (34) eyes -> infinity (236)
  - side view ` (96) / ' (39) eyes -> o (111)
  - flip ear halfblocks (222 <-> 221) on hair-zone cells
  - restore top-of-head halfblock with sparse hair tone
Position guide = original player-body.xp (hair-zone cells = bg PINK_RED).
"""
import sys
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "pipeline"))
from xp_core import XPFile

SPR = REPO / "assets" / "sprites"
SRC_ORIG = SPR / "player-body.xp"
TGT = SPR / "2026-05-28-wallace.xp"

PINK_RED    = (0xFF, 0x55, 0x55)
YELLOW_SKIN = (0xFF, 0xFF, 0x55)
CREAM       = (0xAA, 0xAA, 0xAA)
BLACK       = (0x00, 0x00, 0x00)
HAIR        = (0x55, 0x55, 0x55)
MAGENTA     = (0xFF, 0x00, 0xFF)
WHITE       = (0xFF, 0xFF, 0xFF)
HAIR_BGS    = {PINK_RED, YELLOW_SKIN}


def fix(orig: XPFile, wal: XPFile) -> dict:
    o = orig.layers[2]
    w = wal.layers[2]
    H, W = w.height, w.width
    SH = 9
    counts = {"eyes_front": 0, "eyes_side": 0, "ear_flip": 0, "top_restore": 0, "ear_red_fix": 0}

    for ay in range(H):
        ly_local = ay % SH  # head zone = 0..2
        for ax in range(W):
            o_g, o_fg, o_bg = o.data[ay][ax]
            w_g, w_fg, w_bg = w.data[ay][ax]
            if o_bg in (MAGENTA, WHITE):
                continue

            new = (w_g, w_fg, w_bg)

            # constrain hair/ear/top edits to head zone (ly 0..2) only
            head_zone = ly_local <= 2

            if o_g == 34:
                new = (236, BLACK, CREAM)
                counts["eyes_front"] += 1
            elif o_g in (39, 96):
                new = (111, BLACK, CREAM)
                counts["eyes_side"] += 1
            elif head_zone and o_g == 222 and o_bg in HAIR_BGS:
                new = (221, BLACK, CREAM)
                counts["ear_flip"] += 1
            elif head_zone and o_g == 221 and o_bg in HAIR_BGS:
                new = (222, BLACK, CREAM)
                counts["ear_flip"] += 1
            elif head_zone and ly_local == 0 and o_g == 220 and o_bg in HAIR_BGS:
                new = (220, CREAM, HAIR)
                counts["top_restore"] += 1
            else:
                if w_fg == PINK_RED:
                    new = (w_g, CREAM, w_bg)
                    counts["ear_red_fix"] += 1

            if new != (w_g, w_fg, w_bg):
                w.data[ay][ax] = new
    return counts


def main():
    if not SRC_ORIG.exists():
        sys.exit(f"missing {SRC_ORIG}")
    if not TGT.exists():
        sys.exit(f"missing {TGT}")
    orig = XPFile(); orig.load(str(SRC_ORIG))
    wal  = XPFile(); wal.load(str(TGT))
    c = fix(orig, wal)
    wal.save(str(TGT))
    total = sum(c.values())
    print(f"wallace face pass: {c}  total={total}")


if __name__ == "__main__":
    main()
