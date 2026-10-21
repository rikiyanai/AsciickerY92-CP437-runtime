#!/usr/bin/env python3
"""track_overlap_list.py BUILD_DIR [RADIUS_M] [OUT_JSON] -- FL-4876 listing aid (measures, decides nothing).

Lists every built object whose origin lies within RADIUS_M (default 3.0) of a train route centreline, by module, so a
person can say which ones belong on the track bed. Train routes come from <build>/output.trains.json (A3D units,
converted to metres with terrain_metadata.json). Objects: lower_fixtures / lower_linear / lower_custom instances,
place fronts, the station facade and every sprite in sign_sprites_receipt.json. A linear run is tested at both ends
and its midpoint. Track infrastructure (rail_track_corridor, level-crossing deck) is counted but listed apart.
"""
from __future__ import annotations

import collections
import json
import math
import sys
from pathlib import Path

B = Path(sys.argv[1])
RAD = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
OUT = Path(sys.argv[3]) if len(sys.argv) > 3 else None
tm = json.loads((B / "terrain_metadata.json").read_text())
CS, OX, OY, E0, N0 = tm["content_scale"], tm["offset_x"], tm["offset_y"], tm["origin_easting"], tm["origin_northing"]


def to_en(x, y):
    return E0 + (x - OX) / CS, N0 + (y - OY) / CS


routes = json.loads((B / "output.trains.json").read_text())["routes"]
SEGS = []
for r in routes:
    pts = [to_en(p[0], p[1]) for p in r["polyline"]]
    SEGS += [(r["id"], a, b) for a, b in zip(pts, pts[1:])]


def rail_dist(e, n):
    best = (1e9, None)
    for rid, a, b in SEGS:
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy or 1e-12
        t = max(0.0, min(1.0, ((e - a[0]) * dx + (n - a[1]) * dy) / L2))
        d = math.hypot(e - (a[0] + t * dx), n - (a[1] + t * dy))
        if d < best[0]:
            best = (d, rid)
    return best


TRACK = {"rail_track_corridor", "level_crossing_equipment"}
rows = []


def consider(src, iid, module, pts, extra=None):
    d, rid = min(rail_dist(e, n) for e, n in pts)
    if d <= RAD:
        rows.append({"source": src, "id": iid, "module": module, "dist_m": round(d, 2), "route": rid,
                     "track_infra": module in TRACK, "E": round(pts[0][0], 2), "N": round(pts[0][1], 2), **(extra or {})})


for r in json.loads((B / "lower_fixtures_instances.json").read_text()):
    ac = (r.get("metric_contract") or {}).get("asset_contract") or {}
    consider("fixture", r.get("placement_id"), ac.get("module_id") or r.get("module_id"), [(r["E"], r["N"])],
             {"evidence": r.get("evidence_kind")})
for r in json.loads((B / "lower_linear_instances.json").read_text()):
    rr = r.get("run_record") if isinstance(r.get("run_record"), dict) else {}
    pts = [(r["E"], r["N"])]
    consider("linear", r.get("logical_instance_id"), r.get("module_id"), pts, {"evidence": r.get("evidence_kind")})
for r in json.loads((B / "lower_custom_instances.json").read_text()):
    consider("custom", r.get("placement_id"), r.get("subject"), [(r["E"], r["N"])])
for r in json.loads((B / "place_front_instances.json").read_text()):
    consider("place_front", r.get("feature_id"), r.get("asset_match"), [to_en(*r["front_anchor_world_xy"])])
for s in json.loads((B / "sign_sprites_receipt.json").read_text())["signs"]:
    pos = (s.get("plane") or {}).get("pos") or s["pos"]  # SIGN-PLANE-01: the wall mesh position
    consider("sign_sprite", s["label"], s.get("kind"), [to_en(pos[0], pos[1])])

non = [r for r in rows if not r["track_infra"]]
print(f"within {RAD} m of a train route: {len(rows)} objects ({len(rows) - len(non)} track infrastructure, {len(non)} other)")
c = collections.Counter((r["source"], r["module"]) for r in non)
for k, n in c.most_common():
    print(f"  {n:3d} {k[0]:12s} {k[1]}  min {min(r['dist_m'] for r in non if (r['source'], r['module']) == k)} m")
if OUT:
    OUT.write_text(json.dumps({"schema": "y92.fl4876.track_overlap_list.v1", "build": str(B), "radius_m": RAD,
                               "rows": sorted(rows, key=lambda r: r["dist_m"])}, ensure_ascii=False, indent=1) + "\n")
    print("wrote", OUT)
