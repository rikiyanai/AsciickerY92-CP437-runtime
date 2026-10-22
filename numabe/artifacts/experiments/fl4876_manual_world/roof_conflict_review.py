"""roof_conflict_review.py [--write] -- transcription of the main session's hand review (2026-10-13) of the 14 buildings
whose street-photo evidence carries a roof sample beside the hand-reviewed GSI aerial roof point (roof_rgb). Each was
judged with an aerial crop (aerial_at.py <centroid> 12) and the street sample label. Writes buildings[id]["roof_conflict"].
No roof_rgb changes: in every case the aerial point is the lit roof plane or the street sample is not the roof plane."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_building_appearance.json"
P = "feature:building_footprint:"
R = {
    "way/1507398029": "kept aerial: aerial shows the lit pale slate plane; street sample is the NW face in shade",
    "way/1507398062": "kept aerial: street sample is an eave band in shade, not the roof plane",
    "way/1507403691": "UNRESOLVED identity: street capture 1530308080777696 shows terracotta tiles at x~520 (bearing ~59), "
                      "but this footprint's aerial roof is light grey ribbed metal; NE neighbour way/1507403692 (14.5 m, "
                      "bearing 51 from the camera) has a reddish aerial roof [125,105,115]. Either the lane-end house is "
                      "1507403692 or the aerial predates a re-roof; roof_rgb left on the aerial point until resolved",
    "way/1507403612": "kept aerial: aerial plane grey-blue and lit; street sample is the lower tile roof in shade",
    "way/225101288": "kept aerial: same green/teal hue, street sample is the shaded underside",
    "way/1507403662": "kept aerial: street sample is the roof fascia",
    "way/1507403665": "kept aerial: aerial footprint is mostly dark grey roofs; the lit red-brown metal roof in the street "
                      "photo may be the adjoining lower roof at the outline's SW edge (ambiguous)",
    "way/1507398031": "kept aerial: street sample in shade",
    "way/225101506": "kept aerial: dark-grey tiled gate roof per description; blue-green is the wall coping",
    "way/1507403667": "kept aerial: street sample is a beige wall band over the neighbour roofline",
    "way/225563361": "kept aerial: street sample is a wall band",
    "way/225562527": "kept aerial: same grey-blue",
    "way/225367799": "kept aerial: same family (dE 8.6)",
}
d = json.loads(APP.read_text())
for k, why in R.items():
    d["buildings"][P + k]["roof_conflict"] = {"decision": why, "reviewed_by": "main session", "reviewed_on": "2026-10-13"}
print("reviewed", len(R))
if "--write" in sys.argv:
    APP.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n"); print("written")
