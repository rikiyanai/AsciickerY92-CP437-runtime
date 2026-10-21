#!/usr/bin/env python3
"""author_tama_bridge_approaches.py [--measure] [--write] -- west (Kawasaki-side) approach spans of the Maruko road bridge
and the Tokyu railway bridge (FL-4876, 2026-10-17).

Operator 2026-10-17: the 2026-10-16 spans (author_tama_bridges.py) begin where the OSM bridge way begins, over the flood
plain, and the decks end in mid-air 8 to 9 m above the ground; the aerial shows both bridges continuing straight to the
Kawasaki levee.  The terrain now reaches the levee (apply_extend_west.sh).  This script authors the missing spans as new
ASCII-PLY .akm meshes (names end _20261017; an existing mesh is never overwritten) and the a3d_edit.py "add" records.

It reuses author_tama_bridges.py (read only): the AKM writer, the materials, the deck cross-sections maruko_deck and
tokyu_segment, the piers, the axes and the placement maths.  A span that descends is the level cross-section sheared
vertically, z' = z + k * (y - y_ref) (normals by the inverse transpose), so it keeps the identical profile and joins.

  --measure   print the terrain along each axis (live a3d) and the chosen spans; no files
  --write     write the meshes into the build's meshes/ and the edits to assess/live_fix_20261017/tama_farbank/approach_edits.json
FRAME as author_tama_bridges.py: mesh x right of travel, y along the axis, z above ZB (metres here, world units in the file).
"""
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import author_tama_bridges as B                      # noqa: E402  (read only)
from author_temple_meshes import Mesh, akm_text      # noqa: E402
import a3d_terrain_extend as T                       # noqa: E402
import numpy as np                                   # noqa: E402

CS = B.CS
ZB = B.ZB
TAG = "20261017"
MAP = B.BUILD / "output.a3d"
OUT = HERE / "assess/live_fix_20261017/tama_farbank/approach_edits.json"
BY = "sonnet bridge agent (approach spans)"
E0, N0 = 379699.09478405997, 3938417.876643479

_n, _r, _ex = T.read_terrain(MAP.read_bytes())
_H = {(int(r["x"]), int(r["y"])): r["height"].astype(float) for r in _ex if r["x"] < 0}


def ground_m(E, N):
    """Terrain surface height in metres (the live a3d, bilinear on the 2-unit vertex grid), None where there is no patch."""
    x, y = (E - E0) * CS + 48, (N - N0) * CS + 48
    px, py = math.floor(x / 8), math.floor(y / 8)
    p = _H.get((px, py))
    if p is None:
        return None
    fx, fy = (x - 8 * px) / 2.0, (y - 8 * py) / 2.0
    i, j = min(int(fx), 3), min(int(fy), 3)
    a, b = fx - i, fy - j
    raw = p[j, i] * (1 - a) * (1 - b) + p[j, i + 1] * a * (1 - b) + p[j + 1, i] * (1 - a) * b + p[j + 1, i + 1] * a * b
    return ZB + raw / (CS * 16.0)


class Frame:
    """Point of a bridge in the axis frame: s along the axis from the OSM node, lat = mesh x (right of travel)."""

    def __init__(self, wid, i0, i1, lat_c):
        self.O, self.d, self.brg = B.axis(wid, i0, i1)
        self.lat_c = lat_c
        self.right = (self.d[1], -self.d[0])
        self.left = (-self.d[1], self.d[0])

    def origin(self, s0):
        return (self.O[0] + self.d[0] * s0 + self.left[0] * self.lat_c, self.O[1] + self.d[1] * s0 + self.left[1] * self.lat_c)

    def point(self, s, x):
        e0, n0 = self.origin(0.0)
        return (e0 + self.d[0] * s + self.right[0] * x, n0 + self.d[1] * s + self.right[1] * x)

    def ground(self, s, x):
        return ground_m(*self.point(s, x))


def shear(m, k, y_ref):
    """z' = z + k (y - y_ref) on every vertex; normals by the inverse transpose (n' = (nx, ny - k nz, nz))."""
    o = Mesh()
    for (x, y, z, nx, ny, nz, r, g, b, al) in m.v:
        ny2 = ny - k * nz
        ln = math.sqrt(nx * nx + ny2 * ny2 + nz * nz) or 1.0
        o.v.append((x, y, z + k * (y - y_ref), nx / ln, ny2 / ln, nz / ln, r, g, b, al))
    o.f = [list(f) for f in m.f]
    return o


