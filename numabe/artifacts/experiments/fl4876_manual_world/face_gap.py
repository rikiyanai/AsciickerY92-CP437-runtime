"""face_gap.py BLDG FACE -- for each face of BLDG (or only FACE): length, outward bearing, and the distance
along the outward normal from the face midpoint (and quarter points) to the first other footprint. List-only."""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
canon = json.loads((ROOT / ".run/fl4876_builds/build_reg_73/canonical.world_ir.json").read_text())
fps = {}
for f in canon["canonical_features"]:
    if f.get("semantic_class") == "building_footprint":
        c = f["geometry"]["coordinates"]
        r = c[0] if isinstance(c[0][0], list) else c
        fps[f["feature_id"]] = [tuple(p[:2]) for p in r]
bid = sys.argv[1] if sys.argv[1].startswith("feature:") else "feature:building_footprint:way/" + sys.argv[1]
only = int(sys.argv[2]) if len(sys.argv) > 2 else None
ring = fps[bid]
if ring[0] == ring[-1]:
    ring = ring[:-1]
area = sum(ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1] for i in range(len(ring)))


def first_hit(o, d, maxd=40):
    best = None
    for k, r in fps.items():
        if k == bid:
            continue
        for i in range(len(r) - 1):
            (x3, y3), (x4, y4) = r[i], r[i + 1]
            den = d[0] * (y4 - y3) - d[1] * (x4 - x3)
            if abs(den) < 1e-12:
                continue
            t = ((x3 - o[0]) * (y4 - y3) - (y3 - o[1]) * (x4 - x3)) / den
            u = ((x3 - o[0]) * d[1] - (y3 - o[1]) * d[0]) / den
            if 0 <= u <= 1 and 0 < t < maxd and (best is None or t < best[0]):
                best = (t, k.split("/")[-1])
    return best


for i in range(len(ring)):
    if only is not None and i != only:
        continue
    a, b = ring[i], ring[(i + 1) % len(ring)]
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    n = (dy / L, -dx / L) if area > 0 else (-dy / L, dx / L)
    brg = math.degrees(math.atan2(n[0], n[1])) % 360
    hits = []
    for t in (0.25, 0.5, 0.75):
        o = (a[0] + dx * t + n[0] * 0.05, a[1] + dy * t + n[1] * 0.05)
        h = first_hit(o, n)
        hits.append(None if h is None else (round(h[0], 1), h[1]))
    print("face %d len %.1f out %.0f  gap@1/4,1/2,3/4: %s" % (i, L, brg, hits))
