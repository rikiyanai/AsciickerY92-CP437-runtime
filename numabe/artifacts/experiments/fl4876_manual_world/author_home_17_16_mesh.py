#!/usr/bin/env python3
"""author_home_17_16_mesh.py [--rev N] [--write] -- hand-authored mesh of the operator's home, 17-16 Den-en-chofu Honcho
(footprint way/725138644), for the LIVE Numabe map (FL-4876, 2026-10-17).  No build, no pipeline file touched.

Why: the live instance building_mass_way_725138644 (mass_way_725138644.akm + attach_way_725138644.akm) reads from the lane
as a plain grey box with dark rectangles and a generic balcony.  The operator's two photos (operator_photos/
op_89ad6fb1448be364.png facing the house SE; op_a637d8482424ccc5.png looking NE up the lane) show a two-storey cream house with
dark brown timber trim, a steep terracotta tiled gable roof, a dark bay box on the upper floor, a lower terracotta porch roof
over the carport, plus a pink crape myrtle and a cloud-pruned pine in the front garden.

Writes (new names only, never overwrites a different file):
  home_17_16_house_<TAG>.akm     the house: walls on the exact footprint, gable roof, trim, windows, bay, porch roof
  home_crape_myrtle_<TAG>.akm    H001: pink-flowering crape myrtle (replaces the dark-green box)
  home_niwaki_<TAG>.akm          H002: cloud-pruned pine (replaces the dark-green box)
  assess/live_fix_20261017/home/home_edits[_vN].json   the a3d_edit.py records (apply with a3d_edit.py apply)

FRAME.  The house instance sits at the footprint's mass origin O = E380037.85 N3938703.83 with z = ground, identity rotation, so
mesh x/y are (E - O_E) * 5.132, (N - O_N) * 5.132 and z is metres * 5.132 (world units; content_scale from the build's
terrain_metadata.json; the transform's z scale 16 turns that into height units).  Authoring frame (metres): s along the NW lane
edge from its SW end A toward NE (bearing 29), o outward toward the lane (bearing 299), z up; (s, o, z) is right-handed.
Footprint corners: A=(380031.85,3938702.65), 7.9 m along the lane edge, 9.35 m deep to the SE; they match the old mass mesh.
ROOF.  Gable, eaves 5.88 m, ridge 8.7 m (FAILURE_LOG FL-4876 roof_fields_fitted_s15_v2 home gable 5.88/8.7 m; LiDAR roof raw
profile rises across the lane direction and stays level along bearing 119, so the ridge runs 119/299 and the gable end faces the
lane).  Collision: alpha 0 = walls and roof slabs (as the old mass), alpha 255 = trim, panes, porch, hoods (presentation).
SHADING.  The editor/engine light is (1,0,1)/sqrt2 with shade 0.5+0.5*max(0, n.L): a wall facing NW or SW renders at 0.5, so a
cream wall reads grey.  Wall faces therefore carry a vertex normal tilted toward +z (WALL_LIFT), a baked sky-light.  Roofs and
trim keep true normals.
"""
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from author_temple_meshes import Mesh, akm_text    # noqa: E402

ROOT = HERE.parents[2]
BUILD = ROOT / ".run/fl4876_builds/live_numabe"
OUT_DIR = HERE / "assess/live_fix_20261017/home"
CS = 5.1320023927966725
BY = "sonnet home agent 20261017"
REV = int(sys.argv[sys.argv.index("--rev") + 1]) if "--rev" in sys.argv else 1
TAG = "20261017" + ("" if REV == 1 else "_v%d" % REV)

O = (380037.85, 3938703.83)                 # instance origin (mass origin)
A = (380031.85, 3938702.65)                 # SW end of the NW (lane) edge
U = (math.sin(math.radians(29)), math.cos(math.radians(29)))      # s axis: along the lane edge toward NE
NO = (math.sin(math.radians(299)), math.cos(math.radians(299)))   # o axis: outward toward the lane
W, D = 7.9, 9.35                            # footprint: along the lane, depth
Z_FOOT = -1.2                               # walls run below the ground so no gap shows on slopes
Z_FLOOR2, Z_EAVE, Z_RIDGE = 3.0, 5.88, 8.7
SLOPE = (Z_RIDGE - Z_EAVE) / (W / 2)
WALL_LIFT = 0.75

