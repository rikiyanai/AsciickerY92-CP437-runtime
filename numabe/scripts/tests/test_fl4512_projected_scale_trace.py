from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = (
    ROOT / "scripts/fl4548/fl4548_verify_projected_feature_extent_trace.py"
)
FIXTURE_PATH = ROOT / "assets/glyphs/fixtures/fl4512_projected_scale_golden_v1.json"


def load_verifier():
    spec = importlib.util.spec_from_file_location(
        "fl4548_verify_projected_feature_extent_trace", VERIFIER_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_trace_record() -> dict:
    fixture = json.loads(FIXTURE_PATH.read_text())
    case = fixture["cases"][1]
    expected = case["expected"]
    return {
        "schema": "fl4512.projected_feature_extent_trace.v1",
        "frame": 7,
        "cell": [12, 9],
        "feature_ref": {
            "world_generation": 1,
            "surface": {"world_generation": 1, "owner_kind": 3, "owner_x": 7,
                        "owner_y": 0, "anchor_u_q": 0, "anchor_v_q": 0},
            "material_key": 200,
            "depth_owner": 77,
            "dispatch_surface": 4,
            "role": 1,
            "feature_kind": 2,
            "primitive_id": 19,
            "local_feature_id": 3,
            "semantic_chain_id": 77,
            "chain_phase0_q16": 0,
            "chain_phase1_q16": 65535,
        },
        "object_local_endpoints_q24_8": [0, 0, 0, 256, 0, 0],
        "projected_endpoints_q24_8": [256, 256, 512, 256],
        "scale_anchor_q24_8": [128, 0, 0],
        "asset_metric_q24_8": [256, 256, 256],
        "projection_kind": case["projection_kind"],
        "anchor_camera": case["anchor_camera"],
        "focal_cells": case["focal_cells"],
        "scale_cells": [0.0, 0.0],
        "asset_to_camera_row_major": case["asset_to_camera_row_major"],
        "jacobian_asset_to_cell": expected["jacobian_asset_to_cell"],
        "scale_quantization_version": fixture["scale_quantization_version"],
        "projected_scale_q": expected["projected_scale_q"],
        "scale_valid": True,
        "scale_reason": "none",
		"extent_quantization_version": 1,
		"projected_extent_q": 65536,
        "enter_q": 65536,
        "exit_q": 49152,
        "feature_extent_admission_sha256": "a" * 64,
        "covered_subsamples": 9,
        "clipped_mask8": "00000000000001ff",
        "admission_state": "CLAIMED_CURRENT",
    }


def write_trace(path: Path, record: dict) -> None:
    path.write_text(json.dumps(record, separators=(",", ":")) + "\n")


def test_trace_replays_projection_preimage_and_admission(tmp_path: Path) -> None:
    verifier = load_verifier()
    path = tmp_path / "projected_scale_trace.jsonl"
    write_trace(path, valid_trace_record())
    result = verifier.validate_projected_feature_extent_trace(
        path, action_start=0, action_end=path.stat().st_size,
        expected_material_key=200
    )
    assert result["ok"]
    assert result["record_count"] == 1
    assert result["action_window_record_count"] == 1
    assert result["expected_material_record_count"] == 1
    assert result["problems"] == []


def test_trace_rejects_nonrecomputable_scale_and_wrong_handoff(tmp_path: Path) -> None:
    verifier = load_verifier()
    record = valid_trace_record()
    record["projected_scale_q"] += 1
    record["admission_state"] = "U6_RETENTION_TARGET"
    path = tmp_path / "projected_scale_trace.jsonl"
    write_trace(path, record)
    result = verifier.validate_projected_feature_extent_trace(path)
    assert not result["ok"]
    assert "line 1: projected_scale_q_mismatch" in result["problems"]
    assert "line 1: admission_state" in result["problems"]


def test_trace_rejects_missing_full_feature_identity(tmp_path: Path) -> None:
    verifier = load_verifier()
    record = copy.deepcopy(valid_trace_record())
    del record["feature_ref"]["surface"]
    path = tmp_path / "projected_scale_trace.jsonl"
    write_trace(path, record)
    result = verifier.validate_projected_feature_extent_trace(path)
    assert not result["ok"]
    assert "line 1: feature_ref" in result["problems"]


def test_trace_action_window_requires_target_mesh_record(tmp_path: Path) -> None:
    verifier = load_verifier()
    path = tmp_path / "projected_scale_trace.jsonl"
    write_trace(path, valid_trace_record())
    end = path.stat().st_size
    result = verifier.validate_projected_feature_extent_trace(
        path, action_start=end, action_end=end, expected_material_key=200
    )
    assert not result["ok"]
    assert "empty_projected_scale_action_window" in result["problems"]
    assert "missing_expected_material_scale_record" in result["problems"]


def test_invalid_target_material_record_cannot_close_join(tmp_path: Path) -> None:
    verifier = load_verifier()
    record = valid_trace_record()
    record["projected_scale_q"] += 1
    path = tmp_path / "projected_scale_trace.jsonl"
    write_trace(path, record)
    result = verifier.validate_projected_feature_extent_trace(
        path, action_start=0, action_end=path.stat().st_size,
        expected_material_key=200
    )
    assert not result["ok"]
    assert result["expected_material_record_count"] == 0
    assert "missing_expected_material_scale_record" in result["problems"]
