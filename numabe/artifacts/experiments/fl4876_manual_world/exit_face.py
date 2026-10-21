"""exit_face.py WAY E N [OUT_M] -- list/measure only: for a point inside (or near) a building footprint, every face
with its outward bearing, the point's along/inside distance, the clearance in front of the face (first other
footprint along its normal) and the point moved OUT_M (default 0.8) outside the face at the same along position.
The nearest face with open space in front is marked '<-'. Uses build_reg_75 canonical footprints."""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
way, E, N = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
OUT = float(sys.argv[4]) if len(sys.argv) > 4 else 0.8
fps = {}
for f in json.loads((ROOT / ".run/fl4876_builds/build_reg_75/canonical.world_ir.json").read_text())["canonical_features"]:
    if f.get("semantic_class") == "building_footprint":
        c = f["geometry"]["coordinates"]
        r = [tuple(p[:2]) for p in (c[0] if isinstance(c[0][0], list) else c)]
        fps[f["feature_id"]] = r[:-1] if r[0] == r[-1] else r
bid = "feature:building_footprint:way/" + way
ring = fps[bid]
area = sum(ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1] for i in range(len(ring)))


def first_hit(o, d, maxd=30):
    best = None
    for k, r in fps.items():
        if k == bid:
            continue
        for i in range(len(r)):
            (x3, y3), (x4, y4) = r[i], r[(i + 1) % len(r)]
            den = d[0] * (y4 - y3) - d[1] * (x4 - x3)
            if abs(den) < 1e-12:
                continue
            t = ((x3 - o[0]) * (y4 - y3) - (y3 - o[1]) * (x4 - x3)) / den
            u = ((x3 - o[0]) * d[1] - (y3 - o[1]) * d[0]) / den
            if 0 <= u <= 1 and 0 < t < maxd and (best is None or t < best):
                best = t
    return best


rows = []
for i in range(len(ring)):
    a, b = ring[i], ring[(i + 1) % len(ring)]
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    n = (dy / L, -dx / L) if area > 0 else (-dy / L, dx / L)
    s = max(0.3, min(L - 0.3, (E - a[0]) * dx / L + (N - a[1]) * dy / L))
    q = (a[0] + dx * s / L, a[1] + dy * s / L)
    inside = (E - q[0]) * -n[0] + (N - q[1]) * -n[1]
    gap = first_hit((q[0] + n[0] * 0.05, q[1] + n[1] * 0.05), n)
    out = (q[0] + n[0] * OUT, q[1] + n[1] * OUT)
    rows.append((abs(inside), i, L, math.degrees(math.atan2(n[0], n[1])) % 360, s, inside, gap, out))
open_rows = [r for r in rows if r[6] is None or r[6] > OUT + 0.5]
pick = min(open_rows)[1] if open_rows else None
for d, i, L, brg, s, inside, gap, out in rows:
    print("face %d len %.1f out %3.0f along %.1f depth %.2f gap %s -> (%.2f, %.2f)%s" % (
        i, L, brg, s, inside, "open" if gap is None else "%.1f" % gap, out[0], out[1], "  <-" if i == pick else ""))
