"""Offline transport fixtures for exact front/underlay generation provenance."""
from __future__ import annotations

import hashlib
import sys
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path("scripts").resolve()))

import fl4359_actual_field as field_mod


def _frame(front, underlay, maps):
    def refs(value):
        if np.isscalar(value):
            return np.full((1, 1, 4), value, dtype="<u4")
        array = np.asarray(value, dtype="<u4")
        assert array.shape == (4,)
        return array.reshape((1, 1, 4))

    fields = {
        "fg_rgb": np.zeros((1, 1, 3), dtype="<f4"),
        "native_intensity": np.zeros((1, 1), dtype="<f4"),
        "bg_rgb": np.zeros((1, 1, 3), dtype="<f4"),
        "effective_coverage": np.ones((1, 1), dtype="<f4"),
        "final_rgb": np.zeros((1, 1, 3), dtype="<f4"),
        "actor_alpha": np.zeros((1, 1), dtype="<f4"),
        "front_sources": refs(front),
        "under_sources": refs(underlay),
        "composition": np.zeros((1, 1, 4), dtype="<f4"),
    }
    return field_mod.ActualFieldFrame(
        path=Path("fixture.bin"),
        metadata={"capture_boundary": "pre_imageStore_after_compositor_actor_blend",
                  "source_capture_state": {"probe_size": 4,
                      "capture_bands": [0, 1, 2],
                      "captured_publication_sha256_by_layer": [{}] * 18}},
        fields=fields, source_generation_maps=maps,
        field_size_px=(1, 1), logical_target_size=(1, 1),
        presentation_size=(1, 1), cell_px=(1, 1), grid_cells=(1, 1),
        capture_id="fixture", actual_frame_index=0, sequence_frame=0,
        frame_id=0, request_id=1, profile="fixture", projection="perspective",
        policy={"status": "unavailable"}, source_eligible=False,
        atlas_eligible=False, composite_eligible=True)


def test_front_and_underlay_banks_preserve_same_address_with_typed_sky():
    # Low 28 bits point at physical layer 7, texel (1,2). Both channels use the
    # same address while their effective sources and typed generations differ.
    reference = (1 << 28) | (7 * 16 + 2 * 4 + 1 + 1)
    front = np.zeros((18, 4, 4), dtype="<u4")
    underlay = np.zeros_like(front)
    front[7, 2, 1] = 1
    underlay[7, 2, 1] = 0x80000000 | 4
    frame = _frame(reference, reference, {"front": front, "underlay": underlay})
    result = frame.pixel_provenance(0, 0)
    assert result["front"]["foreground"]["capture_generation"] == 1
    assert result["front"]["foreground"]["capture_generation_kind"] == "captured_geometry"
    assert result["underlay"]["foreground"]["capture_generation"] == 4
    assert result["underlay"]["foreground"]["capture_generation_kind"] == "generated_sky"
    assert result["underlay"]["foreground"]["geometry_capture"] is False


def test_zero_source_generation_fails_closed():
    reference = (1 << 28) | (7 * 16 + 2 * 4 + 1 + 1)
    front = np.zeros((18, 4, 4), dtype="<u4")
    underlay = np.zeros_like(front)
    frame = _frame(reference, reference, {"front": front, "underlay": underlay})
    with pytest.raises(field_mod.ActualFieldError, match="exact captured generation"):
        frame.pixel_provenance(0, 0)


def test_retained_static_and_fresh_dynamic_keep_exact_source_revisions():
    def reference(layer, x, y):
        return (1 << 28) | (layer * 16 + y * 4 + x + 1)

    retained_static = reference(7, 1, 2)
    fresh_dynamic = reference(8, 2, 1)
    front = np.zeros((18, 4, 4), dtype="<u4")
    underlay = np.zeros_like(front)
    front[7, 2, 1] = 1       # retained geometry from the old capture
    front[8, 1, 2] = 11      # freshly captured dynamic geometry
    underlay[7, 2, 1] = 2
    underlay[8, 1, 2] = 12
    frame = _frame(
        [retained_static, fresh_dynamic, retained_static, fresh_dynamic],
        [retained_static, fresh_dynamic, retained_static, fresh_dynamic],
        {"front": front, "underlay": underlay},
    )
    provenance = frame.pixel_provenance(0, 0)
    assert provenance["front"]["glyph"]["capture_generation"] == 1
    assert provenance["front"]["foreground"]["capture_generation"] == 11
    assert provenance["underlay"]["glyph"]["capture_generation"] == 2
    assert provenance["underlay"]["foreground"]["capture_generation"] == 12
    assert provenance["front"]["glyph"]["capture_generation_kind"] == "captured_geometry"
    assert provenance["front"]["foreground"]["geometry_capture"] is True


