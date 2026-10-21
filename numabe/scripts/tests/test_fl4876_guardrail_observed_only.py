#!/usr/bin/env python3
"""FL-4876 GUARDRAIL-OBSERVED-01. Pedestrian guardrails come only from photos and hand-authored fixtures.

build_reg_75 drew 544 rule-made guardrail runs (two road-edge families, twice per span) against ~14 observed ones;
the operator saw handrails everywhere, including in front of home. The rule-family loop lives inside the planner's
main function, so these tests pin the contract at source level; the build-level count is the acceptance check
(rule guardrail runs in lower_linear_instances.json must be 0).
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline import compile_population_plan as P  # noqa: E402


def test_both_pedestrian_guardrails_are_observed_only():
    assert {"pedestrian_guardrail_continuous",
            "pedestrian_guardrail_vertical_bar_continuous"} <= P.OBSERVED_ONLY_MODULES


def test_curbs_paving_and_rail_fences_stay_rule_made():
    # the exemption for continuous road edges is kept for everything that is not a guardrail
    for mod in ("curb_edge_line", "sidewalk_paver_surface", "rail_corridor_fence", "concrete_retaining_edge"):
        assert mod not in P.OBSERVED_ONLY_MODULES


def test_skip_comes_before_any_rule_emission():
    src = inspect.getsource(P)
    skip = src.index("if mod in OBSERVED_ONLY_MODULES:")
    basis = src.index('if src == "DEFAULT:not_designated" and not is_continuous(mod):')
    assert skip < basis, "observed-only skip must run before the continuous-module basis exemption"
    assert '"reason": "observed_only_module"' in src[skip:basis]
