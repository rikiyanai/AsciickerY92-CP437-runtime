#!/usr/bin/env python3
"""
2026-10-08 -- Author the UFO Rapture Death saucer sprite (assets/sprites/ufo.xp)

Spec: docs/specs/2026-10-08-ufo-rapture-death.md (section 5, step 4). Flat .xp
sprite, same UFO for every skin, render-only (no Inst, no physics, no HitWorld).

Re-authored after operator feedback (2026-10-08): "the ufo sprite must be
authored xp sprite using proper glyphs". It is a REXPaint-style CP437 glyph
drawing, not half-block pixels: shape glyphs carry the silhouette and the
fg/bg pairs carry the shading.

    row 0  dome cap      ▄ (glass shades lit from the upper left)
    row 1  dome + alien  ( ░ ∙ ☻ ∙ ░ )  between ▄ hull shoulders
    row 2  upper hull    ▄ ▀▀▀▀ ▀ (chrome highlight over mid grey, collar
                         under the dome)
    row 3  rim band      ▐ ═══ ... ▌ with running lights ☼ ° ∙ and a ◘
                         beacon at the centre
    row 4  underside     \\ ▀▀▀ ▄▄▄▄▄ ▀▀▀ /  with the warm emitter (▄, lower
                         half lit) right above the beam

Outputs:
    assets/sprites/ufo.xp          1 angle, 1 animation of 4 frames, 21x5 cells
    docs/previews/ufo/ufo.png      every frame with the real CP437 atlas
                                   (assets/fonts/cp437_16x16.png), plus a
                                   4-frame strip at 2x for approval

Deterministic: no inputs except the CP437 font for the preview. The .xp is
gzip-compressed with mtime=0 so identical runs give identical bytes (the
shared xp_core.XPFile.save() stamps the current time into the gzip header).

SHEET FACTS (engine/sprite.cpp LoadSpriteLayer)
  * Layer 0 = atlas metadata.  (0,0) is NOT a digit -> angles=1, projs=1 (no
    water-reflection half; the renderer skips projs==1 sprites in the
    reflection pass).  Column 1 row 0 = '4' -> one animation of 4 frames.
    Column 0 row 1 = '0' -> y reference 0 half-blocks (the reference point is
    the bottom edge of the hull, where the beam starts).  Column 0 row 2 = '0'
    -> z reference 0.  Every layer-0 background is the colour key (255,255,85).
  * Layer 1 = per-cell height digits (spare, in full blocks): rows count up
    from the bottom so the hull depth matches a vertical object.
  * Layer 2 = visual.  A colour equal to the key is transparent; sprite.cpp
    matches it EXACTLY, so the generator asserts that no visible fg/bg equals
    (255,255,85) (and, to be safe, that none even quantizes to its cube cell).
    Magenta bk = fully transparent cell.  The key appears only as the empty
    half of a half-block / shape-glyph cell's background.
  * No cyan (0,255,255) swoosh cells: the beam is a warm yellow render pass
    now, so the saucer's own emitter is painted warm instead.

PALETTE: every visible colour sits exactly on the engine's 6-level cube
(0,51,102,153,204,255).
"""
import gzip
import io
import struct
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT_XP = REPO / "assets" / "sprites" / "ufo.xp"
PREVIEW_DIR = REPO / "docs" / "previews" / "ufo"
FONT = REPO / "assets" / "fonts" / "cp437_16x16.png"

KEY = (255, 255, 85)       # layer-0 colour key (transparent)
MAGENTA = (255, 0, 255)    # REXPaint transparent cell
CYAN = (0, 255, 255)       # swoosh marker (not used)

FW, FH, FRAMES = 21, 5, 4

