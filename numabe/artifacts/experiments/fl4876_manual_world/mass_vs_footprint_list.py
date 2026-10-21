#!/usr/bin/env python3
"""mass_vs_footprint_list.py BUILD_DIR [OUT_JSON] -- FL-4876 listing aid (measures, decides nothing).

Facade panels, signs and attachments are placed on the CANONICAL footprint walls (canonical.world_ir.json). The
extruded building the player sees is built from building_instances.json "footprint" (A3D units). Where the two rings
disagree, wall-mounted objects float in front of the drawn wall or sink into it. For each building this measures the
largest distance from any canonical vertex to the drawn ring and from any drawn vertex to the canonical ring
(symmetric Hausdorff over vertices, metres) and lists the buildings whose rings differ by more than 0.3 m.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

B = Path(sys.argv[1])
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else None
tm = json.loads((B / "terrain_metadata.json").read_text())
CS, OX, OY, E0, N0 = tm["content_scale"], tm["offset_x"], tm["offset_y"], tm["origin_easting"], tm["origin_northing"]


def ring_dist(p, ring):
    best = 1e9
    for a, b in zip(ring, ring[1:] + ring[:1]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy or 1e-12
        t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
        best = min(best, math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy))
    return best


canon = {}
for f in json.loads((B / "canonical.world_ir.json").read_text())["canonical_features"]:
    if "building_footprint" in f.get("feature_id", ""):
        pts = f["geometry"]["coordinates"]
        while isinstance(pts[0][0], list):
            pts = pts[0]
        ring = [tuple(p[:2]) for p in pts]
        canon[f["feature_id"]] = ring[:-1] if ring[0] == ring[-1] else ring
rows = []
for b in json.loads((B / "building_instances.json").read_text()):
    c = canon.get(b["feature_id"])
    if not c:
        rows.append({"building": b["feature_id"], "error": "no canonical footprint"})
        continue
    drawn = [(E0 + (x - OX) / CS, N0 + (y - OY) / CS) for x, y in (p[:2] for p in b["footprint"])]
    if drawn[0] == drawn[-1]:
        drawn = drawn[:-1]
    d1 = max(ring_dist(p, drawn) for p in c)
    d2 = max(ring_dist(p, c) for p in drawn)
    rows.append({"building": b["feature_id"].split(":", 2)[-1], "canon_to_drawn_m": round(d1, 2),
                 "drawn_to_canon_m": round(d2, 2), "max_m": round(max(d1, d2), 2),
                 "canon_vertices": len(c), "drawn_vertices": len(drawn)})
rows.sort(key=lambda r: -r.get("max_m", 99))
bad = [r for r in rows if r.get("max_m", 99) > 0.3]
print(f"{len(rows)} buildings; drawn ring differs from the canonical walls by > 0.3 m: {len(bad)}; > 1 m: "
      f"{sum(r.get('max_m', 99) > 1 for r in rows)}; > 3 m: {sum(r.get('max_m', 99) > 3 for r in rows)}")
for r in rows[:12]:
    print("  ", r)
if OUT:
    OUT.write_text(json.dumps({"schema": "y92.fl4876.mass_vs_footprint.v1", "build": str(B), "rows": rows},
                              ensure_ascii=False, indent=1) + "\n")
    print("wrote", OUT)
