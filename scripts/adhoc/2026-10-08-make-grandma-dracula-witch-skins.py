#!/usr/bin/env python3
"""
2026-10-08 -- Author Grandma, Dracula and Witch base skin sheets (idle/walk + death)

Outputs (assets/sprites/):
    2026-10-08-<char>-body.xp     from player-body.xp   (idle/walk)
    2026-10-08-<char>-plydie.xp   from plydie-body.xp   (death)
    for <char> in grandma, dracula, witch
Previews (docs/previews/spooky-skins/):
    <char>-body.png, <char>-plydie.png  contact sheets of every frame
    overview.png                        front idle frame vs. reference image

Deterministic: reads only the two templates (+ the CP437 font and, for the
overview, the reference images if present) and rewrites every output.

TEMPLATE FACTS (verified, see engine/sprite.cpp LoadSpriteLayer)
  * Layer 0 = atlas metadata.  Cell (0,0) digit = angle count, cells (a,0)
    a>=1 = animation lengths.  Its *background colour* (255,255,85) is the
    colour key: any layer-2 fg or bg equal to it is transparent.  Magenta bg
    (255,0,255) makes the whole cell transparent.  Cyan (0,255,255) means
    "swoosh" -> never used here.
  * Layer 1 = per-cell height digits.  Layers 0 and 1 are copied untouched.
  * Layer 2 = visual layer, the only layer edited.
  * Columns = 2 projections x sum(anim lengths); first half of the columns
    is the normal projection, second half the water reflection.  Rows = angles.
      player-body.xp 126x72, L0 "8","1","8": 8 angles, anims [1 idle, 8 walk]
                     -> 18 x 8 frames of 7x9; reflection == exact vertical
                     flip of the projection (glyph map 220<->223, v->^ ...).
      plydie-body.xp 110x88, L0 "8","5": 8 angles, 1 anim of 5 frames
                     -> 10 x 8 frames of 11x11; reflections are hand drawn
                     (NOT a flip) so they are painted with the same rules.
  * Rows (angles): 0 front, 1 and 7 front diagonals, 2 and 6 profiles,
    3 and 5 back diagonals, 4 back.

REGION LOGIC (why this is not a row-range recolor)
  The 2026-05-28 Wallace script recoloured by fixed row bands (0-2 head,
  3-5 torso, 6-8 legs); hands in the torso band got the vest colour and
  vanished, and head side half-cells (ears/hair) were wiped.  Here every
  frame is decoded into a 2x2-subpixel grid (half-block glyphs give two
  "pixels" per cell axis) and each subpixel is classified from the template:
    TORSO P(170,0,170)  LEGL b(85,85,255)  LEGD B(0,0,170)  SHOE n(170,85,0)
    black  -> HAIR, or SOLE when its 4-connected component touches shoes/legs
              and not skin
    skin R(255,85,85) -> HEAD when its component holds a face glyph (eyes,
              mouth, nose) or touches HAIR, otherwise HAND (arms).  Cells with
              finger glyphs 240/19 are always HAND.
  Per frame an "up" axis u = head centroid - torso centroid (snapped) and a
  head anchor (eye cell, else face-glyph cell, else fullest hair cell) are
  computed, so lying/falling death frames and upside-down reflections get the
  hat/curlers on the correct side of the head.  Stamps are placed at
  crown = anchor+u, sides = anchor+-p (p perpendicular), neck = anchor-2u.
  Hands always get a bright arm colour that contrasts with the torso.

PALETTE (all on the engine's 6-level colour cube; NO yellow anywhere --
  revision 2 removed every yellow because it reads as the (255,255,85) key)
  Grandma: hair (51,51,51); curlers club (255,102,204) / heart (255,51,51) on
           hair at crown+-p; pink swirl curls 0x15 at hair-side cells; face
           (255,153,51), eyes black, mouth 'u' (204,0,0); collar (153,0,204);
           blouse (255,255,255) with shoulder band (204,102,153) and ':'
           buttons; arms rose sleeve (255,51,102) + skin hand (255,153,51);
           mint (102,255,204) / teal (0,204,153) dress, thigh gap closed.
  Dracula: hood (0,0,0), face (51,0,51), red figure-eight eyes 0xEC on every
           visible face (eyes 34 and dead 'X' 88), white fangs 'v'/'^'
           (stamped under the eyes when the template has no mouth glyph);
           crimson (153,0,0) high collar: outer halves of the head side cells
           plus collar points rising beside the crown; red '\' '/' shoulder
           collars on cape (51,0,51) cells beside the chin (front views,
           plain cape cells elsewhere); tunic (153,0,153) with red clasp,
           cape (51,0,51) on the outer edge of every torso line with a red
           (204,0,0) lining just inside; legs = cape colour with the gap
           closed (one cloaked shape); back views a full dark cape; arms
           dark purple sleeve (102,51,153) + pale hand (204,204,255).
  Witch:   lilac (204,153,255) hat: triangle 0x1E at crown, brim 0x16 cells
           at sides, band (51,0,51) with buckle 0x07 over the eyes; salmon face
           (255,153,102) with red 'A' mouth; (102,0,153) robe with black club
           0x05 at chest; sleeves striped salmon (255,102,102) / purple
           (153,102,255); magenta dither skirt (0xB1 (255,51,255) on
           (102,0,102)) with the thigh gap closed down to the hem.
  ARMS: a lone half-cell arm is widened inside its own cell (sleeve toward
  the shoulder, hand toward the end); longer arms get hand on the end cell
  and sleeve above; arm cells shared with the torso use the high-contrast
  ARM_SOLO colour.  The self-check requires one arm colour per frame that is
  >= 100 RGB away from torso, dark terrain and sand terrain.
  DRESS / CLOAK (grandma, witch, dracula): fill_dress() closes the space
  between the legs from the top of the legs to the hem with a monotone
  A-line flare (covers stride poses), in frame-local axes so death frames
  get it too; only transparent subpixels are filled.
  Silhouette edits (cells that were transparent in the template):
    grandma curler cells, dress fill (grandma, witch, dracula), dracula
    collar points and shoulder collars.  All are checked by the self-check.
"""
import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "pipeline"))
from xp_core import XPFile  # noqa: E402