# hull greys (lit from the upper left)
CHROME = (255, 255, 255)
HULL_HI = (204, 204, 204)
HULL_MID = (153, 153, 153)
HULL_DARK = (102, 102, 102)
HULL_SHADE = (51, 51, 51)
RIM = (51, 51, 102)
RIM_HI = (153, 153, 204)
RIM_DARK = (0, 0, 51)
# dome glass
GLASS_DARK = (0, 51, 102)
GLASS = (0, 102, 153)
GLASS_HI = (102, 204, 255)
GLINT = (204, 255, 255)
ALIEN = (102, 255, 51)
# warm emitter under the hull (matches the yellow beam, never the key)
EMIT = [(255, 255, 153), (255, 204, 51), (255, 255, 204), (255, 204, 51)]
EMIT_EDGE = (204, 102, 0)
# running lights
LIGHT_DIM = (102, 102, 153)
LIGHTS = [(255, 51, 51), (255, 153, 0), (51, 255, 102), (255, 255, 255)]

EMPTY = (32, (0, 0, 0), MAGENTA)
G_UPPER, G_LOWER, G_LEFT, G_RIGHT, G_FULL = 223, 220, 221, 222, 219
G_LIGHT, G_MED = 176, 177
G_DBL_H = 205            # ═
G_PAREN_L, G_PAREN_R = ord("("), ord(")")
G_BSLASH, G_SLASH = ord("\\"), ord("/")
G_SUN, G_DEG, G_DOT, G_BULLET_INV, G_FACE = 15, 248, 249, 8, 2  # ☼ ° ∙ ◘ ☻


def frame_cells(f):
    """Return FH rows (top to bottom) of FW cells (glyph, fg, bg)."""
    rows = [[EMPTY] * FW for _ in range(FH)]
    # row 0: dome cap -- lower halves, glass shading lit from the upper left
    for x, c in zip(range(8, 13), (GLASS_HI, GLINT, GLASS_HI, GLASS, GLASS_DARK)):
        rows[0][x] = (G_LOWER, c, KEY)
    # row 1: dome with the pilot, between the hull shoulders
    for x, c in ((3, HULL_DARK), (4, HULL_MID), (5, HULL_HI), (6, HULL_HI),
                 (14, HULL_MID), (15, HULL_MID), (16, HULL_DARK), (17, HULL_SHADE)):
        rows[1][x] = (G_LOWER, c, KEY)
    rows[1][7] = (G_PAREN_L, CHROME, GLASS)
    rows[1][8] = (G_LIGHT, GLINT, GLASS)
    rows[1][9] = (G_DOT, GLASS_HI, GLASS)
    rows[1][10] = (G_FACE, ALIEN, GLASS)
    rows[1][11] = (G_DOT, GLASS_HI, GLASS_DARK)
    rows[1][12] = (G_LIGHT, GLASS, GLASS_DARK)
    rows[1][13] = (G_PAREN_R, HULL_MID, GLASS_DARK)
    # row 2: upper hull -- chrome highlight (upper half) over mid grey, the
    # dome collar under the glass, darker toward the right
    rows[2][1] = (G_LOWER, HULL_MID, KEY)
    rows[2][19] = (G_LOWER, HULL_SHADE, KEY)
    for x in range(2, 19):
        top = CHROME if x < 7 else (HULL_HI if x < 14 else HULL_MID)
        bot = HULL_MID if x < 14 else HULL_DARK
        rows[2][x] = (G_UPPER, top, bot)
    # shading transitions dithered with shade glyphs instead of hard steps
    rows[2][6] = (G_MED, CHROME, HULL_MID)
    rows[2][13] = (G_MED, HULL_HI, HULL_MID)
    rows[2][14] = (G_MED, HULL_MID, HULL_DARK)
    # row 3: rim band, full width, running lights chase outward from the beacon
    rows[3][0] = (G_RIGHT, RIM_HI, KEY)
    rows[3][20] = (G_LEFT, RIM, KEY)
    for x in range(1, 20):
        rows[3][x] = (G_DBL_H, RIM_HI if x < 10 else HULL_MID, RIM)
    lights_l, lights_r = (8, 6, 4, 2), (12, 14, 16, 18)
    for i in range(4):
        lit = i == f
        for x in (lights_l[i], lights_r[i]):
            if lit:
                rows[3][x] = (G_SUN, LIGHTS[(i + f) % 4], RIM)
            else:
                rows[3][x] = (G_DEG if i % 2 else G_DOT, LIGHT_DIM, RIM)
    rows[3][10] = (G_BULLET_INV, EMIT[f], RIM_DARK if f % 2 else RIM)  # beacon
    # row 4: underside -- dark lip under the rim, chamfered skirt (\ /),
    # and the warm emitter right above the beam
    for x in (2, 3):
        rows[4][x] = (G_UPPER, RIM_DARK, KEY)
    for x in (17, 18):
        rows[4][x] = (G_UPPER, RIM_DARK, KEY)
    rows[4][4] = (G_BSLASH, HULL_DARK, KEY)
    rows[4][16] = (G_SLASH, HULL_DARK, KEY)
    for x in range(5, 16):
        rows[4][x] = (G_UPPER, HULL_DARK if x < 10 else HULL_SHADE, KEY)
    rows[4][7] = (G_LOWER, EMIT_EDGE, HULL_DARK)
    rows[4][13] = (G_LOWER, EMIT_EDGE, HULL_SHADE)
    for x in range(8, 13):
        rows[4][x] = (G_LOWER, EMIT[(f + x) % 4], HULL_DARK if x < 10 else HULL_SHADE)
    return rows


