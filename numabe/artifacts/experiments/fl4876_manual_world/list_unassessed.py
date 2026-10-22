"""list_unassessed.py [R_M] -- list-only (decides nothing): buildings with no street record in the
appearance owner (no "what"), and for each, the panoramas within R m (default 40) that have a clear
sight line to at least one point of its outline (segment camera -> outline point crosses no other
footprint). Writes assess/unassessed_candidates.json {feature_id: [[capture_id, distance_m], ...]} and
assess/street2_batches.json (contiguous batches by area). Buildings with no clear sight line from
any panorama are listed under "no_street_view" for an explicit aerial-only note."""
import json, math, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
R = float(sys.argv[1]) if len(sys.argv) > 1 else 40.0
NB = int(sys.argv[2]) if len(sys.argv) > 2 else 6
own = json.loads((ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/"
                         "authored_building_appearance.json").read_text())["buildings"]
pano = json.loads((HERE / "pano_index.json").read_text())["panoramas"]
canon = json.loads((ROOT / ".run/fl4876_builds/build_reg_43/canonical.world_ir.json").read_text())
fps = {f["feature_id"]: f["geometry"]["coordinates"] for f in canon["canonical_features"]
       if f.get("semantic_class") == "building_footprint"}


def cross(a, b, c, d):
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return (o(a, b, c) * o(a, b, d) < 0) and (o(c, d, a) * o(c, d, b) < 0)


bbox = {k: (min(q[0] for q in v), min(q[1] for q in v), max(q[0] for q in v), max(q[1] for q in v)) for k, v in fps.items()}


def clear(cam, pt, own_id):
    lo = (min(cam[0], pt[0]), min(cam[1], pt[1])); hi = (max(cam[0], pt[0]), max(cam[1], pt[1]))
    for k, ring in fps.items():
        if k == own_id:
            continue
        b = bbox[k]
        if b[2] < lo[0] or b[0] > hi[0] or b[3] < lo[1] or b[1] > hi[1]:
            continue
        for a, c in zip(ring, ring[1:]):
            if cross(cam, pt, a, c):
                return False
    return True


def samples(ring):
    out = []
    for a, b in zip(ring, ring[1:]):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        for i in range(max(1, int(L // 2))):
            t = (i + 0.5) / max(1, int(L // 2))
            out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
    return out


todo = [k for k in fps if not (own.get(k) or {}).get("what")]
cand, none = {}, []
for k in todo:
    pts = samples(fps[k])
    rows = []
    for cid, p in pano.items():
        cam = (p["E"], p["N"])
        dmin = min(math.hypot(q[0] - cam[0], q[1] - cam[1]) for q in pts)
        if dmin > R:
            continue
        # outline points slightly pulled outward are tested so the building's own edge is not hit
        if any(clear(cam, q, k) for q in sorted(pts, key=lambda q: math.hypot(q[0] - cam[0], q[1] - cam[1]))[:12]):
            rows.append([cid, round(dmin, 1)])
    if rows:
        cand[k] = sorted(rows, key=lambda r: r[1])[:8]
    else:
        none.append(k)
(HERE / "assess" / "unassessed_candidates.json").write_text(json.dumps({"radius_m": R, "candidates": cand,
                                                                        "no_street_view": sorted(none)}, indent=1))
cen = {k: (sum(q[0] for q in fps[k][:-1]) / (len(fps[k]) - 1), sum(q[1] for q in fps[k][:-1]) / (len(fps[k]) - 1)) for k in cand}
ids = sorted(cand, key=lambda k: (round(cen[k][0] / 60), cen[k][1]))
size = -(-len(ids) // NB)
batches = {"street2_batch_%02d" % i: ids[i * size:(i + 1) * size] for i in range(NB)}
(HERE / "assess" / "street2_batches.json").write_text(json.dumps(batches, indent=1))
print("no street record", len(todo), "| visible from a panorama", len(cand), "| no clear view", len(none))
print({k: len(v) for k, v in batches.items()})
