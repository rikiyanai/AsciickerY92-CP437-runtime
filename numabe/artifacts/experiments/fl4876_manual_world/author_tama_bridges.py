#!/usr/bin/env python3
"""author_tama_bridges.py [--write] -- hand-authored bridge meshes for the Tama River terrain (FL-4876, 2026-10-16).

Operator 2026-10-16: the river was added to the live map by direct terrain extension (a3d_terrain_extend.py) but the map
has no bridge models, so the bridges show only as flat road lines on the water.  This script authors two bridges as
ASCII-PLY .akm meshes and writes the a3d_edit.py "add" records that place them.  No build, no pipeline file touched.

Bridges found (OSM bridge=yes crossing the river inside the new terrain, world x -3200..0; checked on the GSI aerial):
  MARUKO  丸子橋, 中原街道 (Nakahara-kaido).  OSM ways 156462928 + 648700092 (carriageway), 648700094 + 27072340 (carriageway,
          9.1 m south-west of the first), footways 662919563 / 662919564 (7.7 m north-west and 15 m south-east of the first).
          Axis = way 156462928 node 2 -> node 3, bearing 60.74.  The aerial shows a steel through truss (pale blue) with an
          arched top chord over the middle span, 6 lanes between the trusses, a footway cantilevered outside each truss.
  TOKYU   東急多摩川橋梁, Toyoko + Meguro lines, 4 tracks (OSM ways 274739348, 274739345, 212758778, 111690256 at 0.0 / 3.4 /
          8.5 / 12.2 m lateral).  Axis = way 274739348 node 2 -> node 1, bearing 48.47.  The aerial shows a pale grey
          plate-girder viaduct with a pier about every 35 m.
  Not authored: the Tokaido / Shinkansen bridges (world y < 0, outside the terrain); the footway bridges 302015583 and
  1041878473 (5 to 14 m long, over no river); the weir with a service deck upstream of Maruko (a weir, no OSM bridge way).

Run:  python3 -I author_tama_bridges.py [--rev N] [--write]   (rev 1 writes "add" records; rev N > 1 writes new meshes
      *_vN.akm and "mesh" + "to" records for the same instances; apply with a3d_edit.py apply).  The road causeway the
      terrain extension painted across the river under Maruko is removed by a3d_terrain_clear_span.py (journalled).
Deck width: the OSM man_made=bridge polygon 1467768291 (丸子橋) is 26.4 m wide, centre line 4.1 m right of way 156462928.

FRAME.  Mesh units are WORLD units: x, y and z are metres * content_scale (5.132), because a3d_edit.py "add" writes a
transform with x/y scale 1 and z scale 16.  Mesh x = right of the travel direction (= minus the OSM left offset), y = along
the axis, z = metres above ZB.  ZB = 4.941 m = raw terrain height 0 (the river bed the extension flattened to), so the
instance z_world is 0.  Placed at the segment origin (axis s0, bridge centre line), facing the axis bearing.
HEIGHTS (terrain read from the live a3d, geo helpers in the session scratch): Maruko east levee crown 14.5 m (s 376..388 on
the axis; levee foot 6.5 m at s 346) so the deck top is 14.5 m; floodplain and river banks are floored at 6.5 m, river bed
4.94 m.  Tokyu east embankment crown 15.4 m (s 220..238), rail top 15.4 m.  Both bridges cross the floodplain on piers.
COLLISION.  alpha 0 collides (engine/physics.cpp:663), 255 is presentation only: deck, curbs, ballast, girders and piers collide;
trusses, hangers, railings and masts do not.
"""
import json
import math
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from author_temple_meshes import Mesh, akm_text       # noqa: E402
from a3d_terrain_extend import ll_to_utm              # noqa: E402

