#!/usr/bin/env python3
"""Contract tests for the FL-4216 same-process temporal proof arm."""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import struct
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run_fl4359_headed_movement.py"
DRIVER = ROOT / "scripts/adhoc/fl4359_external_movement_driver.gd"


def load_runner():
    spec = importlib.util.spec_from_file_location("fl4216_temporal_runner", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _args(**overrides) -> argparse.Namespace:
    values = {
        "single_frame": False,
        "skip_jitter_analysis": True,
        "camera_mode": "perspective",
        "exact_player_position": "127.110,0,-3.923",
        "exact_camera_position": "124.412,19.5625,28.967",
        "exact_camera_yaw": "-0.08185",
        "exact_camera_pitch": "-0.46365",
        "exact_camera_projection": "perspective",
        "exact_camera_fov": "58",
        "exact_camera_size": "",
        "day_cycle_time": 0.5,
        "godot_window_mode": "fullscreen",
        "extra_user_arg": [],
        "fl4216_temporal_capture_interval_seconds": 1.0,
        "fl4216_temporal_capture_count": 2,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _dump_fixture(tmp_path: Path, *, include_required_fields: bool = True) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    cells = tmp_path / "cells.jsonl"
    row = {
        "screen_cell": {"x": 10, "y": 20},
        "selected_owner": "water",
        "source_class": "terrain",
        "glyph_id": 35,
        "glyph_char": "+",
        "fg_rgb": [120, 130, 140],
        "bg_rgb": [20, 30, 40],
        "final_cell_paint_buffer": {
            "glyph_id": 664,
            "final_mask_glyph_ids": [664],
        },
        "selected_surface_quarters": [
            {
                "stable_world_anchor": "(1.0, 0.0, 2.0, 1.0)",
                "owner_kind": 3,
                "submerged_source_owner_kind": 2,
                "direct_water_solid_contact": True,
                "valid": True,
            }
        ],
    }
    if not include_required_fields:
        row.pop("bg_rgb")
    cells.write_text(json.dumps(row) + "\n", encoding="utf-8")
    final_png = tmp_path / "final.png"
    final_png.write_bytes(b"png-proof-placeholder")
    index_html = tmp_path / "index.html"
    index_html.write_text("<html></html>", encoding="utf-8")
    metadata = tmp_path / "metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "camera_world": {"x": 0, "y": 0, "z": 0},
                "player_world": {"x": 0, "y": 0, "z": 0},
                "pixel_camera_capture_contract": {
                    "camera_projection_mode": 0,
                    "scene_camera_origin": [0, 0, 0],
                    "actor_presentation_reference_world": [0, 0, 0],
                    "actor_presentation_camera_contract_valid": True,
                    "camera_yaw": 0.25,
                    "camera_pitch": -0.5,
                },
            }
        ),
        encoding="utf-8",
    )
    return {
        "ok": True,
        "artifact_paths": {
            "cells_jsonl": str(cells),
            "final_png": str(final_png),
            "index_html": str(index_html),
            "metadata_json": str(metadata),
        },
    }


def _snapshot_fixture(tmp_path: Path, *, frame_time: float = 10.0, glyph_id: int = 664) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    metadata = {
        "camera_world": {"x": 0, "y": 0, "z": 0},
        "player_world": {"x": 0, "y": 0, "z": 0},
        "pixel_camera_capture_contract": {
            "semantic_ansi_size": [1, 1],
            "camera_projection_mode": 0,
            "scene_camera_origin": [0, 0, 0],
            "actor_presentation_reference_world": [0, 0, 0],
            "actor_presentation_camera_contract_valid": True,
            "camera_yaw": 0.25,
            "camera_pitch": -0.5,
            "frame_time_seconds": frame_time,
        },
    }
    final_bytes = bytearray(112)
    struct.pack_into("<III", final_bytes, 0, 0xF14231, 3, glyph_id)
    struct.pack_into("<I", final_bytes, 76, 0xF)
    for slot in range(4):
        struct.pack_into("<I", final_bytes, 84 + slot * 4, glyph_id + 1)
    selected_bytes = bytearray(4 * 96)
    for slot in range(4):
        base = slot * 96
        struct.pack_into("<IIII", selected_bytes, base, 3, 77, slot, 3)
        struct.pack_into("<I", selected_bytes, base + 60, 1 | (3 << 7) | (1 << 29))
        struct.pack_into("<4f", selected_bytes, base + 64, 1.0, 0.0, 2.0, 1.0)
    snapshot = {
        "schema": "fl4216_temporal_snapshot.v1",
        "capture_phase": "cadence_snapshot",
        "metadata": metadata,
        "final_cell_record_stride_bytes": 112,
        "selected_surface_record_count": 4,
        "selected_surface_record_stride_bytes": 96,
        "final_cell_paint_b64": base64.b64encode(final_bytes).decode("ascii"),
        "selected_surface_b64": base64.b64encode(selected_bytes).decode("ascii"),
    }
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    png_path = tmp_path / "final.png"
    png_path.write_bytes(b"png")
    return {
        "ok": True,
        "capture_schema": "fl4216_temporal_snapshot.v1",
        "artifact_paths": {
            "temporal_snapshot_json": str(snapshot_path),
            "metadata_json": str(metadata_path),
            "final_png": str(png_path),
        },
    }


