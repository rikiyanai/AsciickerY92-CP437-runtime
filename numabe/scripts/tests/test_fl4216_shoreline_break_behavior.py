"""Focused adversarial tests for the FL-4216 shoreline-break behavior gate."""

from __future__ import annotations

import json
import base64
import struct
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from check_fl4216_shoreline_break_behavior import (
    P1_CAMERA,
    P1_PITCH,
    P1_PLAYER,
    P1_YAW,
    FL4216_SHORELINE_BREAK_GLYPH_ID_SET,
    evaluate_receipt,
)


BREAK_IDS = sorted(FL4216_SHORELINE_BREAK_GLYPH_ID_SET)


def _metadata(frame_time: float) -> dict:
    return {
        "camera_world": {"x": P1_CAMERA[0], "y": P1_CAMERA[1], "z": P1_CAMERA[2]},
        "player_world": {"x": P1_PLAYER[0], "y": P1_PLAYER[1], "z": P1_PLAYER[2]},
        "pixel_camera_capture_contract": {
            "scene_camera_origin": list(P1_CAMERA),
            "actor_presentation_reference_world": list(P1_PLAYER),
            "camera_projection_mode": 0,
            "camera_yaw": P1_YAW,
            "camera_pitch": P1_PITCH,
            "actor_presentation_camera_contract_valid": True,
            "frame_time_seconds": frame_time,
        },
    }


