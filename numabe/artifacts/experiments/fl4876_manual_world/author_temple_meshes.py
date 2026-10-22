#!/usr/bin/env python3
"""author_temple_meshes.py [--write] -- hand-authored meshes for the 有慶山東光院 (Toukou-in) buildings (FL-4876).

Operator 2026-10-16: "the temple is also bullshit". The temple buildings were extruded boxes with LiDAR roof height
fields. This script is the hand authoring of three buildings as real ASCII-PLY .akm meshes, plus the owner records
(authored_building_appearance.json  buildings[<feature>]["authored_mesh"]) that make the building bake emit them in
place of the terrain box and roof field (bake_osm_building_footprints.py, AUTHORED-MESH-01).

Which footprint is which building (operator_ref_b satellite overlaid with the canonical footprints, build_reg_87):
  way/225101288  the teal main hall (本堂) AND, joined on its NE, the pale-grey hip-roofed support building. The
                 L-shaped outline traces the hall block, a low link block and the grey building (456 m2).
  way/225101048  the secondary hall: the blue-tiled hip roof around a flat grey centre (east of the main hall, 210 m2).
                 (It is NOT the 庫裏 residence the old record named; the satellite shows a blue hip with a flat centre.)
  way/225101506  the main gate (山門), 17.7 m2, in the SE precinct wall.
  way/225101505  a 20 m2 outbuilding south of the hall (not authored here).
Reference reads (assess/temple_ref/): operator_ref_a (isometric drawing), operator_ref_b (satellite), hall / band / gate
panorama crops.

FRAME.  Every mesh is in metres, x = east, y = north, z = up (z = 0 is the lowest ground under the footprint),
origin = the area centroid of the canonical footprint (EPSG:32654, anchor_en below).  The bake scales x, y, z by the
run's content_scale and places the instance at that centroid.  Inside this file a hand-chosen axis frame (u, v) is
used: u runs along bearing THETA (clockwise from north), v along THETA + 90 (clockwise).  P(u, v, z) -> (E, N, z).

COLOUR / COLLISION.  Colours are exact vertex RGB (the bake writes them unsnapped).  Alpha follows the engine contract
(engine/physics.cpp:663): alpha <= 128 collides, 255 is presentation only.  Plinth, walls, roof planes: 0 (they
collide, like every gameplay building mass).  Posts, rails, soffits, fascia, steps, ornaments: 255.

Every number below carries its basis: M = measured (canonical footprint, PLATEAU measuredHeight, TMG LiDAR roof field
roof_fields_fitted_s15_v2 read with roof_field_map.py), R = read off a reference image, D = hand design decision (the
reference does not give a number; typical Japanese temple proportions).
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
MESH_DIR = BUNDLE / "authored_meshes"
APP = BUNDLE / "authored_building_appearance.json"
BY = "main session (temple hand authoring from operator references, 2026-10-16)"
GENERATOR = "artifacts/experiments/fl4876_manual_world/author_temple_meshes.py"

# --------------------------------------------------------------------------------------------------------------
# DATA BLOCK
# --------------------------------------------------------------------------------------------------------------
# Colours.  Teal copper: sunlit/shade ends of the tone ramp sampled on the 240959581154487 hall crop (roof shade
# [57,100,101], the 2026-10-16 note TEAL [115,198,197]); R.  Plaster/timber: same crop samples [233,232,228] and
# [66,50,42]; R.  Blue tile: operator_ref_a/b secondary hall tiles; R (hand-matched to the picture).  Grey tile: the
# satellite grey hip building; R.
WING_WALL = (214, 215, 211)                       # R: operator_ref_a pale grey rendered wall (live fix 2 2026-10-16)
WING_FRAME = (238, 238, 234)
WING_GLASS = (112, 128, 140)                      # mid blue-grey glazing, not black
WING_BAND = (190, 191, 186)
# live fix 2 2026-10-16: roof tones re-sampled on operator_ref_a (teal shade [60,113,97] .. lit [129,162,151]; blue
# [60,76,111] .. [94,112,146]); the earlier ramps were flatter and more saturated than the drawing.
TEAL = ((66, 118, 104), (132, 172, 158))          # (shade, lit)
BLUE = ((40, 62, 112), (82, 108, 168))            # Classic's palette turns a duller ramp into grey/violet stripes
GREY_TILE = ((88, 92, 98), (150, 153, 158))
PLASTER = (233, 232, 228)
PLASTER_GREY = (188, 189, 183)                    # authored record wall_rgb of way/225101288 (support wing), R
TIMBER = (66, 50, 42)
TIMBER_LIGHT = (112, 84, 62)
DOOR = (92, 64, 46)
STONE = (146, 142, 134)
STONE_TOP = (160, 155, 146)
ROOF_CAP_TEAL = (50, 92, 86)
ROOF_CAP_BLUE = (44, 56, 92)
ROOF_CAP_GREY = (66, 69, 75)
ONI = (58, 62, 64)
FLAT_GREY = (132, 134, 138)
WINDOW_DARK = (44, 50, 54)

SUN = (math.cos(math.radians(50)) * math.sin(math.radians(160)),   # D: a high southern sun for the tone ramp
       math.cos(math.radians(50)) * math.cos(math.radians(160)), math.sin(math.radians(50)))

# --- main hall way/225101288: canonical footprint, frame THETA = 39.0 (the long edges run 37.5..39.4 / 125.8..132.8)
HALL = {
    "feature": "feature:building_footprint:way/225101288",
    "anchor_en": (379782.996, 3938600.300),   # M: area centroid of the canonical footprint (loc.py, build_reg_87)
    "footprint_area_m2": 455.96,              # M
    "theta": 39.0,                            # M: footprint_edges.py long-axis bearing 39.9, edges 37.5-39.4
    "height_m": 10.7,                         # M: PLATEAU measuredHeight (the LiDAR ridge reads 11 m, 'b' cells)
    # M: the 16 canonical footprint vertices in the (u, v) frame (loc.py)
    "outline_uv": [(-12.92, -8.55), (-10.40, -8.76), (-10.53, -11.33), (0.32, -11.62), (0.13, -8.80), (7.82, -8.75),
                   (8.37, 1.14), (16.83, 1.20), (16.64, 9.51), (2.71, 9.30), (2.74, 7.24), (-0.02, 7.12),
                   (-0.37, 8.93), (-6.43, 9.01), (-6.44, 7.23), (-12.68, 7.06)],
    # M, read from the outline: the hall block is u -12.8..2.7 x v -8.8..7.1 (15.5 x 15.9 m, near square); the
    # 2.8 m NW strip (v -11.6..-8.8, u -10.5..0.3) is a rear annex; the 6.0 x 1.8 m SE notch (u -6.4..-0.4,
    # v 7.1..9.0) is the front porch (向拝); u 2.7..8.2 is a low link block; u 8.2..16.7 x v 1.2..9.4 (8.5 x 8.2)
    # is the pale-grey hip building (satellite: square hip with a short ridge).
    "hall_rect": (-12.8, 2.7, -8.8, 7.1),
    "eave_z": 4.0,            # M: LiDAR cells on the outline rim read 4-5 m (roof_field_map); eave drip line 4.0 m
    "eave_curl": 0.42,        # R: operator_ref_a shows the eave corners curling up (反り); D: 0.42 m at the corners
    "wall_inset": 1.9,        # R: deep eaves (ref_a, hall crop); D: walls stand 1.9 m inside the drip line
    "inner_inset_u": 3.55,    # D: skirt (mokoshi) width on the u ends; leaves an 8.4 m ridge (LiDAR ridge ~8 m long)
    "inner_inset_v": 3.2,     # D: skirt width on the v sides
    "skirt_top_z": 6.0,       # D: skirt meets the upper roof at 6.0 m (LiDAR 5-7 m ring around the 11 m peak)
    "ridge_z": 10.45,         # D: ridge line; with the 0.25 m cap the top is PLATEAU 10.7 m
    "wall_z0": 0.9,           # D: floor 0.9 m above the stone base (raised timber veranda, ref_a)
    "wall_z1": 3.4,           # D: beam line 2.5 m above the floor
    "porch_u": (-6.4, -0.4),  # M: footprint notch
    "porch_v_front": 9.0,     # M
    "porch_ridge_z": 6.1,     # D: porch gable ridge; eave 3.7 below the 4.0 m hall drip line
    "porch_eave_z": 3.7,
    "annex": (-10.5, 0.3, -11.6, -8.8),   # M
    "link": (2.7, 8.2, -8.8, 9.3),         # M
    "link_eave_z": 3.9, "link_ridge_z": 6.0,   # D: LiDAR link block 4-5 m (roof_field_map rows 5-8)
    "grey": (8.2, 16.7, 1.2, 9.4),         # M
    "grey_eave_z": 4.8, "grey_ridge_z": 6.7, "grey_wall_top": 4.6,   # M: LiDAR 5-6 m over this block; D: 2 low storeys
}

# --- secondary hall way/225101048: canonical footprint is a 18.9 x 11.3 m rectangle, frame THETA = 124.4
HALL2 = {
    "feature": "feature:building_footprint:way/225101048",
    "anchor_en": (379805.425, 3938593.954),   # M: area centroid (loc.py, coordinates shifted to the first vertex first)
    "footprint_area_m2": 209.63,              # M
    "theta": 124.4,                           # M: footprint_edges.py long-axis bearing 124.4
    "height_m": 8.1,                          # M: PLATEAU measuredHeight; LiDAR is a flat 8 m across the block
    "rect": (-9.35, 9.45, -5.57, 5.55),       # M: the four canonical vertices (u -9.47/-9.28/9.48/9.45, v 5.70/-5.58/
    #                                              -5.56/5.41), loc.py, fitted by their means (the outline skews 0.2 m)
    "base_z": 0.5,                            # D: low stone base
    "wall_inset": 1.3,                        # D: eave overhang
    "eave_z": 5.5,                            # D: wall top 5.3 + eave; two storeys (PLATEAU storeys 2) at ~2.6 m
    "flat_z": 7.9,                            # M: LiDAR flat roof 8 m; the 0.2 m blue coping brings the top to 8.1
    "ring": 2.6,                              # R: satellite blue ring ~2.5 m wide around the grey flat centre
    "front_side": "v+",                       # R: the SW face (v +) looks onto the court (operator_ref_a, gate side)
    "record_update": {
        "what": "有慶山東光院 secondary hall: large blue-tiled hip roof around a flat grey centre on grey and white "
                "walls with dark timber, a veranda along the court (SW) side; east of the main hall. Identified from "
                "the operator satellite (operator_ref_b) overlaid with the canonical footprint (2026-10-16); the "
                "earlier 'beige 2-storey 庫裏 residence' reading was not borne out by the satellite roof.",
    },
}

# --- gate way/225101506: 4.13 x 4.41 m, road face bearing 123.6 (outward 33.6), PLATEAU 4.5 m, 1 storey
GATE = {
    "feature": "feature:building_footprint:way/225101506",
    "anchor_en": (379804.440, 3938578.892),   # M: area centroid (loc2.py)
    "footprint_area_m2": 17.73,               # M
    "theta": 123.6,                           # M: u runs along the precinct wall (road face bearing 123.6); the road is v-
    "height_m": 4.5,                          # M: PLATEAU measuredHeight (1 storey)
    "rect": (-2.04, 2.02, -2.20, 2.20),       # M: canonical vertices u -2.04..2.09, v -2.19..2.22 (loc2.py)
    "eave_z": 3.3,                            # D: tiled gable eave, 3.3 m
    "ridge_z": 4.25,                          # D: ridge line; the 0.25 m cap brings the top to PLATEAU 4.5 m
    "pier_u": 1.6,                            # D: granite piers at u = +-1.6 (R: gate crop, grey piers either side of the leaf)
    "leaf_h": 2.25,                           # D: grey metal leaf height (R: gate crop, leaf about 2.2 m)
    "record_update": {},
}

# --- outbuilding way/225101505: 20.5 m2 hexagon, 1 storey, PLATEAU 4.9 m; white plaster, blue-grey tile hip roof
OUTB = {
    "feature": "feature:building_footprint:way/225101505",
    "anchor_en": (379794.204, 3938580.325),   # M: area centroid (loc2.py)
    "footprint_area_m2": 20.48,               # M
    "theta": 70.1,                            # M: footprint_edges.py oriented-box bearing 70.1
    "height_m": 4.9,                          # M: PLATEAU measuredHeight
    "outline_uv": [(-1.51, -2.47), (1.13, -2.47), (2.86, -0.21), (1.48, 2.39), (-1.53, 2.43), (-2.69, 0.05)],   # M
    "wall_inset": 0.30,                       # D: eave overhang
    "wall_top": 2.9,                          # D: one storey
    "eave_z": 3.1,                            # D
    "apex_z": 4.65,                           # D: hip apex; the 0.25 m finial brings the top to PLATEAU 4.9 m
    "record_update": {},
}


# --------------------------------------------------------------------------------------------------------------
# mesh helpers
# --------------------------------------------------------------------------------------------------------------
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _newell(pts):
    nx = ny = nz = 0.0
    for i, p in enumerate(pts):
        q = pts[(i + 1) % len(pts)]
        nx += (p[1] - q[1]) * (p[2] + q[2])
        ny += (p[2] - q[2]) * (p[0] + q[0])
        nz += (p[0] - q[0]) * (p[1] + q[1])
    ln = math.sqrt(nx * nx + ny * ny + nz * nz)
    return (nx / ln, ny / ln, nz / ln) if ln > 1e-12 else None


def _lerp(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(len(a)))


def _rgb(c):
    return tuple(max(0, min(255, int(round(x)))) for x in c)


class Mesh:
    def __init__(self):
        self.v = []
        self.f = []

    def poly(self, pts, rgb, alpha, out):
        """pts: (e, n, z) metres; out: direction the visible side must face."""
        n = _newell(pts)
        if n is None:
            return
        if _dot(n, out) < 0:
            pts = pts[::-1]
            n = (-n[0], -n[1], -n[2])
        if len(pts) > 4:                       # Classic's loader takes triangles and quads only: fan a convex polygon
            for i in range(1, len(pts) - 1):
                self.poly([pts[0], pts[i], pts[i + 1]], rgb, alpha, out)
            return
        first = len(self.v)
        for p in pts:
            self.v.append((p[0], p[1], p[2], n[0], n[1], n[2], *_rgb(rgb), alpha))
        self.f.append(list(range(first, first + len(pts))))

    def quad(self, pts, rgb, alpha, out):
        pts = _dedup(pts)
        if len(pts) < 3:
            return
        if len(pts) == 4:
            n = _newell(pts[:3])
            if n is not None and abs(_dot(n, _sub(pts[3], pts[0]))) > 0.02:   # warped: two triangles
                self.poly([pts[0], pts[1], pts[2]], rgb, alpha, out)
                self.poly([pts[0], pts[2], pts[3]], rgb, alpha, out)
                return
        self.poly(pts, rgb, alpha, out)


def _dedup(pts):
    out = []
    for p in pts:
        if not out or math.dist(out[-1], p) > 1e-6:
            out.append(p)
    if len(out) > 1 and math.dist(out[0], out[-1]) <= 1e-6:
        out.pop()
    return out


class Frame:
    def __init__(self, mesh, theta):
        b = math.radians(theta)
        self.ue = (math.sin(b), math.cos(b))
        self.ve = (math.sin(b + math.pi / 2), math.cos(b + math.pi / 2))
        self.m = mesh

    def P(self, u, v, z):
        return (u * self.ue[0] + v * self.ve[0], u * self.ue[1] + v * self.ve[1], z)

    def q(self, uvz, rgb, alpha, out):
        self.m.quad([self.P(*p) for p in uvz], rgb, alpha, self.P(*out))

    def box(self, u0, u1, v0, v1, z0, z1, rgb, alpha, skip=()):
        faces = {
            "-u": ([(u0, v0, z0), (u0, v1, z0), (u0, v1, z1), (u0, v0, z1)], (-1, 0, 0)),
            "+u": ([(u1, v0, z0), (u1, v1, z0), (u1, v1, z1), (u1, v0, z1)], (1, 0, 0)),
            "-v": ([(u0, v0, z0), (u1, v0, z0), (u1, v0, z1), (u0, v0, z1)], (0, -1, 0)),
            "+v": ([(u0, v1, z0), (u1, v1, z0), (u1, v1, z1), (u0, v1, z1)], (0, 1, 0)),
            "-z": ([(u0, v0, z0), (u1, v0, z0), (u1, v1, z0), (u0, v1, z0)], (0, 0, -1)),
            "+z": ([(u0, v0, z1), (u1, v0, z1), (u1, v1, z1), (u0, v1, z1)], (0, 0, 1)),
        }
        for name, (pts, out) in faces.items():
            if name not in skip:
                self.q(pts, rgb, alpha, out)

    def post(self, u, v, z0, z1, w, rgb, alpha=255):
        self.box(u - w / 2, u + w / 2, v - w / 2, v + w / 2, z0, z1, rgb, alpha, skip=("-z",))

    def tri(self, a, b, c, rgb, alpha, out):
        self.m.poly([self.P(*a), self.P(*b), self.P(*c)], rgb, alpha, self.P(*out))


def roof_rgb(tones, normal):
    """Teal/blue/grey tile colour for a roof face: lerp shade -> lit by the face's lambert term (D)."""
    lam = max(0.0, _dot(normal, SUN))
    return _lerp(tones[0], tones[1], min(1.0, lam / 0.85))