def shift_y(m, s0):
    o = Mesh()
    for (x, y, z, nx, ny, nz, r, g, b, al) in m.v:
        o.v.append((x, y - s0, z, nx, ny, nz, r, g, b, al))
    o.f = [list(f) for f in m.f]
    return o


def pier_ok(fr, s, half_w, deck_z, need=4.2):
    """A pier is drawn only where the ground under its cap is lower than the cap (deck - need), so it never sits in a levee."""
    g = max(fr.ground(s, x) or 0.0 for x in (-half_w, 0.0, half_w))
    return g < deck_z - need


# --------------------------------------------------------------------------------------------------------------
# MARUKO: level to the ramp start, then a ramp down to the levee crown (only if the crown is lower than the deck)
# --------------------------------------------------------------------------------------------------------------
M_FR = Frame("156462928", 1, 2, B.LC_M)
M_JOIN = 105.0                    # piece A starts here
M_PIER_STEP = 24.0                # m; the aerial shows no piers on the approach (the flood plain is under a deck shadow)


def maruko_plan():
    """-> (s_end, crown_z): walk west from the join until the ground under the whole deck width is higher than the deck-24 m
    ahead, take the crown = highest ground along the axis in the first levee ridge."""
    best = None
    for k in range(0, 260):
        s = M_JOIN - k * 1.0
        g = M_FR.ground(s, 0.0)
        if g is None:
            break
        if best is None or g > best[1]:
            if k > 40:                                       # skip the flood plain
                best = (s, g)
    s_end = best[0] + 1.0                                    # land 1 m east of the crest peak, on the crest
    return (s_end, M_FR.ground(s_end, 0.0))


def maruko_pieces(s_end, crown_z):
    deck = B.ZB + B.H_M
    ramp = max(0.0, deck - crown_z)
    ramp_len = min(40.0, max(20.0, ramp / 0.04)) if ramp > 0.05 else 0.0     # <= 4 % grade
    pieces = {}
    s_ramp = s_end + ramp_len
    # W1: s_ramp .. M_JOIN level; W2: s_end .. s_ramp ramp (sheared)
    for name, a, b, ramp_piece in (("W1_level", s_ramp, M_JOIN, False), ("W2_ramp", s_end, s_ramp, True)):
        if b - a < 0.5:
            continue
        m = Mesh()
        B.maruko_deck(m, a, b, False)
        piers = []
        s = M_JOIN - M_PIER_STEP
        while s > a + 8.0:
            if a < s < b and pier_ok(M_FR, s, 11.6, deck):
                piers.append(s)
            s -= M_PIER_STEP
        for s in piers:
            B.maruko_pier(m, s, False)
        if ramp_piece:
            k = (crown_z - deck) / (b - a)                        # negative: descends towards the west (smaller s)
            # z falls as y decreases: z' = z - |k| (b - y)  ->  k_y = +|ramp|/L with y_ref = b
            m = shear(m, ramp / (b - a), b)
            # end abutment down to the terrain at the west end
            ab = Mesh()
            B.box(ab, -B.HW, B.HW, a - 3.0, a + 0.2, -3.0, crown_z - ZB, B.PIER, 0)
            m.v.extend([(x, y, z, nx, ny, nz, r, g, bb, al) for (x, y, z, nx, ny, nz, r, g, bb, al) in ab.v])
            off = len(m.v) - len(ab.v)
            m.f.extend([[i + off for i in f] for f in ab.f])
        pieces[f"bridge_maruko_{name}_{TAG}.akm"] = (m, a, name)
    return pieces


# --------------------------------------------------------------------------------------------------------------
# TOKYU: viaduct descending to the levee crown, ballast top meets the ground at the end
# --------------------------------------------------------------------------------------------------------------
T_FR = Frame("274739348", 1, 0, B.LAT_C)
T_JOIN = -4.0                     # piece A starts here
T_PIER_STEP = 34.0                # as the existing spans (T_PIERS)


def tokyu_plan():
    best = None
    for k in range(0, 300):
        s = T_JOIN - k * 1.0
        g = T_FR.ground(s, 0.0)
        if g is None:
            break
        if k > 60 and (best is None or g > best[1]):
            best = (s, g)
    s_end = best[0] + 1.0
    return (s_end, T_FR.ground(s_end, 0.0))


