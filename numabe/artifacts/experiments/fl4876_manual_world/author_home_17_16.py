"""author_home_17_16.py [--write] -- transcription of the main session's hand authoring (2026-10-13) of the home block
17-16 Den-en-chofu Honcho from the two operator reference photos (operator_photos/op_89ad6fb1448be364.png facing the
house SE; op_a637d8482424ccc5.png looking NE up the lane with the house on the right). No Mapillary panorama shows it.

Identity: footprint way/725138644 -- the terracotta roof in the GSI aerial (aerial_at.py 380041 3938707 30) sits on it,
7 m from the operator's home_start point; its NW edge (bearing 29, 7.9 m) faces lane 37708748 (centreline 2.6 m).
Frontage order from the photos, NE -> SW along that edge: pink crape myrtle, pruned pine on a rock bed behind a low
stone wall with an iron fence, brown post + white planter, carport under the tiled porch roof (car transient, not
authored), taupe gate pier carrying the 17-16 plate and a grey wall mailbox, dense hedge continuing SW.
Colours are medians of 60x60..100x150 px boxes in op_89ad6fb1448be364.png (operator_photo_intake.py --sample)."""
import json, math, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
B = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
APP, FX = B / "authored_building_appearance.json", B / "authored_scene_fixtures.json"
K = "feature:building_footprint:way/725138644"
PH1, PH2 = "operator_photo:op_89ad6fb1448be364", "operator_photo:op_a637d8482424ccc5"
BY = "main session (operator reference photos, 2026-10-13)"
A = (380031.85, 3938702.65)                      # SW end of the NW (lane) edge
U = (math.sin(math.radians(29)), math.cos(math.radians(29)))      # along the edge, toward NE
NO = (math.sin(math.radians(299)), math.cos(math.radians(299)))   # outward normal, toward the lane (NW)


def at(s, o):
    """point s m along the NW edge from its SW end, o m outward toward the lane"""
    return round(A[0] + U[0] * s + NO[0] * o, 2), round(A[1] + U[1] * s + NO[1] * o, 2)


app = json.loads(APP.read_text()); rec = app["buildings"][K]
rec.update({
    "what": "17-16 Den-en-chofu Honcho (home block): 2-storey white roughcast-stucco house with a terracotta Spanish-tile "
            "gable roof, dark half-timbering in the lane gable, small tiled hoods over the windows, an iron window grille, "
            "a planted first-floor balcony with a dark metal railing over the tiled porch roof, carport under the porch",
    "wall_rgb": [241, 240, 239], "trim_rgb": [59, 46, 40], "roof_rgb": [216, 164, 121],
    "features": ["white roughcast stucco walls", "terracotta Spanish (S-shaped) tile gable roof with deep eaves",
                 "dark timber half-timbering in the lane gable", "small terracotta-tiled hoods over the upper window and the porch",
                 "black iron window grille", "first-floor balcony with dark metal railing, potted plants and laundry",
                 "tiled porch roof over the carport", "taupe gate pier with the 17-16 address plate and a grey wall mailbox"],
    "roof_form": {"type": "gable", "ridge_bearing_deg": 119, "pitch_deg": 29, "eave_m": 6.3, "ridge_m": 8.5,
                  "basis": "TMG LiDAR raw roof profile (roof_field way/725138644): across bearing 29 it rises 6.3 -> 8.5 "
                           "-> 7.3 m, along 119 it stays 7.5-7.8 m, so the ridge runs 119/299 along the 9.3 m sides and the "
                           "gable ends face NE/SW; photo 1's half-timbered gable is the NE end seen obliquely, the lane "
                           "(NW) side is the long eave with the balcony", "eave_overhang_m": 0.6},
    "attachments": [
        {"kind": "balcony", "face_bearing_deg": 299, "storey": 2, "along_frac": 0.68, "width_frac": 0.55, "depth_m": 1.0,
         "style": "dark_metal", "rgb": [70, 58, 50], "slab_rgb": [216, 164, 121],
         "basis": "photo 1 upper right: first-floor balcony on the lane (NW) long side, right half seen from the lane, "
                  "over the tiled porch roof (slab drawn in the porch-tile colour); dark brown vertical-bar railing"},
        {"kind": "window_grille", "face_bearing_deg": 29, "storey": 2, "along_frac": 0.45, "width_frac": 0.2,
         "style": "dark_metal", "rgb": [40, 38, 38],
         "basis": "photo 1 left: black iron grille over the upper window of the NE gable end, under its tiled hood"},
    ],
    "street_view": {"state": "operator_photo", "checked": [PH1, PH2],
                    "reason": "no Mapillary panorama shows the house; the operator supplied two reference photos"},
    "evidence": (rec.get("evidence") or []) + [{
        "source": "operator reference photos", "capture_id": PH1, "assessed_by": BY,
        "samples": [{"xy_box": [240, 560, 300, 640], "rgb": [241, 240, 239], "what": "white stucco, lit"},
                    {"xy_box": [380, 700, 440, 760], "rgb": [240, 238, 235], "what": "white stucco, lit"},
                    {"xy_box": [700, 600, 800, 660], "rgb": [216, 164, 121], "what": "terracotta porch-roof tile, lit (same tile as the main roof; aerial roof point reads faded [190,167,156])"},
                    {"xy_box": [250, 200, 300, 260], "rgb": [59, 46, 40], "what": "dark half-timbering"},
                    {"xy_box": [640, 1000, 700, 1100], "rgb": [170, 159, 148], "what": "taupe gate pier, lit"},
                    {"xy_box": [780, 950, 860, 1050], "rgb": [49, 61, 34], "what": "hedge"}]}],
    "decided_on": "2026-10-13", "completion": "A",
})
if rec.get("wall_judgement"):
    rec["wall_judgement"]["superseded_by"] = "wall_rgb from operator photo (lit sample)"