def roof_quad(F, uvz, tones, up=(0, 0, 1), band=0, alpha=0):
    """One roof face; `band` darkens alternate seam strips (copper-sheet / tile courses, R: satellite ribs)."""
    pts = [F.P(*p) for p in uvz]
    n = _newell(_dedup(pts))
    if n is None:
        return
    if _dot(n, F.P(*up)) < 0:
        n = (-n[0], -n[1], -n[2])
    rgb = roof_rgb(tones, n)
    if band % 2 and tones is BLUE:      # live fix 2: on the teal ramp Classic's palette turned the rib step into green/grey stripes
        rgb = tuple(c * 0.95 for c in rgb)
    F.m.quad(pts, rgb, alpha, F.P(*up))


def strip_plane(F, a0, a1, b0, b1, n, tones, up=(0, 0, 1), alpha=0):
    """Plane with bottom edge a0->a1 and top edge b0->b1 cut into n seam strips."""
    for i in range(n):
        t0, t1 = i / n, (i + 1) / n
        p0, p1 = _lerp(a0, a1, t0), _lerp(a0, a1, t1)
        q0, q1 = _lerp(b0, b1, t0), _lerp(b0, b1, t1)
        roof_quad(F, [p0, p1, q1, q0], tones, up, band=i, alpha=alpha)