def _comparison_dump(tmp_path: Path, rows: list[dict]) -> dict:
    dump = _dump_fixture(tmp_path)
    Path(dump["artifact_paths"]["cells_jsonl"]).write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    return dump


def _grounding_contract(*, y: float = 0.0, stable_y: float | None = None) -> dict:
    if stable_y is None:
        stable_y = y
    return {
        "pass": True,
        "source": "World.actor_presentation_root_world cross-checked with stable publication",
        "requested_player_position": {"x": 0, "y": 0, "z": 0},
        "authoritative_grounded_player_position": {"x": 0, "y": y, "z": 0},
        "stable_publication_player_position": {"x": 0, "y": stable_y, "z": 0},
        "ground_y_adjustment": y,
        "publication_consistency_error": abs(y - stable_y),
    }


def _contact_row(
    source_class: str,
    anchor: str,
    glyph_id: int | None,
    fg: list[int],
    bg: list[int],
    *,
    final_glyph_id: int | None = None,
    final_mask_glyph_ids: list[int] | None = None,
) -> dict:
    owner_kind = {"terrain": 2, "static_mesh": 1, "dynamic_mesh": 4}[source_class]
    if final_mask_glyph_ids is None:
        final_mask_glyph_ids = [final_glyph_id] if final_glyph_id is not None else []
    return {
        "screen_cell": {"x": 10, "y": 20},
        "selected_owner": "water",
        "source_class": source_class,
        "glyph_id": glyph_id,
        "glyph_char": "+",
        "fg_rgb": fg,
        "bg_rgb": bg,
        "final_cell_paint_buffer": {
            "glyph_id": glyph_id if final_glyph_id is None else final_glyph_id,
            "final_mask_glyph_ids": final_mask_glyph_ids,
        },
        "selected_surface_quarters": [
            {
                "stable_world_anchor": anchor,
                "owner_kind": 3,
                "submerged_source_owner_kind": owner_kind,
                "direct_water_solid_contact": True,
                "valid": True,
            }
        ],
    }


def test_temporal_arm_is_fullscreen_exact_pose_and_never_x_owned():
    runner = load_runner()
    driver = DRIVER.read_text(encoding="utf-8")
    assert len(runner.FL4216_SHORELINE_BREAK_GLYPH_IDS) == 32
    assert 670 not in runner.FL4216_SHORELINE_BREAK_GLYPH_ID_SET
    assert "--fl4216-temporal-capture" in RUNNER.read_text(encoding="utf-8")
    assert "--fl4216-temporal-capture=true" in RUNNER.read_text(encoding="utf-8")
    assert "World._request_final_render_cell_dump" in driver
    assert "queue_fl4216_temporal_snapshot" in driver
    assert "materialize_fl4216_temporal_snapshot" in driver
    assert "deferred raw b43/b64" in RUNNER.read_text(encoding="utf-8")
    temporal_block = driver.split("func _capture_fl4216_temporal_sequence", 1)[1].split(
        "func _capture_single_frame", 1
    )[0]
    assert "_request_final_render_cell_dump" not in temporal_block
    assert "_write_fl4216_temporal_snapshot_artifact" in temporal_block
    assert "actor_presentation_root_world" in driver
    assert "last_player_presentation_position" in driver
    assert '"last_player_presentation_position": _vec3(last_published_actor_root)' in driver
    assert '"last_player_presentation_position": _vec3(player.global_position)' not in driver
    assert '"actor_root_publication_valid": actor_root_publication_valid' in driver
    assert '"ground_y_adjustment"' in driver
    assert '"actor_xz_error"' in driver
    assert "KEY_X" not in driver
    process_block = driver.split("func _process(_delta: float) -> bool:", 1)[1].split(
        "func _fl4216_exact_pose_lock_loop", 1
    )[0]
    assert "return true" not in process_block
    assert process_block.count("return false") == 2

    preflight = runner._exact_pose_capture_preflight(
        _args(), godot_timeout_seconds=600.0, temporal_capture=True
    )
    assert preflight["ok"], preflight
    assert preflight["route_identity"] == "fl4216_same_process_temporal_v1"
    assert preflight["checks"]["fullscreen_requested"] is True
    assert preflight["checks"]["temporal_capture_projection_supported"] is True


