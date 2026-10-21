#!/usr/bin/env python3
"""list_sv_panos.py OUT.json [PER_BATCH] -- FL-4876 listing aid (measures, decides nothing).

Orders the 360 street panoramas the editor's street-view guide uses (GET http://127.0.0.1:8792/api/sv) by route
chainage (aoi.direct.geojson role connected_playable_route), adds the hand-built buildings within 35 m of each camera,
and splits them into batches of PER_BATCH (default 24) contiguous panoramas for the street-view plausibility pass.
"""
import json
import math
import sys
import urllib.request
from pathlib import Path

from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[3]
BUILD = ROOT / ".run/fl4876_builds/live_numabe"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import a3d_edit as A  # noqa: E402


def main():
    out = Path(sys.argv[1])
    per = int(sys.argv[2]) if len(sys.argv) > 2 else 24
    sv = json.load(urllib.request.urlopen("http://127.0.0.1:8792/api/sv"))
    panos = sv["panoramas"]
    aoi = json.loads((BUILD / "aoi.direct.geojson").read_text())
    line = next(f for f in aoi["features"] if f["properties"].get("role") == "connected_playable_route")
    tr = Transformer.from_crs("EPSG:4326", "EPSG:32654", always_xy=True)
    pts = [tr.transform(lon, lat) for lon, lat in line["geometry"]["coordinates"]]
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.dist(a, b))

    def chain(e, n):
        best = (1e18, 0.0)
        for i, (a, b) in enumerate(zip(pts, pts[1:])):
            dx, dy = b[0] - a[0], b[1] - a[1]
            L2 = dx * dx + dy * dy or 1e-9
            t = max(0.0, min(1.0, ((e - a[0]) * dx + (n - a[1]) * dy) / L2))
            d = math.hypot(e - (a[0] + t * dx), n - (a[1] + t * dy))
            if d < best[0]:
                best = (d, cum[i] + t * math.sqrt(L2))
        return best

    meta = A.meta_for(BUILD / "output.a3d")
    blds = json.loads((BUILD / "building_instances.json").read_text())
    cents = []
    for b in blds:
        en = [A.to_en(meta, x, y) for x, y in b["footprint"]]
        cents.append((b["feature_id"].rsplit("/", 1)[-1], sum(p[0] for p in en) / len(en), sum(p[1] for p in en) / len(en)))
    rows = []
    for p in panos:
        d, ch = chain(p["E"], p["N"])
        near = sorted((math.hypot(e - p["E"], n - p["N"]), w) for w, e, n in cents if math.hypot(e - p["E"], n - p["N"]) <= 35)
        rows.append({"id": p["id"], "E": p["E"], "N": p["N"], "chainage_m": round(ch, 1), "route_dist_m": round(d, 1),
                     "aligned": p["id"] in (sv.get("alignment") or {}).get("alignments", {}),
                     "buildings_35m": [w for _, w in near]})
    rows.sort(key=lambda r: r["chainage_m"])
    batches = [{"batch": i // per, "panoramas": rows[i:i + per]} for i in range(0, len(rows), per)]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"schema": "fl4876.sv_pass.v1", "count": len(rows), "per_batch": per, "batches": batches},
                              indent=1))
    print("panoramas", len(rows), "batches", len(batches))
    for b in batches:
        ps = b["panoramas"]
        print(f"batch {b['batch']}: chainage {ps[0]['chainage_m']}-{ps[-1]['chainage_m']} m, {len(ps)} panoramas")


if __name__ == "__main__":
    main()
