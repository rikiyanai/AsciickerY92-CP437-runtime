"""repair_text_offsets.py [--write] -- data repair (decides nothing new): 19 panoramas in authored_building_appearance.json
carried their offset_basis TEXT in heading_offset_deg (an earlier, no longer present merge copied the basis field into
the offset field), so they counted as aligned without a usable heading (found 2026-10-13 by
attachment_review_batches.py). The 8 whose hand reviews (assess/group_05/06/07.json) hold a numeric heading_offset_deg
get that number back (each matches its basis text, e.g. 'at +19', 'offset about +30'); the other 11 never had one and
become heading_offset_deg null with the text kept under offset_note, so they rejoin the hand-alignment queue."""
import glob, json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
APP = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_building_appearance.json"
d = json.loads(APP.read_text()); P = d["panoramas"]
bad = [k for k, v in P.items() if isinstance(v.get("heading_offset_deg"), str)]
num = {}
for p in sorted(glob.glob(str(HERE / "assess" / "group_0*.json"))):
    def walk(o, src=Path(p).name):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in bad and isinstance(v, dict) and isinstance(v.get("heading_offset_deg"), (int, float)):
                    num.setdefault(k, (v["heading_offset_deg"], src))
                walk(v, src)
        elif isinstance(o, list):
            for x in o:
                walk(x, src)
    walk(json.loads(Path(p).read_text()))
for k in bad:
    v = P[k]; text = v["heading_offset_deg"]
    if k in num:
        v["heading_offset_deg"] = float(num[k][0])
        v["offset_repair_2026_10_13"] = "numeric offset restored from assess/%s (field held its basis text)" % num[k][1]
    else:
        v["heading_offset_deg"] = None; v["aligned"] = False; v["offset_note"] = text
        v["offset_repair_2026_10_13"] = "field held basis text and no numeric review exists: unaligned, queued for hand alignment"
    print(k, v["heading_offset_deg"])
print("restored", sum(1 for k in bad if k in num), "unaligned", sum(1 for k in bad if k not in num))
if "--write" in sys.argv:
    APP.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n"); print("written")
