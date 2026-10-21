"""restore_appearance_record.py FEATURE_ID NOTE [--write] -- put one building's appearance record back to its git HEAD
version (transcription of a hand correction; decides nothing) and append NOTE as a correction entry."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REL = "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_building_appearance.json"
fid, note = sys.argv[1], sys.argv[2]
head = json.loads(subprocess.run(["git", "show", "HEAD:" + REL], cwd=ROOT, capture_output=True, text=True,
                                 check=True).stdout)["buildings"][fid]
app = json.loads((ROOT / REL).read_text())
head = dict(head)
head.setdefault("corrections", []).append({"on": "2026-10-16", "note": note})
app["buildings"][fid] = head
print(fid, "restored:", head.get("what", "")[:120])
if "--write" in sys.argv:
    (ROOT / REL).write_text(json.dumps(app, ensure_ascii=False, indent=1) + "\n")
    print("written")
