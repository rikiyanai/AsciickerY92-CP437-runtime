#!/usr/bin/env python3
"""footprint_edges.py BUILD_DIR WAY_OR_ID [...] -- FL-4876 listing aid (measures, decides nothing).

Per building: canonical footprint vertices (EPSG:32654), each edge's length and outward-normal bearing, the long-axis
bearing, the oriented bounding box (length x width) and the built height record, so a person can author a roof form
(ridge bearing, eave and ridge heights) by hand.
"""
import json
import math
import sys
from pathlib import Path

B = Path(sys.argv[1])
feats = {f["feature_id"]: f for f in json.loads((B / "canonical.world_ir.json").read_text())["canonical_features"]}
bi = {b["feature_id"]: b for b in json.loads((B / "building_instances.json").read_text())}
for key in sys.argv[2:]:
    fid = key if key.startswith("feature:") else "feature:building_footprint:" + key
    pts = feats[fid]["geometry"]["coordinates"]
    while isinstance(pts[0][0], list):
        pts = pts[0]
    ring = [tuple(p[:2]) for p in pts]
    if ring[0] == ring[-1]:
        ring = ring[:-1]
    n = len(ring)
    area = sum(ring[k][0] * ring[(k + 1) % n][1] - ring[(k + 1) % n][0] * ring[k][1] for k in range(n)) / 2
    cx = sum(p[0] for p in ring) / n
    cy = sum(p[1] for p in ring) / n
    print(fid, "area %.1f m2, centroid %.2f %.2f, height %s m (%s)" % (
        abs(area), cx, cy, bi.get(fid, {}).get("roof_height_m"), bi.get(fid, {}).get("height_source")))
    best = None
    for k in range(n):
        p, q = ring[k], ring[(k + 1) % n]
        dx, dy = q[0] - p[0], q[1] - p[1]
        L = math.hypot(dx, dy)
        nx, ny = (dy, -dx) if area > 0 else (-dy, dx)
        print("  face %d: %.2f m, edge bearing %.1f, outward %.1f" % (
            k, L, math.degrees(math.atan2(dx, dy)) % 360, math.degrees(math.atan2(nx, ny)) % 360))
        # oriented box along this edge
        ux, uy = dx / (L or 1), dy / (L or 1)
        a = [(r[0] - cx) * ux + (r[1] - cy) * uy for r in ring]
        b = [-(r[0] - cx) * uy + (r[1] - cy) * ux for r in ring]
        box = (max(a) - min(a), max(b) - min(b))
        if L > 1 and (best is None or box[0] * box[1] < best[0]):
            best = (box[0] * box[1], math.degrees(math.atan2(dx, dy)) % 180, box)
    print("  oriented box: %.2f x %.2f m along bearing %.1f/%.1f" % (best[2][0], best[2][1], best[1], best[1] + 180))