def _row(source_class: str, index: int, glyph_id: int) -> dict:
    source_kind = {"terrain": 2, "static_mesh": 1}[source_class]
    return {
        # Screen positions intentionally span two rows and four columns.  They
        # are the final-paint/contact identity, not a screenshot-derived proxy.
        "screen_cell": {"x": 20 + index % 4, "y": 30 + index // 4},
        "final_cell_paint_buffer": {
            "glyph_id": glyph_id,
            "final_mask_glyph_ids": [glyph_id],
        },
        "selected_surface_quarters": [{
            "valid": True,
            "owner_kind": 3,
            "submerged_source_owner_kind": source_kind,
            "direct_water_solid_contact": True,
            "stable_world_anchor": [float(index), 0.0, float(index + 1), 1.0],
        }],
    }


def _receipt(tmp_path: Path, *, phases: list[list[int]] | None = None) -> dict:
    if phases is None:
        # Break coverage remains spatially mixed while the advancing atlas
        # stage moves across different identities between cadence frames.
        phases = [
            [BREAK_IDS[0], 35, BREAK_IDS[1], 35, 35, BREAK_IDS[2], 35, BREAK_IDS[3]],
            [BREAK_IDS[0], BREAK_IDS[4], 35, BREAK_IDS[5], 35, BREAK_IDS[2], BREAK_IDS[6], 35],
            [BREAK_IDS[7], 35, BREAK_IDS[8], BREAK_IDS[9], 35, BREAK_IDS[2], 35, BREAK_IDS[10]],
        ]
    frames = []
    for frame_index, glyphs in enumerate(phases):
        frame_dir = tmp_path / f"frame-{frame_index}"
        frame_dir.mkdir(parents=True, exist_ok=True)
        cells = frame_dir / "cells.jsonl"
        rows = []
        for source_class in ("terrain", "static_mesh"):
            rows.extend(_row(source_class, index, glyph) for index, glyph in enumerate(glyphs))
        cells.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        metadata_path = frame_dir / "metadata.json"
        metadata_path.write_text(json.dumps(_metadata(10.0 + 1.6 * frame_index)), encoding="utf-8")
        (frame_dir / "final.png").write_bytes(b"png-proof")
        (frame_dir / "index.html").write_text("<html></html>", encoding="utf-8")
        frames.append({
            "capture_index": frame_index,
            "same_godot_process": True,
            "capture_started_ticks_usec": 1000000 + frame_index * 1600000,
            "temporal_snapshot_request_id": frame_index + 1,
            "selected_surface_record_count": 32,
            "runtime_field_contract": {"pass": True, "owner": "ASCIICompositorEffect.queue_fl4216_temporal_snapshot"},
            "artifact_pose_contract": {"pass": True},
            "runtime_truth_snapshot": {
                "capture_phase": "cadence_snapshot",
                "frame_time_seconds": 10.0 + 1.6 * frame_index,
                "metadata": _metadata(10.0 + 1.6 * frame_index),
            },
            "final_render_cell_dump": {
                "ok": True,
                "capture_schema": "fl4216_temporal_snapshot.v1-expanded",
                "artifact_paths": {
                    "cells_jsonl": str(cells),
                    "metadata_json": str(metadata_path),
                    "final_png": str(frame_dir / "final.png"),
                    "index_html": str(frame_dir / "index.html"),
                },
            },
        })
    return {
        "fl": "FL-4216",
        "proof": "same_process_exact_p1_final_render_runtime_truth",
        "route_identity": "fl4216_same_process_temporal_v1",
        "camera_mode": "perspective",
        "exact_camera_projection": "perspective",
        "exact_player_position": list(P1_PLAYER),
        "exact_camera_position": list(P1_CAMERA),
        "exact_camera_yaw": P1_YAW,
        "exact_camera_pitch": P1_PITCH,
        "fl4216_temporal_capture": True,
        "fl4216_temporal_same_process": True,
        "fl4216_temporal_capture_count": len(frames),
        "fl4216_temporal_interval_seconds": 1.6,
        "fl4216_temporal_interval_measured_seconds": [0.1] + [1.6] * (len(frames) - 1),
        "exact_pose_requested": True,
        "fl4216_grounding_contract": {
            "pass": True,
            "source": "authoritative grounded player position",
            "authoritative_grounded_player_position": list(P1_PLAYER),
            "stable_publication_player_position": list(P1_PLAYER),
            "publication_consistency_error": 0.0,
        },
        "run_identity": {
            "source_identity_complete": True,
            "build_head": "fixture-head",
            "build_worktree_diff_sha256": "fixture-diff",
        },
        "fl4216_temporal_frames": frames,
    }


def _snapshot_dump(frame_dir: Path, frame_time: float, glyphs: list[int]) -> dict:
    """Build the current 320-byte final-paint ABI plus the 96-byte b64 ABI."""
    frame_dir.mkdir(parents=True, exist_ok=True)
    width, height = 8, 1
    final_bytes = bytearray(width * height * 320)
    selected_bytes = bytearray(width * height * 4 * 96)
    for cell_index, glyph_id in enumerate(glyphs):
        final_base = cell_index * 320
        struct.pack_into("<III", final_bytes, final_base, 0xF14231, 3, glyph_id)
        struct.pack_into("<I", final_bytes, final_base + 76, 1)
        struct.pack_into("<I", final_bytes, final_base + 84, glyph_id + 1)
        source_code = 1 if cell_index < 4 else 2
        for slot in range(4):
            sx = cell_index * 2 + (slot & 1)
            sy = slot >> 1
            selected_base = (sy * (width * 2) + sx) * 96
            struct.pack_into("<IIII", selected_bytes, selected_base, 3, 100 + cell_index, slot, 3)
            struct.pack_into("<I", selected_bytes, selected_base + 60, 1 | (source_code << 7) | (1 << 29))
            struct.pack_into("<4f", selected_bytes, selected_base + 64, float(cell_index), 0.0, float(cell_index + 1), 1.0)
    metadata = _metadata(frame_time)
    metadata["pixel_camera_capture_contract"]["semantic_ansi_size"] = [width, height]
    snapshot = {
        "schema": "fl4216_temporal_snapshot.v1",
        "capture_phase": "cadence_snapshot",
        "metadata": metadata,
        "final_cell_record_stride_bytes": 320,
        "selected_surface_record_count": width * height * 4,
        "selected_surface_record_stride_bytes": 96,
        "final_cell_paint_b64": base64.b64encode(final_bytes).decode("ascii"),
        "selected_surface_b64": base64.b64encode(selected_bytes).decode("ascii"),
    }
    snapshot_path = frame_dir / "snapshot.json"
    metadata_path = frame_dir / "metadata.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    png_path = frame_dir / "final.png"
    png_path.write_bytes(b"png-proof")
    return {
        "ok": True,
        "capture_schema": "fl4216_temporal_snapshot.v1",
        "artifact_paths": {
            "temporal_snapshot_json": str(snapshot_path),
            "metadata_json": str(metadata_path),
            "final_png": str(png_path),
        },
    }


def test_accepts_temporally_advancing_spatially_broken_final_paint(tmp_path: Path) -> None:
    result = evaluate_receipt(_receipt(tmp_path))
    assert result["pass"] is True, result
    assert result["reason"] == "shoreline_break_behavior_ok"
    assert result["temporal"]["source_classes"]["terrain"]["changed_count"] > 0
    assert result["spatial"]["0"]["terrain"]["mixed_break_coverage"] is True


def test_consumes_current_320_byte_temporal_snapshot_abi(tmp_path: Path) -> None:
    receipt = _receipt(tmp_path / "expanded")
    phases = [
        [BREAK_IDS[0], 35, BREAK_IDS[1], 35, 35, BREAK_IDS[2], 35, BREAK_IDS[3]],
        [BREAK_IDS[0], BREAK_IDS[4], 35, 35, 35, BREAK_IDS[5], BREAK_IDS[6], 35],
        [BREAK_IDS[7], 35, BREAK_IDS[8], 35, 35, BREAK_IDS[9], 35, BREAK_IDS[10]],
    ]
    snapshot_frames = []
    for index, glyphs in enumerate(phases):
        snapshot_frames.append({
            "capture_index": index,
            "same_godot_process": True,
            "capture_started_ticks_usec": 1000000 + index * 1600000,
            "temporal_snapshot_request_id": index + 1,
            "selected_surface_record_count": 32,
            "runtime_field_contract": {"pass": True},
            "artifact_pose_contract": {"pass": True},
            "runtime_truth_snapshot": {
                "capture_phase": "cadence_snapshot",
                "frame_time_seconds": 10.0 + index * 1.6,
                "metadata": _metadata(10.0 + index * 1.6),
            },
            "final_render_cell_dump": _snapshot_dump(tmp_path / f"snapshot-{index}", 10.0 + index * 1.6, glyphs),
        })
    receipt["fl4216_temporal_frames"] = snapshot_frames
    result = evaluate_receipt(receipt)
    assert result["pass"] is True, result
    assert all(
        item.get("final_cell_record_stride_bytes") == 320
        for item in result["checks"]["frame_diagnostics"]
    )


@pytest.mark.parametrize(
    ("field", "replacement", "reason_fragment"),
    [
        ("run_identity", None, "run_identity:missing"),
        ("exact_camera_position", None, "p1_pose:exact_player_camera_yaw_pitch_required"),
        ("fl4216_temporal_interval_seconds", None, "fl4216_temporal_interval_seconds:outside_product_window"),
    ],
)
def test_missing_provenance_pose_or_timing_fails_closed(
    tmp_path: Path, field: str, replacement: object, reason_fragment: str
) -> None:
    receipt = _receipt(tmp_path)
    receipt[field] = replacement
    result = evaluate_receipt(receipt)
    assert result["pass"] is False
    assert reason_fragment in result["failures"]


def test_missing_runtime_contract_fails_closed(tmp_path: Path) -> None:
    receipt = _receipt(tmp_path)
    receipt["fl4216_temporal_frames"][1]["runtime_field_contract"] = None
    result = evaluate_receipt(receipt)
    assert result["pass"] is False
    assert "frames[1].runtime_field_contract.pass:required" in result["failures"]


def test_absent_shoreline_band_fails(tmp_path: Path) -> None:
    phases = [[35] * 8, [35] * 8, [35] * 8]
    result = evaluate_receipt(_receipt(tmp_path, phases=phases))
    assert result["pass"] is False
    assert any("absent_solid_or_spatially_homogeneous" in item for item in result["failures"])


def test_solid_shoreline_band_fails(tmp_path: Path) -> None:
    phases = [[BREAK_IDS[0]] * 8, [BREAK_IDS[1]] * 8, [BREAK_IDS[2]] * 8]
    result = evaluate_receipt(_receipt(tmp_path, phases=phases))
    assert result["pass"] is False
    assert any("absent_solid_or_spatially_homogeneous" in item for item in result["failures"])


def test_spatially_homogeneous_single_cell_band_fails(tmp_path: Path) -> None:
    receipt = _receipt(tmp_path)
    for frame in receipt["fl4216_temporal_frames"]:
        cells = Path(frame["final_render_cell_dump"]["artifact_paths"]["cells_jsonl"])
        rows = [json.loads(line) for line in cells.read_text(encoding="utf-8").splitlines()]
        for row in rows:
            row["screen_cell"] = {"x": 20, "y": 30}
        cells.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    result = evaluate_receipt(receipt)
    assert result["pass"] is False
    assert any("spatially_homogeneous" in item for item in result["failures"])


def test_temporally_static_coverage_fails(tmp_path: Path) -> None:
    phases = [[BREAK_IDS[0], 35, BREAK_IDS[1], 35, 35, BREAK_IDS[2], 35, BREAK_IDS[3]]] * 3
    result = evaluate_receipt(_receipt(tmp_path, phases=phases))
    assert result["pass"] is False
    assert any("temporally_static" in item for item in result["failures"])


def test_global_lockstep_transition_fails(tmp_path: Path) -> None:
    phases = [
        [BREAK_IDS[0]] * 4 + [35] * 4,
        [35] * 4 + [BREAK_IDS[1]] * 4,
        [BREAK_IDS[2]] * 4 + [35] * 4,
    ]
    result = evaluate_receipt(_receipt(tmp_path, phases=phases))
    assert result["pass"] is False
    assert any("global_lockstep" in item for item in result["failures"])


def test_plane_air_and_wrong_p1_route_are_rejected(tmp_path: Path) -> None:
    receipt = _receipt(tmp_path)
    receipt["camera_mode"] = "plane_air"
    result = evaluate_receipt(receipt)
    assert result["pass"] is False
    assert "camera_mode:plane_air_forbidden" in result["failures"]

    receipt = _receipt(tmp_path / "wrong-p1")
    receipt["exact_player_position"] = [0.0, 0.0, 0.0]
    result = evaluate_receipt(receipt)
    assert result["pass"] is False
    assert "p1_pose:exact_player_camera_yaw_pitch_required" in result["failures"]