def layer0():
    w = FW * FRAMES
    cells = [[(0, (0, 0, 0), KEY) for _ in range(w)] for _ in range(FH)]
    cells[0][1] = (ord("4"), (0, 0, 0), KEY)   # one animation, 4 frames
    cells[1][0] = (ord("0"), (0, 0, 0), KEY)   # y ref: hull bottom edge
    cells[2][0] = (ord("0"), (0, 0, 0), KEY)   # z ref
    return cells


def layer1():
    w = FW * FRAMES
    return [[(ord(str(FH - 1 - y)), (0, 0, 0), (0, 0, 0)) for _ in range(w)]
            for y in range(FH)]


def layer2():
    w = FW * FRAMES
    out = [[EMPTY] * w for _ in range(FH)]
    for f in range(FRAMES):
        rows = frame_cells(f)
        for y in range(FH):
            for x in range(FW):
                out[y][f * FW + x] = rows[y][x]
    return out


def quant(c):
    return tuple((v * 5 + 128) // 255 for v in c)


def validate(l2):
    """sprite.cpp keys transparency on an EXACT colour match with (255,255,85):
    no visible half of any cell may use it (it would punch a hole), and no
    colour may even land on the key's cube cell.  No cyan swoosh at all."""
    key_q = quant(KEY)
    glyphs = set()
    for y, row in enumerate(l2):
        for x, (g, fg, bg) in enumerate(row):
            assert 0 <= g <= 255
            if bg == MAGENTA:
                continue
            glyphs.add(g)
            # the key may only be the empty background of a shape glyph
            assert fg != KEY, "visible fg equals the transparency key at (%d,%d)" % (x, y)
            if bg == KEY:
                assert g not in (0, 32, G_FULL), "key bg on a fill glyph at (%d,%d)" % (x, y)
            for c in (fg, bg):
                if c == KEY:
                    continue
                assert c != CYAN, "no cyan swoosh cells (beam is yellow) at (%d,%d)" % (x, y)
                assert c != MAGENTA, "magenta used as a colour at (%d,%d)" % (x, y)
                assert quant(c) != key_q, "colour %r quantizes to the key at (%d,%d)" % (c, x, y)
                assert all(v in (0, 51, 102, 153, 204, 255) for v in c), c
    # a glyph-built saucer, not flat half-block pixels
    shape = {G_LOWER, G_UPPER, G_LEFT, G_RIGHT}
    assert len(glyphs - shape - {32}) >= 8, sorted(glyphs)
    for need in (G_DBL_H, G_PAREN_L, G_PAREN_R, G_BSLASH, G_SLASH, G_SUN):
        assert need in glyphs, need


def write_xp(path, layers):
    buf = io.BytesIO()
    buf.write(struct.pack("<i", -1))
    buf.write(struct.pack("<I", len(layers)))
    for cells in layers:
        h, w = len(cells), len(cells[0])
        buf.write(struct.pack("<ii", w, h))
        for x in range(w):
            for y in range(h):
                g, fg, bg = cells[y][x]
                buf.write(struct.pack("<I", g))
                buf.write(bytes(fg))
                buf.write(bytes(bg))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(buf.getvalue(), compresslevel=9, mtime=0))


