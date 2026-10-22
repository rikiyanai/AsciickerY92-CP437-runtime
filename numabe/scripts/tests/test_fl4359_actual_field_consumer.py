"""Focused contract tests for the FL-4359 native final-paint consumer."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fl4359_actual_field import (  # noqa: E402
    ACTUAL_FIELD_SCHEMA,
    ActualFieldError,
    ActualFieldReader,
    actual_field_absent_status,
    _validate_policy,
)
from fl4359_raster_jitter_capture import (  # noqa: E402
    RENDER_FAMILIES,
    _mode_postfix_from_frame_records,
    compose_actual_family,
)


SHA = "0123456789abcdef" * 4
CELL_PX = [3, 2]


def _applied_policy(bg_sampling: str, fg_sampling: str) -> dict:
    return {
        "status": "applied",
        "mode": "FF",
        "bg_sampling": bg_sampling,
        "fg_sampling": fg_sampling,
    }


@pytest.mark.parametrize("mode", ["FF", "FC", "CF", "CC"])
def test_projected_bg_policy_records_source_domain(mode):
    policy = {
        "status": "applied", "mode": mode, "family": "all",
        "fg_sampling": "flat" if mode[0] == "F" else "continuous",
        "bg_sampling": "flat" if mode[1] == "F" else "continuous",
        "projection": "perspective", "bg_domain": "projected_texel",
        "bg_transport": "depth_winning_splat", "bg_owner": "family",
        "owner": "synthetic_test_only", "sha256": SHA,
    }
    validated, eligible = _validate_policy(policy)
    assert eligible and validated == policy
    with pytest.raises(ActualFieldError, match="BG domain"):
        _validate_policy({**policy, "bg_domain": "screen_cell"})
    with pytest.raises(ActualFieldError, match="BG transport"):
        _validate_policy({**policy, "bg_transport": "product"})


def test_bg_only_keeps_a_boundary_inside_one_character_cell(tmp_path):
    frame = ActualFieldReader(tmp_path / "capture.jsonl").read(
        _trace(tmp_path), frame_id=0, sequence_index=0)
    actual = frame.as_dict()
    actual["bg_rgb"][:] = [0.0, 0.0, 1.0]
    actual["bg_rgb"][:, :1] = [1.0, 0.0, 0.0]
    before, _ = compose_actual_family(actual, "bg_only", None, (3, 2))
    actual["bg_rgb"][:, 1:2] = [1.0, 0.0, 0.0]
    after, _ = compose_actual_family(actual, "bg_only", None, (3, 2))
    assert np.array_equal(before[:, 0], after[:, 0])
    assert np.array_equal(before[:, 2:], after[:, 2:])
    assert np.all(np.any(before[:, 1] != after[:, 1], axis=-1))


@pytest.mark.parametrize("bg,fg,expected", [
    ("flat", "flat", "FLAT_BG_FLAT_FG"),
    ("continuous", "flat", "CONTINUOUS_BG_FLAT_FG"),
    ("flat", "continuous", "FLAT_BG_CONTINUOUS_FG"),
    ("continuous", "continuous", "CONTINUOUS_BG_CONTINUOUS_FG"),
])
def test_mode_postfix_uses_consistent_applied_sampling_policy(
    bg: str, fg: str, expected: str,
) -> None:
    records = [
        {"actual_field": {"applied_policy": _applied_policy(bg, fg)}}
        for _ in range(2)
    ]
    assert _mode_postfix_from_frame_records(records) == expected


def test_mode_postfix_rejects_mixed_applied_sampling_policy() -> None:
    records = [
        {"actual_field": {"applied_policy": _applied_policy("flat", "flat")}},
        {"actual_field": {"applied_policy": _applied_policy("continuous", "flat")}},
    ]
    with pytest.raises(ActualFieldError, match="consistent applied FG/BG"):
        _mode_postfix_from_frame_records(records)


def test_mode_postfix_rejects_invalid_applied_sampling_policy() -> None:
    records = [
        {"actual_field": {"applied_policy": _applied_policy("flat", "flat")}},
        {"actual_field": {"applied_policy": _applied_policy("invalid", "flat")}},
    ]
    with pytest.raises(ActualFieldError, match="consistent applied FG/BG"):
        _mode_postfix_from_frame_records(records)


def test_mode_postfix_missing_applied_policy_keeps_legacy_name() -> None:
    records = [{"actual_field": {"applied_policy": {"status": "unavailable"}}}]
    assert _mode_postfix_from_frame_records(records) is None


def _records(width: int = 5, height: int = 3) -> np.ndarray:
    records = np.zeros((height, width, 12), dtype="<f4")
    for y in range(height):
        for x in range(width):
            records[y, x, 0:3] = [(x + 1) / 10, (y + 1) / 10, 0.2]
            records[y, x, 3] = (x + y + 1) / 10
            records[y, x, 4:7] = [0.05 + x / 20, 0.3, (y + 1) / 20]
            records[y, x, 7] = 0.25 + ((x + y) % 3) / 4
            records[y, x, 8:11] = (
                records[y, x, 4:7] * (1 - records[y, x, 7])
                + records[y, x, 0:3] * records[y, x, 7])
            records[y, x, 11] = 0.0
    return records


def _metadata(path: Path, *, frame_id: int = 0, actual_frame_index: int = 100,
              sequence_frame: int = 0, policy: object | None = None,
              residual: str = "none", source: object | None = None,
              atlas: object | None = None) -> dict:
    raw = path.read_bytes()
    return {
        "status": "valid",
        "schema": ACTUAL_FIELD_SCHEMA,
        "raw_path": path.name,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_order": "little_endian",
        "scalar_format": "float32",
        "bytes": len(raw), "record_bytes": 48, "record_count": 15,
        "actual_image_size": [5, 3],
        "logical_target_size": [6, 4],
        "presentation_size": [5, 3],
        "cell_size": CELL_PX, "semantic_ansi_size": [2, 2],
        "record_layout": ["fg_rgb", "native_intensity", "bg_rgb", "effective_coverage", "final_rgb", "actor_alpha"],
        "capture_id": "driver-capture/path#pid=42",
        "frame_index": actual_frame_index,
        "sequence_frame": sequence_frame, "request_id": 77,
        "projection_kind": 1, "projection_mode": 2, "camera_projection_mode": 0,
        "capture_boundary": "pre_imageStore_after_compositor_actor_blend",
        "post_imageStore_display_observed": False,
        "late_overlay_observed": residual != "excluded",
        "applied_policy": {"status": "unavailable"} if policy is None else policy,
        "provenance": {
            "paint_owner": "ascii_compositor_same_dispatch",
            "cell_source_join": "fl4431_final_cell_capture_same_frame_request",
            "atlas_sources": "unavailable_without_runtime_atlas_owner",
            "source": source if source is not None else "unavailable",
            "atlas": atlas if atlas is not None else "unavailable",
        },
    }



def _trace(tmp_path: Path, *, frame_id: int = 0, actual_frame_index: int = 100,
           sequence_frame: int = 0, residual: str = "none",
           policy: object | None = None,
           **field_overrides: object) -> dict:
    sidecar = tmp_path / "field-0.bin"
    _records().tofile(sidecar)
    field = _metadata(
        sidecar,
        frame_id=frame_id,
        actual_frame_index=actual_frame_index,
        sequence_frame=sequence_frame,
        policy=policy,
        residual=residual,
    )
    for key in ("source", "atlas"):
        if key in field_overrides:
            field["provenance"][key] = field_overrides.pop(key)
    field.update(field_overrides)
    return {
        "frame_id": frame_id,
        "frame_index": actual_frame_index,
        "sequence_frame": sequence_frame,
        "request_id": 77,
        "final_paint_field": field,
    }


def test_reads_lossless_fractional_field_and_engine_frame_identity(tmp_path: Path) -> None:
    trace = _trace(tmp_path)
    frame = ActualFieldReader(tmp_path / "capture.jsonl").read(
        trace, frame_id=0, sequence_index=0)
    assert frame.field_size_px == (5, 3)
    assert frame.logical_target_size == (6, 4)
    assert frame.fields["fg_rgb"].shape == (3, 5, 3)
    assert frame.fields["effective_coverage"][0, 1] == pytest.approx(0.5)
    assert frame.fields["native_intensity"][0, 1] == pytest.approx(0.2)
    assert frame.actual_field_observation is True
    assert frame.presentation_mode_eligible is False


def test_actual_views_preserve_within_cell_fg_and_bg_variation(tmp_path: Path) -> None:
    trace = _trace(tmp_path)
    frame = ActualFieldReader(tmp_path / "capture.jsonl").read(
        trace, frame_id=0, sequence_index=0)
    actual = frame.as_dict()
    actual["cell_glyph_id"] = np.zeros((2, 2), dtype=np.int32)
    for family in RENDER_FAMILIES:
        image, stats = compose_actual_family(
            actual, family, None, (3, 2))
        assert image.shape == (3, 5, 3)
        assert stats["field_source"] == ACTUAL_FIELD_SCHEMA
    bg, _ = compose_actual_family(actual, "bg_only", None, (3, 2))
    assert np.any(bg[0, 0] != bg[0, 1])
    fg, _ = compose_actual_family(actual, "fg_only", None, (3, 2))
    assert not np.array_equal(fg[0, 0], fg[0, 1])


def test_unavailable_policy_is_observation_but_not_mode_eligible(tmp_path: Path) -> None:
    available_source = {
        "status": "available",
        "build_id": "build-1",
        "dirty_diff_sha256": SHA,
    }
    available_atlas = {
        "status": "available",
        "sources": [{"path": "font.png", "sha256": SHA}],
    }
    trace = _trace(
        tmp_path,
        source=available_source,
        atlas=available_atlas,
    )
    frame = ActualFieldReader(tmp_path / "capture.jsonl").read(
        trace, frame_id=0, sequence_index=0)
    assert frame.actual_field_observation is True
    assert frame.source_eligible is True
    assert frame.atlas_eligible is True
    assert frame.presentation_mode_eligible is False


def test_applied_mode_and_glyph_transition_use_actual_channels(tmp_path: Path) -> None:
    policy = {
        "status": "applied",
        "family": "water",
        "mode": "CC",
        "owner": "synthetic_test_only",
        "bg_owner": "water",
        "sha256": SHA,
        "fg_sampling": "continuous",
        "bg_sampling": "continuous",
    }
    source = {
        "status": "available",
        "build_id": "build-1",
        "dirty_diff_sha256": SHA,
    }
    atlas = {"status": "available", "sources": [{"path": "font.png", "sha256": SHA}]}
    trace = _trace(tmp_path, policy=policy, source=source, atlas=atlas)
    frame = ActualFieldReader(tmp_path / "capture.jsonl").read(
        trace, frame_id=0, sequence_index=0)
    assert frame.presentation_mode_eligible is True
    actual = frame.as_dict()
    actual["cell_glyph_id"] = np.array([[1, 2], [3, 4]], dtype=np.int32)
    previous = {key: value.copy() if isinstance(value, np.ndarray) else value
               for key, value in actual.items()}
    previous["cell_glyph_id"] = np.array([[1, 9], [3, 4]], dtype=np.int32)
    previous["native_intensity"][0, 3] = 0.0
    _, stats = compose_actual_family(
        actual, "fg_glyph_change_highlight", previous, (3, 2))
    assert stats["cells_with_glyph_change"] == 1
    assert stats["pixels_with_mask_delta"] > 0


@pytest.mark.parametrize("mutation", ["truncate", "extra", "hash", "capture_hash"])
def test_rejects_corrupt_or_circular_sidecar_metadata(tmp_path: Path, mutation: str) -> None:
    trace = _trace(tmp_path)
    path = tmp_path / "field-0.bin"
    if mutation == "truncate":
        path.write_bytes(path.read_bytes()[:-1])
    elif mutation == "extra":
        path.write_bytes(path.read_bytes() + b"x")
    elif mutation == "hash":
        trace["final_paint_field"]["raw_sha256"] = SHA
    else:
        trace["final_paint_field"]["capture_sha256"] = SHA
    with pytest.raises(ActualFieldError):
        ActualFieldReader(tmp_path / "capture.jsonl").read(
            trace, frame_id=0, sequence_index=0)


def test_engine_index_is_not_sequence_ordinal(tmp_path: Path) -> None:
    trace = _trace(tmp_path, actual_frame_index=100)
    trace["frame_index"] = 101
    with pytest.raises(ActualFieldError, match="frame_trace.frame_index"):
        ActualFieldReader(tmp_path / "capture.jsonl").read(
            trace, frame_id=0, sequence_index=1)


@pytest.mark.parametrize("bad_value", [float("nan"), 1.25])
def test_rejects_reencoded_nonfinite_or_out_of_range_coverage(
    tmp_path: Path, bad_value: float
) -> None:
    trace = _trace(tmp_path)
    records = _records()
    records[0, 0, 7] = bad_value
    sidecar = tmp_path / "field-0.bin"
    records.tofile(sidecar)
    trace["final_paint_field"]["raw_sha256"] = hashlib.sha256(
        sidecar.read_bytes()).hexdigest()
    with pytest.raises(ActualFieldError, match="effective_coverage"):
        ActualFieldReader(tmp_path / "capture.jsonl").read(
            trace, frame_id=0, sequence_index=0)


def test_rejects_logical_grid_change_between_frames(tmp_path: Path) -> None:
    first = _trace(tmp_path)
    reader = ActualFieldReader(tmp_path / "capture.jsonl")
    reader.read(first, frame_id=0, sequence_index=0)
    second_path = tmp_path / "field-1.bin"
    _records().tofile(second_path)
    second_field = _metadata(
        second_path, frame_id=11, actual_frame_index=101, sequence_frame=11)
    second_field["logical_target_size"] = [7, 4]
    second_field["semantic_ansi_size"] = [3, 2]
    second = {
        "frame_id": 11,
        "frame_index": 101,
        "sequence_frame": 11,
        "request_id": 77,
        "final_paint_field": second_field,
    }
    with pytest.raises(ActualFieldError, match="logical_target_size"):
        reader.read(second, frame_id=11, sequence_index=1)


@pytest.mark.parametrize("key,value", [
    ("actual_image_size", [5.5, 3]),
    ("cell_size", [True, 2]),
    ("semantic_ansi_size", [-1, 0]),
])
def test_rejects_invalid_dimensions_but_allows_zero_origin(
    tmp_path: Path, key: str, value: object
) -> None:
    trace = _trace(tmp_path)
    trace["final_paint_field"][key] = value
    with pytest.raises(ActualFieldError):
        ActualFieldReader(tmp_path / "capture.jsonl").read(
            trace, frame_id=0, sequence_index=0)


def test_excluded_overlay_residual_invalidates_comparison_without_backfill(
    tmp_path: Path,
) -> None:
    trace = _trace(tmp_path, residual="excluded")
    frame = ActualFieldReader(tmp_path / "capture.jsonl").read(
        trace, frame_id=0, sequence_index=0)
    assert frame.actual_field_observation is True
    assert frame.composite_eligible is False
    assert frame.as_dict()["final_rgb"][0, 0, 0] != 0.0


def test_legacy_status_is_explicitly_diagnostic() -> None:
    status = actual_field_absent_status({})
    assert status["schema"] == ACTUAL_FIELD_SCHEMA
    assert status["actual_field_observation"] is False
    assert status["legacy_pair_diagnostic_only"] is True


@pytest.mark.parametrize("key,value", [
    ("status", "invalid"), ("record_count", 16),
    ("byte_order", "big_endian"), ("scalar_format", "float16"),
    ("projection_mode", 1), ("camera_projection_mode", 1),
    ("sequence_frame", 100), ("request_id", 78),
    ("post_imageStore_display_observed", True),
])
def test_rejects_wrong_producer_join_or_encoding(tmp_path, key, value):
    trace = _trace(tmp_path)
    trace["final_paint_field"][key] = value
    with pytest.raises(ActualFieldError):
        ActualFieldReader(tmp_path / "capture.jsonl").read(trace, frame_id=0, sequence_index=0)


def test_capture_sequence_is_not_consumer_ordinal(tmp_path):
    trace = _trace(tmp_path, frame_id=10, sequence_frame=10, actual_frame_index=110)
    frame = ActualFieldReader(tmp_path / "capture.jsonl").read(trace, frame_id=10, sequence_index=0)
    assert frame.actual_frame_index == 110
    assert frame.sequence_frame == 10


def test_recorded_profile_requires_actual_grid_and_atlas_identity(tmp_path):
    import json
    from copy import deepcopy
    atlas_identity = {"schema": "fl4359.native_atlas_identity.v1", "sources": [
        {"path": "res://synthetic_test.png", "hash_kind": "decoded_rgba8",
         "dimensions": {"w": 2, "h": 2}, "sha256": SHA}]}
    trace = _trace(tmp_path, atlas_identity=atlas_identity)
    trace.update(camera_projection_mode=0, projection_mode=2)
    spec = {"schema": "fl4359.presentation_profile.v1", "profile_id": "synthetic_test",
            "projection": "perspective", "native_cell_size": [3, 2],
            "presentation_size": [5, 3], "ansi_size": [2, 2],
            "atlas_identity": atlas_identity}
    receipt_path = tmp_path / "receipt.json"
    def write_spec(value):
        receipt_path.write_text(json.dumps({"runner_presentation_profile_id": "synthetic_test",
                                           "runner_presentation_profile_spec": value}))
    write_spec(spec)
    assert ActualFieldReader(tmp_path / "capture.jsonl").read(
        trace, frame_id=0, sequence_index=0).profile == "synthetic_test"
    for field in ("width", "atlas"):
        broken = deepcopy(spec)
        if field == "width":
            broken["native_cell_size"][0] = 4
        else:
            broken["atlas_identity"]["sources"][0]["sha256"] = "f" * 64
        write_spec(broken)
        with pytest.raises(ActualFieldError, match="recorded presentation profile mismatch"):
            ActualFieldReader(tmp_path / "capture.jsonl").read(trace, frame_id=0, sequence_index=0)