def test_temporal_capture_queues_async_same_frame_payloads_before_materializing():
    driver = DRIVER.read_text(encoding="utf-8")
    effect = (ROOT / "godot_project/render/api/ASCIICompositorEffect.gd").read_text(
        encoding="utf-8"
    )
    temporal_block = driver.split("func _capture_fl4216_temporal_sequence", 1)[1].split(
        "func _capture_single_frame", 1
    )[0]
    cadence_block, deferred_block = temporal_block.split(
        "# The cadence is complete before any callback is awaited.", 1
    )
    cadence_loop = cadence_block.split("for capture_index in range", 1)[1]
    assert "queue_fl4216_temporal_snapshot" in cadence_loop
    assert "materialize_fl4216_temporal_snapshot" not in cadence_loop
    assert "is_fl4216_temporal_snapshot_ready" not in cadence_loop
    assert "_request_final_render_cell_dump" not in cadence_loop
    assert "_save_screenshot" not in cadence_loop
    assert "is_fl4216_temporal_snapshot_ready" in deferred_block
    assert "materialize_fl4216_temporal_snapshot" in deferred_block

    queue_block = effect.split("func queue_fl4216_temporal_snapshot", 1)[1].split(
        "func _final_cell_paint_capture_active", 1
    )[0]
    assert "buffer_get_data_async" in queue_block
    assert "texture_get_data_async" in queue_block
    assert "buffer_get_data(" not in queue_block
    assert "texture_get_data(" not in queue_block
    assert "FINAL_CELL_RECORD_BYTES != 112" in queue_block
    assert "VSB_VISIBLE_SURFACE_RECORD_BYTES != 96" in queue_block
    assert 'request_id, "final_cell_paint_bytes"' in queue_block
    assert 'request_id, "selected_surface_bytes"' in queue_block
    assert 'Callable(self, "_fl4216_async_snapshot_texture_callback").bind(request_id)' in queue_block
    assert "materialized_contract != queued_contract" in temporal_block


def test_temporal_async_callbacks_are_origin_bound_and_materialization_is_fail_closed():
    effect = (ROOT / "godot_project/render/api/ASCIICompositorEffect.gd").read_text(
        encoding="utf-8"
    )
    assert "request_id: int,\n\tlane: String" in effect
    assert "request_id: int,\n) -> void:" in effect
    assert 'request[lane] = bytes' in effect
    assert 'request["pending"] = maxi' in effect
    assert '"temporal_snapshot_readback_pending"' in effect
    assert '"temporal_snapshot_request_missing"' in effect


def test_temporal_grounding_sampling_rejects_raw_node_and_missing_actor_root_mutants():
    driver = DRIVER.read_text(encoding="utf-8")
    assert "actor_root_world" in driver
    assert "actor_root_candidate.is_finite()" in driver
    assert "and actor_root_valid_this_sample" in driver
    assert "and actor_root_publication_valid" in driver


@pytest.mark.parametrize(
    ("overrides", "failed_check"),
    [
        ({"godot_window_mode": "windowed"}, "fullscreen_requested"),
        ({"camera_mode": "plane_air"}, "temporal_capture_projection_supported"),
        ({"fl4216_temporal_capture_interval_seconds": 0.99}, "temporal_capture_interval_valid"),
        ({"fl4216_temporal_capture_count": 1}, "temporal_capture_count_valid"),
    ],
)
def test_temporal_preflight_fails_closed(overrides, failed_check):
    runner = load_runner()
    preflight = runner._exact_pose_capture_preflight(
        _args(**overrides), godot_timeout_seconds=600.0, temporal_capture=True
    )
    assert not preflight["ok"]
    assert failed_check in preflight["failed_checks"]


