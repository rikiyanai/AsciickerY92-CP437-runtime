#!/usr/bin/env python3
"""
2026-10-08 -- Self-check for the grandma / dracula / witch base skin sheets.

For each of the six outputs vs. its template (player-body.xp / plydie-body.xp):
  1. same layer count and per-layer dimensions
  2. layers 0 and 1 byte-identical (raw decompressed cell bytes)
  3. fully-transparent cell mask identical, except documented silhouette
     edits whose colours are all in the character's ADDED palette (grandma
     curlers + dress fill, witch dither skirt fill, dracula collar points /
     shoulder collars / cloak fill).  No visible cell may become transparent.
  4. arms: every subpixel the template classifies as HAND is painted in the
     character's arm colours, and every side (left/right of the torso
     centroid) that has a hand in the template has an arm-coloured subpixel
     in the output; arm colours differ from the torso colour.
  5. head: every non-empty frame keeps a visible head (head/hair palette or
     a head stamp glyph) within the template head cells +-1.
  6. back view (row 4) has no eye glyphs; idle/walk reflection half equals
     the vertical flip of the projection half (as in the template).
  7. colour key safety, mirroring engine/sprite.cpp: the key is matched by
     exact RGB against layer-0 bg (LoadSpriteLayer bk_transp/fg_transp).
     Every non-key fg/bg actually drawn must (a) differ from the key and
     (b) not quantize to the key's palette index under any engine path:
     inline (c*5+128)/255 (projection), (c*5+128)/400 (reflection) and
     RGB2PAL (c+25)/51; (c) no drawn colour may be yellow (quantized r,g >=
     204 and b <= 153).
  8. arm contrast: every frame with hands uses at least one arm colour that
     is >= 100 RGB-distance from the torso, from dark terrain (51,51,51)
     and from sand terrain (204,153,102).  Dracula front views have the
     figure-eight eye glyph 236 whenever a face is visible.
Exit code 0 when everything passes.
"""
import gzip
import importlib.util
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "gen", REPO / "scripts/adhoc/2026-10-08-make-grandma-dracula-witch-skins.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)
XPFile = gen.XPFile
SP = gen.SPRITES


def raw_layers(path):
    b = gzip.open(path).read()
    _, n = struct.unpack_from("<iI", b, 0)
    off, out = 8, []
    for _ in range(n):
        w, h = struct.unpack_from("<ii", b, off)
        out.append(((w, h), b[off + 8: off + 8 + w * h * 10]))
        off += 8 + w * h * 10
    return out


def transparent(cell, key):
    g, f, b = cell
    return b == gen.MAGENTA or (b == key and (f == key or g in (32, 0, 255)))


ADDED = {
    "grandma": ("CURL", "HEART", "HAIR", "LEGL", "LEGD"),
    "witch": ("SKIRT_FG", "SKIRT_BG", "LEGL", "LEGD"),
    "dracula": ("COLLAR", "CAPE", "RED", "LEGL", "LEGD"),
}
DARK, SAND = (51, 51, 51), (204, 153, 102)


def qidx(c, f):
    return tuple(f(v) for v in c)


QUANT = {
    "inline/255": lambda v: (v * 5 + 128) // 255,
    "inline/400": lambda v: (v * 5 + 128) // 400,
    "RGB2PAL": lambda v: (v + 25) // 51,
}


def dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** .5


def drawn_colors(cell, key):
    g, f, b = cell
    if b == gen.MAGENTA:
        return []
    out = []
    if b != key:
        out.append(b)
    if f != key and g not in (32, 0, 255):
        out.append(f)
    return out


def load(p):
    x = XPFile()
    x.load(str(p))
    return x