def eave_z(ze, curl, t):
    """Eave height along one edge: ze at the middle rising to ze + curl at the corners (反り), D."""
    return ze + curl * abs(2 * t - 1) ** 3


# --------------------------------------------------------------------------------------------------------------
# roof / wall building blocks
# --------------------------------------------------------------------------------------------------------------
def skirt_roof(F, rect, ze, curl, zin, iu, iv, tones, wall_rect, wall_top, n=14, fascia=0.26):
    """Hip skirt (mokoshi) from the drip line (rect, curled eave) up to an inner rectangle at zin, with fascia and
    a timber soffit down to the wall line.  Returns the inner rectangle."""
    u0, u1, v0, v1 = rect
    wu0, wu1, wv0, wv1 = wall_rect
    sides = [
        # (outer(t), inner(t), wall(t), outward dir)
        (lambda t: (u0 + t * (u1 - u0), v1), lambda t: (u0 + iu + t * (u1 - u0 - 2 * iu), v1 - iv),
         lambda t: (wu0 + t * (wu1 - wu0), wv1), (0, 1, 0)),
        (lambda t: (u1, v1 + 0 - t * (v1 - v0)), lambda t: (u1 - iu, v1 - iv - t * (v1 - v0 - 2 * iv)),
         lambda t: (wu1, wv1 - t * (wv1 - wv0)), (1, 0, 0)),
        (lambda t: (u1 - t * (u1 - u0), v0), lambda t: (u1 - iu - t * (u1 - u0 - 2 * iu), v0 + iv),
         lambda t: (wu1 - t * (wu1 - wu0), wv0), (0, -1, 0)),
        (lambda t: (u0, v0 + t * (v1 - v0)), lambda t: (u0 + iu, v0 + iv + t * (v1 - v0 - 2 * iv)),
         lambda t: (wu0, wv0 + t * (wv1 - wv0)), (-1, 0, 0)),
    ]
    for outer, inner, wall, od in sides:
        for i in range(n):
            t0, t1 = i / n, (i + 1) / n
            o0 = (*outer(t0), eave_z(ze, curl, t0))
            o1 = (*outer(t1), eave_z(ze, curl, t1))
            i0 = (*inner(t0), zin)
            i1 = (*inner(t1), zin)
            roof_quad(F, [o0, o1, i1, i0], tones, band=i)
            # fascia (tile-end board) and soffit
            F.q([o0, o1, (o1[0], o1[1], o1[2] - fascia), (o0[0], o0[1], o0[2] - fascia)], (46, 84, 78), 255, od)
            w0 = (*wall(t0), wall_top)
            w1 = (*wall(t1), wall_top)
            F.q([w0, w1, (o1[0], o1[1], o1[2] - fascia), (o0[0], o0[1], o0[2] - fascia)],
                TIMBER if i % 2 else (82, 62, 48), 255, (0, 0, -1))
    return (u0 + iu, u1 - iu, v0 + iv, v1 - iv)


def gable_roof(F, u_a, u_b, v_lo, v_hi, z_base, z_ridge, ov, tones, cap_rgb, n_strips=16):
    """Gable roof with ridge along u at v_c, planes from v_lo and v_hi at z_base; ov = gable overhang."""
    vc = (v_lo + v_hi) / 2
    ua, ub = u_a - ov, u_b + ov
    for vv, up in ((v_lo, (0, -1, 1)), (v_hi, (0, 1, 1))):
        strip_plane(F, (ua, vv, z_base), (ub, vv, z_base), (ua, vc, z_ridge), (ub, vc, z_ridge), n_strips, tones, up)
    # ridge cap
    F.box(ua - 0.1, ub + 0.1, vc - 0.28, vc + 0.28, z_ridge - 0.02, z_ridge + 0.25, cap_rgb, 255)
    # bargeboards along the four rake edges
    for u in (ua, ub):
        for vv, sgn in ((v_lo, 1), (v_hi, -1)):
            out = (1 if u == ub else -1, 0, 0)
            F.q([(u, vv, z_base), (u, vc, z_ridge), (u, vc, z_ridge - 0.32), (u, vv, z_base - 0.28)],
                (54, 48, 44), 255, out)
    # gable end triangles (white plaster, dark timber trim, small gegyo), recessed by ov
    for u, od in ((u_a, -1), (u_b, 1)):
        F.tri((u, v_lo, z_base), (u, v_hi, z_base), (u, vc, z_ridge), PLASTER, 0, (od, 0, 0))
        e = 0.03 * od
        # tie beam and king post
        F.box(u + (0 if od > 0 else -0.05), u + (0.05 if od > 0 else 0), v_lo, v_hi, z_base, z_base + 0.28, TIMBER, 255)
        F.box(u + (0 if od > 0 else -0.05), u + (0.05 if od > 0 else 0), vc - 0.11, vc + 0.11, z_base, z_ridge - 0.05,
              TIMBER, 255)
        # decorated gable (懸魚): dark roundel + bar
        zc = z_base + (z_ridge - z_base) * 0.52
        F.box(u + (0 if od > 0 else -0.07), u + (0.07 if od > 0 else 0), vc - 0.42, vc + 0.42, zc - 0.32, zc + 0.32,
              (78, 60, 48), 255)
        F.box(u + (0 if od > 0 else -0.09), u + (0.09 if od > 0 else 0), vc - 0.18, vc + 0.18, zc - 0.14, zc + 0.14,
              (176, 140, 70), 255)
        # diagonal trim boards following the rake
        for vv in (v_lo, v_hi):
            F.q([(u + e, vv, z_base + 0.28), (u + e, vc, z_ridge - 0.3), (u + e, vc, z_ridge - 0.5),
                 (u + e, vv * 0.97 + vc * 0.03, z_base + 0.28)], TIMBER, 255, (od, 0, 0))
    return vc