S = lambda what, beside: [{"capture_id": PH1, "what": what, "beside": beside}]
REC = [  # fixture id, module, variant, s, o, yaw, unc, what, position basis
    ("SF-4876-H001", "small_tree", "flowering_plum_shrub", 8.4, 0.5, 299, 1.5,
     "pink-flowering crape myrtle (サルスベリ) at the NE end of the frontage",
     "photo 1 far left / photo 2 beyond the pine: NE end of the lane frontage, just past the NE corner"),
    ("SF-4876-H002", "small_tree", "pruned_niwaki", 5.6, 0.4, 299, 1.5,
     "cloud-pruned pine (niwaki) on a rock bed behind the low wall",
     "photo 1 left of the brown post, photo 2 right edge: over the low wall, NE half of the frontage"),
    ("SF-4876-H003", "concrete_block_wall", "brick_pattern_boundary_with_metal_fence", 5.8, 0.3, 29, 1.5,
     "low stone-faced wall with a dark iron fence on top along the lane",
     "photo 1/2: runs along the lane from the brown post NE to the crape myrtle"),
    ("SF-4876-H004", "stone_cobble_wall", "dark_stone_corner_planter", 3.6, 0.3, 29, 1.5,
     "white-rendered planter with the pine's rock mound and shrubs, brown post at its lane corner",
     "photo 1 centre-left: between the carport opening and the stone wall"),
    ("SF-4876-H005", "concrete_wall_column", "roughcast_vertical_panel", 0.6, 0.3, 299, 1.0,
     "taupe roughcast gate pier with the 17-16 address plate",
     "photo 1 right of the carport: SW side of the carport opening at the lane edge"),
    ("SF-4876-H006", "wall_mounted_mailbox", "gray_louvered_box", 0.9, 0.55, 299, 1.0,
     "grey louvered mailbox on the gate pier's lane face",
     "photo 1: mounted on the pier below the address plate"),
    ("SF-4876-H007", "trimmed_hedge", "dense_frontage_hedge", -1.4, 0.4, 29, 1.5,
     "tall dense frontage hedge continuing SW from the gate pier",
     "photo 1 right: from the pier SW along the lane edge"),
]
fx = json.loads(FX.read_text()); have = {f["fixture_id"] for f in fx["fixtures"]}
for fid, mod, var, s, o, yaw, unc, what, pbasis in REC:
    E, N = at(s, o)
    r = {"fixture_id": fid, "module_id": mod, "variant": var, "E": E, "N": N, "carrier_id": 725138644, "yaw_deg": yaw,
         "position_uncertainty_m": unc, "sightings": S(what, "lane frontage of 17-16 (way/725138644 NW edge, lane 37708748)"),
         "manual_review": {"status": "accepted", "reviewed_by": BY, "reviewed_on": "2026-10-13",
                           "module_basis": what, "position_basis": pbasis + " (%.1f m along the NW edge from its SW end, %.1f m outside it)" % (s, o),
                           "carrier_basis": "frontage of the home house way/725138644 on lane 37708748",
                           "orientation_basis": "faces the lane (299)" if yaw == 299 else "runs along the lane edge (29/209)"}}
    if fid in have:
        fx["fixtures"] = [r if f["fixture_id"] == fid else f for f in fx["fixtures"]]
    else:
        fx["fixtures"].append(r)
    print(fid, mod, var, E, N)
if "--write" in sys.argv:
    APP.write_text(json.dumps(app, ensure_ascii=False, indent=1) + "\n")
    FX.write_text(json.dumps(fx, ensure_ascii=False, indent=1) + "\n")
    print("written")