SPRITES = REPO / "assets" / "sprites"
PREVIEWS = REPO / "docs" / "previews" / "spooky-skins"
FONT = REPO / "assets" / "fonts" / "cp437_20x20.png"
REF_DIR = Path("/root/.claude/uploads/402fafb3-5f47-5416-acc6-2bc40c5fd371")
REFS = {"grandma": "6c809286-image.png", "dracula": "3d76cdb3-image.png",
        "witch": "5f6610cc-image.png"}
DATE = "2026-10-08"

MAGENTA = (255, 0, 255)
T_SKIN, T_SHIRT = (255, 85, 85), (170, 0, 170)
T_LEGL, T_LEGD = (85, 85, 255), (0, 0, 170)
T_SHOE, T_BLACK, T_MOUTH, T_GRAY = (170, 85, 0), (0, 0, 0), (170, 0, 0), (85, 85, 85)

BLOCK = {32, 0, 255, 219, 220, 221, 222, 223}
EYE_GLYPHS = {34, 96, 39, 88, 46}
FACE_GLYPHS = EYE_GLYPHS | {118, 94, 192, 218, 217, 191}
HAND_GLYPHS = {240, 19}
FRONT_ROWS, SIDE_ROWS, BACK_ROWS = {0, 1, 7}, {2, 6}, {3, 4, 5}

# classes
HAIR, SOLE, HEAD, HAND, TORSO, LEGL, LEGD, SHOE, GRAY, OTHER, SKIN, BLACK = (
    "HAIR", "SOLE", "HEAD", "HAND", "TORSO", "LEGL", "LEGD", "SHOE", "GRAY",
    "OTHER", "SKIN", "BLACK")
COLOR_CLASS = {T_SKIN: SKIN, T_SHIRT: TORSO, T_LEGL: LEGL, T_LEGD: LEGD,
               T_SHOE: SHOE, T_BLACK: BLACK, T_GRAY: GRAY}
LEG = (LEGL, LEGD)

CHARS = {
    "grandma": dict(
        HAIR=(51, 51, 51), SOLE=(0, 0, 0), HEAD=(255, 153, 51),
        TORSO=(255, 255, 255), BAND=(204, 102, 153), NECK=(153, 0, 204),
        LEGL=(102, 255, 204), LEGD=(0, 204, 153), DRESS=LEGL, SHOE=(153, 51, 0),
        SLEEVE=(255, 51, 102), HANDC=(255, 153, 51), ARM_SOLO=(255, 51, 102),
        EYE=(0, 0, 0), MOUTH=(204, 0, 0), MOUTH_G={118: 117, 94: 110},
        CHEST=(58, (153, 0, 204)), CURL=(255, 102, 204), HEART=(255, 51, 51)),
    "dracula": dict(
        HAIR=(0, 0, 0), SOLE=(0, 0, 0), HEAD=(51, 0, 51),
        TORSO=(153, 0, 153), BACK_TORSO=(51, 0, 51), NECK=(204, 0, 0),
        CAPE=(51, 0, 51), LINING=(204, 0, 0), COLLAR=(153, 0, 0),
        LEGL=(51, 0, 51), LEGD=(51, 0, 51), DRESS=LEGD, SHOE=(153, 51, 0),
        SLEEVE=(102, 51, 153), HANDC=(204, 204, 255), ARM_SOLO=(204, 204, 255),
        EYE=(255, 0, 0), EYE_G={34: 236, 88: 236},
        MOUTH=(255, 255, 255), MOUTH_G={},
        CHEST=(179, (255, 0, 0)), RED=(255, 0, 0)),
    "witch": dict(
        HAIR=(51, 0, 51), SOLE=(0, 0, 0), HEAD=(255, 153, 102),
        TORSO=(102, 0, 153), NECK=(255, 153, 102),
        LEGL=(153, 0, 153), LEGD=(102, 0, 102), DRESS=LEGD, SHOE=(153, 51, 0),
        SLEEVE=(153, 102, 255), HANDC=(255, 102, 102), ARM_SOLO=(153, 102, 255),
        STRIPES=((255, 102, 102), (153, 102, 255)), EYE=(51, 0, 51),
        MOUTH=(204, 0, 0), MOUTH_G={118: 65, 94: 86},
        CHEST=(5, (0, 0, 0)), LILAC=(204, 153, 255), BANDC=(51, 0, 51),
        SKIRT_FG=(255, 51, 255), SKIRT_BG=(102, 0, 102)),
}
for _p in CHARS.values():
    _p["ARM_COLS"] = {_p["SLEEVE"], _p["HANDC"], _p["ARM_SOLO"]} | set(_p.get("STRIPES", ()))

