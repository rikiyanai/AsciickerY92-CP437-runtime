"""fix_inside_mass_fixtures.py [--write] -- transcription of three hand position corrections (main session,
2026-10-13) for accepted scene fixtures that build_reg_55's spatial coherence found inside a building mass.
Decides nothing beyond the written decisions; each keeps its previous values under previous_review."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
FX = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_scene_fixtures.json"
FIX = {
    "SF-4876-0450": {"E": 379839.84, "N": 3938504.43, "carrier_id": 670892441, "position_uncertainty_m": 1.5,
        "position_basis": "the recorded point was 4.4 m INSIDE way/225367852; the nearest sighting (capture "
                          "967749253998911, camera on road 670892441) looks at bearing 258.6 and that ray meets the "
                          "street face 0 of way/225367852 at 8.8 m, so the 15-16 m estimate overshot into the building; "
                          "the photo shows the pole on the pavement just in front of the apartment's ground floor; "
                          "placed 8.0 m along the ray, 0.6 m outside the face and 3.9 m from the road centreline",
        "carrier_basis": "a utility pole stands on the edge of road 670892441, not inside the apartment"},
    "SF-4876-0294": {"E": 379827.99, "N": 3938432.86, "carrier_id": 1038001324, "position_uncertainty_m": 8.0,
        "position_basis": "the sighting places the pole 'between platform fence and 225563357'; the capture pose lies "
                          "inside a building block, so no ray is usable; placed 0.7 m outside the middle of the "
                          "track-facing SW face (face 1, 17.1 m, outward 232) of way/225563357, 6.3 m from railway "
                          "1038001324; along-face position unknown (uncertainty = half the face)",
        "carrier_basis": "the strip between the house wall and the platform fence belongs to the rail corridor"},
    "SF-4876-0211": {"E": 379871.6, "N": 3938481.35, "position_uncertainty_m": 0.8,
        "position_basis": "the TMG vertical return marked the pole but the point sat 0.5 m inside way/1507403671; both "
                          "photos show the lamp at the planter edge IN FRONT of the building toward the channel, so it "
                          "is moved across the nearest (SW) face to 0.5 m outside, 1.5 m from the waterway",
        "carrier_basis": None},
}
doc = json.loads(FX.read_text())
for f in doc["fixtures"]:
    d = FIX.get(f["fixture_id"])
    if not d:
        continue
    f.setdefault("previous_review", {k: f.get(k) for k in ("E", "N", "carrier_id", "position_uncertainty_m")})
    for k in ("E", "N", "carrier_id", "position_uncertainty_m"):
        if d.get(k) is not None:
            f[k] = d[k]
    mr = f["manual_review"]
    mr["position_basis"] = d["position_basis"]
    if d.get("carrier_basis"):
        mr["carrier_basis"] = d["carrier_basis"]
    mr["reviewed_by"] = str(mr.get("reviewed_by")) + "; position corrected by main session (spatial coherence inside-mass review)"
    mr["reviewed_on"] = "2026-10-13"
    print(f["fixture_id"], f["E"], f["N"], f["carrier_id"])
if "--write" in sys.argv:
    FX.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    print("written")
