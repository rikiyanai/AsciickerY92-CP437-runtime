"""Executable guards for FL-4876 manual placement and JEV review routing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.pipeline.spatial_coherence_review import (
    SpatialDecisionInvalid,
    apply_authored_adjudications,
    build_jev_input,
    load_decisions,
)
from scripts.pipeline.run_spatial_coherence_jev import (
    PROPOSALS_SCHEMA,
    TypesafeSpatialBackend,
    run as run_spatial_jev,
)
from scripts.pipeline.jev_decisions import JevError


ROOT = Path(__file__).resolve().parents[2]
DECISIONS = (
    ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
    / "authored_spatial_coherence_decisions.json"
)


def _decided_copy(tmp_path: Path, decision_ids: list[str]) -> dict:
    """Mechanism fixture: the named live rows re-marked as decided.  The live
    rows were superseded on 2026-10-13 (MANUAL-WORLD-03: their derived
    observation placements no longer emit and authored fixtures own the
    objects), but the adjudication mechanisms they exercise must stay tested."""
    document = json.loads(DECISIONS.read_text(encoding="utf-8"))
    rows = []
    for row in document["decisions"]:
        if row["decision_id"] in decision_ids:
            row = dict(row, status="decided")
            row.pop("superseded_by", None)
            rows.append(row)
    path = tmp_path / "decided_copy.json"
    path.write_text(json.dumps(dict(document, decisions=rows)), encoding="utf-8")
    return load_decisions(path)


def test_reviewed_fixture_decisions_are_exact_and_bounded():
    loaded = load_decisions(DECISIONS)
    # MANUAL-WORLD-03 withholds ray-projected observation placements, so every
    # decision that targeted one is superseded by the authored fixture (or the
    # railway geometry) that owns the object; only rows whose target still
    # emits remain decided.
    assert {(row["target_kind"], row["target_id"], row["action"])
            for row in loaded["rows"]} == {
        ("instance", "linear_obs-pole-0023__obs-pole-0059_span",
         "set_minimum_span_endpoint_lateral"),
        ("instance", "facade_feature_facade_plane_numabe_station_entrance_1",
         "offset_facade_normal"),
    }
    document = json.loads(DECISIONS.read_text(encoding="utf-8"))
    superseded = [r for r in document["decisions"] if r["status"] == "superseded"]
    assert len(superseded) == 19
    assert all(r.get("superseded_by") for r in superseded)
    owners = {r["decision_id"]: r["superseded_by"] for r in superseded}
    assert owners["fl4876-jev-visual-01-0498"] == "SF-4876-X001"
    assert owners["fl4876-jev-visual-01-0602"] == "SF-4876-X004"
    assert owners["fl4876-manual-0517-axis"] == "SF-4876-X003"
    fixtures = {f["fixture_id"]: f for f in json.loads(
        (DECISIONS.parent / "authored_scene_fixtures.json").read_text(
            encoding="utf-8"))["fixtures"]}
    for fid in ("SF-4876-X001", "SF-4876-X002", "SF-4876-X003", "SF-4876-X004"):
        assert fixtures[fid]["manual_review"]["status"] == "accepted"


def test_old_pole_0417_offset_is_superseded_by_authored_position():
    document = json.loads(DECISIONS.read_text(encoding="utf-8"))
    old = next(
        row for row in document["decisions"]
        if row["decision_id"] == "fl4876-manual-pole-0417-photo"
    )
    assert old["status"] == "superseded"
    assert old["superseded_by"] == "POS-4876-002"

    loaded = load_decisions(DECISIONS)
    assert not any(
        row["target_kind"] == "placement"
        and row["target_id"] == "obs-inst-0417"
        for row in loaded["rows"]
    )


def test_unbounded_model_action_is_rejected(tmp_path: Path):
    document = json.loads(DECISIONS.read_text(encoding="utf-8"))
    document["decisions"][0]["action"] = "invent_coordinates"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(SpatialDecisionInvalid, match="not bounded"):
        load_decisions(path)


def test_span_endpoints_carry_stable_canonical_pole_ids(tmp_path: Path):
    loaded = load_decisions(DECISIONS)
    span = next(r for r in loaded["rows"]
                if r["action"] == "set_minimum_span_endpoint_lateral")
    assert span["parameters"]["endpoint_canonical_feature_ids"] == [
        "feature:mapillary_object/497769121667490",
        "feature:mapillary_object/497896894988046"]
    document = json.loads(DECISIONS.read_text(encoding="utf-8"))
    row = next(d for d in document["decisions"]
               if d["action"] == "set_minimum_span_endpoint_lateral")
    row["parameters"]["endpoint_canonical_feature_ids"] = ["x", "x"]
    path = tmp_path / "bad_span.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(SpatialDecisionInvalid, match="canonical ids"):
        load_decisions(path)


def test_decided_action_without_a_geometry_consumer_is_rejected(
        tmp_path: Path):
    document = json.loads(DECISIONS.read_text(encoding="utf-8"))
    next(r for r in document["decisions"]
         if r["status"] == "decided")["action"] = "change_host"
    path = tmp_path / "unsupported.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(SpatialDecisionInvalid, match="no deterministic"):
        load_decisions(path)


def test_axial_decision_does_not_hide_a_mass_collision(tmp_path: Path):
    decisions = _decided_copy(tmp_path, ["fl4876-manual-0517-axis"])
    rows = [{
        "inst_name": "linear_obs-inst-0517_unit",
        "orientation_residual_deg": 180.0,
        "collision_state": "inside_mass",
        "verdict": "FAIL",
        "reason": "inside building feature:building_footprint:test",
    }]
    applied = apply_authored_adjudications(rows, decisions, 5.0)
    assert rows[0]["verdict"] == "FAIL"
    assert rows[0]["orientation_residual_deg"] == 0.0
    assert applied[0]["applied_to_verdict"] is False


def test_exact_gantry_failure_can_be_adjudicated_but_not_another_failure(
        tmp_path: Path):
    decisions = _decided_copy(tmp_path, ["fl4876-manual-gantry-0462-0510"])
    exact = {
        "inst_name": "linear_obs-inst-0462__obs-inst-0510_span",
        "orientation_residual_deg": None,
        "collision_state": "span_off_carrier",
        "verdict": "FAIL",
        "reason": "span is not roadside",
    }
    different = dict(exact, collision_state="inside_mass")
    applied = apply_authored_adjudications(
        [exact, different], decisions, 5.0)
    assert exact["verdict"] == "PASS"
    assert different["verdict"] == "FAIL"
    assert [row["applied_to_verdict"] for row in applied] == [True, False]


def test_jev_input_contains_only_failures_and_requires_fresh_visual_review(
        tmp_path: Path):
    coherence_path = tmp_path / "spatial_coherence.json"
    coherence = {
        "rows": [
            {
                "feature_id": "feature:test:bad",
                "inst_name": "linear_test_bad",
                "source_placement_ids": ["obs-test-bad"],
                "verdict": "FAIL",
                "collision_state": "inside_mass",
                "anchor_kind": "carrier_chainage",
                "reason": "inside mass",
            },
            {
                "feature_id": "feature:test:good",
                "inst_name": "linear_test_good",
                "verdict": "PASS",
                "collision_state": "clear",
            },
        ]
    }
    coherence_path.write_text(json.dumps(coherence), encoding="utf-8")
    result = build_jev_input(
        coherence, coherence_path=coherence_path,
        decisions=load_decisions(DECISIONS))
    assert result["counts"] == {
        "jobs": 1, "coherence_jobs": 1, "plan_anomaly_jobs": 0,
        "authored_decisions_excluded": 0}
    job = result["jobs"][0]
    assert [option["choice"] for option in job["options"]][0] == (
        "offset_from_mass_boundary")
    assert job["decision_contract"] == {
        "model_output_is_proposal": True,
        "geometry_output_forbidden": True,
        "human_acceptance_required_before_apply": True,
    }
    assert job["visual_evidence"]["state"] == "needs_evidence_renderer"
    assert job["post_jev_visual_verification"]["requires_fresh_rebuild"] is True


def test_jev_queue_excludes_an_authored_source_placement(tmp_path: Path):
    coherence_path = tmp_path / "spatial_coherence.json"
    coherence = {"rows": [{
        "feature_id": "feature:rail",
        "inst_name": "linear_obs-inst-0459_unit",
        "source_placement_ids": ["obs-inst-0459"],
        "verdict": "FAIL",
        "collision_state": "clear",
        "reason": "rail corridor on a footway",
    }]}
    coherence_path.write_text(json.dumps(coherence), encoding="utf-8")
    result = build_jev_input(
        coherence, coherence_path=coherence_path,
        decisions=_decided_copy(tmp_path, ["fl4876-manual-0459-rail"]))
    assert result["counts"] == {
        "jobs": 0, "coherence_jobs": 0, "plan_anomaly_jobs": 0,
        "authored_decisions_excluded": 1}
    assert result["excluded_authored_decision_ids"] == [
        "fl4876-manual-0459-rail"]


def test_zero_offset_edge_plan_rows_enter_review_not_auto_fix(tmp_path: Path):
    coherence_path = tmp_path / "coherence.json"
    coherence_path.write_text('{"rows": []}', encoding="utf-8")
    plan_path = tmp_path / "population_plan.json"
    plan = {"carriers": [{"placements": [{
        "placement_id": "obs-test-fence",
        "module_id": "rail_corridor_fence",
        "canonical_feature_id": "feature:test:fence",
        "evidence": {"kind": "observation"},
        "attachment": {
            "record_type": "attachment",
            "anchor_kind": "carrier_span",
            "canonical_carrier_id": "feature:railway:test",
            "lateral_offset_m": 0.0,
        },
    }]}]}
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    result = build_jev_input(
        {"rows": []}, coherence_path=coherence_path,
        decisions=load_decisions(DECISIONS), population_plan=plan,
        population_plan_path=plan_path)
    assert result["counts"]["plan_anomaly_jobs"] == 1
    assert result["jobs"][0]["review_id"] == (
        "spatial-plan-zero-offset:obs-test-fence")
    assert result["jobs"][0]["options"][0]["choice"] == (
        "accept_current")


def test_zero_offset_edge_gets_concrete_measured_choices(tmp_path: Path):
    coherence_path = tmp_path / "coherence.json"
    coherence_path.write_text('{"rows": []}', encoding="utf-8")
    plan_path = tmp_path / "population_plan.json"
    plan = {"carriers": [{"placements": [{
        "placement_id": "obs-test-fence",
        "module_id": "rail_corridor_fence",
        "canonical_feature_id": "feature:test:fence",
        "evidence": {"kind": "observation"},
        "attachment": {
            "record_type": "attachment",
            "anchor_kind": "carrier_span",
            "canonical_carrier_id": "feature:highway:test",
            "lateral_offset_m": 0.0,
        },
    }]}]}
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    candidates = {"records": [{
        "review_id": "spatial-plan-zero-offset:obs-test-fence",
        "candidate_lateral_m": 3.25,
        "candidate_basis": "lidar_inner_edge+photo_ray",
        "carrier_band_continuity": 0.0,
        "photo_box_az_deg": [10.0, 20.0],
        "photo_ray_deg": 15.0,
        "planned_side": "R",
        "inner_edges": {
            "R": {"lat_m": 3.25, "continuity": 0.9,
                  "current_run_share_in_photo_box": 0.8},
            "L": {"lat_m": -2.0, "continuity": 0.4,
                  "current_run_share_in_photo_box": 0.0},
        },
    }]}
    result = build_jev_input(
        {"rows": []}, coherence_path=coherence_path,
        decisions=load_decisions(DECISIONS), population_plan=plan,
        population_plan_path=plan_path,
        spatial_candidate_evidence=candidates)
    job = result["jobs"][0]
    assert "not through an active carriageway" in job["facts"][
        "physical_context"]
    assert job["options"][0] == {
        "choice": "measured_primary",
        "action": "set_carrier_lateral_offset",
        "parameters": {"side": "R", "target_lateral_m": 3.25},
        "criterion": (
            "Place the edge object 3.25 m on the R side of the named "
            "carrier and keep its long axis parallel to that carrier. This "
            "candidate comes from the measured photo/LiDAR cross-section; "
            "continuity=0.90, photo-box share=0.80."),
    }


def test_jev_adapter_writes_proposals_not_planner_decisions(tmp_path: Path):
    class FakeBackend:
        stats = {"requests": 1}

        @staticmethod
        def propose(job):
            offered = {option["choice"] for option in job["options"]}
            assert "unresolved" in offered
            return {
                "proposed_action": "unresolved",
                "confidence": 0.9,
                "probabilities": {"unresolved": 0.9},
                "request_id": "fake-request",
                "resolved_model": "fake-jev",
            }

    coherence_path = tmp_path / "coherence.json"
    coherence = {"rows": [{
        "feature_id": "feature:test",
        "inst_name": "linear_test",
        "verdict": "FAIL",
        "collision_state": "clear",
        "reason": "unreviewed mismatch",
    }]}
    coherence_path.write_text(json.dumps(coherence), encoding="utf-8")
    jev_input = build_jev_input(
        coherence, coherence_path=coherence_path,
        decisions=load_decisions(DECISIONS))
    input_path = tmp_path / "jev_input.json"
    input_path.write_text(json.dumps(jev_input), encoding="utf-8")
    out_path = tmp_path / "proposals.json"
    result = run_spatial_jev(input_path, out_path, FakeBackend())
    assert result["schema"] == PROPOSALS_SCHEMA
    assert result["contract"]["planner_consumable"] is False
    assert result["contract"]["model_generated_geometry_forbidden"] is True
    assert result["contract"][
        "supplied_geometry_candidate_selection_allowed"] is True
    assert result["proposals"][0]["status"] == "proposed"
    assert result["proposals"][0]["human_acceptance_required"] is True
    assert json.loads(out_path.read_text(encoding="utf-8")) == result


def test_jev_adapter_preserves_input_order_with_bounded_parallelism(
        tmp_path: Path):
    class FakeBackend:
        stats = {"requests": 2}

        @staticmethod
        def propose(job):
            return {
                "proposed_action": "unresolved",
                "confidence": 0.75,
                "probabilities": {"unresolved": 0.75},
                "request_id": job["review_id"],
                "resolved_model": "fake-jev",
            }

    jobs = []
    for index in range(2):
        jobs.append({
            "review_id": f"spatial:{index}",
            "feature_id": f"feature:{index}",
            "instance_id": None,
            "options": [{
                "choice": "unresolved", "criterion": "insufficient"}],
            "facts": {"reason": "test"},
            "visual_evidence": {"state": "needs_evidence_renderer"},
            "post_jev_visual_verification": {"required": True},
        })
    input_path = tmp_path / "parallel-input.json"
    input_path.write_text(json.dumps({
        "schema": "y92.spatial_coherence_jev_input.v1", "jobs": jobs,
    }), encoding="utf-8")
    result = run_spatial_jev(
        input_path, tmp_path / "parallel-output.json", FakeBackend(),
        workers=2)
    assert [row["review_id"] for row in result["proposals"]] == [
        "spatial:0", "spatial:1"]
    assert result["execution"] == {"workers": 2}


def test_live_adapter_refuses_before_visual_evidence_is_ready(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "not-sent")
    backend = TypesafeSpatialBackend(max_retries=0)
    job = {
        "review_id": "spatial:test",
        "options": [{"choice": "unresolved", "criterion": "not enough"}],
        "visual_evidence": {"state": "needs_evidence_renderer"},
    }
    with pytest.raises(JevError, match="evidence is not ready"):
        backend.propose(job)
    assert backend.stats["requests"] == 0


def test_live_adapter_refuses_generic_anomaly_text(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "not-sent")
    backend = TypesafeSpatialBackend(max_retries=0)
    with pytest.raises(JevError, match="anomaly text and image paths"):
        backend.propose({
            "review_id": "spatial:test",
            "facts": {"reason": "offset is unverified"},
            "options": [{
                "choice": "unresolved", "criterion": "not enough"}],
            "visual_evidence": {"state": "rendered_for_human_review",
                                "plan_overlay_path": "/tmp/panel.jpg"},
        })


def test_live_adapter_maps_measured_choice_to_bounded_geometry(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "not-sent")
    backend = TypesafeSpatialBackend(max_retries=0)

    def fake_post(_questions, _state):
        return {
            "answers": {"placement_action": {
                "choice": "measured_left_edge",
                "confidence": 0.96,
                "probabilities": {
                    "measured_left_edge": 0.96,
                    "unresolved": 0.04,
                },
            }},
            "model": "fake",
            "request_id": "fake-request",
        }

    monkeypatch.setattr(backend, "_post", fake_post)
    result = backend.propose({
        "review_id": "spatial:test-fence",
        "facts": {"physical_context": "fence is on a road centreline"},
        "options": [{
            "choice": "measured_left_edge",
            "action": "set_carrier_lateral_offset",
            "parameters": {"side": "L", "target_lateral_m": 3.2},
            "criterion": "measured road-edge candidate",
        }, {
            "choice": "unresolved",
            "action": "unresolved",
            "criterion": "evidence cannot decide",
        }],
        "visual_evidence": {"state": "needs_evidence_renderer"},
    })
    assert result["selected_option"] == "measured_left_edge"
    assert result["proposed_action"] == "set_carrier_lateral_offset"
    assert result["proposed_parameters"] == {
        "side": "L", "target_lateral_m": 3.2}