# vertical flip glyph map (template-derived + new glyphs used here)
FLIP_G = {220: 223, 223: 220, 118: 94, 94: 118, 96: 46, 39: 46, 192: 218,
          218: 192, 217: 191, 191: 217, 117: 110, 110: 117, 65: 86, 86: 65,
          30: 31, 31: 30, 92: 47, 47: 92}


# ---------------------------------------------------------------- layout
def sheet_layout(xp):
    """Return (angles, frames_per_projection, fw, fh) decoded like sprite.cpp."""
    l0 = xp.layers[0]

    def digit(c):
        g = c[0]
        return g - 48 if 48 <= g <= 57 else 0
    angles = digit(l0.data[0][0])
    s = 0
    for a in range(1, l0.width):
        n = digit(l0.data[0][a])
        if n <= 0:
            break
        s += n
    cols = 2 * s
    return angles, s, l0.width // cols, l0.height // angles


# ---------------------------------------------------------------- decode
def cell_sub(cell, key):
    g, f, b = cell
    if b == MAGENTA:
        return [None] * 4, False
    F = None if f == key else f
    B = None if b == key else b
    if g in (32, 0, 255):
        return [B, B, B, B], True
    if g == 219:
        return [F, F, F, F], True
    if g == 220:
        return [B, B, F, F], True
    if g == 223:
        return [F, F, B, B], True
    if g == 221:
        return [F, B, F, B], True
    if g == 222:
        return [B, F, B, F], True
    return [B, B, B, B], False  # feature glyph: bg fills the cell


