#!/usr/bin/env python3
"""FL-4809 R8: the plan decides which buildings are geometry, bake obeys.

A terrain lattice extrusion has no vertical face, so it cannot carry a window
bay, an awning, a shutter, plus no sign band.  The declared target forbids
exactly that at a street-facing facade -- "No terrain interpolation sheets may
cross street-facing facades" -- yet every one of the 733 buildings took the
lattice, because the mesh path was reachable only as a fallback for footprints
too small to hold a height sample.

WHAT THIS FILE USED TO DO, AND WHY IT WAS REPLACED
==================================================
RETRACTED -- the previous suite had six tests and five of them never touched
the module.  A helper named ``_wants_mesh_for`` rebuilt the promotion predicate
inside the test file and asserted against the rebuild, with a docstring saying
so: "rather than reach into it the test rebuilds it from the same inputs bake()
uses".  Deleting ``_wants_mesh`` from the module entirely would have left five
of the six passing.  That is the defect
``test_fl4809_plan_importance_source`` already names for the planner: testing a
copy proves only that the copy is self-consistent.

The sixth test asserted ``route_importance`` was a parameter of ``bake()``,
which pinned the authority inversion in place.

WHAT IS PINNED NOW
==================
The decision and its consumer, each through its real owner:

* the planner writes ``mass_realization`` on the terrain_mass row it already
  owns, from importance, which it already owns;
* with no route-importance receipt every building stays a lattice, so the
  behaviour before this rule existed is preserved exactly;
* the realization does not disturb the reconstruction class, which is a
  separate axis and measured to stay A 650 / B 83 either way;
* ``bake()`` carries no route-importance parameter at all, and reads the plan;
* the join is ``spec["feature_id"]``, which is total against the plan with no
  normalisation -- the old code matched a stripped feature id against
  ``spec["source"]`` and needed a second "plateau/" spelling to cover the 83
  PLATEAU-only buildings, compensating for a mismatch that does not exist.
"""

from __future__ import annotations

import collections
import inspect
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.pipeline.bake_osm_building_footprints as BAKE  # noqa: E402
from scripts.pipeline.compile_representation_plan import (  # noqa: E402
    MASS_REALIZATIONS,
    MESH_SHELL_IMPORTANCE,
    build,
)

LANE = REPO_ROOT / ".run/numabe_connected/build_planning_arch09"
BUNDLE = (REPO_ROOT
          / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915")
CATALOG = (REPO_ROOT / "artifacts/experiments/fl4813_capture_review_20260915"
           / "preserved/numabe_full_727_mapping_20260915"
           / "generic_module_catalog.json")
PACK = REPO_ROOT / "assets/world_templates/japan_urban/pack.json"
FUSION = REPO_ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/fusion_reviews.json"
ROUTE_IMPORTANCE = (REPO_ROOT / "artifacts/experiments/fl4809_route_importance"
                    / "preserved/5a1feab7a462a28d.route_importance.json")
SPECS = LANE / "building_instances.json"

_LANE_INPUTS = (
    LANE / "canonical.world_ir.json", LANE / "canonical_entities.json",
    LANE / "observation_debt.json", LANE / "osm_ingest_receipt.json",
    LANE / "plan/rules_resolved.json", LANE / "osm.world_ir.json",
    LANE / "plan/jev_decisions.json", LANE / "terrain_metadata.json",
    BUNDLE / "valhalla_pedestrian_route.json", CATALOG, PACK, FUSION,
    ROUTE_IMPORTANCE, SPECS)

requires_lane = pytest.mark.skipif(
    not all(p.is_file() for p in _LANE_INPUTS),
    reason="lane planning artifacts absent (.run is not tracked)")


def _plan(route_importance):
    return build(
        LANE / "canonical.world_ir.json", LANE / "canonical_entities.json",
        LANE / "observation_debt.json", LANE / "osm_ingest_receipt.json",
        LANE / "plan/rules_resolved.json", LANE / "osm.world_ir.json",
        CATALOG, PACK,
        jev_path=LANE / "plan/jev_decisions.json",
        route_path=BUNDLE / "valhalla_pedestrian_route.json",
        fusion_path=FUSION,
        terrain_metadata_path=LANE / "terrain_metadata.json",
        route_importance_path=route_importance,
        authored_waivers_path=None)


@pytest.fixture(scope="module")
def routed():
    return _plan(ROUTE_IMPORTANCE)


@pytest.fixture(scope="module")
def unrouted():
    return _plan(None)


def _masses(plan):
    return [r for r in plan["rows"]
            if r.get("primary") and r["disposition"] == "terrain_mass"]


# --------------------------------------------------------------------------
# the vocabulary
# --------------------------------------------------------------------------

def test_the_realization_vocabulary_is_closed():
    """Two realizations, named. A third would need a decision, not a string."""
    assert MASS_REALIZATIONS == ("terrain_lattice", "mesh_shell")


def test_only_a_walked_past_building_earns_geometry():
    """background is absent on purpose: a lattice is fine where nobody looks."""
    assert MESH_SHELL_IMPORTANCE == (
        "route_visible", "landmark", "gameplay_critical")
    assert "background" not in MESH_SHELL_IMPORTANCE


# --------------------------------------------------------------------------
# bake no longer holds the decision
# --------------------------------------------------------------------------

def test_bake_cannot_read_route_importance():
    """The authority inversion, pinned out of existence.

    bake() used to take a route_importance path, parse the receipt itself, and
    promote buildings the plan had recorded as terrain_mass. The plan said
    terrain_mass for all 733 while this stage promoted 322 of them, so the plan
    stopped being the record of what the world contains. The parameter must not
    come back.
    """
    params = inspect.signature(BAKE.bake).parameters
    assert "route_importance" not in params


def test_bake_reads_the_plan_instead():
    """And it is optional, so a caller that passes nothing bakes lattices."""
    params = inspect.signature(BAKE.bake).parameters
    assert "representation_plan" in params
    assert params["representation_plan"].default is None


def test_the_bake_module_does_not_parse_importance_itself():
    """No second reader of the receipt, and no second copy of the tier list.

    A grep-level guard rather than a behavioural one, because the failure it
    catches is someone reintroducing the shortcut, which behaves correctly
    right up until the plan and the bake disagree.
    """
    source = Path(BAKE.__file__).read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith("#"))
    assert "route_visible" not in code
    assert "gameplay_critical" not in code
    assert "load_route_importance" not in code


