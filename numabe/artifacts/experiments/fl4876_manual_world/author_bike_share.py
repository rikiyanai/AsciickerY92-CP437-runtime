"""author_bike_share.py [--write] -- transcription of the main session's hand decision (2026-10-16) for the Docomo
Bike Share port, OSM node/13726789786 (amenity=bicycle_rental, capacity 20, gbfs name "K1-210.東京都大田区田園調布本町19-2").

The build drew it as a generic shop front on building way/725138518 (no wall behind it, build_reg_75 probe). A bike
share port is a row of docked bicycles on the pavement, not a tenancy: compile_rules_resolved now keeps
amenity=bicycle_rental on the map only (BIKESHARE-MAP-01) and this fixture draws the dock.
Position: no panorama covers this block (Mapillary has no camera north of N 35.584235, survey record in the source
manifest); the OSM node sits at the centroid of the 33 m2 outline way/725138518, so the dock is placed 1.0 m outside
that outline's street face (face 2, outward bearing 125, the face the pipeline chose, road ~3.6 m away), centred on it,
uncertainty 5 m. Module: parked_bicycles, the catalogue's only bicycle module (a cluster, not a 20-bay rack).
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FX = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_scene_fixtures.json"
C = (379983.61, 3938756.14)          # way/725138518 centroid (footprint_edges.py, build_reg_83)
OUT = 4.58 / 2 + 1.0                 # half the outline depth across face 2, plus 1.0 m onto the pavement
B = math.radians(125.0)
E, N = round(C[0] + math.sin(B) * OUT, 2), round(C[1] + math.cos(B) * OUT, 2)
rec = {"fixture_id": "SF-4876-D001", "module_id": "parked_bicycles", "variant": None, "E": E, "N": N,
       "carrier_id": 725138518, "yaw_deg": 125.0, "position_uncertainty_m": 5.0,
       "sightings": [{"capture_id": "osm:node/13726789786", "what": "Docomo Bike Share port, capacity 20 (OSM)",
                      "beside": "street face of way/725138518"}],
       "manual_review": {"status": "accepted", "reviewed_by": "main session (OSM + common sense, 2026-10-16)",
                         "reviewed_on": "2026-10-16",
                         "module_basis": "bicycle_rental port = docked bicycles on the pavement; parked_bicycles is "
                                         "the only bicycle module",
                         "position_basis": "1.0 m outside face 2 (outward 125) of way/725138518, whose centroid "
                                           "holds the OSM node; no panorama covers the block",
                         "carrier_basis": "street face of way/725138518",
                         "orientation_basis": "bicycles face the street (125)"}}
fx = json.loads(FX.read_text())
fx["fixtures"] = [f for f in fx["fixtures"] if f["fixture_id"] != rec["fixture_id"]] + [rec]
print(rec["fixture_id"], E, N)
if "--write" in sys.argv:
    FX.write_text(json.dumps(fx, ensure_ascii=False, indent=1) + "\n")
    print("written")