ROOT = HERE.parents[2]
BUILD = ROOT / ".run/fl4876_builds/live_numabe"
OSM = ROOT / ".run/downloads/tama_osm/tama_west.osm"
EDITS_OUT = HERE / "assess/live_fix_20261016/tama_bridges/bridge_edits.json"
CS = 5.1320023927966725            # terrain_metadata.json content_scale
ZB = 4.941                         # m, raw height 0
BY = "sonnet bridge agent"
INST_TAG = "20261016"
REV = int(sys.argv[sys.argv.index("--rev") + 1]) if "--rev" in sys.argv else 1
TAG = INST_TAG + ("" if REV == 1 else "_v%d" % REV)   # mesh file suffix; revisions are new meshes, applied with op "mesh" + "to"

ASPHALT = (58, 60, 66)
CONCRETE = (176, 174, 168)
CURB = (204, 202, 196)
WALK = (150, 148, 142)
PIER = (160, 157, 150)
GIRDER = (96, 130, 176)
ARCH = (40, 92, 190)           # Maruko blue
LATTICE = (116, 168, 224)
RAILING = (208, 214, 220)
RAIL_GREY = (184, 190, 194)    # Tokyu plate girder
BALLAST = (120, 114, 106)
RAIL = (60, 54, 50)
MAST = (118, 122, 124)


# --------------------------------------------------------------------------------------------------------------
# primitives (metres, mesh frame x right, y along, z up)
# --------------------------------------------------------------------------------------------------------------
def box(m, x0, x1, y0, y1, z0, z1, rgb, a, skip=()):
    f = {
        "-x": ([(x0, y0, z0), (x0, y1, z0), (x0, y1, z1), (x0, y0, z1)], (-1, 0, 0)),
        "+x": ([(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)], (1, 0, 0)),
        "-y": ([(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)], (0, -1, 0)),
        "+y": ([(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)], (0, 1, 0)),
        "-z": ([(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)], (0, 0, -1)),
        "+z": ([(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], (0, 0, 1)),
    }
    for k, (pts, out) in f.items():
        if k not in skip:
            m.quad(pts, rgb, a, out)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a):
    n = math.sqrt(sum(c * c for c in a))
    return tuple(c / n for c in a)


def bar(m, p0, p1, w, rgb, a, w2=None):
    """Square (w x w2) section bar from p0 to p1."""
    t = _unit(tuple(p1[i] - p0[i] for i in range(3)))
    r = (0, 0, 1) if abs(t[2]) < 0.9 else (1, 0, 0)
    u1 = _unit(_cross(r, t))
    u2 = _cross(t, u1)
    h1, h2 = w / 2, (w2 if w2 is not None else w) / 2

    def c(p, s1, s2):
        return tuple(p[i] + u1[i] * s1 * h1 + u2[i] * s2 * h2 for i in range(3))
    for (s1, s2), (t1, t2), out in (((-1, -1), (1, -1), tuple(-c_ for c_ in u2)), ((1, -1), (1, 1), u1),
                                    ((1, 1), (-1, 1), u2), ((-1, 1), (-1, -1), tuple(-c_ for c_ in u1))):
        m.quad([c(p0, s1, s2), c(p0, t1, t2), c(p1, t1, t2), c(p1, s1, s2)], rgb, a, out)
    m.quad([c(p0, -1, -1), c(p0, 1, -1), c(p0, 1, 1), c(p0, -1, 1)], rgb, a, tuple(-c_ for c_ in t))
    m.quad([c(p1, -1, -1), c(p1, 1, -1), c(p1, 1, 1), c(p1, -1, 1)], rgb, a, t)


def railing_line(m, x, y0, y1, z0, h, rgb=RAILING, post_every=2.5, w=0.14):
    n = max(1, int(round((y1 - y0) / post_every)))
    for i in range(n + 1):
        y = y0 + (y1 - y0) * i / n
        box(m, x - w / 2, x + w / 2, y - w / 2, y + w / 2, z0, z0 + h, rgb, 255, skip=("-z",))
    for zz in (z0 + h, z0 + h * 0.5):
        box(m, x - w / 2, x + w / 2, y0, y1, zz - w / 2, zz + w / 2, rgb, 255)


