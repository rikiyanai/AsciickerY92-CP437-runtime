"""align_remaining_panos.py [--write] -- transcription of the main session's hand alignment (2026-10-13) of the 22
panoramas left without a heading after pass 3. Decides nothing beyond what is written here.
Convention (objects_to_fixtures.py): true heading of the image centre = pano_index heading_deg + heading_offset_deg.

Evidence used (reading aids: unaligned_sheet.py contact sheets, pose_aids.py, full-frame views):
  * Numabe station tracks: 1038001324 (NE track) and 1038001323 (SW track), bearing 140.8 / 320.8; two side
    platforms outside the tracks. Platform frames look straight ACROSS the tracks (canopy, platform edge and
    fence rails run level across the frame), so the view is perpendicular to the track: 50.8 (looking NE) or 230.8
    (looking SW). Left-hand running: a NW-bound train (compass ~317-322 = direction of travel) runs on the SW
    track and its window faces the SW platform; the NE platform carries the exit stair / ramp with the up-arrow
    signs seen in 1843293916567635 and 1084209643308007.
  * Lane / building geometry and the sun position (pose_aids.py) for the off-platform frames."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_building_appearance.json"
pi = json.loads((Path(__file__).resolve().parent / "pano_index.json").read_text())["panoramas"]
NE = "platform frame looking across both tracks at the NE platform (exit stair/ramp side), canopy and edge level across " \
     "the frame -> view perpendicular to track 140.8/320.8 looking NE"
SW = "train-window frame from a NW-bound train on the SW track (compass = travel direction ~321); adjacent SW " \
     "platform/wall fills the frame with no track between, edges level -> view perpendicular to the track looking SW"
TRUE = {  # capture: (true heading of image centre, uncertainty deg, basis)
    "2234678753657758": (50.8, 8, NE), "1241014224014542": (50.8, 8, NE), "1149890363825364": (50.8, 8, NE),
    "1291145979378343": (50.8, 8, NE), "1843293916567635": (50.8, 8, NE), "2130952124083816": (50.8, 8, NE),
    "1039908918214598": (50.8, 10, NE + " (equirect centre column)"), "2038323030241374": (50.8, 8, NE),
    "601032566167537": (50.8, 8, NE), "1084209643308007": (50.8, 8, NE), "3679505618848403": (50.8, 10, NE),
    "567968164695691": (50.8, 10, "train-window frame at the NE track (GPS 0.1 m) showing the NE platform ramp/stair "
                                  "also seen in 1084209643308007; view perpendicular to the track looking NE"),
    "1989246731815003": (50.8, 15, "end-of-platform frame of the NE-platform series (compass 75 like 76-99 siblings); "
                                   "fence level across the frame, apartment row 225563354/225563355 beyond"),
    "848053946160154": (230.8, 8, SW), "733023784417474": (230.8, 8, SW), "141654295165313": (230.8, 10, SW),
    "618352366118078": (230.8, 10, "train-window close-up of the wall beside the SW track (NW-bound train, compass 322); "
                                   "nearest building 1507398030 lies SW of the camera; wall frontal -> looking SW"),
    "340145677446699": (230.8, 12, "train-window frame 2017-11-26 10:31 JST: catenary wires run level across the frame "
                                   "(view perpendicular to the track); scene backlit with the sun at az 164 ahead-left; "
                                   "rooftops of 1507398010-012 (260-285 from camera) in the right half -> looking SW; "
                                   "the light shaft at top is window glare"),
    "311685710531089": (230.0, 10, "looks along lane 264753665 (50/230) with the kerb and brown tile wall on the right; "
                                   "that wall is 225369229 whose centroid lies NW (282) of the camera -> looking SW"),
    "320855056383055": (75.0, 12, "upper-floor view, no compass: close gabled house on the right = 1507403650 (111, 4 m), "
                                  "white flat-roofed house left of centre = 1507403647 (53, 14 m), road 264753664 "
                                  "running away at the lower left (41) -> image centre ~75"),
}
UNALIGNED = {
    "1210686679751692": "camera points straight down at a manhole cover; no horizon, building or road edge in frame, "
                        "so no heading can be read from the image",
    "463520421371151": "close-up of the ジャバラ panel; the wooden railing behind it cannot be identified as the footbridge "
                       "railing or a bank fence (no water visible below it), so it gives no heading better than the compass",
}
d = json.loads(APP.read_text())
n = 0
for cid, (true, unc, basis) in TRUE.items():
    k = "mapillary_" + cid
    cur = d["panoramas"][k]
    assert cur.get("heading_offset_deg") is None, k
    off = ((true - float(pi[k].get("heading_deg") or 0.0) + 180.0) % 360.0) - 180.0
    cur.update({"heading_offset_deg": round(off, 1), "aligned": True, "alignment_uncertainty_deg": unc,
                "offset_basis": basis, "aligned_by": "main session hand alignment 2026-10-13"})
    n += 1
    print("%s compass %6.1f -> true %6.1f offset %+6.1f  +-%d" % (cid, float(pi[k].get("heading_deg") or 0), true, off, unc))
for cid, why in UNALIGNED.items():
    cur = d["panoramas"]["mapillary_" + cid]
    cur.update({"aligned": False, "unalignable_reason": why, "aligned_by": "main session hand review 2026-10-13"})
left = [k for k, v in d["panoramas"].items() if v.get("heading_offset_deg") is None]
print("aligned now", n, "| still without heading:", len(left), left)
if "--write" in sys.argv:
    APP.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n")
    print("written")
