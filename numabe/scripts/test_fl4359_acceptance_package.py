#!/usr/bin/env python3
"""Contract tests for the current-only FL-4431 runtime package."""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run_fl4359_acceptance_package.py"
HEADED_RUNNER = ROOT / "scripts/run_fl4359_headed_movement.py"
CHILD_RUNNER = ROOT / "scripts/run_fl4265_airplane_proof.py"
SOURCE_HEAD = "1234567890abcdef1234567890abcdef12345678"
CLIENT_RUNTIME_FINGERPRINT = "a" * 64
SERVER_RUNTIME_FINGERPRINT = "b" * 64
VISUAL_FRAME_TARGET_COUNT = 10
VISUAL_ROUTE_TARGET_TICKS = [
    30 + index * 60 for index in range(VISUAL_FRAME_TARGET_COUNT)
]
NIGHT_TEMPORAL_ROUTE_TARGET_TICKS = [30, 150, 270]
NIGHT_TEMPORAL_LABELS = ["beam_t0", "beam_t1", "beam_t2"]


def source_identity_receipt() -> dict[str, object]:
    """Return one clean current-source identity shared by every arm."""

    return {
        "git_head": SOURCE_HEAD,
        "client_git_head": SOURCE_HEAD,
        "server_git_head": SOURCE_HEAD,
        "git_identity_valid": True,
        "client_identity_valid": True,
        "server_identity_valid": True,
        "git_dirty_paths": [],
        "client_dirty_paths": [],
        "server_dirty_paths": [],
        "client_runtime_scope_fingerprint": CLIENT_RUNTIME_FINGERPRINT,
        "server_runtime_scope_fingerprint": SERVER_RUNTIME_FINGERPRINT,
    }


