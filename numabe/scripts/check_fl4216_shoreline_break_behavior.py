#!/usr/bin/env python3
"""Fail-closed machine gate for the FL-4216 shoreline-break animation.

The headed runner owns capture.  This checker owns only interpretation of its
continuous-run receipt and final-paint/contact artifacts.  It deliberately does
not inspect screenshots, source presence, or diagnostic top-level glyph fields.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import struct
import sys
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from run_fl4359_headed_movement import (  # noqa: E402
    FL4216_P1_REQUIRED_SOURCE_CLASSES,
    FL4216_SHORELINE_BREAK_GLYPH_ID_SET,
    FL4216_TEMPORAL_SOURCE_CLASSES,
    FL4216_TEMPORAL_SOURCE_OWNER_KINDS,
    FL4216_TEMPORAL_PRODUCT_DELTA_MAX_SECONDS,
    FL4216_TEMPORAL_PRODUCT_DELTA_MIN_SECONDS,
    _fl4216_temporal_snapshot_rows,
)


SCHEMA = "fl4216.shoreline_break_behavior.v1"
FEATURE_ID = "GODOT-W07"
FL_ID = "FL-4216"
EXPECTED_PROOF = "same_process_exact_p1_final_render_runtime_truth"
EXPECTED_ROUTE = "fl4216_same_process_temporal_v1"
EXPECTED_PROJECTION = "perspective"
P1_PLAYER = (127.110, 0.0, -3.923)
P1_CAMERA = (124.412, 19.5625, 28.967)
P1_YAW = -0.08185
P1_PITCH = -0.46365
POSE_EPSILON = 0.01
MIN_CONTACTS_PER_CLASS = 4
MIN_MATCH_RATIO = 0.75


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _vec3(value: Any) -> tuple[float, float, float] | None:
    if isinstance(value, Mapping):
        values = (value.get("x"), value.get("y"), value.get("z"))
    elif isinstance(value, (list, tuple)) and len(value) >= 3:
        values = (value[0], value[1], value[2])
    elif isinstance(value, str):
        parts = [part.strip() for part in value.replace(";", ",").split(",")]
        if len(parts) < 3:
            return None
        values = parts[:3]
    else:
        return None
    numbers = tuple(_number(component) for component in values)
    return numbers if all(component is not None for component in numbers) else None  # type: ignore[return-value]


def _angle_error(actual: Any, expected: float) -> float | None:
    number = _number(actual)
    if number is None:
        return None
    return abs(math.atan2(math.sin(number - expected), math.cos(number - expected)))


def _path(value: Any, base: Path) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    local = base / candidate
    if local.exists():
        return local
    return ROOT / candidate


def _normalize_dump(dump: Any, base: Path) -> dict[str, Any]:
    if not isinstance(dump, Mapping):
        return {}
    normalized = dict(dump)
    paths = dump.get("artifact_paths")
    if isinstance(paths, Mapping):
        normalized["artifact_paths"] = {
            key: str(resolved) if (resolved := _path(value, base)) is not None else value
            for key, value in paths.items()
        }
    return normalized


def _contains_forbidden_mode(value: Any) -> bool:
    if isinstance(value, str):
        return value in {"plane_air", "plane_air_ortho"}
    if isinstance(value, Mapping):
        return any(_contains_forbidden_mode(key) or _contains_forbidden_mode(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_mode(item) for item in value)
    return False


def _record_failure(failures: list[str], condition: bool, reason: str) -> None:
    if not condition and reason not in failures:
        failures.append(reason)


def _metadata_for_dump(dump: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    paths = dump.get("artifact_paths")
    if not isinstance(paths, Mapping):
        return None, "artifact_paths:missing"
    metadata_path = Path(str(paths.get("metadata_json", "")))
    if not metadata_path.is_file():
        return None, "artifact_paths.metadata_json:file_missing"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "artifact_paths.metadata_json:invalid_json"
    if not isinstance(metadata, dict):
        return None, "artifact_paths.metadata_json:not_object"
    return metadata, None


def _decode_rows(dump: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if dump.get("capture_schema") == "fl4216_temporal_snapshot.v1":
        # The shared runner decoder covers historical 112/148/168-byte rows.
        # Current Godot emits the same stable prefix in its 320-byte live ABI;
        # decode that ABI here without changing the producer or trusting a
        # stale stride fallback.
        rows, diagnostics = _fl4216_temporal_snapshot_rows(dict(dump))
        if diagnostics.get("readback_state") == "temporal_snapshot_decode_ok":
            return rows, diagnostics
        paths = dump.get("artifact_paths")
        snapshot_path = Path(str(paths.get("temporal_snapshot_json", ""))) if isinstance(paths, Mapping) else Path("")
        try:
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            contract = payload["metadata"]["pixel_camera_capture_contract"]
            width, height = (int(contract["semantic_ansi_size"][0]), int(contract["semantic_ansi_size"][1]))
            final_stride = int(payload["final_cell_record_stride_bytes"])
            selected_stride = int(payload["selected_surface_record_stride_bytes"])
            selected_count = int(payload["selected_surface_record_count"])
            final_bytes = base64.b64decode(payload["final_cell_paint_b64"], validate=True)
            selected_bytes = base64.b64decode(payload["selected_surface_b64"], validate=True)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return [], {**diagnostics, "readback_state": "temporal_snapshot_decode_failed"}
        if (
            payload.get("schema") != "fl4216_temporal_snapshot.v1"
            or width <= 0 or height <= 0
            or final_stride not in (112, 148, 168, 320)
            or selected_stride != 96
            or len(final_bytes) != width * height * final_stride
            or selected_count != width * height * 4
            or len(selected_bytes) != selected_count * selected_stride
        ):
            return [], {**diagnostics, "readback_state": "temporal_snapshot_raw_buffer_shape_mismatch"}
        decoded: list[dict[str, Any]] = []
        contact_quarters = 0
        for y in range(height):
            for x in range(width):
                final_base = (y * width + x) * final_stride
                magic, _owner_kind, glyph_id = struct.unpack_from("<III", final_bytes, final_base)
                if magic != 0xF14231:
                    return [], {**diagnostics, "readback_state": "final_cell_paint_magic_mismatch"}
                mask_presence = struct.unpack_from("<I", final_bytes, final_base + 76)[0]
                mask_glyphs = [
                    (encoded - 1 if encoded else None)
                    for encoded, in (
                        struct.unpack_from("<I", final_bytes, final_base + 84 + slot * 4)
                        for slot in range(4)
                    )
                ]
                quarters: list[dict[str, Any]] = []
                for slot in range(4):
                    sx = x * 2 + (slot & 1)
                    sy = y * 2 + (slot >> 1)
                    selected_base = (sy * (width * 2) + sx) * selected_stride
                    owner, object_id, primitive_id, abi = struct.unpack_from("<IIII", selected_bytes, selected_base)
                    status = struct.unpack_from("<I", selected_bytes, selected_base + 60)[0]
                    anchor = struct.unpack_from("<4f", selected_bytes, selected_base + 64)
                    valid = bool(status & 1) and abi == 3 and owner in (1, 2, 3, 4)
                    submerged_code = (status & 0x180) >> 7
                    submerged_owner = {1: 2, 2: 1, 3: 4}.get(submerged_code, 0)
                    contact = bool(status & (1 << 29))
                    quarters.append({
                        "valid": valid,
                        "owner_kind": owner,
                        "object_id": object_id,
                        "primitive_id": primitive_id,
                        "stable_world_anchor": list(anchor),
                        "direct_water_solid_contact": contact,
                        "submerged_source_owner_kind": submerged_owner,
                        "submerged_source_present": submerged_owner != 0,
                    })
                    contact_quarters += int(valid and contact)
                decoded.append({
                    "screen_cell": {"x": x, "y": y},
                    "glyph_id": glyph_id,
                    "final_cell_paint_buffer": {
                        "glyph_id": glyph_id,
                        "final_mask_glyph_ids": mask_glyphs if mask_presence else [],
                    },
                    "selected_surface_quarters": quarters,
                })
        return decoded, {
            "cells_read": len(decoded),
            "contact_quarters_read": contact_quarters,
            "json_parse_errors": 0,
            "missing_identity_rows": 0,
            "readback_state": "temporal_snapshot_decode_ok",
            "final_cell_record_stride_bytes": final_stride,
        }
    paths = dump.get("artifact_paths")
    cells_path = Path(str(paths.get("cells_jsonl", ""))) if isinstance(paths, Mapping) else Path("")
    diagnostics: dict[str, Any] = {
        "cells_read": 0,
        "contact_quarters_read": 0,
        "json_parse_errors": 0,
        "readback_state": "expanded_cells_jsonl",
    }
    rows: list[dict[str, Any]] = []
    if not cells_path.is_file():
        diagnostics["readback_state"] = "missing_cells_jsonl"
        return rows, diagnostics
    try:
        with cells_path.open("r", encoding="utf-8") as cells_file:
            for line in cells_file:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    diagnostics["json_parse_errors"] += 1
                    continue
                if isinstance(row, dict):
                    rows.append(row)
                    diagnostics["cells_read"] += 1
                    quarters = row.get("selected_surface_quarters")
                    if isinstance(quarters, list):
                        diagnostics["contact_quarters_read"] += sum(
                            1 for quarter in quarters
                            if isinstance(quarter, Mapping)
                            and quarter.get("valid") is True
                            and quarter.get("direct_water_solid_contact") is True
                        )
    except OSError:
        diagnostics["readback_state"] = "unreadable_cells_jsonl"
    return rows, diagnostics


def _identity(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _screen_position(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, Mapping):
        return None
    x = _number(value.get("x"))
    y = _number(value.get("y"))
    return (x, y) if x is not None and y is not None else None


def _final_paint(row: Mapping[str, Any]) -> tuple[int | None, tuple[int, ...] | None]:
    paint = row.get("final_cell_paint_buffer")
    if not isinstance(paint, Mapping):
        return None, None
    glyph = paint.get("glyph_id")
    glyph_id = int(glyph) if _number(glyph) is not None else None
    masks = paint.get("final_mask_glyph_ids")
    if not isinstance(masks, list):
        return glyph_id, None
    mask_ids = tuple(int(item) for item in masks if _number(item) is not None)
    return glyph_id, mask_ids


def _frame_contacts(dump: Mapping[str, Any]) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, Any]]:
    rows, diagnostics = _decode_rows(dump)
    contacts = {source_class: {} for source_class in FL4216_TEMPORAL_SOURCE_CLASSES}
    for row in rows:
        screen = _screen_position(row.get("screen_cell"))
        paint_glyph, paint_masks = _final_paint(row)
        quarters = row.get("selected_surface_quarters")
        if screen is None or paint_glyph is None or not isinstance(quarters, list):
            continue
        for quarter in quarters:
            if not isinstance(quarter, Mapping):
                continue
            if quarter.get("valid") is not True or quarter.get("direct_water_solid_contact") is not True:
                continue
            source_kind = quarter.get("submerged_source_owner_kind")
            source_class = FL4216_TEMPORAL_SOURCE_OWNER_KINDS.get(int(source_kind)) if _number(source_kind) is not None else None
            anchor = quarter.get("stable_world_anchor")
            if source_class is None or anchor is None:
                diagnostics["missing_identity_rows"] = diagnostics.get("missing_identity_rows", 0) + 1
                continue
            break_glyph = paint_glyph if paint_glyph in FL4216_SHORELINE_BREAK_GLYPH_ID_SET else None
            break_mask = tuple(item for item in (paint_masks or ()) if item in FL4216_SHORELINE_BREAK_GLYPH_ID_SET)
            identity = f"{_identity(row.get('screen_cell'))}|{_identity(anchor)}"
            contacts[source_class][identity] = {
                "screen": screen,
                "anchor": anchor,
                "glyph_id": paint_glyph,
                "break_glyph_id": break_glyph,
                "break_mask": break_mask,
                "break": break_glyph is not None,
                "state": (break_glyph, break_mask),
            }
    diagnostics["reason"] = "contact_rows_ok" if diagnostics.get("json_parse_errors", 0) == 0 else "contact_rows_json_invalid"
    return contacts, diagnostics


def _check_identity(receipt: Mapping[str, Any], failures: list[str]) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    checks["feature_id"] = receipt.get("feature_id", FEATURE_ID) == FEATURE_ID
    _record_failure(failures, checks["feature_id"], "feature_id:expected_GODOT-W07")
    checks["fl"] = receipt.get("fl") == FL_ID
    _record_failure(failures, checks["fl"], "fl:expected_FL-4216")
    checks["proof"] = receipt.get("proof") == EXPECTED_PROOF
    _record_failure(failures, checks["proof"], "proof:expected_same_process_exact_p1_final_render_runtime_truth")
    checks["route_identity"] = receipt.get("route_identity") == EXPECTED_ROUTE
    _record_failure(failures, checks["route_identity"], "route_identity:expected_fl4216_same_process_temporal_v1")
    checks["camera_mode"] = receipt.get("camera_mode") == EXPECTED_PROJECTION
    _record_failure(failures, checks["camera_mode"], "camera_mode:perspective_required")
    checks["exact_camera_projection"] = receipt.get("exact_camera_projection") == EXPECTED_PROJECTION
    _record_failure(failures, checks["exact_camera_projection"], "exact_camera_projection:perspective_required")
    checks["temporal_capture"] = receipt.get("fl4216_temporal_capture") is True
    checks["same_process"] = receipt.get("fl4216_temporal_same_process") is True
    _record_failure(failures, checks["temporal_capture"], "fl4216_temporal_capture:true_required")
    _record_failure(failures, checks["same_process"], "fl4216_temporal_same_process:true_required")
    checks["exact_pose_requested"] = receipt.get("exact_pose_requested") is True
    _record_failure(failures, checks["exact_pose_requested"], "exact_pose_requested:true_required")
    if _contains_forbidden_mode(receipt):
        failures.append("camera_mode:plane_air_forbidden")
        checks["plane_air_rejected"] = False
    else:
        checks["plane_air_rejected"] = True
    identity = receipt.get("run_identity")
    checks["run_identity"] = isinstance(identity, Mapping)
    _record_failure(failures, checks["run_identity"], "run_identity:missing")
    if isinstance(identity, Mapping):
        for field in ("source_identity_complete", "build_head", "build_worktree_diff_sha256"):
            if field == "source_identity_complete":
                present = identity.get(field) is True
            else:
                present = isinstance(identity.get(field), str) and bool(identity.get(field).strip())
            checks[f"run_identity.{field}"] = present
            _record_failure(failures, present, f"run_identity.{field}:missing_or_invalid")
    grounding = receipt.get("fl4216_grounding_contract")
    grounding_fields = (
        "source",
        "authoritative_grounded_player_position",
        "stable_publication_player_position",
        "publication_consistency_error",
    )
    checks["grounding_contract"] = bool(
        isinstance(grounding, Mapping)
        and grounding.get("pass") is True
        and all(field in grounding for field in grounding_fields)
        and _vec3(grounding.get("authoritative_grounded_player_position")) is not None
        and _vec3(grounding.get("stable_publication_player_position")) is not None
        and _number(grounding.get("publication_consistency_error")) is not None
        and bool(grounding.get("source"))
    )
    _record_failure(failures, checks["grounding_contract"], "fl4216_grounding_contract:pass_required")
    root_player = _vec3(receipt.get("exact_player_position"))
    root_camera = _vec3(receipt.get("exact_camera_position"))
    player_error = math.dist(root_player, P1_PLAYER) if root_player is not None else None
    camera_error = math.dist(root_camera, P1_CAMERA) if root_camera is not None else None
    yaw_error = _angle_error(receipt.get("exact_camera_yaw"), P1_YAW)
    pitch_error = _number(receipt.get("exact_camera_pitch"))
    pitch_error = abs(pitch_error - P1_PITCH) if pitch_error is not None else None
    checks["p1_pose"] = bool(
        player_error is not None and player_error <= POSE_EPSILON
        and camera_error is not None and camera_error <= POSE_EPSILON
        and yaw_error is not None and yaw_error <= POSE_EPSILON
        and pitch_error is not None and pitch_error <= POSE_EPSILON
    )
    _record_failure(failures, checks["p1_pose"], "p1_pose:exact_player_camera_yaw_pitch_required")
    checks["p1_pose_errors"] = {
        "player": player_error,
        "camera": camera_error,
        "yaw": yaw_error,
        "pitch": pitch_error,
    }
    return checks


def _check_frame_metadata(
    frame: Mapping[str, Any],
    index: int,
    dump: Mapping[str, Any],
    receipt: Mapping[str, Any],
    failures: list[str],
) -> dict[str, Any]:
    prefix = f"frames[{index}]"
    result: dict[str, Any] = {}
    result["capture_index"] = frame.get("capture_index") == index
    _record_failure(failures, result["capture_index"], f"{prefix}.capture_index:expected_{index}")
    capture_started = _number(frame.get("capture_started_ticks_usec"))
    result["capture_started_ticks_usec"] = capture_started is not None
    _record_failure(failures, result["capture_started_ticks_usec"], f"{prefix}.capture_started_ticks_usec:required")
    request_id = _number(frame.get("temporal_snapshot_request_id"))
    result["temporal_snapshot_request_id"] = request_id is not None and request_id > 0
    _record_failure(failures, result["temporal_snapshot_request_id"], f"{prefix}.temporal_snapshot_request_id:required")
    selected_count = _number(frame.get("selected_surface_record_count"))
    result["selected_surface_record_count"] = selected_count is not None and selected_count > 0
    _record_failure(failures, result["selected_surface_record_count"], f"{prefix}.selected_surface_record_count:required")
    for field, expected in (("same_godot_process", True),):
        result[field] = frame.get(field) is expected
        _record_failure(failures, result[field], f"{prefix}.{field}:true_required")
    runtime_contract = frame.get("runtime_field_contract")
    result["runtime_field_contract"] = isinstance(runtime_contract, Mapping) and runtime_contract.get("pass") is True
    _record_failure(failures, result["runtime_field_contract"], f"{prefix}.runtime_field_contract.pass:required")
    pose_contract = frame.get("artifact_pose_contract")
    result["pose_contract"] = isinstance(pose_contract, Mapping) and pose_contract.get("pass") is True
    _record_failure(failures, result["pose_contract"], f"{prefix}.artifact_pose_contract.pass:required")
    runtime_truth = frame.get("runtime_truth_snapshot")
    runtime_truth_ok = isinstance(runtime_truth, Mapping) and runtime_truth.get("capture_phase") == "cadence_snapshot"
    frame_time = runtime_truth.get("frame_time_seconds") if isinstance(runtime_truth, Mapping) else None
    runtime_metadata = runtime_truth.get("metadata") if isinstance(runtime_truth, Mapping) else None
    runtime_metadata_contract = runtime_metadata.get("pixel_camera_capture_contract") if isinstance(runtime_metadata, Mapping) else None
    result["runtime_truth"] = (
        runtime_truth_ok
        and _number(frame_time) is not None
        and isinstance(runtime_metadata, Mapping)
        and isinstance(runtime_metadata_contract, Mapping)
    )
    _record_failure(failures, result["runtime_truth"], f"{prefix}.runtime_truth_snapshot.frame_time_seconds:cadence_value_required")
    metadata, metadata_error = _metadata_for_dump(dump)
    if metadata is None:
        _record_failure(failures, False, f"{prefix}.{metadata_error or 'metadata:missing'}")
        result["metadata"] = False
        return result
    contract = metadata.get("pixel_camera_capture_contract")
    fields = (
        "scene_camera_origin",
        "actor_presentation_reference_world",
        "camera_projection_mode",
        "camera_yaw",
        "camera_pitch",
        "actor_presentation_camera_contract_valid",
    )
    result["metadata"] = isinstance(contract, Mapping) and all(field in contract for field in fields)
    _record_failure(failures, result["metadata"], f"{prefix}.metadata.pixel_camera_capture_contract:required_fields_missing")
    if not isinstance(contract, Mapping):
        return result
    camera = _vec3(contract.get("scene_camera_origin"))
    actor = _vec3(contract.get("actor_presentation_reference_world"))
    grounding = receipt.get("fl4216_grounding_contract")
    grounded = _vec3(grounding.get("authoritative_grounded_player_position")) if isinstance(grounding, Mapping) else None
    camera_error = math.dist(camera, P1_CAMERA) if camera is not None else None
    actor_xz_error = math.dist((actor[0], actor[2]), (P1_PLAYER[0], P1_PLAYER[2])) if actor is not None else None
    actor_y_error = abs(actor[1] - grounded[1]) if actor is not None and grounded is not None else None
    projection_ok = contract.get("camera_projection_mode") == 0
    yaw_error = _angle_error(contract.get("camera_yaw"), P1_YAW)
    pitch = _number(contract.get("camera_pitch"))
    pitch_error = abs(pitch - P1_PITCH) if pitch is not None else None
    result["metadata_pose"] = bool(
        camera_error is not None and camera_error <= POSE_EPSILON
        and actor_xz_error is not None and actor_xz_error <= POSE_EPSILON
        and actor_y_error is not None and actor_y_error <= POSE_EPSILON
        and projection_ok and yaw_error is not None and yaw_error <= POSE_EPSILON
        and pitch_error is not None and pitch_error <= POSE_EPSILON
        and contract.get("actor_presentation_camera_contract_valid") is True
    )
    _record_failure(failures, result["metadata_pose"], f"{prefix}.metadata_pose:p1_contract_mismatch")
    if isinstance(runtime_metadata_contract, Mapping):
        runtime_pose_fields = (
            "scene_camera_origin",
            "actor_presentation_reference_world",
            "camera_projection_mode",
            "camera_yaw",
            "camera_pitch",
        )
        runtime_pose_match = all(runtime_metadata_contract.get(field) == contract.get(field) for field in runtime_pose_fields)
        result["runtime_metadata_pose_match"] = runtime_pose_match
        _record_failure(failures, runtime_pose_match, f"{prefix}.runtime_truth_snapshot.metadata:pose_contract_mismatch")
    result["metadata_pose_errors"] = {
        "camera": camera_error,
        "actor_xz": actor_xz_error,
        "actor_y": actor_y_error,
        "yaw": yaw_error,
        "pitch": pitch_error,
    }
    if isinstance(runtime_truth, Mapping) and "frame_time_seconds" in contract:
        metadata_time = _number(contract.get("frame_time_seconds"))
        product_time = _number(runtime_truth.get("frame_time_seconds"))
        time_match = metadata_time is not None and product_time is not None and abs(metadata_time - product_time) <= 0.001
        result["metadata_frame_time"] = time_match
        _record_failure(failures, time_match, f"{prefix}.metadata.frame_time_seconds:runtime_truth_mismatch")
    return result


def _spatial_summary(contacts: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> tuple[dict[str, Any], list[str]]:
    summary: dict[str, Any] = {}
    failures: list[str] = []
    for source_class in sorted(FL4216_P1_REQUIRED_SOURCE_CLASSES):
        rows = contacts.get(source_class, {})
        positions = [item["screen"] for item in rows.values()]
        breaks = [item for item in rows.values() if item["break"]]
        non_breaks = [item for item in rows.values() if not item["break"]]
        state_set = {_identity(item["state"]) for item in rows.values()}
        break_positions = {item["screen"] for item in breaks}
        non_break_positions = {item["screen"] for item in non_breaks}
        x_span = max((item[0] for item in positions), default=0.0) - min((item[0] for item in positions), default=0.0)
        y_span = max((item[1] for item in positions), default=0.0) - min((item[1] for item in positions), default=0.0)
        mixed = bool(breaks and non_breaks)
        spatially_distributed = len(break_positions) >= 2 and len(non_break_positions) >= 2 and (x_span > 0.0 or y_span > 0.0)
        class_summary = {
            "contact_count": len(rows),
            "break_count": len(breaks),
            "break_coverage_pct": 100.0 * len(breaks) / len(rows) if rows else None,
            "distinct_state_count": len(state_set),
            "distinct_break_positions": len(break_positions),
            "distinct_non_break_positions": len(non_break_positions),
            "screen_span": [x_span, y_span],
            "mixed_break_coverage": mixed,
            "spatially_distributed": spatially_distributed,
            "pass": len(rows) >= MIN_CONTACTS_PER_CLASS and mixed and spatially_distributed and len(state_set) >= 2,
        }
        summary[source_class] = class_summary
        if not class_summary["pass"]:
            failures.append(f"{source_class}:absent_solid_or_spatially_homogeneous_break_band")
    return summary, failures


def _temporal_summary(per_frame: list[dict[str, dict[str, dict[str, Any]]]], failures: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {"pairs": [], "source_classes": {}}
    for source_class in sorted(FL4216_P1_REQUIRED_SOURCE_CLASSES):
        class_pairs: list[dict[str, Any]] = []
        aggregate_changed = 0
        aggregate_matched = 0
        lockstep_pairs = 0
        for frame_index in range(1, len(per_frame)):
            left = per_frame[frame_index - 1].get(source_class, {})
            right = per_frame[frame_index].get(source_class, {})
            matched_ids = set(left) & set(right)
            transitions = [
                (left[item]["state"], right[item]["state"])
                for item in sorted(matched_ids)
                if left[item]["state"] != right[item]["state"]
            ]
            changed = len(transitions)
            denominator = max(len(left), len(right))
            ratio = len(matched_ids) / denominator if denominator else 0.0
            all_changed = bool(matched_ids) and changed == len(matched_ids)
            binary_transitions = {
                (before[0] is not None, after[0] is not None)
                for before, after in transitions
            }
            full_transitions = {_identity(transition) for transition in transitions}
            # A healthy shoreline has spatially staggered progression.  If
            # every matched contact changes on the same cadence edge, the
            # evidence is a global blink/lockstep update rather than local
            # shoreline animation, even when opposite cells toggle in opposite
            # directions.
            lockstep = all_changed
            pair = {
                "frame_a": frame_index - 1,
                "frame_b": frame_index,
                "contact_count_a": len(left),
                "contact_count_b": len(right),
                "matched_count": len(matched_ids),
                "match_ratio": ratio,
                "changed_count": changed,
                "changed_pct": 100.0 * changed / len(matched_ids) if matched_ids else None,
                "lockstep": lockstep,
            }
            class_pairs.append(pair)
            aggregate_changed += changed
            aggregate_matched += len(matched_ids)
            lockstep_pairs += int(lockstep)
            result["pairs"].append({"source_class": source_class, **pair})
        class_result = {
            "pairs": class_pairs,
            "comparable_count": aggregate_matched,
            "changed_count": aggregate_changed,
            "changed_pct": 100.0 * aggregate_changed / aggregate_matched if aggregate_matched else None,
            "lockstep_pair_count": lockstep_pairs,
            "pass": bool(
                class_pairs
                and aggregate_matched > 0
                and aggregate_changed > 0
                and all(pair["match_ratio"] >= MIN_MATCH_RATIO for pair in class_pairs)
                and lockstep_pairs == 0
            ),
        }
        result["source_classes"][source_class] = class_result
        if not class_result["pass"]:
            if aggregate_matched == 0:
                failures.append(f"{source_class}:no_comparable_temporal_contact_identity")
            elif aggregate_changed == 0:
                failures.append(f"{source_class}:temporally_static_break_coverage")
            elif lockstep_pairs:
                failures.append(f"{source_class}:global_lockstep_break_transition")
            else:
                failures.append(f"{source_class}:temporal_identity_coverage_below_{MIN_MATCH_RATIO:.2f}")
    return result


def evaluate_receipt(receipt: Mapping[str, Any], *, artifact_root: Path = ROOT) -> dict[str, Any]:
    """Evaluate one canonical FL-4216 receipt without changing any artifacts."""
    failures: list[str] = []
    if not isinstance(receipt, Mapping):
        return {
            "schema": SCHEMA,
            "feature_id": FEATURE_ID,
            "pass": False,
            "reason": "receipt:not_object",
            "failures": ["receipt:not_object"],
        }
    identity_checks = _check_identity(receipt, failures)
    frames = receipt.get("fl4216_temporal_frames")
    frames_ok = isinstance(frames, list) and len(frames) >= 2
    _record_failure(failures, frames_ok, "fl4216_temporal_frames:at_least_two_required")
    declared_count = _number(receipt.get("fl4216_temporal_capture_count"))
    if declared_count is None:
        failures.append("fl4216_temporal_capture_count:missing")
    else:
        count_matches_frames = bool(
            isinstance(frames, list)
            and declared_count.is_integer()
            and int(declared_count) == len(frames)
        )
        _record_failure(
            failures,
            count_matches_frames,
            "fl4216_temporal_capture_count:does_not_match_frame_list",
        )
    interval = _number(receipt.get("fl4216_temporal_interval_seconds"))
    interval_ok = interval is not None and FL4216_TEMPORAL_PRODUCT_DELTA_MIN_SECONDS <= interval <= FL4216_TEMPORAL_PRODUCT_DELTA_MAX_SECONDS
    _record_failure(failures, interval_ok, "fl4216_temporal_interval_seconds:outside_product_window")
    frame_results: list[dict[str, Any]] = []
    frame_contacts: list[dict[str, dict[str, dict[str, Any]]]] = []
    frame_diagnostics: list[dict[str, Any]] = []
    if isinstance(frames, list):
        for index, frame in enumerate(frames):
            if not isinstance(frame, Mapping):
                failures.append(f"frames[{index}]:not_object")
                frame_results.append({"pass": False})
                frame_contacts.append({source_class: {} for source_class in FL4216_TEMPORAL_SOURCE_CLASSES})
                frame_diagnostics.append({"readback_state": "frame:not_object"})
                continue
            dump = _normalize_dump(frame.get("final_render_cell_dump"), artifact_root)
            dump_ok = dump.get("ok") is True and isinstance(dump.get("artifact_paths"), Mapping)
            _record_failure(failures, dump_ok, f"frames[{index}].final_render_cell_dump:canonical_artifact_required")
            paths = dump.get("artifact_paths", {})
            required_paths = ("final_png", "index_html", "metadata_json", "cells_jsonl")
            if dump.get("capture_schema") == "fl4216_temporal_snapshot.v1":
                required_paths = ("final_png", "metadata_json", "temporal_snapshot_json")
            for key in required_paths:
                present = isinstance(paths, Mapping) and bool(paths.get(key)) and Path(str(paths.get(key))).is_file()
                _record_failure(failures, present, f"frames[{index}].artifact_paths.{key}:file_required")
            result = _check_frame_metadata(frame, index, dump, receipt, failures)
            contacts, diagnostics = _frame_contacts(dump)
            if diagnostics.get("json_parse_errors", 0) or diagnostics.get("readback_state") in {"missing_cells_jsonl", "unreadable_cells_jsonl", "final_cell_paint_magic_mismatch"}:
                failures.append(f"frames[{index}].final_paint_contact_rows:unreadable_or_invalid")
            if diagnostics.get("missing_identity_rows", 0):
                failures.append(f"frames[{index}].final_paint_contact_rows:missing_identity")
            result["contact_rows"] = diagnostics
            result["pass"] = not any(reason.startswith(f"frames[{index}].") for reason in failures)
            frame_results.append(result)
            frame_contacts.append(contacts)
            frame_diagnostics.append(diagnostics)
    capture_ticks = [
        _number(frame.get("capture_started_ticks_usec")) if isinstance(frame, Mapping) else None
        for frame in frames
    ] if isinstance(frames, list) else []
    capture_ticks_ok = len(capture_ticks) >= 2 and all(
        capture_ticks[index] is not None
        and capture_ticks[index - 1] is not None
        and capture_ticks[index] > capture_ticks[index - 1]
        for index in range(1, len(capture_ticks))
    )
    _record_failure(failures, capture_ticks_ok, "capture_started_ticks_usec:strictly_increasing_required")
    request_ids = [
        _number(frame.get("temporal_snapshot_request_id")) if isinstance(frame, Mapping) else None
        for frame in frames
    ] if isinstance(frames, list) else []
    request_ids_ok = len(request_ids) >= 2 and all(item is not None and item > 0 for item in request_ids) and len(set(request_ids)) == len(request_ids)
    _record_failure(failures, request_ids_ok, "temporal_snapshot_request_id:unique_positive_ids_required")
    times: list[float | None] = []
    if isinstance(frames, list):
        for frame in frames:
            runtime_truth = frame.get("runtime_truth_snapshot") if isinstance(frame, Mapping) else None
            times.append(_number(runtime_truth.get("frame_time_seconds")) if isinstance(runtime_truth, Mapping) else None)
    deltas = [times[index] - times[index - 1] for index in range(1, len(times)) if times[index] is not None and times[index - 1] is not None]
    timing_ok = len(times) >= 2 and len(deltas) == len(times) - 1 and all(
        FL4216_TEMPORAL_PRODUCT_DELTA_MIN_SECONDS <= delta <= FL4216_TEMPORAL_PRODUCT_DELTA_MAX_SECONDS for delta in deltas
    )
    _record_failure(failures, timing_ok, "runtime_truth_snapshot:time_separated_product_frames_required")
    spatial: dict[str, Any] = {}
    if frame_contacts:
        for index, contacts in enumerate(frame_contacts):
            frame_spatial, spatial_failures = _spatial_summary(contacts)
            spatial[str(index)] = frame_spatial
            failures.extend(f"frames[{index}].{reason}" for reason in spatial_failures)
    temporal = _temporal_summary(frame_contacts, failures) if len(frame_contacts) >= 2 else {"pairs": [], "source_classes": {}}
    # Deduplicate while retaining first-failure order for stable CI receipts.
    failures = list(dict.fromkeys(failures))
    passed = not failures
    return {
        "schema": SCHEMA,
        "feature_id": FEATURE_ID,
        "fl": FL_ID,
        "pass": passed,
        "reason": "shoreline_break_behavior_ok" if passed else failures[0],
        "failures": failures,
        "checks": {
            "identity": identity_checks,
            "frame_count": len(frames) if isinstance(frames, list) else None,
            "declared_frame_count": int(declared_count) if declared_count is not None else None,
            "interval_seconds": interval,
            "product_frame_time_seconds": times,
            "product_frame_delta_seconds": deltas,
            "capture_started_ticks_usec": capture_ticks,
            "temporal_snapshot_request_ids": request_ids,
            "timing": timing_ok,
            "frames": frame_results,
            "frame_diagnostics": frame_diagnostics,
        },
        "spatial": spatial,
        "temporal": temporal,
        "accepted_source_classes": sorted(FL4216_P1_REQUIRED_SOURCE_CLASSES),
        "break_glyph_ids": sorted(FL4216_SHORELINE_BREAK_GLYPH_ID_SET),
        "constraints": {
            "exact_pose": {
                "player": list(P1_PLAYER),
                "camera": list(P1_CAMERA),
                "yaw": P1_YAW,
                "pitch": P1_PITCH,
                "projection": EXPECTED_PROJECTION,
                "epsilon": POSE_EPSILON,
            },
            "minimum_contacts_per_source_class": MIN_CONTACTS_PER_CLASS,
            "minimum_temporal_match_ratio": MIN_MATCH_RATIO,
            "product_delta_window_seconds": [
                FL4216_TEMPORAL_PRODUCT_DELTA_MIN_SECONDS,
                FL4216_TEMPORAL_PRODUCT_DELTA_MAX_SECONDS,
            ],
        },
    }


def check_receipt(path: Path) -> dict[str, Any]:
    """Read and check a JSON receipt, resolving relative artifact paths safely."""
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return {"schema": SCHEMA, "feature_id": FEATURE_ID, "pass": False, "reason": "receipt:file_missing", "failures": ["receipt:file_missing"]}
    except json.JSONDecodeError:
        return {"schema": SCHEMA, "feature_id": FEATURE_ID, "pass": False, "reason": "receipt:invalid_json", "failures": ["receipt:invalid_json"]}
    result = evaluate_receipt(receipt, artifact_root=path.parent)
    result["receipt_path"] = str(path)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path, help="canonical FL-4216 continuous-run receipt JSON")
    parser.add_argument("--output", type=Path, help="optional machine-readable result JSON")
    args = parser.parse_args(argv)
    result = check_receipt(args.receipt)
    serialized = json.dumps(result, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if result.get("pass") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
