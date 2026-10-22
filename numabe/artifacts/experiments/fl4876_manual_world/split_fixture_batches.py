"""split_fixture_batches.py N -- list-only: split the authored scene fixture ids into N review batches
(assess/fixture_batches.json). Decides nothing about any fixture."""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
n = int(sys.argv[1])
f = json.loads((ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/"
                       "authored_scene_fixtures.json").read_text())["fixtures"]
ids = [x["fixture_id"] for x in sorted(f, key=lambda x: (round(x["E"] / 40), round(x["N"] / 40)))]
k = -(-len(ids) // n)
b = {"fixture_batch_%02d" % i: ids[i * k:(i + 1) * k] for i in range(n)}
(HERE / "assess" / "fixture_batches.json").write_text(json.dumps(b, indent=1))
print(len(ids), {g: len(v) for g, v in b.items()})
