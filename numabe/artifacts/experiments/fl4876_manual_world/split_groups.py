"""split_groups.py N -- list-only: split the indexed panoramas into N contiguous work areas (sorted
along the larger spread axis, then cut into equal runs). Writes assess/groups.json. Decides nothing
about any building or object."""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
n = int(sys.argv[1])
p = json.loads((HERE / "pano_index.json").read_text())["panoramas"]
items = sorted(p.items(), key=lambda kv: (kv[1]["E"] + kv[1]["N"]))
size = -(-len(items) // n)
groups = {"group_%02d" % g: [k for k, _ in items[g * size:(g + 1) * size]] for g in range(n)}
(HERE / "assess").mkdir(exist_ok=True)
(HERE / "assess" / "groups.json").write_text(json.dumps(groups, indent=1))
for g, ids in groups.items():
    es = [p[i]["E"] for i in ids]; ns = [p[i]["N"] for i in ids]
    print(g, len(ids), "E %.0f-%.0f N %.0f-%.0f" % (min(es), max(es), min(ns), max(ns)))