def ridge_ornaments(F, u_a, u_b, vc, z_ridge, top):
    """Onigawara blocks at the ridge ends (R: ref_a ridge ends), top = overall height."""
    for u in (u_a, u_b):
        F.box(u - 0.3, u + 0.3, vc - 0.3, vc + 0.3, z_ridge - 0.05, top, ONI, 255)


def hip_roof(F, rect, ze, curl, ridge_z, ridge_a, ridge_b, axis, tones, cap_rgb, n=10, fascia=0.2):
    """Hip roof over rect.  axis 'u': ridge along u from ridge_a to ridge_b at v_c; axis 'v' likewise at u_c."""
    u0, u1, v0, v1 = rect
    sw = (lambda a, b: (a, b)) if axis == "u" else (lambda a, b: (b, a))
    # work in (a, b) with ridge along a; rect becomes (a0, a1, b0, b1)
    a0, a1, b0, b1 = (u0, u1, v0, v1) if axis == "u" else (v0, v1, u0, u1)
    bc = (b0 + b1) / 2
    ra, rb = ridge_a, ridge_b
    outer_sides = [
        (lambda t: (a0 + t * (a1 - a0), b0), (ra, bc), (rb, bc), (0, -1)),      # b0 edge
        (lambda t: (a1, b0 + t * (b1 - b0)), (rb, bc), (rb, bc), (1, 0)),         # a1 end
        (lambda t: (a1 - t * (a1 - a0), b1), (rb, bc), (ra, bc), (0, 1)),         # b1 edge
        (lambda t: (a0, b1 - t * (b1 - b0)), (ra, bc), (ra, bc), (-1, 0)),        # a0 end
    ]
    for outer, p0, p1, od in outer_sides:
        for i in range(n):
            t0, t1 = i / n, (i + 1) / n
            ia0, ib0 = outer(t0)
            ia1, ib1 = outer(t1)
            z0, z1 = eave_z(ze, curl, t0), eave_z(ze, curl, t1)
            r0 = (p0[0] + (p1[0] - p0[0]) * t0, p0[1] + (p1[1] - p0[1]) * t0, ridge_z)
            r1 = (p0[0] + (p1[0] - p0[0]) * t1, p0[1] + (p1[1] - p0[1]) * t1, ridge_z)
            pts_ab = [(ia0, ib0, z0), (ia1, ib1, z1), r1, r0]
            pts = [(*sw(p[0], p[1]), p[2]) for p in pts_ab]
            roof_quad(F, pts, tones, band=i)
            o_out = (*sw(*od), 0)
            F.q([pts[0], pts[1], (pts[1][0], pts[1][1], pts[1][2] - fascia), (pts[0][0], pts[0][1], pts[0][2] - fascia)],
                (50, 54, 60) if tones is GREY_TILE else (30, 50, 90) if tones is BLUE else (46, 84, 78), 255, o_out)
    ra_uv = sw(ra, bc)
    rb_uv = sw(rb, bc)
    cu0, cu1 = min(ra_uv[0], rb_uv[0]), max(ra_uv[0], rb_uv[0])
    cv0, cv1 = min(ra_uv[1], rb_uv[1]), max(ra_uv[1], rb_uv[1])
    pad = 0.22
    F.box(cu0 - pad, cu1 + pad, cv0 - pad, cv1 + pad, ridge_z - 0.02, ridge_z + 0.22, cap_rgb, 255)


def wall_bays(F, along, fixed, a0, a1, z0, z1, nb, out, plaster, doors=(), windows=(), post_w=0.26, door_rgb=DOOR,
              beam_h=0.34, sill_h=0.16, trim_alpha=255, panel_alpha=0):
    """Plaster wall between dark timber posts.  along: 'u' or 'v' (the wall runs along that axis at the other
    coordinate = fixed); out = (+1 | -1) outward sense on the perpendicular axis.  doors / windows: bay indices."""
    def uvz(a, b, z):
        return (a, b, z) if along == "u" else (b, a, z)
    o = (0, out, 0) if along == "u" else (out, 0, 0)
    step = (a1 - a0) / nb
    inset = post_w / 2
    for i in range(nb):
        pa, pb = a0 + i * step + inset, a0 + (i + 1) * step - inset
        if i in doors:
            F.q([uvz(pa, fixed, z0), uvz(pb, fixed, z0), uvz(pb, fixed, z1 - beam_h), uvz(pa, fixed, z1 - beam_h)],
                door_rgb, panel_alpha, o)
            # plaster transom above is the beam band; lattice muntins (R: ref_a sliding doors)
            for k in range(1, 4):
                aa = pa + (pb - pa) * k / 4
                F.q([uvz(aa - 0.025, fixed + 0.02 * out, z0 + 0.1), uvz(aa + 0.025, fixed + 0.02 * out, z0 + 0.1),
                     uvz(aa + 0.025, fixed + 0.02 * out, z1 - beam_h - 0.05),
                     uvz(aa - 0.025, fixed + 0.02 * out, z1 - beam_h - 0.05)], (150, 118, 86), trim_alpha, o)
        elif i in windows:
            F.q([uvz(pa, fixed, z0), uvz(pb, fixed, z0), uvz(pb, fixed, z1 - beam_h), uvz(pa, fixed, z1 - beam_h)],
                plaster, panel_alpha, o)
            wa0, wa1 = pa + (pb - pa) * 0.2, pb - (pb - pa) * 0.2
            wz0, wz1 = z0 + (z1 - z0) * 0.38, z1 - beam_h - 0.25
            F.q([uvz(wa0, fixed + 0.02 * out, wz0), uvz(wa1, fixed + 0.02 * out, wz0),
                 uvz(wa1, fixed + 0.02 * out, wz1), uvz(wa0, fixed + 0.02 * out, wz1)], WINDOW_DARK, trim_alpha, o)
        else:
            F.q([uvz(pa, fixed, z0), uvz(pb, fixed, z0), uvz(pb, fixed, z1 - beam_h), uvz(pa, fixed, z1 - beam_h)],
                plaster, panel_alpha, o)
    for i in range(nb + 1):
        a = a0 + i * step
        pu, pv, _ = uvz(a, fixed, 0)
        F.post(pu, pv, z0, z1, post_w, TIMBER)
    # sill band and beam (長押 / 内法)
    ua0, vb0, _ = uvz(a0 - post_w / 2, fixed - 0.06, 0)
    ua1, vb1, _ = uvz(a1 + post_w / 2, fixed + 0.06, 0)
    F.box(min(ua0, ua1), max(ua0, ua1), min(vb0, vb1), max(vb0, vb1), z1 - beam_h, z1, TIMBER, trim_alpha, skip=("-z",))
    F.box(min(ua0, ua1), max(ua0, ua1), min(vb0, vb1), max(vb0, vb1), z0, z0 + sill_h, TIMBER, trim_alpha, skip=("-z",))


def wing_wall(F, along, fixed, a0, a1, z0, z1, out, pitch=1.25):
    """Pale-grey wall of the support wing (R: operator_ref_a 'pale grey rendered wall with windows'): light plaster,
    pale pilasters, two storeys of separate framed windows (not a continuous dark band)."""
    def uvz(a, b, z):
        return (a, b, z) if along == "u" else (b, a, z)
    o = (0, out, 0) if along == "u" else (out, 0, 0)
    d = 0.02 * out
    F.q([uvz(a0, fixed, z0), uvz(a1, fixed, z0), uvz(a1, fixed, z1), uvz(a0, fixed, z1)], WING_WALL, 0, o)
    n = max(2, int(round((a1 - a0) / pitch)))
    step = (a1 - a0) / n
    for i in range(n):
        ac = a0 + (i + 0.5) * step
        for wz0, wz1 in ((z0 + 0.75, z0 + 1.75), (2.95, 3.95)):
            hw = min(0.42, step * 0.34)
            F.q([uvz(ac - hw - 0.07, fixed + d, wz0 - 0.07), uvz(ac + hw + 0.07, fixed + d, wz0 - 0.07),
                 uvz(ac + hw + 0.07, fixed + d, wz1 + 0.07), uvz(ac - hw - 0.07, fixed + d, wz1 + 0.07)],
                WING_FRAME, 255, o)
            F.q([uvz(ac - hw, fixed + 2 * d, wz0), uvz(ac + hw, fixed + 2 * d, wz0),
                 uvz(ac + hw, fixed + 2 * d, wz1), uvz(ac - hw, fixed + 2 * d, wz1)], WING_GLASS, 255, o)
            F.q([uvz(ac - 0.015, fixed + 3 * d, wz0), uvz(ac + 0.015, fixed + 3 * d, wz0),
                 uvz(ac + 0.015, fixed + 3 * d, wz1), uvz(ac - 0.015, fixed + 3 * d, wz1)], WING_FRAME, 255, o)
    for k in range(n + 1):                                   # pale pilasters between window groups
        a = a0 + k * step
        pu, pv, _ = uvz(a, fixed, 0)
        F.post(pu, pv, z0, z1, 0.18, WING_FRAME)


