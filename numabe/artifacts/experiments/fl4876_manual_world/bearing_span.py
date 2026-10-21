#!/usr/bin/env python3
"""bearing_span.py BUILD_DIR CAM_E CAM_N BEARING [WAY...] -- FL-4876 listing aid (measures, decides nothing).

From a camera point, lists which building footprints a ray at BEARING (deg, from north) hits first and the bearing
span / nearest distance of each named building, so a person can say which building a panorama column shows.
"""
import json
import math
import sys
from pathlib import Path

B = Path(sys.argv[1])
ce, cn, brg = float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
names = ["feature:building_footprint:" + w for w in sys.argv[5:]]
rings = {}
for f in json.loads((B / "canonical.world_ir.json").read_text())["canonical_features"]:
    if "building_footprint" in f.get("feature_id", ""):
        pts = f["geometry"]["coordinates"]
        while isinstance(pts[0][0], list):
            pts = pts[0]
        rings[f["feature_id"]] = [tuple(p[:2]) for p in pts]
dx, dy = math.sin(math.radians(brg)), math.cos(math.radians(brg))
hits = []
for fid, ring in rings.items():
    for p, q in zip(ring, ring[1:] + ring[:1]):
        ex, ey = q[0] - p[0], q[1] - p[1]
        den = dx * ey - dy * ex
        if abs(den) < 1e-12:
            continue
        t = ((p[0] - ce) * ey - (p[1] - cn) * ex) / den
        u = ((p[0] - ce) * dy - (p[1] - cn) * dx) / den
        if t > 0 and 0 <= u <= 1:
            hits.append((round(t, 1), fid.split(":", 2)[-1]))
seen = set()
print("ray %.1f deg from (%.1f, %.1f) hits, nearest first:" % (brg, ce, cn))
for t, fid in sorted(hits):
    if fid not in seen:
        seen.add(fid)
        print("  %6.1f m  %s" % (t, fid))
    if len(seen) >= 5:
        break
for fid in names:
    ring = rings[fid]
    bs = sorted(math.degrees(math.atan2(p[0] - ce, p[1] - cn)) % 360 for p in ring)
    print("%s spans bearings %.1f..%.1f, nearest vertex %.1f m" % (
        fid.split(":", 2)[-1], bs[0], bs[-1], min(math.hypot(p[0] - ce, p[1] - cn) for p in ring)))
