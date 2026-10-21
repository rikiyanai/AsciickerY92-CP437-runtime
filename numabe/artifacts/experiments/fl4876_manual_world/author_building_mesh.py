#!/usr/bin/env python3
"""author_building_mesh.py SPEC.json [--write] [--preview OUT.png --bearing B] [--map MAP.a3d]  (FL-4876 MAP-EDIT-01, 2026-10-17)

Builds ONE hand-authored building mesh (.akm, ASCII PLY with per-vertex RGB + alpha, like the other live meshes) from a
terrain-baked building's footprint (building_instances.json bake_footprint, world units) and a hand-written spec JSON
(assess/live_fix_20261017/buildings_pilot/specs/<way>.json). No build, no pipeline file touched; a3d_terrain_flatten.py removes
the baked plateau, a3d_edit.py add puts the mesh in the live map.

FRAME. The instance origin is the footprint's vertex mean (E,N); identity rotation; z = ground at the footprint (see GROUND).
Mesh x/y = (E - E0, N - N0) * 5.132 (content_scale of the build's terrain_metadata.json); mesh z = metres * 5.132 and the
instance transform scales z by 16 (a3d_edit.py 'add'), so 1 m of height = 82.112 raw terrain height units. Walls start
foot_m (default -0.6 m) below the origin so a slope never opens a gap. Face i runs from footprint vertex i to i+1 (the numbering
printed by `faces`); s = metres along it from vertex i, o = metres outward, z = metres up.
COLLISION. alpha 0 = what collides (walls, recess back planes and reveals, roof surface); alpha 255 = presentation (frames,
glass, awnings, balcony rails, parapet caps). SHADING. The editor light makes a pale wall facing NW/SW render at 0.5, so wall
faces carry vertex normals tilted toward +z (wall.lift, default 0.75; same trick as author_home_17_16_mesh.py).

SPEC (all lengths metres, colours [r,g,b], "s"/"z" ranges [lo, hi]):
  feature        "feature:building_footprint:way/<id>" (or "way"), instances_json optional override
  height_m       roof top above ground; default the instance's roof_height_m. Must equal the sum of storey_heights_m
                 (+ parapet_m for a flat roof) within 0.75 m or the build is refused (a spec that disagrees with the
                 measured mass is a spec error, not a rounding).
  storey_heights_m [h0, h1, ...]   storeys   (len == number of storeys)
  wall           {color, lift?, bands:[{z:[z0,z1], color}], plinth:{height_m, color}, floor_lines:{color, thick_m, proud_m}}
  roof           {type: flat|gable|hip|shed, color, parapet_m, parapet_color, ridge: long|short, pitch_deg|ridge_m,
                  overhang_m, high_edge (shed: face index of the high side)}
                 default: the roof is built on the footprint's bounding box (overhangs notches). NEW, opt-in (2026-10-17):
                 "fit": "plan" (+ pitch_deg or ridge_m, overhang_m, snap_m 0.5, optional parts [...]) puts one hip/gable roof on each
                 rectangular wing of an L/T/U/notched/stepped plan (or one straight-skeleton hip on a convex plan); the roof
                 outline is the footprint + overhang_m; unfit plans fall back to the box roof with a printed ROOF FALLBACK.
                 stats show roof_wing_rects, roof_frame_polygon (the frame of roof.parts) and roof_audit (m2 uncovered / over a notch).
                 --preview ... --above looks down on the roof; --rev r names the mesh building_hand_<way>_r_20261017.akm.
  faces          {"<i>": FACE, ...}, "default": FACE used for faces not listed
  FACE           {wall_color?, bands?, openings:[OPENING], awnings:[...], bands_proud:[...], balconies:[...], pipes:[...]}
  OPENING        {s:[a,b], z:[z0,z1], depth, fill: window|door|shutter|glazing|void, back_color, reveal_color,
                  frame_color, glass_color, cols, rows, sill:{color, proud}, hood:{color, proud}, panel_color}
                 openings in one face must not overlap; the wall is built as the rectangles around them, the reveals
                 and the back plane (collides) are built inside, the fill is drawn on the back plane.
  decal          {s:[a,b], z:[z0,z1], o (default 0.04 m proud; inside a recess use o = -depth + 0.05), color}
  box            {s:[a,b], z:[z0,z1], depth, color, glass?: colour of a front pane, margin}   oriel / bay / steps
  awning         {s:[a,b], z: lower edge, depth, rise, valance, color, stripe_color?}
  band           {s:[a,b], z:[z0,z1], proud, color}       sign bands, cornices, plinths
  balcony        {s:[a,b], z: floor height, depth, rail_h, slab_color, rail_color, solid_front: colour or null,
                  bar_gap, sides: bool}
  pipe           {s: along, z:[z0,z1], w, color}
  stair          (face list) {s0, width, z:[z0,z1], dir: +1|-1, color, rail_color}  one straight run along the wall

`faces` lists faces (length, outward bearing). `--write` writes the mesh to the build's meshes/ (never overwrites a
different file) and the a3d_edit.py record to assess/.../buildings_pilot/edits/<way>_add.json, with z_world read from the live
map's (already flattened) terrain at the footprint (--map). --preview renders a flat-shaded orthographic PNG (no browser).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from author_temple_meshes import Mesh, akm_text  # noqa: E402

ROOT = HERE.parents[2]
BUILD = ROOT / ".run/fl4876_builds/live_numabe"
PILOT = HERE / "assess/live_fix_20261017/buildings_pilot"
CS = 5.1320023927966725
RAW_PER_M = 16.0 * CS
TAG = "20261017"
BY = "sonnet buildings pilot 20261017"
EPS = 1e-6


# ---------------------------------------------------------------------------------------------------------------
# small geometry helpers (metres)
# ---------------------------------------------------------------------------------------------------------------
def _unit(a):
    n = math.sqrt(sum(c * c for c in a))
    return tuple(c / n for c in a)


def signed_area(poly):
    return sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly))) / 2


def triangulate(poly):
    """Ear clipping of a simple polygon (any orientation) -> list of index triples."""
    n = len(poly)
    idx = list(range(n))
    if signed_area(poly) < 0:
        idx.reverse()
    tris = []

    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def in_tri(p, a, b, c):
        return cross(a, b, p) >= -EPS and cross(b, c, p) >= -EPS and cross(c, a, p) >= -EPS
    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = poly[i0], poly[i1], poly[i2]
            if cross(a, b, c) <= EPS:
                continue
            if any(j not in (i0, i1, i2) and in_tri(poly[j], a, b, c) for j in idx):
                continue
            tris.append((i0, i1, i2))
            idx.pop(k)
            break
        else:
            raise SystemExit("footprint cannot be triangulated (self-intersecting?)")
    tris.append(tuple(idx))
    return tris


def obb(poly):
    """Minimum-area oriented bounding rectangle -> (centre, u, v, hu, hv) with hu >= hv half extents."""
    best = None
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        u = _unit((b[0] - a[0], b[1] - a[1], 0))[:2]
        v = (-u[1], u[0])
        pu = [p[0] * u[0] + p[1] * u[1] for p in poly]
        pv = [p[0] * v[0] + p[1] * v[1] for p in poly]
        area = (max(pu) - min(pu)) * (max(pv) - min(pv))
        if best is None or area < best[0] - 1e-9:
            best = (area, u, v, min(pu), max(pu), min(pv), max(pv))
    _, u, v, u0, u1, v0, v1 = best
    cu, cv = (u0 + u1) / 2, (v0 + v1) / 2
    c = (u[0] * cu + v[0] * cv, u[1] * cu + v[1] * cv)
    hu, hv = (u1 - u0) / 2, (v1 - v0) / 2
    if hv > hu:
        u, v, hu, hv = v, (-u[0], -u[1]), hv, hu
    return c, u, v, hu, hv


# ---------------------------------------------------------------------------------------------------------------
# mesh builder in metres relative to the footprint centre; scaled to world units at the end
# ---------------------------------------------------------------------------------------------------------------
class B:
    def __init__(self):
        self.m = Mesh()
        self.tris = 0

    def q(self, pts, rgb, alpha, out, lift=0.0):
        """pts (e, n, z) metres; out: direction the visible side faces."""
        first = len(self.m.v)
        self.m.quad([tuple(p) for p in pts], rgb, alpha, tuple(out))
        if lift:
            for i in range(first, len(self.m.v)):
                x, y, z, nx, ny, nz, r, g, b, a = self.m.v[i]
                if nz < -0.2:                                   # undersides keep their true normal
                    continue
                nx, ny, nz = nx * (1 - lift), ny * (1 - lift), nz * (1 - lift) + lift
                ln = math.sqrt(nx * nx + ny * ny + nz * nz)
                self.m.v[i] = (x, y, z, nx / ln, ny / ln, nz / ln, r, g, b, a)

    def scaled(self):
        out = Mesh()
        for (x, y, z, nx, ny, nz, r, g, b, a) in self.m.v:
            out.v.append((x * CS, y * CS, z * CS, nx, ny, nz, r, g, b, a))
        out.f = [list(f) for f in self.m.f]
        return out


class Face:
    """Footprint edge frame: P(s, o, z) -> (e, n, z); outward normal n."""

    def __init__(self, b, i, p0, p1, area_sign):
        self.b, self.i, self.p0, self.p1 = b, i, p0, p1
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        self.L = math.hypot(dx, dy)
        self.t = (dx / self.L, dy / self.L)
        self.n = (self.t[1], -self.t[0]) if area_sign > 0 else (-self.t[1], self.t[0])   # ccw: interior left, outward right
        self.bearing = math.degrees(math.atan2(self.n[0], self.n[1])) % 360

    def P(self, s, o, z):
        return (self.p0[0] + self.t[0] * s + self.n[0] * o, self.p0[1] + self.t[1] * s + self.n[1] * o, z)

    def D(self, ds, do, dz):
        return (self.t[0] * ds + self.n[0] * do, self.t[1] * ds + self.n[1] * do, dz)

    def quad(self, pts, rgb, alpha, out=(0, 1, 0), lift=0.0):
        self.b.q([self.P(*p) for p in pts], rgb, alpha, self.D(*out), lift)

    def box(self, s0, s1, o0, o1, z0, z1, rgb, alpha=255, skip=(), lift=0.0):
        f = {"-s": ([(s0, o0, z0), (s0, o1, z0), (s0, o1, z1), (s0, o0, z1)], (-1, 0, 0)),
             "+s": ([(s1, o0, z0), (s1, o1, z0), (s1, o1, z1), (s1, o0, z1)], (1, 0, 0)),
             "-o": ([(s0, o0, z0), (s1, o0, z0), (s1, o0, z1), (s0, o0, z1)], (0, -1, 0)),
             "+o": ([(s0, o1, z0), (s1, o1, z0), (s1, o1, z1), (s0, o1, z1)], (0, 1, 0)),
             "-z": ([(s0, o0, z0), (s1, o0, z0), (s1, o1, z0), (s0, o1, z0)], (0, 0, -1)),
             "+z": ([(s0, o0, z1), (s1, o0, z1), (s1, o1, z1), (s0, o1, z1)], (0, 0, 1))}
        for k, (pts, out) in f.items():
            if k not in skip:
                self.quad(pts, rgb, alpha, out, lift)


def col(c, k=1.0):
    return tuple(max(0, min(255, round(x * k))) for x in c)


# ---------------------------------------------------------------------------------------------------------------
# face construction
# ---------------------------------------------------------------------------------------------------------------
def _intervals_minus(lo, hi, cuts):
    """[lo,hi] minus the (z0,z1) cuts -> remaining intervals."""
    out, cur = [], lo
    for z0, z1 in sorted(cuts):
        if z0 > cur + EPS:
            out.append((cur, min(z0, hi)))
        cur = max(cur, z1)
    if cur < hi - EPS:
        out.append((cur, hi))
    return out


def check_openings(face_no, ops):
    for i, a in enumerate(ops):
        for b in ops[i + 1:]:
            if a["s"][0] < b["s"][1] - EPS and b["s"][0] < a["s"][1] - EPS and a["z"][0] < b["z"][1] - EPS and b["z"][0] < a["z"][1] - EPS:
                raise SystemExit(f"face {face_no}: openings {a['s']}x{a['z']} and {b['s']}x{b['z']} overlap")


def build_wall(face, spec, zb, ztop, wall, fspec, stats):
    """Wall rectangles around the openings (alpha 0), reveals and back planes (alpha 0), then the fills."""
    lift = wall.get("lift", 0.75)
    base = tuple(fspec.get("wall_color") or wall["color"])
    bands = list(fspec.get("bands") or wall.get("bands") or [])
    ops = fspec.get("openings", [])
    check_openings(face.i, ops)
    for o in ops:
        if o["s"][0] < -EPS or o["s"][1] > face.L + EPS or o["z"][0] < zb + EPS or o["z"][1] > ztop + EPS:
            raise SystemExit(f"face {face.i}: opening {o['s']}x{o['z']} leaves the wall (L {face.L:.2f}, z {zb}..{ztop})")
    s_cuts = sorted({0.0, face.L} | {round(v, 6) for o in ops for v in o["s"]})
    area_wall = 0.0
    for sa, sb in zip(s_cuts[:-1], s_cuts[1:]):
        if sb - sa < EPS:
            continue
        cuts = [tuple(o["z"]) for o in ops if o["s"][0] <= sa + EPS and o["s"][1] >= sb - EPS]
        for z0, z1 in _intervals_minus(zb, ztop, cuts):
            marks = sorted({z0, z1} | {v for bd in bands for v in bd["z"] if z0 < v < z1})
            for za, zc in zip(marks[:-1], marks[1:]):
                c = base
                for bd in bands:
                    if bd["z"][0] <= za + EPS and bd["z"][1] >= zc - EPS:
                        c = tuple(bd["color"])
                face.quad([(sa, 0, za), (sb, 0, za), (sb, 0, zc), (sa, 0, zc)], c, 0, (0, 1, 0), lift)
                area_wall += (sb - sa) * (zc - za)
    stats["wall_area"] = stats.get("wall_area", 0.0) + area_wall
    for o in ops:
        s0, s1 = o["s"]
        z0, z1 = o["z"]
        d = float(o.get("depth", 0.12))
        rev = tuple(o.get("reveal_color") or base)
        back = tuple(o.get("back_color") or (50, 50, 54))
        face.quad([(s0, -d, z0), (s1, -d, z0), (s1, -d, z1), (s0, -d, z1)], back, 0, (0, 1, 0), 0.3)           # back plane
        face.quad([(s0, 0, z0), (s0, -d, z0), (s0, -d, z1), (s0, 0, z1)], rev, 0, (1, 0, 0), lift * 0.6)       # reveals
        face.quad([(s1, 0, z0), (s1, -d, z0), (s1, -d, z1), (s1, 0, z1)], rev, 0, (-1, 0, 0), lift * 0.6)
        face.quad([(s0, 0, z1), (s1, 0, z1), (s1, -d, z1), (s0, -d, z1)], rev, 0, (0, 0, -1))
        face.quad([(s0, 0, z0), (s1, 0, z0), (s1, -d, z0), (s0, -d, z0)], rev, 0, (0, 0, 1), 0.6)
        fill_opening(face, o, d)
    stats["openings"] = stats.get("openings", 0) + len(ops)


def _frame(face, s0, s1, z0, z1, o, t, rgb):
    """Frame of width t on the plane o (protrudes 0.03)."""
    for a, b, c, dd in ((s0, s0 + t, z0, z1), (s1 - t, s1, z0, z1), (s0, s1, z0, z0 + t), (s0, s1, z1 - t, z1)):
        face.box(a, b, o, o + 0.03, c, dd, rgb, 255, skip=("-o",))


def fill_opening(face, o, d):
    s0, s1 = o["s"]
    z0, z1 = o["z"]
    fill = o.get("fill", "window")
    po = -d + 0.02
    frame = tuple(o.get("frame_color", (230, 230, 228)))
    glass = tuple(o.get("glass_color", (70, 84, 98)))
    if fill == "void":
        return
    if fill in ("window", "glazing"):
        t = 0.05 if fill == "window" else 0.06
        face.quad([(s0, po, z0), (s1, po, z0), (s1, po, z1), (s0, po, z1)], glass, 255, (0, 1, 0))
        _frame(face, s0, s1, z0, z1, po, t, frame)
        cols, rows = int(o.get("cols", 2)), int(o.get("rows", 1))
        for k in range(1, cols):
            sm = s0 + (s1 - s0) * k / cols
            face.box(sm - 0.025, sm + 0.025, po, po + 0.03, z0, z1, frame, 255, skip=("-o",))
        for k in range(1, rows):
            zm = z0 + (z1 - z0) * k / rows
            face.box(s0, s1, po, po + 0.03, zm - 0.025, zm + 0.025, frame, 255, skip=("-o",))
        if fill == "glazing" and o.get("riser"):          # stall riser under shop glazing
            r = o["riser"]
            face.quad([(s0, po + 0.01, z0), (s1, po + 0.01, z0), (s1, po + 0.01, z0 + r[0]), (s0, po + 0.01, z0 + r[0])],
                      tuple(r[1]), 255, (0, 1, 0))
        sill = o.get("sill")
        if sill:
            face.box(s0 - 0.08, s1 + 0.08, 0, sill.get("proud", 0.14), z0 - 0.07, z0, tuple(sill["color"]), 255)
        hood = o.get("hood")
        if hood:
            p = hood.get("proud", 0.35)
            face.quad([(s0 - 0.08, 0, z1 + 0.18), (s1 + 0.08, 0, z1 + 0.18), (s1 + 0.08, p, z1 + 0.05), (s0 - 0.08, p, z1 + 0.05)],
                      tuple(hood["color"]), 255, (0, 0.5, 1))
            face.quad([(s0 - 0.08, p, z1 + 0.05), (s1 + 0.08, p, z1 + 0.05), (s1 + 0.08, p, z1 - 0.02), (s0 - 0.08, p, z1 - 0.02)],
                      tuple(hood["color"]), 255, (0, 1, 0))
    elif fill == "door":
        pc = tuple(o.get("panel_color", (60, 90, 70)))
        face.quad([(s0, po, z0), (s1, po, z0), (s1, po, z1), (s0, po, z1)], pc, 255, (0, 1, 0))
        _frame(face, s0, s1, z0, z1, po, 0.06, frame)
        if o.get("glass"):                               # glazed upper part
            gz = z0 + (z1 - z0) * 0.35
            face.quad([(s0 + 0.1, po + 0.02, gz), (s1 - 0.1, po + 0.02, gz), (s1 - 0.1, po + 0.02, z1 - 0.1), (s0 + 0.1, po + 0.02, z1 - 0.1)],
                      glass, 255, (0, 1, 0))
        if int(o.get("leaves", 1)) == 2:
            sm = (s0 + s1) / 2
            face.box(sm - 0.02, sm + 0.02, po, po + 0.03, z0, z1, frame, 255, skip=("-o",))
        hs = s1 - 0.14 if o.get("handle", "right") == "right" else s0 + 0.14
        face.box(hs - 0.02, hs + 0.02, po, po + 0.07, z0 + (z1 - z0) * 0.45, z0 + (z1 - z0) * 0.55, (180, 180, 176), 255)
    elif fill == "shutter":
        pc = tuple(o.get("panel_color", (150, 152, 150)))
        face.quad([(s0, po, z0), (s1, po, z0), (s1, po, z1), (s0, po, z1)], pc, 255, (0, 1, 0))
        n = max(2, int((z1 - z0) / 0.14))
        dark = col(pc, 0.7)
        for k in range(1, n):
            zz = z0 + (z1 - z0) * k / n
            face.box(s0, s1, po, po + 0.012, zz - 0.008, zz + 0.008, dark, 255, skip=("-o",))
        face.box(s0, s1, po, po + 0.05, z1 - 0.1, z1, col(pc, 0.8), 255, skip=("-o",))      # shutter box
    else:
        raise SystemExit(f"unknown opening fill {fill}")


def build_awning(face, a):
    s0, s1 = a["s"]
    zl, dep, rise, val = float(a["z"]), float(a["depth"]), float(a.get("rise", 0.45)), float(a.get("valance", 0.3))
    c = tuple(a["color"])
    sc = tuple(a.get("stripe_color") or col(c, 0.82))
    n = max(1, int((s1 - s0) / 0.45))
    for k in range(n):                                   # canvas in stripes
        a0, a1 = s0 + (s1 - s0) * k / n, s0 + (s1 - s0) * (k + 1) / n
        cc = c if k % 2 == 0 else sc
        face.quad([(a0, 0, zl + rise), (a1, 0, zl + rise), (a1, dep, zl), (a0, dep, zl)], cc, 255, (0, 0.6, 1))
        face.quad([(a0, dep, zl), (a1, dep, zl), (a1, dep, zl - val), (a0, dep, zl - val)], cc, 255, (0, 1, 0))
    for s, out in ((s0, -1), (s1, 1)):
        face.quad([(s, 0, zl + rise), (s, dep, zl), (s, dep, zl - val), (s, 0, zl - val)], col(c, 0.85), 255, (out, 0, 0))
    face.quad([(s0, 0, zl + rise), (s1, 0, zl + rise), (s1, dep, zl), (s0, dep, zl)], col(c, 0.7), 255, (0, -0.6, -1))


def build_balcony(face, bc, stats):
    s0, s1 = bc["s"]
    z = float(bc["z"])
    dep = float(bc.get("depth", 0.8))
    rh = float(bc.get("rail_h", 1.1))
    slab = tuple(bc.get("slab_color", (150, 150, 148)))
    rail = tuple(bc.get("rail_color", (40, 42, 44)))
    face.box(s0, s1, 0, dep, z - 0.16, z, slab, 255, lift=0.4)
    sf = bc.get("solid_front")
    gap = float(bc.get("bar_gap", 0.13))
    if sf:
        face.box(s0, s1, dep - 0.08, dep, z, z + rh, tuple(sf), 255, lift=0.4)
    else:
        face.box(s0, s1, dep - 0.04, dep, z + rh - 0.05, z + rh, rail, 255)
        face.box(s0, s1, dep - 0.04, dep, z + 0.05, z + 0.1, rail, 255)
        n = max(2, int((s1 - s0) / gap))
        for k in range(n + 1):
            s = s0 + (s1 - s0) * k / n
            face.quad([(s - 0.012, dep - 0.02, z + 0.1), (s + 0.012, dep - 0.02, z + 0.1), (s + 0.012, dep - 0.02, z + rh), (s - 0.012, dep - 0.02, z + rh)],
                      rail, 255, (0, 1, 0))
            stats["bars"] = stats.get("bars", 0) + 1
    if bc.get("sides", True):
        for s, out in ((s0, -1), (s1, 1)):
            if sf:
                face.box(s - 0.04 if out < 0 else s, s if out < 0 else s + 0.04, 0, dep, z, z + rh, tuple(sf), 255, lift=0.4)
            else:
                face.quad([(s, 0, z + rh), (s, dep, z + rh), (s, dep, z + rh - 0.05), (s, 0, z + rh - 0.05)], rail, 255, (out, 0, 0))
                face.quad([(s, 0, z + 0.1), (s, dep, z + 0.1), (s, dep, z + rh), (s, 0, z + rh)], col(rail, 1.6), 255, (out, 0, 0))


def build_stair(face, st):
    s0, w, (z0, z1) = float(st["s0"]), float(st.get("width", 0.9)), st["z"]
    direction = int(st.get("dir", 1))
    run = (z1 - z0) / 0.18 * 0.27
    n = max(2, int(round((z1 - z0) / 0.18)))
    c = tuple(st.get("color", (80, 110, 90)))
    rc = tuple(st.get("rail_color", c))
    for k in range(n):
        sa = s0 + direction * run * k / n
        sb = s0 + direction * run * (k + 1) / n
        lo, hi = min(sa, sb), max(sa, sb)
        zt = z0 + (z1 - z0) * (k + 1) / n
        face.box(lo, hi, 0, w, zt - 0.05, zt, c, 255)
        face.box(lo, hi, 0.0, w, z0, zt - 0.05, col(c, 0.7), 255, skip=("-o", "-s", "+s", "+z"))
    face.quad([(s0, w, z0 + 0.9), (s0 + direction * run, w, z1 + 0.9), (s0 + direction * run, w, z1 + 0.84), (s0, w, z0 + 0.84)], rc, 255, (0, 1, 0))


# ---------------------------------------------------------------------------------------------------------------
# roof
# ---------------------------------------------------------------------------------------------------------------
def build_flat_roof(b, poly, area_sign, zt, roof):
    par = float(roof.get("parapet_m", 0.5))
    rc = tuple(roof.get("color", (120, 122, 124)))
    pc = tuple(roof.get("parapet_color", (190, 188, 182)))
    th = 0.14
    zr = zt - par
    tris = triangulate(poly)
    for i0, i1, i2 in tris:
        b.q([(*poly[i0], zr), (*poly[i1], zr), (*poly[i2], zr)], rc, 0, (0, 0, 1))
    n = len(poly)
    for i in range(n):
        p0, p1 = poly[i], poly[(i + 1) % n]
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        L = math.hypot(dx, dy)
        t = (dx / L, dy / L)
        inn = (t[1], -t[0]) if area_sign < 0 else (-t[1], t[0])          # inward normal (cw: interior is right of travel)
        q0 = (p0[0] + inn[0] * th, p0[1] + inn[1] * th)
        q1 = (p1[0] + inn[0] * th, p1[1] + inn[1] * th)
        b.q([(*q0, zr), (*q1, zr), (*q1, zt), (*q0, zt)], col(pc, 0.9), 255, (inn[0], inn[1], 0))      # parapet inner face
        b.q([(*p0, zt), (*p1, zt), (*q1, zt), (*q0, zt)], pc, 255, (0, 0, 1))                          # coping
    return {"roof_tris": len(tris)}


def roof_height_fn(poly, roof, zr_eave, zr_ridge, faces):
    c, u, v, hu, hv = obb(poly)
    typ = roof["type"]
    ridge_long = roof.get("ridge", "long") == "long"
    ax_r, ax_x = (u, v) if ridge_long else (v, u)       # ridge runs along ax_r; slope across ax_x
    hr, hx = (hu, hv) if ridge_long else (hv, hu)
    ov = float(roof.get("overhang_m", 0.3))
    hx2, hr2 = hx + ov, hr + ov

    def h(e, n):
        x = (e - c[0]) * ax_x[0] + (n - c[1]) * ax_x[1]
        r = (e - c[0]) * ax_r[0] + (n - c[1]) * ax_r[1]
        if typ == "gable":
            return zr_eave + (zr_ridge - zr_eave) * max(0.0, 1 - abs(x) / hx)
        if typ == "hip":
            return zr_eave + (zr_ridge - zr_eave) * max(0.0, min(1 - abs(x) / hx, (hr - abs(r)) / hx))
        if typ == "shed":
            return zr_eave + (zr_ridge - zr_eave) * max(0.0, min(1.0, (x / hx + 1) / 2))
        return zr_eave
    return h, (c, ax_r, ax_x, hr2, hx2, hr, hx)


def build_pitched_roof(b, poly, area_sign, zt_eave, zt_ridge, roof, faces, wall, stats):
    typ = roof["type"]
    rc = tuple(roof.get("color", (96, 96, 100)))
    c, u, v, hu, hv = obb(poly)
    ridge_long = roof.get("ridge", "long") == "long"
    ax_r, ax_x = (u, v) if ridge_long else (v, u)
    hr, hx = (hu, hv) if ridge_long else (hv, hu)
    ov = float(roof.get("overhang_m", 0.3))

    def P(r, x, z):
        return (c[0] + ax_r[0] * r + ax_x[0] * x, c[1] + ax_r[1] * r + ax_x[1] * x, z)
    zd = zt_eave - ov * (zt_ridge - zt_eave) / hx            # eave edge drops with the slope over the overhang
    R, X = hr + ov, hx + ov
    pieces = []
    if typ == "gable":
        pieces = [([P(-R, -X, zd), P(R, -X, zd), P(R, 0, zt_ridge), P(-R, 0, zt_ridge)], (0, 0)),
                  ([P(-R, X, zd), P(R, X, zd), P(R, 0, zt_ridge), P(-R, 0, zt_ridge)], (0, 0))]
    elif typ == "shed":
        he = int(roof.get("high_edge", 0))
        sgn = 1 if (faces[he].n[0] * ax_x[0] + faces[he].n[1] * ax_x[1]) > 0 else -1
        zlow = zt_eave - ov * (zt_ridge - zt_eave) / (2 * hx)
        pieces = [([P(-R, -X * sgn, zlow), P(R, -X * sgn, zlow), P(R, X * sgn, zt_ridge + ov * (zt_ridge - zt_eave) / (2 * hx)),
                    P(-R, X * sgn, zt_ridge + ov * (zt_ridge - zt_eave) / (2 * hx))], (0, 0))]
    elif typ == "hip":
        rr = max(hr - hx, 0.2)                              # ridge half-length
        pieces = [([P(-R, -X, zd), P(R, -X, zd), P(rr, 0, zt_ridge), P(-rr, 0, zt_ridge)], 0),
                  ([P(R, X, zd), P(-R, X, zd), P(-rr, 0, zt_ridge), P(rr, 0, zt_ridge)], 0),
                  ([P(R, -X, zd), P(R, X, zd), P(rr, 0, zt_ridge)], 0),
                  ([P(-R, X, zd), P(-R, -X, zd), P(-rr, 0, zt_ridge)], 0)]
    else:
        raise SystemExit(f"unknown roof type {typ}")
    for pts, _ in pieces:
        cen = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        zc = sum(p[2] for p in pts) / len(pts)
        # outward = away from the footprint centre horizontally and up
        out = (cen[0] - c[0], cen[1] - c[1], max(0.6, (zt_ridge - zt_eave)))
        if typ == "shed":
            out = (0, 0, 1)
        b.q(pts, rc, 0, out)
    # gable / hip / shed wall tops: fill between the eave line and the roof surface above each footprint edge
    stats["roof_pieces"] = len(pieces)
    h, _ = roof_height_fn(poly, roof, zt_eave, zt_ridge, faces)
    n = len(poly)
    for f in faces:
        p0, p1 = f.p0, f.p1
        ts = sorted({0.0, 1.0} | {k / 8 for k in range(1, 8)})
        pts_top = [(f.P(t * f.L, 0, h(*f.P(t * f.L, 0, 0)[:2]))) for t in ts]
        if max(p[2] for p in pts_top) - zt_eave < 0.05:
            continue
        poly_g = [f.P(0, 0, zt_eave), f.P(f.L, 0, zt_eave)] + pts_top[::-1]
        # drop collinear duplicates on the eave line
        poly_g = [p for k, p in enumerate(poly_g) if not (k >= 2 and abs(p[2] - zt_eave) < 1e-9 and k not in (0, 1))]
        if len(poly_g) >= 3:
            b.q(poly_g, tuple(wall["color"]), 0, (f.n[0], f.n[1], 0), wall.get("lift", 0.75))


# ---------------------------------------------------------------------------------------------------------------
# plan-fitted pitched roofs (FL-4876 reroof 2026-10-17). Opt-in: roof.fit = "plan" (or roof.parts); every other spec builds
# exactly the mesh it always did. The footprint is split into rectangular wings (maximal rectangles of the plan, snapped to
# roof.snap_m); each wing gets its own hip or gable roof; the roof height is the MAX over the wings, so wings meet at ridges,
# hips and valleys. All eaves are at one height, one pitch for the whole building, and the roof outline is the footprint
# offset by roof.overhang_m (a notch stays open). A convex plan that is not rectilinear (trapezoid, pentagon) gets one
# straight-skeleton hip (min of the edge planes). roof.parts gives the wings by hand in the plan frame printed as
# roof_frame_polygon: [{"rect": [x0, x1, y0, y1], "type": "hip|gable", "ridge": "x|y"}, {"poly": [[x, y], ...]}].
# A plan that fits neither (not rectilinear, not convex, no parts) or a shed falls back to the bounding-box roof and says so.
# ---------------------------------------------------------------------------------------------------------------
def _parea(p):
    return abs(signed_area(p)) if len(p) >= 3 else 0.0


def _dedup2(pts, tol=1e-9):
    out = []
    for p in pts:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > tol:
            out.append(p)
    while len(out) > 1 and math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) <= tol:
        out.pop()
    return out if len(out) >= 3 else []


def _clip(poly, a, b, c):
    """The part of the convex polygon with a*x + b*y + c >= 0."""
    out = []
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        dp = a * p[0] + b * p[1] + c
        dq = a * q[0] + b * q[1] + c
        if dp >= 0:
            out.append(p)
        if (dp > 0 and dq < 0) or (dp < 0 and dq > 0):
            t = dp / (dp - dq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return _dedup2(out)


def _subtract(P, C):
    """Convex P minus convex C -> convex pieces."""
    if len(C) < 3:
        return [P]
    if signed_area(C) < 0:
        C = C[::-1]
    edges = []
    for i in range(len(C)):
        p, q = C[i], C[(i + 1) % len(C)]
        a, b = -(q[1] - p[1]), q[0] - p[0]
        edges.append((a, b, -(a * p[0] + b * p[1])))
    inter = P
    for a, b, c in edges:
        inter = _clip(inter, a, b, c)
        if not inter:
            break
    if _parea(inter) < 1e-7:
        return [P]
    pieces, cur = [], P
    for a, b, c in edges:
        out = _clip(cur, -a, -b, -c)
        if _parea(out) > 1e-7:
            pieces.append(out)
        cur = _clip(cur, a, b, c)
        if not cur:
            break
    return pieces


def _in_poly(pt, poly):
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def _in_convex(poly, pt, tol=1e-9):
    sg = 1.0 if signed_area(poly) >= 0 else -1.0
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        if sg * ((q[0] - p[0]) * (pt[1] - p[1]) - (q[1] - p[1]) * (pt[0] - p[0])) < -tol:
            return False
    return True


def _dist_poly(pt, poly):
    best = 1e18
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        dx, dy = q[0] - p[0], q[1] - p[1]
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 < 1e-12 else max(0.0, min(1.0, ((pt[0] - p[0]) * dx + (pt[1] - p[1]) * dy) / L2))
        best = min(best, math.hypot(pt[0] - p[0] - t * dx, pt[1] - p[1] - t * dy))
    return best


def _frame_of(poly, u):
    v = (-u[1], u[0])
    xs = [p[0] * u[0] + p[1] * u[1] for p in poly]
    ys = [p[0] * v[0] + p[1] * v[1] for p in poly]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    return {"o": (u[0] * cx + v[0] * cy, u[1] * cx + v[1] * cy), "u": u, "v": v}


def _to_frame(fr, p):
    dx, dy = p[0] - fr["o"][0], p[1] - fr["o"][1]
    return (dx * fr["u"][0] + dy * fr["u"][1], dx * fr["v"][0] + dy * fr["v"][1])


def _from_frame(fr, x, y):
    return (fr["o"][0] + x * fr["u"][0] + y * fr["v"][0], fr["o"][1] + x * fr["u"][1] + y * fr["v"][1])


def _rectilinear(pf, snap):
    n = len(pf)
    return all(min(abs(pf[(i + 1) % n][0] - pf[i][0]), abs(pf[(i + 1) % n][1] - pf[i][1])) <= snap for i in range(n))


def _is_convex(poly, tol=0.04):
    sg = 1.0 if signed_area(poly) >= 0 else -1.0
    n = len(poly)
    for i in range(n):
        a, b, c = poly[i - 1], poly[i], poly[(i + 1) % n]
        e1, e2 = (b[0] - a[0], b[1] - a[1]), (c[0] - b[0], c[1] - b[1])
        l1, l2 = math.hypot(*e1), math.hypot(*e2)
        if l1 < 1e-9 or l2 < 1e-9:
            continue
        if sg * (e1[0] * e2[1] - e1[1] * e2[0]) / (l1 * l2) < -tol:
            return False
    return True


def _plan_frame(poly, snap):
    """-> (frame, polygon in that frame, rectilinear?). Tries the min-area box axis, then the long edges' directions."""
    c, u, v, hu, hv = obb(poly)
    cands = [u]
    edges = sorted(((math.hypot(poly[(i + 1) % len(poly)][0] - poly[i][0], poly[(i + 1) % len(poly)][1] - poly[i][1]), i)
                    for i in range(len(poly))), reverse=True)
    for L, i in edges:
        if L < 1.5:
            break
        q = poly[(i + 1) % len(poly)]
        e = _unit((q[0] - poly[i][0], q[1] - poly[i][1], 0))[:2]
        ang = math.atan2(e[1], e[0]) % (math.pi / 2)
        if all(min(abs(ang - (math.atan2(w[1], w[0]) % (math.pi / 2))), math.pi / 2 - abs(ang - (math.atan2(w[1], w[0]) % (math.pi / 2))))
               > math.radians(1.0) for w in cands):
            cands.append(e)
    for w in cands:
        fr = _frame_of(poly, w)
        pf = [_to_frame(fr, p) for p in poly]
        if _rectilinear(pf, snap):
            return fr, pf, True
    fr = _frame_of(poly, u)
    return fr, [_to_frame(fr, p) for p in poly], False


