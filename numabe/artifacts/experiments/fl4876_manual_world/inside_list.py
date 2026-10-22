"""inside_list.py -- list-only: accepted pass-4 fixtures (SF-4876-S4*) whose point lies inside a building footprint,
split into wall-mounted modules (which the pipeline snaps onto a face) and ground objects. For each ground object:
depth inside, the nearest face with open space in front, and the point 0.8 m outside that face (same along
position) -- a candidate for a hand decision, never applied by this script."""
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FX = json.loads((ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_scene_fixtures.json").read_text())["fixtures"]
WALL = {"small_commercial_awning", "independent_storefront_awning", "storefront_signboard", "storefront_banner",
        "exterior_ac_condenser", "wall_mounted_mailbox", "glass_entry", "roller_shutter_facade", "noren_or_shutter_entry",
        "meter_and_service_cabinet", "poster_notice_carrier", "notice_sign", "ivy_wall", "rooftop_safety_railing",
        "residential_facade_roofed_entry", "storefront_pedestal_sign", "residential_carport_canopy", "concrete_wall_column"}
fps = {}
for f in json.loads((ROOT / ".run/fl4876_builds/build_reg_75/canonical.world_ir.json").read_text())["canonical_features"]:
    if f.get("semantic_class") == "building_footprint":
        c = f["geometry"]["coordinates"]
        r = [tuple(p[:2]) for p in (c[0] if isinstance(c[0][0], list) else c)]
        fps[f["feature_id"]] = r[:-1] if r[0] == r[-1] else r


def inside(p, r):
    s = False
    for i in range(len(r)):
        (x1, y1), (x2, y2) = r[i], r[i - 1]
        if (y1 > p[1]) != (y2 > p[1]) and p[0] < (x2 - x1) * (p[1] - y1) / (y2 - y1) + x1:
            s = not s
    return s


def first_hit(o, d, me, maxd=30):
    best = None
    for k, r in fps.items():
        if k == me:
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


wall_n, rows = 0, []
for x in FX:
    if (x.get("manual_review") or {}).get("status") != "accepted" or not str(x["fixture_id"]).startswith("SF-4876-S4"):
        continue
    p = (x["E"], x["N"])
    host = next((k for k, r in fps.items() if inside(p, r)), None)
    if not host:
        continue
    if x["module_id"] in WALL:
        wall_n += 1
        continue
    ring = fps[host]
    area = sum(ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1] for i in range(len(ring)))
    best = None
    for i in range(len(ring)):
        a, b = ring[i], ring[(i + 1) % len(ring)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L < 0.5:
            continue
        n = (dy / L, -dx / L) if area > 0 else (-dy / L, dx / L)
        s = max(0.3, min(L - 0.3, (p[0] - a[0]) * dx / L + (p[1] - a[1]) * dy / L))
        q = (a[0] + dx * s / L, a[1] + dy * s / L)
        depth = (p[0] - q[0]) * -n[0] + (p[1] - q[1]) * -n[1]
        gap = first_hit((q[0] + n[0] * .05, q[1] + n[1] * .05), n, host)
        if gap is not None and gap < 1.6:
            continue
        cand = (math.hypot(p[0] - q[0], p[1] - q[1]), i, depth, gap, (q[0] + n[0] * .8, q[1] + n[1] * .8))
        if best is None or cand[0] < best[0]:
            best = cand
    rows.append((x["fixture_id"], x["module_id"], host.split("/")[-1], best))
print("inside a footprint: wall-mounted", wall_n, "| ground objects", len(rows))
for fid, mod, host, b in rows:
    if b is None:
        print("%s %-30s in %s: no open face" % (fid, mod, host))
    else:
        mv, i, depth, gap, out = b
        print("%s %-30s in %s: move %.1f m via face %d (gap %s) -> %.2f %.2f" % (
            fid, mod, host[-12:], mv, i, "open" if gap is None else "%.1f" % gap, out[0], out[1]))