CREAM = (250, 242, 222)
CREAM_SH = (232, 222, 200)
TIMBER = (66, 46, 36)
TIMBER_L = (92, 64, 48)
TILE_A = (208, 100, 58) if REV > 1 else (206, 98, 56)     # rev 2: softer course contrast (rev 1 read as stripes in Classic)
TILE_B = (198, 91, 52) if REV > 1 else (184, 80, 46)
TILE_RIDGE = (150, 62, 40)
GLASS = (52, 62, 76)
GLASS_L = (92, 112, 130)
DARKBOX = (70, 58, 50)
PIPE = (70, 66, 62)
CARPORT = (96, 90, 84)
STONE = (206, 200, 190)


class H:
    """Mesh builder in the authoring frame (s, o, z) metres."""

    def __init__(self):
        self.m = Mesh()

    def q(self, pts, rgb, alpha, out, lift=0.0):
        first = len(self.m.v)
        self.m.quad(pts, rgb, alpha, out)
        if lift:
            for i in range(first, len(self.m.v)):
                x, y, z, nx, ny, nz, r, g, b, a = self.m.v[i]
                nx, ny, nz = nx * (1 - lift), ny * (1 - lift), nz * (1 - lift) + lift
                ln = math.sqrt(nx * nx + ny * ny + nz * nz)
                self.m.v[i] = (x, y, z, nx / ln, ny / ln, nz / ln, r, g, b, a)

    def box(self, s0, s1, o0, o1, z0, z1, rgb, a, skip=(), lift=0.0):
        f = {
            "-s": ([(s0, o0, z0), (s0, o1, z0), (s0, o1, z1), (s0, o0, z1)], (-1, 0, 0)),
            "+s": ([(s1, o0, z0), (s1, o1, z0), (s1, o1, z1), (s1, o0, z1)], (1, 0, 0)),
            "-o": ([(s0, o0, z0), (s1, o0, z0), (s1, o0, z1), (s0, o0, z1)], (0, -1, 0)),
            "+o": ([(s0, o1, z0), (s1, o1, z0), (s1, o1, z1), (s0, o1, z1)], (0, 1, 0)),
            "-z": ([(s0, o0, z0), (s1, o0, z0), (s1, o1, z0), (s0, o1, z0)], (0, 0, -1)),
            "+z": ([(s0, o0, z1), (s1, o0, z1), (s1, o1, z1), (s0, o1, z1)], (0, 0, 1)),
        }
        for k, (pts, out) in f.items():
            if k not in skip:
                self.q(pts, rgb, a, out, lift)

    def bar(self, p0, p1, w, rgb, a, w2=None):
        """Square section bar between two points."""
        t = _unit(tuple(p1[i] - p0[i] for i in range(3)))
        r = (0, 0, 1) if abs(t[2]) < 0.9 else (1, 0, 0)
        u1 = _unit(_cross(r, t))
        u2 = _cross(t, u1)
        h1, h2 = w / 2, (w2 if w2 is not None else w) / 2

        def c(p, s1, s2):
            return tuple(p[i] + u1[i] * s1 * h1 + u2[i] * s2 * h2 for i in range(3))
        for (s1, s2), (t1, t2), out in (((-1, -1), (1, -1), tuple(-x for x in u2)), ((1, -1), (1, 1), u1),
                                        ((1, 1), (-1, 1), u2), ((-1, 1), (-1, -1), tuple(-x for x in u1))):
            self.q([c(p0, s1, s2), c(p0, t1, t2), c(p1, t1, t2), c(p1, s1, s2)], rgb, a, out)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a):
    n = math.sqrt(sum(c * c for c in a))
    return tuple(c / n for c in a)