def test_temporal_preflight_rejects_short_timeout():
    runner = load_runner()
    preflight = runner._exact_pose_capture_preflight(
        _args(), godot_timeout_seconds=240.0, temporal_capture=True
    )
    assert not preflight["ok"]
    assert "temporal_capture_timeout_valid" in preflight["failed_checks"]


def test_temporal_dump_preserves_identity_contact_and_final_paint_fields(tmp_path):
    runner = load_runner()
    valid = runner._validate_fl4216_temporal_dump(_dump_fixture(tmp_path))
    assert valid["pass"] is True
    assert valid["required_top_level_fields_present"] == list(
        runner.FL4216_TEMPORAL_TOP_LEVEL_FIELDS
    )
    assert valid["required_quarter_fields_present"] == list(
        runner.FL4216_TEMPORAL_QUARTER_FIELDS
    )
    assert valid["metadata_fields_present"] == list(runner.FL4216_TEMPORAL_METADATA_KEYS)

    missing = runner._validate_fl4216_temporal_dump(
        _dump_fixture(tmp_path / "missing", include_required_fields=False)
    )
    assert missing["pass"] is False


def test_temporal_snapshot_is_authoritative_and_shape_checked(tmp_path):
    runner = load_runner()
    dump = _snapshot_fixture(tmp_path)
    valid = runner._validate_fl4216_temporal_dump(dump)
    assert valid["pass"] is True
    rows, diagnostics = runner._fl4216_temporal_snapshot_rows(dump)
    assert diagnostics["readback_state"] == "temporal_snapshot_decode_ok"
    assert diagnostics["contact_quarters_read"] == 4
    assert rows[0]["final_cell_paint_buffer"]["glyph_id"] == 664
    assert all(q["submerged_source_owner_kind"] == 4 for q in rows[0]["selected_surface_quarters"])

    payload = json.loads(Path(dump["artifact_paths"]["temporal_snapshot_json"]).read_text())
    payload["selected_surface_record_count"] = 1
    Path(dump["artifact_paths"]["temporal_snapshot_json"]).write_text(json.dumps(payload))
    invalid = runner._validate_fl4216_temporal_dump(dump)
    assert invalid["pass"] is False
    assert invalid["reason"] == "temporal_snapshot_raw_buffer_shape_mismatch"


def test_pruned_runtime_field_contract_reuses_extracted_pass_result() -> None:
    runner = load_runner()
    reused = runner._reuse_pruned_fl4216_contract(
        {
            "pass": False,
            "reason": "missing_temporal_snapshot_json",
        },
        {
            "pass": True,
            "reason": "temporal_snapshot_contract_ok",
        },
        fallback_reasons={"missing_temporal_snapshot_json"},
        contract_kind="runtime_field_contract",
    )
    assert reused["pass"] is True
    assert reused["raw_artifact_pruned"] is True
    assert reused["blocking"] is False
    assert reused["raw_artifact_fallback_contract_kind"] == "runtime_field_contract"


def test_pruned_temporal_comparison_reuses_prior_result_only_for_missing_rows() -> None:
    runner = load_runner()
    reused = runner._reuse_pruned_fl4216_comparison(
        {
            "pass": False,
            "reason": "temporal_contact_rows_unreadable",
            "frame_diagnostics": [
                {"reason": "missing_temporal_snapshot_json", "json_parse_errors": 0, "missing_identity_rows": 0},
                {"reason": "missing_temporal_snapshot_json", "json_parse_errors": 0, "missing_identity_rows": 0},
            ],
        },
        {
            "pass": True,
            "reason": "temporal_shoreline_morphology_comparison_ok",
        },
    )
    assert reused["pass"] is True
    assert reused["raw_artifact_pruned"] is True
    assert reused["raw_artifact_fallback_contract_kind"] == "temporal_comparison"