# --------------------------------------------------------------------------------------------------------------
# the main hall mesh (way/225101288)
# --------------------------------------------------------------------------------------------------------------
def build_hall():
    H = HALL
    mesh = Mesh()
    F = Frame(mesh, H["theta"])
    u0, u1, v0, v1 = H["hall_rect"]
    ins = H["wall_inset"]
    wr = (u0 + ins, u1 - ins, v0 + ins, v1 - ins)                      # wall rectangle
    zf, zb = H["wall_z0"], H["wall_z1"]

    # --- stone base (基壇), collides; the lower edge goes to z = 0 (the bake grounds z = 0 on the lowest terrain)
    bu0, bu1, bv0, bv1 = wr[0] - 0.7, wr[1] + 0.7, wr[2] - 0.7, wr[3] + 1.7     # D: 0.7 m apron, 1.7 m veranda front
    F.box(bu0, bu1, bv0, bv1, 0.0, zf, STONE, 0, skip=("+z",))
    F.q([(bu0, bv0, zf), (bu1, bv0, zf), (bu1, bv1, zf), (bu0, bv1, zf)], STONE_TOP, 0, (0, 0, 1))
    # base course lines (ashlar joints) on the visible sides: two darker bands (R: ref_a stone base)
    for z in (0.3, 0.6):
        F.box(bu0 - 0.01, bu1 + 0.01, bv1 - 0.02, bv1 + 0.01, z, z + 0.04, (112, 108, 100), 255, skip=("-z", "+z"))
    # timber veranda deck (縁側) along the front, between wall and base edge
    F.box(wr[0] - 0.4, wr[1] + 0.4, wr[3], bv1, zf, zf + 0.07, TIMBER_LIGHT, 255, skip=("-z",))

    # --- walls (plaster between dark posts), front = v+ (SE, the court), rear = v-, ends = u-/u+
    nb_front = 5
    wall_bays(F, "u", wr[3], wr[0], wr[1], zf, zb, nb_front, +1, PLASTER, doors=(1, 2, 3))
    wall_bays(F, "u", wr[2], wr[0], wr[1], zf, zb, nb_front, -1, PLASTER, windows=(2,))
    wall_bays(F, "v", wr[0], wr[2], wr[3], zf, zb, 5, -1, PLASTER, windows=(2,))
    wall_bays(F, "v", wr[1], wr[2], wr[3], zf, zb, 5, +1, PLASTER, windows=())

    # --- veranda posts and low rail (R: ref_a timber posts on the veranda)
    vp = wr[3] + 1.25
    pu = [wr[0] + i * (wr[1] - wr[0]) / 5 for i in range(6)]
    for u in pu:
        F.post(u, vp, zf, zb + 0.15, 0.24, TIMBER)
    for a, b in zip(pu, pu[1:]):
        if (a + b) / 2 > H["porch_u"][0] and (a + b) / 2 < H["porch_u"][1]:
            continue
        F.box(a, b, vp - 0.05, vp + 0.05, zf + 0.35, zf + 0.45, TIMBER, 255)
        F.box(a, b, vp - 0.05, vp + 0.05, zf + 0.9, zf + 0.98, TIMBER, 255)

    # --- skirt (mokoshi) all round: curled drip line, fascia and soffit
    skirt_roof(F, H["hall_rect"], H["eave_z"], H["eave_curl"], H["skirt_top_z"], H["inner_inset_u"],
               H["inner_inset_v"], TEAL, wr, zb, n=14)
    iu0, iu1, iv0, iv1 = (u0 + H["inner_inset_u"], u1 - H["inner_inset_u"], v0 + H["inner_inset_v"], v1 - H["inner_inset_v"])
    # --- irimoya upper roof: gable along u over the inner rectangle, deep rake overhang
    vc = gable_roof(F, iu0, iu1, iv0, iv1, H["skirt_top_z"], H["ridge_z"], 0.5, TEAL, ROOF_CAP_TEAL)
    ridge_ornaments(F, iu0 - 0.5, iu1 + 0.5, vc, H["ridge_z"], H["height_m"])
    # skirt inner-edge bridge: close the gap between the skirt top edge and the gable plane at the u ends
    # (the gable overhang reaches 0.5 m past the inner rectangle)

    # --- front porch (向拝): gable facing the court, columns, beam, steps
    pu0, pu1 = H["porch_u"]
    pc = (pu0 + pu1) / 2
    hw = (pu1 - pu0) / 2
    pz, pe = H["porch_ridge_z"], H["porch_eave_z"]
    pv_front = H["porch_v_front"]
    pv_back = 3.8
    ov = 0.25
    for sgn in (-1, 1):
        a0 = (pc + sgn * hw, pv_back, pe)
        a1 = (pc + sgn * hw, pv_front + ov, pe)
        b0 = (pc, pv_back, pz)
        b1 = (pc, pv_front + ov, pz)
        strip_plane(F, a0, a1, b0, b1, 8, TEAL, up=(sgn, 0, 1))
    F.box(pc - 0.25, pc + 0.25, pv_back, pv_front + ov + 0.1, pz - 0.02, pz + 0.22, ROOF_CAP_TEAL, 255)
    # front gable triangle with trim, set back 0.25 m
    vt = pv_front
    F.tri((pc - hw, vt, pe), (pc + hw, vt, pe), (pc, vt, pz), PLASTER, 0, (0, 1, 0))
    F.box(pc - hw, pc + hw, vt, vt + 0.1, pe, pe + 0.3, TIMBER, 255)                         # tie beam
    F.box(pc - 0.1, pc + 0.1, vt, vt + 0.08, pe, pz - 0.05, TIMBER, 255)                     # king post
    F.box(pc - 0.45, pc + 0.45, vt, vt + 0.1, pe + 0.9, pe + 1.55, (78, 60, 48), 255)        # gegyo board
    F.box(pc - 0.2, pc + 0.2, vt, vt + 0.12, pe + 1.1, pe + 1.35, (176, 140, 70), 255)       # gilt emblem
    for sgn in (-1, 1):                                                                       # rake boards
        F.q([(pc + sgn * hw, vt + 0.05, pe), (pc, vt + 0.05, pz), (pc, vt + 0.05, pz - 0.3),
             (pc + sgn * hw * 0.94, vt + 0.05, pe)], (54, 48, 44), 255, (0, 1, 0))
    # columns and cross beam (R: ref_a dark porch posts)
    for sgn in (-1, 1):
        F.post(pc + sgn * (hw - 0.45), pv_front - 0.7, zf, pe, 0.3, TIMBER)
    F.box(pc - hw + 0.1, pc + hw - 0.1, pv_front - 0.85, pv_front - 0.55, pe - 0.35, pe, TIMBER, 255)
    # porch soffit and steps (stone, three treads) in front of the porch
    F.q([(pc - hw, pv_back, pe), (pc + hw, pv_back, pe), (pc + hw, pv_front, pe), (pc - hw, pv_front, pe)],
        (82, 62, 48), 255, (0, 0, -1))
    for j in (1, 2):                      # D: two stone treads, tops 0.6 m and 0.3 m, 0.33 m deep each
        F.box(pc - 1.4 - 0.1 * j, pc + 1.4 + 0.1 * j, bv1 + 0.33 * (j - 1), bv1 + 0.33 * j, 0.0, zf - 0.3 * j,
              STONE_TOP, 255, skip=("-z",))

    # --- rear annex (the 2.8 m NW strip): plaster wall and a teal lean-to roof
    au0, au1, av0, av1 = H["annex"]
    aw = av0 + 0.45
    F.box(au0 + 0.35, au1 - 0.35, av0 + 0.35, av1 + 0.4, 0.0, 3.1, PLASTER, 0, skip=("+z",))
    F.q([(au0 + 0.35, av0 + 0.35, 3.1), (au1 - 0.35, av0 + 0.35, 3.1), (au1 - 0.35, av1 + 0.4, 3.1),
         (au0 + 0.35, av1 + 0.4, 3.1)], PLASTER, 0, (0, 0, 1))
    strip_plane(F, (au0, av0, 3.35), (au1, av0, 3.35), (au0, av1 + 0.3, 4.1), (au1, av1 + 0.3, 4.1), 10, TEAL,
                up=(0, -1, 1))
    for k in range(5):
        F.post(au0 + 0.55 + k * (au1 - au0 - 1.1) / 4, av0 + 0.38, 0.0, 3.1, 0.22, TIMBER)
    F.box(au0 + 0.3, au1 - 0.3, av0 + 0.3, av0 + 0.42, 2.78, 3.1, TIMBER, 255, skip=("-z",))
    F.q([(au0, av0, 3.35), (au1, av0, 3.35), (au1, av0, 3.1), (au0, av0, 3.1)], (46, 84, 78), 255, (0, -1, 0))
    F.q([(au0, av0, 3.1), (au1, av0, 3.1), (au1, av1 + 0.3, 3.1), (au0, av1 + 0.3, 3.1)], (82, 62, 48), 255, (0, 0, -1))
    for u in (au0, au1):
        F.q([(u, av0, 3.35), (u, av1 + 0.3, 4.1), (u, av1 + 0.3, 3.1), (u, av0, 3.1)], PLASTER, 0, (1 if u == au1 else -1, 0, 0))

    # --- link block (low hip, dark teal) between hall and the grey building
    lu0, lu1, lv0, lv1 = H["link"]
    lw = (lu0 + 0.55, lu1 - 0.55, lv0 + 0.55, lv1 - 0.55)
    F.box(lu0, lu1, lv0, lv1, 0.0, 0.5, STONE, 0, skip=("+z",))
    F.q([(lu0, lv0, 0.5), (lu1, lv0, 0.5), (lu1, lv1, 0.5), (lu0, lv1, 0.5)], STONE_TOP, 0, (0, 0, 1))
    wall_bays(F, "v", lw[0], lw[2], lw[3], 0.5, 3.3, 7, -1, PLASTER, windows=(1, 4), beam_h=0.3)
    wall_bays(F, "v", lw[1], lw[2], lw[3], 0.5, 3.3, 7, +1, PLASTER, windows=(2, 5), beam_h=0.3)
    wall_bays(F, "u", lw[3], lw[0], lw[1], 0.5, 3.3, 2, +1, PLASTER, doors=(0,), beam_h=0.3)
    wall_bays(F, "u", lw[2], lw[0], lw[1], 0.5, 3.3, 2, -1, PLASTER, beam_h=0.3)
    hip_roof(F, (lu0, lu1, lv0, lv1), H["link_eave_z"], 0.0, H["link_ridge_z"], lv0 + 3.0, lv1 - 3.0, "v", TEAL,
             ROOF_CAP_TEAL)

    # --- the pale-grey hip building (support wing), two low storeys with dark window bands
    gu0, gu1, gv0, gv1 = H["grey"]
    gw = (gu0 + 0.6, gu1 - 0.6, gv0 + 0.6, gv1 - 0.6)
    gz1 = H["grey_wall_top"]
    F.box(gu0, gu1, gv0, gv1, 0.0, 0.4, STONE, 0, skip=("+z",))
    F.q([(gu0, gv0, 0.4), (gu1, gv0, 0.4), (gu1, gv1, 0.4), (gu0, gv1, 0.4)], STONE_TOP, 0, (0, 0, 1))
    for (along, fixed, a0, a1, out) in (("u", gw[3], gw[0], gw[1], +1), ("u", gw[2], gw[0], gw[1], -1),
                                         ("v", gw[0], gw[2], gw[3], -1), ("v", gw[1], gw[2], gw[3], +1)):
        wing_wall(F, along, fixed, a0, a1, 0.4, gz1, out)   # live fix 2 2026-10-16: pale wall with real window rows
    F.box(gw[0] - 0.12, gw[1] + 0.12, gw[2] - 0.12, gw[3] + 0.12, 2.35, 2.6, WING_BAND, 255, skip=("-z", "+z"))
    gc = (gu0 + gu1) / 2
    hip_roof(F, (gu0, gu1, gv0, gv1), H["grey_eave_z"], 0.15, H["grey_ridge_z"], gc - 1.0, gc + 1.0, "u", GREY_TILE,
             ROOF_CAP_GREY)
    # grey building soffit
    F.q([(gu0, gv0, H["grey_eave_z"] - 0.2), (gu1, gv0, H["grey_eave_z"] - 0.2), (gu1, gv1, H["grey_eave_z"] - 0.2),
         (gu0, gv1, H["grey_eave_z"] - 0.2)], (92, 80, 70), 255, (0, 0, -1))
    return mesh


