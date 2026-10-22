#!/usr/bin/env python3
"""FL-4876. The builders below the pre-assembly gate carried undefined names.

Two of them, found only by forcing execution past the gate:

    compile_world_ir_classic_base.py:172   MARGIN_WORLD   (minimap markers)
    lower_plan_fixtures.py:257             _sanitize      (instance names)

Each appeared exactly once in its file -- at its use site, never at a
definition -- and each raised NameError the first time it was ever reached.
They survived because the gate refuses before compile_base runs, so this half
of the pipeline had never executed on this lane.

This test sweeps every stage below the gate for the same defect class, so the
next one is caught by a test rather than by a fifteen-minute production run.
"""

from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The stages the runner invokes after the pre-assembly gate.
BUILDERS = [
    "compile_world_ir_classic_base.py",
    "lower_plan_linear.py",
    "lower_plan_fixtures.py",
    "lower_plan_custom.py",
    "compile_facade_shell.py",
    "compile_world_ir_fixtures.py",   # owns place_fronts and infrastructure
    "compile_population_ground_plan.py",
    "compile_population_asset_plan.py",
    "osm_carto_stamper.py",
    "bake_osm_building_footprints.py",
    "compile_emission_manifest.py",
    "compile_object_metric_receipt.py",
    "compile_route_walkability.py",
]


def _undefined_globals(path: Path) -> set[str]:
    """Names read at module or function scope that the module never binds."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bound: set[str] = set(dir(builtins)) | {
        "__file__", "__name__", "__doc__", "__package__", "__spec__",
        "__loader__", "__builtins__",
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            bound.add(node.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda)):
            a = node.args
            for arg in (list(a.args) + list(a.kwonlyargs)
                        + list(getattr(a, "posonlyargs", []))):
                bound.add(arg.arg)
            if a.vararg:
                bound.add(a.vararg.arg)
            if a.kwarg:
                bound.add(a.kwarg.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, (ast.comprehension,)):
            pass
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.Global):
            bound.update(node.names)
        elif isinstance(node, (ast.withitem,)):
            pass
    read = {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return read - bound


@pytest.mark.parametrize("name", BUILDERS)
def test_builder_defines_every_global_name_it_reads(name):
    path = ROOT / "scripts" / "pipeline" / name
    if not path.is_file():
        pytest.skip(f"{name} absent")
    missing = _undefined_globals(path)
    assert not missing, f"{name} reads undefined name(s): {sorted(missing)}"


def test_the_three_known_offenders_stay_fixed():
    base = (ROOT / "scripts" / "pipeline"
            / "compile_world_ir_classic_base.py").read_text(encoding="utf-8")
    fix = (ROOT / "scripts" / "pipeline"
           / "lower_plan_fixtures.py").read_text(encoding="utf-8")
    assert not [ln for ln in base.splitlines()
                if "MARGIN_WORLD" in ln and not ln.lstrip().startswith("#")]
    assert "def _sanitize(" in fix
    # math is used at :116 and :119 in the spawn branch and was never imported
    assert "\nimport math\n" in base


def test_the_fixtures_sanitiser_matches_the_sibling_lowerer():
    """One rule, not a fourth variant of it."""
    from scripts.pipeline.lower_plan_custom import _sanitize as sibling
    from scripts.pipeline.lower_plan_fixtures import _sanitize as mine
    for probe in ("lower_fixtures_obs-inst-0110", "a b/c:d", "x" * 200, ""):
        assert mine(probe) == sibling(probe), probe
