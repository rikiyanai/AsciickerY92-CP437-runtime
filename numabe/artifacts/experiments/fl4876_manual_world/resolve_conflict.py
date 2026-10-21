"""resolve_conflict.py FEATURE_ID JSON_PATCH -- write the person's decision for one building into the
appearance owner (fields from the JSON patch replace the entry's fields; 'wall_conflict' is cleared
and the decision is logged under 'conflict_resolution')."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
OWNER = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_building_appearance.json"
fid, patch = sys.argv[1], json.loads(sys.argv[2])
doc = json.loads(OWNER.read_text())
b = doc["buildings"][fid]
prev = b.pop("wall_conflict", None)
b.update({k: v for k, v in patch.items() if k != "basis"})
b["conflict_resolution"] = {"decided_by": "agent:claude-opus-5.5 session 0e4cdb7d, by eye", "decided_on": "2026-10-12",
                            "basis": patch.get("basis"), "conflicting_records": prev}
OWNER.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
print("resolved", fid, {k: b.get(k) for k in ("wall_rgb", "storeys", "ground_floor")})
