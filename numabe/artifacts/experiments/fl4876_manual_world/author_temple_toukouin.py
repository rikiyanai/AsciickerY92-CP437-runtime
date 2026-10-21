"""author_temple_toukouin.py [--write] -- transcription of the main session's hand authoring (2026-10-16) of the
有慶山東光院 (Toukou-in) temple grounds from the panoramas. Operator 2026-10-16: "TEMPLE WRONG, NO MESH OR ANYTHING
FOR THAT (MAKE THAT BOOK MARK AND PROPERLY AUTHOR)". build_reg_75 drew every temple building as a generic box
(LiDAR roof fits: hall 225101048 flat, gate 225101506 shed, outbuilding 225101505 flat).

Evidence (crops kept under assess/temple_ref/):
  mapillary_240959581154487 (camera E379770.2 N3938582.9, panorama left edge, bearing ~73 deg to the hall centroid):
    the main hall: teal-green copper hip roof with a small gable at the top (irimoya), a second teal pent roof
    (mokoshi) below it over a white-plaster wall band with dark timber posts, behind the cream precinct wall with
    blue tile coping;
  mapillary_3921136788001424 (junction NE of the gate, right edge): grey metal gate leaf in the cream wall with blue
    coping, the hall's teal roof behind it, the wisteria pergola to the left;
  mapillary_476931043387324: wisteria pergola with benches over the stream-side path, white wall with blue coping;
  mapillary_489113339072184: cream boundary wall with blue tile coping on a stone base above the planted stream bed.
Footprints and heights (footprint_edges.py on build_reg_75; PLATEAU measuredHeight):
  hall 225101048 18.9 x 11.3 m, long axis 124.4/304.4, 8.1 m; gate 225101506 4.1 x 4.4 m, road face bearing 123.6
  (outward 33.6), 4.5 m; outbuilding 225101505 5.6 x 4.9 m, 4.9 m.
roof_form here is a HAND decision of form, ridge direction and heights (the LiDAR fitter cannot resolve these roofs);
the bake turns it into the same roof model roof_form_fit.py writes (AUTHORED-ROOF-01).

CORRECTION 2026-10-16: the teal hall in 240959581154487 is way/225101288 (bearing_span.py: 10-11 m from the camera,
10.7 m tall, ~48 deg above the horizon), NOT 225101048 (30-35 m away; the priests' quarters 庫裏, record restored).
225101288 is an L-shaped 456 m2 OSM outline (rectangularity 0.68) joining the hall and a support wing, so one roof
model cannot cover it; its roof waits for a hand split of the outline into hall and wing (open item).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
B = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
APP = B / "authored_building_appearance.json"
BY = "main session (panorama review, 2026-10-16)"
TEAL, TEAL_SHADE, PLASTER, TIMBER = [115, 198, 197], [68, 100, 103], [233, 232, 228], [66, 50, 42]

AUTH = {
    # the main hall: roof_field_map.py on roof_fields_s15/way_225101288 shows ONE peaked roof (ridge ~11 m, running
    # N-S for ~10 m around E379783.5 N3938596.6, falling ~1 m per 1.5 m on every side to 4-5 m); the NE wing stays
    # at 4-5 m. A steep hip clamped at the eave reproduces both, so the L-shaped outline needs no split.
    "feature:building_footprint:way/225101288": {
        "what": "有慶山東光院 main hall (本堂): large teal-green copper hip roof with a small top gable over white "
                "plaster and dark timber, joined on the NE by a lower pale-grey two-storey support wing whose long "
                "rendered wall faces the street",
        "roof_rgb": TEAL,
        "roof_form": {"type": "tier_field", "authored": True,
                      "basis": "KEEP THE MEASURED LiDAR TIER FIELD. Compared with the raw TMG LiDAR heights (mean "
                               "|residual|): tier field 0.50 m (p90 1.11), hand hip measured off the profile (ridge "
                               "10.4 m along 38 deg, 41 deg, 12 m ridge) 1.21 m (p90 2.93), LiDAR hip fit 1.38 m "
                               "(ridge 2 m too low), flat 1.51 m. The hall plus wing outline is not one roof plane "
                               "model; its temple form (deep eaves, top gable, pent roof, timber and plaster) needs a "
                               "hand-modelled hall mesh (open)"},
        "evidence_add": [{"capture_id": "mapillary_240959581154487", "assessed_by": BY,
                          "what": "main hall seen across the stream: teal hip roof with top gable, teal pent roof, "
                                  "white plaster panels between dark timber",
                          "crop": "artifacts/experiments/fl4876_manual_world/assess/temple_ref/240959581154487_hall.png"}],
    },
    "feature:building_footprint:way/225101506": {
        "what": "temple main gate (山門): heavy dark-grey tiled gable roof on a timber frame over the NE road entrance, "
                "grey metal gate leaves set in the cream precinct wall",
        "roof_form": {"type": "shed", "authored": True, "fit": True, "eave_overhang_m": 0.6,
                      "basis": "the LiDAR measures ONE plane over this outline (shed rms 0.05 m on 134 points, 21.7 "
                               "deg); a hand gable fitted worse than flat (0.75 vs 0.42 m), so the measured slope "
                               "stands. The gate's own gable may be smaller than this outline (open)"},
        "evidence_add": [{"capture_id": "mapillary_3921136788001424", "assessed_by": BY,
                          "what": "grey metal gate leaf in the cream wall with blue tile coping; hall roof behind",
                          "crop": "artifacts/experiments/fl4876_manual_world/assess/temple_ref/3921136788001424_gate.png"}],
    },
    "feature:building_footprint:way/225101505": {
        "roof_form": {"type": "tier_field", "authored": True,
                      "basis": "KEEP THE MEASURED LiDAR TIER FIELD: mean |residual| to the raw heights 0.16 m, against "
                               "0.70 m for a fitted hip (record 'what': blue-grey tiled roof)"},
    },
}

app = json.loads(APP.read_text())
for fid, upd in AUTH.items():
    rec = app["buildings"].setdefault(fid, {})
    ev = upd.pop("evidence_add", [])
    att = upd.pop("attachments", None)
    rec.update(upd)
    if att is not None:
        rec["attachments"] = [a for a in (rec.get("attachments") or []) if a.get("kind") != "pent_roof"] + att
    have = {json.dumps(e, sort_keys=True) for e in rec.get("evidence") or []}
    rec["evidence"] = (rec.get("evidence") or []) + [e for e in ev if json.dumps(e, sort_keys=True) not in have]
    rec["decided_on"] = "2026-10-16"
    print(fid.split(":")[-1], json.dumps(rec.get("roof_form"))[:120])
if "--write" in sys.argv:
    APP.write_text(json.dumps(app, ensure_ascii=False, indent=1) + "\n")
    print("written")
