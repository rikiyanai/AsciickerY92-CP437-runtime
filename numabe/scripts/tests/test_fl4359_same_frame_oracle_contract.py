#!/usr/bin/env python3
"""Outcome contract for the FL-4359 same-frame direct-ray oracle.

The oracle must observe the current VSB ray and SceneGeometryDB publication.
It must never become a historical comparator or a visible geometry owner.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VSB = ROOT / "godot_project/render/backends/ortho/visibility/visible_surface_builder.glsl"
PUBLISH = ROOT / "godot_project/render/backends/perspective/publish/perspective_probe_payload_publish.glsl"
CAPTURE = ROOT / "godot_project/render/diagnostics/experiments/fl4359_sparse_surface_capture.glsl"
COMPOSITOR = ROOT / "godot_project/render/ascii/raster/ascii_compositor.glsl"
EFFECT = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
PIPELINE = ROOT / "godot_project/render/backends/perspective/PerspectiveProbeBackend.gd"
BUILDER = ROOT / "godot_project/render/core/contracts/visibility/VisibleSurfaceABI.gd"
DRIVER = ROOT / "scripts/adhoc/fl4359_external_movement_driver.gd"
RUNNER = ROOT / "scripts/run_fl4359_headed_movement.py"
ANALYZER = ROOT / "scripts/analyze_fl4359_moving_camera.py"


def _text(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    if path.suffix not in {".glsl", ".glslinc"}:
        return source
    def expand(match):
        included = ROOT / "godot_project" / match.group(1)
        return _text(included)
    return re.sub(r'^\s*#include "res://([^"]+)"\s*$', expand, source, flags=re.MULTILINE)


def _load_analyzer():
    spec = importlib.util.spec_from_file_location("fl4359_oracle_analyzer", ANALYZER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_runner():
    scripts_path = str(ROOT / "scripts")
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    spec = importlib.util.spec_from_file_location("fl4359_oracle_runner", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _terminal_trace(frame_index: int, matrix: list[float] | None = None) -> dict:
    return {
        "world_to_clip_matrix": matrix or [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ],
        "target_size": [100, 100],
        "cell_size": [10, 10],
        "semantic_ansi_size": [10, 10],
        "readback_flip_y": False,
        "frame_index": frame_index,
        "sequence_frame": frame_index,
        "authority_server_tick": 100 + frame_index,
        "camera_hash": 1000 + frame_index,
        "scene_revision": 7,
        "view_revision": 8,
        "dynamic_revision": 9,
    }


def _terminal_frame(
    analyzer,
    frame_index: int,
    cells_and_keys: list[tuple[tuple[int, int], list[list[int]]]],
    *,
    glyph: int = 65,
    validity: int = 0x1 | (5 << 16),
    matrix: list[float] | None = None,
) -> list[dict]:
    trace = _terminal_trace(frame_index, matrix)
    rows: list[dict] = []
    oracle_identity = {
        "owner_kind": 1,
        "object_id": 2,
        "primitive_id": 3,
        "material_id": 4,
    }
    for (cell_x, cell_y), key in cells_and_keys:
        for slot in range(4):
            sample_index = (
                (cell_y * 2 + (slot >> 1)) * 20
                + cell_x * 2
                + (slot & 1)
            )
            rows.append({
                "frame_trace": dict(trace),
                "screen_cell": {"x": cell_x, "y": cell_y},
                "sample_index": sample_index,
                "subcell_slot": slot,
                "final_paint_elected_identity_available": True,
                "final_paint_elected_identity_validity": validity,
                "final_paint_elected_identity_glyph": glyph,
                "final_paint_elected_identity_priority": 3,
                "final_paint_elected_identity_slot": 0,
                "final_paint_elected_identity_key_a": list(key[0]),
                "final_paint_elected_identity_key_b": list(key[1]),
                "final_paint_elected_identity_key_c": list(key[2]),
                "final_paint_elected_identity_key_d": list(key[3]),
                "final_paint_glyph": glyph,
                "final_paint_terminal_occurrence_valid": True,
                "final_paint_terminal_mask_ink_count": 1,
                "final_paint_terminal_mask_sum_x": cell_x * 10,
                "final_paint_terminal_mask_sum_y": cell_y * 10,
                "final_paint_terminal_mask_min_x": cell_x * 10,
                "final_paint_terminal_mask_min_y": cell_y * 10,
                "final_paint_terminal_mask_max_x": cell_x * 10,
                "final_paint_terminal_mask_max_y": cell_y * 10,
                "final_paint_terminal_mask_digest": 77,
                "oracle_query_valid": True,
                "oracle_direct_hit": True,
                "oracle_identity_agrees_with_selected_surface": True,
                "oracle_identity": dict(oracle_identity),
                "oracle_stable_world_anchor": [1.0, 2.0, 3.0],
                "oracle_world_position": [0.0, 0.0, 0.0],
            })
    return rows


def _terminal_key(seed: int) -> list[list[int]]:
    return [
        [seed, 0, 0, 0],
        [seed, 1, 0, 0],
        [seed, 2, 0, 0],
        [seed, 3, 0, 0],
    ]


def test_terminal_expected_cell_synthetic_trajectory_and_content_split() -> None:
    analyzer = _load_analyzer()
    key = _terminal_key(11)
    frame_a = _terminal_frame(analyzer, 0, [((5, 5), key)])
    translated = _terminal_trace(1)
    translated["world_to_clip_matrix"][12] = 0.2
    frame_b = _terminal_frame(
        analyzer,
        1,
        [((6, 5), key)],
        validity=0x1 | (5 << 16) | (1 << 24),
        matrix=translated["world_to_clip_matrix"],
    )
    result = analyzer.summarize_terminal_expected_reprojection([frame_a, frame_b])
    assert result["valid"] is True
    assert result["expected_cell_address_pass"] is True
    assert result["counts"].get("expected_cell_mismatches", 0) == 0
    assert result["counts"]["comparable_expected_cells"] == 2

    changed_glyph = _terminal_frame(analyzer, 1, [((5, 5), key)], glyph=66)
    morphology = changed_glyph
    for row in morphology:
        row["final_paint_terminal_mask_digest"] = 78
    result = analyzer.summarize_terminal_expected_reprojection([frame_a, morphology])
    assert result["valid"] is True
    assert result["expected_cell_address_pass"] is True
    assert result["counts"]["mask_morphology_changed_displacements"] == 1
    assert result["invalid"] == {}


def test_terminal_expected_cell_synthetic_freeze_jump_is_categorical_mismatch() -> None:
    analyzer = _load_analyzer()
    key = _terminal_key(21)
    other = _terminal_key(22)
    frame_a = _terminal_frame(analyzer, 0, [((5, 5), key)])
    # The expected cell remains present with a different surface. The elected
    # source token is painted one cell away, so this is a positional mismatch,
    # not an omitted reference sample or an ambiguous target occurrence.
    moving_trace = _terminal_trace(1)
    moving_trace["world_to_clip_matrix"][12] = 0.2
    frame_b = _terminal_frame(
        analyzer,
        1,
        [((6, 5), other), ((5, 5), key)],
        matrix=moving_trace["world_to_clip_matrix"],
    )
    result = analyzer.summarize_terminal_expected_reprojection([frame_a, frame_b])
    assert result["valid"] is True
    assert result["expected_cell_address_pass"] is False
    assert result["counts"]["expected_cell_mismatches"] == 1


def test_terminal_expected_cell_synthetic_omission_duplicate_and_zero_population_fail_closed() -> None:
    analyzer = _load_analyzer()
    key = _terminal_key(31)
    other = _terminal_key(32)
    frame_a = _terminal_frame(analyzer, 0, [((5, 5), key)])

    omitted = _terminal_frame(analyzer, 1, [((5, 5), other)])
    result = analyzer.summarize_terminal_expected_reprojection([frame_a, omitted])
    assert result["valid"] is False
    assert result["invalid"]["terminal_omission"] == 1

    duplicate = _terminal_frame(
        analyzer,
        1,
        [((5, 5), key), ((6, 5), key)],
    )
    result = analyzer.summarize_terminal_expected_reprojection([frame_a, duplicate])
    assert result["valid"] is False
    assert result["invalid"]["terminal_duplicate_or_ambiguous"] == 1

    center_only = _terminal_frame(
        analyzer,
        0,
        [((5, 5), key)],
        validity=0x1 | (5 << 16) | (1 << 24),
    )
    center_later = _terminal_frame(
        analyzer,
        1,
        [((5, 5), key)],
        validity=0x1 | (5 << 16) | (1 << 24),
    )
    result = analyzer.summarize_terminal_expected_reprojection([center_only, center_later])
    assert result["valid"] is False
    assert result["counts"].get("eligible_source_features", 0) == 0
    assert result["expected_cell_address_pass"] is False

    foreign_occurrence = _terminal_frame(analyzer, 1, [((6, 5), key)])
    for row in foreign_occurrence:
        row["final_paint_terminal_mask_sum_x"] = 50
        row["final_paint_terminal_mask_min_x"] = 50
        row["final_paint_terminal_mask_max_x"] = 50
    result = analyzer.summarize_terminal_expected_reprojection(
        [frame_a, foreign_occurrence]
    )
    assert result["valid"] is False
    assert result["invalid"]["terminal_occurrence_invalid"] == 1
    assert result["invalid"][
        "terminal_occurrence_invalid:occurrence_outside_declared_cell"
    ] == 1


def test_sparse_capture_two_buffer_ceiling_covers_current_full_grid_record_size() -> None:
    effect = _text(EFFECT)
    rows = 113 * 74 * 4
    record_bytes = 536 * 4
    header_bytes = 16
    two_buffers = 2 * (header_bytes + rows * record_bytes)
    ceiling = 160 * 1024 * 1024
    assert two_buffers == 143_425_056
    assert two_buffers <= ceiling
    assert two_buffers > 128 * 1024 * 1024
    assert "FL4359_SPARSE_CAPTURE_MAX_OUTPUT_BYTES := 160 * 1024 * 1024" in effect


def test_full_grid_gate_rejects_missing_or_red_terminal_cell_address_gate() -> None:
    runner = _load_runner()
    required = {
        "evidence_same_frame_oracle_spatial_rows_valid",
        "evidence_same_frame_oracle_query_complete",
        "gameplay_same_frame_geometry_agrees",
        "gameplay_same_frame_product_and_paint_presence_agree",
        "evidence_same_frame_oracle_exhaustive_frame_coverage",
        "evidence_same_frame_oracle_temporal_continuity",
        "evidence_full_grid_temporal_oracle_valid",
        "evidence_full_grid_identity_normalized",
        "evidence_full_grid_rows_classified",
        "evidence_full_grid_raster_witness_valid",
        "evidence_full_grid_downstream_exact_identity",
        "evidence_full_grid_exact_current_ray_differences_reported",
        "gameplay_full_grid_zero_structural_geometry_errors",
        "gameplay_full_grid_zero_changing_reference_state",
        "gameplay_full_grid_exact_product_translation",
        "gameplay_full_grid_zero_glyph_mask_splits",
        "gameplay_cell_mask_owner_stability_bounded",
        "gameplay_full_grid_zero_duplicate_out_of_place",
        "evidence_full_grid_first_divergence_known",
        "evidence_presentation_observer_population_accounted",
        "evidence_presentation_observer_sensitivity_valid",
        "gameplay_moving_camera_jitter_bounded",
        "gameplay_content_shimmer_bounded",
        "gameplay_continuous_product_colour_stability_bounded",
        "evidence_terminal_expected_reprojection_valid",
        "evidence_celestial_time_fixed",
        "gameplay_terminal_expected_cell_address_exact",
        "evidence_product_owner_name_transport_valid",
        "evidence_terrain_product_homogeneity_present",
        "evidence_terrain_product_final_paint_join_complete",
        "gameplay_terrain_product_distribution_non_degenerate",
        "evidence_terrain_product_homogeneity_baseline_calibrated",
        "evidence_product_admission_contingency_valid",
        "gameplay_product_admission_chain_exact",
    }
    summary = {
        "proof_profile": "full_grid_temporal_oracle_v1",
        "gates": {name: {"pass": True} for name in required},
    }
    summary["gates"]["all_pass"] = True
    assert runner._analysis_gates_accept_proof(summary) is True
    del summary["gates"]["gameplay_terminal_expected_cell_address_exact"]
    assert runner._analysis_gates_accept_proof(summary) is False
    summary["gates"]["gameplay_terminal_expected_cell_address_exact"] = {"pass": False}
    assert runner._analysis_gates_accept_proof(summary) is False


def test_full_grid_runner_requires_fixed_day_cycle_time() -> None:
    runner = _load_runner()
    args = SimpleNamespace(
        analyzer_proof_profile="full_grid_temporal_oracle_v1",
        day_cycle_time=None,
    )
    assert runner._full_grid_day_cycle_time_valid(args) is False
    args.day_cycle_time = 0.5
    assert runner._full_grid_day_cycle_time_valid(args) is True
    args.day_cycle_time = 1.0
    assert runner._full_grid_day_cycle_time_valid(args) is False
    args.day_cycle_time = 0.75
    assert runner._full_grid_day_cycle_time_valid(args) is False
    args.analyzer_proof_profile = "airplane_temporal_v1"
    args.day_cycle_time = None
    assert runner._full_grid_day_cycle_time_valid(args) is True


def _valid_runtime_celestial_receipt() -> dict:
    return {
        "day_cycle_time": 0.5,
        "day_cycle_time_pin_valid": True,
        "celestial_runtime_witness_valid": True,
        "celestial_witness_invalid_count": 0,
        "celestial_witness_change_count": 0,
        "capture_schedule": {"effective_active_frames": 5},
        "celestial_frame_witnesses": [
            {
                "frame_index": index,
                "sequence_frame": index,
                "celestial_time": 0.5,
                "celestial_night_factor": 0.5,
                "celestial_sun_direction": [1.0, 0.0, 0.0],
                "celestial_witness_valid": True,
            }
            for index in range(5)
        ],
    }


def test_full_grid_runner_rejects_missing_or_changing_runtime_celestial_witness() -> None:
    runner = _load_runner()
    args = SimpleNamespace(
        analyzer_proof_profile="full_grid_temporal_oracle_v1",
        day_cycle_time=0.5,
    )
    receipt = _valid_runtime_celestial_receipt()
    assert runner._full_grid_runtime_celestial_receipt_accepts(receipt, args) is True

    missing = copy.deepcopy(receipt)
    missing.pop("celestial_frame_witnesses")
    assert runner._full_grid_runtime_celestial_receipt_accepts(missing, args) is False

    changed = copy.deepcopy(receipt)
    changed["celestial_frame_witnesses"][3]["celestial_sun_direction"] = [0.0, 1.0, 0.0]
    assert runner._full_grid_runtime_celestial_receipt_accepts(changed, args) is False

    wrong_pin = copy.deepcopy(receipt)
    wrong_pin["day_cycle_time"] = 0.75
    assert runner._full_grid_runtime_celestial_receipt_accepts(wrong_pin, args) is False


def test_stationary_calibration_driver_is_five_frame_observation_only() -> None:
    driver = _text(DRIVER)
    run_start = driver.index("func _run_stationary_terminal_reprojection_calibration(")
    run_end = driver.index("func _run_ghost_replay(", run_start)
    stationary = driver[run_start:run_end]
    pose_start = driver.index("func _stationary_pose_contract(")
    pose_end = driver.index("func _vec3_to_vector(", pose_start)
    pose_contract = driver[pose_start:pose_end]
    assert "begin_fl4359_capture_identity_observation" in stationary
    assert "finish_fl4359_capture_identity_observation" in stationary
    assert "_stationary_pose_contract(_stationary_frame_samples)" in stationary
    assert '"day_cycle_time_pin_invalid"' in stationary
    assert '"stationary_capture_schedule_incomplete"' in stationary
    assert '"stationary_pose_contract_failed"' in stationary
    assert '"celestial_runtime_witness_missing_or_invalid_frame_%d"' in pose_contract
    assert '"celestial_runtime_witness_drift_frame_%d"' in pose_contract
    assert '"celestial_time"' in driver
    assert '"celestial_night_factor"' in driver
    assert '"celestial_sun_direction"' in driver
    assert '"player_pose_drift_frame_%d"' in pose_contract
    assert '"camera_matrix_drift_frame_%d"' in pose_contract
    assert '"world_revision_drift_frame_%d"' in pose_contract
    assert '"product_time_drift_frame_%d"' in pose_contract
    assert '_notify(main' not in stationary
    normal_route = driver[driver.index("var player_before :="):run_start]
    assert "_notify(main, plane_intent)" in normal_route


def test_stationary_calibration_receipt_accepts_literal_zero_error_counts() -> None:
    runner = _load_runner()
    args = SimpleNamespace(
        fl4359_terminal_reprojection_stationary_calibration=True,
        analyzer_proof_profile="full_grid_temporal_oracle_v1",
        day_cycle_time=0.5,
    )
    receipt = _valid_runtime_celestial_receipt()
    receipt.update(
        {
            "calibration_only": True,
            "proof": runner.STATIONARY_TERMINAL_REPROJECTION_CALIBRATION,
            "route_identity": runner.SCRIPTED_AIRBORNE_AIRPLANE_ROUTE_IDENTITY,
            "calibration_of_route": runner.SCRIPTED_AIRBORNE_AIRPLANE_ROUTE_IDENTITY,
            "camera_mode": "plane_air",
            "expected_projection": "perspective",
            "product_time_pin_valid": True,
            "sparse_lifecycle_facts_valid": True,
            "sparse_capture_drain_pass": True,
            "sparse_capture_pending_after_drain": False,
            "sparse_capture_failed_request_count": 0,
            "sparse_capture_join_invalid_rows": 0,
            "sparse_capture_written_row_count": 160460,
            "stationary_frame_samples": [{"sequence_frame": index} for index in range(5)],
            "stationary_pose_contract": {"pass": True},
        }
    )
    receipt["capture_schedule"].update(
        {
            "observed_active_frames": 5,
            "schedule_complete": True,
        }
    )
    assert runner._stationary_calibration_receipt_accepts(receipt, args) is True

    receipt["sparse_capture_failed_request_count"] = 1
    assert runner._stationary_calibration_receipt_accepts(receipt, args) is False


def test_stationary_summary_requires_valid_exact_terminal_occurrences() -> None:
    runner = _load_runner()
    summary = {
        "proof_profile": "full_grid_temporal_oracle_v1",
        "frame_count": 5,
        "full_grid_temporal_oracle_valid": True,
        "same_frame_oracle_temporal_continuity_valid": True,
        "same_frame_oracle_fixed_day_cycle_time_valid": True,
        "required_field_coverage": {"all_present": True},
        "product_anchor_provenance_complete": True,
        "terminal_expected_reprojection": {
            "valid": True,
            "expected_cell_address_pass": True,
            "counts": {
                "comparable_terminal_displacements": 4,
                "comparable_expected_cells": 5,
                "expected_cell_mismatches": 0,
            },
            "invalid": {},
        },
    }

    assert runner._stationary_calibration_summary_accepts(summary) is True

    for mutation in (
        {"valid": False},
        {"expected_cell_address_pass": False},
        {"counts": {"comparable_terminal_displacements": 0}},
        {"counts": {"comparable_expected_cells": 0}},
        {"counts": {"expected_cell_mismatches": 1}},
        {"invalid": {"terminal_occurrence_invalid": 1}},
    ):
        mutant = json.loads(json.dumps(summary))
        terminal = mutant["terminal_expected_reprojection"]
        if "counts" in mutation:
            terminal["counts"].update(mutation["counts"])
        else:
            terminal.update(mutation)
        assert runner._stationary_calibration_summary_accepts(mutant) is False


def _compile(shader: Path, define: str | None = None) -> None:
    source = _text(shader).removeprefix("#[compute]\n")
    with tempfile.NamedTemporaryFile("w", suffix=".comp", delete=False) as handle:
        handle.write(source)
        source_path = Path(handle.name)
    spirv_path = source_path.with_suffix(".spv")
    try:
        command = [
                "glslangValidator",
                "-V",
                "--target-env",
                "vulkan1.2",
                "-S",
                "comp",
        ]
        if define is not None:
            command.append(f"-D{define}")
        command.extend([str(source_path), "-o", str(spirv_path)])
        result = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        assert result.returncode == 0, result.stdout
    finally:
        source_path.unlink(missing_ok=True)
        spirv_path.unlink(missing_ok=True)


def test_oracle_shaders_compile() -> None:
    _compile(VSB)
    _compile(CAPTURE)
    _compile(PUBLISH)
    _compile(COMPOSITOR)
    _compile(COMPOSITOR, "FL4431_FINAL_CELL_CAPTURE")


def test_product_colour_witness_validity_uses_terminal_compiler_row() -> None:
    effect = _text(EFFECT)
    validity = effect[effect.index("var product_terrain_colour_witness_valid") :]
    validity = validity[: validity.index("var b64_owner_wire")]
    assert '"final_compiler_bg_validity"' in validity
    assert '"palette_bg_validity"' not in validity


def test_oracle_observes_the_current_vsb_invocation_without_authoring_visibility() -> None:
    vsb = _text(VSB)
    effect = _text(EFFECT)
    builder = _text(BUILDER)
    driver = _text(DRIVER)
    assert "layout(set = 0, binding = 85, std140) uniform Fl4359OracleControl" in vsb
    assert "layout(set = 0, binding = 86, std430) buffer Fl4359OracleRecords" in vsb
    diagnostic = vsb.split("if (push_data.projection_kind != PROJECTION_ORTHOGRAPHIC)", 1)[1].split("    SceneRay ray;", 1)[0]
    assert "query_valid = fl4359_oracle_query_surface(diagnostic, observed)" in diagnostic
    assert "fl4359_oracle_write(record_index, query_valid," in diagnostic
    assert "hit_record(observed)" not in diagnostic
    assert "STATUS_RAY_INVALID" in diagnostic
    assert "fl4359_oracle_query_failure_reason" in vsb
    assert "FL4359_ORACLE_QUERY_FAILURE_DYNAMIC_FACE_NONFINITE" in vsb
    assert "oracle_query_failure_reason" in effect
    assert '"oracle_query_failure_name"' in driver
    assert "fl4359_oracle_records.records[record_index] = record;" in vsb
    assert "hit_record(oracle_direct_hit)" not in vsb
    assert "nearest = oracle_direct_hit" not in vsb
    assert "BINDING_FL4359_ORACLE_CONTROL := 85" in builder
    assert "BINDING_FL4359_ORACLE_RECORDS := 86" in builder
    assert "FL4359_ORACLE_RECORD_WORDS := 60" in builder
    # FL-4359 cutover: the expected-centre replay (b61/b62) is deleted, so the
    # sentinel address initialisation appears once, in fl4359_oracle_write.
    assert vsb.count(
        "UINT_SENTINEL, UINT_SENTINEL, UINT_SENTINEL, 0u"
    ) >= 1
    assert (
        "record.expected_center_address_state = uvec4(UINT_SENTINEL);"
        not in vsb
    )
    assert "_fl4359_sparse_capture_active" in effect[
        effect.index("func _build_vsb_view_dispatch(") :
        effect.index("func _ensure_final_cell_paint_buffer", effect.index("func _build_vsb_view_dispatch("))
    ]
    assert "same_frame_oracle_enabled" in effect
    for forbidden in ("August 5", "1c3ecfe01"):
        assert forbidden not in vsb
        assert forbidden not in effect


def test_sparse_recorder_joins_current_sources_publication_and_actual_vertices() -> None:
    capture = _text(CAPTURE)
    vsb = _text(VSB)
    effect = _text(EFFECT)
    driver = _text(DRIVER)
    publish = _text(PUBLISH)
    builder = _text(BUILDER)
    assert "CAPTURE_RECORD_WORDS = CAPTURE_RECORD_V17_WORDS" in capture
    assert "CAPTURE_RECORD_V17_WORDS = 604u" in capture
    assert "layout(set = 0, binding = 86, std430) readonly buffer" in capture
    assert "fl4359_oracle_records.records[sample_index]" in capture
    assert "CAPTURE_ORACLE_OFFSET_WORDS = 96u" in capture
    assert "CAPTURE_SELECTED_WORLD_POSITION_OFFSET_WORDS = 116u" in capture
    assert "FL4359_SPARSE_CAPTURE_RECORD_WORDS := 604" in effect
    assert "CAPTURE_FINAL_PAINT_EXTENDED_OFFSET_WORDS = 272u" in capture
    assert "FL4359_SPARSE_CAPTURE_FINAL_PAINT_EXTENDED_OFFSET_WORDS := 272" in effect
    assert "CAPTURE_FINAL_PAINT_SLOT_COLOUR_OFFSET_WORDS = 478u" in capture
    assert "FL4359_SPARSE_CAPTURE_FINAL_PAINT_SLOT_COLOUR_OFFSET_WORDS := 478" in effect
    assert "binding = 102, std430" in capture
    assert "probe_raster_contender_count" in effect
    assert "probe_source_capture_physical_layer" in effect
    assert "--fl4359-probe-candidate-diagnostics" in _text(RUNNER)
    assert "layout(set = 0, binding = 87) uniform usampler2D prepack_source" in capture
    assert 'var probe_candidate_binding := "deleted"' in effect
    assert 'var prepack_names := ["source_reference", "hardware_depth"]' in effect
    assert "probe_capture_attachment_rids" in effect
    assert '"probe_source_capture_identity"' in effect
    assert '"probe_reconstructed_world_corners"' in effect
    assert '"probe_reconstructed_clip_corners"' in effect
    assert '"probe_raster_candidate_source_contender_count"' in effect
    assert "camera_view_projection: Projection" in effect
    assert "bytes.resize(128)" in effect
    assert "var capture_control := 3" in effect
    assert "effective_face_masks" in effect
    assert "CAPTURE_EXPECTED_RASTER_STATE_OFFSET_WORDS = 334u" in capture
    assert "CAPTURE_EXPECTED_RASTER_CAPTURE_IDENTITY_OFFSET_WORDS = 370u" in capture
    assert "CAPTURE_EXPECTED_CENTER_ORACLE_OFFSET_WORDS = 374u" in capture
    assert "CAPTURE_EXPECTED_CENTER_CAPTURE_IDENTITY_OFFSET_WORDS = 390u" in capture
    assert "CAPTURE_EXPECTED_CENTER_CAPTURE_STATE_OFFSET_WORDS = 394u" in capture
    assert "CAPTURE_EXPECTED_CENTER_NEIGHBOR_ADDRESS_OFFSET_WORDS = 398u" in capture
    assert "CAPTURE_EXPECTED_CENTER_CAPTURE_REVISIONS_OFFSET_WORDS = 406u" in capture
    assert "fl4359_publication_sha256_by_family" in effect
    assert "captured_publication_sha256_by_layer" in effect
    assert "_captured_publication_sha256_by_layer" in _text(PIPELINE)
    assert "captured_submission_context_by_layer" in effect
    assert "_captured_submission_context_by_layer" in _text(PIPELINE)
    assert "begin_fl4359_capture_identity_observation" in effect
    assert "finish_fl4359_capture_identity_observation" in effect
    assert "reset_fl4359_capture_identity_context" in _text(PIPELINE)
    assert "begin_fl4359_capture_identity_observation" in driver
    assert "finish_fl4359_capture_identity_observation" in driver
    assert "_fl4359_capture_identity_active" in effect
    assert "if not bytes.is_empty():" in effect
    assert "if not extra_bytes.is_empty():" in _text(PIPELINE)
    assert "visible_tile_ids_bytes" in _text(PIPELINE)
    assert "BINDING_HEIGHTFIELD_HEIGHT_TEXTURE" in effect
    assert "BINDING_HEIGHTFIELD_MATERIAL_TEXTURE" in effect
    assert "BINDING_DYNAMIC_GEOMETRY_DESCRIPTOR" in effect
    assert '"corner_texture_sha256": corner_texture_sha256' in effect
    assert '"material_texture_sha256": material_texture_sha256' in effect
    assert 'heightfield_facts["corner_texture_sha256"]' in effect
    assert 'heightfield_facts["material_texture_sha256"]' in effect
    assert "corner_texture_sha256," in effect
    assert "material_texture_sha256," in effect
    assert "bool reconstruct_source_quad(" in capture
    assert "bool quad_covers_subcell(" in capture
    assert "bool expected_previous_suppressed(" in capture
    assert "oracle.expected_visibility_masks.z & probe_bit" in capture
    assert "all(equal(captured_identity, oracle.identity))" in capture
    assert "probe_expected_raster_record_valid" in effect
    assert "probe_expected_raster_cover_mask" in effect
    assert "probe_expected_raster_record_valid" in driver
    assert "probe_expected_raster_cover_mask" in driver
    assert '"probe_source_oracle_identity"' in effect
    assert "CURRENT and PREVIOUS are logical owners" in capture
    assert "mapped_revisions.w == source_generation" in capture
    assert "all(equal(mapped_identity, raster_identity))" in capture
    assert "source_revisions_value.w & 0x7fffffffu" in capture
    assert "oracle.binding = FL4359_SPARSE_CAPTURE_ORACLE_BINDING" in effect
    # The b86 record length (60 words) is independent from push-constant
    # offsets.  Offset 44 is the oracle binding lane; offset 60 is the
    # capture-control lane and must never be conflated with the record length.
    assert "FL4359_ORACLE_RECORD_WORDS := 60" in builder
    assert "bytes.encode_u32(44, oracle_binding_mode)" in effect
    assert "bytes.encode_u32(48, int(capture_bands[0]))" in effect
    assert "bytes.encode_u32(52, int(capture_bands[1]))" in effect
    assert "bytes.encode_u32(56, int(capture_bands[2]))" in effect
    assert "bytes.encode_u32(60, capture_control)" in effect
    assert "bytes.encode_u32(60, oracle_binding_mode)" not in effect
    assert '"same_frame_oracle_binding": "vsb_b86_same_frame"' in effect
    assert 'result["schema"] = "fl4359_sparse_surface_capture.v17"' in effect
    # The removed candidate/centre replay must stay explicitly unavailable.
    assert "record.expected_visibility_masks = uvec4(0u)" in vsb
    assert "fl4359_oracle_query_probe_domain(" not in vsb
    query = vsb.split("bool fl4359_oracle_query_surface(", 1)[1].split("bool fl4359_oracle_enabled(", 1)[0]
    for family in ("dynamic_tlas", "static_tlas", "heightfield", "water"):
        assert "scene_query_" + family + "(" in query
    assert '"sampling_policy": sampling_policy' in effect
    assert '"exhaustive_all_subcells"' in effect
    assert "result.records[cell.y * params.cells.x + cell.x] = record" in publish
    assert "record.sources = uvec4(chosen_reference, paper_reference," in publish
    assert "CAPTURE_ACTUAL_VERTEX_WITNESS_OFFSET_WORDS = 584u" in capture
    assert "CAPTURE_ACTUAL_VERTEX_WITNESS_WORDS = 20u" in capture
    assert '"probe_candidate_pack_rejection_reason"' in effect
    assert '"probe_candidate_pack_rejection_reason"' in driver
    assert '"oracle_world_stride": _fl4359_sparse_capture_world_stride' in effect
    assert '"oracle_screen_stride": _fl4359_sparse_capture_screen_stride' in effect
    for field in (
        "oracle_record_valid",
        "oracle_query_valid",
        "oracle_query_failure_reason",
        "oracle_query_failure_name",
        "oracle_identity",
        "oracle_selected_depth_abs_delta",
        "oracle_classification",
    ):
        assert f'"{field}"' in effect
        assert f'"{field}"' in driver

    # The GPU/host evidence must survive the final JSONL transport. A prior
    # omission here turned real stage data into 54,952 uninstrumented rows.
    for field in (
        "probe_prepack_valid",
        "probe_source_capture_valid",
        "probe_reconstructed_world_corners",
        "probe_reconstructed_clip_corners",
        "probe_reconstructed_clip_valid",
        "probe_source_oracle_identity",
        "probe_expected_visibility_identity_match_mask",
        "probe_expected_capture_nearby_match_mask",
        "probe_source_capture_physical_layer",
        "probe_source_capture_join_flags",
        "probe_source_current_revisions",
        "probe_source_capture_origin",
        "probe_source_assigned_origin",
        "probe_source_capture_origin_valid",
        "probe_source_capture_face_admitted",
        "probe_source_capture_publication_context",
        "probe_source_capture_submission_context",
        "probe_raster_contender_count",
        "probe_raster_structural_identity_contender_count",
        "probe_raster_candidate_source_contender_count",
        "probe_raster_candidate_source_nearest_depth",
        "probe_raster_candidate_source_farthest_depth",
        "probe_raster_candidate_source_nearest_source",
        "probe_expected_center_record_valid",
        "probe_expected_center_applicable",
        "probe_expected_center_address_valid",
        "probe_expected_center_query_valid",
        "probe_expected_center_direct_hit",
        "probe_expected_center_source_probe",
        "probe_expected_center_face",
        "probe_expected_center_texel",
        "probe_expected_center_identity",
        "probe_expected_center_world_position",
        "probe_expected_center_ray_depth",
        "probe_expected_center_query_chebyshev_depth",
        "probe_expected_center_direct_angular_residual",
        "probe_expected_center_direct_chebyshev_depth",
        "probe_expected_center_capture_identity",
        "probe_expected_center_capture_physical_layer",
        "probe_expected_center_capture_generation",
        "probe_expected_center_capture_revisions",
        "probe_expected_center_current_revisions",
        "probe_expected_center_capture_origin",
        "probe_expected_center_assigned_origin",
        "probe_expected_center_capture_origin_valid",
        "probe_expected_center_allocation_context",
        "probe_expected_center_publication_context",
        "probe_expected_center_submission_context",
        "probe_expected_center_expected_physical_layer",
        "probe_expected_center_physical_layer_matches",
        "probe_expected_center_face_admitted",
        "probe_expected_center_capture_valid",
        "probe_expected_center_capture_matches_query",
        "probe_expected_center_capture_matches_direct",
        "probe_expected_center_capture_chebyshev_depth",
        "probe_expected_center_neighbor_valid",
        "probe_expected_center_neighbor_dx",
        "probe_expected_center_neighbor_dy",
        "probe_expected_center_neighbor_texel",
        "probe_expected_center_neighbor_generation",
        "probe_expected_center_capture_depth_abs_residual",
        "probe_expected_center_capture_depth_relative_residual",
        "probe_expected_center_neighbor_angular_residual",
        "probe_expected_center_neighbor_depth_relative_residual",
        "final_product_exact_identity",
        "final_paint_expected_atlas_mask_hash",
        "final_paint_actual_mask_hash",
        "final_paint_mask_source_key",
        "final_paint_expected_atlas_mask_pixel_count",
        "final_paint_actual_atlas_mask_pixel_count",
        "final_paint_atlas_mask_dimensions",
        "final_paint_atlas_mask_unknown_bits",
        "final_paint_complete_atlas_mask",
    ):
        assert f'"{field}",' in driver
    assert "out[stage_field] = row.get(stage_field, null)" in driver


def test_downstream_exact_identity_and_complete_mask_fail_closed_independently() -> None:
    analyzer = _load_analyzer()
    rows = _full_grid_rows(12, 0)
    for row in rows:
        row["final_product_exact_identity"] = {
            "exact_product_identity_available": True,
            "valid": True,
            "product_translation_equal": True,
            "product_translation_kind": "identity_preserving",
            "cell_mask_winner_available": True,
            "cell_mask_winner_source_kind": "quarter_slot",
            "cell_mask_winner_glyph": 35,
            "cell_mask_winner_key": {
                "key_a": [1, 2, 3, 4],
                "key_b": [5, 6, 7, 8],
                "key_c": [9, 10, 11, 12],
                "key_d": [13, 14, 15, 16],
            },
            "cell_mask_winner_source_semantic_key": {
                "key_a": [1, 2, 3, 4],
                "key_b": [5, 6, 7, 8],
                "key_c": [9, 10, 11, 12],
                "key_d": [13, 14, 15, 16],
            },
            "cell_mask_winner_source_anchor_bits": [13, 14, 15],
        }
        row["final_paint_glyph"] = 35
        row["final_paint_expected_atlas_mask_hash"] = 0x12345678
        row["final_paint_actual_mask_hash"] = 0x12345678
        row["final_paint_mask_source_key"] = dict(
            row["final_product_exact_identity"]["cell_mask_winner_key"]
        )
        row["final_paint_expected_atlas_mask_pixel_count"] = 17
        row["final_paint_actual_atlas_mask_pixel_count"] = 17
        row["final_paint_atlas_mask_dimensions"] = [8, 16]
        row["final_ink_pixels"] = 128
        row["final_paint_atlas_mask_unknown_bits"] = 0
        row["final_paint_complete_atlas_mask"] = True
    summary = analyzer.summarize_full_grid_temporal_oracle(
        rows, runtime_receipt=_valid_temporal_receipt()
    )
    assert summary["full_grid_temporal_oracle_product_exact_identity_available"] is True
    assert summary["full_grid_temporal_oracle_product_translation_error_rows"] == 0
    assert summary["full_grid_temporal_oracle_product_translation_kinds"] == {
        "identity_preserving": 4
    }
    assert summary["full_grid_temporal_oracle_complete_glyph_mask_hash_available"] is True
    assert summary["full_grid_temporal_oracle_complete_glyph_mask_rows"] == 4
    assert summary["full_grid_temporal_oracle_incomplete_glyph_mask_rows"] == 0

    rows[0]["final_paint_actual_mask_hash"] ^= 1
    rows[0]["final_paint_complete_atlas_mask"] = False
    failed = analyzer.summarize_full_grid_temporal_oracle(
        rows, runtime_receipt=_valid_temporal_receipt()
    )
    assert failed["full_grid_temporal_oracle_complete_glyph_mask_hash_available"] is True
    assert failed["full_grid_temporal_oracle_incomplete_glyph_mask_rows"] == 1
    assert failed["full_grid_temporal_oracle_first_divergence"][
        "compositor_glyph_mask"
    ] == 1

    rows[0]["final_paint_actual_mask_hash"] = 0x12345678
    rows[0]["final_paint_complete_atlas_mask"] = True
    rows[0]["final_paint_mask_source_key"]["stable_surface_id"] = 99
    wrong_source = analyzer.summarize_full_grid_temporal_oracle(
        rows, runtime_receipt=_valid_temporal_receipt()
    )
    assert wrong_source["full_grid_temporal_oracle_incomplete_glyph_mask_rows"] == 1

    rows[0]["final_paint_mask_source_key"]["stable_surface_id"] = 35
    rows[0]["final_product_exact_identity"]["product_translation_equal"] = False
    wrong_product = analyzer.summarize_full_grid_temporal_oracle(
        rows, runtime_receipt=_valid_temporal_receipt()
    )
    assert wrong_product["full_grid_temporal_oracle_product_translation_error_rows"] == 1
    assert wrong_product["full_grid_temporal_oracle_first_divergence"][
        "product_translation"
    ] == 1
    product_defects = analyzer.full_grid_oracle_defect_map_rows(rows)
    assert any(
        defect["first_divergence"] == "product_translation"
        for defect in product_defects
    )
    # A downstream Product mismatch cannot preempt an explicit earlier-stage
    # disagreement. This synthetic row keeps the screen classification green
    # while corrupting the source capture; attribution must still name capture.
    rows[0]["probe_prepack_identity"]["primitive_id"] = 10
    rows[0]["probe_source_capture_identity"]["primitive_id"] = 10
    _make_current_source_submission_comparable(rows[0])
    assert analyzer._oracle_first_divergence(rows[0]) == "capture_write"


def _oracle_row(
    classification: str,
    *,
    product_valid: bool = True,
    product_owner: str = "terrain",
) -> dict:
    return {
        "oracle_schema": "fl4359.same_frame_direct_ray.v2",
        "oracle_record_valid": True,
        "oracle_query_valid": classification != "oracle_query_invalid",
        "oracle_query_failure_reason": 1 if classification == "oracle_query_invalid" else 0,
        "oracle_query_failure_name": (
            "dynamic_publication" if classification == "oracle_query_invalid" else "none"
        ),
        "oracle_direct_hit": classification in {"identity_agreement", "identity_mismatch", "omission"},
        "oracle_direct_no_hit": classification in {"absence_agreement", "ghost_or_commission"},
        "oracle_production_hit": classification in {"identity_agreement", "identity_mismatch", "ghost_or_commission"},
        "oracle_classification": classification,
        "oracle_identity": {"owner_kind": 2, "object_id": 7, "primitive_id": 9, "material": 5},
        "oracle_selected_depth_abs_delta_valid": classification == "identity_agreement",
        "oracle_selected_depth_abs_delta": 0.125,
        "final_product_status": 0x02000001 if product_valid else 0,
        "final_product_owner_name": product_owner,
        "final_paint_magic": 0xF14231 if product_valid else 0,
        "probe_candidate_source_probe": 2,
        "probe_candidate_source_texel": 99,
        "probe_candidate_source_generation": 4,
        "probe_prepack_valid": classification not in {"omission", "absence_agreement"},
        "probe_prepack_identity": {
            "owner_kind": 2,
            "object_id": 7,
            "primitive_id": 9,
            "material": 5,
        },
        "probe_prepack_agrees_with_b61": True,
        "probe_prepack_hardware_depth": 0.5,
        "probe_prepack_provenance": [18, 99, 0, 0],
        "probe_source_capture_band_map_valid": True,
        "probe_source_capture_join_flags": 0x7F,
        "probe_raster_contender_overflow": False,
        "probe_raster_contender_count": 1,
        "probe_raster_contender_recorded_count": 1,
        "probe_raster_exact_identity_contender_count": 1,
        "probe_raster_structural_identity_contender_count": 1,
        "probe_raster_selected_identity_contender_count": 1,
        "probe_raster_nearest_exact_identity_depth": 0.6,
        "probe_raster_nearest_structural_identity_depth": 0.6,
        "probe_source_capture_valid": classification not in {
            "omission", "absence_agreement"
        },
        "probe_source_capture_identity": {
            "owner_kind": 2,
            "object_id": 7,
            "primitive_id": 9,
            "material": 5,
        },
        "probe_source_oracle_query_valid": classification not in {
            "omission", "absence_agreement"
        },
        "probe_source_oracle_direct_hit": classification not in {
            "omission", "absence_agreement"
        },
        "probe_source_oracle_identity": {
            "owner_kind": 2,
            "object_id": 7,
            "primitive_id": 9,
            "material": 5,
        },
        "probe_source_oracle_world_position": [1.0, 2.0, 3.0],
        "probe_source_oracle_depth": 4.0,
        "probe_reconstructed_center": [1.0, 2.0, 3.0],
        "probe_reconstructed_world_corners": [
            [0.5, 2.0, 2.5],
            [1.5, 2.0, 2.5],
            [0.5, 2.0, 3.5],
            [1.5, 2.0, 3.5],
        ],
        "probe_reconstructed_clip_corners": [
            [-0.75, -0.75, 0.5, 1.0],
            [0.75, -0.75, 0.5, 1.0],
            [-0.75, 0.75, 0.5, 1.0],
            [0.75, 0.75, 0.5, 1.0],
        ],
        "probe_reconstructed_clip_valid": True,
        "probe_raster_candidate_source_contender_count": 1,
        "probe_raster_candidate_source_nearest_depth": 0.5,
        "probe_raster_candidate_source_farthest_depth": 0.5,
        "probe_raster_candidate_source_nearest_source": {
            "physical_layer": 4,
            "packed_probe_generation": 18,
            "source_texel": 99,
            "raster_primitive": 0,
        },
        "probe_source_capture_physical_layer": 4,
        "probe_prepack_world_position": [1.1, 2.0, 3.0, 1.0],
        "probe_prepack_world_normal_depth": [0.0, 1.0, 0.0, 0.5],
        "oracle_world_position": [1.1, 2.0, 3.0],
        "oracle_world_normal": [0.0, 1.0, 0.0],
        "frame_id": 12,
        "frame_index": 12,
        "sample_index": 34,
        "frame_trace": {
            "frame_index": 12,
            "sequence_frame": 0,
            "authority_server_tick": 100,
            "scene_revision": 1,
            "view_revision": 160,
            "camera_projection_mode": 0,
            "camera_world": {"x": 1.0, "y": 2.0, "z": 3.0},
            "recorder_identity": {"pid": 1},
            "perspective_probe_state": {"materialized_records": 1},
            "viewport": {"semantic_ansi_size": [1, 1]},
            "oracle_sampling_policy": "world_anchor_hash_mod_or_screen_index_mod",
            "oracle_world_stride": 64,
            "oracle_screen_stride": 64,
        },
        "screen_cell": {"x": 5, "y": 6},
        "selected_surface_owner": 2,
        "selected_surface_object": 7,
        "selected_surface_primitive": 9,
        "selected_surface_material": 5,
        "selected_surface_depth": 10.0,
        "oracle_depth": 10.125,
    }


def _make_admissible_finite_splat(row: dict, source_primitive: int = 10) -> None:
    """Make one exact-primitive mismatch with intact finite-splat provenance."""
    row["oracle_classification"] = "identity_mismatch"
    row["selected_surface_primitive"] = source_primitive
    for field in (
        "probe_prepack_identity",
        "probe_source_capture_identity",
        "probe_source_oracle_identity",
        "probe_candidate_identity",
    ):
        row[field]["primitive_id"] = source_primitive


def _make_current_source_submission_comparable(row: dict) -> None:
    """Supply the exact retained face context required for writer attribution."""
    row.update({
        "capture_schema": "fl4359_sparse_surface_capture.v17",
        "probe_expected_raster_record_valid": True,
        "probe_expected_raster_identity_visible_mask": 2,
        "probe_expected_raster_exact_capture_match_mask": 2,
        "probe_expected_raster_source_valid_mask": 2,
        "probe_expected_raster_face_enabled_mask": 2,
        "probe_expected_raster_clip_valid_mask": 2,
        "probe_expected_raster_cover_mask": 2,
        "probe_expected_raster_suppressed_mask": 0,
        "probe_capture_bands": [1, 2, 0],
        "probe_source_capture_revisions": [1, 1, 1, 4],
        "probe_source_current_revisions": {
            "scene": 1,
            "terrain": 1,
            "static": 1,
            "water": 1,
            "dynamic": 1,
        },
        "probe_source_capture_probe": 2,
        "probe_source_capture_face": 4,
        "probe_source_capture_origin": [1.0, 2.0, 3.0],
        "probe_source_assigned_origin": [1.0, 2.0, 3.0],
        "probe_source_capture_origin_valid": True,
        "probe_source_capture_face_admitted": True,
        "probe_source_capture_publication_context": {
            "family": "terrain",
            "capture_sha256": "a" * 64,
            "current_sha256": "a" * 64,
            "matches": True,
        },
        "probe_source_capture_submission_context": {
            "token_sha256": "b" * 64,
            "publication_sha256": "a" * 64,
            "logical_probe": 2,
            "physical_band": 0,
            "physical_layer": 4,
            "face": 4,
            "assigned_origin": [1.0, 2.0, 3.0],
            "draw_count": 1,
            "instance_count": 64,
            "face_admitted": True,
        },
    })


def test_analyzer_labels_geometry_and_downstream_failures_without_a_baseline_build() -> None:
    analyzer = _load_analyzer()
    rows = [
        _oracle_row("identity_agreement"),
        _oracle_row("ghost_or_commission"),
        _oracle_row("omission", product_valid=False),
        _oracle_row("identity_mismatch"),
        _oracle_row("absence_agreement", product_owner="sky"),
        _oracle_row("oracle_query_invalid", product_valid=False),
    ]
    summary = analyzer.summarize_same_frame_oracle(rows)
    assert summary["same_frame_oracle_spatial_rows_valid"] is True
    assert summary["same_frame_oracle_exhaustive_frame_coverage"] is False
    assert summary["same_frame_oracle_structurally_valid"] is True
    assert summary["same_frame_oracle_query_invalid_rows"] == 1
    assert analyzer._oracle_first_divergence(rows[-1]) == (
        "oracle_query_dynamic_publication"
    )
    assert summary["same_frame_oracle_geometry_defect_rows"] == 3
    assert summary["same_frame_oracle_downstream_defect_rows"] == 2
    assert summary["same_frame_oracle_depth_delta_count"] == 1
    assert summary["same_frame_oracle_depth_delta_max"] == 0.125
    assert summary["same_frame_oracle_source_attribution"][
        "ghost_or_commission|probe=2|generation=4"
    ] == 1


def test_oracle_treats_sky_as_expected_product_for_a_direct_geometry_miss() -> None:
    analyzer = _load_analyzer()
    summary = analyzer.summarize_same_frame_oracle(
        [_oracle_row("absence_agreement", product_owner="sky")]
    )
    assert summary["same_frame_oracle_downstream_counts"] == {
        "direct_product_presence_agreement": 1,
        "final_paint_observed": 1,
    }
    assert summary["same_frame_oracle_downstream_defect_rows"] == 0


def test_oracle_profile_is_fail_closed_and_does_not_change_existing_profiles() -> None:
    analyzer = _text(ANALYZER)
    runner = _text(RUNNER)
    assert '"same_frame_oracle_v1"' in analyzer
    assert 'FULL_GRID_TEMPORAL_ORACLE_PROFILE = "full_grid_temporal_oracle_v1"' in analyzer
    for gate in (
        "evidence_same_frame_oracle_spatial_rows_valid",
        "evidence_same_frame_oracle_exhaustive_frame_coverage",
        "evidence_same_frame_oracle_temporal_continuity",
        "evidence_same_frame_oracle_query_complete",
        "gameplay_same_frame_geometry_agrees",
        "gameplay_same_frame_product_and_paint_presence_agree",
    ):
        assert gate in analyzer
    assert '"--analyzer-proof-profile"' in runner
    assert '"full_grid_temporal_oracle_v1"' in runner
    assert '"--fl4359-full-grid-temporal-oracle=1"' in runner
    assert '"--runtime-receipt"' in runner
    assert "_launcher_log_authority_tick_facts" in runner
    assert "build_worktree_diff_sha256" in runner


def test_sparse_rows_are_not_exhaustive_or_temporal() -> None:
    analyzer = _load_analyzer()
    first = _oracle_row("identity_agreement")
    second = _oracle_row("identity_agreement")
    second["frame_id"] = 13
    second["frame_index"] = 13
    second["sample_index"] = 64
    second["frame_trace"] = dict(first["frame_trace"])
    second["frame_trace"]["frame_index"] = 13
    second["frame_trace"]["sequence_frame"] = 2
    second["frame_trace"]["view_revision"] = 176
    first["frame_trace"]["perspective_probe_state"]["materialized_records"] = 4
    second["frame_trace"]["perspective_probe_state"]["materialized_records"] = 4
    summary = analyzer.summarize_same_frame_oracle([first, second])
    assert summary["same_frame_oracle_rows_per_frame"] == {"12": 1, "13": 1}
    assert summary["same_frame_oracle_expected_subcells_per_frame"] == [4]
    assert summary["same_frame_oracle_exhaustive_frame_coverage"] is False
    assert summary["same_frame_oracle_temporal_sequence_valid"] is False
    assert "authority_queue_drop_or_missing" in summary[
        "same_frame_oracle_temporal_invalid_reasons"
    ]


def _full_grid_rows(frame_index: int, sequence_frame: int) -> list[dict]:
    rows: list[dict] = []
    for sample_index in range(4):
        row = _oracle_row("identity_agreement")
        row["frame_id"] = frame_index
        row["frame_index"] = frame_index
        row["sample_index"] = sample_index
        row["ansi_cell"] = {"x": 0, "y": 0}
        row["oracle_dynamic_revision"] = 7
        row["oracle_stable_world_anchor"] = [float(sample_index), 0.0, 1.0]
        row["selected_surface_stable_world_anchor"] = [float(sample_index), 0.0, 1.0]
        row["probe_candidate_valid"] = True
        row["probe_candidate_selected_surface_valid"] = True
        row["probe_candidate_identity"] = dict(row["oracle_identity"])
        row["probe_candidate_source_probe"] = 1
        row["probe_candidate_source_generation"] = 20
        row["final_paint_mask_contributor_count"] = 1
        row["frame_trace"] = {
            "frame_index": frame_index,
            "sequence_frame": sequence_frame,
            "authority_server_tick": 100 + sequence_frame,
            "scene_revision": 1,
            "view_revision": 160 + sequence_frame,
            "camera_projection_mode": 0,
            "camera_world": {"x": float(sequence_frame), "y": 2.0, "z": 3.0},
            "celestial_time": 0.5,
            "celestial_night_factor": 0.5,
            "celestial_sun_direction": [1.0, 0.0, 0.0],
            "recorder_identity": {"pid": 1},
            "perspective_probe_state": {"materialized_records": 4},
            "viewport": {"semantic_ansi_size": [1, 1]},
            "oracle_sampling_policy": "exhaustive_all_subcells",
            "oracle_world_stride": 1,
            "oracle_screen_stride": 1,
        }
        rows.append(row)
    return rows


def _valid_temporal_receipt() -> dict:
    return {
        "day_cycle_time": 0.5,
        "authority_tick_continuity": {
            "temporal_claim_valid": True,
            "total_dropped": 0,
            "runtime_measurement_window": {
                "ingest_seq_delta": 5,
                "server_tick_start": 99,
                "server_tick_end": 120,
            },
        },
        "run_identity": {
            "build_head": "abc",
            "build_worktree_diff_sha256": "def",
        },
    }


def test_full_grid_temporal_oracle_requires_and_accepts_contiguous_complete_frames() -> None:
    analyzer = _load_analyzer()
    # Sequence metadata follows process/input publication and may skip while the
    # renderer still emits one contiguous frame stream. Raw renderer identity is
    # the cardinality owner; sequence metadata must only be unique and ordered.
    rows = _full_grid_rows(12, 6) + _full_grid_rows(13, 8)
    spatial = analyzer.summarize_same_frame_oracle(
        rows, runtime_receipt=_valid_temporal_receipt()
    )
    temporal = analyzer.summarize_full_grid_temporal_oracle(
        rows, runtime_receipt=_valid_temporal_receipt()
    )
    assert spatial["same_frame_oracle_rows_per_frame"] == {"12": 4, "13": 4}
    assert spatial["same_frame_oracle_exhaustive_frame_coverage"] is True
    assert spatial["same_frame_oracle_temporal_sequence_valid"] is True
    assert spatial["same_frame_oracle_sequence_frames_contiguous"] is False
    assert spatial["same_frame_oracle_sequence_frames_monotonic_unique"] is True
    assert temporal["full_grid_temporal_oracle_valid"] is True
    assert temporal["full_grid_temporal_oracle_comparable_reference_key_pairs"] == 4
    assert temporal["full_grid_temporal_oracle_changing_reference_key_state_pairs"] == 0
    assert temporal["full_grid_temporal_oracle_source_probe_set_changes"] == 0
    assert temporal["full_grid_temporal_oracle_source_generation_set_flips"] == 0
    assert "full_grid_temporal_oracle_current_previous_generation_flips" not in temporal
    assert temporal["full_grid_temporal_oracle_geometry_error_rows"] == 0


def test_full_grid_temporal_oracle_rejects_missing_or_null_day_cycle_time() -> None:
    analyzer = _load_analyzer()
    rows = _full_grid_rows(12, 6) + _full_grid_rows(13, 8)
    for value in ("missing", None):
        receipt = _valid_temporal_receipt()
        if value == "missing":
            receipt.pop("day_cycle_time")
        else:
            receipt["day_cycle_time"] = None
        summary = analyzer.summarize_same_frame_oracle(rows, runtime_receipt=receipt)
        assert summary["same_frame_oracle_fixed_day_cycle_time_valid"] is False
        assert summary["same_frame_oracle_temporal_sequence_valid"] is False
        assert "day_cycle_time_not_fixed_to_0.5" in summary["same_frame_oracle_temporal_invalid_reasons"]


def test_post_window_authority_drop_still_invalidates_temporal_proof() -> None:
    runner = _load_runner()
    with tempfile.TemporaryDirectory() as directory:
        log = Path(directory) / "godot.log"
        log.write_text(
            "FL-4265 authority snapshot queue overflow: "
            "limit=32 dropped_server_tick=99 total_dropped=3\n",
            encoding="utf-8",
        )
        result = runner._launcher_log_authority_tick_facts(
            log,
            {"dropped": 3, "overflow_episodes": 1},
            {
                "complete": True,
                "starts_clean": True,
                "dropped_delta": 0,
                "overflow_delta": 0,
                "temporal_claim_valid": True,
            },
        )
    assert result["temporal_claim_valid"] is False
    assert result["total_dropped"] == 3
    assert result["measurement_window_dropped"] == 0
    assert result["final_total_dropped"] == 3
    assert result["post_window_dropped"] == 3


def test_headed_completion_rejects_each_full_grid_temporal_symptom_gate() -> None:
    runner = _load_runner()
    required = (
        "evidence_same_frame_oracle_spatial_rows_valid",
        "evidence_same_frame_oracle_query_complete",
        "gameplay_same_frame_geometry_agrees",
        "gameplay_same_frame_product_and_paint_presence_agree",
        "evidence_same_frame_oracle_exhaustive_frame_coverage",
        "evidence_same_frame_oracle_temporal_continuity",
        "evidence_full_grid_temporal_oracle_valid",
        "evidence_full_grid_identity_normalized",
        "evidence_full_grid_rows_classified",
        "evidence_full_grid_raster_witness_valid",
        "evidence_full_grid_downstream_exact_identity",
        "evidence_full_grid_exact_current_ray_differences_reported",
        "gameplay_full_grid_zero_structural_geometry_errors",
        "gameplay_full_grid_zero_changing_reference_state",
        "gameplay_full_grid_exact_product_translation",
        "gameplay_full_grid_zero_glyph_mask_splits",
        "gameplay_cell_mask_owner_stability_bounded",
        "gameplay_full_grid_zero_duplicate_out_of_place",
        "evidence_full_grid_first_divergence_known",
        "evidence_presentation_observer_population_accounted",
        "evidence_presentation_observer_sensitivity_valid",
        "gameplay_moving_camera_jitter_bounded",
        "gameplay_content_shimmer_bounded",
        "gameplay_continuous_product_colour_stability_bounded",
        "evidence_terminal_expected_reprojection_valid",
        "evidence_celestial_time_fixed",
        "gameplay_terminal_expected_cell_address_exact",
        "evidence_product_owner_name_transport_valid",
        "evidence_terrain_product_homogeneity_present",
        "evidence_terrain_product_final_paint_join_complete",
        "gameplay_terrain_product_distribution_non_degenerate",
        "evidence_terrain_product_homogeneity_baseline_calibrated",
        "evidence_product_admission_contingency_valid",
        "gameplay_product_admission_chain_exact",
    )
    gates = {name: {"pass": True} for name in required}
    gates["all_pass"] = True
    summary = {
        "proof_profile": "full_grid_temporal_oracle_v1",
        "gates": gates,
    }
    assert runner._analysis_gates_accept_proof(summary) is True

    for gate_name in (
        "gameplay_moving_camera_jitter_bounded",
        "gameplay_content_shimmer_bounded",
        "gameplay_continuous_product_colour_stability_bounded",
        "evidence_celestial_time_fixed",
        "evidence_terrain_product_final_paint_join_complete",
    ):
        mutant = copy.deepcopy(summary)
        mutant["gates"][gate_name]["pass"] = False
        assert runner._analysis_gates_accept_proof(mutant) is False


def test_full_grid_temporal_oracle_fails_missing_duplicate_and_bouncing_rows() -> None:
    analyzer = _load_analyzer()
    first = _full_grid_rows(12, 0)
    second = _full_grid_rows(13, 1)
    second[0]["oracle_classification"] = "identity_mismatch"
    second[0]["selected_surface_primitive"] = 10
    second[0]["probe_candidate_identity"]["primitive_id"] = 10
    second[0]["probe_candidate_pack_rejection_reason"] = 8
    second[0]["final_paint_mask_contributor_count"] = 2
    temporal = analyzer.summarize_full_grid_temporal_oracle(
        first + second, runtime_receipt=_valid_temporal_receipt()
    )
    assert temporal["full_grid_temporal_oracle_geometry_error_rows"] == 1
    assert temporal["full_grid_temporal_oracle_finite_sample_substitution_rows"] == 1
    assert temporal["full_grid_temporal_oracle_finite_splat_inadmissible_rows"] == 1
    assert temporal["full_grid_temporal_oracle_structural_geometry_error_rows"] == 1
    assert temporal["full_grid_temporal_oracle_changing_reference_key_state_pairs"] == 1
    assert temporal["full_grid_temporal_oracle_multi_glyph_mask_cells"] == 1
    assert temporal["full_grid_temporal_oracle_first_divergence_unknown"] == 0
    assert temporal["full_grid_temporal_oracle_first_divergence"][
        "candidate_pack"
    ] == 1
    assert temporal["full_grid_temporal_oracle_mismatch_decomposition"][
        "same_object_same_material_different_primitive_sample"
    ] == 1
    assert temporal[
        "full_grid_temporal_oracle_candidate_pack_rejection_reasons"
    ] == {"8": 1}
    defect_rows = analyzer.full_grid_oracle_defect_map_rows(first + second)
    assert defect_rows[0]["candidate_pack_rejection_reason"] == 8

    missing = analyzer.summarize_same_frame_oracle(
        first + second[:-1], runtime_receipt=_valid_temporal_receipt()
    )
    assert missing["same_frame_oracle_exhaustive_frame_coverage"] is False
    duplicate_rows = first + second + [dict(second[-1])]
    duplicate = analyzer.summarize_same_frame_oracle(
        duplicate_rows, runtime_receipt=_valid_temporal_receipt()
    )
    assert duplicate["same_frame_oracle_exhaustive_frame_coverage"] is False
    assert duplicate["same_frame_oracle_duplicate_subcells_per_frame"][13] == 1


def test_duplicate_components_are_spatially_disconnected_without_row_wrap() -> None:
    analyzer = _load_analyzer()
    components = analyzer._connected_sample_components({0, 1, 7}, 4)
    assert sorted(sorted(component) for component in components) == [[0, 1], [7]]
    # Row-major neighbors 3 and 4 touch in memory but occupy opposite scanline
    # edges, so they must remain distinct screen components.
    wrapped = analyzer._connected_sample_components({3, 4}, 4)
    assert sorted(sorted(component) for component in wrapped) == [[3], [4]]


def test_runtime_tick_drop_and_missing_dirty_build_identity_fail_temporal_claim() -> None:
    analyzer = _load_analyzer()
    first = _oracle_row("identity_agreement")
    second = _oracle_row("identity_agreement")
    second["frame_id"] = 13
    second["frame_index"] = 13
    second["sample_index"] = 35
    second["frame_trace"] = dict(first["frame_trace"])
    second["frame_trace"]["frame_index"] = 13
    second["frame_trace"]["sequence_frame"] = 1
    second["frame_trace"]["view_revision"] = 176
    receipt = {
        "authority_tick_continuity": {
            "temporal_claim_valid": False,
            "total_dropped": 1,
        },
        "run_identity": {"build_head": "abc", "build_worktree_diff_sha256": ""},
    }
    summary = analyzer.summarize_same_frame_oracle(
        [first, second], runtime_receipt=receipt
    )
    assert summary["same_frame_oracle_authority_tick_continuity_valid"] is False
    assert summary["same_frame_oracle_source_identity_valid"] is False
    assert summary["same_frame_oracle_temporal_sequence_valid"] is False


def test_full_grid_rejects_correct_cardinality_with_wrong_index_set() -> None:
    analyzer = _load_analyzer()
    rows = _full_grid_rows(12, 0)
    rows[-1]["sample_index"] = 4
    summary = analyzer.summarize_same_frame_oracle(
        rows, runtime_receipt=_valid_temporal_receipt()
    )
    assert summary["same_frame_oracle_unique_subcells_per_frame"] == {"12": 4}
    assert summary["same_frame_oracle_exact_sample_index_set_per_frame"] == {
        "12": False
    }
    assert summary["same_frame_oracle_out_of_range_sample_indices_per_frame"] == {
        "12": [4]
    }
    assert summary["same_frame_oracle_exhaustive_frame_coverage"] is False


def test_probe_family_and_capture_generation_changes_are_separate_metrics() -> None:
    analyzer = _load_analyzer()
    first = _full_grid_rows(12, 0)
    second = _full_grid_rows(13, 1)
    second[0]["probe_candidate_source_probe"] = 2
    second[1]["probe_candidate_source_generation"] = 21
    temporal = analyzer.summarize_full_grid_temporal_oracle(
        first + second, runtime_receipt=_valid_temporal_receipt()
    )
    assert temporal["full_grid_temporal_oracle_source_probe_set_changes"] == 1
    assert temporal["full_grid_temporal_oracle_source_generation_set_flips"] == 1
    assert temporal["full_grid_temporal_oracle_source_probe_transition_matrix"] == {
        "CURRENT->CURRENT": 3,
        "CURRENT->PREVIOUS": 1,
    }


def test_mismatch_decomposition_excludes_omissions_and_ghosts() -> None:
    analyzer = _load_analyzer()
    rows = _full_grid_rows(12, 0)
    _make_admissible_finite_splat(rows[0])
    rows[1]["oracle_classification"] = "omission"
    rows[1]["oracle_production_hit"] = False
    rows[1]["probe_candidate_valid"] = False
    rows[1]["probe_candidate_selected_surface_valid"] = False
    rows[2]["oracle_classification"] = "ghost_or_commission"
    rows[2]["oracle_direct_hit"] = False
    rows[2]["oracle_direct_no_hit"] = True
    temporal = analyzer.summarize_full_grid_temporal_oracle(
        rows, runtime_receipt=_valid_temporal_receipt()
    )
    assert temporal["full_grid_temporal_oracle_mismatch_decomposition"] == {
        "same_object_same_material_different_primitive_sample": 1
    }
    assert temporal["full_grid_temporal_oracle_mismatch_decomposition_total"] == 1
    assert temporal["full_grid_temporal_oracle_geometry_decomposition_total"] == 3
    assert temporal["full_grid_temporal_oracle_geometry_decomposition_valid"] is True
    assert temporal["full_grid_temporal_oracle_finite_sample_substitution_rows"] == 1
    assert temporal["full_grid_temporal_oracle_finite_splat_admissible_rows"] == 1
    assert temporal["full_grid_temporal_oracle_finite_splat_inadmissible_rows"] == 0
    assert temporal["full_grid_temporal_oracle_finite_splat_decomposition_valid"] is True
    assert temporal["full_grid_temporal_oracle_finite_splat_admissibility_reasons"] == {
        "finite_splat_source_and_footprint_admissible": 1
    }
    assert temporal["full_grid_temporal_oracle_structural_geometry_error_rows"] == 2
    assert temporal["full_grid_temporal_oracle_structural_first_divergence"] == {
        "candidate_pack": 1,
        "raster_depth_winner": 1,
    }


def test_finite_splat_admissibility_fails_closed_on_each_causal_boundary() -> None:
    analyzer = _load_analyzer()

    admissible = _full_grid_rows(12, 0)[0]
    _make_admissible_finite_splat(admissible)
    result = analyzer._finite_splat_admissibility(admissible)
    assert result["admissible"] is True
    assert result["reason"] == "finite_splat_source_and_footprint_admissible"
    assert result["projected_coverage"]["covers"] is True

    bad_source = copy.deepcopy(admissible)
    bad_source["probe_source_oracle_identity"]["primitive_id"] = 11
    result = analyzer._finite_splat_admissibility(bad_source)
    assert result["admissible"] is False
    assert result["reason"] == "capture_identity_disagrees_with_source_ray"

    bad_reconstruction = copy.deepcopy(admissible)
    bad_reconstruction["probe_reconstructed_center"] = [8.0, 2.0, 3.0]
    result = analyzer._finite_splat_admissibility(bad_reconstruction)
    assert result["admissible"] is False
    assert result["reason"] == "reconstruction_disagrees_with_source_ray"

    bad_raster = copy.deepcopy(admissible)
    bad_raster["probe_prepack_identity"]["primitive_id"] = 12
    result = analyzer._finite_splat_admissibility(bad_raster)
    assert result["admissible"] is False
    assert result["reason"] == "raster_identity_disagrees_with_capture"

    missing_projected_quad = copy.deepcopy(admissible)
    missing_projected_quad["probe_reconstructed_clip_valid"] = False
    result = analyzer._finite_splat_admissibility(missing_projected_quad)
    assert result["admissible"] is False
    assert result["reason"] == "projected_quad_invalid"

    outside_projected_quad = copy.deepcopy(admissible)
    outside_projected_quad["probe_reconstructed_clip_corners"] = [
        [0.1, 0.1, 0.5, 1.0],
        [0.2, 0.1, 0.5, 1.0],
        [0.1, 0.2, 0.5, 1.0],
        [0.2, 0.2, 0.5, 1.0],
    ]
    result = analyzer._finite_splat_admissibility(outside_projected_quad)
    assert result["admissible"] is False
    assert result["reason"] == "current_subcell_outside_projected_quad"

    missing_source_fragment = copy.deepcopy(admissible)
    missing_source_fragment["probe_raster_candidate_source_contender_count"] = 0
    result = analyzer._finite_splat_admissibility(missing_source_fragment)
    assert result["admissible"] is False
    assert result["reason"] == "candidate_source_fragment_missing"

    wrong_source_tuple = copy.deepcopy(admissible)
    wrong_source_tuple["probe_raster_candidate_source_nearest_source"][
        "source_texel"
    ] = 100
    result = analyzer._finite_splat_admissibility(wrong_source_tuple)
    assert result["admissible"] is False
    assert result["reason"] == "candidate_source_tuple_disagrees"

    wrong_depth = copy.deepcopy(admissible)
    wrong_depth["probe_raster_candidate_source_nearest_depth"] = 0.6
    wrong_depth["probe_raster_candidate_source_farthest_depth"] = 0.6
    result = analyzer._finite_splat_admissibility(wrong_depth)
    assert result["admissible"] is False
    assert result["reason"] == "winner_depth_outside_candidate_source_envelope"

    outside_quad = copy.deepcopy(admissible)
    outside_quad["probe_prepack_world_position"] = [9.0, 2.0, 3.0, 1.0]
    result = analyzer._finite_splat_admissibility(outside_quad)
    assert result["admissible"] is False
    assert result["reason"] == "current_ray_outside_finite_quad_envelope"

    contradictory_normal = copy.deepcopy(admissible)
    contradictory_normal["probe_prepack_world_normal_depth"] = [0.0, -1.0, 0.0, 0.5]
    result = analyzer._finite_splat_admissibility(contradictory_normal)
    assert result["admissible"] is False
    assert result["reason"] == "surface_normal_contradiction"

    temporal = analyzer.summarize_full_grid_temporal_oracle(
        [admissible, bad_source, bad_reconstruction, bad_raster,
         missing_projected_quad, outside_projected_quad, missing_source_fragment,
         wrong_source_tuple, wrong_depth, outside_quad, contradictory_normal],
        runtime_receipt=_valid_temporal_receipt(),
    )
    assert temporal["full_grid_temporal_oracle_finite_splat_admissible_rows"] == 1
    assert temporal["full_grid_temporal_oracle_finite_splat_inadmissible_rows"] == 10
    assert temporal["full_grid_temporal_oracle_structural_geometry_error_rows"] == 10
    assert temporal["full_grid_temporal_oracle_finite_splat_projected_coverage"] == {
        "covered": 9,
        "outside": 1,
        "unavailable": 1,
    }
    assert temporal["full_grid_temporal_oracle_finite_envelope_projected_coverage"] == {
        "covered": 1
    }


def test_candidate_source_tuple_rejects_disconnected_screen_islands() -> None:
    analyzer = _load_analyzer()
    rows = [_oracle_row("identity_agreement"), _oracle_row("identity_agreement")]
    for index, row in enumerate(rows):
        row["frame_index"] = 12
        row["sample_index"] = index * 3
        row["frame_trace"]["viewport"]["semantic_ansi_size"] = [2, 1]
        row["probe_candidate_valid"] = True
        row["probe_candidate_selected_surface_valid"] = True
        row["probe_candidate_identity"] = dict(row["oracle_identity"])
    temporal = analyzer.summarize_full_grid_temporal_oracle(
        rows, runtime_receipt=_valid_temporal_receipt()
    )
    assert temporal[
        "full_grid_temporal_oracle_candidate_tuple_duplicate_components"
    ] == 1


def test_material_change_is_structural_even_when_primitive_also_changes() -> None:
    analyzer = _load_analyzer()
    row = _full_grid_rows(12, 0)[0]
    row["oracle_classification"] = "identity_mismatch"
    row["selected_surface_primitive"] = 10
    row["selected_surface_material"] = 6
    row["probe_candidate_identity"]["primitive_id"] = 10
    row["probe_candidate_identity"]["material"] = 6
    temporal = analyzer.summarize_full_grid_temporal_oracle(
        [row], runtime_receipt=_valid_temporal_receipt()
    )
    assert temporal["full_grid_temporal_oracle_mismatch_decomposition"] == {
        "same_object_different_material": 1
    }
    assert temporal["full_grid_temporal_oracle_finite_sample_substitution_rows"] == 0
    assert temporal["full_grid_temporal_oracle_structural_geometry_error_rows"] == 1


def test_reused_authority_tick_is_valid_but_backward_tick_is_not() -> None:
    analyzer = _load_analyzer()
    first = _full_grid_rows(12, 0)
    second = _full_grid_rows(13, 1)
    for row in second:
        row["frame_trace"]["authority_server_tick"] = 100
    summary = analyzer.summarize_same_frame_oracle(
        first + second, runtime_receipt=_valid_temporal_receipt()
    )
    assert summary["same_frame_oracle_authority_tick_identity_valid"] is True
    assert summary["same_frame_oracle_authority_tick_reuse_transitions"] == 1
    assert summary["same_frame_oracle_temporal_sequence_valid"] is True

    for row in second:
        row["frame_trace"]["authority_server_tick"] = 99
    backward = analyzer.summarize_same_frame_oracle(
        first + second, runtime_receipt=_valid_temporal_receipt()
    )
    assert backward["same_frame_oracle_authority_tick_identity_valid"] is False
    assert backward["same_frame_oracle_temporal_sequence_valid"] is False


def test_first_divergence_injections_select_capture_reconstruction_raster_and_pack() -> None:
    analyzer = _load_analyzer()

    def mismatch_row() -> dict:
        row = _oracle_row("identity_mismatch")
        row["probe_candidate_valid"] = True
        row["probe_candidate_selected_surface_valid"] = True
        row["probe_candidate_identity"] = dict(row["oracle_identity"])
        row["selected_surface_primitive"] = 10
        row["probe_candidate_identity"]["primitive_id"] = 10
        return row

    capture = mismatch_row()
    capture["probe_prepack_identity"]["primitive_id"] = 10
    capture["probe_source_capture_identity"]["primitive_id"] = 10
    _make_current_source_submission_comparable(capture)
    assert analyzer._oracle_first_divergence(capture) == "capture_write"

    reconstruction = mismatch_row()
    reconstruction["probe_prepack_identity"]["primitive_id"] = 10
    reconstruction["probe_source_capture_identity"]["primitive_id"] = 10
    reconstruction["probe_source_oracle_identity"]["primitive_id"] = 10
    reconstruction["probe_reconstructed_center"] = [9.0, 2.0, 3.0]
    assert analyzer._oracle_first_divergence(reconstruction) == "reconstruction"

    raster = mismatch_row()
    raster["probe_prepack_identity"]["primitive_id"] = 10
    raster["probe_source_capture_identity"]["primitive_id"] = 10
    raster["probe_source_oracle_identity"]["primitive_id"] = 10
    assert analyzer._oracle_first_divergence(raster) == "raster_depth_winner"

    stale_capture_association = mismatch_row()
    stale_capture_association["probe_prepack_identity"]["primitive_id"] = 10
    stale_capture_association["probe_source_capture_valid"] = False
    stale_capture_association["probe_source_oracle_address_valid"] = True
    stale_capture_association["probe_source_oracle_query_valid"] = True
    stale_capture_association["probe_source_oracle_identity"]["primitive_id"] = 10
    assert analyzer._oracle_first_divergence(stale_capture_association) == \
        "raster_winner_source_association"

    empty_ray_covered_by_real_source_texel = mismatch_row()
    empty_ray_covered_by_real_source_texel["oracle_classification"] = \
        "ghost_or_commission"
    empty_ray_covered_by_real_source_texel["oracle_direct_hit"] = False
    empty_ray_covered_by_real_source_texel["oracle_direct_no_hit"] = True
    empty_ray_covered_by_real_source_texel["probe_prepack_identity"][
        "primitive_id"
    ] = 10
    empty_ray_covered_by_real_source_texel["probe_source_capture_valid"] = False
    empty_ray_covered_by_real_source_texel[
        "probe_source_oracle_address_valid"
    ] = True
    empty_ray_covered_by_real_source_texel[
        "probe_source_oracle_query_valid"
    ] = True
    empty_ray_covered_by_real_source_texel["probe_source_oracle_identity"][
        "primitive_id"
    ] = 10
    assert analyzer._oracle_first_divergence(
        empty_ray_covered_by_real_source_texel
    ) == "raster_depth_winner"

    pack = mismatch_row()
    assert analyzer._oracle_first_divergence(pack) == "candidate_pack"

    rejected_wrong_prepack = mismatch_row()
    rejected_wrong_prepack["probe_prepack_identity"]["primitive_id"] = 10
    rejected_wrong_prepack["probe_source_capture_identity"]["primitive_id"] = 10
    rejected_wrong_prepack["probe_source_oracle_identity"]["primitive_id"] = 10
    assert analyzer._oracle_first_divergence(rejected_wrong_prepack) == \
        "raster_depth_winner"


def test_raster_witness_fails_closed_and_separates_coverage_from_depth() -> None:
    analyzer = _load_analyzer()
    row = _oracle_row("identity_mismatch")
    row["probe_candidate_valid"] = True
    row["probe_candidate_selected_surface_valid"] = True
    row["selected_surface_primitive"] = 10
    for field in (
        "probe_prepack_identity",
        "probe_source_capture_identity",
        "probe_source_oracle_identity",
        "probe_candidate_identity",
    ):
        row[field] = dict(row["oracle_identity"])
        row[field]["primitive_id"] = 10

    missing_coverage = dict(row)
    missing_coverage.update({
        "probe_raster_exact_identity_contender_count": 0,
        "probe_expected_visibility_active_mask": 0b110,
        "probe_expected_visibility_query_valid_mask": 0b110,
        "probe_expected_visibility_identity_match_mask": 0b010,
        "probe_expected_capture_active_mask": 0b110,
        "probe_expected_capture_nearby_match_mask": 0b010,
    })
    assert analyzer._oracle_first_divergence(missing_coverage) == \
        "raster_coverage_omission"

    impossible_depth = dict(row)
    impossible_depth["probe_raster_nearest_exact_identity_depth"] = 0.4
    assert analyzer._oracle_first_divergence(impossible_depth) == \
        "raster_depth_order_invalid"

    equal_within_tolerance = dict(row)
    equal_within_tolerance[
        "probe_raster_nearest_exact_identity_depth"
    ] = 0.50000005
    assert analyzer._oracle_first_divergence(equal_within_tolerance) == \
        "raster_depth_winner"

    broad_family_depth_is_not_exact_depth = dict(row)
    broad_family_depth_is_not_exact_depth[
        "probe_raster_nearest_structural_identity_depth"
    ] = 0.4
    broad_family_depth_is_not_exact_depth[
        "probe_raster_nearest_exact_identity_depth"
    ] = 0.6
    assert analyzer._oracle_first_divergence(
        broad_family_depth_is_not_exact_depth
    ) == "raster_depth_winner"

    overflow = dict(row)
    overflow["probe_raster_contender_overflow"] = True
    assert analyzer._oracle_first_divergence(overflow) == \
        "raster_witness_invalid"

    stale_map = dict(row)
    stale_map["probe_source_capture_join_flags"] = 0x3F
    assert analyzer._oracle_first_divergence(stale_map) == \
        "source_physical_join_invalid"


def test_omission_injections_select_visibility_capture_and_raster_coverage() -> None:
    analyzer = _load_analyzer()

    def omission_row() -> dict:
        row = _oracle_row("omission", product_valid=False)
        row.update({
            "probe_expected_visibility_active_mask": 0b110,
            "probe_expected_visibility_query_valid_mask": 0b110,
            "probe_expected_visibility_identity_match_mask": 0b010,
            "probe_expected_capture_active_mask": 0b110,
            "probe_expected_capture_exact_valid_mask": 0b110,
            "probe_expected_capture_exact_match_mask": 0,
            "probe_expected_capture_nearby_match_mask": 0,
            "probe_raster_exact_identity_contender_count": 0,
            "probe_raster_structural_identity_contender_count": 0,
            "probe_raster_selected_identity_contender_count": 0,
        })
        return row

    visibility = omission_row()
    visibility["probe_expected_visibility_identity_match_mask"] = 0
    assert analyzer._oracle_first_divergence(visibility) == \
        "capture_visibility_domain"

    capture = omission_row()
    assert analyzer._oracle_first_divergence(capture) == "capture_write"

    raster = omission_row()
    raster["probe_expected_capture_nearby_match_mask"] = 0b010
    assert analyzer._oracle_first_divergence(raster) == \
        "raster_coverage_omission"

    query_invalid = omission_row()
    query_invalid["probe_expected_visibility_query_valid_mask"] = 0b010
    assert analyzer._oracle_first_divergence(query_invalid) == \
        "expected_capture_query_invalid"

    missing_transport = omission_row()
    missing_transport["probe_expected_capture_active_mask"] = 0
    assert analyzer._oracle_first_divergence(missing_transport) == \
        "expected_capture_search_uninstrumented"

    packed_away = omission_row()
    packed_away["probe_prepack_valid"] = True
    packed_away["probe_prepack_identity"] = dict(packed_away["oracle_identity"])
    packed_away["probe_raster_selected_identity_contender_count"] = 1
    packed_away["probe_source_oracle_query_valid"] = False
    assert analyzer._oracle_first_divergence(packed_away) == "candidate_pack"


# =====================================================================
# FL-4359 continuity law: mandatory falsifier controls
# =====================================================================
# These are the three controls the Aug-23 rollback audit (section 7.4) names as
# the precondition for trusting ANY relaxed oracle reference law. Without them a
# tolerance can silently absorb real defects, or the oracle can grade its own
# output. Each control is asserted here, not merely computed in the receipt.


def _gate_args(**over):
    """Minimal argparse namespace for build_gate_summary()."""
    base = dict(
        max_snap_px_delta=1.0,
        max_snap_sign_flip_rate=0.05,
        max_content_jitter_pct=5.0,
        max_selected_surface_identity_churn_pct=5.0,
        max_post_surface_bounce_pct=12.0,
        min_moving_frame_pairs=0,
        min_camera_motion_ratio=0.0,
        min_comparable_cells=0,
        proof_profile="general",
        expected_red_gate=[],
        oracle_reference_law="strict",
    )
    base.update(over)
    return argparse.Namespace(**base)


def _make_continuity_geometry(
    row: dict,
    *,
    depth: float = 10.0,
    reference_position=(1.0, 2.0, 3.0),
    candidate_position=(1.0, 2.0, 3.0),
    reference_normal=(0.0, 1.0, 0.0),
    candidate_normal=(0.0, 1.0, 0.0),
    candidate_depth: float | None = None,
    source_epoch: int = 0,
) -> None:
    """Give one oracle row the perspective geometry the continuity law reads."""
    row["oracle_projection_kind"] = 1  # PROJECTION_PERSPECTIVE
    row["oracle_depth"] = depth
    row["oracle_world_position"] = list(reference_position)
    row["oracle_world_normal"] = list(reference_normal)
    row["selected_surface_depth"] = depth if candidate_depth is None else candidate_depth
    row["selected_surface_world_position"] = list(candidate_position)
    row["selected_surface_world_normal"] = list(candidate_normal)
    # b64 status word: source epoch in bits 30-31 (visible_surface_builder.glsl
    # STATUS_SOURCE_EPOCH_SHIFT). Epoch 0 is the current-ray answer.
    row["selected_surface_status"] = (source_epoch << 30) | 1
    trace = row["frame_trace"]
    trace["semantic_ansi_size"] = [1, 1]
    trace["target_size"] = [16, 16]
    trace["camera_fov"] = 58.0


def _continuity_rows() -> list[dict]:
    """Four rows: one agreement, one WHERE re-anchor, two other mismatches."""
    rows = _full_grid_rows(12, 0)
    for row in rows:
        _make_continuity_geometry(row)
    # row 0 stays an exact identity agreement.
    # row 1: FL-4359 WHERE re-anchor — terrain, same object/material, different
    # primitive, ray-exact position/normal/depth. By design, not a defect.
    rows[1]["oracle_classification"] = "identity_mismatch"
    rows[1]["selected_surface_primitive"] = 11
    # row 2: same continuous surface reached at a different primitive with a
    # small in-tolerance offset — benign finite resampling.
    rows[2]["oracle_classification"] = "identity_mismatch"
    rows[2]["selected_surface_primitive"] = 12
    _make_continuity_geometry(
        rows[2], candidate_position=(1.0, 2.05, 3.0), candidate_depth=10.05
    )
    # row 3: a genuinely different surface far off the reference plane.
    rows[3]["oracle_classification"] = "identity_mismatch"
    rows[3]["selected_surface_primitive"] = 13
    _make_continuity_geometry(
        rows[3], candidate_position=(1.0, 102.0, 3.0), candidate_depth=110.0
    )
    return rows


def test_continuity_tolerance_zero_control_reproduces_strict_counters() -> None:
    """Control (a): tolerance zero must reproduce the strict exact-identity count."""
    analyzer = _load_analyzer()
    rows = _continuity_rows()
    summary = analyzer.summarize_full_grid_temporal_oracle(
        rows, runtime_receipt=_valid_temporal_receipt()
    )
    controls = summary["full_grid_temporal_oracle_continuity_controls"]
    assert summary["full_grid_temporal_oracle_identity_mismatch_rows"] == 3
    assert controls["strict_defect_rows"] == 3
    assert controls["tolerance_zero_defect_rows"] == 3
    assert controls["tolerance_zero_reproduces_strict"] is True
    # The tolerance must also be doing work: it may not score identically.
    assert controls["tolerance_exercised"] is True
    assert controls["tolerance_is_load_bearing"] is True
    assert controls["continuity_defect_rows"] < controls["tolerance_zero_defect_rows"]
    # The strict counters themselves must be untouched by the added law.
    assert summary["full_grid_temporal_oracle_geometry_error_rows"] == 3
    assert summary["full_grid_temporal_oracle_mismatch_decomposition"] == {
        "same_object_same_material_different_primitive_sample": 3
    }


def test_continuity_decomposes_where_reanchor_and_benign_resample() -> None:
    analyzer = _load_analyzer()
    summary = analyzer.summarize_full_grid_temporal_oracle(
        _continuity_rows(), runtime_receipt=_valid_temporal_receipt()
    )
    classification = summary["full_grid_temporal_oracle_continuity_classification"]
    assert classification["exact_identity_agreement"] == 1
    assert classification["where_reanchor_address_only"] == 1
    assert classification["current_ray_continuous_resample"] == 1
    assert summary["full_grid_temporal_oracle_continuity_defect_rows"] == 1
    assert summary["full_grid_temporal_oracle_continuity_by_design_rows"] == 2
    assert summary["full_grid_temporal_oracle_continuity_decomposition_valid"] is True
    assert summary["full_grid_temporal_oracle_reference_owner"] == (
        "current_ray_scenequery"
    )
    assert summary["full_grid_temporal_oracle_answer_source_epoch_counts"] == {
        "direct_current_ray": 4
    }


def test_continuity_keeps_injected_bad_candidates_as_defects() -> None:
    """Control (b): a genuinely different surface stays a defect under the law."""
    analyzer = _load_analyzer()
    for label, mutate in (
        (
            "plane_distance_exceeded",
            lambda row: _make_continuity_geometry(
                row, candidate_position=(1.0, 402.0, 3.0), candidate_depth=10.0
            ),
        ),
        (
            "surface_normal_discontinuous",
            lambda row: _make_continuity_geometry(
                row, candidate_normal=(0.0, -1.0, 0.0)
            ),
        ),
        (
            "owner_substitution",
            lambda row: row.update({"selected_surface_owner": 1}),
        ),
        (
            "object_substitution",
            lambda row: row.update({"selected_surface_object": 8}),
        ),
        (
            "material_substitution",
            lambda row: row.update({"selected_surface_material": 6}),
        ),
        (
            "depth_separation_exceeded",
            lambda row: _make_continuity_geometry(row, candidate_depth=999.0),
        ),
        (
            # Water is refused on either side, mirroring the shader's
            # FL-4737/FL-4216 coupling proof.
            "water_excluded",
            lambda row: row.update({"selected_surface_owner": 3}),
        ),
    ):
        rows = _full_grid_rows(12, 0)
        for row in rows:
            _make_continuity_geometry(row)
        bad = rows[0]
        bad["oracle_classification"] = "identity_mismatch"
        bad["selected_surface_primitive"] = 11
        mutate(bad)
        if label == "water_excluded":
            bad["oracle_identity"] = dict(bad["oracle_identity"])
            bad["oracle_identity"]["owner_kind"] = 3
        verdict = analyzer._oracle_continuity_class(bad)
        assert verdict["defect"] is True, label
        assert verdict["class"] == f"real_defect_{label}", label
        summary = analyzer.summarize_full_grid_temporal_oracle(
            rows, runtime_receipt=_valid_temporal_receipt()
        )
        assert summary["full_grid_temporal_oracle_continuity_defect_rows"] == 1, label


def test_continuity_reference_collapse_guard_marks_analysis_invalid() -> None:
    """Control (c): an all-retained arm with zero defects grades itself."""
    analyzer = _load_analyzer()
    rows = _full_grid_rows(12, 0)
    for row in rows:
        # Every row retained-authored (epoch CURRENT) and in exact agreement.
        _make_continuity_geometry(row, source_epoch=2)
    summary = analyzer.summarize_full_grid_temporal_oracle(
        rows, runtime_receipt=_valid_temporal_receipt()
    )
    controls = summary["full_grid_temporal_oracle_continuity_controls"]
    assert controls["retained_authored_row_fraction"] == 1.0
    assert controls["continuity_defect_rows"] == 0
    assert controls["reference_collapse_suspected"] is True
    assert controls["valid"] is False
    assert summary["full_grid_temporal_oracle_continuity_controls_valid"] is False
    assert summary["full_grid_temporal_oracle_answer_source_epoch_counts"] == {
        "retained_current": 4
    }


def test_continuity_controls_refuse_any_unproven_reference_row() -> None:
    """Control (c2): one unprovable reference owner voids the controls.

    The row chosen here is an exact-identity agreement, so it never reaches the
    continuity relation's `reference_owner_unproven` refusal and every other
    control still reports valid. Only the zero-unproven-reference requirement
    can fail this case, which is what makes it load-bearing.
    """
    analyzer = _load_analyzer()
    rows = _continuity_rows()
    # The oracle never validated its own query for this ray, so its reference
    # cannot be credited to the independent current-ray SceneQuery.
    rows[0]["oracle_query_valid"] = False
    summary = analyzer.summarize_full_grid_temporal_oracle(
        rows, runtime_receipt=_valid_temporal_receipt()
    )
    controls = summary["full_grid_temporal_oracle_continuity_controls"]
    assert controls["unproven_reference_rows"] == 1
    assert summary["full_grid_temporal_oracle_reference_owner_counts"] == {
        "current_ray_scenequery": 3,
        "unproven": 1,
    }
    assert summary["full_grid_temporal_oracle_reference_owner"] == "unproven"
    # Every other control still passes; the new requirement is the only failure.
    assert controls["tolerance_zero_reproduces_strict"] is True
    assert controls["tolerance_is_load_bearing"] is True
    assert controls["decomposition_valid"] is True
    assert controls["reference_collapse_suspected"] is False
    assert controls["valid"] is False
    assert summary["full_grid_temporal_oracle_continuity_controls_valid"] is False


def test_continuity_refuses_rows_without_the_geometry_it_needs() -> None:
    """A row lacking the surface geometry stays strict; it is never admitted."""
    analyzer = _load_analyzer()
    rows = _full_grid_rows(12, 0)
    for row in rows:
        _make_continuity_geometry(row)
    bare = rows[0]
    bare["oracle_classification"] = "identity_mismatch"
    bare["selected_surface_primitive"] = 11
    bare.pop("selected_surface_world_normal")
    verdict = analyzer._oracle_continuity_class(bare)
    assert verdict["class"] == "real_defect_surface_geometry_missing"
    # An unreconstructible ANSI-cell span is equally refused, never guessed.
    orthographic = rows[1]
    orthographic["oracle_classification"] = "identity_mismatch"
    orthographic["selected_surface_primitive"] = 12
    _make_continuity_geometry(
        orthographic, candidate_position=(1.0, 2.05, 3.0), candidate_depth=10.05
    )
    orthographic["oracle_projection_kind"] = 0
    assert analyzer._oracle_continuity_class(orthographic)["class"] == (
        "real_defect_ansi_cell_span_unavailable"
    )


def test_oracle_reference_law_default_is_strict_and_flag_swaps_blocking_set() -> None:
    """The added law changes nothing until --oracle-reference-law selects it."""
    analyzer = _load_analyzer()
    summary = analyzer.summarize_full_grid_temporal_oracle(
        _continuity_rows(), runtime_receipt=_valid_temporal_receipt()
    )
    summary = dict(summary)
    summary["same_frame_oracle_rows"] = 4
    summary["same_frame_oracle_geometry_defect_rows"] = 3
    summary["same_frame_oracle_downstream_defect_rows"] = 0

    strict_args = _gate_args(
        proof_profile=analyzer.FULL_GRID_TEMPORAL_ORACLE_PROFILE,
        oracle_reference_law=analyzer.ORACLE_REFERENCE_LAW_STRICT,
    )
    strict_gates = analyzer.build_gate_summary(summary, strict_args)
    assert strict_gates["oracle_reference_law"] == "strict"
    assert (
        "gameplay_full_grid_zero_structural_geometry_errors"
        in strict_gates["blocking_profile_gates"]
    )
    assert (
        "gameplay_full_grid_zero_structural_geometry_errors_continuity"
        not in strict_gates["blocking_profile_gates"]
    )
    assert (
        "evidence_full_grid_continuity_law_controls_valid"
        not in strict_gates["blocking_profile_gates"]
    )
    # Both populations are always reported, whichever law blocks.
    assert (
        "gameplay_full_grid_zero_structural_geometry_errors_continuity"
        in strict_gates
    )

    continuity_args = _gate_args(
        proof_profile=analyzer.FULL_GRID_TEMPORAL_ORACLE_PROFILE,
        oracle_reference_law=analyzer.ORACLE_REFERENCE_LAW_CONTINUITY,
    )
    continuity_gates = analyzer.build_gate_summary(summary, continuity_args)
    assert continuity_gates["oracle_reference_law"] == "continuity_v1"
    blocking = continuity_gates["blocking_profile_gates"]
    assert "gameplay_full_grid_zero_structural_geometry_errors_continuity" in blocking
    assert "gameplay_full_grid_zero_structural_geometry_errors" not in blocking
    assert "gameplay_same_frame_geometry_agrees_continuity" in blocking
    assert "gameplay_same_frame_geometry_agrees" not in blocking
    assert "gameplay_full_grid_zero_changing_reference_state_continuity" in blocking
    assert "evidence_full_grid_continuity_law_controls_valid" in blocking
    # The gate that is already primitive-insensitive keeps exactly one owner.
    assert "gameplay_full_grid_zero_duplicate_out_of_place" in blocking


def test_legacy_source_capture_cannot_claim_writer_attribution() -> None:
    analyzer = _load_analyzer()
    row = _oracle_row("identity_mismatch")
    _make_current_source_submission_comparable(row)
    row["capture_schema"] = "fl4359_sparse_surface_capture.v15"
    assert analyzer._oracle_source_capture_write_stage(row) == "source_capture_legacy_unavailable"