def test_pruned_temporal_comparison_does_not_mask_json_corruption() -> None:
    runner = load_runner()
    current = {
        "pass": False,
        "reason": "temporal_contact_rows_unreadable",
        "frame_diagnostics": [
            {"reason": "missing_temporal_snapshot_json", "json_parse_errors": 1, "missing_identity_rows": 0},
            {"reason": "missing_temporal_snapshot_json", "json_parse_errors": 0, "missing_identity_rows": 0},
        ],
    }
    preserved = {"pass": True, "reason": "temporal_shoreline_morphology_comparison_ok"}
    assert runner._reuse_pruned_fl4216_comparison(current, preserved) == current


def test_temporal_snapshot_morphology_does_not_attach_later_dump(tmp_path):
    runner = load_runner()
    frame_a_dump = _snapshot_fixture(tmp_path / "a", frame_time=10.0, glyph_id=664)
    frame_b_dump = _snapshot_fixture(tmp_path / "b", frame_time=11.6, glyph_id=546)
    frames = [
        {
            "capture_index": 0,
            "same_godot_process": True,
            "runtime_truth_snapshot": {"capture_phase": "cadence_snapshot", "frame_time_seconds": 10.0},
            "final_render_cell_dump": frame_a_dump,
        },
        {
            "capture_index": 1,
            "same_godot_process": True,
            "runtime_truth_snapshot": {"capture_phase": "cadence_snapshot", "frame_time_seconds": 11.6},
            "final_render_cell_dump": frame_b_dump,
        },
    ]
    comparison = runner._compare_fl4216_temporal_frames(frames)
    assert comparison["pass"] is True
    assert comparison["source_classes"]["dynamic_mesh"]["break_state_changed_count"] > 0


def test_temporal_analysis_skip_requires_two_time_separated_frames(tmp_path):
    runner = load_runner()
    frames = []
    for index in range(2):
        dump = _dump_fixture(tmp_path / f"frame_{index}")
        if index == 1:
            cells_path = Path(dump["artifact_paths"]["cells_jsonl"])
            row = json.loads(cells_path.read_text(encoding="utf-8"))
            row["final_cell_paint_buffer"]["glyph_id"] = 546
            row["final_cell_paint_buffer"]["final_mask_glyph_ids"] = [546]
            cells_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        validation = runner._validate_fl4216_temporal_dump(dump)
        screenshot = tmp_path / f"screenshot_{index}.png"
        screenshot.write_bytes(b"screenshot-proof-placeholder")
        frames.append(
            {
                "capture_index": index,
                "same_godot_process": True,
                "elapsed_since_previous_seconds": 0.0 if index == 0 else 1.01,
                "runtime_truth_snapshot": {
                    "capture_phase": "cadence_snapshot",
                    "frame_time_seconds": 10.0 + index * 1.6,
                },
                "screenshot": str(screenshot),
                "final_render_cell_dump": dump,
                "runtime_field_contract": validation,
            }
        )
    receipt = {
        "fl4216_temporal_capture": True,
        "fl4216_temporal_same_process": True,
        "fl4216_temporal_interval_seconds": 1.0,
        "camera_mode": "perspective",
        "exact_camera_yaw": 0.25,
        "exact_camera_pitch": -0.5,
        "fl4216_temporal_frames": frames,
        "fl4216_temporal_comparison": runner._compare_fl4216_temporal_frames(frames),
    }
    for frame in frames:
        frame["artifact_pose_contract"] = runner._validate_fl4216_temporal_pose_metadata(
            frame["final_render_cell_dump"],
            {"x": 0, "y": 0, "z": 0},
            {"x": 0, "y": 0, "z": 0},
            "perspective",
            0.25,
            -0.5,
            _grounding_contract(),
        )
    receipt["fl4216_temporal_pose_contract"] = {
        "pass": all(frame["artifact_pose_contract"]["pass"] for frame in frames),
        "frames": [frame["artifact_pose_contract"] for frame in frames],
    }
    receipt["fl4216_temporal_timing_contract"] = runner._validate_fl4216_temporal_frame_timing(
        frames, 1.0
    )
    summary = {"analysis_skipped": True, "reason": "canonical_png_grid_capture_only"}
    assert runner._fl4216_temporal_capture_accepts_analysis_skip(summary, receipt, True)
    frames[1]["elapsed_since_previous_seconds"] = 0.5
    assert not runner._fl4216_temporal_capture_accepts_analysis_skip(summary, receipt, True)