def _cluster(vals, snap):
    vals = sorted(vals)
    groups = [[vals[0]]]
    for v in vals[1:]:
        if v - groups[-1][0] <= snap:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [sum(g) / len(g) for g in groups]


def _cover_rects(pf, snap, leftover_m2=0.5):
    """Greedy cover of a rectilinear plan by maximal rectangles (snapped coordinates) -> [(x0, x1, y0, y1)]."""
    xs = _cluster([p[0] for p in pf], snap)
    ys = _cluster([p[1] for p in pf], snap)
    nx, ny = len(xs) - 1, len(ys) - 1
    if nx < 1 or ny < 1:
        return []
    fill = [[_in_poly(((xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2), pf) for j in range(ny)] for i in range(nx)]
    pre = [[0] * (ny + 1) for _ in range(nx + 1)]
    for i in range(nx):
        for j in range(ny):
            pre[i + 1][j + 1] = pre[i][j + 1] + pre[i + 1][j] - pre[i][j] + (1 if fill[i][j] else 0)

    def full(i0, i1, j0, j1):
        return pre[i1][j1] - pre[i0][j1] - pre[i1][j0] + pre[i0][j0] == (i1 - i0) * (j1 - j0)
    rects = []
    for i0 in range(nx):
        for i1 in range(i0 + 1, nx + 1):
            for j0 in range(ny):
                for j1 in range(j0 + 1, ny + 1):
                    if not full(i0, i1, j0, j1):
                        continue
                    if (i0 > 0 and full(i0 - 1, i0, j0, j1)) or (i1 < nx and full(i1, i1 + 1, j0, j1)) \
                            or (j0 > 0 and full(i0, i1, j0 - 1, j0)) or (j1 < ny and full(i0, i1, j1, j1 + 1)):
                        continue
                    rects.append((i0, i1, j0, j1))
    cw = [xs[i + 1] - xs[i] for i in range(nx)]
    ch = [ys[j + 1] - ys[j] for j in range(ny)]
    unc = {(i, j) for i in range(nx) for j in range(ny) if fill[i][j]}
    out = []
    while unc:
        if out and sum(cw[i] * ch[j] for i, j in unc) < leftover_m2:
            break
        best, bkey = None, None
        for r in rects:
            gain = sum(cw[i] * ch[j] for i in range(r[0], r[1]) for j in range(r[2], r[3]) if (i, j) in unc)
            key = (round(gain, 6), (xs[r[1]] - xs[r[0]]) * (ys[r[3]] - ys[r[2]]))
            if gain > 0 and (bkey is None or key > bkey):
                best, bkey = r, key
        if best is None:
            break
        out.append((xs[best[0]], xs[best[1]], ys[best[2]], ys[best[3]]))
        unc -= {(i, j) for i in range(best[0], best[1]) for j in range(best[2], best[3])}
    return out


def _wing_d(w, x, y):
    return min(kx * x + ky * y + k0 for kx, ky, k0 in w["planes"])


def _rect_wing(x0, x1, y0, y1, axis, typ, ov):
    """Wing record for a rectangle; axis 'x'/'y' = the ridge direction. Distances d >= 0 measured inward from each wall."""
    cross = [(0, 1, -y0), (0, -1, y1)] if axis == "x" else [(1, 0, -x0), (-1, 0, x1)]
    ends = [(1, 0, -x0), (-1, 0, x1)] if axis == "x" else [(0, 1, -y0), (0, -1, y1)]
    return {"kind": "rect", "type": typ, "axis": axis, "rect": [x0, x1, y0, y1], "cut": [False, False], "ov": ov,
            "planes": cross + (ends if typ == "hip" else []), "cross": cross, "ends": ends}


def _finish_rect_wing(w):
    x0, x1, y0, y1 = w["rect"]
    ov = w["ov"]
    ax = w["axis"]
    cl, ch_ = w["cut"]
    if ax == "x":
        X0, X1, Y0, Y1 = x0 - (0 if cl else ov), x1 + (0 if ch_ else ov), y0 - ov, y1 + ov
    else:
        X0, X1, Y0, Y1 = x0 - ov, x1 + ov, y0 - (0 if cl else ov), y1 + (0 if ch_ else ov)
    w["domain"] = [(X0, Y0), (X1, Y0), (X1, Y1), (X0, Y1)]
    if ax == "x":
        cross = [(0, 1, -y0), (0, -1, y1)]
        ends = [(1, 0, -x0), (-1, 0, x1)]
        rl, hxw = x1 - x0, (y1 - y0) / 2
    else:
        cross = [(1, 0, -x0), (-1, 0, x1)]
        ends = [(0, 1, -y0), (0, -1, y1)]
        rl, hxw = y1 - y0, (x1 - x0) / 2
    keep = [e for e, c in zip(ends, (cl, ch_)) if not c] if w["type"] == "hip" else []
    w["planes"] = cross + keep
    w["peak"] = hxw if not keep else min(hxw, rl / 2 if len(keep) == 2 else rl)
    return w


def _poly_wing(pts, ov):
    pts = [tuple(p) for p in pts]
    if signed_area(pts) < 0:
        pts = pts[::-1]
    planes = []
    n = len(pts)
    for i in range(n):
        p, q = pts[i], pts[(i + 1) % n]
        L = math.hypot(q[0] - p[0], q[1] - p[1])
        if L < 0.25:
            continue
        kx, ky = -(q[1] - p[1]) / L, (q[0] - p[0]) / L
        planes.append((kx, ky, -(kx * p[0] + ky * p[1])))
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    dom = [(min(xs) - ov - 1, min(ys) - ov - 1), (max(xs) + ov + 1, min(ys) - ov - 1), (max(xs) + ov + 1, max(ys) + ov + 1),
           (min(xs) - ov - 1, max(ys) + ov + 1)]
    for kx, ky, k0 in planes:
        dom = _clip(dom, kx, ky, k0 + ov)
    w = {"kind": "poly", "type": "hip", "axis": None, "planes": planes, "domain": dom, "poly": pts, "ov": ov, "cut": [False, False]}
    best, step = (-1e9, 0, 0), max(max(xs) - min(xs), max(ys) - min(ys)) / 24
    for i in range(25):
        for j in range(25):
            x, y = min(xs) + i * step, min(ys) + j * step
            if _in_poly((x, y), pts):
                best = max(best, (_wing_d(w, x, y), x, y))
    d0, bx, by = best
    for _ in range(40):
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
            d1 = _wing_d(w, bx + dx * step, by + dy * step)
            if d1 > d0:
                d0, bx, by = d1, bx + dx * step, by + dy * step
        step *= 0.8
    w["peak"] = d0
    return w


def _gable_cuts(wings):
    """Gable wings stop at the centre line of an earlier, wider wing that they run into (where that wing is at least as high)."""
    placed = []
    for w in wings:
        if w["kind"] == "rect" and w["type"] == "gable" and w.get("cuttable", True):
            ax = w["axis"]
            for W in placed:
                if W["kind"] != "rect" or W["axis"] == ax:
                    continue
                wx0, wx1, wy0, wy1 = w["rect"]
                Wx0, Wx1, Wy0, Wy1 = W["rect"]
                if ax == "x":
                    R, C, WR, WC = [wx0, wx1], (wy0, wy1), (Wx0, Wx1), (Wy0, Wy1)
                    # W is a y-ridge wing: its cross axis is x
                    WR, WC = (Wy0, Wy1), (Wx0, Wx1)
                else:
                    R, C, WR, WC = [wy0, wy1], (wx0, wx1), (Wx0, Wx1), (Wy0, Wy1)
                    WR, WC = (Wx0, Wx1), (Wy0, Wy1)
                # now: R = w's ridge interval, C = w's cross interval, WC = W's cross interval (same axis as R), WR = W's ridge interval (same axis as C)
                if min(C[1], WR[1]) - max(C[0], WR[0]) < 0.2 or min(R[1], WC[1]) - max(R[0], WC[0]) < 0.2:
                    continue
                cwid = (WC[0] + WC[1]) / 2
                for end in (0, 1):
                    if w["cut"][end]:
                        continue
                    a = R[end]
                    ok = (WC[0] - 0.3 <= a < cwid - 0.3) if end == 0 else (cwid + 0.3 < a <= WC[1] + 0.3)
                    if not ok:
                        continue
                    # W must be at least as high as w along the new end (everywhere across w's width)
                    good = True
                    t = C[0]
                    while t <= C[1] + 1e-9:
                        qx, qy = (cwid, t) if ax == "x" else (t, cwid)       # a point of the new end line (ridge coordinate = cwid)
                        if _wing_d(W, qx, qy) < w["peak"] - 0.12:          # W is as high as w along the cut (or lower by < 0.12 m)
                            good = False
                            break
                        t += 0.1
                    if not good:
                        continue
                    w["cut"][end] = True
                    R[end] = cwid
                    if ax == "x":
                        w["rect"][0 if end == 0 else 1] = cwid
                    else:
                        w["rect"][2 if end == 0 else 3] = cwid
        placed.append(w)
    return [w for w in wings if w["kind"] != "rect" or (w["rect"][1] - w["rect"][0] > 0.3 and w["rect"][3] - w["rect"][2] > 0.3)]


def plan_roof(poly, roof, typ):
    """Plan-fitted pitched roof -> {"fallback": reason} or a dict with the wings, the pieces and the audit (see module notes)."""
    ov = float(roof.get("overhang_m", 0.3))
    snap = float(roof.get("snap_m", PLAN_SNAP_M))
    if typ not in ("gable", "hip"):
        return {"fallback": f"roof type {typ} has no plan fit"}
    fr, pf, rect_ok = _plan_frame(poly, snap)
    info = {"frame": fr, "polygon_frame": pf}
    wings = []
    parts = roof.get("parts")
    if parts:
        for p in parts:
            if "poly" in p:
                wings.append(_poly_wing(p["poly"], ov))
            else:
                x0, x1, y0, y1 = (float(v) for v in p["rect"])
                if x1 <= x0 or y1 <= y0:
                    raise SystemExit(f"roof.parts rect {p['rect']} is empty")
                ax = {"x": "x", "u": "x", "y": "y", "v": "y"}.get(p.get("ridge"), "x" if (x1 - x0) >= (y1 - y0) * 0.95 else "y")
                w = _rect_wing(x0, x1, y0, y1, ax, p.get("type", typ), ov)
                w["cuttable"] = p.get("cut", True)
                wings.append(w)
    elif rect_ok:
        for (x0, x1, y0, y1) in _cover_rects(pf, snap):
            wings.append(_rect_wing(x0, x1, y0, y1, "x" if (x1 - x0) >= (y1 - y0) * 0.95 else "y", typ, ov))
    elif _is_convex(pf):
        if typ != "hip":
            return {"fallback": "a gable roof needs a rectilinear plan (this one is convex but not rectangular); use hip or give roof.parts", **info}
        wings.append(_poly_wing(pf, ov))
    else:
        return {"fallback": "plan is neither rectilinear nor convex; give roof.parts", **info}
    if not wings:
        return {"fallback": "no wings", **info}
    for w in wings:
        if w["kind"] == "rect":
            _finish_rect_wing(w)
    wings.sort(key=lambda w: (-round(w["peak"] / 0.1), -max(_parea(w["domain"]) / max(2 * w["peak"], 1e-6), 0.0), -_parea(w["domain"])))
    if any(w["kind"] == "rect" and w["type"] == "gable" for w in wings):
        wings = _gable_cuts(wings)
        for w in wings:
            if w["kind"] == "rect":
                _finish_rect_wing(w)
    peak = max(w["peak"] for w in wings)
    if roof.get("pitch_deg") is not None:
        s = math.tan(math.radians(float(roof["pitch_deg"])))
        ridge_m = s * peak
    elif roof.get("ridge_m") is not None:
        ridge_m = float(roof["ridge_m"])
        s = ridge_m / peak
    else:
        raise SystemExit("a plan-fitted roof needs roof.pitch_deg or roof.ridge_m")
    faces = []
    for wi, w in enumerate(wings):
        for pl in w["planes"]:
            dom = w["domain"]
            for pj in w["planes"]:
                if pj is not pl:
                    dom = _clip(dom, pj[0] - pl[0], pj[1] - pl[1], pj[2] - pl[2])
                    if not dom:
                        break
            if dom and _parea(dom) > 1e-6:
                faces.append({"w": wi, "plane": pl, "dom": dom})
    pieces = []
    for f in faces:
        cur = [f["dom"]]
        for g in faces:
            if g["w"] == f["w"]:
                continue
            da, db, dc = (g["plane"][i] - f["plane"][i] for i in range(3))
            if abs(da) < 1e-9 and abs(db) < 1e-9 and abs(dc) < 1e-9:
                C = g["dom"] if g["w"] < f["w"] else []
            else:
                C = _clip(g["dom"], da, db, dc)
            if not C or _parea(C) < 1e-7:
                continue
            nxt = []
            for P in cur:
                nxt += _subtract(P, C)
            cur = nxt
        for P in cur:
            if _parea(P) > 1e-7:
                pieces.append({"poly": P, "plane": f["plane"], "w": f["w"]})

    def height(x, y):
        best = None
        for w in wings:
            if _in_convex(w["domain"], (x, y)):
                v = s * _wing_d(w, x, y)
                best = v if best is None else max(best, v)
        return best
    # audit: roof outline vs the footprint (sampled every 0.1 m)
    xs = [p[0] for p in pf]
    ys = [p[1] for p in pf]
    step = 0.1
    unc = over = 0.0
    tol = ov * math.sqrt(2) + snap / 2 + 0.05
    nx = int((max(xs) - min(xs) + 2 * ov + 2) / step)
    ny = int((max(ys) - min(ys) + 2 * ov + 2) / step)
    for i in range(nx):
        for j in range(ny):
            x, y = min(xs) - ov - 1 + (i + 0.5) * step, min(ys) - ov - 1 + (j + 0.5) * step
            inside = _in_poly((x, y), pf)
            cov = any(_in_convex(w["domain"], (x, y)) for w in wings)
            if inside and not cov:
                unc += step * step
            elif cov and not inside and _dist_poly((x, y), pf) > tol:
                over += step * step
    return {**info, "wings": wings, "s": s, "ridge_m": ridge_m, "pieces": pieces, "height": height, "ov": ov, "peak": peak,
            "audit": {"uncovered_m2": round(unc, 3), "overhang_over_notch_m2": round(over, 3), "tol_m": round(tol, 3)}}


def build_plan_roof(b, plan, zt_eave, roof, faces, wall):
    rc = tuple(roof.get("color", (96, 96, 100)))
    fr, s = plan["frame"], plan["s"]
    for pc in plan["pieces"]:
        kx, ky, k0 = pc["plane"]
        pts = [(*_from_frame(fr, x, y), zt_eave + s * (kx * x + ky * y + k0)) for x, y in pc["poly"]]
        b.q(pts, rc, 0, (0, 0, 1))
    failed = 0
    for f in faces:
        n = max(2, int(math.ceil(f.L / 0.05)))
        prof = []
        for k in range(n + 1):
            sv = f.L * k / n
            e, nn, _ = f.P(sv, 0, 0)
            hh = plan["height"](*_to_frame(fr, (e, nn)))
            prof.append((sv, max(0.0, hh or 0.0)))
        if max(z for _, z in prof) < 0.05:
            continue
        runs, cur = [], []
        for k, (sv, z) in enumerate(prof):
            if z > 0.02:
                if not cur and k > 0:
                    cur.append((prof[k - 1][0], 0.0))
                cur.append((sv, z))
            elif cur:
                cur.append((sv, 0.0))
                runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
        for run in runs:
            keep = [run[0]]
            for k in range(1, len(run) - 1):
                (s0, z0), (s1, z1), (s2, z2) = run[k - 1], run[k], run[k + 1]
                if abs((z1 - z0) * (s2 - s1) - (z2 - z1) * (s1 - s0)) > 1e-5:
                    keep.append(run[k])
            keep.append(run[-1])
            poly_g = _dedup2(keep + [(keep[-1][0], 0.0), (keep[0][0], 0.0)]) if keep[-1][1] > 0 or keep[0][1] > 0 else _dedup2(keep)
            if len(poly_g) < 3:
                continue
            try:
                tris = triangulate(poly_g)
            except SystemExit:
                failed += 1
                continue
            for i0, i1, i2 in tris:
                b.q([f.P(poly_g[i][0], 0, zt_eave + poly_g[i][1]) for i in (i0, i1, i2)], tuple(wall["color"]), 0,
                    (f.n[0], f.n[1], 0), wall.get("lift", 0.75))
    st = {"roof_pieces": len(plan["pieces"]), "roof_fit": "plan", "roof_wings": len(plan["wings"]),
          "roof_pitch_deg": round(math.degrees(math.atan(s)), 1), "roof_ridge_m": round(plan["ridge_m"], 2),
          "roof_audit": plan["audit"]}
    if failed:
        st["roof_infill_failed"] = failed
    return st


PLAN_SNAP_M = 0.5


# ---------------------------------------------------------------------------------------------------------------
# top level
# ---------------------------------------------------------------------------------------------------------------
def load_footprint(spec, instances_json=None):
    rows = json.loads(Path(instances_json or BUILD / "building_instances.json").read_text())
    fid = spec.get("feature") or ("feature:building_footprint:way/" + spec["way"])
    mine = [r for r in rows if r["feature_id"] == fid]
    if len(mine) != 1:
        raise SystemExit(f"{fid}: {len(mine)} matches in building_instances.json")
    return mine[0]


def projection():
    m = json.loads((BUILD / "terrain_metadata.json").read_text())
    return m


def build(spec, row, meta):
    """-> (Mesh in world units, info dict). Pure apart from reading nothing."""
    cs = float(meta["content_scale"])
    assert abs(cs - CS) < 1e-9
    pw = [tuple(p) for p in row["bake_footprint"]]
    pe = [((x - meta["offset_x"]) / cs, (y - meta["offset_y"]) / cs) for x, y in pw]       # metres east/north of the map origin
    cx = sum(p[0] for p in pe) / len(pe)
    cy = sum(p[1] for p in pe) / len(pe)
    poly = [(p[0] - cx, p[1] - cy) for p in pe]
    asg = signed_area(poly)
    zb = float(spec.get("foot_m", -0.6))
    sh = [float(x) for x in spec["storey_heights_m"]]
    roof = spec.get("roof", {"type": "flat"})
    typ = roof.get("type", "flat")
    plan, fallback = None, None
    if roof.get("fit") not in (None, "bbox", "plan"):
        raise SystemExit(f"roof.fit must be 'bbox' or 'plan', not {roof.get('fit')!r}")
    if typ != "flat" and (roof.get("fit") == "plan" or roof.get("parts")):
        plan = plan_roof(poly, roof, typ)
        if plan.get("fallback"):
            fallback = plan["fallback"]
            print(f"ROOF FALLBACK to the bounding-box {typ} roof: {fallback}", file=sys.stderr)
            plan_frame = plan.get("polygon_frame")
            plan = None
        else:
            plan_frame = plan["polygon_frame"]
            roof = dict(roof, ridge_m=plan["ridge_m"])
    par = float(roof.get("parapet_m", 0.5)) if typ == "flat" else 0.0
    ztop_walls = sum(sh)
    H = float(spec.get("height_m") or row["roof_height_m"])
    expect = ztop_walls + par if typ == "flat" else ztop_walls + float(roof.get("ridge_m", 0.0))
    if abs(expect - H) > 0.75:
        raise SystemExit(f"height_m {H} disagrees with storeys {sh} + roof ({expect:.2f}); fix the spec")
    zt = H if typ == "flat" else ztop_walls
    wall = dict(spec["wall"])
    bld = B()
    faces = [Face(bld, i, poly[i], poly[(i + 1) % len(poly)], asg) for i in range(len(poly))]
    stats = {}
    fdefault = spec.get("faces", {}).get("default", {})
    zc_floor = [0.0]
    for h in sh:
        zc_floor.append(zc_floor[-1] + h)
    pl = wall.get("plinth")
    fl = wall.get("floor_lines")
    for f in faces:
        fspec = dict(fdefault)
        fspec.update(spec.get("faces", {}).get(str(f.i), {}))
        wtop = zt if typ == "flat" else ztop_walls
        build_wall(f, spec, zb, wtop, wall, fspec, stats)
        if pl:
            f.box(0, f.L, 0, 0.04, 0, pl["height_m"], tuple(pl["color"]), 255, skip=("-o", "-s", "+s", "-z"))
        if fl:
            for k in range(1, len(sh)):
                f.box(0, f.L, 0, fl.get("proud_m", 0.04), zc_floor[k] - fl.get("thick_m", 0.12) / 2, zc_floor[k] + fl.get("thick_m", 0.12) / 2,
                      tuple(fl["color"]), 255, skip=("-o", "-s", "+s"))
        for a in fspec.get("awnings", []):
            build_awning(f, a)
        for bd in fspec.get("bands_proud", []):
            f.box(bd["s"][0], bd["s"][1], 0, bd.get("proud", 0.05), bd["z"][0], bd["z"][1], tuple(bd["color"]), 255, skip=("-o",))
        for dc in fspec.get("decals", []):                 # posters, notice paper, noren, sign blocks: flat colour on a plane
            o = float(dc.get("o", 0.04))
            f.quad([(dc["s"][0], o, dc["z"][0]), (dc["s"][1], o, dc["z"][0]), (dc["s"][1], o, dc["z"][1]), (dc["s"][0], o, dc["z"][1])],
                   tuple(dc["color"]), 255, (0, 1, 0), float(dc.get("lift", 0.0)))   # "lift" 0.75 = same shading as the wall (a decal that repaints part of a wall)
        for bx in fspec.get("boxes", []):                  # projecting boxes: oriel / bay windows, plant boxes, steps
            f.box(bx["s"][0], bx["s"][1], 0, bx["depth"], bx["z"][0], bx["z"][1], tuple(bx["color"]), 255, lift=0.5)
            if bx.get("glass"):
                m = float(bx.get("margin", 0.15))
                f.quad([(bx["s"][0] + m, bx["depth"] + 0.012, bx["z"][0] + m), (bx["s"][1] - m, bx["depth"] + 0.012, bx["z"][0] + m),
                        (bx["s"][1] - m, bx["depth"] + 0.012, bx["z"][1] - m), (bx["s"][0] + m, bx["depth"] + 0.012, bx["z"][1] - m)],
                       tuple(bx["glass"]), 255, (0, 1, 0))
        for bc in fspec.get("balconies", []):
            build_balcony(f, bc, stats)
        for p in fspec.get("pipes", []):
            w = float(p.get("w", 0.1))
            f.box(p["s"] - w / 2, p["s"] + w / 2, 0, w * 0.8, p["z"][0], p["z"][1], tuple(p.get("color", (110, 110, 108))), 255)
        for st in fspec.get("stairs", []):
            build_stair(f, st)
    if typ == "flat":
        stats.update(build_flat_roof(bld, poly, asg, zt, roof))
    elif plan is not None:
        stats.update(build_plan_roof(bld, plan, ztop_walls, roof, faces, wall))
        stats["roof_frame_polygon"] = [[round(x, 2), round(y, 2)] for x, y in plan_frame]
        stats["roof_wing_rects"] = [[round(v, 2) for v in w["rect"]] + [w["type"]] if w["kind"] == "rect"
                                    else [[round(x, 2), round(y, 2)] for x, y in w["poly"]] for w in plan["wings"]]
    else:
        if fallback:
            stats["roof_fallback"] = fallback
            if plan_frame:
                stats["roof_frame_polygon"] = [[round(x, 2), round(y, 2)] for x, y in plan_frame]
        build_pitched_roof(bld, poly, asg, ztop_walls, ztop_walls + float(roof.get("ridge_m", 2.0)), roof, faces, wall, stats)
    info = {"origin_E": meta["origin_easting"] + cx, "origin_N": meta["origin_northing"] + cy, "height_m": H, "faces": [
        {"i": f.i, "len_m": round(f.L, 2), "outward_bearing": round(f.bearing)} for f in faces], "stats": stats,
        "polygon_m": poly, "storeys": len(sh), "area_sign": asg}
    m = bld.scaled()
    info["vertices"], info["faces_n"] = len(m.v), len(m.f)
    return m, info


def preview(mesh, path, bearing, pitch=28.0, size=900, above=False):
    """Flat-shaded orthographic painter's-algorithm render (no browser). Default view (unchanged): the camera is BELOW the
    horizon plane, so roofs hide behind the walls; above=True (--above) looks down at the building from `pitch` degrees."""
    from PIL import Image, ImageDraw
    th, ph = math.radians(bearing), math.radians(pitch)
    right = (math.cos(th), -math.sin(th), 0.0)                      # screen right for a viewer looking along bearing
    fwd = (math.sin(th), math.cos(th), 0.0)

    def proj(p):
        x = p[0] * right[0] + p[1] * right[1]
        d = p[0] * fwd[0] + p[1] * fwd[1]
        if above:
            return x, p[2] * math.cos(ph) + d * math.sin(ph), d * math.cos(ph) - p[2] * math.sin(ph)
        y = p[2] * math.cos(ph) - d * math.sin(ph)
        return x, y, d * math.cos(ph) + p[2] * math.sin(ph)
    polys = []
    for f in mesh.f:
        vs = [mesh.v[i] for i in f]
        pr = [proj(v[:3]) for v in vs]
        depth = sum(p[2] for p in pr) / len(pr)
        n = vs[0][3:6]
        lit = 0.5 + 0.5 * max(0.0, (n[0] * 0.707 + n[1] * 0.0 + n[2] * 0.707))
        polys.append((depth, [(p[0], p[1]) for p in pr], tuple(int(c * lit) for c in vs[0][6:9])))
    xs = [q[0] for _, pts, _ in polys for q in pts]
    ys = [q[1] for _, pts, _ in polys for q in pts]
    sc = (size - 40) / max(max(xs) - min(xs), max(ys) - min(ys))
    img = Image.new("RGB", (size, size), (200, 214, 230))
    d = ImageDraw.Draw(img)
    for _, pts, c in sorted(polys, key=lambda t: -t[0]):               # far first (depth grows away from the viewer)
        d.polygon([(20 + (x - min(xs)) * sc, size - 20 - (y - min(ys)) * sc) for x, y in pts], fill=c)
    img.save(path)


def ground_from_map(map_path, poly_en_world):
    """Median raw terrain height over the footprint's vertices and edge midpoints (bilinear on the 2-unit vertex grid)."""
    import numpy as np
    sys.path.insert(0, str(HERE))
    from a3d_terrain_extend import read_terrain
    data = Path(map_path).read_bytes()
    n, _, tr = read_terrain(data)
    idx = {(int(r["x"]), int(r["y"])): k for k, r in enumerate(tr)}
    vals = []
    pts = list(poly_en_world) + [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(poly_en_world, poly_en_world[1:] + poly_en_world[:1])]
    for x, y in pts:
        px, py = int(x // 8), int(y // 8)
        if (px, py) not in idx:       # footprint vertex or edge midpoint outside the terrain patch grid (map edge): no ground there
            continue
        r = tr[idx[(px, py)]]
        fx, fy = (x - px * 8) / 2, (y - py * 8) / 2
        i, j = min(int(fx), 3), min(int(fy), 3)
        a, c = fx - i, fy - j
        h = r["height"].astype(float)
        vals.append(h[j, i] * (1 - a) * (1 - c) + h[j, i + 1] * a * (1 - c) + h[j + 1, i] * (1 - a) * c + h[j + 1, i + 1] * a * c)
    return float(np.median(vals)), float(min(vals)), float(max(vals))


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--preview")
    ap.add_argument("--bearing", type=float, default=30.0)
    ap.add_argument("--pitch", type=float, default=28.0)
    ap.add_argument("--above", action="store_true", help="--preview looks down at the building (roofs visible); default view unchanged")
    ap.add_argument("--map", default=str(BUILD / "output.a3d"))
    ap.add_argument("--instances")
    ap.add_argument("--edits-dir", help="where --write puts <way>_add.json (default: the pilot edits folder)")
    ap.add_argument("--by", help="'by' of the add record (default: the pilot's)")
    ap.add_argument("--rev", help="revision tag in the mesh file name: building_hand_<way>_<rev>_20261017.akm (a re-roof 'r', 'r2'); default none")
    a = ap.parse_args(argv)
    spec = json.loads(Path(a.spec).read_text())
    for k in ("basis", "wall", "storey_heights_m"):
        if not spec.get(k):
            raise SystemExit(f"spec needs '{k}'")
    row = load_footprint(spec, a.instances)
    meta = projection()
    mesh, info = build(spec, row, meta)
    way = row["feature_id"].split("/")[-1]
    print(f"way {way}: {info['vertices']} vertices, {info['faces_n']} faces, height {info['height_m']} m, storeys {info['storeys']}")
    for f in info["faces"]:
        print(f"  face {f['i']}: {f['len_m']} m, outward bearing {f['outward_bearing']}")
    print("  stats", info["stats"])
    if a.preview:
        preview(mesh, a.preview, a.bearing, a.pitch, above=a.above)
        print("preview ->", a.preview)
    if not a.write:
        return 0
    name = f"building_hand_{way}_{a.rev}_{TAG}.akm" if a.rev else f"building_hand_{way}_{TAG}.akm"
    path = BUILD / "meshes" / name
    comment = (f"FL-4876 hand-authored building way/{way} (spec {Path(a.spec).name}); world units (metres*5.132), origin = footprint "
               f"vertex mean, z=0 ground; alpha 0 collides, alpha 255 presentation")
    text = akm_text(mesh, comment)
    if path.exists() and path.read_text(encoding="utf-8") != text:
        raise SystemExit(f"{path} exists with other content; change the spec revision (never overwrite a mesh)")
    if not path.exists():
        path.write_text(text, encoding="utf-8")
    med, lo, hi = ground_from_map(a.map, [tuple(p) for p in row["bake_footprint"]])
    cs = float(meta["content_scale"])
    note = f"ground raw {med:.1f} (vertex range {lo:.1f}..{hi:.1f} = {(hi - lo) / RAW_PER_M:.2f} m), elev {6.5 + (med - 128) / RAW_PER_M:.2f} m"
    print(" ", note)
    edits = [{"inst": f"building_hand_{way}_{TAG}", "op": "add", "mesh": name, "E": round(info["origin_E"], 4), "N": round(info["origin_N"], 4),
              "z_world": round(med, 2), "bearing_deg": 0.0,
              "basis": spec["basis"] if isinstance(spec["basis"], str) else spec["basis"].get("summary", json.dumps(spec["basis"], ensure_ascii=False)[:900]),
              "by": a.by or BY}]
    out = (Path(a.edits_dir) if a.edits_dir else PILOT / "edits") / f"{way}_add.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(edits, indent=1, ensure_ascii=False) + "\n")
    print("mesh ->", path, "\nedits ->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
