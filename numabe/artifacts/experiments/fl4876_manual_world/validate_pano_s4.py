"""validate_pano_s4.py assess/pano_s4_NN.json [...] -- checks only (decides and writes nothing).

Shape of a fourth-pass review file (FL-4876 FIXTURE-PASS4):
  {"batch": "pano_s4_NN", "reviewer": "...", "panoramas": {capture_id: {"notes": str, "objects": [row, ...]}}}
  row = {"what", "verdict", "reason", "owned_by"?, "record"?, "packet_i"?, "same_as"?}
  verdict in OWNED (owned_by = an existing fixture_id), MISSING (record = complete fixture record),
  SAME_AS_NEW (same physical object as a MISSING record elsewhere in this pass; same_as = that fixture_id),
  TRANSIENT (cars, people, bins out for collection on the day, scaffolding), NOT_FIXTURE (building, roof, sky,
  terrain, vegetation mass, road paint already owned by the road surface).
  There is no SKIP: a real, permanent object is placed at its most likely position (operator instruction).
A MISSING record must carry fixture_id (SF-4876-S4NN-###), module_id (catalog), variant (catalog, or omitted),
E, N, carrier_id (a placement-zone carrier), yaw_deg, position_uncertainty_m, sightings[{capture_id,x,y,what}],
manual_review{status accepted, reviewed_by, reviewed_on, module_basis, position_basis, carrier_basis,
orientation_basis}."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
B = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
cat = {m["module_id"]: set(m.get("variants") or []) for m in json.loads((ROOT / (
    "artifacts/experiments/fl4813_capture_review_20260915/preserved/numabe_full_727_mapping_20260915/"
    "generic_module_catalog.json")).read_text())["modules"]}
zones = {str(z["carrier_id"]) for z in json.loads(
    (ROOT / ".run/fl4876_builds/build_reg_73/observation_registration/placement_zones.json").read_text())}
have = {f["fixture_id"] for f in json.loads((B / "authored_scene_fixtures.json").read_text())["fixtures"]}
VERDICTS = {"OWNED", "MISSING", "SAME_AS_NEW", "TRANSIENT", "NOT_FIXTURE"}
NEED = ("fixture_id", "module_id", "E", "N", "carrier_id", "yaw_deg", "position_uncertainty_m", "sightings",
        "manual_review")
MR = ("status", "reviewed_by", "reviewed_on", "module_basis", "position_basis", "carrier_basis", "orientation_basis")
errors, counts, new_ids = [], {}, {}
for path in sys.argv[1:]:
    doc = json.loads(Path(path).read_text())
    for cid, r in doc["panoramas"].items():
        for k, o in enumerate(r.get("objects") or []):
            where = "%s %s #%d" % (Path(path).name, cid, k)
            v = o.get("verdict")
            counts[v] = counts.get(v, 0) + 1
            if v not in VERDICTS:
                errors.append("%s: verdict %r" % (where, v)); continue
            if not o.get("reason"):
                errors.append("%s: no reason" % where)
            if v == "OWNED" and o.get("owned_by") not in have and o.get("owned_by") not in new_ids:
                errors.append("%s: owned_by %r is not an existing fixture" % (where, o.get("owned_by")))
            if v == "MISSING":
                rec = o.get("record") or {}
                miss = [f for f in NEED if f not in rec]
                miss += ["manual_review." + f for f in MR if f not in (rec.get("manual_review") or {})]
                if miss:
                    errors.append("%s: record lacks %s" % (where, miss))
                if rec.get("module_id") not in cat:
                    errors.append("%s: module %r not in catalog" % (where, rec.get("module_id")))
                elif rec.get("variant") and rec["variant"] not in cat[rec["module_id"]]:
                    errors.append("%s: variant %r not in %s" % (where, rec["variant"], rec["module_id"]))
                if str(rec.get("carrier_id")) not in zones:
                    errors.append("%s: carrier %r not a placement zone" % (where, rec.get("carrier_id")))
                fid = rec.get("fixture_id")
                # a pass-4 id already in the owner was written by an earlier merge of these same files
                if (fid in have and not str(fid).startswith("SF-4876-S4")) or fid in new_ids:
                    errors.append("%s: duplicate fixture_id %r" % (where, fid))
                new_ids[fid] = where
            if v == "SAME_AS_NEW" and not o.get("same_as"):
                errors.append("%s: SAME_AS_NEW without same_as" % where)
for o_where in ():
    pass
# SAME_AS_NEW targets must exist among the MISSING ids of the files checked together
for path in sys.argv[1:]:
    for cid, r in json.loads(Path(path).read_text())["panoramas"].items():
        for k, o in enumerate(r.get("objects") or []):
            if o.get("verdict") == "SAME_AS_NEW" and o.get("same_as") not in new_ids:
                errors.append("%s %s #%d: same_as %r not a MISSING record here" % (Path(path).name, cid, k, o.get("same_as")))
print(json.dumps({"files": len(sys.argv) - 1, "verdicts": counts, "new_fixtures": len(new_ids),
                  "errors": len(errors)}, ensure_ascii=False))
for e in errors[:60]:
    print("  ERROR", e)
sys.exit(1 if errors else 0)