def test_temporal_timing_rejects_31_9_second_dump_alias_and_accepts_1_6_product_delta():
    runner = load_runner()
    frames = [
        {"runtime_truth_snapshot": {"frame_time_seconds": 20.0}},
        {"runtime_truth_snapshot": {"frame_time_seconds": 21.6}},
    ]
    accepted = runner._validate_fl4216_temporal_frame_timing(frames, 1.6)
    assert accepted["pass"] is True
    assert accepted["product_frame_delta_seconds"] == pytest.approx([1.6])
    frames[1]["runtime_truth_snapshot"]["frame_time_seconds"] = 51.9
    rejected = runner._validate_fl4216_temporal_frame_timing(frames, 1.6)
    assert rejected["pass"] is False
    assert rejected["reason"] == "temporal_product_frame_delta_out_of_window"
    assert rejected["product_frame_delta_seconds"] == pytest.approx([31.9])


def test_temporal_driver_waits_for_both_monotonic_and_product_clocks():
    runner = load_runner()
    driver = DRIVER.read_text(encoding="utf-8")
    wait_block = driver.split("func _wait_for_fl4216_temporal_interval", 1)[1].split(
        "func _validate_fl4216_temporal_dump_artifacts", 1
    )[0]
    assert 'effect.call("get_last_pixel_camera_capture_contract")' in driver
    assert "monotonic_elapsed_us >= requested_us and product_ready" in wait_block
    assert "product_frame_time - previous_product_frame_time" in wait_block
    assert "FL4216_TEMPORAL_PRODUCT_DELTA_MAX_SECONDS" in wait_block


def test_temporal_driver_requires_a_completed_pixel_camera_contract_before_snapshot_zero():
    driver = DRIVER.read_text(encoding="utf-8")
    initial_wait = driver.split("func _wait_for_fl4216_initial_render_contract", 1)[1].split(
        "func _wait_for_fl4216_temporal_interval", 1
    )[0]
    capture = driver.split("func _capture_fl4216_temporal_sequence", 1)[1].split(
        "func _capture_single_frame", 1
    )[0]
    assert "FL4216_INITIAL_RENDER_CONTRACT_MAX_FRAMES" in driver
    assert "await RenderingServer.frame_post_draw" in initial_wait
    assert "get_last_pixel_camera_capture_contract" in initial_wait
    assert "_fl4216_pixel_camera_contract_ready" in initial_wait
    assert "await _wait_for_fl4216_initial_render_contract" in capture
    assert "fl4216_temporal_initial_render_contract_missing" in capture
    assert capture.index("await _wait_for_fl4216_initial_render_contract") < capture.index(
        "for capture_index in range"
    )

    # This is the exact clock split from the failed orthographic receipt. The
    # monotonic interval cannot substitute for Product animation time, so the
    # analyzer must remain red until the dual-clock driver wait advances it.
    frames = [
        {
            "elapsed_since_previous_seconds": 0.0,
            "runtime_truth_snapshot": {"frame_time_seconds": 2.43533333333333},
        },
        {
            "elapsed_since_previous_seconds": 1.521051,
            "runtime_truth_snapshot": {"frame_time_seconds": 3.926},
        },
    ]
    rejected = runner._validate_fl4216_temporal_frame_timing(frames, 1.5)
    assert rejected["pass"] is False
    assert rejected["product_frame_delta_seconds"] == pytest.approx([1.49066666666667])


def test_temporal_pose_metadata_accepts_grounded_actor_y_and_records_adjustment(tmp_path):
    runner = load_runner()
    dump = _dump_fixture(tmp_path)
    metadata_path = Path(dump["artifact_paths"]["metadata_json"])
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["player_world"]["y"] = 0.0
    metadata["pixel_camera_capture_contract"]["actor_presentation_reference_world"][1] = 3.0625
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    grounding = _grounding_contract(y=3.0625)
    result = runner._validate_fl4216_temporal_pose_metadata(
        dump,
        {"x": 0, "y": 0, "z": 0},
        {"x": 0, "y": 0, "z": 0},
        "perspective",
        0.25,
        -0.5,
        grounding,
    )
    assert result["pass"] is True
    assert result["actor_xz_error"] == pytest.approx(0.0)
    assert result["actor_y_error"] == pytest.approx(0.0)
    assert result["ground_y_adjustment"] == pytest.approx(3.0625)