# --------------------------------------------------------------------------------------------------------------
# the secondary hall (way/225101048): blue tile hip around a flat grey centre
# --------------------------------------------------------------------------------------------------------------
def build_hall2():
    H = HALL2
    mesh = Mesh()
    F = Frame(mesh, H["theta"])
    u0, u1, v0, v1 = H["rect"]
    ins = H["wall_inset"]
    wr = (u0 + ins, u1 - ins, v0 + ins, v1 - ins)
    zb0 = H["base_z"]
    ze = H["eave_z"]
    zwall = ze - 0.25
    ring = H["ring"]

    # stone base
    F.box(u0 + 0.2, u1 - 0.2, v0 + 0.2, v1 - 0.2, 0.0, zb0, STONE, 0, skip=("+z",))
    F.q([(u0 + 0.2, v0 + 0.2, zb0), (u1 - 0.2, v0 + 0.2, zb0), (u1 - 0.2, v1 - 0.2, zb0), (u0 + 0.2, v1 - 0.2, zb0)],
        STONE_TOP, 0, (0, 0, 1))
    # walls: white lower storey, grey upper storey, dark timber belt between (R: grey/white walls with dark timber)
    zmid = 2.9
    for (along, fixed, a0, a1, out, nb) in (("u", wr[3], wr[0], wr[1], +1, 7), ("u", wr[2], wr[0], wr[1], -1, 7),
                                           ("v", wr[0], wr[2], wr[3], -1, 4), ("v", wr[1], wr[2], wr[3], +1, 4)):
        door = (2, 3, 4) if (along == "u" and out == +1) else ()
        wall_bays(F, along, fixed, a0, a1, zb0, zmid, nb, out, PLASTER, doors=door, post_w=0.24, beam_h=0.3)
        wall_bays(F, along, fixed, a0, a1, zmid, zwall, nb, out, (176, 178, 178),
                  windows=(1, 3, 5) if along == "u" and out == +1 else (), post_w=0.24, beam_h=0.3)
    # front veranda (SW face): deck, posts, low rail
    vp = wr[3] + 0.9
    F.box(wr[0] + 0.3, wr[1] - 0.3, wr[3], wr[3] + 1.2, zb0, zb0 + 0.07, TIMBER_LIGHT, 255, skip=("-z",))
    xs = [wr[0] + 0.6 + i * (wr[1] - wr[0] - 1.2) / 6 for i in range(7)]
    for u in xs:
        F.post(u, vp, zb0, zmid + 0.3, 0.22, TIMBER)
    for a, b in zip(xs, xs[1:]):
        F.box(a, b, vp - 0.04, vp + 0.04, zb0 + 0.35, zb0 + 0.42, TIMBER, 255)
        F.box(a, b, vp - 0.04, vp + 0.04, zb0 + 0.85, zb0 + 0.92, TIMBER, 255)
    for j in (1, 2):                                        # D: two stone steps at the entrance, tops 0.35 / 0.2 m
        F.box(-1.9, 1.9, v1 - 0.2 + 0.2 * (j - 1), v1 - 0.2 + 0.2 * j, 0.0, 0.5 - 0.15 * j, STONE_TOP, 255,
              skip=("-z",))
    # blue tile hip skirt (no curl: a plainer hall roof), drip line = the footprint
    rect = (u0, u1, v0, v1)
    skirt_roof_b = []
    # skirt from the drip line to the flat roof boundary
    iu, iv = ring, ring
    sides = [
        (lambda t: (u0 + t * (u1 - u0), v1), lambda t: (u0 + iu + t * (u1 - u0 - 2 * iu), v1 - iv),
         lambda t: (wr[0] + t * (wr[1] - wr[0]), wr[3]), (0, 1, 0)),
        (lambda t: (u1, v1 - t * (v1 - v0)), lambda t: (u1 - iu, v1 - iv - t * (v1 - v0 - 2 * iv)),
         lambda t: (wr[1], wr[3] - t * (wr[3] - wr[2])), (1, 0, 0)),
        (lambda t: (u1 - t * (u1 - u0), v0), lambda t: (u1 - iu - t * (u1 - u0 - 2 * iu), v0 + iv),
         lambda t: (wr[1] - t * (wr[1] - wr[0]), wr[2]), (0, -1, 0)),
        (lambda t: (u0, v0 + t * (v1 - v0)), lambda t: (u0 + iu, v0 + iv + t * (v1 - v0 - 2 * iv)),
         lambda t: (wr[0], wr[2] + t * (wr[3] - wr[2])), (-1, 0, 0)),
    ]
    zin = H["flat_z"]
    n = 14
    for outer, inner, wall, od in sides:
        for i in range(n):
            t0, t1 = i / n, (i + 1) / n
            o0, o1 = (*outer(t0), ze + 0.12 * abs(2 * t0 - 1) ** 3), (*outer(t1), ze + 0.12 * abs(2 * t1 - 1) ** 3)
            i0, i1 = (*inner(t0), zin), (*inner(t1), zin)
            roof_quad(F, [o0, o1, i1, i0], BLUE, band=i)
            F.q([o0, o1, (o1[0], o1[1], o1[2] - 0.24), (o0[0], o0[1], o0[2] - 0.24)], (26, 44, 80), 255, od)
            w0, w1 = (*wall(t0), zwall), (*wall(t1), zwall)
            F.q([w0, w1, (o1[0], o1[1], o1[2] - 0.24), (o0[0], o0[1], o0[2] - 0.24)],
                TIMBER if i % 2 else (82, 62, 48), 255, (0, 0, -1))
    # flat grey centre, blue coping around it (the top of the building is PLATEAU 8.1 m)
    fu0, fu1, fv0, fv1 = u0 + iu, u1 - iu, v0 + iv, v1 - iv
    F.q([(fu0, fv0, zin), (fu1, fv0, zin), (fu1, fv1, zin), (fu0, fv1, zin)], FLAT_GREY, 0, (0, 0, 1))
    top = H["height_m"]
    for (a0, a1, b0, b1) in ((fu0 - 0.1, fu1 + 0.1, fv0 - 0.1, fv0 + 0.12), (fu0 - 0.1, fu1 + 0.1, fv1 - 0.12, fv1 + 0.1),
                              (fu0 - 0.1, fu0 + 0.12, fv0 - 0.1, fv1 + 0.1), (fu1 - 0.12, fu1 + 0.1, fv0 - 0.1, fv1 + 0.1)):
        F.box(a0, a1, b0, b1, zin - 0.02, top, ROOF_CAP_BLUE, 255)
    # rooftop hatch and a drain pipe on the flat (satellite: small white box at the flat's corner)
    F.box(fu0 + 1.0, fu0 + 2.2, fv0 + 0.8, fv0 + 1.8, zin, top, (206, 206, 200), 255)
    return mesh


