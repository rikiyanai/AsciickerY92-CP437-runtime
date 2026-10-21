"""sv_plan.py [MERGE_M] -- planning aid only (decides nothing): for every route_visible building with no
wall colour in the appearance owner, a Street View viewpoint on the nearest road centreline in front of
its closest road-facing edge, the heading from there to the building, and the Google Maps URL
(official Maps URLs pano action, no key). Viewpoints closer than MERGE_M (default 18 m) are merged so one
view covers several buildings (each keeps its own heading). Writes assess/sv_plan.json."""
import json, math, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
from fetch_aerial import utm_to_ll  # noqa: E402
MERGE = float(sys.argv[1]) if len(sys.argv) > 1 else 18.0
own = json.loads((ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/"
                         "authored_building_appearance.json").read_text())["buildings"]
B = ROOT / ".run/fl4876_builds/build_reg_46"
imp = {r["feature_id"]: r.get("importance") for r in json.loads((B / "route_importance.json").read_text())["rows"]}
canon = json.loads((B / "canonical.world_ir.json").read_text())
fps = {f["feature_id"]: f["geometry"]["coordinates"] for f in canon["canonical_features"]
       if f.get("semantic_class") == "building_footprint"}
roads = [f["geometry"]["coordinates"] for f in canon["canonical_features"]
         if f.get("semantic_class") == "road" and (f.get("geometry") or {}).get("kind") == "polyline"]


def near(p, pl):
    best = None
    for a, b in zip(pl, pl[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]; L2 = dx * dx + dy * dy or 1e-12
        t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
        q = (a[0] + t * dx, a[1] + t * dy); d = math.dist(p, q)
        if best is None or d < best[0]:
            best = (d, q)
    return best


todo = [k for k in fps if imp.get(k) == "route_visible" and not (own.get(k) or {}).get("wall_rgb")]
views = []
for k in todo:
    ring = fps[k]
    mids = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(ring, ring[1:])]
    best = min(((near(m, r), m) for m in mids for r in roads), key=lambda x: x[0][0])
    (d, q), m = best
    views.append({"building": k, "view_EN": q, "facade_EN": m, "road_dist_m": round(d, 1)})
groups = []
for v in sorted(views, key=lambda v: (v["view_EN"][0], v["view_EN"][1])):
    g = next((g for g in groups if math.dist(g["EN"], v["view_EN"]) < MERGE), None)
    if not g:
        g = {"EN": v["view_EN"], "buildings": []}; groups.append(g)
    hd = math.degrees(math.atan2(v["facade_EN"][0] - g["EN"][0], v["facade_EN"][1] - g["EN"][1])) % 360
    g["buildings"].append({"feature_id": v["building"], "heading_deg": round(hd), "facade_road_dist_m": v["road_dist_m"]})
for i, g in enumerate(groups):
    lo, la = utm_to_ll(*g["EN"])
    g["id"] = "SV%03d" % i; g["lat"] = round(la, 6); g["lon"] = round(lo, 6)
    h0 = g["buildings"][0]["heading_deg"]
    g["url"] = ("https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=%.6f,%.6f&heading=%d&pitch=5&fov=90"
                % (la, lo, h0))
(HERE / "assess" / "sv_plan.json").write_text(json.dumps({"merge_m": MERGE, "views": groups}, indent=1))
print("buildings", len(todo), "viewpoints", len(groups))
