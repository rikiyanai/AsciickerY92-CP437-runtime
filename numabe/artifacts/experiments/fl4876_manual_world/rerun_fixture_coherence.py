"""rerun_fixture_coherence.py RUN_ROOT -- re-lower the fixture plan of a finished build and re-run its spatial-
coherence check in place (minutes, not a 25-minute world build). FL-4876: used to test a lowering fix against the
exact build that failed (build_reg_90). Same calls and arguments as scripts/sbu_e2e_run.py."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
from scripts.pipeline.lower_plan_fixtures import lower_population_plan as lower_fixtures  # noqa: E402
from scripts.pipeline.compile_spatial_coherence_receipt import main as run_spatial_coherence  # noqa: E402
import json  # noqa: E402

import importlib.util  # noqa: E402

run = Path(sys.argv[1]).resolve()
B = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
meta = json.loads((run / "terrain_metadata.json").read_text())
# the lowerer appends its instances to the map it is given; give it the build's map minus its own earlier instances
spec = importlib.util.spec_from_file_location("a3d_edit", Path(__file__).with_name("a3d_edit.py"))
ED = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ED)
prefix, version, recs, tail = ED.parse((run / "output.a3d").read_bytes())
recs = [r for r in recs if not (r["kind"] == "mesh" and r["inst"].startswith("lower_fixtures_"))]
work = run / "rerun_fixture_coherence.a3d"
work.write_bytes(ED.serialise(prefix, version, recs, tail))
lower_fixtures(plan_path=run / "plan/population_grounded/population_plan.fixtures.json",
               asset_plan_path=run / "plan/population_asset_plan.json",
               world_ir_path=run / "canonical.world_ir.json",
               terrain_metadata_path=run / "terrain_metadata.json",
               map_path=work, meshes_out=run / "meshes", output_root=run,
               scale=float(meta["content_scale"]))
argv = ["--route", str(B / "valhalla_pedestrian_route.json"), "--world-ir", str(run / "canonical.world_ir.json"),
        "--terrain-metadata", str(run / "terrain_metadata.json"),
        "--plan", str(run / "plan/representation_plan.json"), "--build-dir", str(run),
        "--out", str(run / "spatial_coherence.json"), "--receipt-out", str(run / "spatial_coherence_receipt.json"),
        "--jev-input-out", str(run / "spatial_coherence_jev_input.json"),
        "--generated-at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "--authored-spatial-decisions", str(B / "authored_spatial_coherence_decisions.json"),
        "--rules", str(run / "plan/rules_resolved.json")]
sys.exit(run_spatial_coherence(argv))
