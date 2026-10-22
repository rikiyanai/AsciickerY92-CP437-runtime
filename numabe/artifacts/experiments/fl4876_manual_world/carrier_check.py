"""carrier_check.py FIXTURE_ID [...] -- list-only: for an authored fixture, its distance to the carrier it names and
the nearest placement-zone carriers of each class (building outlines, roads, railways, waterways), plus whether
the point lies inside a building footprint. Uses build_reg_75 canonical features and placement zones."""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
B = ROOT / ".run/fl4876_builds/build_reg_75"
FX = {f["fixture_id"]: f for f in json.loads((ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/"
                                                      "authored_scene_fixtures.json").read_text())["fixtures"]}
zones = {str(z["carrier_id"]): z for z in json.loads((B / "observation_registration/placement_zones.json").read_text())}
geo = {}
for f in json.loads((B / "canonical.world_ir.json").read_text())["canonical_features"]:
    c = f.get("geometry", {}).get("coordinates")
    if not c or not isinstance(c[0], list):  # points carry bare coordinates
        continue
    pts = c[0] if isinstance(c[0][0], list) else c
    if not isinstance(pts[0], list):
        continue
    geo[f["feature_id"].split("/")[-1]] = (f.get("semantic_class"), [tuple(p[:2]) for p in pts])


def seg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / ((dx * dx + dy * dy) or 1e-9)))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def dist(p, pts):
    return min(seg(p, pts[i], pts[i + 1]) for i in range(len(pts) - 1)) if len(pts) > 1 else math.dist(p, pts[0])


def inside(p, pts):
    r = False
    for i in range(len(pts)):
        (x1, y1), (x2, y2) = pts[i], pts[i - 1]
        if (y1 > p[1]) != (y2 > p[1]) and p[0] < (x2 - x1) * (p[1] - y1) / (y2 - y1) + x1:
            r = not r
    return r


for fid in sys.argv[1:]:
    r = FX[fid]
    p = (r["E"], r["N"])
    own = geo.get(str(r["carrier_id"]))
    print("%s %s at (%.1f, %.1f) carrier %s (%s) dist %s" % (fid, r["module_id"], p[0], p[1], r["carrier_id"],
          zones.get(str(r["carrier_id"]), {}).get("carrier_class"), round(dist(p, own[1]), 1) if own else "n/a"))
    near = []
    for k, z in zones.items():
        g = geo.get(k)
        if not g:
            continue
        d = dist(p, g[1])
        if d < 15:
            near.append((round(d, 1), k, z["carrier_class"], "INSIDE" if z["carrier_class"] == "building" and inside(p, g[1]) else ""))
    for row in sorted(near)[:6]:
        print("    ", row)
    print("    what:", "; ".join(s.get("what", "")[:60] for s in r["sightings"][:2]))
    print("    pos:", r["manual_review"].get("position_basis", "")[:200])
