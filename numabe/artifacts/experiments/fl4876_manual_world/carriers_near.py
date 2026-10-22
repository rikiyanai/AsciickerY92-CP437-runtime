"""carriers_near.py FIXTURE_ID [E N] -- reading aid: zone carriers within 15 m of the fixture's point
(or of E N if given): full carrier id, class, distance, and the bearing of the nearest edge segment
(degrees clockwise from north) for choosing the orientation. Decides nothing."""
import json, math, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
fid = sys.argv[1]
fx = next(f for f in json.loads((ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/"
                                        "authored_scene_fixtures.json").read_text())["fixtures"] if f["fixture_id"] == fid)
p = (float(sys.argv[2]), float(sys.argv[3])) if len(sys.argv) > 3 else (fx["E"], fx["N"])
zones = json.loads((ROOT / ".run/fl4876_builds/build_reg_43/observation_registration/placement_zones.json").read_text())


def seg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]; L2 = dx * dx + dy * dy or 1e-12
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


rows = []
for z in zones:
    pl = z.get("polyline_epsg32654") or []
    best = min(((seg(p, a, b), a, b) for a, b in zip(pl, pl[1:])), default=None, key=lambda r: r[0])
    if best and best[0] <= 15:
        brg = math.degrees(math.atan2(best[2][0] - best[1][0], best[2][1] - best[1][1])) % 180
        rows.append((best[0], z["carrier_id"], z["carrier_class"], brg))
print("%s %s at E %.2f N %.2f" % (fid, fx["module_id"], p[0], p[1]))
for dd, cid, cls, brg in sorted(rows)[:10]:
    print("  %-14s %-18s %5.1f m  edge bearing %5.1f / %5.1f" % (cid, cls, dd, brg, brg + 180))