def lighten(c, n=1):
    return tuple(min(255, v + 51 * n) for v in c)


def q6(c):
    return tuple(((v * 5 + 128) // 255) * 51 for v in c)


def render_frame(rows, font, C, sky):
    from PIL import Image
    im = Image.new("RGB", (FW * C, FH * C), sky)
    for y in range(FH):
        for x in range(FW):
            g, fg, bg = rows[y][x]
            if bg == MAGENTA:
                continue
            box = (x * C, y * C, (x + 1) * C, (y + 1) * C)
            gm = font.crop(((g % 16) * C, (g // 16) * C, (g % 16 + 1) * C, (g // 16 + 1) * C))
            if bg != KEY:
                im.paste(q6(bg), box)
            if fg != KEY:
                im.paste(q6(fg), (x * C, y * C), gm)
    return im


def preview(path):
    from PIL import Image, ImageDraw, ImageFont
    font = Image.open(FONT).convert("L")
    C = font.width // 16
    try:
        lf = ImageFont.truetype("DejaVuSans.ttf", 15)
    except OSError:
        lf = ImageFont.load_default()
    backs = [("grass", (51, 153, 51)), ("night", (0, 0, 51))]
    pad, th = 16, 56
    fr = {n: [render_frame(frame_cells(f), font, C, b) for f in range(FRAMES)] for n, b in backs}
    fw, fh = fr["grass"][0].size
    big = [im.resize((im.width * 2, im.height * 2), Image.NEAREST) for im in fr["grass"]]
    W = max(pad + FRAMES * (fw + pad), pad + 2 * (big[0].width + pad))
    H = th + 2 * (fh + 28) + 12 + 2 * (big[0].height + pad)
    out = Image.new("RGB", (W, H), (24, 24, 30))
    d = ImageDraw.Draw(out)
    d.text((pad, 8), "ufo.xp  %dx%d cells, 1 angle, %d-frame light cycle, CP437 %dx%d atlas"
           % (FW, FH, FRAMES, C, C), fill=(230, 230, 230), font=lf)
    d.text((pad, 28), "glyph-built: ( \u263b ) dome, \u2550 rim, \u263c\u00b0\u2219\u25d8 lights, \\ / underside, "
           "\u2584 warm emitter", fill=(170, 170, 170), font=lf)
    y = th
    for n, _ in backs:
        for f, im in enumerate(fr[n]):
            x = pad + f * (fw + pad)
            d.text((x, y), "%s  frame %d" % (n, f), fill=(200, 200, 120), font=lf)
            out.paste(im, (x, y + 20))
        y += fh + 28
    y += 12
    for f, im in enumerate(big):
        out.paste(im, (pad + (f % 2) * (im.width + pad), y + (f // 2) * (im.height + pad)))
    path.parent.mkdir(parents=True, exist_ok=True)
    out.save(path)


def main():
    l0, l1, l2 = layer0(), layer1(), layer2()
    validate(l2)
    write_xp(OUT_XP, [l0, l1, l2])
    print("wrote", OUT_XP.relative_to(REPO))
    preview(PREVIEW_DIR / "ufo.png")
    print("wrote", (PREVIEW_DIR / "ufo.png").relative_to(REPO))


if __name__ == "__main__":
    main()
