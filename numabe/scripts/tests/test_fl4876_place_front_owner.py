#!/usr/bin/env python3
"""FL-4876 PLACE-FRONT-01. The place_fronts owner stage, actually connected.

Thirteen named commercial places are routed by the representation plan to
``owner_stage: "place_fronts"``. Their designated emitter,
``compile_world_ir_place_fronts``, existed, refused foreign owners, and had
its own tests -- but the production runner loaded its plan slice, printed the
row count, and never called it. The gate therefore demanded a *population*
placement for rows population can never own, which is a true refusal with a
false cause.

The repair is the wiring, not the wording. These tests pin the three joints
that make the difference observable:

* the runner invokes the emitter (delete the call and this fails);
* the gate accepts the row only when the owner's plan slice carries it;
* the emission manifest reconciles every planned front against an emitted
  instance, and names a debt when one is missing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.compile_emission_manifest import (  # noqa: E402
    build as build_emission_manifest,
)
from scripts.pipeline.compile_preassembly_candidate import (  # noqa: E402
    build as build_candidate,
)
from scripts.pipeline.compile_representation_plan import (  # noqa: E402
    NON_POPULATION_OWNER_SLICES,
    NON_POPULATION_OWNER_STAGES,
    POPULATION_OWNER_STAGES,
    WIRED_NON_POPULATION_OWNERS,
)
from scripts.tests.test_fl4809_preassembly_candidate import (  # noqa: E402
    _candidate_inputs,
    _write,
)

@pytest.fixture(autouse=True)
def _projected_route(monkeypatch):
    """Same stub the sibling gate suite uses; this lane's route is not the
    subject here and a Valhalla leg would only add an unrelated failure."""
    import scripts.pipeline.compile_preassembly_candidate as gate
    monkeypatch.setattr(
        gate, "_project_route",
        lambda *_args: ([(0.0, 0.0), (100.0, 0.0)], {}))


RUNNER_SOURCE = ROOT / "scripts" / "sbu_e2e_run.py"
LOWERER_SOURCE = ROOT / "scripts" / "pipeline" / "compile_world_ir_fixtures.py"

PLACE_ID = "feature:place"


# --------------------------------------------------------------------------
# The wiring claim, and its falsifier.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("stage,token", sorted(WIRED_NON_POPULATION_OWNERS.items()))
def test_the_runner_actually_invokes_every_owner_it_claims_to_have_wired(
        stage, token):
    """The gate may only trust a stage the production runner really calls.

    This is the whole difference between connecting an owner and renaming its
    blocker. If the call site is deleted, the gate would start passing rows
    nothing emits -- so the deletion has to fail here first.
    """
    source = RUNNER_SOURCE.read_text(encoding="utf-8")
    assert token in source, (
        f"{stage!r} is declared wired, but {RUNNER_SOURCE.name} contains no "
        f"{token!r} call site")
    # Loading the plan slice is not invoking the owner: that was the old
    # state, and it is exactly what this test must not accept as wiring.
    call_lines = [line.strip() for line in source.splitlines()
                  if token in line and not line.strip().startswith("#")]
    assert call_lines, f"{token!r} appears only in comments"


def test_every_wired_owner_declares_an_emitter_and_a_plan_slice():
    for stage in WIRED_NON_POPULATION_OWNERS:
        assert stage in NON_POPULATION_OWNER_STAGES, (
            f"{stage!r} is wired but names no emitter")
        assert stage in NON_POPULATION_OWNER_SLICES, (
            f"{stage!r} is wired but names no plan slice to hand it")
        assert stage not in POPULATION_OWNER_STAGES, (
            f"{stage!r} cannot be both a population owner and an emission owner")


def test_the_runner_hands_the_emitter_the_plan_slice_it_loaded():
    """A lowerer called without its plan rows would re-derive its own scope."""
    source = RUNNER_SOURCE.read_text(encoding="utf-8")
    start = source.index("compile_world_ir_place_fronts(")
    call = source[start:start + 1200]
    assert "plan_rows=plan_front_rows" in call
    assert "canonical_doc=lane_canonical_doc" in call
    assert "instances_out=place_front_instances_path" in call


def test_the_runner_invokes_the_representation_placement_generator():
    """FL-4876 REPFIX-01. The same wiring test, for the stage that had the
    same defect.

    compile_representation_fixture_placements generates a population
    placement for every mesh_fixture row whose template and attachment the
    plan already resolved. It was written, tested, measured through a
    hand-assembled chain -- and never called by the production runner. The
    first orchestrated run after the place_fronts repair is what exposed it:
    the chain measured 49 blockers and the runner measured 387, the whole
    difference being this stage and what depends on it.

    A generator nothing calls is not a repair, whoever wrote it.
    """
    source = RUNNER_SOURCE.read_text(encoding="utf-8")
    assert "build_representation_placements(" in source, (
        "the production runner does not invoke "
        "compile_representation_fixture_placements")
    start = source.index("build_representation_placements(")
    call = source[start:start + 500]
    assert "representation_plan=" in call
    assert "canonical=" in call
    assert "merge_representation_placements(" in source, (
        "generated placements are never merged into the population plan")
    # And the merged plan, not the original, is what dispatch receives.
    merge = source.index("merge_representation_placements(_repfix_plan")
    dispatch = source.index("write_dispatch(", merge)
    assert source.index("plan_path = lane_pop_dir", merge) < dispatch, (
        "dispatch must receive the merged plan, not the pre-merge one")


def test_the_emitter_refuses_plan_rows_owned_by_another_stage():
    """Unchanged pre-existing guard; pinned because the wiring now feeds it."""
    source = LOWERER_SOURCE.read_text(encoding="utf-8")
    assert 'if prow.get("owner_stage") not in ("place_fronts", None):' in source


def test_the_build_receipt_no_longer_counts_fixture_panels_as_place_fronts():
    """emitted_place_fronts counted a different owner's facade panels.

    It was ``len(_front_emissions)`` -- fixture-owner ``building_face_panel``
    rows -- so the count was non-zero on every run in which no place front
    was ever emitted.
    """
    source = RUNNER_SOURCE.read_text(encoding="utf-8")
    assert '"emitted_place_fronts": len(_place_front_emissions),' in source
    assert 'row.get("owner") == "place_front"' in source


# --------------------------------------------------------------------------
# The pre-assembly handoff check.
# --------------------------------------------------------------------------

def _place_front_inputs(tmp_path: Path, *, slice_features=None,
                        slice_dir: bool = True) -> dict:
    inputs = _candidate_inputs(tmp_path)
    representation = json.loads(
        Path(inputs["representation_plan_path"]).read_text(encoding="utf-8"))
    for row in representation["rows"]:
        if row["feature_id"] == PLACE_ID:
            row["owner_stage"] = "place_fronts"
    _write(inputs["representation_plan_path"], representation)
    if slice_dir:
        owner_dir = tmp_path / "plan_by_owner"
        owner_dir.mkdir(exist_ok=True)
        features = [PLACE_ID] if slice_features is None else slice_features
        _write(owner_dir / NON_POPULATION_OWNER_SLICES["place_fronts"],
               {"rows": [{"feature_id": fid, "owner_stage": "place_fronts",
                          "disposition": "mesh_facade"} for fid in features]})
        inputs["plan_by_owner_dir"] = owner_dir
    return inputs


def _kinds(document, subject=PLACE_ID) -> list[str]:
    return sorted({row["kind"] for row in document["blockers"]
                   if row["subject"] == subject})


def _reason(document, kind, subject=PLACE_ID) -> str:
    return next(row["reason"] for row in document["blockers"]
                if row["subject"] == subject and row["kind"] == kind)


def test_a_place_front_row_in_its_owner_slice_is_not_a_blocker(tmp_path):
    document = build_candidate(**_place_front_inputs(tmp_path))
    assert _kinds(document) == []


def test_a_place_front_row_missing_from_its_owner_slice_still_refuses(tmp_path):
    """The row is planned, but the emitter will never be handed it."""
    document = build_candidate(
        **_place_front_inputs(tmp_path, slice_features=[]))
    assert _kinds(document) == ["required_place",
                                "unwired_representation_owner"]
    reason = _reason(document, "unwired_representation_owner")
    assert "place_fronts.json" in reason
    assert "never see this row" in reason
    # The required-place blocker names the same true cause rather than
    # sending a reader into population.
    assert "never see this row" in _reason(document, "required_place")


def test_without_the_slice_directory_the_handoff_cannot_be_verified(tmp_path):
    """Fail closed: an unsupplied slice is not an absent obligation."""
    document = build_candidate(**_place_front_inputs(tmp_path, slice_dir=False))
    assert _kinds(document) == ["required_place",
                                "unwired_representation_owner"]
    assert "cannot be verified" in _reason(
        document, "unwired_representation_owner")


def test_a_declared_but_unwired_owner_stage_still_refuses(tmp_path, monkeypatch):
    """Declaring an emitter is not connecting it.

    This is the state place_fronts was in: an owner with a real emitter that
    the production runner never invoked.
    """
    import scripts.pipeline.compile_preassembly_candidate as gate
    monkeypatch.setattr(gate, "WIRED_NON_POPULATION_OWNERS", {})
    document = build_candidate(**_place_front_inputs(tmp_path))
    assert _kinds(document) == ["required_place",
                                "unwired_representation_owner"]
    assert "no production runner invocation" in _reason(
        document, "unwired_representation_owner")


def test_the_place_front_row_never_asks_population_for_a_placement(tmp_path):
    """The old blocker sent readers hunting through population."""
    document = build_candidate(**_place_front_inputs(tmp_path,
                                                     slice_features=[]))
    assert "population_owner" not in _kinds(document)


# --------------------------------------------------------------------------
# The emission-side reconciliation.
# --------------------------------------------------------------------------

def _emission_build_dir(tmp_path: Path, *, planned, emitted) -> Path:
    build_dir = tmp_path / "build"
    plan_dir = build_dir / "plan" / "population"
    plan_dir.mkdir(parents=True)
    for owner in ("linear", "fixtures", "custom", "terminal"):
        _write(plan_dir / f"population_plan.{owner}.json", {"placements": []})
    _write(plan_dir / "population_dispatch.json", {"placements_by_owner": {}})
    _write(build_dir / "plan" / "station_facade_plan.json", {"placements": []})
    for name in ("lower_linear_instances", "lower_fixtures_instances",
                 "lower_custom_instances", "station_facade_instances"):
        _write(build_dir / f"{name}.json", [])
    _write(build_dir / "building_instances.json", [])
    _write(build_dir / "surface_receipt.json",
           {"features": [], "terminal_states": []})
    owner_dir = build_dir / "plan" / "plan_by_owner"
    owner_dir.mkdir(parents=True)
    _write(owner_dir / "place_fronts.json",
           {"rows": [{"feature_id": fid, "owner_stage": "place_fronts"}
                     for fid in planned]})
    if emitted is not None:
        meshes = build_dir / "meshes"
        meshes.mkdir(exist_ok=True)
        rows = []
        for fid in emitted:
            mesh = meshes / (fid.replace(":", "_").replace("/", "_") + ".akm")
            mesh.write_bytes(b"test-akm")
            rows.append({"feature_id": fid, "mesh_name": mesh.name,
                         "inst_name": "front_" + fid.split("/")[-1],
                         "representation_id": "rep:" + fid,
                         "resolved_profile_id": "narrow_mixed_use",
                         "transform": [1, 0, 0, 0]})
        _write(build_dir / "place_front_instances.json", rows)
    return build_dir


def test_an_emitted_place_front_reconciles_against_its_plan_row(tmp_path):
    manifest = build_emission_manifest(_emission_build_dir(
        tmp_path, planned=[PLACE_ID], emitted=[PLACE_ID]))
    assert manifest["counts"]["place_front_representations"] == 1
    assert manifest["counts"]["place_fronts_emitted"] == 1
    assert manifest["counts"]["blocking_terminal_states"] == 0
    owners = {row["owner"] for row in manifest["emissions"]
              if row["record_kind"] == "emitted_instance"}
    assert "place_front" in owners


def test_a_planned_place_front_with_no_instance_is_a_named_debt(tmp_path):
    manifest = build_emission_manifest(_emission_build_dir(
        tmp_path, planned=[PLACE_ID, "feature:place:node/2"],
        emitted=[PLACE_ID]))
    assert manifest["counts"]["place_fronts_emitted"] == 1
    blocking = manifest["blocking_terminal_states"]
    assert [row["feature_id"] for row in blocking] == ["feature:place:node/2"]
    assert [row["reason"] for row in manifest["errors"]] == [
        "place_front_representation_not_emitted"]


def test_planned_fronts_with_no_instance_file_at_all_refuse(tmp_path):
    """The exact prior state: thirteen planned, the lowerer never invoked."""
    with pytest.raises(ValueError) as excinfo:
        build_emission_manifest(_emission_build_dir(
            tmp_path, planned=[PLACE_ID], emitted=None))
    assert "compile_world_ir_place_fronts" in str(excinfo.value)
    assert "did not run" in str(excinfo.value)


def test_a_lane_with_no_planned_fronts_stays_silent(tmp_path):
    manifest = build_emission_manifest(_emission_build_dir(
        tmp_path, planned=[], emitted=None))
    assert manifest["counts"]["place_front_representations"] == 0
    assert manifest["inputs"]["place_front_plan"] is None


def test_an_emitted_front_with_no_plan_row_is_an_error(tmp_path):
    manifest = build_emission_manifest(_emission_build_dir(
        tmp_path, planned=[PLACE_ID], emitted=[PLACE_ID, "feature:place:node/9"]))
    assert "emitted_without_planned_representation" in {
        row["reason"] for row in manifest["errors"]}


def test_the_emitted_instance_asset_must_exist_and_be_nonempty(tmp_path):
    build_dir = _emission_build_dir(tmp_path, planned=[PLACE_ID],
                                    emitted=[PLACE_ID])
    for mesh in (build_dir / "meshes").iterdir():
        mesh.write_bytes(b"")
    with pytest.raises(ValueError) as excinfo:
        build_emission_manifest(build_dir)
    assert "absent/empty" in str(excinfo.value)
