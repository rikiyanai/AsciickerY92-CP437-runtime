#!/usr/bin/env python3
"""Contract tests for the fail-closed FL-4684 reachability census.

These tests do not launch the game. They prove that the observation-only
instrumentation preserves absence, reports the twelve producer stages in source
order, refuses contradictory owner counts, and cannot manufacture cross-target
agreement. Live desktop and WebAssembly values remain a separate run gate.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MEASURER = ROOT / "scripts/measure_fl4684_live_producer_evidence.py"
TOTALS = ROOT / "apps/fl4512-runtime/platform/fl4684_active_stroke_reachability_totals.h"
CENSUS = ROOT / "apps/fl4512-runtime/platform/fl4684_scene_fact_emission_census.h"
WORLD = ROOT / "engine/render/render_world_pass.cpp"
INTERNAL = ROOT / "engine/render/render_internal.h"
BRIDGE = ROOT / "engine/render/render_frame_input.h"
RESOLVE = ROOT / "engine/render/render_resolve.cpp"
# FL-4512 seam: the observer-carrier copy and the census-tapped producer live
# in Current owners now (module + scene-fact builder); the shared TUs route
# through hooks.
MODULE = ROOT / "apps/fl4512-runtime/engine/fl4512_runtime_module.cpp"
BUILDER = ROOT / "apps/fl4512-runtime/engine/fl4512_scene_fact_builder.cpp"


def load_measurer():
    spec = importlib.util.spec_from_file_location("fl4684_measurer", MEASURER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def measurer():
    return load_measurer()


def full_reachability(**overrides) -> dict:
    block = {
        "snapshot_reads": 20,
        "finalizer_entered_total": 12,
        "missing_store_total": 0,
        "reprojection_failure_total": 0,
        "pre_admission_ready_total": 12,
        "admission_scan_entered_total": 12,
        "eligible_input_feature_total": 300,
        "admission_success_total": 12,
        "owner_invoked_total": 12,
        "observation_valid_total": 12,
        "observation_invalid_total": 0,
        "input_sample_total": 240,
        "input_sample_frame_max": 20,
        "frames_with_any_input_sample": 12,
        "owner_group_total": 48,
        "owner_group_frame_max": 4,
        "frames_with_any_owner_group": 12,
        "accepted_owner_group_total": 36,
        "accepted_owner_group_frame_max": 3,
        "frames_with_any_accepted_owner_group": 12,
        "rejected_owner_group_total": 12,
        "rejected_owner_group_frame_max": 1,
        "frames_with_any_rejected_owner_group": 12,
        "frames_with_all_owner_groups_rejected": 0,
        "owner_group_count_contract_violation_total": 0,
        "published_snake_total": 24,
        "published_snake_frame_max": 2,
        "frames_with_any_published_snake": 12,
        "published_segment_total": 96,
        "published_segment_frame_max": 8,
        "frames_with_any_published_segment": 12,
        "open_snake_total": 24,
        "closed_snake_total": 0,
        "split_total": 0,
        "split_frame_max": 0,
        "frames_with_any_split": 0,
        "merge_total": 0,
        "merge_frame_max": 0,
        "frames_with_any_merge": 0,
        "failure_frame_counts": [12, 0, 0, 0, 0, 0],
        "failure_out_of_range": 0,
        "reset_reason_frame_counts": [12, 0, 0, 0, 0, 0, 0, 0],
        "reset_reason_out_of_range": 0,
    }
    block.update(overrides)
    return block


def make_census(reachability: dict | None) -> dict:
    return {
        "addresses": [],
        "frame_id_transitions_observed": 2,
        "facts_emitted": 0,
        "address_observations": 0,
        "distinct_addresses": 0,
        "address_table_overflow": 0,
        "facts_with_empty_route_family_mask": 0,
        "facts_with_role_out_of_range": 0,
        "role_counts": [0] * 6,
        "route_family_counts": [0] * 7,
        "scene_fact_topology_event_counts": [0] * 6,
        "active_stroke_reachability": reachability,
    }


def write_run(
    tmp_path: Path,
    name: str,
    reachability: dict | None,
    *,
    target: str = "desktop_native",
    motion: bool = False,
) -> Path:
    census_path = tmp_path / f"{name}.census.json"
    census_path.write_text(json.dumps(make_census(reachability)))
    captures = [
        {"path": "a", "scene_phase": "in_play", "bridge_frame": 1}
    ]
    if motion:
        captures.append(
            {
                "path": "b",
                "scene_phase": "in_play",
                "bridge_frame": 2,
                "pixel_bytes_differing_from_previous_pct": 10.0,
            }
        )
    receipt = {
        "census_path": str(census_path),
        "run": {"mode": "synthetic_fixture", "target": target},
        "revision": {"head": "0" * 40, "branch": "test"},
        "viewport_captures": captures,
        "capture_scene_phase_classification": {
            "classified_by": "unit test",
            "evidence": "synthetic input; no product claim",
        },
    }
    path = tmp_path / f"{name}.receipt.json"
    path.write_text(json.dumps(receipt))
    return path


def run_measurer(tmp_path: Path, receipts: dict[str, Path]) -> dict:
    out = tmp_path / "artifact.json"
    args = [sys.executable, str(MEASURER), "--out", str(out)]
    for name, path in receipts.items():
        args.extend(["--run", f"{name}={path}"])
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(out.read_text())


EXPECTED_STAGES = [
    "finalizer_entered",
    "pre_admission_authority_ready",
    "admission_scan_entered",
    "eligible_input_features",
    "compiled_profile_admitted",
    "stroke_owner_invoked",
    "valid_observation_published",
    "input_samples",
    "owner_groups",
    "accepted_owner_groups",
    "published_snakes",
    "published_segments",
]


def test_declares_all_twelve_stages_in_source_order(measurer) -> None:
    block = measurer.build_reachability_census(make_census(full_reachability()))
    assert [row["stage"] for row in block["stages"]] == EXPECTED_STAGES
    assert [row["order"] for row in block["stages"]] == list(range(12))
    assert all(len(row["zero_means"]) > 60 for row in block["stages"])


@pytest.mark.parametrize(
    ("measure", "stage"),
    [
        ("finalizer_entered_total", "finalizer_entered"),
        ("pre_admission_ready_total", "pre_admission_authority_ready"),
        ("admission_scan_entered_total", "admission_scan_entered"),
        ("eligible_input_feature_total", "eligible_input_features"),
        ("admission_success_total", "compiled_profile_admitted"),
        ("owner_invoked_total", "stroke_owner_invoked"),
        ("input_sample_total", "input_samples"),
        ("owner_group_total", "owner_groups"),
        ("accepted_owner_group_total", "accepted_owner_groups"),
        ("published_snake_total", "published_snakes"),
        ("published_segment_total", "published_segments"),
    ],
)
def test_each_measured_zero_locates_its_stage(measurer, measure, stage) -> None:
    block = measurer.build_reachability_census(
        make_census(full_reachability(**{measure: 0}))
    )
    assert block["first_empty_stage"] == stage
    assert block["verdict_blocked_by_missing_measure"] is None


def test_zero_valid_observations_blocks_downstream_verdict(measurer) -> None:
    reach = full_reachability(observation_valid_total=0)
    for key in (
        "input_sample_total",
        "owner_group_total",
        "accepted_owner_group_total",
        "published_snake_total",
        "published_segment_total",
    ):
        reach[key] = None
    block = measurer.build_reachability_census(make_census(reach))
    assert block["first_empty_stage"] is None
    assert block["verdict_blocked_by_missing_measure"] == (
        "valid_observation_published"
    )


def test_absent_snapshot_is_not_zero(measurer) -> None:
    block = measurer.build_reachability_census(make_census(None))
    assert block["census_block_present"] is False
    assert block["first_empty_stage"] is None
    assert block["verdict_blocked_by_missing_measure"] == "census_snapshot_absent"
    assert all(row["value"] is None for row in block["stages"])


def test_missing_measurement_blocks_before_later_zero(measurer) -> None:
    reach = full_reachability(input_sample_total=0)
    del reach["eligible_input_feature_total"]
    block = measurer.build_reachability_census(make_census(reach))
    assert block["first_empty_stage"] is None
    assert block["verdict_blocked_by_missing_measure"] == "eligible_input_features"


def test_recording_without_count_integrity_certificate_is_blocked(measurer) -> None:
    reach = full_reachability()
    del reach["owner_group_count_contract_violation_total"]
    block = measurer.build_reachability_census(make_census(reach))
    assert block["first_empty_stage"] is None
    assert block["verdict_blocked_by_missing_measure"] == (
        "owner_group_count_contract_check"
    )


def test_impossible_rejected_group_count_blocks_instead_of_becoming_zero(
    measurer,
) -> None:
    reach = full_reachability(
        accepted_owner_group_total=0,
        owner_group_count_contract_violation_total=1,
    )
    block = measurer.build_reachability_census(make_census(reach))
    assert block["first_empty_stage"] is None
    assert block["verdict_blocked_by_missing_measure"] == (
        "owner_group_count_contract_violation"
    )
    assert block["rejection"][
        "owner_group_count_contract_violation_total"
    ] == 1


def test_all_groups_rejected_is_a_real_stage_result(measurer) -> None:
    reach = full_reachability(
        accepted_owner_group_total=0,
        frames_with_all_owner_groups_rejected=12,
    )
    block = measurer.build_reachability_census(make_census(reach))
    assert block["first_empty_stage"] == "accepted_owner_groups"
    assert block["rejection"]["frames_with_all_owner_groups_rejected"] == 12


def test_one_run_cannot_agree_with_itself(tmp_path: Path) -> None:
    artifact = run_measurer(
        tmp_path,
        {"desktop": write_run(tmp_path, "desktop", full_reachability())},
    )
    summary = artifact["fl4684_active_stroke_reachability"]
    assert summary["runs_that_can_answer"] == ["desktop"]
    assert summary["targets_agree_on_first_empty_stage"] is False


def test_blocked_null_runs_cannot_manufacture_agreement(tmp_path: Path) -> None:
    artifact = run_measurer(
        tmp_path,
        {
            "desktop": write_run(tmp_path, "desktop", None),
            "web": write_run(tmp_path, "web", None, target="webassembly"),
        },
    )
    summary = artifact["fl4684_active_stroke_reachability"]
    assert summary["runs_that_can_answer"] == []
    assert summary["targets_agree_on_first_empty_stage"] is False


def test_two_answerable_targets_can_agree(tmp_path: Path) -> None:
    reach = full_reachability(eligible_input_feature_total=0)
    artifact = run_measurer(
        tmp_path,
        {
            "desktop": write_run(tmp_path, "desktop", reach),
            "web": write_run(tmp_path, "web", reach, target="webassembly"),
        },
    )
    summary = artifact["fl4684_active_stroke_reachability"]
    assert summary["runs_that_can_answer"] == ["desktop", "web"]
    assert summary["represented_targets_that_can_answer"] == [
        "desktop_native",
        "webassembly",
    ]
    assert summary["targets_agree_on_first_empty_stage"] is True


def test_motion_aggregate_preserves_missing_value(tmp_path: Path) -> None:
    reach = full_reachability()
    del reach["split_total"]
    artifact = run_measurer(
        tmp_path,
        {"desktop": write_run(tmp_path, "desktop", reach, motion=True)},
    )
    motion = artifact["fl4685_topology_during_motion"]
    assert motion["active_stroke_split_total_in_motion_runs"] is None
    assert motion["aggregate_blocked_by_missing_measure"] == (
        "motion_run_missing_active_stroke_measure"
    )


def test_no_motion_run_reports_unavailable_aggregate(tmp_path: Path) -> None:
    artifact = run_measurer(
        tmp_path,
        {"desktop": write_run(tmp_path, "desktop", full_reachability())},
    )
    motion = artifact["fl4685_topology_during_motion"]
    assert motion["active_stroke_split_total_in_motion_runs"] is None
    assert motion["aggregate_blocked_by_missing_measure"] == (
        "no_run_with_evidenced_camera_motion"
    )

def test_source_separates_short_circuit_prerequisites() -> None:
    # FL-4512 seam: the accumulator moved verbatim to the Current module.
    text = MODULE.read_text()
    assert "missing_store_total++" in text
    assert "reprojection_failure_total++" in text
    assert "pre_admission_ready_total++" in text
    missing = text.index("missing_store_total++")
    admission_call = text.index("Fl4512ActiveStrokeAdmissionForFrameV2", missing)
    assert missing < admission_call


def test_source_rejects_impossible_group_counts_fail_closed() -> None:
    text = MODULE.read_text()
    assert "rejected_owner_group_count > frame.owner_group_count" in text
    assert "owner_group_count_contract_violation_total++" in text
    assert "floored at zero" not in text


def test_one_record_is_copied_whole_and_never_per_cell_summed() -> None:
    module = MODULE.read_text()
    census = CENSUS.read_text()
    builder = BUILDER.read_text()
    assert "bridge->fl4684_active_stroke_reachability =\n" in module
    assert "st->fl4684_active_stroke_reachability;" in module
    assert "c->active_stroke_latest = *stroke;" in census
    assert "c->active_stroke_latest +=" not in census
    assert "Fl4684EmissionCensusObserveSceneFactV1(&out," in builder
    assert "Fl4684ActiveStrokeReachabilityTotalsV1" in TOTALS.read_text()


def test_snapshot_refresh_is_not_gated_by_frame_identity() -> None:
    text = CENSUS.read_text()
    overwrite = text.index("c->active_stroke_latest = *stroke;")
    frame_gate = text.index("if (fact->frame_id != c->last_frame_id)")
    assert overwrite < frame_gate


def test_observation_record_is_not_read_by_render_decisions() -> None:
    source = WORLD.read_text()
    # All source references are writes through the local totals alias or direct
    # increments. The record is never used as a branch condition.
    assert "if (r->fl4684_active_stroke_reachability" not in source
    assert "runtime_consumption_allowed" not in TOTALS.read_text()
    assert CENSUS.read_text().upper().count("OBSERVATION ONLY") >= 1


def test_retired_three_field_design_is_absent() -> None:
    repository_text = "\n".join(
        path.read_text()
        for path in (TOTALS, CENSUS, WORLD, INTERNAL, BRIDGE, RESOLVE,
                     MODULE, BUILDER, MEASURER)
    )
    assert "fl4684_active_stroke_finalizer_call_total" not in repository_text
    assert "Fl4684ActiveStrokeStageSampleV1" not in repository_text
