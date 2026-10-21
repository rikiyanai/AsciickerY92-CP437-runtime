"""pano_s4_dup_list.py [FROM] [N] -- list-only: the duplicate candidates merge_pano_s4.py prints, with both records'
description, captures and position basis, for a hand decision (drop / alias / keep both)."""
import glob
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
B = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
fx = {f["fixture_id"]: f for f in json.loads((B / "authored_scene_fixtures.json").read_text())["fixtures"]}
new = {}
for p in sorted(glob.glob(str(HERE / "assess" / "pano_s4_[0-9][0-9].json"))):
    for cid, r in json.loads(Path(p).read_text())["panoramas"].items():
        for o in r.get("objects") or []:
            if o.get("verdict") == "MISSING" and o.get("record"):
                new[o["record"]["fixture_id"]] = o["record"]
acc = [f for f in fx.values() if (f.get("manual_review") or {}).get("status") == "accepted"]
pairs = []
ids = sorted(new)
for i, a in enumerate(ids):
    for b in ids[i + 1:]:
        if new[a]["module_id"] == new[b]["module_id"]:
            d = math.hypot(new[a]["E"] - new[b]["E"], new[a]["N"] - new[b]["N"])
            if d <= 4:
                pairs.append((a, b, d))
    for f in acc:
        if f.get("module_id") == new[a]["module_id"]:
            d = math.hypot(f["E"] - new[a]["E"], f["N"] - new[a]["N"])
            if d <= 4:
                pairs.append((a, f["fixture_id"], d))


def desc(fid):
    r = new.get(fid) or fx.get(fid)
    s = r.get("sightings") or []
    caps = sorted({x.get("capture_id", "")[-8:] for x in s})
    what = "; ".join(sorted({(x.get("what") or "")[:60] for x in s}))[:150]
    return "%s v=%s caps=%s | %s | pos: %s" % (fid, r.get("variant"), ",".join(caps), what,
                                                ((r.get("manual_review") or {}).get("position_basis") or "")[:110])


start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
n = int(sys.argv[2]) if len(sys.argv) > 2 else 40
for k, (a, b, d) in enumerate(pairs[start:start + n], start):
    print("#%d %s %.1f m" % (k, new[a]["module_id"], d))
    print("   A", desc(a))
    print("   B", desc(b))
print("total", len(pairs))
