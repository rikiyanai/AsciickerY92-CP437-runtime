#!/usr/bin/env python3
"""
2026-05-28 -- Make Wallace & Gromit sprite variants

Wallace (player-body.xp -> 2026-05-28-wallace.xp):
  - Shirt + arms: very dark forest colour (#006600 BG, #004400 FG detail)
  - Face: cream (#aaaaaa), no hair (#ff5555 head rows -> cream)
  - Red tie: glyph 31 (down-triangle), #aa0000 FG on #ffffff BG at chest-center
  - Pants: brown (#aa5500)
  - Shoes: unchanged

Gromit (wolfie.xp -> 2026-05-28-gromit.xp):
  - Body: cream/light-gray (#ffff55 BG -> #aaaaaa)
  - Outlines: black FG -> brown (#aa5500) for dog-marking look
  - Red saddle spots -> brown

Position-aware sprite grid (player-body.xp):
  Layer 2 is the visual layer.
  Sprite cell = 7 cols x 9 rows. Grid = 18 x 8 (144 sprites total).
  local_y 0-2 = head/face
  local_y 3-5 = shirt/arms
  local_y 6-7 = pants/shoes
"""
import sys
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "pipeline"))
from xp_core import XPFile

SPRITES = REPO / "assets" / "sprites"

# -- Palette --
YELLOW_SKIN   = (0xFF, 0xFF, 0x55)
PINK_RED      = (0xFF, 0x55, 0x55)
SHIRT_MAIN    = (0xAA, 0x00, 0xAA)
BLUE_PANTS_BG = (0x55, 0x55, 0xFF)
BLUE_PANTS_DK = (0x00, 0x00, 0xAA)
WHITE         = (0xFF, 0xFF, 0xFF)
MAGENTA       = (0xFF, 0x00, 0xFF)
BLACK         = (0x00, 0x00, 0x00)

CREAM         = (0xAA, 0xAA, 0xAA)
VEST_BG       = (0x00, 0x66, 0x00)   # very dark forest-tone vest bg
VEST_FG       = (0x00, 0x44, 0x00)   # darker detail FG for vest texture
BROWN         = (0xAA, 0x55, 0x00)
TIE_FG        = (0xAA, 0x00, 0x00)
TIE_BG        = (0xFF, 0xFF, 0xFF)
TIE_GLYPH     = 31                   # down-pointing triangle glyph

SPRITE_W = 7
SPRITE_H = 9


def recolor_wallace(xp: XPFile) -> int:
    layer = xp.layers[2]
    sprite_cols = layer.width  // SPRITE_W   # 18
    sprite_rows = layer.height // SPRITE_H   # 8
    changed = 0

    for sr in range(sprite_rows):
        for sc in range(sprite_cols):
            for ly in range(SPRITE_H):
                for lx in range(SPRITE_W):
                    ax = sc * SPRITE_W + lx
                    ay = sr * SPRITE_H + ly
                    g, fg, bg = layer.data[ay][ax]
                    if bg in (WHITE, MAGENTA):
                        continue

                    new_g, new_fg, new_bg = g, fg, bg

                    if ly <= 2:
                        # head / face rows
                        if bg == YELLOW_SKIN:
                            new_bg = CREAM
                            # glyph 220 (half-block) = hair texture at top of head
                            # set fg to cream too so the dark half disappears
                            if g == 220:
                                new_fg = CREAM
                                new_g  = 32
                        elif bg == PINK_RED:
                            new_bg = CREAM
                            # glyph 34 (") = two eye dots — keep fg black so eyes stay visible
                            # glyph 118 (v) = mouth/nose detail — keep black too
                            if g in (34, 118):
                                new_fg = BLACK
                            else:
                                new_fg = CREAM

                    elif 3 <= ly <= 5:
                        # shirt / arm rows
                        if bg in (SHIRT_MAIN, PINK_RED, YELLOW_SKIN):
                            new_bg = VEST_BG
                        if new_bg == VEST_BG:
                            if fg in (SHIRT_MAIN, PINK_RED, YELLOW_SKIN):
                                new_fg = VEST_FG
                        # tie at center chest
                        if lx == 3 and ly == 3:
                            new_g, new_fg, new_bg = TIE_GLYPH, TIE_FG, TIE_BG

                    else:
                        # pants / shoes rows
                        if bg in (BLUE_PANTS_BG, BLUE_PANTS_DK):
                            new_bg = BROWN
                        if fg in (BLUE_PANTS_BG, BLUE_PANTS_DK):
                            new_fg = BROWN

                    if (new_g, new_fg, new_bg) != (g, fg, bg):
                        layer.data[ay][ax] = (new_g, new_fg, new_bg)
                        changed += 1

    return changed


GROMIT_FG_MAP = {
    YELLOW_SKIN:        CREAM,
    BLACK:              BROWN,
}
GROMIT_BG_MAP = {
    YELLOW_SKIN:        CREAM,
    (0xAA, 0x00, 0x00): BROWN,
}


def recolor_gromit(xp: XPFile) -> int:
    changed = 0
    for layer in xp.layers:
        for y, row in enumerate(layer.data):
            for x, cell in enumerate(row):
                g, fg, bg = cell
                new_fg = GROMIT_FG_MAP.get(fg, fg)
                new_bg = GROMIT_BG_MAP.get(bg, bg)
                if new_fg != fg or new_bg != bg:
                    layer.data[y][x] = (g, new_fg, new_bg)
                    changed += 1
    return changed


def make_variant(src_name, dst_name, recolor_fn):
    src = SPRITES / src_name
    dst = SPRITES / dst_name
    print(f"  {src_name} -> {dst_name}")
    shutil.copy2(src, dst)
    xp = XPFile()
    xp.load(str(dst))
    n = recolor_fn(xp)
    xp.save(str(dst))
    print(f"    {n} cells recolored")


if __name__ == "__main__":
    print("=== Wallace ===")
    make_variant("player-body.xp", "2026-05-28-wallace.xp", recolor_wallace)
    print("\n=== Gromit ===")
    make_variant("wolfie.xp", "2026-05-28-gromit.xp", recolor_gromit)
    print("\nVariants written.")
