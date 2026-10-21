"""resolve_vending_0406.py [--write] -- transcription of one hand conflict resolution (main session,
2026-10-13; decides nothing beyond what is written here).

Evidence: capture mapillary_945852359566475 (pano offset 0) shows ONE red Coca-Cola vending machine with
a poster at the left end of the shuttered lettered building, beside a utility pole, before the corner
crosswalk. Two records describe it: the registered observation inst-0406 (annotation
ann-1789451194391-5dc528, box x 442-471; explicit position POS-4876-026 at the foot of way/225367798,
0.5 m outside its wall, range 6.6 m) and accepted scene fixture SF-4876-0395 (sighting of the same capture
at x 452, 14 m, on the west wall of way/225367803). The bearing of x 456 (70.3 deg) meets way/225367798
face 8 at 7.8 m BEFORE way/225367803 (10.2 m), so the 14 m point lies behind the first wall: the
POS-4876-026 position is right. Result: SF-4876-0395 is the single object, moved to the POS-4876-026 point
on way/225367798 face 8 (outward 313.9 deg); identity IDADJ-4876-030 is owned by it."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
B = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
FX, ADJ = B / "authored_scene_fixtures.json", B / "authored_observation_positions.json"
fx = json.loads(FX.read_text()); adj = json.loads(ADJ.read_text())
f = next(f for f in fx["fixtures"] if f["fixture_id"] == "SF-4876-0395")
f.setdefault("previous_review", {"E": f["E"], "N": f["N"], "carrier_id": f["carrier_id"], "yaw_deg": f["yaw_deg"]})
f.update({"E": 379800.0, "N": 3938540.4, "carrier_id": 225367798, "yaw_deg": 313.9, "position_uncertainty_m": 0.8})
f["sightings"] = f["sightings"] + [s for s in [{"capture_id": "mapillary_945852359566475",
                                                 "annotation_id": "ann-1789451194391-5dc528", "x": 456,
                                                 "what": "red Coca-Cola vending machine with poster",
                                                 "beside": "foot of way/225367798, left end, beside the pole"}]
                                    if s["annotation_id"] not in {x.get("annotation_id") for x in f["sightings"]}]
mr = f["manual_review"]
mr.update({"reviewed_by": mr["reviewed_by"] + "; position corrected by main session (hand conflict resolution)",
           "reviewed_on": "2026-10-13",
           "position_basis": "same machine as observation inst-0406 (box x 442-471 = this fixture's x 452 sighting); "
                             "bearing 70.3 from capture 945852359566475 meets way/225367798 face 8 at 7.8 m before "
                             "way/225367803, so the earlier 14 m point on 225367803 was behind the first wall; placed at "
                             "POS-4876-026 (0.5 m outside face 8, 15.9 m along)",
           "carrier_basis": "the machine stands against way/225367798 at the left end of its lettered front",
           "orientation_basis": "faces out of face 8 (outward normal 313.9 deg) toward the street"})
for d in adj["identities"]:
    if d["decision_id"] == "IDADJ-4876-030":
        d["owned_by_scene_fixture"] = "SF-4876-0395"
        d["owned_by_basis"] = "same physical vending machine (resolve_vending_0406.py)"
print("SF-4876-0395 ->", f["E"], f["N"], f["carrier_id"], f["yaw_deg"])
if "--write" in sys.argv:
    FX.write_text(json.dumps(fx, ensure_ascii=False, indent=1) + "\n")
    ADJ.write_text(json.dumps(adj, ensure_ascii=False, indent=2) + "\n")
    print("written")