def scaled(m):
    """A copy with positions in world units (metres * CS)."""
    o = Mesh()
    for (x, y, z, nx, ny, nz, r, g, b, al) in m.v:
        o.v.append((x * CS, y * CS, z * CS, nx, ny, nz, r, g, b, al))
    o.f = [list(f) for f in m.f]
    return o


# --------------------------------------------------------------------------------------------------------------
# MARUKO
# --------------------------------------------------------------------------------------------------------------
H_M = 14.5 - ZB                      # deck top above ZB
HW = 13.2                            # half width incl. footways: OSM man_made=bridge polygon 1467768291 (丸子橋) is 26.4 m wide
LC_M = -4.1                          # its centre line, lateral offset from way 156462928 (left positive)
XT = 9.4                             # truss plane / curb line
M_PIERS = [105.0, 138.5, 172.0, 236.667, 301.333, 366.0]
M_END = 374.0
M_TRUSS = (172.0, 366.0)
M_ARCH = (236.667, 301.333)
SIDE_TOP = 6.0
ARCH_RISE = 8.0


def top_h(s):
    a, b = M_ARCH
    if a <= s <= b:
        sc, hl = (a + b) / 2, (b - a) / 2
        return SIDE_TOP + ARCH_RISE * (1 - ((s - sc) / hl) ** 2)
    return SIDE_TOP


def maruko_deck(m, y0, y1, truss):
    H = H_M
    box(m, -HW, HW, y0, y1, H - 0.5, H - 0.05, CONCRETE, 0)                      # slab
    box(m, -9.0, 9.0, y0, y1, H - 0.05, H, ASPHALT, 0, skip=("-z",))           # roadway
    for sx in (-1, 1):
        x0, x1 = (sx * 9.0, sx * 9.4) if sx > 0 else (sx * 9.4, sx * 9.0)
        box(m, x0, x1, y0, y1, H - 0.05, H + 0.28, CURB, 0, skip=("-z",))      # curb
        fx0, fx1 = (9.4, HW) if sx > 0 else (-HW, -9.4)
        box(m, fx0, fx1, y0, y1, H - 0.05, H + 0.12, WALK, 0, skip=("-z",))    # footway
        railing_line(m, sx * (HW - 0.1), y0, y1, H + 0.12, 1.1)
    box(m, -0.14, 0.14, y0, y1, H, H + 0.04, (236, 190, 60), 255, skip=("-z", "-x", "+x", "-y", "+y"))   # centre line
    k0 = int(math.ceil(y0 / 9.0))
    while k0 * 9.0 < y1:                                                          # dashed lane lines, 3 m dash every 9 m
        a = k0 * 9.0
        for lx in (-6.0, -3.0, 3.0, 6.0):
            box(m, lx - 0.07, lx + 0.07, max(a, y0), min(a + 3.0, y1), H, H + 0.04, (232, 232, 226), 255,
                skip=("-z", "-x", "+x", "-y", "+y"))
        k0 += 1
    for gx in (-11.0, -7.5, -2.5, 2.5, 7.5, 11.0):                                # main girders
        box(m, gx - 0.3, gx + 0.3, y0, y1, H - 2.15, H - 0.5, GIRDER, 0)
    n = max(1, int(round((y1 - y0) / 8.08)))
    for i in range(n + 1):                                                        # cross beams
        y = y0 + (y1 - y0) * i / n
        box(m, -HW, HW, y - 0.2, y + 0.2, H - 1.7, H - 0.5, GIRDER, 0)
    if not truss:                                                                 # concrete parapet on the approaches
        for sx in (-1, 1):
            x0, x1 = (sx * 9.0, sx * 9.4) if sx > 0 else (sx * 9.4, sx * 9.0)
            box(m, x0, x1, y0, y1, H + 0.28, H + 1.0, CURB, 0)