# --------------------------------------------------------------------------
# the planner owns it, measured on the real lane
# --------------------------------------------------------------------------

@requires_lane
def test_without_a_route_receipt_every_building_stays_a_lattice(unrouted):
    """The behaviour before this rule existed, preserved exactly.

    Importance falls to background for all 733 because buildings are triage
    group A and out of debt scope, so nothing is promoted and an existing
    caller cannot be changed by accident.
    """
    masses = _masses(unrouted[0])
    assert len(masses) == 733
    assert {r["mass_realization"] for r in masses} == {"terrain_lattice"}
    assert {r["importance"] for r in masses} == {"background"}


@requires_lane
def test_the_route_receipt_promotes_the_walked_past_buildings(routed):
    """322 of 733, the same count the route receipt scores route_visible."""
    masses = _masses(routed[0])
    got = collections.Counter(r["mass_realization"] for r in masses)
    assert got == {"mesh_shell": 322, "terrain_lattice": 411}
    for row in masses:
        want = ("mesh_shell" if row["importance"] in MESH_SHELL_IMPORTANCE
                else "terrain_lattice")
        assert row["mass_realization"] == want, row["feature_id"]


@requires_lane
def test_the_realization_does_not_move_the_reconstruction_class(
        routed, unrouted):
    """Two separate axes, and the receipt must not quietly fuse them.

    Level of information need is not the same question as how much evidence a
    reconstruction rests on. If promoting a building also moved its class, the
    plan would be answering one question with the other.
    """
    def split(plan):
        return collections.Counter(
            r["reconstruction_class"] for r in _masses(plan))
    assert split(routed[0]) == split(unrouted[0]) == {"A": 650, "B": 83}


@requires_lane
def test_the_decision_is_auditable_in_the_receipt(routed):
    """A reader can ask how many buildings were promoted and by which rule."""
    applied = routed[1]["rules_applied"]
    assert applied["observed_defaults:mass_class:mesh_shell"] == 322
    assert applied["observed_defaults:mass_class"] == 411


@requires_lane
def test_a_promoted_row_says_why_in_prose(routed):
    """The reason names the missing vertical face, not merely a tier."""
    row = next(r for r in _masses(routed[0])
               if r["mass_realization"] == "mesh_shell")
    assert row["reason"].startswith("R-MASS-SHELL:")
    assert "vertical face" in row["reason"]
    assert row["owner_stage"] == "bake"
    assert row["disposition"] == "terrain_mass"


# --------------------------------------------------------------------------
# the join bake performs
# --------------------------------------------------------------------------

@requires_lane
def test_the_plan_joins_the_bake_specs_exactly(routed):
    """feature_id is total both ways, so no building is scored by silence.

    The retracted code joined a stripped feature id against spec["source"] and
    needed a second "plateau/" spelling for the 83 PLATEAU-only buildings. A
    miss there scored a building background without saying so. The field beside
    it already carries the plan's exact spelling.
    """
    specs = json.loads(SPECS.read_text(encoding="utf-8"))
    spec_ids = {s["feature_id"] for s in specs}
    masses = {r["feature_id"] for r in _masses(routed[0])}
    assert masses == spec_ids
    assert len(spec_ids) == len(specs) == 733
    shell = {r["feature_id"] for r in _masses(routed[0])
             if r["mass_realization"] == "mesh_shell"}
    assert len(shell & spec_ids) == len(shell) == 322