class Frame:
    """Decoded frame: subpixel colour/class grid + per-cell glyph info."""

    def __init__(self, xp, x0, y0, fw, fh, row):
        self.x0, self.y0, self.fw, self.fh, self.row = x0, y0, fw, fh, row
        L, K = xp.layers[2], xp.layers[0]
        self.cells = [[L.data[y0 + y][x0 + x] for x in range(fw)] for y in range(fh)]
        self.keys = [[K.data[y0 + y][x0 + x][2] for x in range(fw)] for y in range(fh)]
        W, H = 2 * fw, 2 * fh
        self.W, self.H = W, H
        self.col = [[None] * W for _ in range(H)]
        self.feature = [[False] * fw for _ in range(fh)]
        for y in range(fh):
            for x in range(fw):
                s, blk = cell_sub(self.cells[y][x], self.keys[y][x])
                self.feature[y][x] = (not blk) and self.cells[y][x][2] != MAGENTA
                for j in range(2):
                    for i in range(2):
                        self.col[2 * y + j][2 * x + i] = s[2 * j + i]
        self.classify()

    def glyph(self, x, y):
        return self.cells[y][x][0]

    def neigh(self, x, y):
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < self.W and 0 <= ny < self.H:
                yield nx, ny

    def comps(self, cls):
        seen, out = set(), []
        for y in range(self.H):
            for x in range(self.W):
                if self.cls[y][x] == cls and (x, y) not in seen:
                    st, comp = [(x, y)], []
                    seen.add((x, y))
                    while st:
                        p = st.pop()
                        comp.append(p)
                        for q in self.neigh(*p):
                            if q not in seen and self.cls[q[1]][q[0]] == cls:
                                seen.add(q)
                                st.append(q)
                    out.append(comp)
        return out

    def classify(self):
        self.cls = [[None if c is None else COLOR_CLASS.get(c, OTHER) for c in r]
                    for r in self.col]
        face_cells = {(x, y) for y in range(self.fh) for x in range(self.fw)
                      if self.feature[y][x] and self.glyph(x, y) in FACE_GLYPHS
                      and self.cells[y][x][2] == T_SKIN}
        hand_cells = {(x, y) for y in range(self.fh) for x in range(self.fw)
                      if self.feature[y][x] and self.glyph(x, y) in HAND_GLYPHS}
        self.face_cells = face_cells
        # black -> HAIR / SOLE
        for comp in self.comps(BLACK):
            touch = set()
            for (x, y) in comp:
                if (x // 2, y // 2) in face_cells:
                    touch.add(SKIN)
                for nx, ny in self.neigh(x, y):
                    touch.add(self.cls[ny][nx])
            sole = (SKIN not in touch) and (SHOE in touch or LEGL in touch or LEGD in touch)
            for (x, y) in comp:
                self.cls[y][x] = SOLE if sole else HAIR
        # skin -> HEAD / HAND
        for comp in self.comps(SKIN):
            head = False
            for (x, y) in comp:
                if (x // 2, y // 2) in face_cells:
                    head = True
                for nx, ny in self.neigh(x, y):
                    if self.cls[ny][nx] == HAIR:
                        head = True
            for (x, y) in comp:
                c = (x // 2, y // 2)
                self.cls[y][x] = HAND if (c in hand_cells or not head) else HEAD
        # geometry
        def centroid(classes):
            pts = [(x, y) for y in range(self.H) for x in range(self.W)
                   if self.cls[y][x] in classes]
            if not pts:
                return None
            return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        self.head_c = centroid((HEAD, HAIR))
        self.torso_c = centroid((TORSO,)) or centroid((LEGL, LEGD, SHOE))
        self.u = (0, -1)
        if self.head_c and self.torso_c:
            dx = self.head_c[0] - self.torso_c[0]
            dy = self.head_c[1] - self.torso_c[1]
            if abs(dx) > abs(dy):
                self.u = (1 if dx > 0 else -1, 0)
            else:
                self.u = (0, 1 if dy > 0 else -1)
        ux, uy = self.u
        self.p = (-uy, ux)
        self.anchor = self.find_anchor()

    def cell_classes(self, x, y):
        return [self.cls[2 * y + j][2 * x + i] for j in range(2) for i in range(2)]

    def find_anchor(self):
        if not self.head_c:
            return None
        hx, hy = self.head_c[0] / 2, self.head_c[1] / 2

        def d(c):
            return (c[0] + .5 - hx) ** 2 + (c[1] + .5 - hy) ** 2
        eyes = [c for c in self.face_cells if self.glyph(*c) in EYE_GLYPHS]
        if eyes:
            return min(eyes, key=d), "eye"
        if self.face_cells:
            return min(self.face_cells, key=d), "face"
        hair = [(x, y) for y in range(self.fh) for x in range(self.fw)
                if HAIR in self.cell_classes(x, y)]
        if not hair:
            return None
        return min(hair, key=lambda c: (-self.cell_classes(*c).count(HAIR), d(c))), "hair"

    def inside(self, c):
        return 0 <= c[0] < self.fw and 0 <= c[1] < self.fh


# ---------------------------------------------------------------- paint
def sub_cells(c):
    return [(2 * c[0] + i, 2 * c[1] + j) for j in range(2) for i in range(2)]


def fill_dress(fr, fill_cls):
    """Dress / cloak: close the gap between the legs from the top of the legs
    down to the hem (the lowest leg line), with a monotone A-line flare so
    stride poses are covered too; only transparent subpixels are filled, so
    shoes, hands and torso stay.  Works in frame-local axes (t = distance
    "down" = -u, s = along p) so falling/lying death frames are handled.
    Returns the set of filled subpixels."""
    ux, uy = fr.u
    px, py = fr.p
    lines = {}
    for y in range(fr.H):
        for x in range(fr.W):
            if fr.cls[y][x] in LEG:
                t, s = -(x * ux + y * uy), x * px + y * py
                lines.setdefault(t, []).append(s)
    filled = set()
    if not lines:
        return filled
    lo = hi = None
    for t in range(min(lines), max(lines) + 1):
        ss = lines.get(t)
        if ss:
            lo = min(ss) if lo is None else min(lo, min(ss))
            hi = max(ss) if hi is None else max(hi, max(ss))
        if lo is None:
            continue
        for s in range(lo, hi + 1):
            x, y = -t * ux + s * px, -t * uy + s * py
            if 0 <= x < fr.W and 0 <= y < fr.H and fr.cls[y][x] is None:
                fr.cls[y][x] = fill_cls
                filled.add((x, y))
    # flare inside cells that only hold dress: cell already visible
    for cy in range(fr.fh):
        for cx in range(fr.fw):
            cc = set(fr.cell_classes(cx, cy))
            if cc & set(LEG) and cc <= {None, LEGL, LEGD}:
                for sx, sy in sub_cells((cx, cy)):
                    if fr.cls[sy][sx] is None:
                        fr.cls[sy][sx] = fill_cls
                        filled.add((sx, sy))
    return filled


def cape_sides(fr, out, pal, classes, lining_min):
    """Dracula: on every line across the body, the outermost body subpixel on
    each side becomes cape, the next one inward the red lining."""
    ux, uy = fr.u
    px, py = fr.p
    lines = {}
    for y in range(fr.H):
        for x in range(fr.W):
            if fr.cls[y][x] in classes:
                lines.setdefault(-(x * ux + y * uy), []).append((x * px + y * py, x, y))
    for t, pts in lines.items():
        pts.sort()
        n = len(pts)
        if n >= 3:
            for k in (0, n - 1):
                out[pts[k][2]][pts[k][1]] = pal["CAPE"]
        if n >= lining_min:
            for k in (1, n - 2):
                out[pts[k][2]][pts[k][1]] = pal["LINING"]


def paint_frame(fr, name, pal):
    """Return dict of output cells {(x,y): (glyph, fg, bg)} for the frame."""
    back = fr.row in BACK_ROWS
    front = fr.row in FRONT_ROWS
    if "DRESS" in pal:
        fill_dress(fr, pal["DRESS"])
    ux, uy = fr.u
    # --- per-subpixel colour
    out = [[None] * fr.W for _ in range(fr.H)]
    tor = [(x, y) for y in range(fr.H) for x in range(fr.W) if fr.cls[y][x] == TORSO]
    tmax = max((x * ux + y * uy for x, y in tor), default=0)
    for y in range(fr.H):
        for x in range(fr.W):
            c = fr.cls[y][x]
            if c is None:
                continue
            if c == TORSO:
                col = pal.get("BACK_TORSO", pal["TORSO"]) if back else pal["TORSO"]
                if "BAND" in pal and x * ux + y * uy >= tmax - 1:
                    col = pal["BAND"]
            elif c == HEAD:
                # neck: head skin sharing a cell with torso
                cc = fr.cell_classes(x // 2, y // 2)
                col = pal["NECK"] if TORSO in cc else pal["HEAD"]
            elif c == HAND:
                col = pal["ARM_SOLO"]
            elif c in (GRAY, OTHER):
                col = fr.col[y][x]
            else:
                col = pal[c]
            out[y][x] = col
    if name == "dracula" and not back:
        cape_sides(fr, out, pal, (TORSO,), 6)
        cape_sides(fr, out, pal, LEG, 99)  # cloak below the waist: no lining
    # --- arms: sleeve + hand, ordered from the hand end (lowest along u)
    explicit = {}
    for comp in fr.comps(HAND):
        hc = sorted({(x // 2, y // 2) for x, y in comp},
                    key=lambda c: (c[0] * ux + c[1] * uy))
        if len(hc) == 1:
            c = hc[0]
            if set(fr.cell_classes(*c)) <= {HAND, None} and not fr.feature[c[1]][c[0]]:
                # widen the lone arm cell: sleeve toward the shoulder, hand
                # toward the end (cell was already visible -> mask unchanged)
                for sx, sy in sub_cells(c):
                    up_half = (sx % 2 if ux == 1 else 1 - sx % 2) if ux else \
                              (1 - sy % 2 if uy == -1 else sy % 2)
                    out[sy][sx] = pal["SLEEVE"] if up_half else pal["HANDC"]
                    fr.cls[sy][sx] = HAND
            else:
                for sx, sy in sub_cells(c):
                    if fr.cls[sy][sx] == HAND:
                        out[sy][sx] = pal["ARM_SOLO"]
            continue
        for k, c in enumerate(hc):
            if "STRIPES" in pal:
                col = pal["STRIPES"][k % 2]
            else:
                col = pal["HANDC"] if k == 0 else pal["SLEEVE"]
            for sx, sy in sub_cells(c):
                if fr.cls[sy][sx] == HAND:
                    out[sy][sx] = col
    # --- encode cells
    cells = {}
    for y in range(fr.fh):
        for x in range(fr.fw):
            cells[(x, y)] = encode_cell(fr, x, y, out, name, pal)
    # --- stamps
    stamp_head(fr, name, pal, out, explicit, cells)
    stamp_torso(fr, name, pal, out, explicit, front)
    cells.update(explicit)
    return cells


def encode_cell(fr, x, y, out, name, pal):
    tg, tf, tb = fr.cells[y][x]
    key = fr.keys[y][x]
    sub = [out[2 * y + j][2 * x + i] for j in range(2) for i in range(2)]
    cls = fr.cell_classes(x, y)
    if all(s is None for s in sub):
        return (32, (0, 0, 0), MAGENTA)
    dither = name == "witch" and all(c in LEG for c in cls)
    if dither:
        return (177, pal["SKIRT_FG"], pal["SKIRT_BG"])
    if fr.feature[y][x]:
        bg = sub[0] if sub[0] is not None else key
        g = tg
        fgc = tf
        if tb == T_SKIN and tg in FACE_GLYPHS:
            if tg in EYE_GLYPHS:
                fgc = pal["EYE"]
                g = pal.get("EYE_G", {}).get(tg, tg)
            else:
                fgc = pal["MOUTH"]
                g = pal["MOUTH_G"].get(tg, tg)
        elif tf in COLOR_CLASS:
            k = COLOR_CLASS[tf]
            fgc = {SKIN: pal["HEAD"], BLACK: (0, 0, 0), TORSO: pal["TORSO"],
                   GRAY: T_GRAY}.get(k, pal.get(k, tf))
        if tf == T_MOUTH:
            fgc = pal["MOUTH"]
        return (g, fgc, bg)
    return encode_block(tg, sub, key)


def encode_block(tg, sub, key):
    tl, tr, bl, br = [key if s is None else s for s in sub]
    opts = []
    for g in ([tg] if tg in (220, 223, 221, 222) else []) + [220, 223, 221, 222]:
        if g == 220:
            fg, bg = bl, tl
            pred = [bg, bg, fg, fg]
        elif g == 223:
            fg, bg = tl, bl
            pred = [fg, fg, bg, bg]
        elif g == 221:
            fg, bg = tl, tr
            pred = [fg, bg, fg, bg]
        else:
            fg, bg = tr, tl
            pred = [bg, fg, bg, fg]
        miss = sum(a != b for a, b in zip(pred, [tl, tr, bl, br]))
        opts.append((miss, g, fg, bg))
    if tl == tr == bl == br:
        g = tg if tg in (219, 220, 221, 222, 223) else 219
        if tl == key:
            return (32, (0, 0, 0), MAGENTA)
        return (g, tl, tl)
    miss, g, fg, bg = min(opts, key=lambda o: o[0])
    if fg == key and bg == key:
        return (32, (0, 0, 0), MAGENTA)
    return (g, fg, bg)


def oriented(u, up, down, left, right):
    return {(0, -1): up, (0, 1): down, (-1, 0): left, (1, 0): right}[u]


def stamp_head(fr, name, pal, out, explicit, cells):
    if not fr.anchor:
        return
    (ax, ay), kind = fr.anchor
    ux, uy = fr.u
    px, py = fr.p
    crown = (ax + ux, ay + uy)
    sides = [(ax - px, ay - py), (ax + px, ay + py)]
    head_ok = {None, HAIR, HEAD}

    def classes(c):
        return set(fr.cell_classes(*c)) if fr.inside(c) else {"OUT"}

    def empty(c):
        return fr.inside(c) and classes(c) == {None} and c not in explicit

    def key_of(c):
        return fr.keys[c[1]][c[0]]

    crown_ok = fr.inside(crown) and classes(crown) <= head_ok
    if name == "grandma":
        if crown_ok:
            explicit[crown] = (219, pal["HAIR"], pal["HAIR"])
            for c, gl, col in ((crown[0] - px, crown[1] - py), 5, pal["CURL"]), \
                              ((crown[0] + px, crown[1] + py), 3, pal["HEART"]):
                if empty(c):
                    explicit[c] = (gl, col, pal["HAIR"])
        for s in sides:
            cc = classes(s)
            if fr.inside(s) and HAIR in cc and HEAD not in cc and cc <= head_ok:
                explicit[s] = (21, pal["CURL"], pal["HAIR"])
    elif name == "witch":
        if crown_ok:
            g = oriented(fr.u, 30, 31, 17, 16)
            explicit[crown] = (g, pal["LILAC"], key_of(crown))
        brim_g = 22 if ux == 0 else 179
        for s in sides:
            cc = classes(s)
            if fr.inside(s) and cc <= head_ok and cc != {None}:
                explicit[s] = (brim_g, pal["BANDC"], pal["LILAC"])
        if kind in ("eye", "hair"):
            eg = 88 if fr.glyph(ax, ay) == 88 else 7
            explicit[(ax, ay)] = (eg, pal["LILAC"], pal["BANDC"])
    elif name == "dracula":
        a_s = ax * px + ay * py
        # high collar: transparent halves of the head side cells -> crimson
        for s in sides:
            cc = classes(s)
            if fr.inside(s) and cc <= head_ok and None in cc and cc != {None}:
                sub = [pal["COLLAR"] if out[sy][sx] is None else out[sy][sx]
                       for sx, sy in sub_cells(s)]
                explicit[s] = encode_block(fr.glyph(*s), sub, key_of(s))
        # collar points rising above: outer half of the cells beside the crown
        for sg in (-1, 1):
            c = (crown[0] + sg * px, crown[1] + sg * py)
            if empty(c):
                sc = 2 * a_s + 0.5  # anchor centre along p, in subpixels
                ds = [abs((sx * px + sy * py) - sc) for sx, sy in sub_cells(c)]
                sub = [pal["COLLAR"] if d == max(ds) else None for d in ds]
                explicit[c] = encode_block(32, sub, key_of(c))
        # shoulder collars beside the chin (one row below the eyes)
        chin = (ax - ux, ay - uy)
        for sg in (-1, 1):
            c = (chin[0] + sg * px, chin[1] + sg * py)
            if empty(c):
                if fr.row in FRONT_ROWS and ux == 0:
                    left = c[0] < chin[0]
                    g = (92 if left else 47) if uy == -1 else (47 if left else 92)
                    explicit[c] = (g, pal["RED"], pal["CAPE"])
                else:
                    explicit[c] = (219, pal["CAPE"], pal["CAPE"])
        # fangs whenever the eyes are visible but no mouth glyph is drawn
        if kind == "eye" and fr.inside(chin) and not fr.feature[chin[1]][chin[0]] \
                and set(fr.cell_classes(*chin)) == {HEAD}:
            explicit[chin] = (oriented(fr.u, 118, 94, 60, 62), pal["MOUTH"], pal["HEAD"])


def stamp_torso(fr, name, pal, out, explicit, front):
    if not fr.anchor or not front:
        return
    (ax, ay), kind = fr.anchor
    ux, uy = fr.u
    px, py = fr.p

    def full_torso(c):
        return fr.inside(c) and not fr.feature[c[1]][c[0]] and \
            fr.cell_classes(*c) == [TORSO] * 4 and c not in explicit and \
            len({out[sy][sx] for sx, sy in sub_cells(c)}) == 1
    cand = [(x, y) for y in range(fr.fh) for x in range(fr.fw) if full_torso((x, y))]
    if not cand or not fr.torso_c:
        return
    tx, ty = fr.torso_c[0] / 2, fr.torso_c[1] / 2

    def score(c):
        perp = abs((c[0] - ax) * px + (c[1] - ay) * py)
        return (perp, (c[0] + .5 - tx) ** 2 + (c[1] + .5 - ty) ** 2)
    c = min(cand, key=score)
    g, fgc = pal["CHEST"]
    if ux != 0:
        g = {58: 249, 179: 196}.get(g, g)
    bg = out[2 * c[1]][2 * c[0]]
    explicit[c] = (g, fgc, bg)


# ---------------------------------------------------------------- sheets
def flip_cell(cell):
    g, f, b = cell
    return (FLIP_G.get(g, g), f, b)


def build_sheet(template, name, refl_mode):
    xp = XPFile()
    xp.load(str(SPRITES / template))
    angles, nfr, fw, fh = sheet_layout(xp)
    L = xp.layers[2]
    stats = {"frames": 0}
    for r in range(angles):
        for f in range(nfr):
            fr = Frame(xp_template(template), f * fw, r * fh, fw, fh, r)
            cells = paint_frame(fr, name, CHARS[name])
            for (x, y), cell in cells.items():
                L.data[r * fh + y][f * fw + x] = cell
            stats["frames"] += 1
    for r in range(angles):
        for f in range(nfr, 2 * nfr):
            if refl_mode == "flip":
                src = f - nfr
                for y in range(fh):
                    for x in range(fw):
                        L.data[r * fh + (fh - 1 - y)][f * fw + x] = \
                            flip_cell(L.data[r * fh + y][src * fw + x])
            else:
                fr = Frame(xp_template(template), f * fw, r * fh, fw, fh, r)
                cells = paint_frame(fr, name, CHARS[name])
                for (x, y), cell in cells.items():
                    L.data[r * fh + y][f * fw + x] = cell
            stats["frames"] += 1
    return xp, (angles, nfr, fw, fh)


_TPL = {}


def xp_template(template):
    if template not in _TPL:
        t = XPFile()
        t.load(str(SPRITES / template))
        _TPL[template] = t
    return _TPL[template]


# ---------------------------------------------------------------- preview
def q(c):
    return tuple(((v * 5 + 128) // 255) * 51 for v in c)


def render_cells(xp, x0, y0, w, h, font, C, bgc):
    from PIL import Image
    L, K = xp.layers[2], xp.layers[0]
    im = Image.new("RGB", (w * C, h * C), bgc)
    for y in range(h):
        for x in range(w):
            g, f, b = L.data[y0 + y][x0 + x]
            key = K.data[y0 + y][x0 + x][2]
            if b == MAGENTA:
                continue
            gm = font.crop(((g % 16) * C, (g // 16) * C, (g % 16 + 1) * C, (g // 16 + 1) * C))
            if b != key:
                im.paste(q(b), (x * C, y * C, (x + 1) * C, (y + 1) * C))
            if f != key:
                im.paste(q(f), (x * C, y * C), gm)
    return im


def contact_sheet(xp, layout, title, out_path, kind):
    from PIL import Image, ImageDraw, ImageFont
    angles, nfr, fw, fh = layout
    C = 20
    font = Image.open(FONT).convert("L")
    try:
        lf = ImageFont.truetype("DejaVuSans.ttf", 18)
    except OSError:
        lf = ImageFont.load_default()
    dirs = ["0 front", "1 front-diag", "2 profile", "3 back-diag", "4 back",
            "5 back-diag", "6 profile", "7 front-diag"]
    if kind == "body":
        colnames = ["idle"] + ["walk%d" % i for i in range(1, nfr)]
    else:
        colnames = ["f%d" % i for i in range(nfr)]
    colnames = colnames + ["R:" + c for c in colnames]
    pad, lw, th = 6, 130, 60
    cw, ch = fw * C + pad, fh * C + pad
    W = lw + cw * 2 * nfr + 20
    H = th + 26 + ch * angles + 10
    im = Image.new("RGB", (W, H), (24, 24, 30))
    d = ImageDraw.Draw(im)
    d.text((10, 10), title + "   (R: = water-reflection half; colours quantized like the engine)",
           fill=(230, 230, 230), font=lf)
    for ci, cn in enumerate(colnames):
        d.text((lw + ci * cw + 4, th), cn, fill=(200, 200, 120), font=lf)
    for r in range(angles):
        y = th + 26 + r * ch
        d.text((6, y + ch // 2 - 10), dirs[r] if r < len(dirs) else str(r),
               fill=(200, 200, 120), font=lf)
        for f in range(2 * nfr):
            x = lw + f * cw
            bgc = (70, 78, 70) if f < nfr else (50, 60, 80)
            fim = render_cells(xp, f * fw, r * fh, fw, fh, font, C, bgc)
            im.paste(fim, (x, y))
            d.rectangle([x - 1, y - 1, x + fw * C, y + fh * C], outline=(110, 110, 160))
    im.save(out_path)


def overview(results, out_path):
    from PIL import Image, ImageDraw, ImageFont
    font = Image.open(REPO / "assets" / "fonts" / "cp437_40x40.png").convert("L")
    try:
        lf = ImageFont.truetype("DejaVuSans.ttf", 22)
    except OSError:
        lf = ImageFont.load_default()
    panels = []
    for name, xp in results:
        spr = render_cells(xp, 0, 0, 7, 9, font, 40, (70, 78, 70))
        ref_p = REF_DIR / REFS[name]
        if ref_p.exists():
            ref = Image.open(ref_p).convert("RGB")
            s = spr.height / ref.height
            ref = ref.resize((int(ref.width * s), spr.height), Image.NEAREST)
        else:
            ref = Image.new("RGB", (200, spr.height), (0, 0, 0))
        p = Image.new("RGB", (ref.width + spr.width + 30, spr.height + 40), (24, 24, 30))
        p.paste(ref, (0, 40))
        p.paste(spr, (ref.width + 30, 40))
        ImageDraw.Draw(p).text((4, 8), "%s: reference | front idle (7x9)" % name,
                               fill=(230, 230, 230), font=lf)
        panels.append(p)
    W = sum(p.width for p in panels) + 40 * (len(panels) + 1)
    H = max(p.height for p in panels) + 40
    im = Image.new("RGB", (W, H), (24, 24, 30))
    x = 40
    for p in panels:
        im.paste(p, (x, 20))
        x += p.width + 40
    im.save(out_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-previews", action="store_true")
    a = ap.parse_args()
    PREVIEWS.mkdir(parents=True, exist_ok=True)
    fronts = []
    for name in ("grandma", "dracula", "witch"):
        for tpl, suffix, mode in (("player-body.xp", "body", "flip"),
                                  ("plydie-body.xp", "plydie", "paint")):
            xp, layout = build_sheet(tpl, name, mode)
            dst = SPRITES / ("%s-%s-%s.xp" % (DATE, name, suffix))
            xp.save(str(dst))
            print("wrote", dst.relative_to(REPO), "layout", layout)
            if not a.no_previews:
                contact_sheet(xp, layout, "%s %s (template %s)" % (name, suffix, tpl),
                              PREVIEWS / ("%s-%s.png" % (name, suffix)), suffix)
            if suffix == "body":
                fronts.append((name, xp))
    if not a.no_previews:
        overview(fronts, PREVIEWS / "overview.png")
        print("previews in", PREVIEWS.relative_to(REPO))


if __name__ == "__main__":
    main()