def to_world(m, z0=0.0):
    """Authoring (s, o, z) metres -> instance-local world units (x = E, y = N, z up), normals rotated."""
    out = Mesh()
    for (s, o, z, ns, no, nz, r, g, b, a) in m.v:
        E = A[0] + U[0] * s + NO[0] * o
        N = A[1] + U[1] * s + NO[1] * o
        nE = U[0] * ns + NO[0] * no
        nN = U[1] * ns + NO[1] * no
        out.v.append(((E - O[0]) * CS, (N - O[1]) * CS, (z - z0) * CS, nE, nN, nz, r, g, b, a))
    out.f = [list(f) for f in m.f]
    return out


# --------------------------------------------------------------------------------------------------------------
# house
# --------------------------------------------------------------------------------------------------------------
def tile_slope(h, p_low, p_high, o_a, o_b, rgb_a, rgb_b, course, out, alpha):
    """Tile courses parallel to the eave: p_low=(s,z) eave edge, p_high=(s,z) ridge side; o from o_a to o_b."""
    (s0, z0), (s1, z1) = p_low, p_high
    ln = math.hypot(s1 - s0, z1 - z0)
    n = max(1, int(round(ln / course)))
    for i in range(n):
        t0, t1 = i / n, (i + 1) / n
        a = (s0 + (s1 - s0) * t0, z0 + (z1 - z0) * t0)
        b = (s0 + (s1 - s0) * t1, z0 + (z1 - z0) * t1)
        h.q([(a[0], o_a, a[1]), (a[0], o_b, a[1]), (b[0], o_b, b[1]), (b[0], o_a, b[1])],
            rgb_a if i % 2 == 0 else rgb_b, alpha, out)


def window(h, s0, s1, z0, z1, o, rgb_glass=GLASS, mull=1, face=0, sill=True):
    """Dark-framed window on the wall plane o (facing +o), glass pane with a mullion; frame protrudes 0.09 m."""
    f = 0.11
    h.box(s0, s1, o, o + 0.09, z0, z1, TIMBER, 255, skip=("-o", "-s", "+s", "-z", "+z") if False else ("-o",))
    h.q([(s0 + f, o + 0.1, z0 + f), (s1 - f, o + 0.1, z0 + f), (s1 - f, o + 0.1, z1 - f), (s0 + f, o + 0.1, z1 - f)],
        rgb_glass, 255, (0, 1, 0))
    for k in range(1, mull + 1):
        sm = s0 + (s1 - s0) * k / (mull + 1)
        h.box(sm - 0.035, sm + 0.035, o + 0.095, o + 0.14, z0 + f, z1 - f, TIMBER, 255, skip=("-o", "-z", "+z"))
    zm = (z0 + z1) / 2
    h.box(s0 + f, s1 - f, o + 0.095, o + 0.14, zm - 0.035, zm + 0.035, TIMBER, 255, skip=("-o", "-s", "+s"))
    if sill:
        h.box(s0 - 0.08, s1 + 0.08, o, o + 0.2, z0 - 0.08, z0, TIMBER_L, 255)


def side_window(h, sx, o0, o1, z0, z1, out_s, mull=1):
    """Window on a side wall plane s = sx facing out_s (+1 NE side, -1 SW side)."""
    d = 0.09 * out_s
    f = 0.11
    lo, hi = (sx, sx + d) if out_s > 0 else (sx + d, sx)
    h.box(lo, hi, o0, o1, z0, z1, TIMBER, 255, skip=("-s",) if out_s > 0 else ("+s",))
    p = sx + 0.1 * out_s
    h.q([(p, o0 + f, z0 + f), (p, o1 - f, z0 + f), (p, o1 - f, z1 - f), (p, o0 + f, z1 - f)], GLASS, 255, (out_s, 0, 0))
    om = (o0 + o1) / 2
    lo, hi = (p, p + 0.04 * out_s) if out_s > 0 else (p + 0.04 * out_s, p)
    h.box(lo, hi, om - 0.035, om + 0.035, z0 + f, z1 - f, TIMBER, 255, skip=("-s",) if out_s > 0 else ("+s",))


