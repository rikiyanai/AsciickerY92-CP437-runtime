"""split_pano_s3.py -- list-only: the panoramas whose record in authored_building_appearance.json has no
hand-aligned heading or no visible-object list, split into 3 review batches (assess/pano_s3_batches.json).
Decides nothing about any panorama."""
import json
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
d = json.loads((ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/"
                       "authored_building_appearance.json").read_text())["panoramas"]
todo = sorted(k for k, v in d.items() if v.get("heading_offset_deg") is None or not v.get("visible_objects"))
b = {"pano_s3_%02d" % i: todo[i::3] for i in range(3)}
(HERE / "assess" / "pano_s3_batches.json").write_text(json.dumps(b, indent=1))
print({k: len(v) for k, v in b.items()})
