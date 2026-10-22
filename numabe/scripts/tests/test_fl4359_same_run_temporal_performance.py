"""Fail-closed joins for the headed movement temporal/performance gate."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/run_fl4359_headed_movement.py"


def _load_runner():
    scripts_path = str(ROOT / "scripts")
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    spec = importlib.util.spec_from_file_location(
        "fl4359_same_run_temporal_performance_runner", RUNNER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER_MODULE = _load_runner()
MATRIX = {
    "schema": "fl4359.matrix_configuration_identity.v1",
    "key": "3600x2252|3600x2252|3840x2160|16x32|225x71|terminal|perspective|FF",
}


def _valid_receipt() -> dict:
    records = []
    start_usec = 1_000_000
    for sequence in range(7):
        end_usec = start_usec + 16_000
        records.append(
            {
                "schema": "fl4359.same_run_temporal_performance_frame.v1",
                "sequence_frame": sequence,
                "movement_phase": (
                    "active" if sequence < 5 else "release" if sequence == 5 else "settle"
                ),
                "wall_interval": {
                    "start_usec": start_usec,
                    "end_usec": end_usec,
                    "duration_ms": 16.0,
                },
                "deadline_ms": 16.667,
                "deadline_met": True,
                "post_draw_join": {
                    "status": "matched",
                    "post_draw_frame": 100 + sequence,
                    "dispatch_frame_index": 100 + sequence,
                },
                "render_frame_index": 200 + sequence,
                "canvas_render_frame_id": 300 + sequence,
                "canvas_render_target_id": 700,
                "canvas_request_token": 800 + sequence,
                "request_id": 900 + sequence,
                "matrix_identity": copy.deepcopy(MATRIX),
                "custom_presentation_output": {
                    "schema": "fl4691.custom_presentation_output.v1",
                    "output_rid": 600,
                    "render_frame_id": 300 + sequence,
                    "canvas_request_token": 800 + sequence,
                    "owner": "ascii_compositor_post_present_scene_color",
                },
                "authority": {
                    "status": "available",
                    "ingest_seq": 1000 + sequence,
                    "server_tick": 2000 + sequence,
                    "server_tick_rate_hz": 30.0,
                },
            }
        )
        start_usec = end_usec
    native_frames = []
    for sequence in range(5):
        record = records[sequence]
        native_frames.append(
            {
                "sequence_frame": sequence,
                "post_draw_frame": 100 + sequence,
                "dispatch_canvas_render_frame_id": 300 + sequence,
                "canvas_request_token": 800 + sequence,
                "canvas_provenance_target": 700,
                "dispatch": {
                    "frame_index": 100 + sequence,
                    "request_id": 900 + sequence,
                    "render_frame_index": 200 + sequence,
                    "canvas_render_frame_id": 300 + sequence,
                    "canvas_render_target_id": 700,
                    "canvas_request_token": 800 + sequence,
                    "matrix_identity": copy.deepcopy(MATRIX),
                    "custom_presentation_output": copy.deepcopy(
                        record["custom_presentation_output"]
                    ),
                },
                "canvas_provenance": {
                    "status": "ok",
                    "frame_id": 300 + sequence,
                    "render_frame_id": 300 + sequence,
                },
            }
        )
    return {
        "capture_schedule": {
            "effective_active_frames": 5,
            "effective_release_frames": 1,
            "effective_settle_frames": 1,
            "effective_total_frames": 7,
        },
        "same_run_temporal_performance_timing": {
            "schema": "fl4359.same_run_temporal_performance_timing.v1",
            "measurement_classification": (
                "instrumented_full_grid_temporal_capture_cadence"
            ),
            "includes_sparse_capture_overhead": True,
            "includes_native_frame_observer_overhead": True,
            "includes_canvas_and_tonemap_capture_overhead": True,
            "per_present_frame_timing_complete": False,
            "wall_interval_semantics": (
                "selected_capture_cadence_including_intervening_post_draws"
            ),
            "continuous_post_draw_timing_status": "open",
            "continuous_post_draw_timing_reason": (
                "selected_capture_rows_do_not_cover_intervening_draws"
            ),
            "clean_60fps_acceptance_eligible": False,
            "clean_60fps_absence_reason": (
                "proof_capture_and_provenance_observers_armed"
            ),
            "deadline_ms": 16.667,
            "expected_record_count": 7,
            "record_count": 7,
            "post_draw_observation_count": 7,
            "unselected_post_draw_count": 0,
            "repeated_sequence_post_draw_count": 0,
            "skipped_post_draw_frame_count": 0,
            "repeated_or_reversed_post_draw_frame_count": 0,
            "deadline_met_count": 7,
            "capture_mode_deadline_pass": True,
            "authority_progress": {
                "valid": True,
                "reason": "authority_advancing",
                "ingested_count": 6,
                "server_tick_delta": 6,
            },
            "records": records,
        },
        "native_frame_capture": {
            "status": "readback_complete",
            "frames": native_frames,
        },
        "presentation_field_sidecars": [
            {
                "sequence_frame": sequence,
                "request_id": 900 + sequence,
                "frame_index": 200 + sequence,
            }
            for sequence in range(5)
        ],
        "native_source_content_sidecars": [
            {
                "sequence_frame": sequence,
                "request_id": 900 + sequence,
                "frame_index": 200 + sequence,
                "render_frame_index": 200 + sequence,
            }
            for sequence in range(5)
        ],
    }


def _gate(receipt: dict, temporal_pass: bool = True) -> dict:
    return RUNNER_MODULE._same_run_temporal_performance_gate(
        receipt,
        {},
        temporal_gate_pass=temporal_pass,
        source_content_capture_pass=True,
        positive_comparable_population_pass=True,
    )


def test_exact_same_run_join_passes_only_as_instrumented_capture_cadence() -> None:
    result = _gate(_valid_receipt())
    assert result["pass"] is True
    assert result["capture_mode_deadline_pass"] is True
    assert result["temporal_identity_join_pass"] is True
    assert result["clean_60fps_acceptance_eligible"] is False
    assert result["continuous_post_draw_timing_status"] == "open"
    assert result["claim_scope"] == "instrumented_same_run_temporal_capture_cadence_only"


def test_off_by_one_post_draw_join_fails() -> None:
    receipt = _valid_receipt()
    receipt["native_frame_capture"]["frames"][-1]["post_draw_frame"] += 1
    result = _gate(receipt)
    assert result["pass"] is False
    assert "final_canvas_identity_join_mismatch" in result["errors"]


def test_sparse_and_zero_timing_records_fail() -> None:
    for retained in (0, 3):
        receipt = _valid_receipt()
        timing = receipt["same_run_temporal_performance_timing"]
        timing["records"] = timing["records"][:retained]
        timing["record_count"] = retained
        result = _gate(receipt)
        assert result["pass"] is False
        assert "timing_record_count_mismatch" in result["errors"]


def test_unselected_and_repeated_post_draws_remain_explicit_capture_cadence() -> None:
    receipt = _valid_receipt()
    timing = receipt["same_run_temporal_performance_timing"]
    timing["post_draw_observation_count"] = 9
    timing["unselected_post_draw_count"] = 1
    timing["repeated_sequence_post_draw_count"] = 1
    timing["skipped_post_draw_frame_count"] = 3
    timing["repeated_or_reversed_post_draw_frame_count"] = 1
    timing["capture_mode_deadline_pass"] = False
    result = _gate(receipt)
    assert result["pass"] is False
    assert result["temporal_gate_pass"] is True
    assert result["identity_join_pass"] is True
    assert result["temporal_identity_join_pass"] is True
    assert result["capture_mode_deadline_pass"] is False
    assert result["claim_scope"] == "instrumented_same_run_temporal_capture_cadence_only"
    assert result["post_draw_observation_accounting"]["skipped_post_draw_frame_count"] == 3


def test_post_draw_observation_counts_cannot_hide_deduplicated_callbacks() -> None:
    receipt = _valid_receipt()
    receipt["same_run_temporal_performance_timing"]["post_draw_observation_count"] = 8
    result = _gate(receipt)
    assert result["pass"] is False
    assert "post_draw_observation_accounting_mismatch" in result["errors"]


def test_mixed_matrix_identity_fails() -> None:
    receipt = _valid_receipt()
    receipt["same_run_temporal_performance_timing"]["records"][4][
        "matrix_identity"
    ]["key"] = "different-matrix"
    result = _gate(receipt)
    assert result["pass"] is False
    assert "matrix_identity_changed" in result["errors"]


def test_stalled_authority_fails_even_when_producer_claims_valid_progress() -> None:
    receipt = _valid_receipt()
    records = receipt["same_run_temporal_performance_timing"]["records"]
    records[-1]["authority"] = copy.deepcopy(records[0]["authority"])
    result = _gate(receipt)
    assert result["pass"] is False
    assert "authority_progress_not_reproducible_from_frames" in result["errors"]


def test_unmatched_final_canvas_frame_fails() -> None:
    receipt = _valid_receipt()
    receipt["native_frame_capture"]["frames"][-1]["sequence_frame"] = 6
    result = _gate(receipt)
    assert result["pass"] is False
    assert "final_canvas_sequence_set_incomplete" in result["errors"]


def test_wrong_source_sidecar_request_or_frame_fails() -> None:
    for sidecar_key, field in (
        ("presentation_field_sidecars", "request_id"),
        ("native_source_content_sidecars", "frame_index"),
    ):
        receipt = _valid_receipt()
        receipt[sidecar_key][-1][field] += 1
        result = _gate(receipt)
        assert result["pass"] is False
        assert "source_sidecar_timing_identity_join_mismatch" in result["errors"]


def test_wrong_native_dispatch_request_or_render_frame_fails() -> None:
    for field in ("request_id", "render_frame_index"):
        receipt = _valid_receipt()
        receipt["native_frame_capture"]["frames"][-1]["dispatch"][field] += 1
        result = _gate(receipt)
        assert result["pass"] is False
        assert "final_canvas_identity_join_mismatch" in result["errors"]


def test_capture_overhead_cannot_be_relabeled_as_clean_60fps() -> None:
    receipt = _valid_receipt()
    receipt["same_run_temporal_performance_timing"][
        "clean_60fps_acceptance_eligible"
    ] = True
    result = _gate(receipt)
    assert result["pass"] is False
    assert "instrumented_timing_misclassified_as_clean_60fps" in result["errors"]


def test_slow_instrumented_capture_preserves_temporal_and_identity_verdicts() -> None:
    receipt = _valid_receipt()
    timing = receipt["same_run_temporal_performance_timing"]
    record = timing["records"][3]
    interval = record["wall_interval"]
    interval["end_usec"] = interval["start_usec"] + 20_000
    interval["duration_ms"] = 20.0
    record["deadline_met"] = False
    for later in timing["records"][4:]:
        later["wall_interval"]["start_usec"] += 4_000
        later["wall_interval"]["end_usec"] += 4_000
    timing["deadline_met_count"] = 6
    timing["capture_mode_deadline_pass"] = False
    result = _gate(receipt)
    assert result["pass"] is False
    assert result["capture_mode_deadline_pass"] is False
    assert result["temporal_gate_pass"] is True
    assert result["identity_join_pass"] is True


def test_temporal_failure_cannot_pass_on_fast_frame_records() -> None:
    result = _gate(_valid_receipt(), temporal_pass=False)
    assert result["pass"] is False
    assert "temporal_analyzer_gate_failed" in result["errors"]


def test_missing_actual_source_content_cannot_pass() -> None:
    result = RUNNER_MODULE._same_run_temporal_performance_gate(
        _valid_receipt(),
        {},
        temporal_gate_pass=True,
        source_content_capture_pass=False,
        positive_comparable_population_pass=True,
    )
    assert result["pass"] is False
    assert "actual_native_source_content_capture_missing" in result["errors"]


def test_zero_comparable_population_cannot_pass() -> None:
    result = RUNNER_MODULE._same_run_temporal_performance_gate(
        _valid_receipt(),
        {},
        temporal_gate_pass=True,
        source_content_capture_pass=True,
        positive_comparable_population_pass=False,
    )
    assert result["pass"] is False
    assert "positive_comparable_population_missing" in result["errors"]


def test_identity_join_failure_rejects_full_grid_runtime_proof() -> None:
    raw_receipt = _valid_receipt()
    raw_receipt["native_frame_capture"]["frames"][-1]["post_draw_frame"] += 1
    gate = _gate(raw_receipt)
    runtime_receipt = {"pass": True, "reason": "driver_ok"}
    summary = {"admissible": True, "reason": "analyzer_ok"}
    contract = {"pass": True, "reason": "full_grid_receipt_contract_ok"}
    assert gate["temporal_gate_pass"] is True
    assert gate["temporal_identity_join_pass"] is False
    assert RUNNER_MODULE._enforce_same_run_temporal_identity_gate(
        runtime_receipt, summary, contract, gate
    ) is False
    assert runtime_receipt == {
        "pass": False,
        "reason": "same_run_temporal_identity_join_failed",
    }
    assert summary["admissible"] is False
    assert contract["pass"] is False
    assert contract["same_run_temporal_identity_join_pass"] is False


def test_slow_joined_capture_keeps_full_grid_temporal_proof_valid() -> None:
    raw_receipt = _valid_receipt()
    timing = raw_receipt["same_run_temporal_performance_timing"]
    record = timing["records"][3]
    interval = record["wall_interval"]
    interval["end_usec"] = interval["start_usec"] + 20_000
    interval["duration_ms"] = 20.0
    record["deadline_met"] = False
    for later in timing["records"][4:]:
        later["wall_interval"]["start_usec"] += 4_000
        later["wall_interval"]["end_usec"] += 4_000
    timing["deadline_met_count"] = 6
    timing["capture_mode_deadline_pass"] = False
    gate = _gate(raw_receipt)
    runtime_receipt = {"pass": True, "reason": "driver_ok"}
    summary = {"admissible": True, "reason": "analyzer_ok"}
    contract = {"pass": True, "reason": "full_grid_receipt_contract_ok"}
    assert gate["pass"] is False
    assert gate["capture_mode_deadline_pass"] is False
    assert gate["temporal_identity_join_pass"] is True
    assert RUNNER_MODULE._enforce_same_run_temporal_identity_gate(
        runtime_receipt, summary, contract, gate
    ) is True
    assert runtime_receipt["pass"] is True
    assert summary["admissible"] is True
    assert contract["pass"] is True
    assert contract["same_run_temporal_identity_join_pass"] is True
    assert contract["same_run_temporal_identity_join_reason"] == (
        "temporal_analyzer_and_exact_identity_join_passed"
    )