# --------------------------------------------------------------------------------------------------------------
# the main gate (way/225101506): granite piers, two grey metal leaves, tiled gable on timber posts
# --------------------------------------------------------------------------------------------------------------
GATE_TILE = ((52, 58, 60), (100, 108, 110))       # R: authored record "heavy dark-grey tiled gable roof (~[64,73,74] lit)"
GATE_LEAF = (138, 144, 148)                        # R: gate crop, grey metal leaf
GATE_LEAF_DARK = (104, 110, 114)
GRANITE = (128, 124, 116)                          # R: authored record "granite gate posts"


def build_gate():
    H = GATE
    mesh = Mesh()
    F = Frame(mesh, H["theta"])
    u0, u1, v0, v1 = H["rect"]
    ze, zr = H["eave_z"], H["ridge_z"]
    # paved base (stone-paved approach, R: authored record)
    F.box(u0, u1, v0, v1, 0.0, 0.12, (150, 146, 138), 0, skip=("+z",))
    F.q([(u0, v0, 0.12), (u1, v0, 0.12), (u1, v1, 0.12), (u0, v1, 0.12)], (166, 162, 154), 0, (0, 0, 1))
    # granite piers with caps either side of the leaves
    pu = H["pier_u"]
    for s in (-1, 1):
        F.box(s * pu - 0.27, s * pu + 0.27, -0.27, 0.27, 0.12, 2.62, GRANITE, 0, skip=("-z",))
        F.box(s * pu - 0.33, s * pu + 0.33, -0.33, 0.33, 2.62, 2.74, (150, 146, 138), 255, skip=("-z",))
    # two grey metal leaves with a centre gap, vertical bars and a cross rail (R: gate crop)
    lh = H["leaf_h"]
    for a, b in ((-pu + 0.27, -0.03), (0.03, pu - 0.27)):
        F.box(a, b, -0.04, 0.04, 0.2, 0.2 + lh, GATE_LEAF, 0)
        for k in range(1, 5):
            x = a + (b - a) * k / 5
            F.box(x - 0.03, x + 0.03, -0.055, 0.055, 0.25, 0.15 + lh, GATE_LEAF_DARK, 255)
        F.box(a, b, -0.06, 0.06, 1.1, 1.2, GATE_LEAF_DARK, 255)
    # timber posts carrying a tiled gable; a lintel with the 有慶山 plaque board (R: authored record)
    for su in (-1, 1):
        for sv in (-1, 1):
            F.post(su * 1.78, sv * 1.9, 0.12, ze, 0.26, TIMBER)
    F.box(u0 + 0.1, u1 - 0.1, -1.9 - 0.13, -1.9 + 0.13, ze - 0.3, ze, TIMBER, 255)
    F.box(u0 + 0.1, u1 - 0.1, 1.9 - 0.13, 1.9 + 0.13, ze - 0.3, ze, TIMBER, 255)
    F.box(-0.55, 0.55, -0.08, -0.02, 2.82, 3.14, (70, 56, 44), 255)
    for k in range(3):
        F.box(-0.34 + 0.34 * k, -0.2 + 0.34 * k, -0.09, -0.07, 2.9, 3.06, (196, 164, 88), 255)
    # gable roof, ridge along u, rake overhang 0.3 m, eaves at the footprint edge
    gable_roof(F, u0 + 0.35, u1 - 0.35, v0 + 0.05, v1 - 0.05, ze, zr, 0.3, GATE_TILE, (44, 50, 52), n_strips=10)
    return mesh


# --------------------------------------------------------------------------------------------------------------
# the outbuilding (way/225101505): small white-plastered hexagon under a blue-grey pyramidal hip roof
# --------------------------------------------------------------------------------------------------------------
OUTB_TILE = ((86, 100, 108), (150, 166, 170))     # R: authored record roof_rgb [162,178,171] lit, 'blue-grey tiled roof'
OUTB_PLASTER = (246, 235, 201)                     # R: authored record wall_rgb


