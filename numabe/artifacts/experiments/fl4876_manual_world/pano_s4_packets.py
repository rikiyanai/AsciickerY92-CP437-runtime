"""pano_s4_packets.py [N_BATCHES] -- list-only (decides nothing): review packets for the fourth panorama pass
(FL-4876 FIXTURE-PASS4, operator goal: every visible object hand-placed, most likely position, common sense).

For every panorama in authored_building_appearance.json "panoramas" and every recorded visible object it lists:
  * bearing from the pixel x (equirect: heading + offset + (x/W - 0.5) * 360; perspective: approximate with an
    assumed 70 deg horizontal field, flagged as approximate) and, when the record carries distance_m_est, a
    projected E/N;
  * the scene fixtures (authored_scene_fixtures.json, accepted AND rejected, with status) whose point lies within
    6 deg of that bearing up to 45 m, or within 8 m of the projected point, with distance along/off the ray;
  * the pass-3 verdict for that object when one exists (SKIP rows are what pass 4 must revisit).
Plus the local image path, projection, size and heading offset of the panorama.

Writes assess/pano_s4_batches/pano_s4_NN.json (contiguous route order so neighbouring captures share a batch)
and assess/pano_s4_batches/index.json. Nothing is written to any owner."""
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
B = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
NB = int(sys.argv[1]) if len(sys.argv) > 1 else 10
app = json.loads((B / "authored_building_appearance.json").read_text())
PO = app["panoramas"]
P = json.loads((HERE / "pano_index.json").read_text())["panoramas"]
FX = json.loads((B / "authored_scene_fixtures.json").read_text())["fixtures"]
PERSP_HFOV = 70.0


def bearing_of(p, off, x, w):
    head = p["heading_deg"] + (off or 0.0)
    if p["projection"] == "equirectangular":
        return (head + (x / w - 0.5) * 360.0) % 360, "equirect"
    f = (w / 2) / math.tan(math.radians(PERSP_HFOV / 2))
    return (head + math.degrees(math.atan((x - w / 2) / f))) % 360, "perspective_approx_hfov70"


def ang(a, b):
    return abs((a - b + 180) % 360 - 180)


def near_fixtures(E, N, brg, proj):
    out = []
    for f in FX:
        dE, dN = f["E"] - E, f["N"] - N
        d = math.hypot(dE, dN)
        if d > 45:
            continue
        fb = math.degrees(math.atan2(dE, dN)) % 360
        off_ray = d * math.sin(math.radians(ang(fb, brg)))
        hit = ang(fb, brg) <= 6 or (proj and math.hypot(f["E"] - proj[0], f["N"] - proj[1]) <= 8)
        if hit:
            out.append({"fixture_id": f["fixture_id"], "module_id": f.get("module_id"),
                        "status": (f.get("manual_review") or {}).get("status"),
                        "dist_m": round(d, 1), "bearing": round(fb, 1), "off_ray_m": round(off_ray, 1),
                        "E": round(f["E"], 2), "N": round(f["N"], 2)})
    return sorted(out, key=lambda r: r["dist_m"])[:8]


packets = {}
for cid, rec in PO.items():
    p = P.get(cid)
    if not p:
        continue
    off = rec.get("heading_offset_deg") if isinstance(rec.get("heading_offset_deg"), (int, float)) else None
    w = p["width"]
    s3 = {(v.get("what") or "").strip(): v for v in rec.get("object_verdicts") or []}
    objs = []
    for i, o in enumerate(rec.get("visible_objects") or []):
        if isinstance(o, str):
            objs.append({"i": i, "what": o, "note": "text-only sighting (no pixel position recorded)"})
            continue
        row = {"i": i, "what": o.get("what"), "beside": o.get("beside"), "x": o.get("x"), "y": o.get("y"),
               "count": o.get("count"), "transient": o.get("transient"), "dist_est_m": o.get("distance_m_est"),
               "recorded_fixture": o.get("fixture")}
        if isinstance(o.get("x"), (int, float)):
            brg, how = bearing_of(p, off, o["x"], w)
            row["bearing"] = round(brg, 1)
            row["bearing_method"] = how + ("" if off is not None else " (no heading offset: raw compass)")
            proj = None
            if isinstance(o.get("distance_m_est"), (int, float)):
                d = float(o["distance_m_est"])
                proj = (p["E"] + d * math.sin(math.radians(brg)), p["N"] + d * math.cos(math.radians(brg)))
                row["projected_EN"] = [round(proj[0], 2), round(proj[1], 2)]
            row["candidates"] = near_fixtures(p["E"], p["N"], brg, proj)
        v3 = s3.get((o.get("what") or "").strip())
        if v3:
            row["pass3"] = v3
        objs.append(row)
    packets[cid] = {"capture_id": cid, "image": p.get("local_path"), "projection": p["projection"],
                    "size": [p["width"], p["height"]], "heading_deg": p["heading_deg"], "heading_offset_deg": off,
                    "E": p["E"], "N": p["N"], "timestamp": p.get("timestamp"),
                    "buildings_seen": rec.get("buildings_seen"), "pass3_done": "object_verdicts" in rec,
                    "objects": objs,
                    # pass-3 rows judged real but not placed; pass 4 places them (operator: most likely place)
                    "pass3_skips": [v for v in rec.get("object_verdicts") or [] if v.get("verdict") == "SKIP"]}
# contiguous by time within route (timestamp order keeps walk sequences together)
order = sorted(packets, key=lambda k: (packets[k]["timestamp"] or "", k))
out = HERE / "assess" / "pano_s4_batches"
out.mkdir(parents=True, exist_ok=True)
size = math.ceil(len(order) / NB)
index = {}
for b in range(NB):
    ids = order[b * size:(b + 1) * size]
    if not ids:
        continue
    name = "pano_s4_%02d" % b
    (out / (name + ".json")).write_text(json.dumps({"batch": name, "panoramas": {k: packets[k] for k in ids}},
                                                   ensure_ascii=False, indent=1))
    index[name] = {"panoramas": len(ids), "objects": sum(len(packets[k]["objects"]) for k in ids),
                   "pass3_skips": sum(len(packets[k]["pass3_skips"]) for k in ids)}
(out / "index.json").write_text(json.dumps(index, indent=1))
print(json.dumps(index, indent=1))