def test_sidecar_descriptor_is_two_bank_and_hashable(tmp_path):
    raw = np.zeros((18, 4, 4), dtype="<u4")
    paths = {}
    for bank in ("front", "underlay"):
        path = tmp_path / f"{bank}.u32"
        raw.tofile(path)
        paths[bank] = {"status": "valid", "schema": field_mod.SOURCE_GENERATION_SCHEMA,
                       "bank": bank, "raw_path": path.name,
                       "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "bytes": path.stat().st_size, "probe_size": 4, "layer_count": 18,
                       "word_format": "little_endian_u32",
                       "encoding": {"zero": "missing",
                                    "low31": "captured_geometry_source_revision_w",
                                    "high_bit": "generated_sky_paint_generation"}}
    metadata = {"source_capture_generation_sidecars": {
        "schema": field_mod.SOURCE_GENERATION_SCHEMA, "probe_size": 4,
        "layer_count": 18, "capture_frame_index": 0,
        "capture_request_id": 1, "capture_sequence_frame": 0,
        "snapshot_frame_index": 0,
        "snapshot_frame_join_valid": True,
        "snapshot_timeline": "same_frame_post_image_store_before_next_frame_async_transfer",
        "capture_bands": [0, 1, 2],
        "front": paths["front"], "underlay": paths["underlay"],
        "frame_index": 0, "request_id": 1, "sequence_frame": 0},
        "source_capture_state": {"probe_size": 4, "capture_bands": [0, 1, 2]}}
    metadata.update({"frame_index": 0, "request_id": 1, "sequence_frame": 0})
    maps = field_mod._load_source_generation_sidecars(metadata, tmp_path / "trace.jsonl", required=True)
    assert maps["front"].shape == (18, 4, 4)
    assert maps["underlay"].shape == (18, 4, 4)


def test_sidecar_rejects_probe_or_capture_band_join_mismatch(tmp_path):
    raw = np.zeros((18, 4, 4), dtype="<u4")
    paths = {}
    for bank in ("front", "underlay"):
        path = tmp_path / f"{bank}.u32"
        raw.tofile(path)
        paths[bank] = {"status": "valid", "schema": field_mod.SOURCE_GENERATION_SCHEMA,
                       "bank": bank, "raw_path": path.name,
                       "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "bytes": path.stat().st_size, "probe_size": 4, "layer_count": 18,
                       "word_format": "little_endian_u32",
                       "encoding": {"zero": "missing",
                                    "low31": "captured_geometry_source_revision_w",
                                    "high_bit": "generated_sky_paint_generation"}}
    envelope = {"schema": field_mod.SOURCE_GENERATION_SCHEMA, "probe_size": 4,
                "layer_count": 18, "capture_frame_index": 0,
                "capture_request_id": 1, "capture_sequence_frame": 0,
                "snapshot_frame_index": 0, "snapshot_frame_join_valid": True,
                "snapshot_timeline": "same_frame_post_image_store_before_next_frame_async_transfer",
                "capture_bands": [0, 1, 2], "front": paths["front"],
                "underlay": paths["underlay"], "frame_index": 0,
                "request_id": 1, "sequence_frame": 0}
    metadata = {"source_capture_generation_sidecars": envelope,
                "source_capture_state": {"probe_size": 4, "capture_bands": [0, 1, 2]},
                "frame_index": 0, "request_id": 1, "sequence_frame": 0}
    bad_probe = deepcopy(metadata)
    bad_probe["source_capture_generation_sidecars"]["probe_size"] = 8
    with pytest.raises(field_mod.ActualFieldError, match="probe_size"):
        field_mod._load_source_generation_sidecars(
            bad_probe, tmp_path / "trace.jsonl", required=True)
    bad_bands = deepcopy(metadata)
    bad_bands["source_capture_generation_sidecars"]["capture_bands"] = [0, 0, 2]
    with pytest.raises(field_mod.ActualFieldError, match="capture_bands"):
        field_mod._load_source_generation_sidecars(
            bad_bands, tmp_path / "trace.jsonl", required=True)



def test_historical_perspective_field_without_sidecars_keeps_raw_pixels():
    reference = (1 << 28) | 1
    frame = _frame(reference, reference, {})
    assert frame.fields["final_rgb"].shape == (1, 1, 3)
    with pytest.raises(field_mod.ActualFieldError, match="no matching source-generation bank"):
        frame.pixel_provenance(0, 0)