def load_runner():
    """Import the real front door so every gate is exercised as shipped."""

    spec = importlib.util.spec_from_file_location(
        "run_fl4359_acceptance_package_contract",
        RUNNER,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_headed_runner():
    """Import the headed completion front door without launching a process."""

    if str(HEADED_RUNNER.parent) not in sys.path:
        sys.path.insert(0, str(HEADED_RUNNER.parent))
    spec = importlib.util.spec_from_file_location(
        "run_fl4359_headed_movement_contract",
        HEADED_RUNNER,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def visual_receipt(
    *,
    view_mode: str,
    frames: int = VISUAL_FRAME_TARGET_COUNT,
) -> dict[str, object]:
    mode_receipts = {
        "perspective": ("true_perspective", 0),
        "ortho": ("ortho", 1),
    }
    camera_label, camera_projection = mode_receipts[view_mode]
    frame_paths = [
        f"/tmp/fl4431-{view_mode}-{route_tick}.png"
        for route_tick in VISUAL_ROUTE_TARGET_TICKS[:frames]
    ]
    receipt = {
        "proof": "moving_current_flight_capture",
        "pass": True,
        "frame_captured": True,
        "clean_play_no_capture": False,
        "secondary_effect_diagnostic_only": False,
        "diagnostic_nonvisual": False,
        "jitter_capture_requested": False,
        "capture_reason": "",
        "server_premounted_primary_airplane_observed": True,
        "airborne_airplane_ready": True,
        "local_airplane_pilot": True,
        "airplane_world_item_count_exact": True,
        "max_airplane_world_item_count": 2,
        "visual_frame_count": frames,
        "visual_capture_limit": VISUAL_FRAME_TARGET_COUNT,
        "visual_route_target_ticks": list(VISUAL_ROUTE_TARGET_TICKS),
        "missing_visual_route_ticks": [],
        "visual_route_ticks_exact": True,
        "visual_frame_paths": frame_paths,
        "visual_frame_samples": [
            {"route_tick": route_tick, "path": path}
            for route_tick, path in zip(
                VISUAL_ROUTE_TARGET_TICKS[:frames],
                frame_paths,
            )
        ],
        "camera_view_mode_label": camera_label,
        "camera_projection": camera_projection,
    }
    receipt.update(source_identity_receipt())
    return receipt


def clean_receipt(
    *,
    view_mode: str,
    route_ticks: int = 240,
    frame_times_ms: list[float] | None = None,
    measured_fps: float | None = None,
    fps_frame_time_ms: float | None = None,
) -> dict[str, object]:
    mode_receipts = {
        "perspective": ("true_perspective", 0),
        "ortho": ("ortho", 1),
    }
    camera_label, camera_projection = mode_receipts[view_mode]
    if frame_times_ms is None:
        frame_times_ms = [16.6, 16.7] * 240
    mean_frame_time_ms = sum(frame_times_ms) / len(frame_times_ms)
    derived_fps = 1000.0 / mean_frame_time_ms
    route_start_tick = 30
    route_end_tick = route_start_tick + route_ticks
    receipt = {
        "proof": "moving_current_flight_capture",
        "pass": True,
        "clean_play_no_capture": True,
        "clean_play_timing_uncontaminated": True,
        "secondary_effect_diagnostic_only": False,
        "diagnostic_nonvisual": False,
        "frame_captured": False,
        "jitter_capture_requested": False,
        "visual_frame_count": 0,
        "camera_view_mode_label": camera_label,
        "camera_projection": camera_projection,
        "requested_route_tick_duration": route_ticks,
        "clean_route_start_tick": route_start_tick,
        "clean_route_start_tick_overshoot": 0,
        "clean_route_end_tick": route_end_tick,
        "server_route_end_tick_observed": route_end_tick,
        "clean_route_end_tick_overshoot": 0,
        "fps_sample_count": len(frame_times_ms),
        "clean_play_measured_frame_count": len(frame_times_ms),
        "total_frames": len(frame_times_ms),
        "clean_play_frame_times_ms": frame_times_ms,
        "hitch_count_33_333ms": 0,
        "measured_fps": (
            derived_fps if measured_fps is None else measured_fps
        ),
        "fps_frame_time_ms": (
            mean_frame_time_ms
            if fps_frame_time_ms is None
            else fps_frame_time_ms
        ),
    }
    receipt.update(source_identity_receipt())
    return receipt


def secondary_receipt() -> dict[str, object]:
    receipt = {
        "proof": "moving_current_flight_capture",
        "pass": True,
        "secondary_effect_diagnostic_only": True,
        "diagnostic_nonvisual": True,
        "frame_captured": False,
        "product_mat207_contribution_receipt_available": True,
        "host_vapor_sample_count": 48,
        "retained_mat207_face_count": 96,
        "product_mat207_contributed_subcell_count": 12,
    }
    receipt.update(source_identity_receipt())
    return receipt


def night_temporal_receipt() -> dict[str, object]:
    frame_paths = [
        f"/tmp/fl4431-night-{route_tick}.png"
        for route_tick in NIGHT_TEMPORAL_ROUTE_TARGET_TICKS
    ]
    capture_id = "current-night-temporal-123"
    samples: list[dict[str, object]] = []
    required_counts = {
        "lighthouse_beam_contributed_subcell_count": 12,
        "bonfire_flame_contributed_subcell_count": 8,
        "bonfire_light_contributed_subcell_count": 9,
        "bonfire_smoke_contributed_subcell_count": 5,
        "lighthouse_tower_selected_subcell_count": 18,
        "terrain_selected_subcell_count": 120,
        "water_selected_subcell_count": 40,
    }
    for index, (label, route_tick, path) in enumerate(zip(
        NIGHT_TEMPORAL_LABELS,
        NIGHT_TEMPORAL_ROUTE_TARGET_TICKS,
        frame_paths,
    )):
        offset = (2.0 * 3.141592653589793 / 3.0) * index
        samples.append({
            "label": label,
            "route_tick": route_tick,
            "frame_index": index,
            "path": path,
            "temporal_capture_id": capture_id,
            "png_capture_owner": "run_fl4265_airplane_dogfight_proof.gd",
            "png_captured_in_same_process": True,
            "airborne_airplane_ready": True,
            "lighthouse_record_published": True,
            "bonfire_record_published": True,
            "product_frame_time_seconds": 10.0 + index,
            "firefly_record_count": 2,
            "firefly_record_published": True,
            "beam_phase_offset_target": offset,
            "beam_phase_offset_measured": offset,
            "lighthouse_beam_azimuth": 0.25 + offset,
            **required_counts,
        })
    receipt: dict[str, object] = {
        "proof": "moving_current_flight_capture",
        "pass": True,
        "night_airborne_temporal_sequence": True,
        "temporal_capture_id": capture_id,
        "visual_capture_owner": "run_fl4265_airplane_dogfight_proof.gd",
        "frame_captured": True,
        "clean_play_no_capture": False,
        "secondary_effect_diagnostic_only": False,
        "diagnostic_nonvisual": False,
        "jitter_capture_requested": False,
        "capture_reason": "",
        "server_premounted_primary_airplane_observed": True,
        "airborne_airplane_ready": True,
        "local_airplane_pilot": True,
        "camera_view_mode_label": "true_perspective",
        "camera_projection": 0,
        "visual_frame_count": 3,
        "visual_route_target_ticks": list(
            NIGHT_TEMPORAL_ROUTE_TARGET_TICKS
        ),
        "missing_visual_route_ticks": [],
        "visual_route_ticks_exact": True,
        "visual_frame_paths": frame_paths,
        "visual_frame_samples": samples,
    }
    receipt.update(source_identity_receipt())
    return receipt


def visual_result(module, view_mode: str) -> dict[str, object]:
    receipt = visual_receipt(view_mode=view_mode)
    return {
        "returncode": 0,
        "renderer": "current",
        "view_mode": view_mode,
        "visual_evidence": True,
        "performance_evidence": False,
        "frame_count": VISUAL_FRAME_TARGET_COUNT,
        "frame_paths": list(receipt["visual_frame_paths"]),
        "receipt": receipt,
        "runtime_args": list(module.CURRENT_VISUAL_RUNTIME_ARGS[view_mode]),
    }


def night_temporal_result(module) -> dict[str, object]:
    receipt = night_temporal_receipt()
    return {
        "returncode": 0,
        "renderer": "current",
        "view_mode": "perspective",
        "visual_evidence": True,
        "temporal_evidence": True,
        "performance_evidence": False,
        "frame_count": 3,
        "frame_paths": list(receipt["visual_frame_paths"]),
        "receipt": receipt,
        "runtime_args": list(module.CURRENT_NIGHT_TEMPORAL_RUNTIME_ARGS),
    }


def visual_gate_kwargs(view_mode: str) -> dict[str, object]:
    return {
        "expected_view_mode": view_mode,
        "expected_frame_count": VISUAL_FRAME_TARGET_COUNT,
        "expected_route_ticks": list(VISUAL_ROUTE_TARGET_TICKS),
        "expected_head": SOURCE_HEAD,
        "expected_client_fingerprint": CLIENT_RUNTIME_FINGERPRINT,
        "expected_server_fingerprint": SERVER_RUNTIME_FINGERPRINT,
    }


def clean_gate_kwargs(view_mode: str) -> dict[str, object]:
    return {
        "expected_route_ticks": 240,
        "expected_view_mode": view_mode,
        "expected_head": SOURCE_HEAD,
        "expected_client_fingerprint": CLIENT_RUNTIME_FINGERPRINT,
        "expected_server_fingerprint": SERVER_RUNTIME_FINGERPRINT,
    }


def secondary_gate_kwargs() -> dict[str, object]:
    return {
        "expected_head": SOURCE_HEAD,
        "expected_client_fingerprint": CLIENT_RUNTIME_FINGERPRINT,
        "expected_server_fingerprint": SERVER_RUNTIME_FINGERPRINT,
    }


def ready_package(module) -> dict[str, object]:
    """Build one package whose runtime arms share exact source provenance."""

    return {
        "capture_requested": True,
        "dry_run": False,
        "static_checks_skipped": False,
        "dirty_diagnostic_capture_allowed": False,
        "static_checks": [{"returncode": 0}],
        "head": SOURCE_HEAD,
        "client_runtime_scope_fingerprint": CLIENT_RUNTIME_FINGERPRINT,
        "server_runtime_scope_fingerprint": SERVER_RUNTIME_FINGERPRINT,
        "visual_frame_target_count": VISUAL_FRAME_TARGET_COUNT,
        "visual_route_target_ticks": list(VISUAL_ROUTE_TARGET_TICKS),
        "clean_play_route_ticks": 240,
        "current_visual_perspective": visual_result(
            module,
            "perspective",
        ),
        "current_visual_ortho": visual_result(module, "ortho"),
        "current_night_temporal": night_temporal_result(module),
        "current_secondary_effect": secondary_result(module),
        "current_clean_play_perspective": clean_result(
            module,
            "perspective",
        ),
        "current_clean_play_ortho": clean_result(module, "ortho"),
        "current_full_grid_temporal_oracle": full_grid_oracle_result(module),
    }


def clean_result(module, view_mode: str) -> dict[str, object]:
    return {
        "returncode": 0,
        "renderer": "current",
        "view_mode": view_mode,
        "visual_evidence": False,
        "performance_evidence": True,
        "receipt": clean_receipt(view_mode=view_mode),
        "runtime_args": list(
            module.CURRENT_CLEAN_PLAY_RUNTIME_ARGS[view_mode]
        ),
    }


def replace_clean_frame_times(
    receipt: dict[str, object],
    frame_times_ms: list[float],
    *,
    hitch_count: int = 0,
) -> dict[str, object]:
    updated = dict(receipt)
    mean_frame_time_ms = sum(frame_times_ms) / len(frame_times_ms)
    sample_count = len(frame_times_ms)
    updated.update(
        {
            "fps_sample_count": sample_count,
            "clean_play_measured_frame_count": sample_count,
            "total_frames": sample_count,
            "clean_play_frame_times_ms": frame_times_ms,
            "hitch_count_33_333ms": hitch_count,
            "measured_fps": 1000.0 / mean_frame_time_ms,
            "fps_frame_time_ms": mean_frame_time_ms,
        }
    )
    return updated


def secondary_result(module) -> dict[str, object]:
    return {
        "returncode": 0,
        "renderer": "current",
        "visual_evidence": False,
        "performance_evidence": False,
        "diagnostic_attachment": True,
        "receipt": secondary_receipt(),
        "runtime_args": list(module.CURRENT_SECONDARY_EFFECT_RUNTIME_ARGS),
    }


def full_grid_oracle_result(module) -> dict[str, object]:
    """Return one exhaustive zero-error oracle arm at the package source."""

    return {
        "returncode": 0,
        "arm": "current_full_grid_temporal_oracle",
        "renderer": "current",
        "visual_evidence": False,
        "performance_evidence": False,
        "temporal_geometry_evidence": True,
        "proof_profile": module.FULL_GRID_ORACLE_PROFILE,
        "receipt": {
            "wrapper_git_head": SOURCE_HEAD,
            "proof_profile": module.FULL_GRID_ORACLE_PROFILE,
            "pass": True,
            "analyzer_pass": True,
            "authority_tick_continuity": {"temporal_claim_valid": True},
        },
        "analysis_summary": {
            "proof_profile": module.FULL_GRID_ORACLE_PROFILE,
            "same_frame_oracle_exhaustive_frame_coverage": True,
            "same_frame_oracle_temporal_sequence_valid": True,
            "full_grid_temporal_oracle_valid": True,
            "full_grid_temporal_oracle_geometry_error_rows": 0,
            "full_grid_temporal_oracle_structural_geometry_error_rows": 0,
            "full_grid_temporal_oracle_finite_splat_inadmissible_rows": 0,
            "full_grid_temporal_oracle_changing_reference_key_state_pairs": 0,
            "full_grid_temporal_oracle_source_probe_set_changes": 0,
            "same_frame_oracle_exact_sample_index_set_per_frame": {
                "10": True,
            },
            "same_frame_oracle_authority_queue_window_valid": True,
            "same_frame_oracle_authority_tick_identity_valid": True,
            "full_grid_temporal_oracle_defect_map_complete": True,
            "full_grid_temporal_oracle_defect_map_sha256": "a" * 64,
            "full_grid_temporal_oracle_mismatch_decomposition_valid": True,
            "full_grid_temporal_oracle_geometry_decomposition_valid": True,
            "full_grid_temporal_oracle_finite_splat_decomposition_valid": True,
            "full_grid_temporal_oracle_product_exact_identity_available": True,
            "full_grid_temporal_oracle_product_translation_error_rows": 0,
            "full_grid_temporal_oracle_complete_glyph_mask_hash_available": True,
            "full_grid_temporal_oracle_multi_glyph_mask_cells": 0,
            "full_grid_temporal_oracle_complete_glyph_mask_rows": 32092,
            "full_grid_temporal_oracle_incomplete_glyph_mask_rows": 0,
            "full_grid_temporal_oracle_unavailable_glyph_mask_rows": 0,
            "full_grid_temporal_oracle_duplicate_components": 0,
            "full_grid_temporal_oracle_out_of_place_identities": 0,
            "full_grid_temporal_oracle_structural_duplicate_components": 0,
            "full_grid_temporal_oracle_structural_out_of_place_identities": 0,
            "full_grid_temporal_oracle_candidate_tuple_duplicate_components": 0,
            "full_grid_temporal_oracle_first_divergence_unknown": 0,
            "full_grid_temporal_oracle_unclassified_subcells": 0,
            "full_grid_temporal_oracle_normalization_invalid_rows": 0,
            "terrain_product_homogeneity_source": "final_product_b55",
            "terrain_product_homogeneity_rows": 100,
            "terrain_product_homogeneity_invalid_rows": 0,
            "terrain_product_dominant_material_ratio": 0.5,
            "terrain_product_homogeneity_baseline_calibrated": True,
            "terrain_product_final_paint_eligible_rows": 100,
            "terrain_product_final_paint_actor_overlay_rows": 0,
            "terrain_product_final_paint_glyph_join_rows": 100,
            "terrain_product_final_paint_colour_join_rows": 100,
            "terrain_product_final_paint_missing_slot_rows": 0,
            "terrain_product_final_paint_missing_colour_rows": 0,
            "product_admission_contingency": {
                "product_admission_contingency_row_count": 100,
                "product_admission_contingency_counts": {
                    "nonterrain_product_rows": 0,
                    "terrain_admission_witness_missing": 0,
                    "terrain_admission_witness_invalid": 0,
                    "terrain_authored_admission_to_b55_mismatch": 0,
                    "terrain_classifier_to_b55_mismatch": 0,
                    "terrain_b55_to_b43_actor_overlay": 0,
                    "terrain_b55_to_b43_missing": 0,
                    "terrain_b55_to_b43_mismatch": 0,
                    "terrain_full_chain_valid": 100,
                },
                "product_admission_contingency_total": 100,
                "product_admission_contingency_total_matches_input": True,
                "product_admission_terrain_rows": 100,
                "product_admission_terrain_valid_rows": 100,
                "product_admission_contingency_valid": True,
                "product_admission_evidence_valid": True,
                "product_admission_gameplay_chain_valid": True,
                "product_admission_gameplay_chain_mismatch_rows": 0,
                "product_admission_first_collapse_category": None,
                "product_admission_flow_valid_witness_rows": 100,
                "product_admission_flow_invalid_witness_rows": 0,
                "product_admission_flow_histogram_total": 100,
                "product_admission_flow_histogram_total_matches_valid_witness_rows": True,
            },
            "cell_mask_owner_stability": {
                "counts": {
                    "comparable_cells": 32092,
                    "stable_cells": 32092,
                    "unstable_cells": 0,
                },
                "invalid_cell_count": 0,
                "comparable_pair_count": 4,
                "max_instability_pct": 0.0,
            },
            "gates": {
                "gameplay_moving_camera_jitter_bounded": {"pass": True},
                "gameplay_content_shimmer_bounded": {"pass": True},
                "gameplay_continuous_product_colour_stability_bounded": {
                    "pass": True
                },
                "gameplay_cell_mask_owner_stability_bounded": {"pass": True},
                "evidence_presentation_observer_population_accounted": {
                    "pass": True
                },
                "evidence_presentation_observer_sensitivity_valid": {
                    "pass": True
                },
                "evidence_celestial_time_fixed": {
                    "pass": True
                },
                "evidence_terminal_expected_reprojection_valid": {
                    "pass": True
                },
                "gameplay_terminal_expected_cell_address_exact": {
                    "pass": True
                },
                "gameplay_terminal_expected_reprojection_bounded": {
                    "pass": True,
                    "baseline_calibrated": True,
                    "acceptance_threshold_authorized": True,
                },
                "evidence_product_admission_contingency_valid": {
                    "pass": True,
                },
                "gameplay_product_admission_chain_exact": {
                    "pass": True,
                },
            },
        },
    }


def projection_override_matches(source: str) -> list[str]:
    """Return forbidden projection-owner writes from the moving-flight body."""

    patterns = (
        r"\bset_camera_view_mode\b",
        r"\bset_force_perspective\b",
        r"\.projection\s*=",
    )
    return [pattern for pattern in patterns if re.search(pattern, source)]


def test_module_imports_without_historical_artifacts() -> None:
    module = load_runner()
    assert module.ROOT == ROOT
    assert module.MOVING_CAPTURE_HARNESS.is_file()


def test_git_provenance_preserves_complete_status_past_tail_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_runner()
    exact_head = "1234567890abcdef1234567890abcdef12345678"
    first_line = " M scripts/first_review_grid.py"
    later_lines = [
        f" M scripts/generated/{index:04d}_{'x' * 48}.py"
        for index in range(120)
    ]
    status_stdout = "\n".join([first_line, *later_lines]) + "\n"
    assert len(status_stdout) > 4000
    assert status_stdout[-4000:].splitlines()[0] != first_line

    def fake_run(command, **_kwargs):
        if command == ["git", "rev-parse", "--verify", "HEAD"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"{exact_head}\n",
            )
        if command == ["git", "status", "--porcelain=v1"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=status_stdout,
            )
        raise AssertionError(command)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    head, dirty_paths = module.capture_git_provenance()

    assert head == exact_head
    assert dirty_paths[0] == first_line
    assert dirty_paths == [first_line, *later_lines]


def test_package_fingerprint_matches_child_runner_contract() -> None:
    package = load_runner()
    spec = importlib.util.spec_from_file_location(
        "run_fl4265_airplane_proof_fingerprint_contract",
        CHILD_RUNNER,
    )
    assert spec is not None
    assert spec.loader is not None
    child = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(child)
    for scope in (ROOT / "godot_project", ROOT / "colyseus_server"):
        assert package.capture_runtime_scope_fingerprint(scope) == (
            child._runtime_scope_fingerprint(scope)
        )


def test_cli_has_no_runtime_argument_passthrough() -> None:
    result = subprocess.run(
        [sys.executable, str(RUNNER), "--help"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert result.returncode == 0
    assert "--current-extra-user-arg" not in result.stdout
    assert "--current-metric-readback" not in result.stdout
    assert "--metric-world-sample-stride" not in result.stdout
    assert "--visual-stride" not in result.stdout
    assert "--visual-limit" not in result.stdout
    assert "--visual-start" not in result.stdout


def test_cli_rejects_reduced_visual_target(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--skip-static",
            "--dry-run",
            "--out",
            str(tmp_path),
            "--visual-limit",
            "1",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert result.returncode == 2
    assert "unrecognized arguments: --visual-limit 1" in result.stdout


def test_cli_rejects_animation_freeze_runtime_injection(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--skip-static",
            "--dry-run",
            "--out",
            str(tmp_path),
            "--current-extra-user-arg=--fl4359-freeze-animation",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert result.returncode == 2
    assert "unrecognized arguments" in result.stdout


def test_dry_run_manifest_names_current_source_only(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--skip-static",
            "--dry-run",
            "--out",
            str(tmp_path),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert result.returncode == 0, result.stdout
    receipt = json.loads(
        (tmp_path / "current_package_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["schema"] == "asciicker.fl4431.current_package.v1"
    assert receipt["status"] == "dry_run_no_capture"
    assert receipt["capture_requested"] is False
    assert receipt["presentation_size"] == [3600, 2252]
    assert receipt["target_size"] == [3600, 2260]
    assert receipt["ansi_size"] == [180, 113]
    assert receipt["clean_play_route_ticks"] == 240
    assert receipt["clean_play_minimum_fps"] == 60.0
    assert receipt["visual_frame_target_count"] == 10
    assert receipt["visual_route_target_ticks"] == VISUAL_ROUTE_TARGET_TICKS
    assert receipt["night_temporal_frame_target_count"] == 3
    assert (
        receipt["night_temporal_route_target_ticks"]
        == NIGHT_TEMPORAL_ROUTE_TARGET_TICKS
    )
    assert len(receipt["client_runtime_scope_fingerprint"]) == 64
    assert len(receipt["server_runtime_scope_fingerprint"]) == 64
    assert receipt["visual_view_modes"] == ["perspective", "ortho"]
    assert receipt["clean_play_view_modes"] == ["perspective", "ortho"]
    assert receipt["static_checks_skipped"] is True
    assert receipt["dirty_diagnostic_capture_allowed"] is False
    assert receipt["owned_runtime_args"] == {
        "current_visual_perspective": ["--fl4270-force-perspective"],
        "current_visual_ortho": ["--fl4270-force-ortho"],
        "current_secondary_effect": [
            "--fl4270-force-perspective",
            "--fl4431-secondary-effect-evidence",
        ],
        "current_night_temporal": [
            "--fl4270-force-perspective",
            "--manual-environment-night",
            "--force-weather=clear",
            "--force-baseline-feature=night,lighthouse_light,bonfire_light,firefly_light",
            "--fl4431-secondary-effect-evidence",
            "--fl4431-night-airborne-temporal-sequence",
            "--fl4431-night-temporal-labels=beam_t0,beam_t1,beam_t2",
            "--fl4431-night-temporal-phase-offsets=0.0,2.0943951023931953,4.1887902047863905",
        ],
        "current_clean_play_perspective": [
            "--fl4270-force-perspective"
        ],
        "current_clean_play_ortho": ["--fl4270-force-ortho"],
    }


@pytest.mark.parametrize(
    ("view_mode", "force_arg"),
    (
        ("perspective", "--fl4270-force-perspective"),
        ("ortho", "--fl4270-force-ortho"),
    ),
)
def test_visual_commands_use_exact_projection_without_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    view_mode: str,
    force_arg: str,
) -> None:
    module = load_runner()
    captured: dict[str, object] = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        return {"returncode": 0, "cmd": command}

    monkeypatch.setattr(module, "run_cmd", fake_run)
    monkeypatch.setattr(
        module,
        "load_json",
        lambda _path: visual_receipt(view_mode=view_mode, frames=0),
    )
    result = module.run_current_visual_arm(
        project=ROOT / "godot_project",
        colyseus_dir=ROOT / "colyseus_server",
        out_dir=tmp_path,
        port=2701,
        frames=600,
        visual_stride=60,
        visual_limit=10,
        visual_start=0,
        godot_timeout=120,
        server_timeout=45,
        view_mode=view_mode,
    )
    command = captured["command"]
    assert isinstance(command, list)
    assert str(ROOT / "godot_project") in command
    assert "--fl4359-jitter-capture" not in command
    assert "--fl4359-jitter-capture-frame-start" not in command
    assert "--fl4359-jitter-capture-frame-limit" not in command
    assert "--fl4359-jitter-capture-world-sample-stride" not in command
    assert "--fl4431-secondary-effect-diagnostic-only" not in command
    assert module.SECONDARY_EFFECT_EVIDENCE_ARG not in " ".join(command)
    assert "--fl4359-freeze-animation" not in " ".join(command)
    assert f"--extra-user-arg={force_arg}" in command
    assert [
        token
        for token in command
        if token.startswith("--extra-user-arg=")
    ] == [f"--extra-user-arg={force_arg}"]
    assert result["runtime_args"] == list(
        module.CURRENT_VISUAL_RUNTIME_ARGS[view_mode]
    )
    assert result["renderer"] == "current"
    assert result["view_mode"] == view_mode
    assert result["arm"] == f"current_visual_{view_mode}"


def test_night_temporal_command_owns_current_night_and_effect_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_runner()
    captured: dict[str, object] = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        return {"returncode": 0, "cmd": command}

    monkeypatch.setattr(module, "run_cmd", fake_run)
    monkeypatch.setattr(module, "load_json", lambda _path: {})
    result = module.run_current_night_temporal_arm(
        project=ROOT / "godot_project",
        colyseus_dir=ROOT / "colyseus_server",
        out_dir=tmp_path,
        port=2702,
        godot_timeout=120,
        server_timeout=45,
    )
    command = captured["command"]
    assert isinstance(command, list)
    runtime_args = [
        token.removeprefix("--extra-user-arg=")
        for token in command
        if token.startswith("--extra-user-arg=")
    ]
    assert runtime_args == list(module.CURRENT_NIGHT_TEMPORAL_RUNTIME_ARGS)
    assert "--fl4359-clean-play-no-capture" not in command
    assert "--fl4431-secondary-effect-diagnostic-only" not in command
    assert "--fl4359-jitter-capture" not in command
    assert (
        f"--extra-user-arg={module.SECONDARY_EFFECT_EVIDENCE_ARG}"
        in command
    )
    assert command[
        command.index("--fl4359-visual-capture-stride") + 1
    ] == "120"
    assert command[
        command.index("--fl4359-visual-capture-limit") + 1
    ] == "3"
    assert command[
        command.index("--fl4359-visual-capture-start-frame") + 1
    ] == "0"
    assert result["arm"] == "current_night_temporal"
    assert result["visual_evidence"] is True
    assert result["temporal_evidence"] is True
    assert result["performance_evidence"] is False


@pytest.mark.parametrize(
    ("view_mode", "force_arg"),
    (
        ("perspective", "--fl4270-force-perspective"),
        ("ortho", "--fl4270-force-ortho"),
    ),
)
def test_clean_play_commands_have_explicit_projection_without_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    view_mode: str,
    force_arg: str,
) -> None:
    module = load_runner()
    captured: dict[str, object] = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        return {"returncode": 0, "cmd": command}

    monkeypatch.setattr(module, "run_cmd", fake_run)
    monkeypatch.setattr(
        module,
        "load_json",
        lambda _path: clean_receipt(view_mode=view_mode),
    )
    result = module.run_current_clean_play_arm(
        project=ROOT / "godot_project",
        colyseus_dir=ROOT / "colyseus_server",
        out_dir=tmp_path,
        port=2702,
        route_ticks=240,
        godot_timeout=120,
        server_timeout=45,
        view_mode=view_mode,
    )
    command = captured["command"]
    assert isinstance(command, list)
    assert "--fl4359-clean-play-no-capture" in command
    assert "--fl4359-clean-play-frames" in command
    assert "--fl4359-jitter-capture" not in command
    assert "--fl4359-visual-capture-limit" not in command
    assert "--fl4431-secondary-effect-diagnostic-only" not in command
    assert (
        "--extra-user-arg=--fl4431-secondary-effect-evidence"
        not in command
    )
    assert f"--extra-user-arg={force_arg}" in command
    assert [
        token
        for token in command
        if token.startswith("--extra-user-arg=")
    ] == [f"--extra-user-arg={force_arg}"]
    assert "--fl4359-freeze-animation" not in " ".join(command)
    assert result["view_mode"] == view_mode
    assert result["runtime_args"] == [force_arg]


def test_clean_play_harness_cannot_override_package_projection() -> None:
    harness = (
        ROOT
        / "godot_project/tools/proofs/run_fl4265_airplane_dogfight_proof.gd"
    ).read_text(encoding="utf-8")
    capture_body = harness.split(
        "func _capture_fl4359_perspective_flight() -> void:",
        1,
    )[1].split("\nfunc ", 1)[0]
    assert projection_override_matches(capture_body) == []
    assert (
        capture_body.count(
            '"proof": "moving_current_flight_capture"'
        )
        == 2
    )
    assert '"proof": "moving_perspective_capture"' not in capture_body


@pytest.mark.parametrize(
    "projection_override",
    (
        'player.call("set_camera_view_mode", 0)',
        'player.call("set_force_perspective", false)',
        "camera.projection = Camera3D.PROJECTION_ORTHOGONAL",
        "camera.projection=Camera3D.PROJECTION_PERSPECTIVE",
    ),
)
def test_projection_owner_guard_kills_hidden_writer_mutants(
    projection_override: str,
) -> None:
    assert projection_override_matches(projection_override)


def test_secondary_effect_command_is_nonvisual_attachment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_runner()
    captured: dict[str, object] = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        return {"returncode": 0, "cmd": command}

    monkeypatch.setattr(module, "run_cmd", fake_run)
    monkeypatch.setattr(module, "load_json", lambda _path: secondary_receipt())
    result = module.run_current_secondary_effect_arm(
        project=ROOT / "godot_project",
        colyseus_dir=ROOT / "colyseus_server",
        out_dir=tmp_path,
        port=2703,
        godot_timeout=120,
        server_timeout=45,
    )
    command = captured["command"]
    assert isinstance(command, list)
    assert "--fl4431-secondary-effect-diagnostic-only" in command
    assert (
        f"--extra-user-arg={module.SECONDARY_EFFECT_EVIDENCE_ARG}" in command
    )
    assert "--extra-user-arg=--fl4270-force-perspective" in command
    assert [
        token
        for token in command
        if token.startswith("--extra-user-arg=")
    ] == [
        "--extra-user-arg=--fl4270-force-perspective",
        f"--extra-user-arg={module.SECONDARY_EFFECT_EVIDENCE_ARG}",
    ]
    assert "--fl4359-freeze-animation" not in " ".join(command)
    assert result["visual_evidence"] is False
    assert result["performance_evidence"] is False


def test_full_grid_oracle_command_uses_exhaustive_current_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_runner()
    captured: dict[str, object] = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        return {"returncode": 1, "cmd": command}

    monkeypatch.setattr(module, "run_cmd", fake_run)
    monkeypatch.setattr(module, "load_json", lambda _path: {})
    result = module.run_current_full_grid_oracle_arm(
        project=ROOT / "godot_project",
        colyseus_dir=ROOT / "colyseus_server",
        out_dir=tmp_path,
        port=2703,
        godot_timeout=120,
        server_timeout=45,
    )
    command = captured["command"]
    assert isinstance(command, list)
    assert str(module.FULL_GRID_ORACLE_RUNNER) in command
    assert command[command.index("--camera-mode") + 1] == "plane_air"
    assert command[command.index("--analyzer-proof-profile") + 1] == (
        module.FULL_GRID_ORACLE_PROFILE
    )
    assert command[command.index("--product-time") + 1] == "17.0"
    assert command[command.index("--day-cycle-time") + 1] == "0.5"
    assert "--allow-large-jitter-capture" in command
    assert result["temporal_geometry_evidence"] is True


@pytest.mark.parametrize("view_mode", ("perspective", "ortho"))
def test_current_visual_gate_rejects_runtime_argument_contamination(
    view_mode: str,
) -> None:
    module = load_runner()
    valid = visual_result(module, view_mode)
    assert module.current_visual_arm_ok(
        valid,
        **visual_gate_kwargs(view_mode),
    )

    animation_freeze = dict(valid)
    animation_freeze["runtime_args"] = [
        *animation_freeze["runtime_args"],
        "--fl4359-freeze-animation",
    ]
    assert not module.current_visual_arm_ok(
        animation_freeze,
        **visual_gate_kwargs(view_mode),
    )

    arbitrary_behavior_flag = dict(valid)
    arbitrary_behavior_flag["runtime_args"] = [
        *arbitrary_behavior_flag["runtime_args"],
        "--unowned-behavior-change",
    ]
    assert not module.current_visual_arm_ok(
        arbitrary_behavior_flag,
        **visual_gate_kwargs(view_mode),
    )

    other_mode = "ortho" if view_mode == "perspective" else "perspective"
    swapped_projection_arg = dict(valid)
    swapped_projection_arg["runtime_args"] = list(
        module.CURRENT_VISUAL_RUNTIME_ARGS[other_mode]
    )
    assert not module.current_visual_arm_ok(
        swapped_projection_arg,
        **visual_gate_kwargs(view_mode),
    )


@pytest.mark.parametrize("view_mode", ("perspective", "ortho"))
def test_current_visual_gate_rejects_missing_real_airborne_facts(
    view_mode: str,
) -> None:
    module = load_runner()
    for field in (
        "server_premounted_primary_airplane_observed",
        "airborne_airplane_ready",
        "local_airplane_pilot",
        "airplane_world_item_count_exact",
    ):
        mutant = visual_result(module, view_mode)
        mutant["receipt"] = dict(mutant["receipt"])
        mutant["receipt"][field] = False
        assert not module.current_visual_arm_ok(
            mutant,
            **visual_gate_kwargs(view_mode),
        ), field


@pytest.mark.parametrize(
    ("view_mode", "wrong_label", "wrong_projection"),
    (
        ("perspective", "ortho", 1),
        ("ortho", "true_perspective", 0),
    ),
)
def test_current_visual_gate_requires_exact_projection_receipt(
    view_mode: str,
    wrong_label: str,
    wrong_projection: int,
) -> None:
    module = load_runner()
    valid = visual_result(module, view_mode)
    assert module.current_visual_arm_ok(
        valid,
        **visual_gate_kwargs(view_mode),
    )

    wrong_label_result = visual_result(module, view_mode)
    wrong_label_result["receipt"] = dict(wrong_label_result["receipt"])
    wrong_label_result["receipt"]["camera_view_mode_label"] = wrong_label
    assert not module.current_visual_arm_ok(
        wrong_label_result,
        **visual_gate_kwargs(view_mode),
    )

    wrong_projection_result = visual_result(module, view_mode)
    wrong_projection_result["receipt"] = dict(
        wrong_projection_result["receipt"]
    )
    wrong_projection_result["receipt"]["camera_projection"] = wrong_projection
    assert not module.current_visual_arm_ok(
        wrong_projection_result,
        **visual_gate_kwargs(view_mode),
    )

    stale_proof_kind = visual_result(module, view_mode)
    stale_proof_kind["receipt"] = dict(stale_proof_kind["receipt"])
    stale_proof_kind["receipt"]["proof"] = "moving_perspective_capture"
    assert not module.current_visual_arm_ok(
        stale_proof_kind,
        **visual_gate_kwargs(view_mode),
    )

    mismatched_frame_count = visual_result(module, view_mode)
    mismatched_frame_count["frame_count"] = 2
    assert not module.current_visual_arm_ok(
        mismatched_frame_count,
        **visual_gate_kwargs(view_mode),
    )

    for field in (
        "secondary_effect_diagnostic_only",
        "diagnostic_nonvisual",
    ):
        contaminated = visual_result(module, view_mode)
        contaminated["receipt"] = dict(contaminated["receipt"])
        contaminated["receipt"][field] = True
        assert not module.current_visual_arm_ok(
            contaminated,
            **visual_gate_kwargs(view_mode),
        ), field


@pytest.mark.parametrize("view_mode", ("perspective", "ortho"))
def test_current_visual_gate_requires_every_declared_target(
    view_mode: str,
) -> None:
    module = load_runner()
    valid = visual_result(module, view_mode)
    assert module.current_visual_arm_ok(
        valid,
        **visual_gate_kwargs(view_mode),
    )

    missing = visual_result(module, view_mode)
    missing_receipt = dict(missing["receipt"])
    missing_paths = list(missing_receipt["visual_frame_paths"])[:-1]
    missing_samples = list(missing_receipt["visual_frame_samples"])[:-1]
    missing_receipt["visual_frame_count"] = len(missing_paths)
    missing_receipt["visual_frame_paths"] = missing_paths
    missing_receipt["visual_frame_samples"] = missing_samples
    missing["receipt"] = missing_receipt
    missing["frame_count"] = len(missing_paths)
    missing["frame_paths"] = missing_paths
    assert not module.current_visual_arm_ok(
        missing,
        **visual_gate_kwargs(view_mode),
    )

    extra = visual_result(module, view_mode)
    extra_receipt = dict(extra["receipt"])
    extra_tick = VISUAL_ROUTE_TARGET_TICKS[-1] + 60
    extra_path = f"/tmp/fl4431-{view_mode}-{extra_tick}.png"
    extra_paths = [*extra_receipt["visual_frame_paths"], extra_path]
    extra_samples = [
        *extra_receipt["visual_frame_samples"],
        {"route_tick": extra_tick, "path": extra_path},
    ]
    extra_receipt["visual_frame_count"] = len(extra_paths)
    extra_receipt["visual_frame_paths"] = extra_paths
    extra_receipt["visual_frame_samples"] = extra_samples
    extra["receipt"] = extra_receipt
    extra["frame_count"] = len(extra_paths)
    extra["frame_paths"] = extra_paths
    assert not module.current_visual_arm_ok(
        extra,
        **visual_gate_kwargs(view_mode),
    )

    shifted_tick = visual_result(module, view_mode)
    shifted_receipt = dict(shifted_tick["receipt"])
    shifted_samples = [
        dict(sample) for sample in shifted_receipt["visual_frame_samples"]
    ]
    shifted_samples[3]["route_tick"] += 1
    shifted_receipt["visual_frame_samples"] = shifted_samples
    shifted_tick["receipt"] = shifted_receipt
    assert not module.current_visual_arm_ok(
        shifted_tick,
        **visual_gate_kwargs(view_mode),
    )


def test_clean_play_gate_separates_route_ticks_from_rendered_samples() -> None:
    module = load_runner()
    valid = clean_result(module, "perspective")
    assert valid["receipt"]["requested_route_tick_duration"] == 240
    assert valid["receipt"]["fps_sample_count"] == 480
    assert module.current_clean_play_arm_ok(
        valid,
        **clean_gate_kwargs("perspective"),
    )

    one_tick_overshoot = clean_result(module, "perspective")
    one_tick_overshoot["receipt"] = dict(one_tick_overshoot["receipt"])
    one_tick_overshoot["receipt"]["server_route_end_tick_observed"] += 1
    one_tick_overshoot["receipt"]["clean_route_end_tick_overshoot"] = 1
    assert module.current_clean_play_arm_ok(
        one_tick_overshoot,
        **clean_gate_kwargs("perspective"),
    )

    one_tick_start = clean_result(module, "perspective")
    one_tick_start["receipt"] = dict(one_tick_start["receipt"])
    one_tick_start["receipt"]["clean_route_start_tick"] += 1
    one_tick_start["receipt"]["clean_route_end_tick"] += 1
    one_tick_start["receipt"]["server_route_end_tick_observed"] += 1
    one_tick_start["receipt"]["clean_route_start_tick_overshoot"] = 1
    assert module.current_clean_play_arm_ok(
        one_tick_start,
        **clean_gate_kwargs("perspective"),
    )

    mutant = clean_result(module, "perspective")
    mutant["receipt"] = dict(mutant["receipt"])
    mutant["receipt"]["fps_sample_count"] = 479
    assert not module.current_clean_play_arm_ok(
        mutant,
        **clean_gate_kwargs("perspective"),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("requested_route_tick_duration", 239),
        ("clean_route_end_tick", 269),
        ("server_route_end_tick_observed", 269),
        ("clean_play_measured_frame_count", 479),
        ("total_frames", 479),
    ),
)
def test_clean_play_gate_rejects_route_and_sample_receipt_mismatch(
    field: str,
    value: int,
) -> None:
    module = load_runner()
    mutant = clean_result(module, "perspective")
    mutant["receipt"] = dict(mutant["receipt"])
    mutant["receipt"][field] = value
    assert not module.current_clean_play_arm_ok(
        mutant,
        **clean_gate_kwargs("perspective"),
    )


@pytest.mark.parametrize("view_mode", ("perspective", "ortho"))
def test_clean_play_gate_requires_sustained_60_fps(
    view_mode: str,
) -> None:
    module = load_runner()
    exact_floor = clean_result(module, view_mode)
    assert module.current_clean_play_arm_ok(
        exact_floor,
        **clean_gate_kwargs(view_mode),
    )

    below_floor = clean_result(module, view_mode)
    below_floor["receipt"] = clean_receipt(
        view_mode=view_mode,
        frame_times_ms=[17.0] * 480,
    )
    assert not module.current_clean_play_arm_ok(
        below_floor,
        **clean_gate_kwargs(view_mode),
    )


def test_clean_play_gate_accepts_normal_frame_jitter() -> None:
    module = load_runner()
    jittered = clean_result(module, "perspective")
    jittered["receipt"] = clean_receipt(
        view_mode="perspective",
        frame_times_ms=[16.6, 16.7] * 240,
    )
    assert module.current_clean_play_arm_ok(
        jittered,
        **clean_gate_kwargs("perspective"),
    )


def test_clean_play_gate_rejects_slow_contiguous_30_frame_segment() -> None:
    module = load_runner()
    mutant = clean_result(module, "perspective")
    mutant["receipt"] = replace_clean_frame_times(
        mutant["receipt"],
        [20.0] * 30 + [15.0] * 450,
    )
    assert float(mutant["receipt"]["measured_fps"]) > 60.0
    assert not module.current_clean_play_arm_ok(
        mutant,
        **clean_gate_kwargs("perspective"),
    )


def test_clean_play_gate_rejects_isolated_100ms_spike() -> None:
    module = load_runner()
    mutant = clean_result(module, "perspective")
    mutant["receipt"] = replace_clean_frame_times(
        mutant["receipt"],
        [100.0] + [15.0] * 479,
        hitch_count=1,
    )
    assert float(mutant["receipt"]["measured_fps"]) > 60.0
    assert not module.current_clean_play_arm_ok(
        mutant,
        **clean_gate_kwargs("perspective"),
    )


@pytest.mark.parametrize("invalid_value", (float("nan"), 0.0))
def test_clean_play_gate_rejects_nonfinite_and_zero_frame_rows(
    invalid_value: float,
) -> None:
    module = load_runner()
    mutant = clean_result(module, "perspective")
    mutant["receipt"] = dict(mutant["receipt"])
    frame_times = list(mutant["receipt"]["clean_play_frame_times_ms"])
    frame_times[17] = invalid_value
    mutant["receipt"]["clean_play_frame_times_ms"] = frame_times
    assert not module.current_clean_play_arm_ok(
        mutant,
        **clean_gate_kwargs("perspective"),
    )


def test_clean_play_gate_rejects_scalar_timing_forgery() -> None:
    module = load_runner()
    mutant = clean_result(module, "perspective")
    mutant["receipt"] = dict(mutant["receipt"])
    mutant["receipt"]["measured_fps"] = 120.0
    mutant["receipt"]["fps_frame_time_ms"] = 8.333333
    assert not module.current_clean_play_arm_ok(
        mutant,
        **clean_gate_kwargs("perspective"),
    )


def test_clean_play_gate_rejects_nonzero_hitch_count() -> None:
    module = load_runner()
    mutant = clean_result(module, "perspective")
    mutant["receipt"] = dict(mutant["receipt"])
    mutant["receipt"]["hitch_count_33_333ms"] = 1
    assert not module.current_clean_play_arm_ok(
        mutant,
        **clean_gate_kwargs("perspective"),
    )


def test_clean_play_gate_rejects_capture_and_readback() -> None:
    module = load_runner()
    for field, value in (
        ("frame_captured", True),
        ("jitter_capture_requested", True),
        ("visual_frame_count", 1),
        ("clean_play_timing_uncontaminated", False),
        ("secondary_effect_diagnostic_only", True),
        ("diagnostic_nonvisual", True),
    ):
        mutant = clean_result(module, "ortho")
        mutant["receipt"] = dict(mutant["receipt"])
        mutant["receipt"][field] = value
        assert not module.current_clean_play_arm_ok(
            mutant,
            **clean_gate_kwargs("ortho"),
        ), field


def test_clean_play_gate_rejects_projection_and_argument_mutants() -> None:
    module = load_runner()
    valid = clean_result(module, "ortho")
    assert module.current_clean_play_arm_ok(
        valid,
        **clean_gate_kwargs("ortho"),
    )

    wrong_label = clean_result(module, "ortho")
    wrong_label["receipt"] = dict(wrong_label["receipt"])
    wrong_label["receipt"]["camera_view_mode_label"] = "true_perspective"
    assert not module.current_clean_play_arm_ok(
        wrong_label,
        **clean_gate_kwargs("ortho"),
    )

    wrong_projection = clean_result(module, "ortho")
    wrong_projection["receipt"] = dict(wrong_projection["receipt"])
    wrong_projection["receipt"]["camera_projection"] = 0
    assert not module.current_clean_play_arm_ok(
        wrong_projection,
        **clean_gate_kwargs("ortho"),
    )

    stale_proof_kind = clean_result(module, "ortho")
    stale_proof_kind["receipt"] = dict(stale_proof_kind["receipt"])
    stale_proof_kind["receipt"]["proof"] = "moving_perspective_capture"
    assert not module.current_clean_play_arm_ok(
        stale_proof_kind,
        **clean_gate_kwargs("ortho"),
    )

    animation_freeze = clean_result(module, "ortho")
    animation_freeze["runtime_args"] = [
        *animation_freeze["runtime_args"],
        "--fl4359-freeze-animation",
    ]
    assert not module.current_clean_play_arm_ok(
        animation_freeze,
        **clean_gate_kwargs("ortho"),
    )


def test_secondary_effect_gate_requires_full_host_retained_product_join() -> None:
    module = load_runner()
    valid = secondary_result(module)
    assert module.current_secondary_effect_arm_ok(
        valid,
        **secondary_gate_kwargs(),
    )

    for field in (
        "host_vapor_sample_count",
        "retained_mat207_face_count",
        "product_mat207_contributed_subcell_count",
    ):
        mutant = secondary_result(module)
        mutant["receipt"] = dict(mutant["receipt"])
        mutant["receipt"][field] = 0
        assert not module.current_secondary_effect_arm_ok(
            mutant,
            **secondary_gate_kwargs(),
        ), field


def test_night_temporal_gate_binds_pixels_motion_and_product_counts() -> None:
    module = load_runner()
    valid = night_temporal_result(module)
    gate_kwargs = {
        "expected_head": SOURCE_HEAD,
        "expected_client_fingerprint": CLIENT_RUNTIME_FINGERPRINT,
        "expected_server_fingerprint": SERVER_RUNTIME_FINGERPRINT,
    }
    assert module.current_night_temporal_arm_ok(valid, **gate_kwargs)

    mutations = (
        ("night_airborne_temporal_sequence", False),
        ("visual_capture_owner", ""),
        ("camera_view_mode_label", "ortho"),
    )
    for field, value in mutations:
        mutant = night_temporal_result(module)
        mutant["receipt"] = dict(mutant["receipt"])
        mutant["receipt"][field] = value
        assert not module.current_night_temporal_arm_ok(
            mutant,
            **gate_kwargs,
        ), field

    for field, value in (
        ("lighthouse_record_published", False),
        ("bonfire_record_published", False),
        ("firefly_record_published", False),
        ("lighthouse_beam_contributed_subcell_count", 0),
        ("bonfire_flame_contributed_subcell_count", 0),
        ("lighthouse_tower_selected_subcell_count", 0),
        ("beam_phase_offset_measured", 0.5),
        ("temporal_capture_id", "different-capture"),
        ("route_tick", 31),
    ):
        mutant = night_temporal_result(module)
        mutant["receipt"] = dict(mutant["receipt"])
        samples = [
            dict(sample)
            for sample in mutant["receipt"]["visual_frame_samples"]
        ]
        samples[0][field] = value
        mutant["receipt"]["visual_frame_samples"] = samples
        assert not module.current_night_temporal_arm_ok(
            mutant,
            **gate_kwargs,
        ), field

    missing_publication = night_temporal_result(module)
    missing_publication["receipt"] = dict(missing_publication["receipt"])
    missing_samples = [
        dict(sample)
        for sample in missing_publication["receipt"]["visual_frame_samples"]
    ]
    del missing_samples[0]["firefly_record_published"]
    missing_publication["receipt"]["visual_frame_samples"] = missing_samples
    assert not module.current_night_temporal_arm_ok(
        missing_publication,
        **gate_kwargs,
    )

    for field, value in (
        ("firefly_record_count", 0),
        ("firefly_record_count", -1),
        ("firefly_record_count", 1.5),
    ):
        mutant = night_temporal_result(module)
        mutant["receipt"] = dict(mutant["receipt"])
        samples = [
            dict(sample)
            for sample in mutant["receipt"]["visual_frame_samples"]
        ]
        samples[0][field] = value
        mutant["receipt"]["visual_frame_samples"] = samples
        assert not module.current_night_temporal_arm_ok(
            mutant,
            **gate_kwargs,
        ), (field, value)

    for mutation in ("missing", "null", "nan", "infinite", "non_increasing"):
        mutant = night_temporal_result(module)
        mutant["receipt"] = dict(mutant["receipt"])
        samples = [
            dict(sample)
            for sample in mutant["receipt"]["visual_frame_samples"]
        ]
        if mutation == "missing":
            del samples[0]["product_frame_time_seconds"]
        elif mutation == "null":
            samples[0]["product_frame_time_seconds"] = None
        elif mutation == "nan":
            samples[0]["product_frame_time_seconds"] = float("nan")
        elif mutation == "infinite":
            samples[0]["product_frame_time_seconds"] = float("inf")
        else:
            samples[1]["product_frame_time_seconds"] = (
                samples[0]["product_frame_time_seconds"]
            )
        mutant["receipt"]["visual_frame_samples"] = samples
        assert not module.current_night_temporal_arm_ok(
            mutant,
            **gate_kwargs,
        ), mutation


def test_night_temporal_source_contract_reads_product_clock_and_firefly_kind() -> None:
    source = (
        ROOT / "godot_project/tools/proofs/run_fl4265_airplane_dogfight_proof.gd"
    ).read_text(encoding="utf-8")
    start = source.index("func _fl4431_night_temporal_frame_fields")
    end = source.index("func _fl4431_night_temporal_samples_complete", start)
    frame_fields_source = source[start:end]
    assert "_fl4431_last_product_frame_time_seconds" in frame_fields_source
    assert "_local_light_last_metrics" in frame_fields_source
    assert "for record_kind in record_kinds" in frame_fields_source
    assert "int(record_kind) == 2" in frame_fields_source
    assert 'fields[\"firefly_record_count\"] = firefly_record_count' in (
        frame_fields_source
    )
    assert (
        'fields[\"firefly_record_published\"] = firefly_record_count > 0'
        in frame_fields_source
    )


def test_package_status_requires_visual_fps_and_full_grid_oracle_surfaces() -> None:
    module = load_runner()
    valid = ready_package(module)
    assert (
        module.package_status(valid)
        == "current_package_ready_for_runtime_review"
    )

    expected = (
        (
            "current_visual_perspective",
            "current_visual_perspective_failed",
        ),
        ("current_visual_ortho", "current_visual_ortho_failed"),
        ("current_night_temporal", "current_night_temporal_failed"),
        ("current_secondary_effect", "current_secondary_effect_failed"),
        (
            "current_clean_play_perspective",
            "current_clean_play_perspective_failed",
        ),
        (
            "current_clean_play_ortho",
            "current_clean_play_ortho_failed",
        ),
        (
            "current_full_grid_temporal_oracle",
            "current_full_grid_temporal_oracle_failed",
        ),
    )
    for key, status in expected:
        mutant = dict(valid)
        mutant[key] = {}
        assert module.package_status(mutant) == status

    swapped_visual_modes = dict(valid)
    swapped_visual_modes["current_visual_perspective"] = visual_result(
        module,
        "ortho",
    )
    swapped_visual_modes["current_visual_ortho"] = visual_result(
        module,
        "perspective",
    )
    assert (
        module.package_status(swapped_visual_modes)
        == "current_visual_perspective_failed"
    )

    reduced_target = ready_package(module)
    reduced_target["visual_frame_target_count"] = 1
    reduced_target["visual_route_target_ticks"] = [30]
    assert (
        module.package_status(reduced_target)
        == "current_visual_target_contract_failed"
    )

    shifted_target = ready_package(module)
    shifted_target["visual_route_target_ticks"] = [
        tick + 1 for tick in VISUAL_ROUTE_TARGET_TICKS
    ]
    assert (
        module.package_status(shifted_target)
        == "current_visual_target_contract_failed"
    )


@pytest.mark.parametrize(
    "field",
    (
        "full_grid_temporal_oracle_structural_geometry_error_rows",
        "full_grid_temporal_oracle_finite_splat_inadmissible_rows",
        "full_grid_temporal_oracle_changing_reference_key_state_pairs",
        "full_grid_temporal_oracle_source_probe_set_changes",
        "full_grid_temporal_oracle_multi_glyph_mask_cells",
        "full_grid_temporal_oracle_incomplete_glyph_mask_rows",
        "full_grid_temporal_oracle_structural_duplicate_components",
        "full_grid_temporal_oracle_structural_out_of_place_identities",
        "full_grid_temporal_oracle_candidate_tuple_duplicate_components",
        "full_grid_temporal_oracle_first_divergence_unknown",
        "full_grid_temporal_oracle_unclassified_subcells",
        "full_grid_temporal_oracle_normalization_invalid_rows",
    ),
)
def test_package_status_rejects_each_nonzero_full_grid_defect(field: str) -> None:
    module = load_runner()
    mutant = ready_package(module)
    arm = dict(mutant["current_full_grid_temporal_oracle"])
    summary = dict(arm["analysis_summary"])
    summary[field] = 1
    arm["analysis_summary"] = summary
    mutant["current_full_grid_temporal_oracle"] = arm
    assert (
        module.package_status(mutant)
        == "current_full_grid_temporal_oracle_failed"
    )


def test_package_status_rejects_whole_cell_mask_owner_instability() -> None:
    module = load_runner()
    package = ready_package(module)
    arm = dict(package["current_full_grid_temporal_oracle"])
    summary = dict(arm["analysis_summary"])
    stability = dict(summary["cell_mask_owner_stability"])
    counts = dict(stability["counts"])
    counts["unstable_cells"] = 1
    stability["counts"] = counts
    summary["cell_mask_owner_stability"] = stability
    arm["analysis_summary"] = summary
    package["current_full_grid_temporal_oracle"] = arm

    assert (
        module.package_status(package)
        == "current_full_grid_temporal_oracle_failed"
    )


@pytest.mark.parametrize(
    "gate_name",
    (
        "gameplay_moving_camera_jitter_bounded",
        "gameplay_content_shimmer_bounded",
        "gameplay_continuous_product_colour_stability_bounded",
    ),
)
def test_package_status_rejects_each_temporal_presentation_failure(
    gate_name: str,
) -> None:
    module = load_runner()
    package = ready_package(module)
    arm = dict(package["current_full_grid_temporal_oracle"])
    summary = dict(arm["analysis_summary"])
    gates = {
        name: dict(gate)
        for name, gate in summary["gates"].items()
    }
    gates[gate_name]["pass"] = False
    summary["gates"] = gates
    arm["analysis_summary"] = summary
    package["current_full_grid_temporal_oracle"] = arm

    assert (
        module.package_status(package)
        == "current_full_grid_temporal_oracle_failed"
    )


@pytest.mark.parametrize(
    "gate_name",
    (
        "evidence_celestial_time_fixed",
        "evidence_terminal_expected_reprojection_valid",
        "gameplay_terminal_expected_cell_address_exact",
    ),
)
def test_package_status_rejects_missing_or_red_terminal_reprojection_gate(
    gate_name: str,
) -> None:
    module = load_runner()
    valid = ready_package(module)

    missing = ready_package(module)
    missing_arm = dict(missing["current_full_grid_temporal_oracle"])
    missing_summary = dict(missing_arm["analysis_summary"])
    missing_gates = dict(missing_summary["gates"])
    missing_gates.pop(gate_name)
    missing_summary["gates"] = missing_gates
    missing_arm["analysis_summary"] = missing_summary
    missing["current_full_grid_temporal_oracle"] = missing_arm
    assert module.package_status(missing) == "current_full_grid_temporal_oracle_failed"

    red = ready_package(module)
    red_arm = dict(red["current_full_grid_temporal_oracle"])
    red_summary = dict(red_arm["analysis_summary"])
    red_gates = {
        name: dict(gate) for name, gate in red_summary["gates"].items()
    }
    red_gates[gate_name]["pass"] = False
    red_summary["gates"] = red_gates
    red_arm["analysis_summary"] = red_summary
    red["current_full_grid_temporal_oracle"] = red_arm
    assert module.package_status(red) == "current_full_grid_temporal_oracle_failed"


def test_package_status_does_not_substitute_legacy_jitter_for_terminal_reprojection() -> None:
    module = load_runner()
    package = ready_package(module)
    arm = dict(package["current_full_grid_temporal_oracle"])
    summary = dict(arm["analysis_summary"])
    gates = {
        name: dict(gate) for name, gate in summary["gates"].items()
    }
    gates.pop("evidence_terminal_expected_reprojection_valid")
    summary["gates"] = gates
    arm["analysis_summary"] = summary
    package["current_full_grid_temporal_oracle"] = arm

    assert gates["gameplay_moving_camera_jitter_bounded"]["pass"] is True
    assert module.package_status(package) == "current_full_grid_temporal_oracle_failed"


def test_package_status_requires_product_admission_structure_and_exact_chain() -> None:
    module = load_runner()
    assert module.package_status(ready_package(module)) == (
        "current_package_ready_for_runtime_review"
    )

    for gate_name in (
        "evidence_product_admission_contingency_valid",
        "gameplay_product_admission_chain_exact",
    ):
        missing = ready_package(module)
        missing_arm = dict(missing["current_full_grid_temporal_oracle"])
        missing_summary = dict(missing_arm["analysis_summary"])
        missing_gates = dict(missing_summary["gates"])
        missing_gates.pop(gate_name)
        missing_summary["gates"] = missing_gates
        missing_arm["analysis_summary"] = missing_summary
        missing["current_full_grid_temporal_oracle"] = missing_arm
        assert module.package_status(missing) == (
            "current_full_grid_temporal_oracle_failed"
        )

        red = ready_package(module)
        red_arm = dict(red["current_full_grid_temporal_oracle"])
        red_summary = dict(red_arm["analysis_summary"])
        red_gates = {
            name: dict(gate) for name, gate in red_summary["gates"].items()
        }
        red_gates[gate_name]["pass"] = False
        red_summary["gates"] = red_gates
        red_arm["analysis_summary"] = red_summary
        red["current_full_grid_temporal_oracle"] = red_arm
        assert module.package_status(red) == (
            "current_full_grid_temporal_oracle_failed"
        )

    malformed = ready_package(module)
    malformed_arm = dict(malformed["current_full_grid_temporal_oracle"])
    malformed_summary = dict(malformed_arm["analysis_summary"])
    malformed_summary["product_admission_contingency"] = {}
    malformed_arm["analysis_summary"] = malformed_summary
    malformed["current_full_grid_temporal_oracle"] = malformed_arm
    assert module.package_status(malformed) == (
        "current_full_grid_temporal_oracle_failed"
    )

    for field in (
        "terrain_admission_witness_missing",
        "terrain_admission_witness_invalid",
    ):
        mutant = ready_package(module)
        mutant_arm = dict(mutant["current_full_grid_temporal_oracle"])
        mutant_summary = dict(mutant_arm["analysis_summary"])
        admission = dict(mutant_summary["product_admission_contingency"])
        counts = dict(admission["product_admission_contingency_counts"])
        counts[field] = 1
        admission["product_admission_contingency_counts"] = counts
        mutant_summary["product_admission_contingency"] = admission
        mutant_arm["analysis_summary"] = mutant_summary
        mutant["current_full_grid_temporal_oracle"] = mutant_arm
        assert module.package_status(mutant) == (
            "current_full_grid_temporal_oracle_failed"
        )

    classifier_red = ready_package(module)
    classifier_arm = dict(classifier_red["current_full_grid_temporal_oracle"])
    classifier_summary = dict(classifier_arm["analysis_summary"])
    classifier_admission = dict(
        classifier_summary["product_admission_contingency"]
    )
    classifier_admission["product_admission_gameplay_chain_valid"] = False
    classifier_admission["product_admission_gameplay_chain_mismatch_rows"] = 1
    classifier_admission["product_admission_first_collapse_category"] = (
        "terrain_classifier_to_b55_mismatch"
    )
    classifier_summary["product_admission_contingency"] = classifier_admission
    classifier_gates = {
        name: dict(gate) for name, gate in classifier_summary["gates"].items()
    }
    classifier_gates["gameplay_product_admission_chain_exact"]["pass"] = False
    classifier_summary["gates"] = classifier_gates
    classifier_arm["analysis_summary"] = classifier_summary
    classifier_red["current_full_grid_temporal_oracle"] = classifier_arm
    assert classifier_admission["product_admission_contingency_valid"] is True
    assert classifier_admission["product_admission_evidence_valid"] is True
    assert classifier_red["current_full_grid_temporal_oracle"][
        "analysis_summary"
    ]["gates"]["evidence_product_admission_contingency_valid"]["pass"] is True
    assert module.package_status(classifier_red) == (
        "current_full_grid_temporal_oracle_failed"
    )


def test_package_status_treats_uncalibrated_pixel_residual_as_diagnostic() -> None:
    module = load_runner()
    package = ready_package(module)
    arm = dict(package["current_full_grid_temporal_oracle"])
    summary = dict(arm["analysis_summary"])
    gates = {
        name: dict(gate) for name, gate in summary["gates"].items()
    }
    gates["gameplay_terminal_expected_reprojection_bounded"][
        "baseline_calibrated"
    ] = False
    gates["gameplay_terminal_expected_reprojection_bounded"][
        "acceptance_threshold_authorized"
    ] = False
    summary["gates"] = gates
    arm["analysis_summary"] = summary
    package["current_full_grid_temporal_oracle"] = arm

    assert module.package_status(package) == module.package_status(ready_package(module))


def test_headed_completion_rejects_missing_or_red_terminal_reprojection_gates() -> None:
    headed = load_headed_runner()
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
        "evidence_celestial_time_fixed",
        "evidence_terminal_expected_reprojection_valid",
        "gameplay_terminal_expected_cell_address_exact",
        "evidence_product_owner_name_transport_valid",
        "evidence_terrain_product_homogeneity_present",
        "evidence_terrain_product_final_paint_join_complete",
        "gameplay_terrain_product_distribution_non_degenerate",
        "evidence_terrain_product_homogeneity_baseline_calibrated",
        "evidence_product_admission_contingency_valid",
        "gameplay_product_admission_chain_exact",
    )
    summary = {
        "proof_profile": "full_grid_temporal_oracle_v1",
        "gates": {
            "all_pass": True,
            **{name: {"pass": True} for name in required},
        },
    }
    assert headed._analysis_gates_accept_proof(summary) is True

    for terminal_gate in (
        "evidence_celestial_time_fixed",
        "evidence_terminal_expected_reprojection_valid",
        "gameplay_terminal_expected_cell_address_exact",
        "evidence_product_admission_contingency_valid",
        "gameplay_product_admission_chain_exact",
    ):
        missing = {
            "proof_profile": summary["proof_profile"],
            "gates": {
                name: gate
                for name, gate in summary["gates"].items()
                if name != terminal_gate
            },
        }
        assert headed._analysis_gates_accept_proof(missing) is False

        red = {
            "proof_profile": summary["proof_profile"],
            "gates": {
                name: (dict(gate) if isinstance(gate, dict) else gate)
                for name, gate in summary["gates"].items()
            },
        }
        red["gates"][terminal_gate]["pass"] = False
        assert headed._analysis_gates_accept_proof(red) is False

    uncalibrated = {
        "proof_profile": summary["proof_profile"],
        "gates": {
            name: (dict(gate) if isinstance(gate, dict) else gate)
            for name, gate in summary["gates"].items()
        },
    }
    uncalibrated["gates"]["gameplay_terminal_expected_reprojection_bounded"] = {
        "pass": False,
        "baseline_calibrated": False,
        "acceptance_threshold_authorized": False,
    }
    assert headed._analysis_gates_accept_proof(uncalibrated) is True


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("terrain_product_homogeneity_rows", 0),
        ("terrain_product_homogeneity_invalid_rows", 1),
        ("terrain_product_dominant_material_ratio", 0.650001),
        ("terrain_product_homogeneity_source", "selected_surface_material"),
        ("terrain_product_homogeneity_baseline_calibrated", False),
        ("terrain_product_final_paint_missing_colour_rows", 1),
        ("terrain_product_final_paint_colour_join_rows", 99),
    ),
)
def test_package_status_rejects_uncalibrated_or_degenerate_terrain_evidence(
    field: str, value: object
) -> None:
    module = load_runner()
    package = ready_package(module)
    arm = dict(package["current_full_grid_temporal_oracle"])
    summary = dict(arm["analysis_summary"])
    summary[field] = value
    arm["analysis_summary"] = summary
    package["current_full_grid_temporal_oracle"] = arm

    assert (
        module.package_status(package)
        == "current_full_grid_temporal_oracle_failed"
    )


def test_exact_primitive_disagreement_is_diagnostic_after_finite_splat_passes() -> None:
    module = load_runner()
    package = ready_package(module)
    arm = dict(package["current_full_grid_temporal_oracle"])
    summary = dict(arm["analysis_summary"])
    summary["full_grid_temporal_oracle_geometry_error_rows"] = 12
    summary["full_grid_temporal_oracle_finite_sample_substitution_rows"] = 12
    summary["full_grid_temporal_oracle_finite_splat_admissible_rows"] = 12
    summary["full_grid_temporal_oracle_duplicate_components"] = 3
    summary["full_grid_temporal_oracle_out_of_place_identities"] = 4
    arm["analysis_summary"] = summary
    package["current_full_grid_temporal_oracle"] = arm
    assert module.package_status(package) == "current_package_ready_for_runtime_review"

@pytest.mark.parametrize(
    ("arm", "expected_status"),
    (
        (
            "current_visual_perspective",
            "current_visual_perspective_failed",
        ),
        ("current_visual_ortho", "current_visual_ortho_failed"),
        ("current_night_temporal", "current_night_temporal_failed"),
        ("current_secondary_effect", "current_secondary_effect_failed"),
        (
            "current_clean_play_perspective",
            "current_clean_play_perspective_failed",
        ),
        (
            "current_clean_play_ortho",
            "current_clean_play_ortho_failed",
        ),
    ),
)
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("client_git_head", "f" * 40),
        ("server_dirty_paths", [" M colyseus_server/src/rooms/World.ts"]),
        ("client_runtime_scope_fingerprint", "c" * 64),
        ("server_runtime_scope_fingerprint", "d" * 64),
    ),
)
def test_package_status_rejects_mixed_arm_source_provenance(
    arm: str,
    expected_status: str,
    field: str,
    value: object,
) -> None:
    module = load_runner()
    mutant = ready_package(module)
    arm_result = dict(mutant[arm])
    arm_receipt = dict(arm_result["receipt"])
    arm_receipt[field] = value
    arm_result["receipt"] = arm_receipt
    mutant[arm] = arm_result
    assert module.package_status(mutant) == expected_status


@pytest.mark.parametrize(
    ("field", "status"),
    (
        (
            "static_checks_skipped",
            "current_package_nonpromotable_static_checks_skipped",
        ),
        (
            "dirty_diagnostic_capture_allowed",
            "current_package_nonpromotable_dirty_diagnostic_capture",
        ),
    ),
)
def test_package_status_never_promotes_diagnostic_capture_modes(
    field: str,
    status: str,
) -> None:
    module = load_runner()
    valid = ready_package(module)
    valid[field] = True
    assert module.package_status(valid) == status
    assert (
        module.package_status(valid)
        != "current_package_ready_for_runtime_review"
    )


@pytest.mark.parametrize(
    ("cli_flags", "dirty_paths", "expected_status"),
    (
        (
            ("--skip-static",),
            (),
            "current_package_nonpromotable_static_checks_skipped",
        ),
        (
            ("--allow-dirty-diagnostic-capture",),
            (" M scripts/dirty_capture_source.py",),
            "current_package_nonpromotable_dirty_diagnostic_capture",
        ),
    ),
)
def test_capture_cli_exits_nonzero_for_nonpromotable_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cli_flags: tuple[str, ...],
    dirty_paths: tuple[str, ...],
    expected_status: str,
) -> None:
    module = load_runner()
    monkeypatch.setattr(
        module,
        "capture_git_provenance",
        lambda: ("1234567890abcdef1234567890abcdef12345678", list(dirty_paths)),
    )
    monkeypatch.setattr(
        module,
        "capture_runtime_scope_fingerprint",
        lambda path: (
            CLIENT_RUNTIME_FINGERPRINT
            if path.name == "godot_project"
            else SERVER_RUNTIME_FINGERPRINT
        ),
    )
    monkeypatch.setattr(
        module,
        "static_checks",
        lambda _out_dir, _godot: [{"returncode": 0}],
    )
    monkeypatch.setattr(module, "capture_blocker_from_port", lambda _port: "")
    monkeypatch.setattr(
        module,
        "run_current_visual_arm",
        lambda **kwargs: visual_result(module, str(kwargs["view_mode"])),
    )
    monkeypatch.setattr(
        module,
        "run_current_secondary_effect_arm",
        lambda **_kwargs: secondary_result(module),
    )
    monkeypatch.setattr(
        module,
        "run_current_night_temporal_arm",
        lambda **_kwargs: night_temporal_result(module),
    )
    monkeypatch.setattr(
        module,
        "run_current_clean_play_arm",
        lambda **kwargs: clean_result(module, str(kwargs["view_mode"])),
    )
    monkeypatch.setattr(
        module,
        "run_current_full_grid_oracle_arm",
        lambda **_kwargs: full_grid_oracle_result(module),
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            str(RUNNER),
            "--capture",
            *cli_flags,
            "--out",
            str(tmp_path),
        ],
    )

    assert module.main() == 1
    manifest = json.loads(
        (tmp_path / "current_package_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["status"] == expected_status
    assert manifest["status"] != "current_package_ready_for_runtime_review"


def test_static_check_markers_are_current_owner_checks() -> None:
    module = load_runner()
    markers = set(module.REQUIRED_STATIC_CHECK_MARKERS)
    assert "scripts/test_fl4431_airborne_latch_deleted_contract.py" in markers
    assert "scripts/test_fl4431_actor_quad_selection_contract.py" in markers
    assert "scripts/test_fl4431_visual_effect_reachability_contract.py" in markers
    assert "res://scripts/tests/test_fl4431_night_null_product_tuple.gd" in markers
    assert "scripts/run_fl4265_airplane_proof.py" in markers
    assert "res://tools/proofs/run_fl4265_airplane_dogfight_proof.gd" in markers
    assert "git diff --check" in markers
    # This current-only, no-jitter package has no red-gate analyzer phase.
    # Keeping the option would advertise a control that can never affect its
    # admission result, so the retired argument must stay physically absent.
    assert "--expected-red-gate" not in RUNNER.read_text(encoding="utf-8")


def test_godot_static_check_owns_a_durable_log_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent user:// log rotation from crashing the package before runtime."""

    module = load_runner()
    commands: list[list[str]] = []

    def record_command(command, *, log_path):
        commands.append(list(command))
        return {
            "cmd": list(command),
            "returncode": 0,
            "duration_seconds": 0.0,
            "log": str(log_path),
            "stdout_tail": "",
        }

    monkeypatch.setattr(module, "run_cmd", record_command)
    module.static_checks(tmp_path, "godot")
    godot_commands = [
        command
        for command in commands
        if command and command[0] == "godot"
    ]
    assert len(godot_commands) == 3
    compositor_command = next(
        command
        for command in godot_commands
        if command[-1] == "res://render/api/ASCIICompositorEffect.gd"
    )
    dogfight_command = next(
        command
        for command in godot_commands
        if command[-1] == "res://tools/proofs/run_fl4265_airplane_dogfight_proof.gd"
    )
    night_tuple_command = next(
        command
        for command in godot_commands
        if command[-1]
        == "res://scripts/tests/test_fl4431_night_null_product_tuple.gd"
    )
    compositor_log_index = compositor_command.index("--log-file")
    assert compositor_command[compositor_log_index + 1] == str(
        tmp_path / "logs" / "godot_static_check.log"
    )
    assert compositor_log_index < compositor_command.index("--check-only")
    dogfight_log_index = dogfight_command.index("--log-file")
    assert dogfight_command[dogfight_log_index + 1] == str(
        tmp_path / "logs" / "godot_dogfight_check.log"
    )
    assert dogfight_log_index < dogfight_command.index("--check-only")
    night_tuple_log_index = night_tuple_command.index("--log-file")
    assert night_tuple_command[night_tuple_log_index + 1] == str(
        tmp_path / "logs" / "godot_night_tuple_check.log"
    )


def main() -> int:
    """Make direct execution honest instead of silently collecting zero tests."""

    return pytest.main([str(Path(__file__).resolve()), "-q"])


if __name__ == "__main__":
    raise SystemExit(main())