def test_temporal_pose_metadata_fails_on_actor_xz_drift(tmp_path):
    runner = load_runner()
    dump = _dump_fixture(tmp_path)
    metadata_path = Path(dump["artifact_paths"]["metadata_json"])
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["pixel_camera_capture_contract"]["actor_presentation_reference_world"][0] = 0.2
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    result = runner._validate_fl4216_temporal_pose_metadata(
        dump, {"x": 0, "y": 0, "z": 0}, {"x": 0, "y": 0, "z": 0},
        "perspective", 0.25, -0.5, _grounding_contract()
    )
    assert result["pass"] is False
    assert result["actor_xz_error"] == pytest.approx(0.2)


def test_temporal_pose_metadata_fails_on_camera_drift(tmp_path):
    runner = load_runner()
    dump = _dump_fixture(tmp_path)
    metadata_path = Path(dump["artifact_paths"]["metadata_json"])
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["pixel_camera_capture_contract"]["scene_camera_origin"][0] = 0.2
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    result = runner._validate_fl4216_temporal_pose_metadata(
        dump, {"x": 0, "y": 0, "z": 0}, {"x": 0, "y": 0, "z": 0},
        "perspective", 0.25, -0.5, _grounding_contract()
    )
    assert result["pass"] is False
    assert result["camera_error"] == pytest.approx(0.2)


def test_temporal_pose_metadata_fails_on_inconsistent_grounded_y(tmp_path):
    runner = load_runner()
    dump = _dump_fixture(tmp_path)
    metadata_path = Path(dump["artifact_paths"]["metadata_json"])
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["pixel_camera_capture_contract"]["actor_presentation_reference_world"][1] = 3.0625
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    result = runner._validate_fl4216_temporal_pose_metadata(
        dump, {"x": 0, "y": 0, "z": 0}, {"x": 0, "y": 0, "z": 0},
        "perspective", 0.25, -0.5, _grounding_contract(y=3.0625, stable_y=4.0625)
    )
    assert result["pass"] is False
    assert result["reason"] == "authoritative_grounding_fact_inconsistent"


def test_temporal_comparison_reports_changed_and_absent_classes(tmp_path):
    runner = load_runner()
    frame_a = _comparison_dump(
        tmp_path / "a",
        [
            _contact_row("terrain", "(1,0,2,1)", 35, [1, 2, 3], [4, 5, 6], final_glyph_id=664),
            _contact_row("static_mesh", "(3,0,4,1)", 36, [7, 8, 9], [10, 11, 12], final_glyph_id=668),
        ],
    )
    frame_b = _comparison_dump(
        tmp_path / "b",
        [
            _contact_row("terrain", "(1,0,2,1)", 37, [20, 2, 3], [4, 5, 6], final_glyph_id=546),
            _contact_row("static_mesh", "(3,0,4,1)", 38, [17, 8, 9], [10, 11, 12], final_glyph_id=649),
        ],
    )
    result = runner._compare_fl4216_temporal_frames(
        [{"final_render_cell_dump": frame_a}, {"final_render_cell_dump": frame_b}]
    )
    assert result["pass"] is True
    assert result["source_classes"]["terrain"]["comparable_count"] == 1
    assert result["source_classes"]["terrain"]["changed_count"] == 1
    assert result["source_classes"]["terrain"]["changed_pct"] == 100.0
    assert result["source_classes"]["terrain"]["break_glyph_count_by_frame"] == [1, 1]
    assert result["source_classes"]["terrain"]["break_coverage_pct_by_frame"] == [100.0, 100.0]
    assert result["source_classes"]["static_mesh"]["changed_count"] == 1
    assert result["source_classes"]["dynamic_mesh"]["status"] == "unproven_absent"


def test_temporal_comparison_fails_claimed_class_without_matched_identity(tmp_path):
    runner = load_runner()
    frame_a = _comparison_dump(
        tmp_path / "a",
        [_contact_row("terrain", "(1,0,2,1)", 35, [1, 2, 3], [4, 5, 6], final_glyph_id=664)],
    )
    frame_b = _comparison_dump(
        tmp_path / "b",
        [_contact_row("terrain", "(9,0,8,1)", 35, [1, 2, 3], [4, 5, 6], final_glyph_id=546)],
    )
    result = runner._compare_fl4216_temporal_frames(
        [{"final_render_cell_dump": frame_a}, {"final_render_cell_dump": frame_b}]
    )
    assert result["pass"] is False
    assert result["reason"] == "claimed_class_has_zero_comparable_cells"
    assert result["source_classes"]["terrain"]["status"] == "unproven_no_comparable_identity"