def build_house():
    h = H()
    # ---- walls (collide) ---------------------------------------------------------------------------------
    h.box(0, W, -D, 0, Z_FOOT, Z_EAVE, CREAM, 0, skip=("+z",), lift=WALL_LIFT)
    h.q([(0, 0, Z_EAVE), (W, 0, Z_EAVE), (W / 2, 0, Z_RIDGE)], CREAM, 0, (0, 1, 0), WALL_LIFT)       # lane gable end
    h.q([(0, -D, Z_EAVE), (W, -D, Z_EAVE), (W / 2, -D, Z_RIDGE)], CREAM, 0, (0, -1, 0), WALL_LIFT)   # rear gable end
    # ---- gable roof (collides): tile courses on both slopes, front/rear overhang ---------------------------
    ov_s, ov_f, ov_b = 0.65, 0.6, 0.5
    ze = Z_EAVE - ov_s * SLOPE
    o_hi, o_lo = ov_f, -D - ov_b
    tile_slope(h, (-ov_s, ze), (W / 2, Z_RIDGE), o_lo, o_hi, TILE_A, TILE_B, 0.42,
               _unit((-SLOPE, 0, 1)), 0)
    tile_slope(h, (W + ov_s, ze), (W / 2, Z_RIDGE), o_lo, o_hi, TILE_A, TILE_B, 0.42,
               _unit((SLOPE, 0, 1)), 0)
    h.box(W / 2 - 0.2, W / 2 + 0.2, o_lo - 0.04, o_hi + 0.04, Z_RIDGE - 0.02, Z_RIDGE + 0.2, TILE_RIDGE, 255,
          skip=("-z",))
    th = 0.14
    for sgn, s_e in ((-1, -ov_s), (1, W + ov_s)):                  # eave fascia (dark board) front + underside soffit
        h.q([(s_e, o_lo, ze - th), (s_e, o_hi, ze - th), (s_e, o_hi, ze), (s_e, o_lo, ze)], TIMBER, 255, (sgn, 0, 0))
        s_w = 0 if sgn < 0 else W
        h.q([(s_e, o_lo, ze - th), (s_e, o_hi, ze - th), (s_w, o_hi, Z_EAVE - th), (s_w, o_lo, Z_EAVE - th)],
            TIMBER_L, 255, _unit((-sgn * SLOPE, 0, -1)))
    for o_e, sgn_o in ((o_hi, 1), (o_lo, -1)):                     # gable overhang: soffit and dark bargeboards
        for a_s, b_s in ((-ov_s, W / 2), (W + ov_s, W / 2)):
            za = ze if a_s < 0 or a_s > W else Z_EAVE
            sgn = -1 if a_s < 0 else 1
            h.q([(a_s, o_e, ze - th), (b_s, o_e, Z_RIDGE - th), (b_s, 0 if sgn_o > 0 else -D, Z_RIDGE - th),
                 (a_s, 0 if sgn_o > 0 else -D, ze - th)], TIMBER_L, 255, _unit((-sgn * SLOPE, 0, -1)))
            bw = 0.42                                              # bargeboard: broad dark board on the rake
            h.q([(a_s, o_e, ze - bw), (b_s, o_e, Z_RIDGE - bw), (b_s, o_e, Z_RIDGE + 0.04), (a_s, o_e, ze + 0.04)],
                TIMBER, 255, (0, sgn_o, 0))
    # ---- lane face (o = 0): timber frame, gable half-timbering, windows, bay, downspout --------------------
    t = 0.09
    h.box(0, 0.22, 0, t, Z_FOOT + 1.2, Z_EAVE, TIMBER, 255, skip=("-o",))                         # corner posts
    h.box(W - 0.22, W, 0, t, Z_FOOT + 1.2, Z_EAVE, TIMBER, 255, skip=("-o",))
    h.box(0, W, 0, t, Z_FLOOR2 - 0.14, Z_FLOOR2 + 0.14, TIMBER, 255, skip=("-o",))                 # floor-line band
    h.box(0, W, 0, t, Z_EAVE - 0.24, Z_EAVE, TIMBER, 255, skip=("-o",))                            # tie beam under the gable
    h.box(0, W, 0, 0.05, 0.0, 0.3, STONE, 255, skip=("-o",))                                        # plinth
    for s in (0.85, 3.95, 5.9, 7.1):                                                               # upper-floor boards
        if abs(s - 3.95) < 0.01:
            continue
        h.box(s - 0.06, s + 0.06, 0, t, Z_FLOOR2 + 0.14, Z_EAVE - 0.24, TIMBER, 255, skip=("-o",))
    for s in (1.1, 2.4, 3.95, 5.5, 6.8):                                                           # gable boards
        top = Z_EAVE + min(s, W - s) * SLOPE - 0.12
        h.box(s - 0.06, s + 0.06, 0, t, Z_EAVE, top, TIMBER, 255, skip=("-o",))
    h.bar((3.95, 0.05, Z_EAVE + 0.1), (1.25, 0.05, Z_EAVE + 1.25), 0.1, TIMBER, 255)               # braces
    h.bar((3.95, 0.05, Z_EAVE + 0.1), (6.65, 0.05, Z_EAVE + 1.25), 0.1, TIMBER, 255)
    h.box(1.6, 6.3, 0, t, Z_EAVE + 1.1, Z_EAVE + 1.28, TIMBER, 255, skip=("-o",))
    window(h, 5.15, 6.75, 3.55, 5.1, 0, mull=1)                                                   # upper window (A)
    h.q([(4.95, 0.0, 5.45), (6.95, 0.0, 5.45), (6.95, 0.62, 5.12), (4.95, 0.62, 5.12)], TILE_A, 255, _unit((0, 0.33, 0.62)))
    h.q([(4.95, 0.62, 5.12), (6.95, 0.62, 5.12), (6.95, 0.62, 4.98), (4.95, 0.62, 4.98)], TIMBER, 255, (0, 1, 0))
    for s in (5.0, 6.9):
        h.bar((s, 0.05, 4.98), (s, 0.6, 5.0), 0.07, TIMBER, 255)
    window(h, 5.25, 6.85, 0.95, 2.35, 0, mull=1)                                                   # lower window (A)
    # bay box on the upper floor (B), over the porch roof
    h.box(1.2, 3.2, 0.0, 0.95, 3.3, 5.1, DARKBOX, 255, skip=("-o", "-z"))
    h.box(1.1, 3.3, -0.0, 1.05, 5.1, 5.24, TIMBER, 255, skip=("-o",))                              # flat roof of the bay
    h.box(1.1, 3.3, 0.0, 1.05, 3.12, 3.3, TIMBER, 255, skip=("-o",))                               # sill slab
    for k in range(3):
        sa = 1.32 + k * 0.58
        h.q([(sa, 0.96, 3.55), (sa + 0.46, 0.96, 3.55), (sa + 0.46, 0.96, 4.85), (sa, 0.96, 4.85)], GLASS_L, 255, (0, 1, 0))
        h.box(sa - 0.06, sa, 0.95, 1.0, 3.5, 4.9, TIMBER_L, 255, skip=("-o",))
    h.box(3.14, 3.2, 0.95, 1.0, 3.5, 4.9, TIMBER_L, 255, skip=("-o",))
    h.box(1.2, 3.2, 0.95, 1.0, 4.18, 4.24, TIMBER_L, 255, skip=("-o",))
    h.bar((4.05, 0.07, 0.3), (4.05, 0.07, Z_EAVE - 0.4), 0.1, PIPE, 255)                          # downspout
    # carport recess (the car is transient, not authored) under the porch roof
    h.q([(0.95, 0.03, 0.0), (4.25, 0.03, 0.0), (4.25, 0.03, 2.45), (0.95, 0.03, 2.45)], CARPORT, 255, (0, 1, 0))
    h.box(0.85, 4.35, 0.0, 0.08, 2.45, 2.6, TIMBER, 255, skip=("-o",))
    # porch roof over the carport (presentation), terracotta, sloping to the lane
    pw0, pw1, po, zh, zl = 0.5, 4.5, 1.25, 3.0, 2.5
    n = 5
    for i in range(n):
        t0, t1 = i / n, (i + 1) / n
        a_o, b_o = po * t0, po * t1
        za, zb = zh + (zl - zh) * t0, zh + (zl - zh) * t1
        h.q([(pw0, a_o, za + 0.08), (pw1, a_o, za + 0.08), (pw1, b_o, zb + 0.08), (pw0, b_o, zb + 0.08)],
            TILE_A if i % 2 == 0 else TILE_B, 255, _unit((0, 0.5 / po * 1.0, 1.0)))
    h.q([(pw0, po, zl - 0.1), (pw1, po, zl - 0.1), (pw1, po, zl + 0.08), (pw0, po, zl + 0.08)], TIMBER, 255, (0, 1, 0))
    h.q([(pw0, 0, zh - 0.1), (pw1, 0, zh - 0.1), (pw1, po, zl - 0.1), (pw0, po, zl - 0.1)], TIMBER_L, 255, (0, -0.4, -1))
    for s in (pw0, pw1):
        h.q([(s, 0, zh - 0.1), (s, po, zl - 0.1), (s, po, zl + 0.08), (s, 0, zh + 0.08)], TILE_B, 255, (-1 if s == pw0 else 1, 0, 0))
        h.bar((s, 0.04, zh - 0.9), (s, po - 0.1, zl - 0.12), 0.09, TIMBER, 255)
    # ---- NE side wall (s = W) and SW side wall (s = 0), rear wall ---------------------------------------------
    for sx, sgn in ((W, 1), (0, -1)):
        lo, hi = (sx, sx + 0.09 * sgn) if sgn > 0 else (sx + 0.09 * sgn, sx)
        h.box(lo, hi, -D, 0, Z_FLOOR2 - 0.14, Z_FLOOR2 + 0.14, TIMBER, 255, skip=("-s",) if sgn > 0 else ("+s",))
        h.box(lo, hi, -D, 0, Z_EAVE - 0.24, Z_EAVE, TIMBER, 255, skip=("-s",) if sgn > 0 else ("+s",))
        for o in (-0.1, -D + 0.1):
            lo2, hi2 = (lo, hi)
            h.box(lo2, hi2, o - 0.11, o + 0.11, Z_FOOT + 1.2, Z_EAVE, TIMBER, 255, skip=("-s",) if sgn > 0 else ("+s",))
        for o in (-2.4, -4.7, -7.0):
            h.box(lo, hi, o - 0.06, o + 0.06, Z_FLOOR2 + 0.14, Z_EAVE - 0.24, TIMBER, 255,
                  skip=("-s",) if sgn > 0 else ("+s",))
        side_window(h, sx, -3.9, -2.5, 3.5, 5.0, sgn)
        side_window(h, sx, -7.1, -5.4, 3.5, 5.0, sgn)
        side_window(h, sx, -3.9, -2.5, 0.95, 2.3, sgn)
        side_window(h, sx, -7.1, -5.4, 0.95, 2.3, sgn)
    h.box(0, W, -D - 0.09, -D, Z_FLOOR2 - 0.14, Z_FLOOR2 + 0.14, TIMBER, 255, skip=("+o",))
    h.box(0, W, -D - 0.09, -D, Z_EAVE - 0.24, Z_EAVE, TIMBER, 255, skip=("+o",))
    return h.m


