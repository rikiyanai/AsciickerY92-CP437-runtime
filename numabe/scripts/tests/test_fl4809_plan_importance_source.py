#!/usr/bin/env python3
"""FL-4809 R1b: the representation plan takes importance from the route.

``importance`` {background, route_visible, landmark, gameplay_critical} is a
level-of-information-need statement.  It was a field of the observation-debt
row; debt scopes itself to the 17 B/C triage features; triage assigns group A
to every class that is not ``place`` and not ``fixture``.  So all 733 buildings
resolved to the ``background`` default.

An earlier revision of this file tested a COPY of the planner's resolution
order written inside the test, which proves only that the copy is
self-consistent.  These tests call the shipped functions
``compile_representation_plan.resolve_importance`` and
``debt_policy_key_for``, and, when the lane artifacts are present, the real
``build()``.

What is pinned here:

* the resolution order, on the production function;
* the no-receipt path, which must remain the pre-R1 behaviour exactly;
* the consumer's refusal to read an untrustworthy receipt -- wrong schema,
  duplicate feature id, unknown tier, incomplete canonical coverage -- because
  every one of those degrades to a silent ``background``, which is the answer
  this whole stage exists to stop being the default;
* the ``route_visible_C`` hazard, on the production function rather than a
  restatement of it;
* the receipt provenance pin, so an override plan compiled WITH route
  importance cannot be replayed into a run without it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.compile_representation_plan import (  # noqa: E402
    debt_policy_key_for,
    resolve_importance,
)
from scripts.pipeline.compile_route_importance import (  # noqa: E402
    SCHEMA,
    RouteImportanceInvalid,
    load_route_importance,
)


def _receipt(tmp_path: Path, rows, schema=SCHEMA) -> Path:
    path = tmp_path / "route_importance.json"
    path.write_text(json.dumps({"schema": schema, "rows": rows}),
                    encoding="utf-8")
    return path


def _row(fid, importance):
    return {"feature_id": fid, "importance": importance}


# --------------------------------------------------------------------------
# resolution order, on the shipped function
# --------------------------------------------------------------------------

def test_absent_receipt_keeps_the_debt_row_answer():
    """An empty table is what an absent receipt produces; behaviour is the
    pre-R1 behaviour, not an approximation of it."""
    debt = {"feature:place:node/1": {"importance": "landmark"}}
    assert resolve_importance({}, debt, "feature:place:node/1") == "landmark"


def test_absent_receipt_and_absent_debt_row_is_background():
    assert resolve_importance({}, {}, "feature:building_footprint:way/1") \
        == "background"


def test_receipt_supplies_importance_for_a_building():
    """The case that was structurally impossible: a building is not background.

    No building has a debt row, so before the receipt existed this feature
    resolved to the default and every importance-driven rule saw background.
    """
    fid = "feature:building_footprint:way/225198068"
    assert resolve_importance({fid: "route_visible"}, {}, fid) \
        == "route_visible"


def test_receipt_wins_over_the_debt_row():
    """One owner answers. The receipt is computed for every feature."""
    fid = "feature:place:node/1"
    table = {fid: "route_visible"}
    debt = {fid: {"importance": "background"}}
    assert resolve_importance(table, debt, fid) == "route_visible"


# --------------------------------------------------------------------------
# the consumer refuses a receipt it cannot trust
# --------------------------------------------------------------------------

def test_wrong_schema_is_refused(tmp_path):
    path = _receipt(tmp_path, [_row("feature:a", "route_visible")],
                    schema="y92.route_importance.v2")
    with pytest.raises(RouteImportanceInvalid, match="schema"):
        load_route_importance(path)


def test_duplicate_feature_id_is_refused(tmp_path):
    """Keying by feature id cannot say which of two rows won."""
    path = _receipt(tmp_path, [_row("feature:a", "route_visible"),
                               _row("feature:a", "background")])
    with pytest.raises(RouteImportanceInvalid, match="more than once"):
        load_route_importance(path)


def test_unknown_importance_tier_is_refused(tmp_path):
    """An unrecognised tier would select the blocking policy in silence.

    debt_policy_key_for treats everything that is not "background" as
    promoted, so a typo would route the feature to route_visible_C rather
    than fail.
    """
    path = _receipt(tmp_path, [_row("feature:a", "rout_visible")])
    with pytest.raises(RouteImportanceInvalid, match="not one of"):
        load_route_importance(path)


def test_incomplete_canonical_coverage_is_refused(tmp_path):
    """A receipt from a different canonical IR answers background by omission.

    The content-hash pin cannot catch this on its own: a caller that does not
    pin route_importance in verify() never compares it. Coverage is checked
    against the consumer's own feature set instead.
    """
    path = _receipt(tmp_path, [_row("feature:a", "route_visible")])
    with pytest.raises(RouteImportanceInvalid, match="unscored"):
        load_route_importance(path,
                              required_feature_ids=["feature:a", "feature:b"])


def test_unexpected_feature_id_is_refused(tmp_path):
    """Extra ids prove a different denominator, so the overlap is untrusted.

    Nothing reads the extra row, so this is not a silent-background failure
    like the missing direction. It is a provenance failure: a receipt that
    scores features this run does not carry was compiled against a different
    canonical IR, and the scores this run DOES read were therefore measured
    in a world it is not reproducing.
    """
    path = _receipt(tmp_path, [_row("feature:a", "route_visible"),
                               _row("feature:b", "background")])
    with pytest.raises(RouteImportanceInvalid, match="unexpected"):
        load_route_importance(path, required_feature_ids=["feature:a"])


def test_both_coverage_directions_are_named_at_once(tmp_path):
    """A receipt that both misses and adds ids reports both, not the first."""
    path = _receipt(tmp_path, [_row("feature:a", "route_visible")])
    with pytest.raises(RouteImportanceInvalid) as exc:
        load_route_importance(path,
                              required_feature_ids=["feature:b"])
    message = str(exc.value)
    assert "unscored" in message and "unexpected" in message, message


def test_complete_coverage_loads(tmp_path):
    path = _receipt(tmp_path, [_row("feature:a", "route_visible"),
                               _row("feature:b", "background")])
    table = load_route_importance(
        path, required_feature_ids=["feature:a", "feature:b"])
    assert table == {"feature:a": "route_visible", "feature:b": "background"}


def test_empty_receipt_is_refused(tmp_path):
    path = _receipt(tmp_path, [])
    with pytest.raises(RouteImportanceInvalid, match="no rows"):
        load_route_importance(path)


# --------------------------------------------------------------------------
# the hazard, on the shipped function
# --------------------------------------------------------------------------

def test_route_visible_group_a_selects_the_blocking_debt_policy():
    """HAZARD for whoever routes buildings through the debt policy.

    There is no ``route_visible_A``. Every building is triage group A, so a
    building promoted to ``route_visible`` does not select ``background_A``
    and is not group B either: it falls to ``route_visible_C``, whose policy
    is "observation_owed blocks evidence_complete until observation, authored
    waiver, or explicit synthetic proxy". compile_preassembly_candidate
    refuses a run on ``evidence_complete: false``, so admitting buildings to
    an importance-driven rule without also giving them an evidence
    disposition would refuse the whole run.
    """
    assert debt_policy_key_for("background", "A") == "background_A"
    assert debt_policy_key_for("route_visible", "A") == "route_visible_C"
    assert debt_policy_key_for("landmark", "A") == "route_visible_C"
    assert debt_policy_key_for("route_visible", "B") == "route_visible_B"


# --------------------------------------------------------------------------
# the real planner, on the real lane inputs
# --------------------------------------------------------------------------

LANE = REPO_ROOT / ".run/numabe_connected/build_planning_arch09"
BUNDLE = (REPO_ROOT
          / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915")
CATALOG = (REPO_ROOT / "artifacts/experiments/fl4813_capture_review_20260915"
           / "preserved/numabe_full_727_mapping_20260915"
           / "generic_module_catalog.json")
PACK = REPO_ROOT / "assets/world_templates/japan_urban/pack.json"
FUSION = REPO_ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/fusion_reviews.json"

_LANE_INPUTS = (
    LANE / "canonical.world_ir.json", LANE / "canonical_entities.json",
    LANE / "observation_debt.json", LANE / "osm_ingest_receipt.json",
    LANE / "plan/rules_resolved.json", LANE / "osm.world_ir.json",
    LANE / "plan/jev_decisions.json", LANE / "terrain_metadata.json",
    BUNDLE / "valhalla_pedestrian_route.json",
    BUNDLE / "required_landmarks.json", CATALOG, PACK, FUSION)

requires_lane = pytest.mark.skipif(
    not all(p.is_file() for p in _LANE_INPUTS),
    reason="lane planning artifacts absent (.run is not tracked)")


def _build_lane_plan(route_importance_path):
    """Invoke the production planner, not a restatement of its logic."""
    from scripts.pipeline.compile_representation_plan import build
    return build(
        LANE / "canonical.world_ir.json", LANE / "canonical_entities.json",
        LANE / "observation_debt.json", LANE / "osm_ingest_receipt.json",
        LANE / "plan/rules_resolved.json", LANE / "osm.world_ir.json",
        CATALOG, PACK,
        jev_path=LANE / "plan/jev_decisions.json",
        route_path=BUNDLE / "valhalla_pedestrian_route.json",
        fusion_path=FUSION,
        terrain_metadata_path=LANE / "terrain_metadata.json",
        route_importance_path=route_importance_path)


@pytest.fixture(scope="module")
def lane_receipt(tmp_path_factory):
    """Compile the receipt from the same canonical IR the planner will read."""
    from scripts.pipeline.compile_route_importance import (
        compile_route_importance)
    out = tmp_path_factory.mktemp("ri") / "route_importance.json"
    compile_route_importance(LANE / "canonical.world_ir.json",
                             BUNDLE / "valhalla_pedestrian_route.json",
                             BUNDLE / "required_landmarks.json", out)
    return out


@pytest.fixture(scope="module")
def lane_off():
    """build() is not cheap; both lane plans are compiled once per module."""
    return _build_lane_plan(None)


@pytest.fixture(scope="module")
def lane_on(lane_receipt):
    return _build_lane_plan(lane_receipt)


@requires_lane
def test_real_planner_leaves_every_building_background_without_the_receipt(
        lane_off):
    """The pre-R1 state, measured through build() rather than asserted."""
    plan, receipt = lane_off
    facades = [r for r in plan["rows"]
               if r["semantic_class"] == "building_footprint"
               and not r["primary"]]
    assert facades, "expected secondary building facade rows"
    assert {r["importance"] for r in facades} == {"background"}
    assert "route_importance" not in receipt["inputs"], \
        "a planner that consumed nothing must pin nothing"


@requires_lane
def test_real_planner_promotes_route_visible_buildings(lane_on):
    """The receipt reaches the shipped facade rows, measured on lane data.

    Both halves matter: buildings stop being uniformly background, AND some
    stay background, so the receipt is being read rather than blanket-applied.
    """
    plan, receipt = lane_on
    facades = [r for r in plan["rows"]
               if r["semantic_class"] == "building_footprint"
               and not r["primary"]]
    promoted = [r for r in facades if r["importance"] == "route_visible"]
    background = [r for r in facades if r["importance"] == "background"]
    assert promoted and background, {
        "facades": len(facades), "promoted": len(promoted)}
    assert len(promoted) + len(background) == len(facades)
    assert receipt["inputs"]["route_importance"], "receipt must pin the input"


@requires_lane
def test_real_planner_reports_the_new_blocking_row(lane_off, lane_on):
    """Wiring the receipt ADDS evidence debt. Recorded, not discovered later.

    A street sighting with no defensible authored representation may be
    intentionally omitted only while it is background. Measured against the
    route it is not background, so R-STREET-OMIT stops applying and the
    sighting becomes observation_owed -- which is correct, and which
    compile_preassembly_candidate blocks on. The orchestrator owner needs
    this number before wiring, not after.
    """
    off, on = lane_off[0], lane_on[0]
    off_incomplete = {r["feature_id"] for r in off["rows"]
                      if not r["evidence_complete"]}
    on_incomplete = {r["feature_id"] for r in on["rows"]
                     if not r["evidence_complete"]}
    assert off_incomplete <= on_incomplete, \
        "route importance must not silence an existing debt row"
    added = on_incomplete - off_incomplete
    assert len(added) == len(on_incomplete) - len(off_incomplete)
    for fid in added:
        row = next(r for r in on["rows"]
                   if r["feature_id"] == fid and not r["evidence_complete"])
        assert row["disposition"] == "observation_owed", row["disposition"]


@requires_lane
def test_real_planner_does_not_change_reconstruction_class(
        lane_off, lane_on):
    """Scope statement: importance reaches the field, not yet the class.

    A reader could reasonably assume promoting a building to route_visible
    raises its target reconstruction class. It does not today -- no rule
    consults importance for that -- and this pins the honest scope so the
    claim is not made by accident.
    """
    off, on = lane_off[0], lane_on[0]
    off_class = {(r["representation_id"], r["primary"]):
                 r["reconstruction_class"] for r in off["rows"]}
    changed = [k for k, r in
               {(r["representation_id"], r["primary"]): r
                for r in on["rows"]}.items()
               if k in off_class and r["reconstruction_class"] != off_class[k]]
    assert not changed, changed[:5]


@requires_lane
def test_real_producer_pins_its_inputs_by_hash(lane_receipt):
    """The receipt's provenance is its input hashes, not its input paths.

    The canonical IR it was compiled from lives under gitignored ``.run/``.
    A preserved receipt naming that path alone names a file that can vanish,
    and cannot be checked against anything while it exists. Every input is
    pinned by content, including the landmark document, which is the sole
    source of every ``landmark`` row and is re-derived by no later stage.
    """
    from scripts.pipeline.representation_plan_provenance import file_sha256
    receipt = json.loads(lane_receipt.read_text(encoding="utf-8"))
    inputs = receipt["inputs"]
    assert set(inputs) == {"world_ir", "route", "landmarks"}, sorted(inputs)
    for name, entry in inputs.items():
        assert entry["path"], name
        assert entry["sha256"] == file_sha256(entry["path"]), name
