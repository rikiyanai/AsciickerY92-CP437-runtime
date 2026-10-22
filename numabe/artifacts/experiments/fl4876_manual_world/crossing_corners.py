"""crossing_corners.py -- arithmetic aid for the hand placement of the Numabe level-crossing gear
(decides nothing): for the offsets chosen by hand below (metres from the primary-road centreline, + north;
metres from the outer track centreline, + away from the tracks), solve the point, then report its
distance to every track, road, footway and whether it falls inside a building footprint."""
import json, math
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
X0 = (379755.6, 3938508.0)
zones = json.loads((ROOT / ".run/fl4876_builds/build_reg_43/observation_registration/placement_zones.json").read_text())
Z = {z["carrier_id"]: z for z in zones}
loc = lambda q: (q[0] - X0[0], q[1] - X0[1])


def unit(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]; L = math.hypot(dx, dy); return (dx / L, dy / L)


# local straight pieces at the crossing (read off the carriers by eye from the printed polylines)
ROAD_E = ((1.8, 0.4), unit((1.8, 0.4), (7.6, 2.1)))      # primary 1115662463, east of the tracks
ROAD_W = ((-1.8, -0.4), unit((-6.3, -1.0), (-1.8, -0.4)))  # primary 1115662463, west of the tracks
TRK_NE = ((1.8, 0.4), unit((1.8, 0.4), (4.5, -2.7)))     # railway 24475081 (NE track)
TRK_SW = ((-1.8, -0.4), unit((-1.8, -0.4), (0.9, -3.7)))  # railway 1038001322 (SW track)


def left(line, p):  # signed distance, + to the left of the line direction
    (a, d) = line
    return d[0] * (p[1] - a[1]) - d[1] * (p[0] - a[0])


def solve(road, s_road, trk, s_trk):
    # find p with left(road,p)=s_road and left(trk,p)=s_trk (2x2 linear)
    (a1, d1), (a2, d2) = road, trk
    # left(l,p) = -d_y*p_x + d_x*p_y + (d_y*a_x - d_x*a_y)
    A = [[-d1[1], d1[0]], [-d2[1], d2[0]]]
    b = [s_road - (d1[1] * a1[0] - d1[0] * a1[1]), s_trk - (d2[1] * a2[0] - d2[0] * a2[1])]
    det = A[0][0] * A[1][1] - A[0][1] * A[1][0]
    return ((b[0] * A[1][1] - A[0][1] * b[1]) / det, (A[0][0] * b[1] - b[0] * A[1][0]) / det)


def seg(p, pl):
    best = 1e9
    for a, c in zip(pl, pl[1:]):
        dx, dy = c[0] - a[0], c[1] - a[1]; L2 = dx * dx + dy * dy or 1e-12
        t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
        best = min(best, math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy))
    return best


canon = json.loads((ROOT / ".run/fl4876_builds/build_reg_43/canonical.world_ir.json").read_text())
rings = [(f["feature_id"], [loc(q) for q in f["geometry"]["coordinates"]]) for f in canon["canonical_features"]
         if f.get("semantic_class") == "building_footprint"]


def inside(p, ring):
    c = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > p[1]) != (y2 > p[1]) and p[0] < x1 + (p[1] - y1) * (x2 - x1) / (y2 - y1):
            c = not c
    return c


# Track direction runs SE, so "left" of the track is NE.  Road direction runs ENE, so "left" is north.
PLAN = [  # fixture, road piece, metres north(+)/south(-) of road centreline, track, metres NE(+)/SW(-)
    ("SF-4876-0080", ROAD_E, +3.8, TRK_NE, +2.8),
    ("SF-4876-0157", ROAD_E, -5.0, TRK_NE, +2.0),
    ("SF-4876-0069", ROAD_E, -5.7, TRK_NE, +2.1),
    ("SF-4876-0067", ROAD_E, -6.1, TRK_NE, +3.6),
    ("SF-4876-0081", ROAD_E, -7.6, TRK_NE, +1.9),
    ("SF-4876-0369", ROAD_W, -5.6, TRK_SW, -2.8),
    ("SF-4876-0399", ROAD_W, +3.8, TRK_SW, -2.8),
]
for fid, road, sr, trk, st in PLAN:
    p = solve(road, sr, trk, st)
    near = sorted((seg(p, [loc(q) for q in z["polyline_epsg32654"]]), cid, z["carrier_class"]) for cid, z in Z.items()
                  if z["carrier_class"] != "building" and z.get("polyline_epsg32654"))[:5]
    inb = [f for f, r in rings if inside(p, r)]
    print("%s  E %.2f N %.2f  (local %.1f,%.1f)  in_bldg %s" % (fid, p[0] + X0[0], p[1] + X0[1], p[0], p[1], inb or "-"))
    print("     " + "; ".join("%s %s %.1f" % (c, k, d) for d, c, k in near))