def maruko_truss(m, a, b, arch):
    H = H_M
    n = 8
    pl = (b - a) / n
    for sx in (-1, 1):
        x = sx * XT
        pts = [a + pl * k for k in range(n + 1)]
        # bottom chord
        bar(m, (x, a, H + 0.1), (x, b, H + 0.1), 0.8, GIRDER, 255)
        # top chord (piecewise linear; two pieces per panel on the arch)
        sub = 2 if arch else 1
        for k in range(n * sub):
            s0, s1 = a + pl * k / sub, a + pl * (k + 1) / sub
            bar(m, (x, s0, H + top_h(s0)), (x, s1, H + top_h(s1)), 0.9, ARCH, 255)
        # hangers / verticals and Warren diagonals
        for k, s in enumerate(pts):
            bar(m, (x, s, H + 0.1), (x, s, H + top_h(s)), 0.45, LATTICE, 255)
        for k in range(n):
            s0, s1 = pts[k], pts[k + 1]
            if k % 2 == 0:
                bar(m, (x, s0, H + 0.1), (x, s1, H + top_h(s1)), 0.4, LATTICE, 255)
            else:
                bar(m, (x, s0, H + top_h(s0)), (x, s1, H + 0.1), 0.4, LATTICE, 255)
    for k, s in enumerate([a + pl * k for k in range(n + 1)]):                    # top lateral bracing, every panel point
        if k % 2 == 0:
            bar(m, (-XT, s, H + top_h(s)), (XT, s, H + top_h(s)), 0.5, LATTICE, 255)
    for s in (a, b):                                                              # portal knee braces at the span ends
        for sx in (-1, 1):
            bar(m, (sx * XT, s, H + top_h(s) - 1.8), (sx * (XT - 2.2), s, H + top_h(s)), 0.4, LATTICE, 255)


def maruko_pier(m, s, water):
    H = H_M
    if water:
        box(m, -10.0, 10.0, s - 1.6, s + 1.6, -3.0, H - 2.15, PIER, 0)
        box(m, -11.6, 11.6, s - 2.1, s + 2.1, H - 3.7, H - 2.15, PIER, 0)
    else:
        for cx in (-7.5, 0.0, 7.5):
            box(m, cx - 1.2, cx + 1.2, s - 1.2, s + 1.2, -3.0, H - 2.15, PIER, 0)
        box(m, -11.6, 11.6, s - 1.5, s + 1.5, H - 3.6, H - 2.15, PIER, 0)


def maruko_meshes():
    out = {}
    # (name, y0, y1, truss, arch, piers[(s, water)], abutment)
    parts = [
        ("A_west_approach", 105.0, 172.0, False, False, [(105.0, False), (138.5, False)], False),
        ("B_span1", 172.0, 236.667, True, False, [(172.0, False)], False),
        ("C_span2_arch", 236.667, 301.333, True, True, [(236.667, True)], False),
        ("D_span3", 301.333, 366.0, True, False, [(301.333, True)], False),
        ("E_east_end", 366.0, M_END, False, False, [(366.0, False)], True),
    ]
    for tag, s0, s1, truss, arch, piers, abut in parts:
        m = Mesh()
        # local y = s - s0 (so the instance origin is the segment start on the axis); x = -(lat - LC_M)
        sub = Mesh()
        maruko_deck(sub, s0, s1, truss)
        if truss:
            maruko_truss(sub, s0, s1, arch)
        for s, w in piers:
            maruko_pier(sub, s, w)
        if abut:
            box(sub, -HW, HW, M_END - 0.2, M_END + 3.0, 0.0, H_M, PIER, 0)
        out[f"bridge_maruko_{tag}_{TAG}.akm"] = (sub, s0)
    return out


# --------------------------------------------------------------------------------------------------------------
# TOKYU
# --------------------------------------------------------------------------------------------------------------
RAIL_TOP = 15.4 - ZB
LAT_C = 6.1
TRACK_LAT = (0.0, 3.4, 8.5, 12.2)
T_PIERS = [0.0, 34.0, 68.0, 102.0, 136.0, 170.0, 204.0]
T_END = 216.0


