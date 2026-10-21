"""resolve_flagged_fixtures.py [--write] -- transcription of hand resolutions (main session, 2026-10-13) of
accepted scene fixtures flagged by the third panorama pass. Decides nothing beyond what is written here;
previous values are kept under previous_review.
  SF-4876-0540  REJECT duplicate of SF-4876-0384: both are the one footbridge over the Rokugo channel at the
                plaza entrance (2.2 m apart on carrier 225198783); 0384 keeps the satellite-checked point.
  SF-4876-0542  MOVE: its point was a default (longest-edge midpoint of 225197888, 'no pano pose'); capture
                5779981762023097 (flat photo on lane 670892441, heading corrected by 180 to look NW) shows the
                white cabinet on the LEFT lane side against the white multi-storey building at ~15 m: bearing
                323 from the camera meets the lane-facing wall of way/225367803; placed 0.4 m outside it.
  SF-4876-0545  MOVE: the 'ジャバラ' interpretive panel fills capture 463520421371151, taken on the footbridge
                (camera 2.2 m from SF-4876-0384) looking NE; the footbridge's wooden railing stands behind it.
                The earlier point was 36 m W ('near mapped point', no pano pose). Placed 1.5 m NE of the camera.
  SF-4876-0548  CHECKED, unchanged: 0.29 m off the rounded wall of plateau/bldg_f55b4339 as photographed;
                its carrier is the lane only because that PLATEAU footprint has no zone carrier."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
FX = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_scene_fixtures.json"
BY = "main session (hand resolution of pass-3 flags)"
doc = json.loads(FX.read_text())
F = {f["fixture_id"]: f for f in doc["fixtures"]}


def keep_prev(f):
    f.setdefault("previous_review", {k: f.get(k) for k in ("E", "N", "carrier_id", "yaw_deg", "position_uncertainty_m")}
                 | {"status": f["manual_review"].get("status")})


f = F["SF-4876-0540"]; keep_prev(f)
f["manual_review"].update({"status": "rejected", "reject_reason": "duplicate of SF-4876-0384 (same footbridge, 2.2 m apart)",
                           "reviewed_by": BY, "reviewed_on": "2026-10-13"})
f = F["SF-4876-0542"]; keep_prev(f)
f.update({"E": 379809.01, "N": 3938547.45, "carrier_id": 225367803, "position_uncertainty_m": 4.0})
f["manual_review"].update({"reviewed_by": BY, "reviewed_on": "2026-10-13",
    "position_basis": "capture 5779981762023097 looking NW (offset 180) shows the cabinet on the left lane side at ~15 m; "
                      "bearing 323 meets the lane-facing wall of way/225367803; 0.4 m outside it (camera pose loose: 4 m)",
    "carrier_basis": "stands against the white multi-storey building way/225367803 on the lane"})
f = F["SF-4876-0545"]; keep_prev(f)
f.update({"E": 379788.27, "N": 3938575.02, "yaw_deg": 227.0, "position_uncertainty_m": 2.0})
f["manual_review"].update({"reviewed_by": BY, "reviewed_on": "2026-10-13",
    "position_basis": "close-up from capture 463520421371151 taken on the footbridge (camera 2.2 m from SF-4876-0384) "
                      "looking NE; the panel stands ~1.5 m ahead in front of the footbridge railing",
    "orientation_basis": "panel faces the camera on the bridge approach (SW, 227)"})
F["SF-4876-0548"]["manual_review"]["position_check_2026_10_13"] = (
    "0.29 m off the rounded wall of plateau/bldg_f55b4339 as photographed; carrier is the lane because that PLATEAU "
    "footprint has no zone carrier")
for k in ("SF-4876-0540", "SF-4876-0542", "SF-4876-0545"):
    print(k, F[k]["manual_review"]["status"], F[k]["E"], F[k]["N"])
if "--write" in sys.argv:
    FX.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    print("written")
