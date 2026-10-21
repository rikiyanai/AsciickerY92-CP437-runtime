"""merge_fixture_reviews.py [--write] -- transcribe the manual fixture reviews (assess/fixture_review_*.json)
into authored_scene_fixtures.json. Decides nothing: an accepted review replaces the projected module,
point, carrier, yaw and uncertainty with the reviewer's values and attaches manual_review; a rejected
review marks the fixture rejected (kept as evidence, never placed). Unreviewed fixtures stay pending."""
import glob, json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OWNER = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_scene_fixtures.json"
doc = json.loads(OWNER.read_text())
reviews = {}
for p in sorted(glob.glob(str(HERE / "assess" / "fixture_review_*.json"))):
    for fid, r in (json.loads(Path(p).read_text()).get("reviews") or {}).items():
        reviews[fid] = dict(r, _src=Path(p).stem)
n = {"accepted": 0, "rejected": 0, "pending": 0, "incomplete": 0}
for fx in doc["fixtures"]:
    r = reviews.get(fx["fixture_id"])
    if not r:
        n["pending"] += 1
        continue
    mr = dict(r.get("manual_review") or {})
    mr["status"] = r.get("status")
    mr["review_file"] = "artifacts/experiments/fl4876_manual_world/assess/%s.json" % r["_src"]
    if r.get("status") == "accepted":
        need = [k for k in ("module_id", "E", "N", "carrier_id", "yaw_deg", "position_uncertainty_m") if r.get(k) is None]
        if need:
            n["incomplete"] += 1
            mr["status"] = "incomplete"; mr["missing"] = need
            fx["manual_review"] = mr
            continue
        fx["projected"] = {k: fx.get(k) for k in ("module_id", "E", "N")}
        for k in ("module_id", "E", "N", "carrier_id", "yaw_deg", "position_uncertainty_m"):
            fx[k] = r[k]
        n["accepted"] += 1
    else:
        mr["reject_reason"] = r.get("reject_reason")
        n["rejected"] += 1
    fx["manual_review"] = mr
print(n)
if "--write" in sys.argv:
    OWNER.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    print("written", OWNER)
