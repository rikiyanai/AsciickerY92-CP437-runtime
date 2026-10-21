"""merge_pano_s3.py [--write] -- transcription only (decides nothing): copies the third panorama pass
(assess/pano_s3_00..02.json, hand reviews of the 57 panoramas that had no aligned heading or no object
list) into the owners:
  * authored_building_appearance.json "panoramas": heading_offset_deg / offset_basis / aligned /
    buildings_seen / visible_objects / object verdicts, without overwriting an existing hand value;
  * authored_scene_fixtures.json: every MISSING record (complete accepted manual_review), after checking
    module/variant against the catalog and the carrier against the placement zones."""
import glob, json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
B = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
APP, FX = B / "authored_building_appearance.json", B / "authored_scene_fixtures.json"
cat = {m["module_id"]: set(m.get("variants") or []) for m in json.loads((ROOT / (
    "artifacts/experiments/fl4813_capture_review_20260915/preserved/numabe_full_727_mapping_20260915/"
    "generic_module_catalog.json")).read_text())["modules"]}
zones = {str(z["carrier_id"]) for z in json.loads((ROOT / ".run/fl4876_builds/build_reg_57/observation_registration/"
                                                          "placement_zones.json").read_text())}
app, fx = json.loads(APP.read_text()), json.loads(FX.read_text())
have = {f["fixture_id"] for f in fx["fixtures"]}
n = {"panoramas": 0, "offsets_set": 0, "objects_set": 0, "fixtures_added": 0, "rejected_records": []}
for p in sorted(glob.glob(str(HERE / "assess" / "pano_s3_0*.json"))):
    src = Path(p).stem
    for cid, r in json.loads(Path(p).read_text())["panoramas"].items():
        cur = app["panoramas"].setdefault(cid, {})
        n["panoramas"] += 1
        # numeric only: 19 panoramas once carried basis TEXT in this field (repair_text_offsets.py)
        if cur.get("heading_offset_deg") is None and isinstance(r.get("heading_offset_deg"), (int, float)):
            cur["heading_offset_deg"] = r["heading_offset_deg"]; n["offsets_set"] += 1
            cur["offset_basis"] = r.get("offset_basis")
        cur.setdefault("aligned", r.get("aligned"))
        if r.get("buildings_seen"):
            cur["buildings_seen"] = sorted(set(cur.get("buildings_seen") or []) | set(r["buildings_seen"]))
        if not cur.get("visible_objects") and r.get("visible_objects"):
            cur["visible_objects"] = r["visible_objects"]; n["objects_set"] += 1
        cur["object_verdicts"] = [{k: o.get(k) for k in ("what", "verdict", "owned_by", "reason")}
                                  for o in r.get("objects") or []]
        cur["pass3_assessed_by"] = src
        if r.get("notes"):
            cur["pass3_notes"] = r["notes"]
        for o in r.get("objects") or []:
            rec = o.get("record")
            if o.get("verdict") != "missing" and str(o.get("verdict")).upper() != "MISSING" or not rec:
                continue
            bad = []
            if rec.get("module_id") not in cat:
                bad.append("module")
            elif rec.get("variant") and rec["variant"] not in cat[rec["module_id"]]:
                bad.append("variant")
            if str(rec.get("carrier_id")) not in zones:
                bad.append("carrier")
            if bad:
                n["rejected_records"].append((rec.get("fixture_id"), bad)); continue
            if rec["fixture_id"] not in have:
                fx["fixtures"].append(rec); have.add(rec["fixture_id"]); n["fixtures_added"] += 1
print(n)
if "--write" in sys.argv:
    APP.write_text(json.dumps(app, ensure_ascii=False, indent=1) + "\n")
    FX.write_text(json.dumps(fx, ensure_ascii=False, indent=1) + "\n")
    print("written")
