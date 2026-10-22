"""list_railing_buildings.py BUILD_DIR -- listing aid (decides nothing): buildings whose hand record mentions balconies,
railings, balustrades, grilles or handrails, whether the build realized them as a mesh mass, and the matching features."""
import glob, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
bl = json.loads((ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_building_appearance.json").read_text())["buildings"]
mesh = {Path(p).stem[len("mass_"):] for p in glob.glob(str(Path(sys.argv[1]) / "meshes" / "mass_*.akm"))}
T = ("railing", "rails", "balustrade", "handrail", "grille", "balcon")
rows = []
for k, v in bl.items():
    feats = [x for x in (v.get("features") or []) if any(t in str(x).lower() for t in T)]
    if feats:
        src = k.split(":", 2)[-1]
        rows.append((src.split("/")[0] + "_" + src.split("/")[1] in mesh, src, feats))
print(len(rows), "mesh masses", sum(r[0] for r in rows))
for r in rows:
    print("mesh" if r[0] else "terr", r[1], "|", "; ".join(r[2])[:200])