def test_temporal_comparison_fails_static_claimed_class(tmp_path):
    runner = load_runner()
    row = _contact_row("static_mesh", "(3,0,4,1)", 36, [7, 8, 9], [10, 11, 12], final_glyph_id=668)
    frame_a = _comparison_dump(tmp_path / "a", [row])
    frame_b = _comparison_dump(tmp_path / "b", [row])
    result = runner._compare_fl4216_temporal_frames(
        [{"final_render_cell_dump": frame_a}, {"final_render_cell_dump": frame_b}]
    )
    assert result["pass"] is False
    assert result["reason"] == "claimed_class_has_no_changed_break_morphology"
    assert result["source_classes"]["static_mesh"]["status"] == "static_no_changed_break_morphology"


def test_temporal_comparison_uses_final_paint_glyph_when_top_level_glyph_is_null(tmp_path):
    runner = load_runner()
    frame_a = _comparison_dump(
        tmp_path / "a",
        [_contact_row("terrain", "(1,0,2,1)", None, [1, 2, 3], [4, 5, 6], final_glyph_id=664)],
    )
    frame_b = _comparison_dump(
        tmp_path / "b",
        [_contact_row("terrain", "(1,0,2,1)", None, [1, 2, 3], [4, 5, 6], final_glyph_id=546)],
    )
    result = runner._compare_fl4216_temporal_frames(
        [{"final_render_cell_dump": frame_a}, {"final_render_cell_dump": frame_b}]
    )
    assert result["pass"] is True
    assert result["source_classes"]["terrain"]["break_state_changed_count"] == 1


def test_temporal_comparison_rejects_non_break_extended_glyph_670(tmp_path):
    runner = load_runner()
    frame_a = _comparison_dump(
        tmp_path / "a",
        [_contact_row("terrain", "(1,0,2,1)", 670, [1, 2, 3], [4, 5, 6])],
    )
    frame_b = _comparison_dump(
        tmp_path / "b",
        [_contact_row("terrain", "(1,0,2,1)", 670, [20, 2, 3], [4, 5, 6], final_glyph_id=664)],
    )
    result = runner._compare_fl4216_temporal_frames(
        [{"final_render_cell_dump": frame_a}, {"final_render_cell_dump": frame_b}]
    )
    assert result["pass"] is False
    assert result["reason"] == "claimed_class_missing_break_glyph_in_frame"
    assert result["source_classes"]["terrain"]["break_glyph_count_by_frame"] == [0, 1]


def test_temporal_comparison_ignores_generic_fg_bg_only_change(tmp_path):
    runner = load_runner()
    frame_a = _comparison_dump(
        tmp_path / "a",
        [_contact_row("terrain", "(1,0,2,1)", None, [1, 2, 3], [4, 5, 6], final_glyph_id=664)],
    )
    frame_b = _comparison_dump(
        tmp_path / "b",
        [_contact_row("terrain", "(1,0,2,1)", None, [20, 2, 3], [40, 5, 6], final_glyph_id=664)],
    )
    result = runner._compare_fl4216_temporal_frames(
        [{"final_render_cell_dump": frame_a}, {"final_render_cell_dump": frame_b}]
    )
    assert result["pass"] is False
    assert result["reason"] == "claimed_class_has_no_changed_break_morphology"
    assert result["source_classes"]["terrain"]["break_state_changed_count"] == 0


def test_temporal_comparison_accepts_real_break_glyph_change(tmp_path):
    runner = load_runner()
    frame_a = _comparison_dump(
        tmp_path / "a",
        [_contact_row("terrain", "(1,0,2,1)", None, [1, 2, 3], [4, 5, 6], final_glyph_id=664)],
    )
    frame_b = _comparison_dump(
        tmp_path / "b",
        [_contact_row("terrain", "(1,0,2,1)", None, [1, 2, 3], [4, 5, 6], final_glyph_id=546)],
    )
    result = runner._compare_fl4216_temporal_frames(
        [{"final_render_cell_dump": frame_a}, {"final_render_cell_dump": frame_b}]
    )
    assert result["pass"] is True
    assert result["reason"] == "temporal_shoreline_morphology_comparison_ok"
