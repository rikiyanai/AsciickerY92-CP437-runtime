"""FL-4876 TRACKSIDE-01 / REFILE-01 guards.

The planner logic lives inside compile_population_plan's main closure, so its
behaviour is evidenced by the rebuild census
(artifacts/experiments/fl4876_refile/refile_invariants.json). These guards keep
the trackside module list from silently going stale and keep the two rules
wired where the census measured them.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLANNER = ROOT / "scripts/pipeline/compile_population_plan.py"
CATALOG = (ROOT / "artifacts/experiments/fl4813_capture_review_20260915/preserved/"
           "numabe_full_727_mapping_20260915/generic_module_catalog.json")


def _trackside_set() -> set[str]:
    tree = ast.parse(PLANNER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "_TRACKSIDE_MODULES"
                                                 for t in node.targets)):
            call = node.value
            return {elt.value for elt in call.args[0].elts}
    raise AssertionError("_TRACKSIDE_MODULES not found")


def test_every_trackside_module_exists_in_the_catalog():
    doc = json.loads(CATALOG.read_text(encoding="utf-8"))
    ids = {m["module_id"] for m in (doc.get("modules") or doc)}
    missing = _trackside_set() - ids
    assert not missing, f"trackside modules absent from the catalog: {sorted(missing)}"


def test_level_crossing_equipment_is_not_forced_onto_rail():
    assert "level_crossing_equipment" not in _trackside_set()


def test_every_freestanding_attachment_passes_its_module():
    tree = ast.parse(PLANNER.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_freestanding_rec"]
    assert len(calls) >= 4
    assert all(any(k.arg == "_module" for k in c.keywords) for c in calls)


def test_refile_excludes_poles_and_reports_moves():
    src = PLANNER.read_text(encoding="utf-8")
    i = src.index("FL-4876 REFILE-01")
    block = src[i:i + 4000]
    assert '_fpl.get("module_id") != _POLE_MOD' in block
    assert '"refiled_to_attachment_carrier"' in src
    assert '"registration_filing"' in block