# --------------------------------------------------------------------------------------------------------------
# trees (instance-local frame: centred on x/y, z up, metres * CS at the end)
# --------------------------------------------------------------------------------------------------------------
def blob(m, c, rx, ry, rz, rgb, rgb2, seg=8, rings=4, alpha=255, jitter=0.0):
    """Low-poly ellipsoid, flat shaded, alternating two colours by facet."""
    pts = [[(c[0], c[1], c[2] + rz)]]
    for r in range(1, rings):
        th = math.pi * r / rings
        pts.append([(c[0] + rx * math.sin(th) * math.cos(2 * math.pi * k / seg),
                     c[1] + ry * math.sin(th) * math.sin(2 * math.pi * k / seg), c[2] + rz * math.cos(th))
                    for k in range(seg)])
    pts.append([(c[0], c[1], c[2] - rz)])

    def out(p):
        return (p[0] - c[0], p[1] - c[1], p[2] - c[2])
    for k in range(seg):
        k2 = (k + 1) % seg
        col = rgb if k % 2 == 0 else rgb2
        a, b, d = pts[0][0], pts[1][k], pts[1][k2]
        m.poly([a, b, d], col, alpha, out(_cm([a, b, d])))
        for r in range(1, rings - 1):
            q = [pts[r][k], pts[r + 1][k], pts[r + 1][k2], pts[r][k2]]
            m.quad(q, col if (r + k) % 2 == 0 else rgb2, alpha, out(_cm(q)))
        a, b, d = pts[-1][0], pts[-2][k2], pts[-2][k]
        m.poly([a, b, d], col, alpha, out(_cm([a, b, d])))