def build_outbuilding():
    H = OUTB
    mesh = Mesh()
    F = Frame(mesh, H["theta"])
    outline = H["outline_uv"]
    cu = sum(p[0] for p in outline) / len(outline)
    cv = sum(p[1] for p in outline) / len(outline)
    f = 1 - H["wall_inset"] / 2.5                   # wall ring = outline pulled toward the centre
    ring = [(cu + (u - cu) * f, cv + (v - cv) * f) for u, v in outline]
    n = len(outline)
    zt, ze, za = H["wall_top"], H["eave_z"], H["apex_z"]
    # stone plinth
    F.m.poly([F.P(u, v, 0.0) for u, v in outline[::-1]], STONE, 0, (0, 0, -1))
    for i in range(n):
        a, b = outline[i], outline[(i + 1) % n]
        F.q([(a[0], a[1], 0.0), (b[0], b[1], 0.0), (b[0], b[1], 0.35), (a[0], a[1], 0.35)], STONE, 0,
            ((a[0] + b[0]) / 2 - cu, (a[1] + b[1]) / 2 - cv, 0))
    # plaster walls on the ring, dark timber posts at the corners, one small window and a door
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        out = ((a[0] + b[0]) / 2 - cu, (a[1] + b[1]) / 2 - cv, 0)
        F.q([(a[0], a[1], 0.35), (b[0], b[1], 0.35), (b[0], b[1], zt), (a[0], a[1], zt)], OUTB_PLASTER, 0, out)
        F.post(a[0], a[1], 0.35, zt, 0.16, TIMBER)
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        ln = math.hypot(b[0] - a[0], b[1] - a[1])
        du, dv = (b[0] - a[0]) / ln, (b[1] - a[1]) / ln
        nu, nv = out[0] / math.hypot(out[0], out[1]), out[1] / math.hypot(out[0], out[1])
        if i == 1:      # door facing the court
            F.q([(mx - du * 0.45 + nu * 0.02, my - dv * 0.45 + nv * 0.02, 0.35),
                 (mx + du * 0.45 + nu * 0.02, my + dv * 0.45 + nv * 0.02, 0.35),
                 (mx + du * 0.45 + nu * 0.02, my + dv * 0.45 + nv * 0.02, 2.3),
                 (mx - du * 0.45 + nu * 0.02, my - dv * 0.45 + nv * 0.02, 2.3)], DOOR, 255, out)
        elif i == 3:    # the small window (R: authored record 'small window')
            F.q([(mx - du * 0.35 + nu * 0.02, my - dv * 0.35 + nv * 0.02, 1.3),
                 (mx + du * 0.35 + nu * 0.02, my + dv * 0.35 + nv * 0.02, 1.3),
                 (mx + du * 0.35 + nu * 0.02, my + dv * 0.35 + nv * 0.02, 2.0),
                 (mx - du * 0.35 + nu * 0.02, my - dv * 0.35 + nv * 0.02, 2.0)], WINDOW_DARK, 255, out)
    # top plate, soffit and the pyramidal hip roof from the footprint (drip line) to the apex over the centre
    F.m.poly([F.P(u, v, ze - 0.2) for u, v in outline[::-1]], (82, 62, 48), 255, (0, 0, -1))
    for i in range(n):
        a, b = outline[i], outline[(i + 1) % n]
        pts = [(a[0], a[1], ze), (b[0], b[1], ze), (cu, cv, za)]
        roof_quad(F, pts, OUTB_TILE, band=i)
        F.q([(a[0], a[1], ze), (b[0], b[1], ze), (b[0], b[1], ze - 0.2), (a[0], a[1], ze - 0.2)], (60, 74, 82), 255,
            ((a[0] + b[0]) / 2 - cu, (a[1] + b[1]) / 2 - cv, 0))
    F.box(cu - 0.12, cu + 0.12, cv - 0.12, cv + 0.12, za - 0.05, H["height_m"], (58, 62, 66), 255)   # finial
    return mesh


# --------------------------------------------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------------------------------------------
def akm_text(mesh, comment):
    lines = ["ply", "format ascii 1.0", "comment " + comment, f"element vertex {len(mesh.v)}",
             "property float x", "property float y", "property float z", "property float nx", "property float ny",
             "property float nz", "property uchar red", "property uchar green", "property uchar blue",
             "property uchar alpha", f"element face {len(mesh.f)}", "property list uchar uint vertex_indices",
             "end_header"]
    for (x, y, z, nx, ny, nz, r, g, b, a) in mesh.v:
        lines.append(f"{x:.3f} {y:.3f} {z:.3f} {nx:.3f} {ny:.3f} {nz:.3f} {r} {g} {b} {a}")
    for f in mesh.f:
        lines.append(f"{len(f)} " + " ".join(str(i) for i in f))
    return "\n".join(lines) + "\n"


def extent(mesh):
    xs = [v[0] for v in mesh.v]
    ys = [v[1] for v in mesh.v]
    zs = [v[2] for v in mesh.v]
    return (min(xs), max(xs)), (min(ys), max(ys)), (min(zs), max(zs))


JOBS = [
    ("toukouin_main_hall_way_225101288.akm", build_hall, HALL,
     "有慶山東光院 main hall (本堂): teal irimoya with curled deep eaves, front porch gable, plaster and timber walls, "
     "raised veranda, low link block and pale-grey hip support building"),
    ("toukouin_secondary_hall_way_225101048.akm", build_hall2, HALL2,
     "有慶山東光院 secondary hall: blue-tiled hip roof around a flat grey centre on grey/white walls with dark timber"),
    ("toukouin_gate_way_225101506.akm", build_gate, GATE,
     "有慶山東光院 main gate (山門): dark-grey tiled gable on timber posts, granite piers, grey metal leaves"),
    ("toukouin_outbuilding_way_225101505.akm", build_outbuilding, OUTB,
     "有慶山東光院 outbuilding: small white-plastered hexagon under a blue-grey pyramidal hip roof"),
]


def main():
    write = "--write" in sys.argv
    app = json.loads(APP.read_text()) if write else None
    preview = Path(sys.argv[sys.argv.index("--preview-dir") + 1]) if "--preview-dir" in sys.argv else None
    for name, fn, D, what in JOBS:
        mesh = fn()
        (x0, x1), (y0, y1), (z0, z1) = extent(mesh)
        print(f"{name}: {len(mesh.v)} vertices, {len(mesh.f)} faces, x {x0:.1f}..{x1:.1f} y {y0:.1f}..{y1:.1f} "
              f"z {z0:.2f}..{z1:.2f}")
        if abs(z0) > 1e-6 or abs(z1 - D["height_m"]) > 0.011:
            raise SystemExit(f"{name}: z range {z0}..{z1} must be 0..{D['height_m']} (PLATEAU measuredHeight)")
        text = akm_text(mesh, f"FL-4876 hand-authored {name}; metres, x=E y=N z=up, origin = footprint area centroid; "
                              f"alpha<=128 collides")
        if preview is not None:                       # a look-only copy; no owner record, nothing under the bundle
            preview.mkdir(parents=True, exist_ok=True)
            (preview / name).write_text(text, encoding="utf-8")
        if write:
            MESH_DIR.mkdir(parents=True, exist_ok=True)
            (MESH_DIR / name).write_text(text, encoding="utf-8")
            rec = app["buildings"].setdefault(D["feature"], {})
            rec["authored_mesh"] = {
                "file": "authored_meshes/" + name, "units": "metres", "frame": "x=E y=N z=up, z=0 lowest ground",
                "anchor": "footprint_area_centroid", "anchor_en_epsg32654": list(D["anchor_en"]),
                "footprint_area_m2": D["footprint_area_m2"], "height_m": D["height_m"],
                "height_basis": "PLATEAU bldg:measuredHeight; the mesh's top (ridge cap / roof coping) is set to it",
                "generator": GENERATOR, "what": what, "decided_by": BY, "decided_on": "2026-10-16"}
            rec["decided_on"] = "2026-10-16"
            for key, value in (D.get("record_update") or {}).items():
                if key in rec and "previous_" + key not in rec:
                    rec["previous_" + key] = rec[key]
                rec[key] = value
            print("  owner record", D["feature"])
    if write:
        APP.write_text(json.dumps(app, ensure_ascii=False, indent=1) + "\n")
        print("written")


if __name__ == "__main__":
    main()