def tokyu_segment(m, y0, y1, piers, mast_at):
    R = RAIL_TOP
    HWr = 8.0
    box(m, -HWr - 0.6, HWr + 0.6, y0, y1, R - 1.35, R - 0.8, RAIL_GREY, 0)                  # deck slab
    box(m, -HWr, HWr, y0, y1, R - 0.8, R - 0.18, BALLAST, 0, skip=("-z",))                  # ballast
    for sx in (-1, 1):                                                                      # girder webs
        x0, x1 = (sx * HWr, sx * (HWr + 0.6)) if sx > 0 else (sx * (HWr + 0.6), sx * HWr)
        box(m, x0, x1, y0, y1, R - 2.7, R + 0.9, RAIL_GREY, 0)
        wx0, wx1 = (HWr + 0.6, HWr + 1.7) if sx > 0 else (-HWr - 1.7, -HWr - 0.6)           # outside walkway
        box(m, wx0, wx1, y0, y1, R - 0.7, R - 0.55, WALK, 0)
        railing_line(m, sx * (HWr + 1.65), y0, y1, R - 0.55, 1.1)
    for gx in (-6.0, 0.0, 6.0):
        box(m, gx - 0.35, gx + 0.35, y0, y1, R - 3.4, R - 1.35, RAIL_GREY, 0)
    n = max(1, int(round((y1 - y0) / 8.5)))
    for i in range(n + 1):
        y = y0 + (y1 - y0) * i / n
        box(m, -HWr - 0.6, HWr + 0.6, y - 0.2, y + 0.2, R - 2.8, R - 1.35, RAIL_GREY, 0)
    for lat in TRACK_LAT:                                                                   # rails (1067 mm gauge)
        x = -(lat - LAT_C)
        for dx in (-0.533, 0.533):
            box(m, x + dx - 0.06, x + dx + 0.06, y0, y1, R - 0.18, R, RAIL, 255)
    for s, water in piers:
        box(m, -HWr, HWr, s - 1.1, s + 1.1, -3.0, R - 3.4, PIER, 0)
        box(m, -HWr - 1.0, HWr + 1.0, s - 1.5, s + 1.5, R - 4.2, R - 3.4, PIER, 0)
    for s in mast_at:                                                                       # overhead-line portals
        for sx in (-1, 1):
            bar(m, (sx * (HWr + 1.7), s, R - 0.55), (sx * (HWr + 1.7), s, R + 7.6), 0.4, MAST, 255)
        bar(m, (-HWr - 1.7, s, R + 7.4), (HWr + 1.7, s, R + 7.4), 0.35, MAST, 255)
        bar(m, (-HWr - 1.7, s, R + 5.8), (HWr + 1.7, s, R + 5.8), 0.2, MAST, 255)


def tokyu_meshes():
    out = {}
    cuts = [(-4.0, 68.0), (68.0, 136.0), (136.0, T_END)]
    for i, (s0, s1) in enumerate(cuts):
        m = Mesh()
        piers = [(s, True) for s in T_PIERS if s0 <= s < s1]
        masts = [s for s in T_PIERS if s0 <= s < s1]
        tokyu_segment(m, s0, s1, piers, masts)
        if i == len(cuts) - 1:                                                              # abutment into the embankment
            box(m, -8.6, 8.6, T_END - 0.2, T_END + 3.0, 0.0, RAIL_TOP - 0.18, PIER, 0)
        out[f"bridge_tokyu_{'ABC'[i]}_{TAG}.akm"] = (m, s0)
    return out


# --------------------------------------------------------------------------------------------------------------
# placement records
# --------------------------------------------------------------------------------------------------------------
def way_nodes(wid):
    root = ET.parse(OSM).getroot()
    nodes = {n.get("id"): (float(n.get("lon")), float(n.get("lat"))) for n in root.iter("node")}
    for w in root.iter("way"):
        if w.get("id") == wid:
            return [tuple(float(v) for v in ll_to_utm(*nodes[r.get("ref")])) for r in w.findall("nd")]
    raise SystemExit("way %s not in %s" % (wid, OSM))