def _cm(ps):
    return tuple(sum(p[i] for p in ps) / len(ps) for i in range(3))


def trunk(m, base, top, w, rgb, alpha=255):
    h = H()
    h.m = m
    h.bar(base, top, w, rgb, alpha)


def build_crape_myrtle():
    m = Mesh()
    BARK = (120, 88, 76)
    trunk(m, (0, 0, 0), (0.05, 0.0, 1.5), 0.26, BARK)
    trunk(m, (0.05, 0, 1.2), (-0.55, 0.1, 2.5), 0.15, BARK)
    trunk(m, (0.05, 0, 1.3), (0.6, -0.15, 2.6), 0.15, BARK)
    trunk(m, (0.05, 0, 1.4), (0.05, 0.4, 2.9), 0.15, BARK)
    P1, P2, P3 = (232, 120, 156), (246, 162, 190), (214, 92, 134)
    blob(m, (-0.55, 0.1, 2.9), 0.95, 0.9, 0.7, P1, P2)
    blob(m, (0.65, -0.15, 3.0), 1.0, 0.9, 0.7, P2, P1)
    blob(m, (0.05, 0.4, 3.35), 0.95, 0.9, 0.7, P3, P1)
    blob(m, (0.0, -0.45, 2.7), 0.8, 0.75, 0.55, P1, P3)
    blob(m, (0.0, 0.0, 3.85), 0.6, 0.6, 0.45, P2, P1)
    return m


