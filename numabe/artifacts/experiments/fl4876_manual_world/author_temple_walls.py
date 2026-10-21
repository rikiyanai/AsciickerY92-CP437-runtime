"""author_temple_walls.py [--write] -- transcription of the main session's hand decision (2026-10-16): the 有慶山東光院
precinct wall fixtures were drawn as grey block walls with a terracotta cap, but every sighting of them reads cream /
white plaster with blue glazed-tile coping (panoramas 489113339072184, 3921136788001424, 476931043387324 and the
pass-3/pass-4 sightings quoted in each record). They take the new concrete_block_wall variant
cream_plaster_wall_with_blue_tile_coping (population_asset_geometry.py). Position and run are unchanged."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FX = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_scene_fixtures.json"
IDS = ("SF-4876-S404-028", "SF-4876-S405-026", "SF-4876-S407-004", "SF-4876-S407-013", "SF-4876-S407-014")
NEW = "cream_plaster_wall_with_blue_tile_coping"
fx = json.loads(FX.read_text())
for f in fx["fixtures"]:
    if f["fixture_id"] in IDS and f.get("variant") != NEW:
        f.setdefault("parent_corrections", []).append({
            "on": time.strftime("%Y-%m-%d"), "field": "variant", "from": f.get("variant"), "to": NEW,
            "reason": "sightings read cream/white plaster with blue tile coping: " + "; ".join(
                s.get("what", "")[:80] for s in f.get("sightings", [])[:2])})
        f["variant"] = NEW
        print(f["fixture_id"], "->", NEW)
if "--write" in sys.argv:
    FX.write_text(json.dumps(fx, ensure_ascii=False, indent=1) + "\n")
    print("written")