def axis(wid, i0, i1):
    a, b = way_nodes(wid)[i0], way_nodes(wid)[i1]
    dE, dN = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dE, dN)
    return a, (dE / L, dN / L), math.degrees(math.atan2(dE, dN)) % 360.0


def local_mesh(entry, lat_c, sign_x_from_lat=True):
    """Shift y by -s0 (origin at the segment start on the axis); x already is -(lat - lat_c)."""
    m, s0 = entry
    o = Mesh()
    for (x, y, z, nx, ny, nz, r, g, b, al) in m.v:
        o.v.append((x, y - s0, z, nx, ny, nz, r, g, b, al))
    o.f = [list(f) for f in m.f]
    return o


def main():
    write = "--write" in sys.argv
    edits = []
    jobs = []
    O, d, brg = axis("156462928", 1, 2)
    jobs.append(("maruko", maruko_meshes(), O, d, brg, LC_M))     # centre line = lateral LC_M (x = -(lat - LC_M))
    O2, d2, brg2 = axis("274739348", 1, 0)
    jobs.append(("tokyu", tokyu_meshes(), O2, d2, brg2, LAT_C))
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    for name, meshes, O_, d_, brg_, lat_c in jobs:
        if only and name != only:
            continue
        nE, nN = -d_[1], d_[0]                                        # left of travel
        for fname, entry in meshes.items():
            m = scaled(local_mesh(entry, lat_c))
            xs = [v[0] / CS for v in m.v]; ys = [v[1] / CS for v in m.v]; zs = [v[2] / CS for v in m.v]
            print(f"{fname}: {len(m.v)} vertices, {len(m.f)} faces; x {min(xs):.1f}..{max(xs):.1f} y {min(ys):.1f}..{max(ys):.1f} "
                  f"z {min(zs):.1f}..{max(zs):.1f} m")
            if write:
                path = BUILD / "meshes" / fname
                text = akm_text(m, f"FL-4876 hand-authored {name} bridge segment {fname}; world units (metres*5.132), x right of "
                                   f"travel, y along the axis, z above raw terrain height 0 (4.941 m); alpha<=128 collides")
                if path.exists() and path.read_text(encoding="utf-8") != text:
                    raise SystemExit(f"{path} exists with other content; write new mesh names, never overwrite one")
                if not path.exists():
                    path.write_text(text, encoding="utf-8")
            s0 = entry[1]
            E = O_[0] + d_[0] * s0 + nE * lat_c
            N = O_[1] + d_[1] * s0 + nN * lat_c
            inst = fname[:-len(TAG) - 4] + INST_TAG                  # instance name stays the same across revisions
            basis = (f"OSM bridge axis ({name}) and terrain levee heights, see author_tama_bridges.py docstring; "
                     f"pictures: assess/live_fix_20261016/tama_bridges/")
            if REV == 1:
                edits.append({"inst": inst, "op": "add", "mesh": fname, "E": round(E, 3), "N": round(N, 3),
                              "z_world": 0.0, "bearing_deg": round(brg_, 3), "basis": basis, "by": BY})
            else:                                                         # a revision: new mesh on the same instance, re-centred
                edits.append({"inst": inst, "op": "mesh", "mesh": fname, "basis": basis + f"; revision {REV}", "by": BY})
                edits.append({"inst": inst, "op": "to", "E": round(E, 3), "N": round(N, 3),
                              "basis": basis + f"; revision {REV}", "by": BY})
    if write:
        out = EDITS_OUT if REV == 1 else EDITS_OUT.with_name(f"bridge_edits_rev{REV}.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(edits, indent=1, ensure_ascii=False) + "\n")
        print("edits ->", out)


if __name__ == "__main__":
    main()