def build_niwaki():
    m = Mesh()
    BARK = (86, 60, 48)
    G1, G2 = (44, 82, 50), (62, 104, 62)
    trunk(m, (0, 0, 0), (0.1, 0.0, 1.1), 0.3, BARK)
    trunk(m, (0.1, 0, 1.0), (-0.9, 0.2, 1.7), 0.17, BARK)
    trunk(m, (0.1, 0, 1.0), (0.95, -0.1, 1.9), 0.17, BARK)
    trunk(m, (0.1, 0, 1.0), (0.1, 0.1, 2.4), 0.2, BARK)
    blob(m, (-0.95, 0.2, 1.95), 1.05, 0.95, 0.38, G1, G2, seg=8, rings=4)
    blob(m, (0.95, -0.1, 2.15), 1.1, 1.0, 0.4, G2, G1, seg=8, rings=4)
    blob(m, (0.1, 0.1, 2.7), 0.95, 0.9, 0.36, G1, G2, seg=8, rings=4)
    blob(m, (0.0, -0.55, 1.55), 0.7, 0.6, 0.28, G2, G1, seg=8, rings=4)
    return m


def scale_tree(m):
    o = Mesh()
    for (x, y, z, nx, ny, nz, r, g, b, a) in m.v:
        o.v.append((x * CS, y * CS, z * CS, nx, ny, nz, r, g, b, a))
    o.f = [list(f) for f in m.f]
    return o


