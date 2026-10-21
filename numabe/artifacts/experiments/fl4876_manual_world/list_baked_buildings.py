#!/usr/bin/env python3
"""list_baked_buildings.py BUILD_DIR OUT.json [N_BATCHES] -- FL-4876 listing aid (measures, decides nothing).

Lists the buildings of BUILD_DIR/building_instances.json that are still terrain-baked plateaus in the live map: no
building_mass_* instance and no hand-built building_hand_<way>_* instance in output.a3d. For each: footprint centre
(E/N), route chainage and distance to the playable route (aoi.direct.geojson role connected_playable_route), roof
height, storeys, and the panorama cameras within 40 m (pano_index.json). Splits them into N_BATCHES (default 3) of
contiguous chainage with about equal counts, and writes OUT.json.
"""
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import a3d_edit as A  # noqa: E402

from pyproj import Transformer  # noqa: E402


MASS = False


def main():
    global MASS
    args = [a for a in sys.argv[1:] if a != "--mass"]
    MASS = "--mass" in sys.argv[1:]
    build, out = Path(args[0]), Path(args[1])
    nb = int(args[2]) if len(args) > 2 else 3
    mp = build / "output.a3d"
    meta = A.meta_for(mp)
    _, _, recs, _ = A.parse(mp.read_bytes())
    names = {r["inst"] for r in recs if r["kind"] == "mesh"}
    mesh_of = {r["inst"]: r["mesh"] for r in recs if r["kind"] == "mesh"}
    # hand-built instances: building_hand_way_<way>_<date> and the pilot's building_hand_<way>_<date>; a plateau way
    # id is itself 'bldg_<uuid>' (it contains '_'), so strip the known prefix and the trailing _<date> only
    hand = set()
    for n in names:
        if n.startswith("building_hand_"):
            core = n[len("building_hand_way_"):] if n.startswith("building_hand_way_") else n[len("building_hand_"):]
            hand.add(core.rsplit("_", 1)[0])
    blds = json.loads((build / "building_instances.json").read_text())
    tr = Transformer.from_crs("EPSG:4326", "EPSG:32654", always_xy=True)
    aoi = json.loads((build / "aoi.direct.geojson").read_text())
    line = next(f for f in aoi["features"] if f["properties"].get("role") == "connected_playable_route")
    pts = [tr.transform(lon, lat) for lon, lat in line["geometry"]["coordinates"]]
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.dist(a, b))
    panos = json.loads((HERE / "pano_index.json").read_text())["panoramas"]

    def chain(e, n):
        best = (1e18, 0.0)
        for i, (a, b) in enumerate(zip(pts, pts[1:])):
            dx, dy = b[0] - a[0], b[1] - a[1]
            L2 = dx * dx + dy * dy or 1e-9
            t = max(0.0, min(1.0, ((e - a[0]) * dx + (n - a[1]) * dy) / L2))
            px, py = a[0] + t * dx, a[1] + t * dy
            d = math.hypot(e - px, n - py)
            if d < best[0]:
                best = (d, cum[i] + t * math.sqrt(L2))
        return best

    rows = []
    for b in blds:
        way = b["feature_id"].rsplit("/", 1)[-1]
        if way in hand:
            continue
        has_mass = b["inst_name"] in names
        if has_mass != MASS:
            continue
        if MASS and not mesh_of[b["inst_name"]].startswith("mass_"):
            continue  # the mass instance already draws a hand-made mesh (e.g. the operator's home)
        en = [A.to_en(meta, x, y) for x, y in b["footprint"]]
        ce = sum(p[0] for p in en) / len(en)
        cn = sum(p[1] for p in en) / len(en)
        d, ch = chain(ce, cn)
        near = sorted((math.hypot(p["E"] - ce, p["N"] - cn), k) for k, p in panos.items()
                      if math.hypot(p["E"] - ce, p["N"] - cn) <= 40)
        rows.append({"feature_id": b["feature_id"], "way": way, "E": round(ce, 1), "N": round(cn, 1),
                     "chainage_m": round(ch, 1), "route_dist_m": round(d, 1),
                     "roof_height_m": b.get("roof_height_m"), "storeys": b.get("storeys"),
                     "area_m2": round(b.get("footprint_area_m2") or 0, 1),
                     "panos_40m": [k for _, k in near[:8]], "pano_count_40m": len(near)})
    rows.sort(key=lambda r: r["chainage_m"])
    size = math.ceil(len(rows) / nb) if rows else 0
    batches = []
    for i in range(nb):
        part = rows[i * size:(i + 1) * size]
        if part:
            batches.append({"batch": i, "chainage_from": part[0]["chainage_m"], "chainage_to": part[-1]["chainage_m"],
                            "count": len(part), "with_photo": sum(1 for r in part if r["pano_count_40m"]),
                            "buildings": part})
    doc = {"schema": "fl4876.baked_buildings.v1", "map": str(mp), "baked_total": len(rows),
           "with_photo_within_40m": sum(1 for r in rows if r["pano_count_40m"]), "batches": batches}
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    print("baked", len(rows), "with photo within 40 m", doc["with_photo_within_40m"])
    for bt in batches:
        print(f"batch {bt['batch']}: chainage {bt['chainage_from']}-{bt['chainage_to']} m, {bt['count']} buildings, "
              f"{bt['with_photo']} with a photo")


if __name__ == "__main__":
    main()
