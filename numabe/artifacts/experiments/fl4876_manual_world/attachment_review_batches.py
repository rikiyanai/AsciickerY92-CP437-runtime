"""attachment_review_batches.py OUT_DIR N_BATCHES -- reading aid (decides nothing): review batches for the hand authoring of
building attachments (balconies, railings, window grilles, rooftop railings, stair handrails) on every building whose
hand record mentions them. Per building: record text and features, footprint faces (outward bearing, length m) from the
canonical World IR, PLATEAU storeys/height, and each evidence photo with its image path, camera position, aligned view
heading (pano_index heading + heading_offset_deg), bearing and distance from the camera to the footprint centroid."""
import json, math, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True); NB = int(sys.argv[2])
B = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
app = json.loads((B / "authored_building_appearance.json").read_text())
bl, pans = app["buildings"], app["panoramas"]
pi = json.loads((HERE / "pano_index.json").read_text())["panoramas"]
attrs = json.loads((HERE / "assess" / "plateau_attrs.json").read_text())
canon = json.loads((ROOT / ".run/fl4876_builds/build_reg_64/canonical.world_ir.json").read_text())
CAP = ROOT / "artifacts/experiments/fl4810_aoi_street_network_boxing_20260915/captures"
foot = {}
for f in canon["canonical_features"]:
    if f.get("semantic_class") == "building_footprint":
        q = f["geometry"]["coordinates"]
        while isinstance(q[0][0], list):
            q = q[0]
        foot[f["feature_id"]] = [tuple(p[:2]) for p in q]
T = ("railing", "rails", "balustrade", "handrail", "grille", "balcon")
rows = []
for k, v in bl.items():
    if k.endswith("way/725138644"):
        continue  # home block authored by the main session from the operator photos
    feats = [x for x in (v.get("features") or []) if any(t in str(x).lower() for t in T)]
    if not feats or k not in foot:
        continue
    ring = foot[k]
    if ring[0] == ring[-1]:
        ring = ring[:-1]
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1]))
    if area < 0:
        ring = ring[::-1]
    cx = sum(p[0] for p in ring) / len(ring); cy = sum(p[1] for p in ring) / len(ring)
    faces = []
    for i in range(len(ring)):
        p, q = ring[i], ring[(i + 1) % len(ring)]
        dx, dy = q[0] - p[0], q[1] - p[1]; ln = math.hypot(dx, dy)
        if ln < 0.5:
            continue
        faces.append({"outward_bearing_deg": round(math.degrees(math.atan2(dy, -dx)) % 360, 1), "length_m": round(ln, 1),
                      "midpoint_EN": [round((p[0] + q[0]) / 2, 1), round((p[1] + q[1]) / 2, 1)]})
    ev = []
    for e in v.get("evidence") or []:
        cid = e.get("capture_id")
        if not cid or cid in {x["capture_id"] for x in ev} or cid not in pi:
            continue
        cam = pi[cid]; off = (pans.get(cid) or {}).get("heading_offset_deg")
        ev.append({"capture_id": cid, "image": str(CAP / (cid + ".jpg")), "projection": cam.get("projection"),
                   "camera_EN": [round(cam["E"], 1), round(cam["N"], 1)],
                   "view_heading_deg": None if off is None else round((cam["heading_deg"] + off) % 360, 1),
                   "bearing_to_building_deg": round(math.degrees(math.atan2(cx - cam["E"], cy - cam["N"])) % 360, 1),
                   "distance_m": round(math.hypot(cx - cam["E"], cy - cam["N"]), 1),
                   "what_seen": e.get("what") or [s.get("what") for s in (e.get("samples") or [])][:4]})
    a = attrs.get(k) or {}
    rows.append({"id": k, "what": v.get("what"), "matching_features": feats, "all_features": v.get("features"),
                 "storeys": a.get("storeys") if a.get("storeys") != 9999 else None, "height_m": a.get("height_m"),
                 "faces": faces, "evidence": ev})
for b in range(NB):
    part = rows[b::NB]
    (out / ("attach_batch_%02d.json" % b)).write_text(json.dumps({"buildings": part}, ensure_ascii=False, indent=1))
print("buildings", len(rows), "batches", NB, "evidence photos", sum(len(r["evidence"]) for r in rows))