def write_mesh(name, mesh, comment, write):
    xs = [v[0] for v in mesh.v]; ys = [v[1] for v in mesh.v]; zs = [v[2] for v in mesh.v]
    print(f"{name}: {len(mesh.v)} vertices, {len(mesh.f)} faces; x {min(xs):.1f}..{max(xs):.1f} y {min(ys):.1f}..{max(ys):.1f} "
          f"z {min(zs):.1f}..{max(zs):.1f}")
    if not write:
        return
    path = BUILD / "meshes" / name
    text = akm_text(mesh, comment)
    if path.exists() and path.read_text(encoding="utf-8") != text:
        raise SystemExit(f"{path} exists with other content; use --rev N for a new mesh name")
    if not path.exists():
        path.write_text(text, encoding="utf-8")


def main():
    write = "--write" in sys.argv
    house = to_world(build_house())
    hname = f"home_17_16_house_{TAG}.akm"
    write_mesh(hname, house, "FL-4876 hand-authored home 17-16 Den-en-chofu Honcho (way/725138644): cream two-storey house, dark "
               "timber trim, terracotta gable roof, bay box, porch roof; world units (metres*5.132), origin = old mass origin; "
               "alpha 0 walls/roof collide, alpha 255 trim presentation", write)
    cname, nname = f"home_crape_myrtle_{TAG}.akm", f"home_niwaki_{TAG}.akm"
    write_mesh(cname, scale_tree(build_crape_myrtle()), "FL-4876 hand-authored pink crape myrtle, world units, origin at the trunk foot", write)
    write_mesh(nname, scale_tree(build_niwaki()), "FL-4876 hand-authored cloud-pruned pine, world units, origin at the trunk foot", write)
    pics = "artifacts/experiments/fl4876_manual_world/assess/live_fix_20261017/home/ (before/, after/, sheets/)"
    b1 = ("operator photos op_89ad6fb1448be364.png / op_a637d8482424ccc5.png: cream two-storey house, dark timber trim, steep "
          "terracotta gable roof, bay box, porch roof over the carport; pictures " + pics)
    edits = [
        {"inst": "building_mass_way_725138644", "op": "mesh", "mesh": hname, "basis": b1, "by": BY},
        {"inst": "linear_obs-authored-SF-4876-H001_point", "op": "mesh", "mesh": cname,
         "basis": "op_a637d8482424ccc5.png pink-flowering crape myrtle at the NE end of the frontage; was a dark-green box; " + pics,
         "by": BY},
        {"inst": "linear_obs-authored-SF-4876-H002_point", "op": "mesh", "mesh": nname,
         "basis": "op_89ad6fb1448be364.png cloud-pruned pine over the low wall; was a dark-green box; " + pics, "by": BY},
    ]
    if REV == 1:
        edits.append({"inst": "attach_building_mass_way_725138644", "op": "delete",
                      "basis": "the new house mesh includes the roof, bay box and trim; the old attach (generic balcony and "
                               "roof slab) would duplicate them; " + pics, "by": BY})
    else:
        edits = [e for e in edits if e["op"] == "mesh"]
        edits[0]["inst"] = "building_mass_way_725138644"
    if write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / ("home_edits.json" if REV == 1 else f"home_edits_v{REV}.json")
        out.write_text(json.dumps(edits, indent=1, ensure_ascii=False) + "\n")
        print("edits ->", out)


if __name__ == "__main__":
    main()