def main():
    fails = []
    report = []
    for name in ("grandma", "dracula", "witch"):
        pal = gen.CHARS[name]
        arm_cols = pal["ARM_COLS"]
        added_cols = {pal[k] for k in ADDED[name]}
        torso = pal["TORSO"]
        for tpl, suf in (("player-body.xp", "body"), ("plydie-body.xp", "plydie")):
            out_p = SP / ("%s-%s-%s.xp" % (gen.DATE, name, suf))
            tag = out_p.name
            T, O = load(SP / tpl), load(out_p)
            # 1 + 2
            rt, ro = raw_layers(SP / tpl), raw_layers(out_p)
            if [d for d, _ in rt] != [d for d, _ in ro]:
                fails.append("%s: layer dims differ" % tag)
            for i in (0, 1):
                if rt[i][1] != ro[i][1]:
                    fails.append("%s: layer %d bytes differ" % tag)
            # 3
            angles, nfr, fw, fh = gen.sheet_layout(T)
            LT, LO, K = T.layers[2], O.layers[2], T.layers[0]
            added, lost, bad_added = 0, 0, 0
            for y in range(LT.height):
                for x in range(LT.width):
                    k = K.data[y][x][2]
                    tt, to = transparent(LT.data[y][x], k), transparent(LO.data[y][x], k)
                    if tt and not to:
                        added += 1
                        if not set(drawn_colors(LO.data[y][x], k)) <= added_cols:
                            bad_added += 1
                    elif to and not tt:
                        lost += 1
            if bad_added or lost:
                fails.append("%s: mask: %d undocumented added, %d lost" % (tag, bad_added, lost))
            # 7 key safety
            key_hits, yellow = 0, set()
            for y in range(LO.height):
                for x in range(LO.width):
                    k = K.data[y][x][2]
                    for c in drawn_colors(LO.data[y][x], k):
                        if c == k or any(qidx(c, fq) == qidx(k, fq) for fq in QUANT.values()):
                            key_hits += 1
                        qc = qidx(c, QUANT["RGB2PAL"])
                        if qc[0] >= 4 and qc[1] >= 4 and qc[2] <= 3:
                            yellow.add(c)
            if key_hits or yellow:
                fails.append("%s: %d key-colliding drawn colours, yellow %s" % (tag, key_hits, sorted(yellow)))
            # 4 + 5 + 6
            arm_frames = arm_bad_px = side_fail = head_fail = eye_back = 0
            contrast_fail = drac_eye_fail = 0
            hand_px = 0
            for r in range(angles):
                for f in range(2 * nfr):
                    fr = gen.Frame(T, f * fw, r * fh, fw, fh, r)
                    hands = [(x, y) for y in range(fr.H) for x in range(fr.W)
                             if fr.cls[y][x] == gen.HAND]
                    oc = [[None] * fr.W for _ in range(fr.H)]
                    for cy in range(fh):
                        for cx in range(fw):
                            s, _ = gen.cell_sub(LO.data[r * fh + cy][f * fw + cx],
                                                K.data[r * fh + cy][f * fw + cx][2])
                            for j in range(2):
                                for i in range(2):
                                    oc[2 * cy + j][2 * cx + i] = s[2 * j + i]
                    if hands:
                        arm_frames += 1
                        hand_px += len(hands)
                        used = {oc[y][x] for x, y in hands} & arm_cols
                        if not any(dist(c, torso) >= 100 and dist(c, DARK) >= 100 and
                                   dist(c, SAND) >= 100 for c in used):
                            contrast_fail += 1
                        bad = [(x, y) for x, y in hands if oc[y][x] not in arm_cols]
                        arm_bad_px += len(bad)
                        tcx = fr.torso_c[0] if fr.torso_c else fr.W / 2
                        tcy = fr.torso_c[1] if fr.torso_c else fr.H / 2
                        px, py = fr.p
                        for sgn in (-1, 1):
                            want = [h for h in hands
                                    if sgn * ((h[0] - tcx) * px + (h[1] - tcy) * py) > 0.5]
                            if want and not any(oc[y][x] in arm_cols for x, y in want):
                                side_fail += 1
                    heads = [(x // 2, y // 2) for y in range(fr.H) for x in range(fr.W)
                             if fr.cls[y][x] in (gen.HEAD, gen.HAIR)]
                    if heads:
                        okh = False
                        hc = {(cx + dx, cy + dy) for cx, cy in heads
                              for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
                        headset = {pal["HEAD"], pal["HAIR"], pal.get("LILAC"), pal.get("COLLAR")}
                        for cx, cy in hc:
                            if 0 <= cx < fw and 0 <= cy < fh:
                                g, fg_, bg_ = LO.data[r * fh + cy][f * fw + cx]
                                if fg_ in headset or bg_ in headset or g in (5, 3, 21, 30, 31, 16, 17):
                                    okh = True
                        if not okh:
                            head_fail += 1
                    if name == "dracula" and r in gen.FRONT_ROWS and any(
                            fr.glyph(*c) in (34, 88) for c in fr.face_cells):
                        if not any(LO.data[r * fh + cy][f * fw + cx][0] == 236
                                   for cy in range(fh) for cx in range(fw)):
                            drac_eye_fail += 1
                    if r == 4 and f < nfr:
                        for cy in range(fh):
                            for cx in range(fw):
                                if LO.data[r * fh + cy][f * fw + cx][0] in (34, 96, 39):
                                    eye_back += 1
            if torso in arm_cols:
                fails.append("%s: arm colour equals torso colour" % tag)
            if arm_bad_px or side_fail or head_fail or eye_back or contrast_fail or drac_eye_fail:
                fails.append("%s: arms bad px %d, side fails %d, contrast fails %d, head fails %d,"
                             " back eyes %d, dracula eye fails %d"
                             % (tag, arm_bad_px, side_fail, contrast_fail, head_fail, eye_back,
                                drac_eye_fail))
            flip_ok = "n/a"
            if suf == "body":
                def flip_matches(L):
                    for r in range(angles):
                        for f in range(nfr):
                            for y in range(fh):
                                for x in range(fw):
                                    a = gen.flip_cell(L.data[r * fh + y][f * fw + x])
                                    b = L.data[r * fh + fh - 1 - y][(f + nfr) * fw + x]
                                    if a != b:
                                        return False
                    return True
                flip_ok = "%s (template %s)" % (flip_matches(LO), flip_matches(LT))
                if not flip_matches(LO):
                    fails.append("%s: reflection is not the flip of projection" % tag)
            report.append("%-30s layout %dx%d frames of %dx%d | L0/L1 identical | mask +%d (documented) -%d"
                          " | hand frames %d, hand px %d, bad %d, side fails %d | head fails %d"
                          " | back eyes %d | refl flip %s | key hits %d, yellow %d | contrast fails %d"
                          % (tag, 2 * nfr, angles, fw, fh, added, lost, arm_frames, hand_px,
                             arm_bad_px, side_fail, head_fail, eye_back, flip_ok,
                             key_hits, len(yellow), contrast_fail))
    print("\n".join(report))
    if fails:
        print("\nFAIL")
        print("\n".join(fails))
        return 1
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
