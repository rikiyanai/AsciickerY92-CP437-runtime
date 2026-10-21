"""align_promenade_sequence.py [--write] -- transcription of the main session's hand alignment (2026-10-13) of the 11
promenade frames re-opened by repair_text_offsets.py: one 18 s walk (2022-10-29 15:27 JST) SE along the greenway
670892441 toward its SE end at the junction (379859.33, 3938483.73), channel on the right.
Method: in each frame the paved path runs straight to the junction, so the vanishing point (read by eye on the
seq*.png sheets, x as a fraction of the width) lies on the bearing from that camera to the path's SE end; the image
centre heading = that bearing - (vp - 0.5) * 65 (flat 1024x768 frames, hFOV ~65)."""
import json, math, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
APP = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_building_appearance.json"
pi = json.loads((HERE / "pano_index.json").read_text())["panoramas"]
END = (379859.331, 3938483.73)
VP = {  # capture: (vanishing-point x fraction, uncertainty deg, what was read)
    "5551690858229919": (0.50, 12, "junction + overpass straight ahead; white stair apartment 225367802 at the right edge"),
    "111837468275520": (0.68, 8, "path runs to the junction signals at x~700"),
    "127870530058098": (0.45, 8, "red-paved path converges just left of centre"),
    "680617530000398": (0.50, 8, "path converges at centre"),
    "142857205158097": (0.45, 8, "path converges just left of centre"),
    "565865518637783": (0.50, 8, "path and channel converge at centre"),
    "1251131275673158": (0.45, 8, "path converges just left of centre"),
    "1125370308099550": (0.40, 8, "path converges left of centre, channel + footbridge centre-right"),
    "811853376807095": (0.35, 12, "path runs up-left to the path-end barrier furniture"),
}
UNALIGNED = {
    "1021198522606682": "motion-blurred close-up of the rock garden; no path, horizon or building edge to read",
    "1097177034324958": "close-up whose earlier note says 'facing E toward the brick wall' while the frame shows the rock "
                        "garden; contradictory, no straight edge to read",
}
d = json.loads(APP.read_text()); P = d["panoramas"]
for cid, (vp, unc, what) in VP.items():
    k = "mapillary_" + cid; cam = pi[k]
    b_end = math.degrees(math.atan2(END[0] - cam["E"], END[1] - cam["N"])) % 360
    head = (b_end - (vp - 0.5) * 65) % 360
    off = ((head - float(cam["heading_deg"]) + 180) % 360) - 180
    P[k].update({"heading_offset_deg": round(off, 1), "aligned": True, "alignment_uncertainty_deg": unc,
                 "offset_basis": "%s; bearing to the promenade SE end %.1f, vp x %.2f -> centre %.1f" % (what, b_end, vp, head),
                 "aligned_by": "main session hand alignment 2026-10-13"})
    print(cid, "compass %.1f -> %.1f offset %+.1f" % (cam["heading_deg"], head, off))
for cid, why in UNALIGNED.items():
    P["mapillary_" + cid].update({"aligned": False, "unalignable_reason": why, "aligned_by": "main session hand review 2026-10-13"})
left = [k for k, v in P.items() if not isinstance(v.get("heading_offset_deg"), (int, float))]
print("panoramas without a numeric heading:", len(left), left)
if "--write" in sys.argv:
    APP.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n"); print("written")