def tokyu_pieces(s_end, crown_z):
    top_ballast = B.RAIL_TOP + ZB - 0.18                         # ballast top at the join, m
    L = T_JOIN - s_end
    k = (top_ballast - crown_z) / L                              # fall per metre towards the west (> 0)
    cuts = []
    a = s_end
    while a < T_JOIN - 1e-6:
        cuts.append((a, min(a + 69.0, T_JOIN)))
        a += 69.0
    pieces = {}
    for i, (a, b) in enumerate(cuts):
        m = Mesh()
        piers = []
        s = T_JOIN - T_PIER_STEP
        while s > s_end + 6.0:
            if a <= s < b:
                z_deck = ZB + B.RAIL_TOP - k * (T_JOIN - s)
                if pier_ok(T_FR, s, 9.0, z_deck, need=4.4):
                    piers.append((s, True))
            s -= T_PIER_STEP
        B.tokyu_segment(m, a, b, piers, [p[0] for p in piers])
        # shear about the join: z' = z - k (T_JOIN - y)  = z + k (y - T_JOIN)
        m = shear(m, k, T_JOIN)
        if i == 0:                                               # abutment into the levee at the west end
            ab = Mesh()
            B.box(ab, -8.6, 8.6, a - 3.0, a + 0.2, -3.0, crown_z - ZB, B.PIER, 0)
            off = len(m.v)
            m.v.extend(ab.v)
            m.f.extend([[j + off for j in f] for f in ab.f])
        pieces[f"bridge_tokyu_W{i + 1}_{TAG}.akm"] = (m, a, "W%d" % (i + 1))
    return pieces, k


def edits_for(pieces, fr, kind):
    out = []
    for fname, (m, s0, tag) in pieces.items():
        E, N = fr.origin(s0)
        inst = fname[:-4]
        out.append({"inst": inst, "op": "add", "mesh": fname, "E": round(E, 3), "N": round(N, 3), "z_world": 0.0,
                    "bearing_deg": round(fr.brg, 3),
                    "basis": f"aerial (mosaic_wide + top view sat 1.0) shows the {kind} continuing straight on the same axis to the "
                             f"Kawasaki levee; DEM/live terrain levee crown measured along the axis; see author_tama_bridge_approaches.py; "
                             f"pictures assess/live_fix_20261017/tama_farbank/",
                    "by": BY})
    return out


def local(m, s0):
    return shift_y(m, s0)


def main():
    measure = "--measure" in sys.argv
    write = "--write" in sys.argv
    mp = maruko_plan()
    tp = tokyu_plan()
    print("maruko levee crown (s, z):", mp, " deck", round(B.ZB + B.H_M, 3))
    print("tokyu  levee crown (s, z):", tp, " ballast top at join", round(B.RAIL_TOP + ZB - 0.18, 3))
    for nm, fr, lo, hi in (("maruko", M_FR, mp[0] - 8, M_JOIN + 4), ("tokyu", T_FR, tp[0] - 8, T_JOIN + 4)):
        print(nm, "ground by s (lat -13 / 0 / +13):")
        for s in np.arange(lo, hi, 6.0):
            print("  s %7.1f " % s, " ".join("%s" % ("None" if fr.ground(s, x) is None else "%.2f" % fr.ground(s, x)) for x in (-13, 0, 13)))
    if measure and not write:
        return
    mpieces = maruko_pieces(*mp)
    tpieces, k = tokyu_pieces(*tp)
    print("tokyu fall per m", round(k, 5), "=", round(k * 100, 2), "%")
    edits = []
    for kind, fr, pieces in (("Maruko road bridge", M_FR, mpieces), ("Tokyu railway bridge", T_FR, tpieces)):
        for fname, (m, s0, tag) in pieces.items():
            lm = B.scaled(local(m, s0))
            ys = [v[1] / CS for v in lm.v]
            zs = [v[2] / CS for v in lm.v]
            print(f"{fname}: s0 {s0:.2f} {len(lm.v)} vertices {len(lm.f)} faces y {min(ys):.1f}..{max(ys):.1f} z {min(zs):.1f}..{max(zs):.1f}")
            if write:
                path = B.BUILD / "meshes" / fname
                text = akm_text(lm, f"FL-4876 hand-authored {kind} west approach {fname}; world units (metres*5.132), x right of "
                                    f"travel, y along the axis, z above raw terrain height 0 (4.941 m); alpha<=128 collides")
                if path.exists() and path.read_text(encoding="utf-8") != text:
                    raise SystemExit(f"{path} exists with other content; write new mesh names, never overwrite one")
                if not path.exists():
                    path.write_text(text, encoding="utf-8")
        edits += edits_for(pieces, fr, kind)
    if write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(edits, indent=1, ensure_ascii=False) + "\n")
        print("edits ->", OUT)


if __name__ == "__main__":
    main()
