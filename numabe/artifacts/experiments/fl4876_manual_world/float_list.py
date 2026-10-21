#!/usr/bin/env python3
"""float_list.py BUILD_DIR [MIN_M] -- FL-4876 listing aid (measures, decides nothing).

Every mesh instance of a built (or hand-edited) map whose origin z stands more than MIN_M (default 0.5) above, or
below, the terrain under its origin. Found 2026-10-16: the "handrail" in front of the operator's home was four flat
sidewalk/curb strips floating ~1.3 m above the lane. Terrain from scripts/pipeline/extruded_wall.Terrain on the same
output.a3d; height units per metre = content_scale * 16. Facade-mounted objects (signs, awnings) legitimately stand
above the ground and are listed with their module so a person can tell them apart.
"""
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT)]
from scripts.pipeline.extruded_wall import Terrain  # noqa: E402

B = Path(sys.argv[1]).resolve()
MIN_M = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5
spec = importlib.util.spec_from_file_location("ed", Path(__file__).with_name("a3d_edit.py"))
ED = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ED)
meta = ED.meta_for(B / "output.a3d")
zpm = meta["cs"] * 16.0
T = Terrain(B / "output.a3d")
_, _, recs, _ = ED.parse((B / "output.a3d").read_bytes())
rows = []
for r in recs:
    if r["kind"] != "mesh" or r["inst"].startswith("mass_") or "terrain" in r["inst"]:
        continue
    x, y, z = r["tf"][12:15]
    h = T.height(x, y)
    if h is None:
        continue
    d = (z - h) / zpm
    if abs(d) > MIN_M:
        e, n = ED.to_en(meta, x, y)
        rows.append({"inst": r["inst"], "mesh": r["mesh"], "E": round(e, 2), "N": round(n, 2), "above_m": round(d, 2)})
kind = Counter(x["inst"].split("_")[0] + ("_" + x["inst"].split("_")[1] if "_" in x["inst"] else "") for x in rows)
print(len(rows), "instances off the terrain by more than", MIN_M, "m; by prefix:", kind.most_common(12))
lin = [x for x in rows if x["inst"].startswith("linear_")]
print(len(lin), "linear instances; worst:")
for x in sorted(lin, key=lambda x: -abs(x["above_m"]))[:25]:
    print("  %-70s %+6.2f m  E%.1f N%.1f" % (x["inst"][:70], x["above_m"], x["E"], x["N"]))
if len(sys.argv) > 3:
    Path(sys.argv[3]).write_text(json.dumps(rows, indent=1) + "\n")
