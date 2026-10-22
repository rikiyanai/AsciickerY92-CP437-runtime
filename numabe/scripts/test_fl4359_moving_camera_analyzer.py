#!/usr/bin/env python3
"""FL-4359 moving-camera analyzer regression tests.

Pins the false-green path Codex caught on 2026-06-27: scrolled-off
(different_world_sample) and no_match cells must NOT sit in the jitter_pct
DENOMINATOR, or fast camera motion would dilute the shimmer percentage into a
false pass. Also pins the comparable-cell evidence gate and the moving-camera
requirement.
"""
import argparse
import copy
import importlib.util
import json
import math
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

_MOD_PATH = Path(__file__).resolve().parent / "analyze_fl4359_moving_camera.py"
_spec = importlib.util.spec_from_file_location("fl4359_moving_camera", _MOD_PATH)
mc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mc)


def _f32_bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def _terrain_colour_witness(seed: float = 0.0) -> dict[str, list[int]]:
    witness = {
        stage: [_f32_bits(seed + float(lane) * 0.01) for lane in range(4)]
        for stage in mc.PRODUCT_TERRAIN_COLOUR_WITNESS_STAGES
    }
    witness["palette_bg_validity"][3] = _f32_bits(1.0)
    return witness


def _packed_rgb(rgb: list[int]) -> int:
    return int(rgb[0]) | (int(rgb[1]) << 8) | (int(rgb[2]) << 16) | (0xFF << 24)


def _producer_subcell_index(
    ansi_width: int, cell_x: int, cell_y: int, slot: int
) -> int:
    """Independent copy of the shader's row-major 2xANSI index law."""
    return (
        (cell_y * 2 + (slot >> 1)) * (ansi_width * 2)
        + cell_x * 2
        + (slot & 1)
    )


def _row(fid, x, z, owner="terrain", pid=1):
    anchor = {"x": float(x), "y": 0.0, "z": float(z)}
    return {
        "frame_id": fid,
        "screen_cell": [int(x), int(z)],
        "ansi_cell_world_anchor": anchor,
        "final_product_world_anchor": dict(anchor),
        "final_product_status": 0x02000001,
        "comparison_anchor_source": mc.COMPARISON_ANCHOR_SOURCE_B55,
        "capture_schema": mc.CURRENT_SPARSE_CAPTURE_SCHEMA,
        "sample_index": int(x) * 4,
        "subcell_slot": 0,
        "selected_owner": owner,
        "selected_product_id": pid,
        "terrain_cell": [int(x), int(z)],
        "material_role": 1,
        "water_phase": 0,
        "terrain_product_detail": 0,
        "cloud_shadow_phase": 0,
        "foliage_product_present": False,
        "final_product_owner": 1 if owner == "terrain" else 5 if owner == "water" else 2,
        "final_product_source_key": int(x) * 1000 + int(z),
        "final_product_matcell_packed": (int(x) << 16) | (int(z) & 0xFFFF),
        "final_product_glyph": 10,
        "final_product_material_family": 1 if owner == "terrain" else 3,
        "final_product_material_id": 4 if owner == "terrain" else 140,
        "final_product_authored_source_key": int(x) * 1000 + int(z),
        "final_product_authored_receiver_cell": [int(x), int(z)],
        "final_product_authored_material_id": 4 if owner == "terrain" else 140,
        "final_product_authored_role_family": 0xFFFFFFFF,
        "final_product_authored_role_family_valid": False,
        "final_product_classifier_family": 1 if owner == "terrain" else 3,
        "final_product_dirt_mountain_role": False,
        "final_product_elevation_class": 0,
        "final_product_matlib_column_id": 4 if owner == "terrain" else 140,
        "final_product_matlib_profile": [256, 192, 0],
        "final_product_matlib_profile_valid": True,
        "final_product_matlib_profile_validity_bits": 0x1F,
        "final_product_admission_validity_bits": mc.PRODUCT_ADMISSION_EXPECTED_VALIDITY_MASK,
        "final_product_admission_transport_valid": owner == "terrain",
        "final_product_fg_rgb": [80, 70, 60],
        "final_product_bg_rgb": [30, 25, 20],
        "glyph_id": 10,
        "fg_rgb": [80, 70, 60],
        "bg_rgb": [30, 25, 20],
        "final_product_trace_receiver_bits": [
            _f32_bits(float(x)), _f32_bits(0.0), _f32_bits(float(z)), _f32_bits(1.0)
        ],
        "final_product_continuous_sample_bits": [
            _f32_bits(float(x) + 0.0625),
            _f32_bits(0.0),
            _f32_bits(float(z) + 0.0625),
        ],
        "final_product_continuous_sample_valid": owner == "terrain",
        "final_product_terrain_colour_witness_bits": _terrain_colour_witness(),
        "final_product_terrain_colour_witness_valid": owner == "terrain",
        "final_product_pre_finalization_signature_bits": [
            0x02000001,
            10,
            _packed_rgb([80, 70, 60]),
            _packed_rgb([30, 25, 20]),
        ],
        "final_product_post_finalization_signature_bits": [
            0x02000001,
            10,
            _packed_rgb([80, 70, 60]),
            _packed_rgb([30, 25, 20]),
        ],
        "final_product_owner_name": owner,
        "final_paint_slot_glyph": 10,
        "final_paint_slot_present": True,
        "final_paint_slot_actor_overlay": False,
        "final_paint_slot_colour_present": True,
        "final_paint_slot_fg_rgb": [80, 70, 60],
        "final_paint_slot_bg_rgb": [30, 25, 20],
        "final_paint_mask_unknown_bits": 0,
        "final_product_exact_identity": {
            "b55": {
                "mapped_owner_kind": 1 if owner == "terrain" else 5,
                "anchor_bits": [int(x), 0, int(z)],
            },
            "b64": {
                "object_id": 0,
                "primitive_id": int(x) * 1000 + int(z),
                "material_id": 1,
            },
            "b60_sidecar": {
                "output_r1": [
                    1,
                    int(x) * 1000 + int(z),
                    0,
                    (int(x) << 16) | (int(z) & 0xFFFF),
                ],
            },
            "cell_mask_winner_available": True,
            "cell_mask_winner_cell_consistent": True,
            "cell_mask_winner_key": {
                "key_a": [1 if owner == "terrain" else 3, 0, int(x) * 1000 + int(z), 1],
                "key_b": [1, int(x) * 1000 + int(z), 0, (int(x) << 16) | (int(z) & 0xFFFF)],
                "key_c": [0, 0, 0, 0],
                "key_d": [int(x), 0, int(z), 0],
            },
            "cell_mask_winner_glyph": 10,
            "cell_mask_winner_priority": 800,
            "cell_mask_winner_slot": 0,
            "cell_mask_winner_source_kind": "quarter_slot",
            "cell_mask_winner_source_kind_code": 0,
            "cell_mask_winner_source_kind_available": True,
            "cell_mask_winner_source_semantic_key": {
                "key_a": [1 if owner == "terrain" else 3, 0, int(x) * 1000 + int(z), 1],
                "key_b": [1, int(x) * 1000 + int(z), 0, (int(x) << 16) | (int(z) & 0xFFFF)],
                "key_c": [0, 0, 0, 0],
                "key_d": [int(x), 0, int(z), 0],
            },
            "cell_mask_winner_source_anchor_bits": [int(x), 0, int(z)],
        },
    }


def _cell_rows(
    fid: int,
    screen_x: int,
    screen_y: int,
    mask_anchor: tuple[float, float, float],
    glyph: int = 10,
    source_kind: str = "quarter_slot",
    priority: int = 850,
    owner: str = "terrain",
) -> list[dict]:
    rows: list[dict] = []
    for slot in range(4):
        row = _row(fid, screen_x, screen_y, owner=owner, pid=screen_x * 100 + slot)
        row["screen_cell"] = [screen_x, screen_y]
        row["sample_index"] = (screen_y * 1000 + screen_x) * 4 + slot
        row["subcell_slot"] = slot
        row["final_product_exact_identity"]["cell_mask_winner_slot"] = 0
        row["final_product_exact_identity"]["cell_mask_winner_glyph"] = glyph
        row["final_product_exact_identity"]["cell_mask_winner_priority"] = priority
        row["final_product_exact_identity"]["cell_mask_winner_source_kind"] = source_kind
        row["final_product_exact_identity"]["cell_mask_winner_source_kind_code"] = (
            3 if source_kind == "center_surface"
            else 2 if source_kind == "center_sky"
            else 1 if source_kind == "center_terrain"
            else 0
        )
        mask_key = row["final_product_exact_identity"]["cell_mask_winner_key"]
        # Product's terrain world-cell key retains OWNER_TERRAIN in lane zero.
        # Earlier fixtures put coordinates there and could not exercise the
        # production owner-domain partition used by the mask stability oracle.
        mask_key["key_a"] = [
            1 if owner == "terrain"
            else 5 if owner == "water"
            else 6 if owner == "sky"
            else 2 if owner == "static_mesh"
            else 3,
            99,
            (int(math.floor(mask_anchor[1])) & 0xFFFFFFFF) ^ 0x80000000,
            1,
        ]
        mask_key["key_b"] = [1, 77, 88, 1]
        mask_key["key_c"] = [1, 77, 88, 99]
        mask_key["key_d"] = [
            _f32_bits(mask_anchor[0]),
            _f32_bits(mask_anchor[1]),
            _f32_bits(mask_anchor[2]),
            0,
        ]
        row["final_product_exact_identity"]["cell_mask_winner_source_semantic_key"] = {
            lane: list(words) for lane, words in mask_key.items()
        }
        row["final_product_exact_identity"]["cell_mask_winner_source_anchor_bits"] = list(
            mask_key["key_d"][:3]
        )
        row["final_paint_slot_glyph"] = glyph
        if source_kind == "quarter_slot" and slot == 0:
            row["final_product_world_anchor"] = {
                "x": mask_anchor[0],
                "y": mask_anchor[1],
                "z": mask_anchor[2],
            }
        rows.append(row)
    return rows


def _terminal_reprojection_frame(
    sequence: int,
    matrix_tx: float,
    terminal_x: int,
) -> list[dict]:
    rows = _cell_rows(
        sequence, 0, 0, (0.0, 0.0, 0.0), glyph=35, priority=850
    )
    matrix = [
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        matrix_tx, 0.0, 0.0, 1.0,
    ]
    trace = {
        "world_to_clip_matrix": matrix,
        "target_size": [32, 32],
        "cell_size": [32, 32],
        "semantic_ansi_size": [1, 1],
        "readback_flip_y": False,
        "frame_index": 10 + sequence,
        "sequence_frame": sequence,
        "authority_server_tick": 100 + sequence,
        "camera_hash": 200 + sequence,
        "scene_revision": 1,
        "view_revision": 300 + sequence,
        "dynamic_revision": 400 + sequence,
    }
    oracle_identity = {
        "owner_kind": 2,
        "object_id": 7,
        "primitive_id": 11,
        "material": 4,
    }
    for slot, row in enumerate(rows):
        row["sample_index"] = slot
        row["subcell_slot"] = slot
        row["screen_cell"] = [0, 0]
        row["frame_trace"] = dict(trace)
        row["oracle_query_valid"] = True
        row["oracle_direct_hit"] = True
        row["oracle_identity"] = dict(oracle_identity)
        row["oracle_stable_world_anchor"] = [0.0, 0.0, 0.0]
        row["oracle_world_position"] = [0.0, 0.0, 0.0]
        row["oracle_identity_agrees_with_selected_surface"] = True
        row["final_product_exact_identity"]["cell_mask_winner_slot"] = 3
        b43_key = row["final_product_exact_identity"]["cell_mask_winner_key"]
        row["final_paint_elected_identity_available"] = True
        row["final_paint_elected_identity_validity"] = 0x1 | (5 << 16)
        row["final_paint_elected_identity_glyph"] = 35
        row["final_paint_elected_identity_priority"] = 850
        row["final_paint_elected_identity_slot"] = 3
        for name in ("a", "b", "c", "d"):
            row[f"final_paint_elected_identity_key_{name}"] = list(
                b43_key[f"key_{name}"]
            )
        row["final_paint_glyph"] = 35
        row["final_paint_terminal_mask_ink_count"] = 1
        row["final_paint_terminal_mask_sum_x"] = terminal_x
        row["final_paint_terminal_mask_sum_y"] = 16
        row["final_paint_terminal_mask_min_x"] = terminal_x
        row["final_paint_terminal_mask_min_y"] = 16
        row["final_paint_terminal_mask_max_x"] = terminal_x
        row["final_paint_terminal_mask_max_y"] = 16
        row["final_paint_terminal_mask_digest"] = 0x1234
        row["final_paint_terminal_occurrence_valid"] = True
    return rows


def _terminal_reprojection_two_cell_frame(
    sequence: int,
    matrix_tx: float,
    tracked_terminal_x: int,
) -> list[dict]:
    """Build two ANSI cells while keeping only cell zero's Product token tracked."""
    tracked = _terminal_reprojection_frame(sequence, matrix_tx, tracked_terminal_x)
    reference = _terminal_reprojection_frame(sequence, matrix_tx, 48)
    for row in tracked + reference:
        row["frame_trace"]["target_size"] = [64, 32]
        row["frame_trace"]["semantic_ansi_size"] = [2, 1]
    for slot, row in enumerate(tracked):
        row["sample_index"] = _producer_subcell_index(2, 0, 0, slot)
    for slot, row in enumerate(reference):
        row["sample_index"] = _producer_subcell_index(2, 1, 0, slot)
        row["screen_cell"] = [1, 0]
        identity = row["final_product_exact_identity"]
        identity["cell_mask_winner_glyph"] = 64
        row["final_paint_slot_glyph"] = 64
        row["final_paint_elected_identity_glyph"] = 64
        row["final_paint_glyph"] = 64
        row["final_paint_elected_identity_key_a"][2] += 1
        row["final_paint_elected_identity_key_c"][3] += 1
        if sequence == 0:
            # The second cell supplies the later frame's exact reference ray;
            # it is not another eligible frame-A terminal tracking source.
            row["oracle_query_valid"] = False
    return tracked + reference


def _v6_expected_raster_row(**overrides):
    row = {
        "capture_schema": "fl4359_sparse_surface_capture.v6",
        "oracle_classification": "identity_mismatch",
        "oracle_query_valid": True,
        "oracle_direct_hit": True,
        "oracle_identity": {
            "owner_kind": 2,
            "object_id": 10,
            "primitive_id": 20,
            "material": 4,
        },
        "probe_raster_contender_overflow": False,
        "probe_raster_exact_identity_contender_count": 0,
        "probe_raster_selected_identity_contender_count": 0,
        "probe_raster_structural_identity_contender_count": 0,
        "probe_expected_raster_record_valid": True,
        "probe_expected_raster_identity_visible_mask": 2,
        "probe_expected_raster_exact_capture_match_mask": 2,
        "probe_expected_raster_source_valid_mask": 2,
        "probe_expected_raster_face_enabled_mask": 2,
        "probe_expected_raster_clip_valid_mask": 2,
        "probe_expected_raster_cover_mask": 2,
        "probe_expected_raster_suppressed_mask": 0,
        "probe_prepack_valid": False,
        "probe_candidate_valid": False,
        "probe_candidate_selected_surface_valid": False,
    }
    row.update(overrides)
    return row


def _v8_expected_center_row(**overrides):
    """Build a v8 revision-matched center-witness row.

    The center query is intentionally independent from the expected raster
    masks.  These rows are injected falsifiers for the capture-center witness,
    so a query/capture alias must not be mislabeled as capture-writer damage.
    Numeric capture-generation changes are deliberately non-causal here.
    """
    direct_identity = {
        "owner_kind": 2,
        "object_id": 10,
        "primitive_id": 20,
        "material": 4,
    }
    row = _v6_expected_raster_row(
        capture_schema=mc.CURRENT_SPARSE_CAPTURE_SCHEMA,
        probe_expected_raster_exact_capture_match_mask=0,
        probe_expected_center_record_valid=True,
        probe_expected_center_applicable=True,
        probe_expected_center_address_valid=True,
        probe_expected_center_query_valid=True,
        probe_expected_center_direct_hit=True,
        probe_expected_center_source_probe=1,
        probe_expected_center_face=0,
        probe_expected_center_texel=1234,
        probe_capture_bands=[0, 1, 2],
        probe_expected_center_identity=dict(direct_identity),
        probe_expected_center_capture_identity=dict(direct_identity),
        probe_expected_center_capture_valid=True,
        probe_expected_center_capture_matches_query=True,
        probe_expected_center_capture_matches_direct=True,
        probe_expected_center_capture_generation=7,
        probe_expected_center_capture_revisions={
            "scene": 101,
            "terrain": 201,
            "water": 301,
            "dynamic": 401,
        },
        probe_expected_center_current_revisions={
            "scene": 101,
            "terrain": 201,
            "water": 301,
            "dynamic": 401,
        },
        probe_expected_center_capture_origin=[10.0, 20.0, 30.0],
        probe_expected_center_assigned_origin=[10.0, 20.0, 30.0],
        probe_expected_center_capture_origin_valid=True,
        probe_expected_center_capture_physical_layer=6,
        probe_expected_center_expected_physical_layer=6,
        probe_expected_center_physical_layer_matches=True,
        probe_expected_center_face_admitted=True,
        probe_expected_center_allocation_context={
            "scene": 101,
            "terrain": 201,
            "water": 301,
            "dynamic": 401,
        },
        probe_expected_center_publication_context={
            "family": "terrain",
            "capture_sha256": "a" * 64,
            "current_sha256": "a" * 64,
            "matches": True,
        },
        probe_expected_center_submission_context={
            "token_sha256": "b" * 64,
            "publication_sha256": "a" * 64,
            "logical_probe": 1,
            "physical_band": 1,
            "physical_layer": 6,
            "face": 0,
            "assigned_origin": [10.0, 20.0, 30.0],
            "draw_count": 1,
            "instance_count": 64,
            "face_admitted": True,
        },
        probe_expected_center_capture_depth_abs_residual=0.0,
        probe_expected_center_neighbor_valid=True,
        probe_expected_center_neighbor_dx=0,
        probe_expected_center_neighbor_dy=0,
        probe_expected_center_neighbor_texel=1234,
        probe_expected_center_neighbor_generation=7,
    )
    row.update(overrides)
    return row


def _v8_source_capture_row(**overrides):
    row = {
        "capture_schema": mc.CURRENT_SPARSE_CAPTURE_SCHEMA,
        "probe_capture_bands": [0, 1, 2],
        "probe_source_capture_identity": {
            "owner_kind": 2,
            "object_id": 10,
            "primitive_id": 20,
            "material": 4,
        },
        "probe_source_capture_revisions": [101, 201, 201, 7],
        "probe_source_current_revisions": {
            "scene": 101,
            "terrain": 201,
            "static": 301,
            "water": 401,
            "dynamic": 501,
        },
        "probe_source_capture_probe": 1,
        "probe_source_capture_face": 0,
        "probe_source_capture_physical_layer": 6,
        "probe_source_capture_origin": [10.0, 20.0, 30.0],
        "probe_source_assigned_origin": [10.0, 20.0, 30.0],
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
            "logical_probe": 1,
            "physical_band": 1,
            "physical_layer": 6,
            "face": 0,
            "assigned_origin": [10.0, 20.0, 30.0],
            "draw_count": 1,
            "instance_count": 64,
            "face_admitted": True,
        },
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("mutation", "expected_stage"),
    [
        ({"probe_expected_raster_record_valid": False},
         "expected_reference_raster_witness_invalid"),
        ({"probe_expected_raster_identity_visible_mask": 0},
         "capture_visibility_domain"),
        ({"probe_expected_raster_exact_capture_match_mask": 0},
         "capture_write"),
        ({"probe_expected_raster_source_valid_mask": 0},
         "capture_write"),
        ({"probe_expected_raster_face_enabled_mask": 0},
         "capture_face_visibility_domain"),
        ({"probe_expected_raster_clip_valid_mask": 0},
         "reconstruction_clip_invalid"),
        ({"probe_expected_raster_suppressed_mask": 2},
         "capture_positive_contradiction_suppression"),
        ({"probe_expected_raster_cover_mask": 0},
         "reconstruction_coverage"),
        ({}, "raster_expected_fragment_missing"),
    ],
)
def test_v6_expected_reference_witness_assigns_earliest_stage(
    mutation, expected_stage
):
    assert mc._oracle_first_divergence(
        _v6_expected_raster_row(**mutation)
    ) == expected_stage


@pytest.mark.parametrize(
    ("mutation", "expected_classification"),
    [
        (
            {
                "probe_expected_center_identity": {
                    "owner_kind": 2,
                    "object_id": 10,
                    "primitive_id": 21,
                    "material": 4,
                },
                "probe_expected_center_capture_identity": {
                    "owner_kind": 2,
                    "object_id": 10,
                    "primitive_id": 21,
                    "material": 4,
                },
                "probe_expected_center_capture_matches_direct": False,
            },
            "capture_center_alias_confirmed",
        ),
        (
            {
                "probe_expected_center_capture_identity": {
                    "owner_kind": 2,
                    "object_id": 10,
                    "primitive_id": 21,
                    "material": 4,
                },
                "probe_expected_center_capture_matches_query": False,
                "probe_expected_center_capture_matches_direct": False,
            },
            "capture_writer_identity_mismatch",
        ),
        (
            {
                "probe_expected_center_direct_hit": False,
                "probe_expected_center_identity": {
                    "owner_kind": 0,
                    "object_id": 0,
                    "primitive_id": 0,
                    "material": 0,
                },
            },
            "capture_writer_false_positive",
        ),
        (
            {"probe_expected_center_capture_valid": False},
            "capture_writer_false_negative",
        ),
        (
            {"probe_expected_center_query_valid": False},
            "capture_center_query_invalid",
        ),
        (
            {"probe_expected_center_applicable": False},
            "capture_center_not_applicable",
        ),
    ],
)
def test_v8_center_witness_injected_falsifiers_are_mutually_exclusive(
    mutation, expected_classification
):
    row = _v8_expected_center_row(**mutation)
    assert mc._oracle_expected_center_classification(row) == expected_classification


def test_v8_center_alias_does_not_claim_capture_writer_corruption():
    row = _v8_expected_center_row(
        probe_expected_center_identity={
            "owner_kind": 2,
            "object_id": 10,
            "primitive_id": 21,
            "material": 4,
        },
        probe_expected_center_capture_identity={
            "owner_kind": 2,
            "object_id": 10,
            "primitive_id": 21,
            "material": 4,
        },
        probe_expected_center_capture_matches_direct=False,
    )
    assert mc._oracle_expected_center_classification(row) == (
        "capture_center_alias_confirmed"
    )
    assert mc._oracle_first_divergence(row) != "capture_write"


@pytest.mark.parametrize(
    "mutation",
    [
        {
            "probe_expected_center_capture_identity": {
                "owner_kind": 2,
                "object_id": 10,
                "primitive_id": 21,
                "material": 4,
            },
            "probe_expected_center_capture_matches_query": False,
            "probe_expected_center_capture_matches_direct": False,
        },
        {"probe_expected_center_direct_hit": False},
        {"probe_expected_center_capture_valid": False},
    ],
)
def test_v8_center_writer_falsifiers_assign_capture_write(mutation):
    row = _v8_expected_center_row(**mutation)
    assert mc._oracle_expected_center_classification(row) in {
        "capture_writer_identity_mismatch",
        "capture_writer_false_positive",
        "capture_writer_false_negative",
    }
    assert mc._oracle_first_divergence(row) == "capture_write"


def test_v8_center_invalid_record_fails_closed_before_any_writer_claim():
    row = _v8_expected_center_row(probe_expected_center_record_valid=False)
    assert mc._oracle_expected_center_classification(row) not in {
        "capture_center_alias_confirmed",
        "capture_writer_identity_mismatch",
        "capture_writer_false_positive",
        "capture_writer_false_negative",
    }
    assert mc._oracle_first_divergence(row) == (
        "expected_capture_center_record_invalid"
    )


@pytest.mark.parametrize(
    ("mutation", "expected_classification", "expected_stage"),
    [
        (
            {
                "probe_expected_center_capture_revisions": {
                    "scene": 999,
                    "terrain": 201,
                    "water": 301,
                    "dynamic": 401,
                }
            },
            "capture_center_scene_revision_mismatch",
            "expected_capture_center_scene_revision_mismatch",
        ),
        (
            {
                "probe_expected_center_capture_revisions": {
                    "scene": 101,
                    "terrain": 999,
                    "water": 301,
                    "dynamic": 401,
                }
            },
            "capture_center_family_revision_mismatch",
            "expected_capture_center_family_revision_mismatch",
        ),
        (
            {"probe_expected_center_capture_origin": [11.0, 20.0, 30.0]},
            "capture_center_origin_mismatch",
            "expected_capture_center_origin_mismatch",
        ),
    ],
)
def test_v8_center_revision_and_origin_mismatches_fail_closed_before_writer(
    mutation, expected_classification, expected_stage
):
    row = _v8_expected_center_row(**mutation)
    assert mc._oracle_expected_center_classification(row) == expected_classification
    assert mc._oracle_first_divergence(row) == expected_stage
    assert mc._oracle_first_divergence(row) != "capture_write"


def test_v8_center_capture_generation_difference_alone_is_not_corruption():
    row = _v8_expected_center_row(probe_expected_center_capture_generation=999)
    assert mc._oracle_expected_center_classification(row) in {
        "capture_center_identity_agreement",
        "capture_center_alias_confirmed",
    }
    assert mc._oracle_first_divergence(row) != "capture_write"


def test_v8_center_invalid_capture_origin_context_fails_closed():
    row = _v8_expected_center_row(
        probe_expected_center_capture_origin_valid=False
    )
    assert mc._oracle_expected_center_classification(row) == (
        "capture_center_origin_context_invalid"
    )
    assert mc._oracle_first_divergence(row) == (
        "expected_capture_center_origin_context_invalid"
    )


def test_v8_center_publication_mismatch_fails_closed_before_writer():
    row = _v8_expected_center_row(
        probe_expected_center_publication_context={
            "family": "terrain",
            "capture_sha256": "a" * 64,
            "current_sha256": "b" * 64,
            "matches": False,
        }
    )
    assert mc._oracle_expected_center_classification(row) == (
        "capture_center_publication_mismatch"
    )
    assert mc._oracle_first_divergence(row) == (
        "expected_capture_center_publication_mismatch"
    )


def test_v8_center_missing_publication_hash_fails_closed_before_writer():
    row = _v8_expected_center_row(
        probe_expected_center_publication_context={
            "family": "terrain",
            "capture_sha256": "",
            "current_sha256": "a" * 64,
            "matches": False,
        }
    )
    assert mc._oracle_expected_center_classification(row) == (
        "capture_center_publication_context_invalid"
    )
    assert mc._oracle_first_divergence(row) == (
        "expected_capture_center_publication_context_invalid"
    )


def test_v8_center_equal_publication_identity_mismatch_maps_to_capture_write():
    row = _v8_expected_center_row(
        probe_expected_center_capture_identity={
            "owner_kind": 2,
            "object_id": 10,
            "primitive_id": 21,
            "material": 4,
        },
        probe_expected_center_capture_matches_query=False,
        probe_expected_center_capture_matches_direct=False,
    )
    assert mc._oracle_expected_center_classification(row) == (
        "capture_writer_identity_mismatch"
    )
    assert mc._oracle_first_divergence(row) == "capture_write"


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (
            {"probe_expected_center_capture_physical_layer": 7},
            "capture_center_physical_layer_mismatch",
        ),
        (
            {"probe_expected_center_face_admitted": False},
            "capture_center_face_not_admitted",
        ),
        (
            {"probe_expected_center_submission_context": {}},
            "capture_center_publication_context_invalid",
        ),
        (
            {
                "probe_expected_center_submission_context": {
                    "token_sha256": "b" * 64,
                    "publication_sha256": "a" * 64,
                    "logical_probe": 1,
                    "physical_band": 1,
                    "physical_layer": 6,
                    "face": 0,
                    "assigned_origin": [10.0, 20.0, 30.0],
                    "draw_count": 0,
                    "instance_count": 0,
                    "face_admitted": True,
                }
            },
            "capture_center_submission_context_mismatch",
        ),
    ],
)
def test_v8_center_submission_identity_fails_closed(mutation, expected):
    row = _v8_expected_center_row(**mutation)
    assert mc._oracle_expected_center_classification(row) == expected
    assert mc._oracle_first_divergence(row) != "capture_write"


def test_v8_generic_source_writer_requires_complete_submission_identity():
    assert mc._oracle_source_capture_write_stage(
        _v8_source_capture_row()
    ) == "capture_write"
    assert mc._oracle_source_capture_write_stage(
        _v8_source_capture_row(probe_source_capture_face_admitted=False)
    ) == "source_capture_face_not_admitted"
    assert mc._oracle_source_capture_write_stage(
        _v8_source_capture_row(probe_source_capture_submission_context={})
    ) == "source_capture_publication_context_invalid"


def test_v8_previous_accepts_exact_current_authoring_submission_after_band_swap():
    row = _v8_source_capture_row(
        probe_capture_bands=[0, 2, 1],
        probe_source_capture_probe=2,
        probe_source_capture_physical_layer=6,
        probe_source_capture_submission_context={
            "token_sha256": "b" * 64,
            "publication_sha256": "a" * 64,
            "logical_probe": 1,
            "physical_band": 1,
            "physical_layer": 6,
            "face": 0,
            "assigned_origin": [10.0, 20.0, 30.0],
            "draw_count": 1,
            "instance_count": 64,
            "face_admitted": True,
        },
    )

    assert mc._oracle_source_capture_write_stage(row) == "capture_write"


def test_v8_current_rejects_a_previous_authoring_submission():
    submission = _v8_source_capture_row()[
        "probe_source_capture_submission_context"
    ].copy()
    submission["logical_probe"] = 2
    row = _v8_source_capture_row(
        probe_source_capture_submission_context=submission
    )

    assert mc._oracle_source_capture_write_stage(row) == (
        "source_capture_submission_context_mismatch"
    )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (
            {"oracle_direct_hit": False},
            "production_splat_outside_current_ray_visibility",
        ),
        (
            {
                "probe_raster_exact_identity_contender_count": 0,
            },
            "exact_reference_contender_absent",
        ),
        (
            {
                "probe_raster_nearest_exact_identity_depth": 0.4,
                "probe_prepack_hardware_depth": 0.5,
            },
            "exact_reference_nearer_depth_order_invalid",
        ),
        (
            {
                "probe_raster_nearest_exact_identity_depth": 0.5,
                "probe_prepack_hardware_depth": 0.4,
            },
            "selected_splat_nearer_legal_depth_result",
        ),
        (
            {
                "probe_raster_nearest_exact_identity_depth": 0.5,
                "probe_prepack_hardware_depth": 0.5,
            },
            "depth_tie_legal_depth_result",
        ),
        (
            {
                "probe_raster_nearest_exact_identity_depth": math.nan,
                "probe_prepack_hardware_depth": 0.5,
            },
            "depth_witness_invalid",
        ),
    ],
)
def test_raster_winner_relation_does_not_call_a_legal_winner_depth_invalid(
    mutation, expected
):
    row = {
        "oracle_direct_hit": True,
        "probe_raster_exact_identity_contender_count": 1,
        "probe_raster_nearest_exact_identity_depth": 0.5,
        "probe_prepack_hardware_depth": 0.4,
    }
    row.update(mutation)

    assert mc._raster_winner_relation(row) == expected


def test_v7_generic_source_path_cannot_claim_capture_write():
    row = _v8_source_capture_row(
        capture_schema="fl4359_sparse_surface_capture.v7"
    )
    assert mc._oracle_source_capture_write_stage(row) == (
        "source_capture_legacy_unavailable"
    )


def test_v7_center_schema_is_legacy_unavailable_for_v8_causal_attribution():
    row = _v8_expected_center_row(
        capture_schema="fl4359_sparse_surface_capture.v7"
    )
    assert mc._oracle_expected_center_classification(row) == (
        "capture_center_legacy_unavailable"
    )


def _load_headed_runner():
    runner_path = Path(__file__).resolve().parent / "run_fl4359_headed_movement.py"
    if str(runner_path.parent) not in sys.path:
        sys.path.insert(0, str(runner_path.parent))
    spec = importlib.util.spec_from_file_location("run_fl4359_headed_movement", runner_path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner


def test_skip_jitter_defaults_to_fail_closed_movement_proof():
    runner = _load_headed_runner()
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parent / "run_fl4359_headed_movement.py"),
            "--skip-jitter-analysis",
            "--preflight-only",
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "movement-proof classification requires actual jitter/shimmer analysis" in result.stdout
    assert runner._run_classification_contract_reason(
        runner.RUN_CLASSIFICATION_MOVEMENT_PROOF,
        True,
    )


def test_canonical_png_grid_is_explicit_diagnostic_not_movement_proof():
    runner = _load_headed_runner()
    diagnostic = {
        "run_classification": runner.RUN_CLASSIFICATION_DIAGNOSTIC_ONLY,
        "proof_classification": runner.DIAGNOSTIC_PROOF_CLASSIFICATION,
        "diagnostic_only": True,
        "analysis_skipped": True,
        "admissible": False,
        "proof_pass": False,
        "pass": False,
        "reason": "canonical_png_grid_capture_only",
    }
    assert runner._summary_is_diagnostic_only(diagnostic)
    assert not runner._summary_classification_is_movement_proof(diagnostic)
    assert runner._bounded_static_diagnostic_accepts_analysis_skip(
        diagnostic,
        {"pass": True},
        skip_jitter_analysis=True,
        run_classification=runner.RUN_CLASSIFICATION_DIAGNOSTIC_ONLY,
        exact_pose_capture=True,
    )


def test_analyzed_diagnostic_collects_temporal_evidence_without_proof_authority():
    runner = _load_headed_runner()
    diagnostic = {
        "run_classification": runner.RUN_CLASSIFICATION_DIAGNOSTIC_ONLY,
        "proof_classification": runner.DIAGNOSTIC_PROOF_CLASSIFICATION,
        "diagnostic_only": True,
        "analysis_skipped": False,
        "frame_count": 8,
        "admissible": False,
        "proof_pass": False,
        "pass": False,
        "proof": "bounded_movement_diagnostic",
        "proof_reason": "diagnostic_only_analysis_observed_non_proof",
    }
    assert runner._run_classification_contract_reason(
        runner.RUN_CLASSIFICATION_DIAGNOSTIC_ONLY,
        False,
    ) == ""
    assert runner._summary_is_diagnostic_only(diagnostic)
    assert not runner._summary_classification_is_movement_proof(diagnostic)
    assert runner._analyzed_diagnostic_is_nonproof(
        diagnostic,
        {"pass": True},
        skip_jitter_analysis=False,
        run_classification=runner.RUN_CLASSIFICATION_DIAGNOSTIC_ONLY,
    )
    diagnostic["admissible"] = True
    assert not runner._analyzed_diagnostic_is_nonproof(
        diagnostic,
        {"pass": True},
        skip_jitter_analysis=False,
        run_classification=runner.RUN_CLASSIFICATION_DIAGNOSTIC_ONLY,
    )


def test_valid_movement_summary_requires_all_proof_identity_fields():
    runner = _load_headed_runner()
    movement = {
        "run_classification": runner.RUN_CLASSIFICATION_MOVEMENT_PROOF,
        "proof_classification": runner.RUN_CLASSIFICATION_MOVEMENT_PROOF,
        "analysis_skipped": False,
        "admissible": True,
        "proof_pass": True,
        "pass": True,
    }
    assert runner._summary_classification_is_movement_proof(movement)
    movement["admissible"] = False
    assert not runner._summary_classification_is_movement_proof(movement)


def test_movement_preflight_remains_admitted_with_valid_proof_route():
    runner = _load_headed_runner()
    args = argparse.Namespace(
        camera_mode="plane_air",
        run_classification=runner.RUN_CLASSIFICATION_MOVEMENT_PROOF,
        godot_window_mode="fullscreen",
        turn_action="rotate_left",
        plane_throttle=0.37,
        plane_pitch_base=0.02,
        plane_pitch_amp=0.03,
        plane_roll_base=-0.12,
        plane_roll_amp=0.04,
        plane_circle_frames=12,
        turn_frames=8,
        skip_jitter_analysis=False,
        jitter_capture_world_sample_stride=64,
        exact_camera_projection="perspective",
        product_time=0.35,
        after_frames=2,
        extra_user_arg=[],
    )
    preflight = runner._airplane_route_preflight(
        args,
        effective_plane_circle_frames=12,
        effective_turn_frames=8,
        effective_jitter_capture_frame_limit=5,
        godot_timeout_seconds=180.0,
    )
    assert preflight["ok"] is True
    assert preflight["failed_checks"] == []
    assert preflight["proof_classification"] == runner.RUN_CLASSIFICATION_MOVEMENT_PROOF
    for timeout in (float("inf"), float("-inf"), float("nan")):
        invalid = runner._airplane_route_preflight(
            args,
            effective_plane_circle_frames=12,
            effective_turn_frames=8,
            effective_jitter_capture_frame_limit=5,
            godot_timeout_seconds=timeout,
        )
        assert invalid["ok"] is False
        assert invalid["failed_checks"] == ["finite_godot_timeout"]


@pytest.mark.parametrize("aspect,columns", [("square", 113), ("terminal", 225)])
def test_32px_airplane_preflight_selects_canonical_presentation_and_pinned_dump_owner(aspect, columns):
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parent / "run_fl4359_headed_movement.py"),
            "--camera-mode", "plane_air",
            "--exact-camera-projection", "perspective",
            "--godot-window-mode", "fullscreen",
            "--resolution", "3600x2338",
            "--glyph-matrix-cell-px", "32",
            "--cell-aspect", aspect,
            "--product-time", "17.0",
            "--keep-forever",
            "--preflight-only",
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    godot_command = next(
        line.split("godot_command: ", 1)[1]
        for line in result.stdout.splitlines()
        if "godot_command: " in line
    )
    assert godot_command.count("--fl4431-canonical-presentation=3600x2252") == 1
    assert "--fullscreen" not in godot_command
    assert "--fl4359-capture-window-mode=fullscreen" not in godot_command
    assert "--fl4359-capture-window-mode=" not in godot_command
    assert "--resolution 3600x2338" not in godot_command
    assert "--fl4359-capture-resolution=" not in godot_command
    assert "--final-render-dump-keep-forever" in godot_command
    assert re.search(
        rf'"semantic_ansi_size":\s*\[\s*{columns},\s*71\s*\]',
        result.stdout,
    )


def test_canonical_airplane_receipt_gate_rejects_wrong_viewport_and_ansi():
    runner = _load_headed_runner()
    valid = {
        "canonical_presentation_requested": True,
        "presentation_surface": "canonical_locked",
        "capture_window_mode_requested": "canonical_locked",
        "canonical_presentation_contract": {
            "pass": True,
            "expected": {
                "presentation": {"w": 3600, "h": 2252},
                "ansi": {"w": 113, "h": 71},
            },
            "observed": {
                "viewport_size": {"w": 3600, "h": 2252},
                "pixel_camera_presentation_size": {"w": 3600, "h": 2252},
                "display_window_size": {"w": 3600, "h": 2252},
                "semantic_ansi_size": {"w": 113, "h": 71},
            },
        },
    }
    assert runner._canonical_airplane_presentation_receipt_accepts(
        valid,
        "plane_air",
    )
    invalid = json.loads(json.dumps(valid))
    invalid["canonical_presentation_contract"]["observed"]["viewport_size"] = {
        "w": 3600,
        "h": 2338,
    }
    assert not runner._canonical_airplane_presentation_receipt_accepts(
        invalid,
        "plane_air",
    )
    invalid_ansi = json.loads(json.dumps(valid))
    invalid_ansi["canonical_presentation_contract"]["observed"]["semantic_ansi_size"] = {
        "w": 113,
        "h": 74,
    }
    assert not runner._canonical_airplane_presentation_receipt_accepts(
        invalid_ansi,
        "plane_air",
    )


def test_canonical_locked_route_does_not_require_native_fullscreen_receipt():
    runner = _load_headed_runner()
    args = argparse.Namespace(
        camera_mode="plane_air",
        glyph_matrix_cell_px=32,
        godot_window_mode="fullscreen",
    )
    locked_receipt = {
        "canonical_presentation_requested": True,
        "presentation_surface": "canonical_locked",
        "capture_window_mode_requested": "canonical_locked",
        "canonical_presentation_contract": {
            "pass": True,
            "expected": {
                "presentation": {"w": 3600, "h": 2252},
                "ansi": {"w": 113, "h": 71},
            },
            "observed": {
                "viewport_size": {"w": 3600, "h": 2252},
                "pixel_camera_presentation_size": {"w": 3600, "h": 2252},
                "display_window_size": {"w": 3600, "h": 2252},
                "semantic_ansi_size": {"w": 113, "h": 71},
            },
        },
        "capture_window": {"actual_fullscreen": False},
    }
    assert runner._capture_window_receipt_accepts(locked_receipt, args)

    native_args = argparse.Namespace(
        camera_mode="plane_air",
        glyph_matrix_cell_px=20,
        godot_window_mode="fullscreen",
    )
    assert not runner._capture_window_receipt_accepts(locked_receipt, native_args)


def test_grounded_movement_preflight_owns_physical_route_without_airplane_checks():
    runner = _load_headed_runner()
    args = argparse.Namespace(
        camera_mode="perspective",
        run_classification=runner.RUN_CLASSIFICATION_MOVEMENT_PROOF,
        godot_window_mode="fullscreen",
        move_frames=120,
        turn_frames=8,
        settle_frames=2,
        after_frames=2,
        skip_jitter_analysis=False,
        jitter_capture_world_sample_stride=64,
        exact_camera_projection="",
        product_time=0.0,
    )
    preflight = runner._grounded_route_preflight(
        args,
        effective_turn_frames=8,
        effective_jitter_capture_frame_limit=5,
        godot_timeout_seconds=180.0,
    )
    assert preflight["ok"] is True
    assert preflight["failed_checks"] == []
    assert preflight["route_identity"] == runner.SCRIPTED_GROUNDED_ROUTE_IDENTITY
    assert "airplane_mode" not in preflight["checks"]

    args.run_classification = runner.RUN_CLASSIFICATION_DIAGNOSTIC_ONLY
    diagnostic = runner._grounded_route_preflight(
        args,
        effective_turn_frames=8,
        effective_jitter_capture_frame_limit=5,
        godot_timeout_seconds=180.0,
    )
    assert diagnostic["ok"] is True
    assert diagnostic["checks"]["jitter_metadata_enabled"] is True
    assert diagnostic["checks"]["diagnostic_nonproof_classification"] is True
    args.run_classification = runner.RUN_CLASSIFICATION_MOVEMENT_PROOF

    args.exact_camera_projection = "ortho"
    crossed = runner._grounded_route_preflight(
        args,
        effective_turn_frames=8,
        effective_jitter_capture_frame_limit=5,
        godot_timeout_seconds=180.0,
    )
    assert crossed["ok"] is False
    assert crossed["failed_checks"] == ["projection_matches_camera_mode"]


def test_context_preflight_allows_current_native_context_when_external_snapshot_is_stale(monkeypatch):
    runner = _load_headed_runner()
    calls = []

    def degraded_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({
                "ok": True,
                "fl": "FL-4359",
                "native": {
                    "authority": "native_failure_log",
                    "card": {"fl": "FL-4359"},
                },
                "external_context_channel": {
                    "status": "blocked",
                    "error": "projection_stale",
                },
            }),
            stderr="",
        )

    monkeypatch.setattr(runner.subprocess, "run", degraded_run)
    args = argparse.Namespace(
        preflight_only=False,
        fl4216_temporal_capture=False,
        run_classification=runner.RUN_CLASSIFICATION_MOVEMENT_PROOF,
    )
    ok, message = runner._context_preflight(args)
    assert ok is True
    assert "external=blocked:projection_stale" in message
    assert calls and calls[0][0][0:5] == [
        sys.executable,
        str(runner.ROOT / "scripts" / "analyze_runs.py"),
        "fl",
        "context",
        "FL-4359",
    ]
    assert "canonical_png_grid_capture_only" in calls[0][0][-2]
    assert "performance-only receipts" in calls[0][0][-2]
    assert calls[0][0][-1] == "--json"


def test_context_preflight_fresh_zero_reaches_next_preflight_seam(monkeypatch):
    runner = _load_headed_runner()
    calls = []

    def fresh_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({
                "ok": True,
                "fl": "FL-4216",
                "native": {
                    "authority": "native_failure_log",
                    "card": {"fl": "FL-4216"},
                },
            }),
            stderr="",
        )

    monkeypatch.setattr(runner.subprocess, "run", fresh_run)
    args = argparse.Namespace(
        preflight_only=False,
        fl4216_temporal_capture=True,
        run_classification=runner.RUN_CLASSIFICATION_DIAGNOSTIC_ONLY,
    )
    ok, message = runner._context_preflight(args)
    assert ok is True
    assert "FL-4216" in message
    assert calls and calls[0][0][4] == "FL-4216"
    assert "same-process temporal frames" in calls[0][0][-2]
    assert "FL-4359 movement proof" in calls[0][0][-2]


def test_context_preflight_blocks_nonzero_native_context(monkeypatch):
    runner = _load_headed_runner()
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            2,
            stdout=json.dumps({"ok": False, "error": "not_found"}),
            stderr="",
        ),
    )
    args = argparse.Namespace(
        preflight_only=False,
        fl4216_temporal_capture=False,
        run_classification=runner.RUN_CLASSIFICATION_MOVEMENT_PROOF,
    )

    ok, message = runner._context_preflight(args)

    assert ok is False
    assert "rc=2" in message


def test_context_preflight_blocks_malformed_native_receipt(monkeypatch):
    runner = _load_headed_runner()
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout="not-json",
            stderr="",
        ),
    )
    args = argparse.Namespace(
        preflight_only=False,
        fl4216_temporal_capture=False,
        run_classification=runner.RUN_CLASSIFICATION_MOVEMENT_PROOF,
    )

    ok, message = runner._context_preflight(args)

    assert ok is False
    assert "invalid JSON" in message


def test_context_preflight_is_not_run_for_local_preflight_only(monkeypatch):
    runner = _load_headed_runner()

    def unexpected_run(*_args, **_kwargs):
        raise AssertionError("context subprocess must not run for --preflight-only")

    monkeypatch.setattr(runner.subprocess, "run", unexpected_run)
    args = argparse.Namespace(
        preflight_only=True,
        fl4216_temporal_capture=False,
        run_classification=runner.RUN_CLASSIFICATION_MOVEMENT_PROOF,
    )
    assert runner._context_preflight(args) == (True, "preflight_only")


def test_fl4216_temporal_capture_cannot_use_movement_proof_classification():
    runner = _load_headed_runner()
    reason = runner._run_classification_contract_reason(
        runner.RUN_CLASSIFICATION_MOVEMENT_PROOF,
        False,
        True,
    )
    assert "FL-4216 temporal capture requires explicit diagnostic-only classification" in reason


def _build_frames():
    # 10 stable, 10 shimmer (owner flip), 80 scrolled-off cells.
    frame_a, frame_b = [], []
    for i in range(10):  # stable: identical product at identical anchor
        frame_a.append(_row(0, 100 + i, 100))
        frame_b.append(_row(1, 100 + i, 100))
    for i in range(10):  # shimmer: same anchor, different owner -> owner_instability
        frame_a.append(_row(0, 200 + i, 200, owner="terrain"))
        frame_b.append(_row(1, 200 + i, 200, owner="water"))
    for i in range(80):  # scrolled: A anchors with no nearby B match
        frame_a.append(_row(0, 300 + i, 300))
    return frame_a, frame_b


def test_scrolled_cells_excluded_from_jitter_denominator():
    frame_a, frame_b = _build_frames()
    result = mc.compare_frames(frame_a, frame_b)
    cls = result["classifications"]
    assert result["total_matched_cells"] == 100
    assert cls.get("different_world_sample", 0) == 80
    assert cls.get("stable", 0) == 10
    assert cls.get("owner_instability", 0) == 10
    # 20 comparable cells (100 - 80 scrolled - 0 no_match); 10 of them shimmer.
    assert result["comparable_cells"] == 20
    assert result["shimmer_cells"] == 10
    assert result["raw_excluded_counts"]["raw_matched_cells"] == 100
    assert result["raw_excluded_counts"]["comparable_cells"] == 20
    assert result["raw_excluded_counts"]["excluded_cells"] == 80
    assert result["raw_excluded_counts"]["different_world_sample_excluded_cells"] == 80
    # Corrected: shimmer over COMPARABLE cells = 10/20 = 50%, NOT 10/100 = 10%.
    assert abs(result["jitter_pct"] - 50.0) < 1e-6
    # The diluted legacy value (the false green) is preserved for transparency.
    assert abs(result["jitter_pct_over_total_legacy"] - 10.0) < 1e-6
    assert result["jitter_pct"] > result["jitter_pct_over_total_legacy"]


def test_valid_b55_product_anchor_rows_are_comparable():
    a = _row(0, 12, 12)
    b = _row(1, 12, 12)

    result = mc.compare_frames([a], [b])

    assert result["product_anchor_provenance_complete"] is True
    assert result["product_anchor_provenance_invalid_rows_a"] == 0
    assert result["product_anchor_provenance_invalid_rows_b"] == 0
    assert result["comparable_cells"] == 1


def test_historical_schema_cannot_impersonate_current_product_provenance():
    row = _row(0, 12, 12)
    row["capture_schema"] = "fl4359_sparse_surface_capture.v2"

    assert mc.has_current_product_anchor_provenance(row) is False
    assert mc.current_product_anchor_provenance_error(row) == "capture_schema_not_current"
    assert mc.summarize_product_anchor_provenance([row])["invalid_reason_counts"] == {
        "capture_schema_not_current": 1,
    }


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"final_product_world_anchor": None}, "missing_or_invalid_final_product_world_anchor"),
        ({"ansi_cell_world_anchor": None}, "missing_or_invalid_comparison_world_anchor"),
        (
            {"ansi_cell_world_anchor": {"x": 99.0, "y": 0.0, "z": 12.0}},
            "comparison_anchor_mismatches_final_product_world_anchor",
        ),
        ({"subcell_slot": 4}, "invalid_native_mask_slot"),
        ({"final_paint_slot_present": False}, "native_mask_slot_missing"),
        ({"final_paint_slot_actor_overlay": True}, "native_mask_slot_actor_overlay"),
        ({"final_paint_mask_unknown_bits": 1}, "native_mask_unknown_bits"),
        ({"final_paint_slot_glyph": None}, "native_mask_slot_glyph_missing"),
        ({"final_product_fg_rgb": None}, "final_product_colours_missing"),
    ],
)
def test_current_product_provenance_names_exact_failed_invariant(mutation, reason):
    row = _row(0, 12, 12)
    row.update(mutation)

    assert mc.has_current_product_anchor_provenance(row) is False
    assert mc.current_product_anchor_provenance_error(row) == reason


def test_b64_only_historical_row_cannot_contribute_product_anchor_evidence():
    a = _row(0, 13, 13)
    b = _row(1, 13, 13)
    b["comparison_anchor_source"] = "b64_selected_surface_stable_world_anchor"
    b["final_product_status"] = 0

    result = mc.compare_frames([a], [b])
    provenance = mc.summarize_product_anchor_provenance([a, b])

    assert result["product_anchor_provenance_complete"] is False
    assert result["product_anchor_provenance_invalid_rows_b"] == 1
    assert result["comparable_cells"] == 0
    assert result["motion_compensated_final_cell_instability_cells"] == 0
    assert provenance["complete"] is False
    assert provenance["invalid_current_b55_anchor_rows"] == 1
    assert provenance["invalid_reason_counts"] == {
        "invalid_or_missing_final_product_status": 1,
    }


def test_missing_product_anchor_provenance_turns_content_gate_red_without_snap_change():
    valid = _row(0, 14, 14)
    historical = dict(_row(1, 14, 14))
    historical.pop("final_product_world_anchor")
    historical.pop("comparison_anchor_source")
    historical.pop("final_product_status")
    result = mc.compare_frames([valid], [historical])
    snap_trace = {
        "camera_world": {"x": 0.0, "y": 5.0, "z": 10.0},
        "camera_world_unsnapped": {"x": 0.0, "y": 5.0, "z": 10.0},
        "camera_snap_error_px": {"x": 0.25, "y": 0.0},
        "style_post_shift_px": {"x": 0.25, "y": 0.0},
        "camera_projection_mode": 0,
        "camera_fov": 75.0,
        "camera_near": 0.1,
        "camera_far": 4000.0,
        "pixel_camera_snap_world": 0.0,
    }
    snap_valid = mc.summarize_shimmer_metrics({
        0: [{"frame_id": 0, "frame_trace": dict(snap_trace)}],
        1: [{"frame_id": 1, "frame_trace": dict(snap_trace, camera_world={"x": 1.0, "y": 5.0, "z": 10.0}, camera_world_unsnapped={"x": 1.0, "y": 5.0, "z": 10.0})}],
    })
    snap_historical = mc.summarize_shimmer_metrics({
        0: [{"frame_id": 0, "frame_trace": dict(snap_trace), **historical}],
        1: [{"frame_id": 1, "frame_trace": dict(snap_trace, camera_world={"x": 1.0, "y": 5.0, "z": 10.0}, camera_world_unsnapped={"x": 1.0, "y": 5.0, "z": 10.0}), **historical}],
    })
    assert snap_historical == snap_valid
    snap = {
        "frame_count": 2,
        "row_count": 2,
        "moving_frame_pairs": 1,
        "camera_motion_continuous_ratio": 1.0,
        "min_comparable_cells": 1,
        "min_nonzero_comparable_cells": 1,
        "total_comparable_cells": result["comparable_cells"],
        "comparable_pair_count": 1,
        "comparable_pairs_meeting_min": 1,
        "required_comparable_pairs": 1,
        "motion_compensated_final_cell_instability_pct": 0.0,
        "corrected_final_cell_oracle_valid": False,
        "product_anchor_provenance_complete": False,
        "pixel_camera_snap_world_observed_frames": 2,
        "pixel_camera_snap_world_nonzero_frames": 0,
        "camera_projection_modes": {0: 2},
        "projection_fields_present_frames": 2,
        "snap_contract_mismatch_frames": 0,
        "camera_snap_residual_nonzero_frames": 0,
        "style_post_shift_nonzero_frames": 0,
        "product_time_constant": True,
        "product_time_values": [0.35],
    }
    gates = mc.build_gate_summary(snap, _args(min_moving_frame_pairs=1))

    assert gates["gameplay_content_shimmer_bounded"]["pass"] is False
    assert gates["gameplay_content_shimmer_bounded"]["product_anchor_provenance_complete"] is False
    assert gates["gameplay_moving_camera_jitter_bounded"]["pass"] is True


def test_surface_split_diagnostics_separate_surface_and_paint_churn():
    a = _row(0, 10, 10)
    b = _row(1, 10, 10)
    a.update({"selected_surface_owner": 2, "selected_surface_object": 100})
    b.update({"selected_surface_owner": 2, "selected_surface_object": 101})
    b["cloud_shadow_phase"] = 65535

    result = mc.compare_frames([a], [b])

    assert result["classifications"]["dynamic_cloud_shadow_animation"] == 1
    assert result["surface_identity_split_diagnostics"]["counts"]["selected_surface_identity_churn_cells"] == 1
    assert result["surface_identity_split_diagnostics"]["counts"]["cloud_shadow_only_cells"] == 1
    # A selected-surface identity change is not double-counted as a post-
    # surface bounce. The retained semantic key owns this transition once.
    assert result["surface_identity_split_diagnostics"]["counts"].get("post_surface_bounce_cells", 0) == 0
    assert result["selected_surface_identity_churn_pct"] == 100.0
    assert result["post_surface_bounce_pct"] == 0.0

    paint_a = _row(0, 20, 20)
    paint_b = _row(1, 20, 20)
    paint_b["cloud_shadow_phase"] = 65535

    paint_result = mc.compare_frames([paint_a], [paint_b])
    paint_counts = paint_result["surface_identity_split_diagnostics"]["counts"]

    assert paint_result["classifications"]["dynamic_cloud_shadow_animation"] == 1
    assert paint_counts["cloud_shadow_only_cells"] == 1
    assert paint_counts.get("post_surface_bounce_cells", 0) == 0
    assert paint_result["cloud_shadow_only_pct"] == 100.0
    assert paint_result["post_surface_bounce_pct"] == 0.0


def test_water_phase_camera_drift_is_diagnostic_not_visible_shimmer():
    a = _row(0, 30, 30, owner="water", pid="water_30_30")
    b = _row(1, 30, 30, owner="water", pid="water_30_30")
    a["water_phase"] = 100
    b["water_phase"] = 200

    result = mc.compare_frames([a], [b])
    split_counts = result["surface_identity_split_diagnostics"]["counts"]

    assert result["classifications"]["stable"] == 1
    assert result["original_shimmer_cells"] == 0
    assert result["original_shimmer_pct"] == 0.0
    assert split_counts["water_phase_churn_cells"] == 1
    assert result["authored_animation_excluded_cells"] == 0


def test_fixed_product_time_water_payload_change_is_visible_shimmer():
    a = _row(0, 31, 31, owner="water", pid="water_31_31")
    b = _row(1, 31, 31, owner="water", pid="water_31_31")
    a["product_time_seconds"] = 17.0
    b["product_time_seconds"] = 17.0
    b["final_product_fg_rgb"] = [112, 70, 60]

    result = mc.compare_frames([a], [b])

    assert result["classifications"]["final_paint_or_glyph_instability"] == 1
    assert result["shimmer_cells"] == 1
    assert result["authored_animation_excluded_cells"] == 0


def test_advancing_product_time_authorizes_water_animation_exclusion():
    a = _row(0, 32, 32, owner="water", pid="water_32_32")
    b = _row(1, 32, 32, owner="water", pid="water_32_32")
    a["product_time_seconds"] = 17.0
    b["product_time_seconds"] = 17.5
    b["final_product_fg_rgb"] = [112, 70, 60]

    result = mc.compare_frames([a], [b])

    assert result["classifications"]["dynamic_water_animation"] == 1
    assert result["shimmer_cells"] == 0
    assert result["authored_animation_excluded_cells"] == 1


def test_motion_compensated_final_cell_signature_churn_counts_matched_anchor():
    a = _row(0, 40, 40, owner="terrain", pid="terrain_40_40")
    b = _row(1, 40, 40, owner="terrain", pid="terrain_40_40")
    a["final_product_glyph"] = 100
    b["final_product_glyph"] = 200
    a["final_paint_slot_glyph"] = 100
    b["final_paint_slot_glyph"] = 200
    a["final_product_fg_rgb"] = [100, 120, 140]
    b["final_product_fg_rgb"] = [132, 120, 140]

    result = mc.compare_frames([a], [b])

    assert result["classifications"]["final_paint_or_glyph_instability"] == 1
    assert result["surface_identity_split_diagnostics"]["counts"]["final_cell_signature_churn_cells"] == 1
    assert result["surface_identity_split_diagnostics"]["counts"]["post_surface_bounce_cells"] == 1
    assert result["final_cell_signature_churn_pct"] == 100.0
    assert result["motion_compensated_final_cell_instability_pct"] == 100.0
    assert result["screen_position_residual_world_max"] == 0.0
    attr = result["final_signature_churn_attribution"]
    assert attr["field_counts"]["final_product_glyph"] == 1
    assert attr["field_counts"]["final_product_fg_rgb_quant"] == 1
    assert attr["field_surface_counts"]["final_product_glyph:product:terrain"] == 1
    assert attr["field_surface_counts"]["final_product_fg_rgb_quant:product:terrain"] == 1


def test_motion_compensated_product_signature_ignores_unrelated_ansi_cell_header():
    a = _row(0, 41, 41, owner="terrain", pid="terrain_41_41")
    b = _row(1, 41, 41, owner="terrain", pid="terrain_41_41")
    a["glyph_id"] = 47
    b["glyph_id"] = 44
    a["fg_rgb"] = [255, 64, 0]
    b["fg_rgb"] = [0, 96, 255]
    a["bg_rgb"] = [0, 0, 0]
    b["bg_rgb"] = [255, 255, 255]

    result = mc.compare_frames([a], [b])

    assert result["motion_compensated_final_cell_instability_pct"] == 0.0
    assert result["surface_identity_split_diagnostics"]["counts"].get(
        "final_cell_signature_churn_cells", 0
    ) == 0


def test_continuous_product_colour_oracle_compares_exact_authored_sample():
    a = _row(0, 42, 42, owner="terrain", pid="terrain_42_42")
    b = _row(1, 42, 42, owner="terrain", pid="terrain_42_42")
    b["final_product_fg_rgb"] = [112, 70, 60]

    stability = mc.summarize_continuous_product_colour_stability([a], [b])

    assert stability["valid"] is True
    assert stability["counts"]["comparable_stable_semantic_samples"] == 1
    assert stability["counts"]["colour_changed_samples"] == 1
    assert stability["counts"]["first_divergence:product_staging_publication"] == 1
    assert stability["instability_pct"] == 100.0


def test_continuous_product_colour_oracle_names_first_changed_compiler_stage():
    a = _row(0, 45, 45, owner="terrain", pid="terrain_45_45")
    b = _row(1, 45, 45, owner="terrain", pid="terrain_45_45")
    b["final_product_terrain_colour_witness_bits"] = _terrain_colour_witness()
    b["final_product_terrain_colour_witness_bits"]["continuous_normal_cast"][3] = _f32_bits(0.5)
    b["final_product_fg_rgb"] = [112, 70, 60]

    stability = mc.summarize_continuous_product_colour_stability([a], [b])

    assert stability["valid"] is True
    assert stability["counts"]["first_divergence:continuous_normal_cast"] == 1
    assert stability["examples"][0]["first_divergence"] == "continuous_normal_cast"


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        (
            "final_product_pre_finalization_signature_bits",
            "product_record_construction",
        ),
        (
            "final_product_post_finalization_signature_bits",
            "product_finalization",
        ),
    ],
)
def test_continuous_product_colour_oracle_names_product_boundary(
    field: str, expected: str
):
    a = _row(0, 47, 47, owner="terrain", pid="terrain_47_47")
    b = _row(1, 47, 47, owner="terrain", pid="terrain_47_47")
    b[field] = list(b[field])
    b[field][2] = _packed_rgb([112, 70, 60])
    if field == "final_product_pre_finalization_signature_bits":
        b["final_product_post_finalization_signature_bits"] = list(b[field])
    b["final_product_fg_rgb"] = [112, 70, 60]

    stability = mc.summarize_continuous_product_colour_stability([a], [b])

    assert stability["valid"] is True
    assert stability["counts"][f"first_divergence:{expected}"] == 1
    assert stability["examples"][0]["first_divergence"] == expected


def test_continuous_product_colour_oracle_rejects_categorical_anchor_alias():
    a = _row(0, 43, 43, owner="terrain", pid="terrain_43_43")
    b = _row(1, 43, 43, owner="terrain", pid="terrain_43_43")
    # Both rows retain the same b55 cell-center anchor, but Product consumed a
    # different 8x8 paint sample. This is spatial variation, not a temporal
    # comparison, even when the resulting colour differs.
    b["final_product_continuous_sample_bits"] = [
        _f32_bits(43.1875), _f32_bits(0.0), _f32_bits(43.0625)
    ]
    b["final_product_fg_rgb"] = [112, 70, 60]

    stability = mc.summarize_continuous_product_colour_stability([a], [b])

    assert stability["counts"]["unmatched_exact_samples"] == 1
    assert stability["counts"].get("colour_changed_samples", 0) == 0
    assert stability["valid"] is False


def test_continuous_product_colour_oracle_fails_closed_without_sample_witness():
    a = _row(0, 44, 44, owner="terrain", pid="terrain_44_44")
    b = _row(1, 44, 44, owner="terrain", pid="terrain_44_44")
    b["final_product_continuous_sample_valid"] = False

    stability = mc.summarize_continuous_product_colour_stability([a], [b])

    assert stability["valid"] is False
    assert stability["invalid_terrain_rows_b"] == 1


def test_continuous_product_colour_oracle_fails_closed_without_compiler_witness():
    a = _row(0, 46, 46, owner="terrain", pid="terrain_46_46")
    b = _row(1, 46, 46, owner="terrain", pid="terrain_46_46")
    b["final_product_terrain_colour_witness_valid"] = False

    stability = mc.summarize_continuous_product_colour_stability([a], [b])

    assert stability["valid"] is False
    assert stability["invalid_colour_witness_rows_b"] == 1


def test_continuous_product_colour_oracle_fails_closed_without_finalization_witness():
    a = _row(0, 48, 48, owner="terrain", pid="terrain_48_48")
    b = _row(1, 48, 48, owner="terrain", pid="terrain_48_48")
    b.pop("final_product_post_finalization_signature_bits")

    stability = mc.summarize_continuous_product_colour_stability([a], [b])

    assert stability["valid"] is False
    assert stability["invalid_finalization_witness_rows_b"] == 1


def test_terminal_reprojection_uses_frame_a_world_point_and_later_matrix():
    frame_a = _terminal_reprojection_frame(0, 0.0, 16)
    frame_b = _terminal_reprojection_frame(1, 0.25, 20)

    result = mc.summarize_terminal_expected_reprojection([frame_a, frame_b])

    assert result["valid"] is True
    assert result["baseline_calibrated"] is False
    assert result["acceptance_threshold_authorized"] is False
    assert result["counts"]["eligible_source_features"] == 1
    assert result["counts"]["comparable_terminal_displacements"] == 1
    assert result["counts"]["comparable_expected_cells"] == 2
    assert result["counts"].get("expected_cell_mismatches", 0) == 0
    assert result["expected_cell_address_pass"] is True
    assert result["max_residual_px"] == pytest.approx(0.0)


@pytest.mark.parametrize("cell", [(8, 8), (8, 16), (10, 20), (16, 16), (16, 32), (20, 20), (32, 32)])
@pytest.mark.parametrize("raster_scale", [1, 2])
def test_five_frame_rotation_reprojection_across_cell_aspects_and_rasters(cell, raster_scale):
    """Synthetic sensitivity, not the operator's canonical camera trajectory.

    A stationary point at (0,0,-4) under pure view yaw projects to
    x = width/2 * (1 + tan(yaw)). Compare that independent analytic path
    with observed terminal pixels; freezing the output must remain detectable.
    """
    width = height = 64 * raster_scale
    cols, rows_count = math.ceil(width / cell[0]), math.ceil(height / cell[1])

    def sequence(freeze=False):
        frames = []
        for index in range(5):
            yaw = math.atan(index / 8.0)
            c, s = math.cos(yaw), math.sin(yaw)
            matrix = [c, 0, -1.1*s, -s, 0, 1, 0, 0,
                      -s, 0, -1.1*c, -c, 0, 0, -0.2, 0]
            x = (32 if freeze else 32 + 4*index) * raster_scale
            y = 32 * raster_scale
            cx, cy = x // cell[0], y // cell[1]
            frame = _terminal_reprojection_frame(index, 0.0, x)
            for slot, row in enumerate(frame):
                row["frame_trace"].update(world_to_clip_matrix=matrix,
                    target_size=[width, height], cell_size=list(cell),
                    semantic_ansi_size=[cols, rows_count])
                row["oracle_world_position"] = [0.0, 0.0, -4.0]
                row["screen_cell"] = [cx, cy]
                row["sample_index"] = (2*cy + (slot >> 1)) * (2*cols) + 2*cx + (slot & 1)
                for field in ("sum_y", "min_y", "max_y"):
                    row["final_paint_terminal_mask_" + field] = y
            expected_cx = ((32 + 4*index) * raster_scale) // cell[0]
            if freeze and expected_cx != cx:
                # Keep an independent oracle observation at the expected cell.
                # Without it this is missing evidence, not a measured bad move.
                reference_rows = copy.deepcopy(frame)
                for slot, row in enumerate(reference_rows):
                    row["screen_cell"] = [expected_cx, cy]
                    row["sample_index"] = (2*cy + (slot >> 1)) * (2*cols) + 2*expected_cx + (slot & 1)
                    row["final_paint_glyph"] = row["final_paint_slot_glyph"] = 0
                    row["final_paint_elected_identity_glyph"] = 0
                    row["final_paint_terminal_mask_ink_count"] = 0
                    row["final_paint_terminal_occurrence_valid"] = False
                frame.extend(reference_rows)
            frames.append(frame)
        return frames

    correct = mc.summarize_terminal_expected_reprojection(sequence())
    assert correct["valid"] is True, correct
    assert correct["max_residual_px"] == pytest.approx(0.0, abs=1e-8)
    assert correct["expected_cell_address_pass"] is True
    frozen = mc.summarize_terminal_expected_reprojection(sequence(freeze=True))
    assert frozen["max_residual_px"] >= 4 * raster_scale, frozen
    # Coarse cells may conceal a wrong displacement within the SAME cell.
    # Pixel residual must survive even when the categorical cell gate is green.
    assert frozen["acceptance_threshold_authorized"] is False


def test_terminal_subcell_index_uses_producer_row_major_layout():
    contract = {"semantic_ansi_size": [3, 2]}
    expected = {
        (0, 0, 0): 0,
        (0, 0, 1): 1,
        (0, 0, 2): 6,
        (0, 0, 3): 7,
        (2, 1, 0): 16,
        (2, 1, 1): 17,
        (2, 1, 2): 22,
        (2, 1, 3): 23,
    }
    for (cell_x, cell_y, slot), sample_index in expected.items():
        assert (
            mc._terminal_sample_index_for_cell(
                cell_x, cell_y, slot, contract
            )
            == sample_index
        )
        assert mc._terminal_cell_from_sample_index(sample_index, contract) == (
            cell_x,
            cell_y,
        )


def test_terminal_reprojection_detects_freeze_before_jump_calibration():
    frame_a = _terminal_reprojection_frame(0, 0.0, 16)
    frame_b = _terminal_reprojection_frame(1, 0.25, 16)

    result = mc.summarize_terminal_expected_reprojection([frame_a, frame_b])

    assert result["valid"] is True
    assert result["max_residual_px"] == pytest.approx(4.0)
    assert result["examples"][0]["trajectory"][0]["residual"] == pytest.approx(
        (-4.0, 0.0)
    )


def test_terminal_reprojection_fails_exact_expected_ansi_cell_address():
    frame_a = _terminal_reprojection_two_cell_frame(0, -0.5, 16)
    frame_b = _terminal_reprojection_two_cell_frame(1, 0.5, 16)

    result = mc.summarize_terminal_expected_reprojection([frame_a, frame_b])

    assert result["valid"] is True
    assert result["expected_cell_address_pass"] is False
    assert result["counts"]["comparable_expected_cells"] == 2
    assert result["counts"]["expected_cell_matches"] == 1
    assert result["counts"]["expected_cell_mismatches"] == 1
    trajectory = result["examples"][0]["trajectory"][0]
    assert trajectory["expected_cell"] == (1, 0)
    assert trajectory["actual_cell"] == (0, 0)


def test_terminal_reprojection_separates_mask_morphology_from_position():
    frame_a = _terminal_reprojection_frame(0, 0.0, 16)
    frame_b = _terminal_reprojection_frame(1, 0.25, 20)
    for row in frame_b:
        row["final_paint_terminal_mask_digest"] = 0x5678

    result = mc.summarize_terminal_expected_reprojection([frame_a, frame_b])

    assert result["valid"] is True
    assert result["counts"]["mask_morphology_changed_displacements"] == 1
    assert result["excluded"]["mask_morphology_changed"] == 1
    assert result["counts"].get("comparable_terminal_displacements", 0) == 0
    assert result["max_residual_px"] == 0.0


def test_terminal_reprojection_excludes_a_validly_blank_terminal_glyph():
    frame_a = _terminal_reprojection_frame(0, 0.0, 16)
    frame_b = _terminal_reprojection_frame(1, 0.25, 20)
    for row in frame_a + frame_b:
        row["final_product_exact_identity"]["cell_mask_winner_glyph"] = 32
        row["final_paint_elected_identity_glyph"] = 32
        row["final_paint_glyph"] = 32
        row["final_paint_slot_glyph"] = 32
        row["final_paint_terminal_occurrence_valid"] = False
        row["final_paint_terminal_mask_ink_count"] = 0

    result = mc.summarize_terminal_expected_reprojection([frame_a, frame_b])

    assert result["valid"] is False
    assert result["invalid"] == {}
    assert result["excluded"]["blank_terminal_glyph"] == 2


def test_terminal_reprojection_keeps_nonblank_occurrence_rejection_red():
    frame_a = _terminal_reprojection_frame(0, 0.0, 16)
    frame_b = _terminal_reprojection_frame(1, 0.25, 20)
    for row in frame_b:
        row["final_paint_terminal_occurrence_valid"] = False

    result = mc.summarize_terminal_expected_reprojection([frame_a, frame_b])

    assert result["valid"] is False
    assert result["invalid"]["terminal_occurrence_invalid"] == 1
    assert result["invalid"][
        "terminal_occurrence_invalid:host_validity_rejected_unclassified"
    ] == 1


def test_terminal_reprojection_metric_is_not_truncated_with_examples():
    frame_a = _terminal_reprojection_frame(0, 0.0, 16)
    frame_b = _terminal_reprojection_frame(1, 0.25, 16)

    result = mc.summarize_terminal_expected_reprojection(
        [frame_a, frame_b], details_limit=0
    )

    assert result["valid"] is True
    assert result["examples"] == []
    assert result["max_residual_px"] == pytest.approx(4.0)


def test_terminal_reprojection_fails_closed_on_missing_terminal_token():
    frame_a = _terminal_reprojection_frame(0, 0.0, 16)
    frame_b = _terminal_reprojection_frame(1, 0.25, 20)
    for row in frame_b:
        row["final_paint_elected_identity_key_d"][0] = _f32_bits(1.0)

    result = mc.summarize_terminal_expected_reprojection([frame_a, frame_b])

    assert result["valid"] is False
    assert result["invalid"]["terminal_omission"] == 1


def test_terminal_reprojection_fails_closed_on_duplicate_terminal_token():
    frame_a = _terminal_reprojection_frame(0, 0.0, 16)
    frame_b = _terminal_reprojection_frame(1, 0.25, 20)
    duplicate = _terminal_reprojection_frame(1, 0.25, 24)
    for row in frame_a + frame_b + duplicate:
        row["frame_trace"]["target_size"] = [64, 32]
        row["frame_trace"]["semantic_ansi_size"] = [2, 1]
    for rows in (frame_a, frame_b):
        for slot, row in enumerate(rows):
            row["sample_index"] = _producer_subcell_index(2, 0, 0, slot)
    for slot, row in enumerate(duplicate):
        row["screen_cell"] = [1, 0]
        row["sample_index"] = _producer_subcell_index(2, 1, 0, slot)
        row["final_paint_terminal_mask_sum_x"] = 40
        row["final_paint_terminal_mask_min_x"] = 40
        row["final_paint_terminal_mask_max_x"] = 40

    result = mc.summarize_terminal_expected_reprojection(
        [frame_a, frame_b + duplicate]
    )

    assert result["valid"] is False
    assert result["invalid"]["terminal_duplicate_or_ambiguous"] == 1


def test_terminal_reprojection_excludes_semantic_token_without_exact_point_ownership():
    frame_a = _terminal_reprojection_frame(0, 0.0, 16)
    frame_b = _terminal_reprojection_frame(1, 0.25, 20)
    mismatched_anchor_bits = [_f32_bits(8.0), _f32_bits(0.0), _f32_bits(0.0)]
    for row in frame_a + frame_b:
        row["final_paint_elected_identity_key_d"][:3] = mismatched_anchor_bits

    result = mc.summarize_terminal_expected_reprojection([frame_a, frame_b])

    # The semantic Product anchor is intentionally not compared with W_A.
    # Changing it cannot invalidate the independent b43 categorical bridge.
    assert result["valid"] is True
    assert result["counts"]["eligible_source_features"] == 1
    assert result["expected_cell_address_pass"] is True


def test_terminal_reprojection_rejects_screen_cell_sample_slot_disagreement():
    frame_a = _terminal_reprojection_frame(0, 0.0, 16)
    frame_b = _terminal_reprojection_frame(1, 0.25, 20)
    frame_b[3]["subcell_slot"] = 2

    result = mc.summarize_terminal_expected_reprojection([frame_a, frame_b])

    assert result["valid"] is False
    assert result["invalid"]["screen_cell_sample_slot_mismatch"] == 1


def test_terminal_reprojection_rejects_out_of_bounds_screen_cell():
    frame_a = _terminal_reprojection_frame(0, 0.0, 16)
    frame_b = _terminal_reprojection_frame(1, 0.25, 20)
    for row in frame_b:
        row["screen_cell"] = [1, 0]

    result = mc.summarize_terminal_expected_reprojection([frame_a, frame_b])

    assert result["valid"] is False
    assert result["invalid"]["screen_cell_out_of_bounds"] == 4


def test_terminal_reprojection_rejects_invalid_matrix():
    frame_a = _terminal_reprojection_frame(0, 0.0, 16)
    frame_b = _terminal_reprojection_frame(1, 0.25, 20)
    for row in frame_b:
        row["frame_trace"]["world_to_clip_matrix"] = [1.0] * 15

    result = mc.summarize_terminal_expected_reprojection([frame_a, frame_b])

    assert result["valid"] is False
    assert result["invalid"]["invalid_world_to_clip_matrix"] == 1


def test_cell_mask_owner_stability_matches_reprojected_cell_by_mask_anchor():
    frame_a = _cell_rows(0, 10, 20, (100.0, 5.0, -40.0), glyph=35)
    frame_b = _cell_rows(1, 13, 22, (100.0, 5.0, -40.0), glyph=35)

    stability = mc.summarize_cell_mask_owner_stability(frame_a, frame_b)

    assert stability["invalid_cell_count"] == 0
    assert stability["counts"]["comparable_cells"] == 1
    assert stability["counts"]["stable_cells"] == 1
    assert stability["counts"].get("unstable_cells", 0) == 0
    assert stability["instability_pct"] == 0.0


def test_cell_mask_owner_stability_uses_elected_quarter_product_anchor():
    rows = _cell_rows(0, 10, 20, (100.0, 5.0, -40.0), glyph=35)
    raw_vsb_anchor = (900.0, 3.0, -800.0)
    for row in rows:
        identity = row["final_product_exact_identity"]
        raw_bits = [_f32_bits(value) for value in raw_vsb_anchor]
        identity["cell_mask_winner_key"]["key_d"][:3] = raw_bits
        identity["cell_mask_winner_source_semantic_key"]["key_d"][:3] = raw_bits
        identity["cell_mask_winner_source_anchor_bits"] = raw_bits

    observations, invalid, excluded = mc._cell_mask_observations(rows)

    assert invalid == {}
    assert excluded == {}
    assert observations[0]["anchor"] == {"x": 100.0, "y": 5.0, "z": -40.0}


def test_analytic_water_cells_sharing_raw_anchor_are_not_cross_paired():
    raw_vsb_anchor = (900.0, 3.0, -800.0)

    def with_raw_anchor(rows: list[dict]) -> list[dict]:
        for row in rows:
            identity = row["final_product_exact_identity"]
            raw_bits = [_f32_bits(value) for value in raw_vsb_anchor]
            identity["cell_mask_winner_key"]["key_d"][:3] = raw_bits
            identity["cell_mask_winner_source_semantic_key"]["key_d"][:3] = raw_bits
            identity["cell_mask_winner_source_anchor_bits"] = raw_bits
        return rows

    frame_a = with_raw_anchor(
        _cell_rows(0, 10, 20, (100.0, 3.0, -40.0), glyph=35, owner="water")
        + _cell_rows(0, 20, 20, (200.0, 3.0, -40.0), glyph=37, owner="water")
    )
    # Reverse the screen ordering while preserving each semantic water cell.
    # A matcher using the shared raw VSB anchor pairs both cells incorrectly.
    frame_b = with_raw_anchor(
        _cell_rows(1, 13, 22, (200.0, 3.0, -40.0), glyph=37, owner="water")
        + _cell_rows(1, 23, 22, (100.0, 3.0, -40.0), glyph=35, owner="water")
    )

    stability = mc.summarize_cell_mask_owner_stability(frame_a, frame_b)

    assert stability["counts"]["comparable_cells"] == 2
    assert stability["counts"]["stable_cells"] == 2
    assert stability["counts"].get("winner_glyph_changed_cells", 0) == 0


def test_center_terrain_mask_owner_still_uses_key_anchor():
    rows = _cell_rows(
        0, 10, 20, (100.0, 5.0, -40.0), glyph=35, source_kind="center_terrain"
    )
    rows[0]["final_product_world_anchor"] = {"x": 999.0, "y": 9.0, "z": 999.0}

    observations, invalid, excluded = mc._cell_mask_observations(rows)

    assert invalid == {}
    assert excluded == {}
    assert observations[0]["anchor"] == {"x": 100.0, "y": 5.0, "z": -40.0}


def test_center_sky_mask_owner_is_valid_but_excluded_from_world_anchor_matching():
    rows = _cell_rows(
        0, 10, 20, (0.0, 0.0, 1.0), glyph=42,
        source_kind="center_sky", owner="sky",
    )

    observations, invalid, excluded = mc._cell_mask_observations(rows)

    assert observations == []
    assert invalid == {}
    assert excluded == {"sky_direction_domain": 1}


def test_b43_elected_identity_accepts_explicit_center_sky_source_kind():
    row = _terminal_reprojection_frame(0, 0.0, 16)[0]
    row["final_paint_elected_identity_validity"] = 0x1 | (5 << 16) | (2 << 24)

    witness = mc.b43_elected_identity_witness(row)

    assert witness["available"] is True
    assert witness["source_kind"] == "center_sky"
    assert witness["source_kind_code"] == 2


def test_b43_elected_identity_accepts_explicit_center_surface_source_kind():
    row = _terminal_reprojection_frame(0, 0.0, 16)[0]
    row["final_paint_elected_identity_validity"] = 0x1 | (5 << 16) | (3 << 24)

    witness = mc.b43_elected_identity_witness(row)

    assert witness["available"] is True
    assert witness["source_kind"] == "center_surface"
    assert witness["source_kind_code"] == 3


def test_center_surface_mask_owner_uses_its_exact_world_anchor():
    rows = _cell_rows(
        0, 10, 20, (18.0, 7.0, -12.0), glyph=35,
        source_kind="center_surface", owner="static_mesh",
    )

    observations, invalid, excluded = mc._cell_mask_observations(rows)

    assert invalid == {}
    assert excluded == {}
    assert len(observations) == 1
    assert observations[0]["source_owner"] == "static_mesh"
    assert observations[0]["anchor"] == {"x": 18.0, "y": 7.0, "z": -12.0}


def test_center_surface_mask_owner_rejects_water_owner():
    rows = _cell_rows(
        0, 10, 20, (18.0, 7.0, -12.0), glyph=35,
        source_kind="center_surface", owner="water",
    )

    observations, invalid, excluded = mc._cell_mask_observations(rows)

    assert observations == []
    assert invalid == {"center_source_owner_not_nonterrain_surface": 1}
    assert excluded == {}


def test_cell_mask_owner_stability_detects_glyph_change_without_polluting_product_metric():
    frame_a = _cell_rows(0, 10, 20, (100.0, 5.0, -40.0), glyph=35)
    frame_b = _cell_rows(1, 13, 22, (100.0, 5.0, -40.0), glyph=37)
    for slot, row in enumerate(frame_a):
        row["ansi_cell_world_anchor"] = {"x": 10.0 + slot, "y": 0.0, "z": 20.0}
        row["final_product_world_anchor"] = dict(row["ansi_cell_world_anchor"])
    for slot, row in enumerate(frame_b):
        row["ansi_cell_world_anchor"] = {"x": 10.0 + slot, "y": 0.0, "z": 20.0}
        row["final_product_world_anchor"] = dict(row["ansi_cell_world_anchor"])
        row["final_product_exact_identity"]["b55"] = dict(
            frame_a[slot]["final_product_exact_identity"]["b55"]
        )
        row["final_product_exact_identity"]["b64"] = dict(
            frame_a[slot]["final_product_exact_identity"]["b64"]
        )
        row["final_product_exact_identity"]["b60_sidecar"] = {
            "output_r1": list(
                frame_a[slot]["final_product_exact_identity"]["b60_sidecar"]["output_r1"]
            )
        }
        row["final_product_source_key"] = frame_a[slot]["final_product_source_key"]
        row["final_product_matcell_packed"] = frame_a[slot]["final_product_matcell_packed"]

    result = mc.compare_frames(frame_a, frame_b)

    assert result["motion_compensated_final_cell_instability_pct"] == 0.0
    stability = result["cell_mask_owner_stability"]
    assert stability["counts"]["comparable_cells"] == 1
    assert stability["counts"]["winner_glyph_changed_cells"] == 1
    assert stability["counts"]["unstable_cells"] == 1
    assert stability["instability_pct"] == 100.0


def test_cell_mask_owner_stability_fails_closed_on_invalid_quarter_product_anchor():
    frame_a = _cell_rows(0, 10, 20, (100.0, 5.0, -40.0), glyph=35)
    frame_b = _cell_rows(1, 13, 22, (100.0, 5.0, -40.0), glyph=35)
    frame_b[0]["final_product_world_anchor"] = None

    stability = mc.summarize_cell_mask_owner_stability(frame_a, frame_b)

    assert stability["counts"]["comparable_cells"] == 0
    assert stability["invalid_cell_count"] == 1
    assert stability["invalid_cells_b"] == {
        "winner_elected_product_anchor_invalid": 1
    }


def test_cell_mask_owner_stability_names_product_owner_mismatch():
    frame_a = _cell_rows(0, 10, 20, (100.0, 5.0, -40.0), glyph=35)
    frame_b = _cell_rows(1, 13, 22, (100.0, 5.0, -40.0), glyph=35)
    frame_b[0]["final_product_owner_name"] = "water"

    stability = mc.summarize_cell_mask_owner_stability(frame_a, frame_b)

    assert stability["counts"]["comparable_cells"] == 0
    assert stability["invalid_cells_b"] == {
        "winner_elected_product_owner_mismatch:terrain->water": 1
    }


def test_cell_mask_owner_stability_fails_closed_on_invalid_center_key_anchor():
    frame_a = _cell_rows(
        0, 10, 20, (100.0, 5.0, -40.0), glyph=35, source_kind="center_terrain"
    )
    frame_b = _cell_rows(
        1, 13, 22, (100.0, 5.0, -40.0), glyph=35, source_kind="center_terrain"
    )
    for row in frame_b:
        row["final_product_exact_identity"]["cell_mask_winner_key"]["key_d"][0] = "not-u32"
        row["final_product_exact_identity"]["cell_mask_winner_source_semantic_key"]["key_d"][0] = "not-u32"
        row["final_product_exact_identity"]["cell_mask_winner_source_anchor_bits"][0] = "not-u32"

    stability = mc.summarize_cell_mask_owner_stability(frame_a, frame_b)

    assert stability["counts"]["comparable_cells"] == 0
    assert stability["invalid_cells_b"] == {"winner_key_anchor_invalid": 1}


def test_cell_mask_owner_stability_reports_center_quarter_handoff_separately():
    frame_a = _cell_rows(
        0, 10, 20, (100.0, 5.0, -40.0), glyph=35, source_kind="center_terrain"
    )
    frame_b = _cell_rows(
        1, 13, 22, (100.0, 5.0, -40.0), glyph=35, source_kind="quarter_slot"
    )

    stability = mc.summarize_cell_mask_owner_stability(frame_a, frame_b)

    assert stability["counts"]["winner_source_kind_changed_cells"] == 1
    assert stability["counts"][
        "source_kind_transition:center_terrain->quarter_slot"
    ] == 1
    assert stability["counts"]["semantic_owner_changed_cells"] == 1
    assert stability["counts"]["unstable_cells"] == 0
    assert stability["counts"][
        "glyph_stable_source_kind_transition:center_terrain->quarter_slot"
    ] == 1
    assert stability["counts"][
        "source_owner_transition:terrain->terrain"
    ] == 1


def test_cell_mask_owner_stability_attributes_center_glyph_change_exactly():
    frame_a = _cell_rows(
        0, 10, 20, (100.0, 5.0, -40.0), glyph=35, source_kind="center_terrain"
    )
    frame_b = _cell_rows(
        1, 13, 22, (100.0, 5.0, -40.0), glyph=37, source_kind="center_terrain"
    )

    stability = mc.summarize_cell_mask_owner_stability(frame_a, frame_b)

    assert stability["counts"][
        "glyph_changed_source_kind_transition:center_terrain->center_terrain"
    ] == 1
    assert stability["counts"][
        "glyph_changed_source_owner_transition:terrain->terrain"
    ] == 1
    assert stability["counts"][
        "glyph_changed_source_kind_priority_transition:center_terrain:850->center_terrain:850"
    ] == 1
    assert stability["counts"]["glyph_changed_with_source_kind_stable_cells"] == 1
    assert stability["counts"]["glyph_changed_with_source_key_stable_cells"] == 1
    assert stability["counts"][
        "glyph_changed_source_kind_key_state:center_terrain->center_terrain:stable"
    ] == 1


def test_cell_mask_owner_stability_excludes_sky_null_anchor_domain():
    frame_a = _cell_rows(
        0, 10, 20, (0.0, 0.0, 0.0), glyph=42, source_kind="quarter_slot"
    )
    frame_b = _cell_rows(
        1, 13, 22, (0.0, 0.0, 0.0), glyph=46, source_kind="quarter_slot"
    )
    for row in frame_a + frame_b:
        identity = row["final_product_exact_identity"]
        identity["cell_mask_winner_priority"] = 840
        identity["cell_mask_winner_key"]["key_a"][0] = 6
        identity["cell_mask_winner_source_semantic_key"]["key_a"][0] = 6

    stability = mc.summarize_cell_mask_owner_stability(frame_a, frame_b)

    assert stability["invalid_cell_count"] == 0
    assert stability["counts"]["comparable_cells"] == 0
    assert stability["counts"]["excluded_sky_direction_anchor_cells_a"] == 1
    assert stability["counts"]["excluded_sky_direction_anchor_cells_b"] == 1
    assert stability["excluded_cells_a"] == {"sky_direction_domain": 1}
    assert stability["excluded_cells_b"] == {"sky_direction_domain": 1}


def test_current_cell_mask_membership_replay_is_explicitly_invalid():
    result = mc.compare_frames(
        _cell_rows(0, 10, 20, (100.0, 5.0, -40.0)),
        _cell_rows(1, 11, 20, (100.0, 5.0, -40.0)),
    )

    membership = result["cell_mask_candidate_membership"]
    assert membership["valid"] is False
    assert membership["invalid_reason"] == (
        "current_capture_missing_cell_owned_center_nominee_token"
    )


def test_cell_mask_winner_decomposition_names_key_glyph_priority_and_slot_changes():
    a = _row(0, 42, 42, owner="terrain", pid="terrain_42_42")
    b = _row(1, 42, 42, owner="terrain", pid="terrain_42_42")
    b_winner = b["final_product_exact_identity"]
    b_winner["cell_mask_winner_key"]["key_b"][1] += 1
    b_winner["cell_mask_winner_source_semantic_key"]["key_b"][1] += 1
    b_winner["cell_mask_winner_glyph"] = 11
    b_winner["cell_mask_winner_priority"] = 801
    b_winner["cell_mask_winner_slot"] = 2
    b["final_paint_slot_glyph"] = 11

    result = mc.compare_frames([a], [b])
    decomposition = result["cell_mask_winner_decomposition"]

    assert decomposition["diagnostic_comparable_cells"] == 1
    assert decomposition["counts"]["cell_mask_glyph_changed_cells"] == 1
    assert decomposition["counts"]["cell_mask_winner_key_changed_cells"] == 1
    assert decomposition["counts"]["cell_mask_same_winner_key_glyph_changed_cells"] == 0
    assert decomposition["counts"]["cell_mask_winner_priority_changed_cells"] == 1
    assert decomposition["counts"]["cell_mask_winner_slot_changed_cells"] == 1
    assert decomposition["counts"]["discrete_glyph_or_mask_changed_cells"] == 1
    assert decomposition["winner_priority_transition_counts"] == {"800->801": 1}
    assert decomposition["glyph_change_priority_transition_counts"] == {
        "800->801": 1
    }
    assert decomposition["glyph_change_priority_transition_surface_counts"] == {
        "800->801:product:terrain": 1
    }
    assert decomposition["glyph_change_mechanism_counts"] == {
        "800->801:key_changed:slot_changed": 1
    }
    assert decomposition["glyph_change_key_field_transition_counts"] == {
        "800->801:key_b[1]": 1
    }
    assert decomposition["surface_counts"]["cell_mask_winner_key_change"]["product:terrain"] == 1
    assert decomposition["winner_key_field_counts"] == {"key_b[1]": 1}
    assert decomposition["winner_key_field_surface_counts"] == {
        "key_b[1]:product:terrain": 1
    }
    assert decomposition["counts"]["cell_mask_winner_relation_available_cells"] == 1
    assert decomposition["counts"]["cell_mask_winner_observed_product_relation_changed_cells"] == 1
    assert decomposition["counts"]["cell_mask_key_changed_with_sibling_winner_cells"] == 1
    assert decomposition["counts"]["cell_mask_key_changed_with_observed_product_winner_cells"] == 0


def test_cell_mask_winner_decomposition_detects_same_key_glyph_mutation():
    a = _row(0, 43, 43, owner="terrain", pid="terrain_43_43")
    b = _row(1, 43, 43, owner="terrain", pid="terrain_43_43")
    b["final_product_exact_identity"]["cell_mask_winner_glyph"] = 12
    b["final_paint_slot_glyph"] = 12

    decomposition = mc.compare_frames([a], [b])["cell_mask_winner_decomposition"]

    assert decomposition["counts"]["cell_mask_glyph_changed_cells"] == 1
    assert decomposition["counts"]["cell_mask_winner_key_changed_cells"] == 0
    assert decomposition["counts"]["cell_mask_same_winner_key_glyph_changed_cells"] == 1
    assert decomposition["winner_priority_transition_counts"] == {"800->800": 1}
    assert decomposition["glyph_change_priority_transition_counts"] == {
        "800->800": 1
    }
    assert decomposition["glyph_change_mechanism_counts"] == {
        "800->800:key_stable:slot_stable": 1
    }
    assert decomposition["glyph_change_key_field_transition_counts"] == {}


def test_cell_mask_winner_decomposition_separates_product_glyph_change():
    a = _row(0, 44, 44, owner="terrain", pid="terrain_44_44")
    b = _row(1, 44, 44, owner="terrain", pid="terrain_44_44")
    b["final_product_glyph"] = 13

    decomposition = mc.compare_frames([a], [b])["cell_mask_winner_decomposition"]

    assert decomposition["counts"]["final_product_glyph_changed_cells"] == 1
    assert decomposition["counts"]["cell_mask_glyph_changed_cells"] == 0
    assert decomposition["counts"]["discrete_glyph_or_mask_changed_cells"] == 1


def test_cell_mask_winner_decomposition_separates_colour_only_response():
    a = _row(0, 46, 46, owner="terrain", pid="terrain_46_46")
    b = _row(1, 46, 46, owner="terrain", pid="terrain_46_46")
    b["final_product_fg_rgb"] = [160, 70, 60]
    b["final_product_bg_rgb"] = [30, 90, 20]

    decomposition = mc.compare_frames([a], [b])["cell_mask_winner_decomposition"]

    assert decomposition["counts"]["camera_dependent_colour_only_cells"] == 1
    assert decomposition["counts"]["discrete_glyph_or_mask_changed_cells"] == 0


def test_cell_mask_winner_decomposition_fails_closed_on_missing_winner_provenance():
    a = _row(0, 47, 47, owner="terrain", pid="terrain_47_47")
    b = _row(1, 47, 47, owner="terrain", pid="terrain_47_47")
    del b["final_product_exact_identity"]

    decomposition = mc.compare_frames([a], [b])["cell_mask_winner_decomposition"]

    assert decomposition["diagnostic_comparable_cells"] == 1
    assert decomposition["counts"]["cell_mask_winner_provenance_unavailable_cells"] == 1
    assert decomposition["counts"]["camera_dependent_colour_only_cells"] == 0


def test_mountain_churn_attribution_names_terrain_detail_field():
    a = _row(0, 45, 45, owner="terrain", pid="terrain_45_45")
    b = _row(1, 45, 45, owner="terrain", pid="terrain_45_45")
    a["material_role"] = "cliff_rock"
    b["material_role"] = "cliff_rock"
    a["final_product_material_family"] = 2
    b["final_product_material_family"] = 2
    a["final_product_material_id"] = 5
    b["final_product_material_id"] = 5
    a["terrain_product_detail"] = (1 << 3) | (1 << 16)
    b["terrain_product_detail"] = (1 << 3) | (2 << 16)

    result = mc.compare_frames([a], [b])

    assert result["classifications"]["terrain_product_detail_churn"] == 1
    attr = result["final_signature_churn_attribution"]
    assert attr["terrain_detail_field_counts"]["material_tone_band"] == 1
    assert attr["terrain_detail_field_surface_counts"]["material_tone_band:mountain:cliff_rock"] == 1


def test_screen_cell_signature_counts_visible_ink_without_world_product_churn():
    a = _row(0, 46, 46, owner="terrain", pid="terrain_46_46")
    b = _row(1, 46, 46, owner="terrain", pid="terrain_46_46")
    a["glyph_id"] = 35
    b["glyph_id"] = 35
    a["fg_rgb"] = [120, 120, 120]
    b["fg_rgb"] = [120, 120, 120]
    a["bg_rgb"] = [20, 20, 20]
    b["bg_rgb"] = [20, 20, 20]
    a["final_ink_fg"] = 200
    a["final_ink_pixels"] = 400
    b["final_ink_fg"] = 10
    b["final_ink_pixels"] = 380

    result = mc.compare_frames([a], [b])

    assert result["classifications"]["stable"] == 1
    assert result["surface_identity_split_diagnostics"]["counts"].get(
        "final_cell_signature_churn_cells", 0
    ) == 0
    fields = mc.final_cell_signature_fields(a)
    assert fields["final_ink_coverage_q"] == 500
    assert fields["final_ink_mix_q"] == (500, 0, 500, 400)


def test_perspective_moved_occurrence_does_not_compare_screen_ink_coverage():
    a = _row(0, 47, 47, owner="terrain", pid="terrain_shared")
    b = _row(1, 47, 47, owner="terrain", pid="terrain_shared")
    a["screen_cell"] = {"x": 20, "y": 30}
    b["screen_cell"] = {"x": 24, "y": 31}
    for row in (a, b):
        row.update({
            "glyph_id": 35,
            "fg_rgb": [120, 120, 120],
            "bg_rgb": [20, 20, 20],
            "final_ink_pixels": 400,
        })
    a.update({"final_ink_fg": 20, "final_ink_bg": 380})
    b.update({"final_ink_fg": 120, "final_ink_bg": 280})

    result = mc.compare_frames([a], [b])

    assert result["classifications"]["stable"] == 1
    assert result["surface_identity_split_diagnostics"]["counts"].get(
        "final_cell_signature_churn_cells", 0
    ) == 0


def test_perspective_duplicate_products_match_one_to_one_by_screen_occurrence():
    a_left = _row(0, 1, 1, pid="terrain_shared")
    a_right = _row(0, 1, 1, pid="terrain_shared")
    b_right = _row(1, 1, 1, pid="terrain_shared")
    b_left = _row(1, 1, 1, pid="terrain_shared")
    a_left["screen_cell"] = {"x": 10, "y": 20}
    a_right["screen_cell"] = {"x": 30, "y": 20}
    b_left["screen_cell"] = {"x": 11, "y": 20}
    b_right["screen_cell"] = {"x": 31, "y": 20}
    for row, ink in ((a_left, 10), (b_left, 10), (a_right, 30), (b_right, 30)):
        row.update({
            "glyph_id": 35,
            "fg_rgb": [120, 120, 120],
            "bg_rgb": [20, 20, 20],
            "final_ink_fg": ink,
            "final_ink_bg": 400 - ink,
            "final_ink_pixels": 400,
        })

    # Deliberately reverse frame B. The old independent nearest-anchor search
    # reused b_right for both A rows and manufactured a paint-churn result.
    result = mc.compare_frames([a_left, a_right], [b_right, b_left])

    assert result["comparable_cells"] == 2
    assert result["classifications"]["stable"] == 2
    assert result["surface_identity_split_diagnostics"]["counts"].get(
        "final_cell_signature_churn_cells", 0
    ) == 0


def _product_source_with_includes():
    """FL-4359: follow the real include graph after Product extraction.

    This is a source contract, not preprocessor execution or GPU evidence.
    Detached files cannot satisfy an assertion after their include is removed.
    """
    project = Path(__file__).resolve().parents[1] / "godot_project"

    def expand(path, stack=()):
        assert path not in stack, f"cyclic shader include: {path}"
        return re.sub(
            r'^\s*#include\s+"res://([^\"]+)"\s*$',
            lambda match: expand(project / match.group(1), (*stack, path)),
            path.read_text(), flags=re.MULTILINE,
        )

    return expand(project / "render/product/cell_owner_product.glsl")


def test_surface_grid_and_world_keyed_paint_source_contracts():
    """Pin moving-camera identity to retained world records.

    The probe-margin experiment was deleted with its renderer. The executable
    contract now follows the VSB projection/revision/anchor handoff through the
    sole Product writer and sole compositor reader.
    """
    root = Path(__file__).resolve().parents[1]
    vsb = (root / "godot_project" / "render" / "backends" / "ortho" / "visibility" / "visible_surface_builder.glsl").read_text()
    product = _product_source_with_includes()
    comp_shader = (root / "godot_project" / "render" / "ascii" / "raster" / "ascii_compositor.glsl").read_text()
    host = (root / "godot_project" / "render" / "api" / "ASCIICompositorEffect.gd").read_text()
    assert "record.stable_world_anchor = vec4(hit.anchor, 1.0);" in vsb
    assert "record.projection_kind = push_data.projection_kind;" in vsb
    assert "record.view_revision = push_data.revisions.z;" in vsb
    assert "record.projection_kind != product_vsb_frame.view_identity.z" in product
    assert "record.view_revision != product_vsb_frame.view_identity.y" in product
    assert "vsb_visible_surface_products.products[visible_index]" in product
    assert "finalized_subcell_products.products[cell_index * 4u + slot]" in comp_shader
    assert "_build_vsb_view_dispatch" in host


def test_terrain_product_dither_key_is_product_cell_not_subcell():
    product = _product_source_with_includes()
    assert "q3_probe_payload_apply_surface_stable_dither(\n\t\t\tcontext.material_cell" in product
    assert "q3_probe_payload_apply_world_cell_edge" in product


def test_surface_record_payload_does_not_own_terrain_water_product_paint():
    """Prove VSB identity is decoded before Product authors the final paint."""
    product = _product_source_with_includes()
    assert "bool vsb_product_prepare_terrain(" in product
    assert "bool vsb_product_prepare_water(" in product
    assert "compile_probe_subcell_payload(" in product
    assert "final_record = vsb_product_finalize_record(" in product
    assert "finalized_probe_subcell_products.products[final_index] = final_record;" not in product


def _args(**over):
    base = dict(
        max_snap_px_delta=1.0,
        max_snap_sign_flip_rate=0.05,
        max_content_jitter_pct=5.0,
        max_selected_surface_identity_churn_pct=5.0,
        max_post_surface_bounce_pct=12.0,
        min_moving_frame_pairs=0,
        min_camera_motion_ratio=0.0,
        min_comparable_cells=200,
    )
    base.update(over)
    return argparse.Namespace(**base)


def test_comparable_evidence_gate_fails_closed_when_too_few():
    # Fixed-screen paint cannot substitute for exact-world comparison while the
    # camera moves; ordinary scene scrolling would otherwise become shimmer.
    summary = {
        "frame_count": 2,
        "row_count": 100,
        "max_pair_jitter_pct": 0.0,  # would otherwise pass
        "min_comparable_cells": 20,
        "total_comparable_cells": 20,
        "moving_frame_pairs": 5,
        "camera_motion_continuous_ratio": 1.0,
        "fixed_screen_temporal_evidence_present": True,
        "fixed_screen_temporal_comparable_cells": 1000,
        "fixed_screen_visible_signature_flip_pct": 0.5,
        "motion_compensated_final_cell_instability_pct": 0.5,
        "corrected_final_cell_oracle_valid": False,
        "product_anchor_provenance_complete": True,
        "cell_mask_owner_stability": {
            "counts": {"comparable_cells": 800, "stable_cells": 800},
            "invalid_cell_count": 0,
            "comparable_pair_count": 4,
            "max_instability_pct": 0.0,
        },
    }
    gates = mc.build_gate_summary(summary, _args())
    assert gates["evidence_comparable_cells_present"]["pass"] is False
    assert gates["gameplay_content_shimmer_bounded"]["pass"] is False


def test_comparable_evidence_gate_uses_enough_qualifying_pairs():
    # A long moving review can include one broad-motion pair with few matched
    # anchors. The run still has valid shimmer evidence when enough other pairs
    # meet the comparable-cell threshold.
    summary = {
        "frame_count": 6,
        "row_count": 1000,
        "max_pair_jitter_pct": 0.5,
        "min_comparable_cells": 65,
        "min_nonzero_comparable_cells": 65,
        "total_comparable_cells": 5000,
        "comparable_pair_count": 5,
        "comparable_pairs_meeting_min": 4,
        "required_comparable_pairs": 3,
        "moving_frame_pairs": 5,
        "camera_motion_continuous_ratio": 1.0,
        "fixed_screen_temporal_evidence_present": True,
        "fixed_screen_temporal_comparable_cells": 1000,
        "fixed_screen_visible_signature_flip_pct": 0.5,
        "motion_compensated_final_cell_instability_pct": 0.5,
        "corrected_final_cell_oracle_valid": True,
        "product_anchor_provenance_complete": True,
        "terrain_product_homogeneity_rows": 1000,
        "terrain_product_homogeneity_invalid_rows": 0,
        "terrain_product_dominant_material_ratio": 0.5,
        "terrain_product_homogeneity_baseline_calibrated": True,
        "presentation_observer_population_accounting_valid": True,
    }
    gates = mc.build_gate_summary(summary, _args(min_moving_frame_pairs=3))
    assert gates["evidence_comparable_cells_present"]["pass"] is True
    assert gates["gameplay_content_shimmer_bounded"]["pass"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("terrain_product_dominant_material_ratio", 0.885),
        ("terrain_product_homogeneity_baseline_calibrated", False),
    ),
)
def test_shimmer_and_fg_truth_fail_closed_on_degenerate_or_uncalibrated_terrain(
    field, value
):
    summary = {
        "frame_count": 6,
        "row_count": 1000,
        "moving_frame_pairs": 5,
        "camera_motion_continuous_ratio": 1.0,
        "motion_compensated_final_cell_instability_pct": 0.08,
        "corrected_final_cell_oracle_valid": True,
        "product_anchor_provenance_complete": True,
        "terrain_product_homogeneity_rows": 1000,
        "terrain_product_homogeneity_invalid_rows": 0,
        "terrain_product_dominant_material_ratio": 0.5,
        "terrain_product_homogeneity_baseline_calibrated": True,
        "presentation_observer_population_accounting_valid": True,
        "continuous_product_colour_stability": {
            "counts": {"comparable_stable_semantic_samples": 2434},
            "invalid_terrain_row_count": 0,
            "invalid_colour_witness_row_count": 0,
            "invalid_finalization_witness_row_count": 0,
            "comparable_pair_count": 5,
            "min_comparable_samples_per_pair": 200,
            "max_instability_pct": 0.08,
        },
    }
    summary[field] = value

    gates = mc.build_gate_summary(
        summary,
        _args(min_moving_frame_pairs=4, min_comparable_cells=200),
    )

    for gate_name in (
        "gameplay_content_shimmer_bounded",
        "gameplay_continuous_product_colour_stability_bounded",
    ):
        gate = gates[gate_name]
        assert gate["pass"] is False
        assert gate["presentation_distribution_admissible"] is False
        assert gate["diagnostic_only_reason"] == (
            "terrain_presentation_degenerate_uncalibrated_or_population_unaccounted"
        )


def test_shimmer_gate_uses_motion_compensated_final_visible_signature():
    summary = {
        "frame_count": 6,
        "row_count": 1000,
        "max_pair_jitter_pct": 0.0,
        "fixed_screen_temporal_evidence_present": True,
        "fixed_screen_temporal_comparable_cells": 1000,
        "fixed_screen_visible_signature_flip_pct": 80.0,
        "motion_compensated_final_cell_instability_pct": 9.25,
        "corrected_final_cell_oracle_valid": True,
        "product_anchor_provenance_complete": True,
        "min_comparable_cells": 500,
        "min_nonzero_comparable_cells": 500,
        "total_comparable_cells": 5000,
        "comparable_pair_count": 5,
        "comparable_pairs_meeting_min": 5,
        "required_comparable_pairs": 4,
        "moving_frame_pairs": 5,
        "camera_motion_continuous_ratio": 1.0,
    }
    gates = mc.build_gate_summary(summary, _args(min_moving_frame_pairs=4))
    shimmer_gate = gates["gameplay_content_shimmer_bounded"]
    assert shimmer_gate["pass"] is False
    assert "exact continuous terrain paint sample" in shimmer_gate["definition"]
    assert shimmer_gate["motion_compensated_final_cell_instability_pct"] == 9.25
    assert shimmer_gate["fixed_screen_metric_is_acceptance_gate"] is False
    assert shimmer_gate["fixed_screen_visible_signature_flip_pct"] == 80.0
    assert shimmer_gate["legacy_max_pair_jitter_pct"] == 0.0
    assert shimmer_gate["legacy_max_pair_jitter_is_acceptance_gate"] is False


def _fixed_screen_row(frame_id, product, mask, reasons, glyph=10):
    return {
        "frame_id": frame_id,
        "screen_cell": {"x": 7, "y": 11},
        "f10_diag_readback_present": True,
        "f10_backstop_subtap_mask": mask,
        "f10_diag_markers": [mask, 0, 0, 0],
        "f10_rekey_reasons": reasons,
        "selected_product_id": product,
        "selected_owner": "terrain",
        "glyph_id": glyph,
        "fg_rgb": [80, 90, 100],
        "bg_rgb": [10, 20, 30],
        "final_ink_pixels": 400,
        "final_ink_fg": 200,
        "final_ink_bg": 200,
    }


def test_fixed_screen_temporal_separates_f10_classification_from_paint_flip():
    frames = {
        0: [_fixed_screen_row(0, "terrain_1", 0, [0, 0, 0, 0])],
        1: [_fixed_screen_row(1, "terrain_2", 1, [3, 0, 0, 0], glyph=11)],
    }
    result = mc.summarize_fixed_screen_temporal(frames)
    assert result["fixed_screen_temporal_comparable_cells"] == 1
    assert result["fixed_screen_backstop_marker_flip_cells"] == 1
    assert result["fixed_screen_rekey_reason_flip_cells"] == 1
    assert result["fixed_screen_product_flip_cells"] == 1
    assert result["fixed_screen_visible_signature_flip_cells"] == 1
    assert result["fixed_screen_highlighted_classification_flip_cells"] == 1
    assert result["fixed_screen_stable_highlight_visible_flip_cells"] == 0


def test_fixed_screen_temporal_names_stable_highlight_downstream_change():
    frames = {
        0: [_fixed_screen_row(0, "terrain_1", 1, [3, 0, 0, 0], glyph=10)],
        1: [_fixed_screen_row(1, "terrain_1", 1, [3, 0, 0, 0], glyph=11)],
    }
    result = mc.summarize_fixed_screen_temporal(frames)
    assert result["fixed_screen_backstop_marker_flip_cells"] == 0
    assert result["fixed_screen_rekey_reason_flip_cells"] == 0
    assert result["fixed_screen_visible_signature_flip_cells"] == 1
    assert result["fixed_screen_stable_highlight_visible_flip_cells"] == 1


def test_retained_plate_mechanics_reads_keys_not_f10_colors():
    frames = {0: [], 1: []}
    for x in range(3):
        row = _row(0, x, 0)
        row["final_product_source_key"] = 777
        row["selected_source_epoch"] = "current"
        frames[0].append(row)
    for x in range(3):
        row = _row(1, x, 0)
        row["final_product_source_key"] = 888 if x < 2 else 777
        row["selected_source_epoch"] = "previous" if x < 2 else "current"
        frames[1].append(row)

    result = mc.summarize_retained_plate_mechanics(frames)

    assert result["retained_plate_mechanics_present"] is True
    assert result["retained_plate_component_max_size"] == 3
    assert result["retained_plate_temporal_comparable_screen_cells"] == 3
    assert result["retained_plate_source_key_flip_cells"] == 2
    assert result["retained_plate_stationary_world_anchor_cells"] == 3
    assert result["retained_plate_stationary_source_key_flip_cells"] == 2
    assert result["retained_plate_generation_capture_present"] is True
    assert result["retained_plate_generation_flip_cells"] == 2
    assert result["retained_plate_current_previous_generation_flip_cells"] == 2
    assert result["retained_plate_stationary_current_previous_generation_flip_cells"] == 2


def test_retained_plate_mechanics_keeps_source_key_metric_when_generation_absent():
    row_a = _row(0, 0, 0)
    row_b = _row(1, 0, 0)
    row_b["final_product_source_key"] = row_a["final_product_source_key"] + 1

    result = mc.summarize_retained_plate_mechanics({0: [row_a], 1: [row_b]})

    assert result["retained_plate_generation_capture_present"] is False
    assert result["retained_plate_generation_comparable_cells"] == 0
    assert result["retained_plate_temporal_comparable_screen_cells"] == 1
    assert result["retained_plate_source_key_flip_cells"] == 1


def test_retained_plate_mechanics_reports_unmeasured_without_cell_provenance():
    """The b55 status source-epoch bits are not a provenance owner.

    The producer leaves selected_surface_status bits 30-31 zero on every row,
    so a row carrying only that status has no per-cell generation measurement.
    It must land in the explicit unmeasured bucket rather than reporting the
    "direct" epoch.
    """
    row_a = _row(0, 0, 0)
    row_b = _row(1, 0, 0)
    row_a["selected_surface_status"] = 34
    row_b["selected_surface_status"] = 34

    result = mc.summarize_retained_plate_mechanics({0: [row_a], 1: [row_b]})

    assert result["retained_plate_generation_capture_present"] is False
    assert result["retained_plate_generation_comparable_cells"] == 0
    assert result["retained_plate_generation_flip_cells"] == 0
    assert result["retained_plate_current_previous_generation_flip_cells"] == 0
    assert result["retained_plate_generation_unmeasured_cells"] == 1
    assert result["retained_plate_current_previous_classified_cells"] == 0
    assert result["retained_plate_current_previous_unmeasured_cells"] == 1
    assert result["retained_plate_component_examples"][0]["generation_labels"] == [
        mc.PROVENANCE_UNMEASURED
    ]


def test_retained_plate_layer_evidence_absent_reports_unmeasured_transition():
    """A capture without the layer-granular schema classifies as unmeasured."""
    row_a = _row(0, 0, 0)
    row_b = _row(1, 0, 0)

    result = mc.summarize_retained_plate_mechanics({0: [row_a], 1: [row_b]})

    assert result["retained_plate_layer_evidence_present"] is False
    assert result["retained_plate_layer_evidence_frames"] == 0
    assert result["retained_plate_layer_epoch_measured_cells"] == 0
    assert result["retained_plate_layer_epoch_unmeasured_cells"] == 1
    assert result["retained_plate_provenance_change_unmeasured_cells"] == 1
    assert result["retained_plate_provenance_change_band_rotation_cells"] == 0
    assert result["retained_plate_provenance_change_layer_recaptured_cells"] == 0
    activity = result["retained_plate_capture_activity_by_probe_counts"]
    assert activity["current"] == {mc.PROVENANCE_UNMEASURED: 2}


def _layer_lifecycle_row(frame_id: int, *, bands, epochs, role, face, texel, generation):
    """Build one b61 row joined to a frame's layer-granular probe evidence."""
    row = _row(frame_id, 0, 0)
    row["probe_source_capture_valid"] = True
    row["probe_source_capture_probe"] = role
    row["probe_source_capture_face"] = face
    row["probe_source_capture_texel"] = texel
    row["probe_source_capture_generation"] = generation
    row["frame_trace"] = {
        "perspective_probe_state": {
            "capture_bands": list(bands),
            "layer_content_epochs": list(epochs),
            "layer_content_epoch_generations": [generation] * 18,
            "layer_content_epoch_reasons": [""] * 18,
            "layer_content_epochs_before_capture": list(epochs),
            "layers_recaptured_this_frame": [],
            "recapture_reasons_by_probe": [[], [], []],
            "capture_activity_by_probe": [
                "reused_retained",
                "reused_retained",
                "reused_retained",
            ],
        }
    }
    return row


def test_retained_plate_splits_band_rotation_from_actual_recapture():
    """Role relabel over surviving pixels is not a recapture."""
    epochs = [7] * 18
    # CURRENT->band 1 in frame 0, CURRENT->band 2 in frame 1: the role label
    # rotates onto a band whose face-0 content epoch never advanced.
    row_a = _layer_lifecycle_row(
        0, bands=[0, 1, 2], epochs=epochs, role=1, face=0, texel=5, generation=7
    )
    row_b = _layer_lifecycle_row(
        1, bands=[0, 2, 1], epochs=epochs, role=2, face=0, texel=5, generation=7
    )

    result = mc.summarize_retained_plate_mechanics({0: [row_a], 1: [row_b]})

    assert result["retained_plate_layer_evidence_present"] is True
    assert result["retained_plate_layer_epoch_survived_cells"] == 1
    assert result["retained_plate_layer_epoch_advanced_cells"] == 0
    assert result["retained_plate_provenance_change_band_rotation_cells"] == 1
    assert result["retained_plate_provenance_change_layer_recaptured_cells"] == 0
    assert result["retained_plate_provenance_change_unmeasured_cells"] == 0


def test_retained_plate_names_layer_recapture_when_content_epoch_advances():
    epochs_after = [7] * 18
    # Physical layer 6 (band 1, face 0) is rewritten between the two frames.
    epochs_after[6] = 8
    row_a = _layer_lifecycle_row(
        0, bands=[0, 1, 2], epochs=[7] * 18, role=1, face=0, texel=5, generation=7
    )
    row_b = _layer_lifecycle_row(
        1, bands=[0, 1, 2], epochs=epochs_after, role=1, face=0, texel=9, generation=8
    )

    result = mc.summarize_retained_plate_mechanics({0: [row_a], 1: [row_b]})

    assert result["retained_plate_layer_epoch_advanced_cells"] == 1
    assert result["retained_plate_layer_epoch_survived_cells"] == 0
    assert result["retained_plate_provenance_change_layer_recaptured_cells"] == 1
    assert result["retained_plate_provenance_change_band_rotation_cells"] == 0


# ---------------------------------------------------------------------------
# fl4359_jitter_transport.v16 per-cell generation columns.  The pre-v16 key
# names are absent from v16, so these columns are the only per-cell generation
# owners a current capture carries.
# ---------------------------------------------------------------------------


def test_v16_generation_prefers_candidate_over_prepack_and_oracle():
    row = _row(0, 0, 0)
    row["probe_candidate_source_generation"] = 11
    row["probe_candidate_valid"] = True
    row["probe_prepack_provenance"] = [22, 4096, 0, 0]
    row["probe_prepack_valid"] = True
    row["probe_source_oracle_generation"] = 33
    row["probe_source_oracle_query_valid"] = True

    assert mc.retained_probe_generation_label(row) == "candidate_gen:11"


def test_v16_generation_falls_to_prepack_then_oracle_by_ownership_order():
    row = _row(0, 0, 0)
    row["probe_candidate_source_generation"] = 11
    row["probe_candidate_valid"] = False
    row["probe_prepack_provenance"] = [22, 4096, 0, 0]
    row["probe_prepack_valid"] = True
    row["probe_source_oracle_generation"] = 33
    row["probe_source_oracle_query_valid"] = True
    assert mc.retained_probe_generation_label(row) == "prepack_gen:22"

    row["probe_prepack_valid"] = False
    row["probe_source_capture_generation"] = 44
    row["probe_source_capture_valid"] = True
    assert mc.retained_probe_generation_label(row) == "source_capture_gen:44"

    row["probe_source_capture_valid"] = False
    assert mc.retained_probe_generation_label(row) == "oracle_gen:33"


def test_v16_generation_rejects_producer_absent_sentinels():
    row = _row(0, 0, 0)
    # 0x3FFFFFFF is the packed-candidate absent marker, 0xFFFFFFFF the u32 one.
    row["probe_candidate_source_generation"] = 0x3FFFFFFF
    row["probe_candidate_valid"] = True
    row["probe_source_oracle_generation"] = 0xFFFFFFFF
    row["probe_source_oracle_query_valid"] = True

    assert mc.retained_probe_generation_label(row) is None
    assert mc.retained_probe_generation_classification(row) == mc.PROVENANCE_UNMEASURED


def test_v16_generation_rejects_residual_value_when_record_absent():
    """A live-looking value with the record-present flag unset is not evidence."""
    row = _row(0, 0, 0)
    row["probe_candidate_source_generation"] = 1
    row["probe_candidate_valid"] = False
    row["probe_source_capture_generation"] = 0
    row["probe_source_capture_valid"] = False

    assert mc.retained_probe_generation_label(row) is None
    assert mc.retained_probe_generation_classification(row) == mc.PROVENANCE_UNMEASURED


def test_v16_generation_reads_mask_column_as_record_present():
    row = _row(0, 0, 0)
    row["probe_expected_capture_first_match_generation"] = 7
    row["probe_expected_capture_exact_match_mask"] = 0
    assert mc.retained_probe_generation_label(row) is None

    row["probe_expected_capture_exact_match_mask"] = 0b001000
    assert mc.retained_probe_generation_label(row) == "expected_first_match_gen:7"


def test_v16_generation_owner_prefix_keeps_counters_out_of_role_vocabulary():
    """Generations 0 and 1 must not read as the CURRENT/PREVIOUS role pair."""
    row_a = _row(0, 0, 0)
    row_a["probe_candidate_source_generation"] = 0
    row_a["probe_candidate_valid"] = True
    row_b = _row(1, 0, 0)
    row_b["probe_candidate_source_generation"] = 1
    row_b["probe_candidate_valid"] = True

    label_a = mc.retained_probe_generation_label(row_a)
    label_b = mc.retained_probe_generation_label(row_b)
    assert (label_a, label_b) == ("candidate_gen:0", "candidate_gen:1")
    assert mc._retained_generation_is_current_previous_flip(label_a, label_b) is False

    result = mc.summarize_retained_plate_mechanics({0: [row_a], 1: [row_b]})
    assert result["retained_plate_generation_capture_present"] is True
    assert result["retained_plate_generation_comparable_cells"] == 1
    assert result["retained_plate_generation_flip_cells"] == 1
    assert result["retained_plate_current_previous_generation_flip_cells"] == 0
    assert result["retained_plate_current_previous_classified_cells"] == 1
    assert result["retained_plate_generation_unmeasured_cells"] == 0


def test_v16_generation_keeps_legacy_key_names_ahead_of_the_v16_table():
    row = _row(0, 0, 0)
    row["selected_source_epoch"] = "current"
    row["probe_candidate_source_generation"] = 11
    row["probe_candidate_valid"] = True

    assert mc.retained_probe_generation_label(row) == "current"


def test_shimmer_gate_fails_closed_without_exact_anchor_comparable_evidence():
    summary = {
        "frame_count": 6,
        "row_count": 1000,
        "moving_frame_pairs": 5,
        "camera_motion_continuous_ratio": 1.0,
        "min_comparable_cells": 500,
        "total_comparable_cells": 5000,
        "comparable_pair_count": 5,
        "comparable_pairs_meeting_min": 5,
        "required_comparable_pairs": 4,
    }
    gates = mc.build_gate_summary(summary, _args(min_moving_frame_pairs=4))
    assert gates["gameplay_content_shimmer_bounded"]["pass"] is False


def test_moving_camera_requirement_rejects_steady_pose():
    summary = {
        "frame_count": 2,
        "row_count": 100,
        "max_pair_jitter_pct": 0.0,
        "max_snap_px_delta": 0.0,
        "snap_sign_flip_rate": 0.0,
        "min_comparable_cells": 5000,
        "total_comparable_cells": 5000,
        "moving_frame_pairs": 0,  # camera never moved
        "camera_motion_continuous_ratio": 0.0,
    }
    gates = mc.build_gate_summary(summary, _args(min_moving_frame_pairs=4, min_camera_motion_ratio=0.5))
    assert gates["scenario_moving_camera_continuous"]["pass"] is False
    # Steady pose cannot satisfy the gameplay gates even with zero measured jitter.
    assert gates["gameplay_moving_camera_jitter_bounded"]["pass"] is False
    assert gates["gameplay_content_shimmer_bounded"]["pass"] is False


def test_motion_detected_on_unsnapped_when_snapped_pose_held():
    # Snapped pose identical every frame (held across a quantum); unsnapped pose
    # translates each frame. Motion MUST be detected from the unsnapped field.
    frames = {
        fid: [{
            "frame_id": fid,
            "camera_world": {"x": 10.0, "y": 5.0, "z": 10.0},
            "camera_world_unsnapped": {"x": 10.0 + 0.3 * fid, "y": 5.0, "z": 10.0},
        }]
        for fid in range(6)
    }
    m = mc.summarize_shimmer_metrics(frames)
    assert m["moving_frame_pairs"] == 5
    assert m["camera_translation_total"] > 1.0


def test_motion_and_snap_vectors_are_read_from_retained_frame_trace():
    frames = {
        fid: [{
            "frame_id": fid,
            "frame_trace": {
                "camera_world": {"x": float(fid), "y": 5.0, "z": 10.0},
                "camera_world_unsnapped": {"x": float(fid), "y": 5.0, "z": 10.0},
                "camera_snap_error_px": {"x": 0.25 * fid, "y": 0.0},
                "style_post_shift_px": {"x": 0.25 * fid, "y": 0.0},
            },
        }]
        for fid in range(3)
    }
    metrics = mc.summarize_shimmer_metrics(frames)
    assert metrics["moving_frame_pairs"] == 2
    assert metrics["camera_translation_total"] == 2.0
    assert metrics["max_snap_px_delta"] == 0.25
    assert metrics["max_snap_compensation_error_px"] == 0.0
    assert metrics["snap_contract_mismatch_frames"] == 0


def test_orthographic_snap_wrap_passes_when_presentation_shift_cancels_it():
    frames = {
        0: [{
            "frame_id": 0,
            "camera_world_unsnapped": {"x": 0.0, "y": 5.0, "z": 10.0},
            "camera_projection_mode": 1,
            "camera_fov": 75.0,
            "camera_near": 0.1,
            "camera_far": 4000.0,
            "pixel_camera_snap_world": 0.5,
            "camera_snap_error_px": {"x": -9.9, "y": 0.0},
            "style_post_shift_px": {"x": -9.9, "y": 0.0},
        }],
        1: [{
            "frame_id": 1,
            "camera_world_unsnapped": {"x": 1.0, "y": 5.0, "z": 10.0},
            "camera_projection_mode": 1,
            "camera_fov": 75.0,
            "camera_near": 0.1,
            "camera_far": 4000.0,
            "pixel_camera_snap_world": 0.5,
            "camera_snap_error_px": {"x": 9.9, "y": 0.0},
            "style_post_shift_px": {"x": 9.9, "y": 0.0},
        }],
    }
    metrics = mc.summarize_shimmer_metrics(frames)
    assert metrics["max_snap_px_delta"] == 19.8
    assert metrics["max_snap_compensation_error_px"] == 0.0
    assert metrics["snap_contract_mismatch_frames"] == 0


def test_compact_frame_header_is_joined_without_becoming_a_cell(tmp_path):
    capture = tmp_path / "compact.jsonl"
    frame_trace = {
        "pixel_camera_snap_world": 0.5,
        "probe_grid_step": 8.0,
    }
    rows = [
        {"row_type": "fl4359_jitter_frame", "frame_id": 7, "frame_trace": frame_trace},
        {
            "row_type": "fl4359_jitter_cell",
            "frame_id": 7,
            "screen_cell": {"x": 2, "y": 3},
        },
    ]
    capture.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    loaded = mc.load_rows(str(capture))

    assert len(loaded) == 1
    assert loaded[0]["screen_cell"] == {"x": 2, "y": 3}
    assert loaded[0]["frame_trace"] == frame_trace


def test_positional_transport_round_trips_cell_and_frame_header(tmp_path):
    capture = tmp_path / "positional.jsonl"
    frame_trace = {"pixel_camera_snap_world": 0.5, "probe_grid_step": 8.0}
    fields = ["frame_id", "row_type", "screen_cell", "sample_index"]
    records = [
        {"row_type": "fl4359_jitter_frame", "frame_id": 7, "frame_trace": frame_trace},
        {
            "row_type": "fl4359_jitter_schema",
            "transport_schema": mc.CURRENT_JITTER_TRANSPORT_SCHEMA,
            "fields": fields,
        },
        [7, "fl4359_jitter_cell", {"x": 2, "y": 3}, 11],
    ]
    capture.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    loaded = mc.load_rows(str(capture))

    assert loaded == [
        {
            "frame_id": 7,
            "row_type": "fl4359_jitter_cell",
            "screen_cell": {"x": 2, "y": 3},
            "sample_index": 11,
            "frame_trace": frame_trace,
        }
    ]


def test_numeric_product_owner_repairs_and_audits_stale_transport_label(tmp_path):
    capture = tmp_path / "product_owner.jsonl"
    fields = [
        "frame_id",
        "row_type",
        "final_product_owner",
        "final_product_owner_name",
    ]
    records = [
        {
            "row_type": "fl4359_jitter_schema",
            "transport_schema": mc.CURRENT_JITTER_TRANSPORT_SCHEMA,
            "fields": fields,
        },
        [7, "fl4359_jitter_cell", 3, "water"],
        [7, "fl4359_jitter_cell", 4, "dynamic_mesh"],
        [7, "fl4359_jitter_cell", 5, "actor_sprite"],
    ]
    capture.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    loaded = mc.load_rows(str(capture))

    assert [row["final_product_owner_name"] for row in loaded] == [
        "dynamic_mesh",
        "actor_sprite",
        "water",
    ]
    assert [row["final_product_owner_name_transport"] for row in loaded] == [
        "water",
        "dynamic_mesh",
        "actor_sprite",
    ]
    assert all(
        row["final_product_owner_name_transport_valid"] is False for row in loaded
    )


def test_positional_transport_preserves_nonfinite_float_bits(tmp_path):
    capture = tmp_path / "nonfinite.jsonl"
    fields = ["frame_id", "row_type", "positive_inf", "negative_inf", "nan_value"]

    def bits(value: float) -> dict:
        low, high = struct.unpack("<II", struct.pack("<d", value))
        return {mc.JITTER_TRANSPORT_F64_BITS_KEY: [low, high]}

    records = [
        {
            "row_type": "fl4359_jitter_schema",
            "transport_schema": mc.CURRENT_JITTER_TRANSPORT_SCHEMA,
            "fields": fields,
        },
        [
            0,
            "fl4359_jitter_cell",
            bits(math.inf),
            bits(-math.inf),
            bits(math.nan),
        ],
    ]
    capture.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    loaded = mc.load_rows(str(capture))

    assert math.isinf(loaded[0]["positive_inf"]) and loaded[0]["positive_inf"] > 0
    assert math.isinf(loaded[0]["negative_inf"]) and loaded[0]["negative_inf"] < 0
    assert math.isnan(loaded[0]["nan_value"])


@pytest.mark.parametrize(
    "records",
    [
        [[7, "fl4359_jitter_cell"]],
        [
            {
                "row_type": "fl4359_jitter_schema",
                "transport_schema": mc.CURRENT_JITTER_TRANSPORT_SCHEMA,
                "fields": ["frame_id", "row_type", "sample_index"],
            },
            [7, "fl4359_jitter_cell"],
        ],
        [
            {
                "row_type": "fl4359_jitter_schema",
                "transport_schema": "fl4359_jitter_transport.unknown",
                "fields": ["frame_id"],
            }
        ],
        [
            {
                "row_type": "fl4359_jitter_schema",
                "transport_schema": mc.CURRENT_JITTER_TRANSPORT_SCHEMA,
                "fields": ["frame_id", "frame_id"],
            }
        ],
    ],
)
def test_positional_transport_malformed_input_fails_closed(tmp_path, records):
    capture = tmp_path / "malformed.jsonl"
    capture.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    with pytest.raises(ValueError):
        mc.load_rows(str(capture))


def test_fullscreen_native_pixel_debug_owner_is_deleted():
    root = Path(__file__).resolve().parents[1]
    compositor = (root / "godot_project" / "render" / "ascii" / "raster" / "ascii_compositor.glsl").read_text()
    effect = (root / "godot_project" / "render" / "api" / "ASCIICompositorEffect.gd").read_text()

    assert "PixelDebugBuffer" not in compositor
    assert "record_pixel_debug" not in compositor
    assert "pixel_debug_rd" not in effect
    assert "_ensure_pixel_debug_buffer" not in effect


def test_required_field_coverage_reports_present_and_missing_fields():
    rows = [
        {
            "frame_id": 0,
            "row_type": "fl4359_jitter_cell",
            "frame_trace": {
                "snap_grid_phase": {"x": 0.0, "y": 0.0},
                "pixel_camera_snap_world": 0.5,
                "springarm_slope_occlusion_frames": 0,
                "probe_grid_step": 8.0,
            },
            "camera_world": {"x": 0.0, "y": 0.0, "z": 0.0},
            "camera_world_unsnapped": {"x": 0.0, "y": 0.0, "z": 0.0},
            "camera_snap_error_px": {"x": 0.0, "y": 0.0},
            "source_key": 12,
            "terr_stable_cell_gpu": {"x": 1, "y": 2},
            "probe_texel_id": -1,
            "probe_depth_test_result": False,
            "probe_candidate_fallback_cell": False,
            "cell_owner_product_feed_row": {
                "selected_owner": "terrain",
                "selected_product_id": "terrain_1_1_2",
                "source_key": 12,
                "probe_texel_id": -1,
                "probe_depth_test_result": False,
                "probe_candidate_fallback_cell": False,
            },
            "water_splat_winner_hidden_as_sky": False,
            "selected_owner": "terrain",
            "selected_product_id": "terrain_1_1_2",
            "material_role": "cliff_rock",
            "terrain_product_detail": 1 << 3,
        },
        {
            "frame_id": 1,
            "row_type": "fl4359_jitter_cell",
            "frame_trace": {
                "snap_grid_phase": {"x": 0.25, "y": 0.0},
                "pixel_camera_snap_world": 0.5,
                "springarm_slope_occlusion_frames": 0,
                "probe_grid_step": 8.0,
            },
            "camera_world": {"x": 1.0, "y": 0.0, "z": 0.0},
            "camera_world_unsnapped": {"x": 1.0, "y": 0.0, "z": 0.0},
            "camera_snap_error_px": {"x": 0.25, "y": 0.0},
            "source_key": 12,
            "terr_stable_cell_gpu": {"x": 1, "y": 2},
            "probe_texel_id": -1,
            "probe_depth_test_result": False,
            "probe_candidate_fallback_cell": False,
            "cell_owner_product_feed_row": {
                "selected_owner": "terrain",
                "selected_product_id": "terrain_1_1_2",
                "source_key": 12,
                "probe_texel_id": -1,
                "probe_depth_test_result": False,
                "probe_candidate_fallback_cell": False,
            },
            "water_splat_winner_hidden_as_sky": False,
            "selected_owner": "terrain",
            "selected_product_id": "terrain_1_1_2",
            "material_role": "cliff_rock",
            "terrain_product_detail": 1 << 3,
        },
    ]
    summary = {
        "frame_count": 2,
        "pair_count": 1,
        "pair_results": [
            {
                "classifications": {"stable": 1},
                "shimmer_cells": 0,
            }
        ],
        "comparable_pair_count": 1,
        "moving_frame_pairs": 1,
        "mountain_material_rows": 2,
        "frame_metrics": [
            {"snap_px_delta_from_previous": 0.0},
            {"snap_px_delta_from_previous": 0.25},
        ],
    }
    for row in rows:
        row.update({
            "frame_index": row["frame_id"],
            "movement_phase": "active",
            "admission_bits": mc.ADMISSION_WORLD | mc.ADMISSION_SCREEN,
            "product_time_seconds": 0.35,
            "selected_surface_owner": 2,
            "selected_surface_object": 10,
            "selected_surface_primitive": 1,
            "selected_surface_material": 4,
            "selected_surface_scene_revision": 7,
            "selected_surface_view_revision": 8,
            "selected_surface_projection_kind": 0,
            "final_product_owner": 1,
            "final_product_status": 0x02000001,
            "final_product_source_key": 0,
            "final_product_matcell_packed": 0x0102,
            "final_product_world_anchor": {"x": 10.0, "y": 0.0, "z": 10.0},
            "ansi_cell_world_anchor": {"x": 10.0, "y": 0.0, "z": 10.0},
            "comparison_anchor_source": mc.COMPARISON_ANCHOR_SOURCE_B55,
            "capture_schema": mc.CURRENT_SPARSE_CAPTURE_SCHEMA,
            "sample_index": 0,
            "subcell_slot": 0,
                "final_product_glyph": 33,
                "final_product_material_family": 2,
                "final_product_material_id": 5,
                    "final_product_fg_rgb": [120, 120, 120],
                "final_product_bg_rgb": [20, 20, 20],
                "final_product_trace_receiver_bits": [0, 0, 0, 0],
                "final_product_continuous_sample_bits": [1, 2, 3],
                "final_product_continuous_sample_valid": True,
                    "final_product_terrain_colour_witness_bits": _terrain_colour_witness(),
                    "final_product_terrain_colour_witness_valid": True,
                    "final_product_pre_finalization_signature_bits": [
                        0x02000001, 33, _packed_rgb([120, 120, 120]),
                        _packed_rgb([20, 20, 20]),
                    ],
                    "final_product_post_finalization_signature_bits": [
                        0x02000001, 33, _packed_rgb([120, 120, 120]),
                        _packed_rgb([20, 20, 20]),
                    ],
            "final_paint_slot_glyph": 33,
                "final_paint_slot_present": True,
                "final_paint_slot_actor_overlay": False,
                "final_paint_slot_colour_present": True,
                "final_paint_slot_fg_rgb": [120, 120, 120],
                "final_paint_slot_bg_rgb": [20, 20, 20],
                "final_paint_mask_unknown_bits": 0,
            "final_paint_glyph": 33,
                "final_paint_mask_presence": 1,
                "final_paint_mask_contributor_count": 1,
                "final_paint_terminal_mask_ink_count": 1,
                "final_paint_terminal_mask_sum_x": 0,
                "final_paint_terminal_mask_sum_y": 0,
                "final_paint_terminal_mask_min_x": 0,
                "final_paint_terminal_mask_min_y": 0,
                "final_paint_terminal_mask_max_x": 0,
                "final_paint_terminal_mask_max_y": 0,
                "final_paint_terminal_mask_digest": 1,
                "final_paint_terminal_occurrence_valid": True,
            })
    coverage = mc.summarize_required_field_coverage(rows, summary)

    assert coverage["all_present"] is True
    assert set(mc.FL4359_REQUIRED_FIELDS).issubset(coverage["present"])
    assert not {"probe_texel_id", "probe_depth_test_result", "probe_candidate_fallback_cell"}.intersection(coverage["fields"])
    assert "fps_frame_time_ms" not in coverage["fields"]
    assert "fps_frame_time_ms" not in coverage["missing"]


def test_required_field_one_pass_preserves_nested_and_falsey_presence() -> None:
    rows = [
        {
            # A direct None must not hide the authoritative nested value.
            "final_product_source_key": None,
            "frame_trace": {
                "final_product_source_key": 0,
                "pixel_camera_snap_world": False,
            },
        },
        {
            # Direct and nested copies are one field occurrence for this row.
            "final_product_source_key": 0,
            "frame_trace": {
                "final_product_source_key": 9,
                "pixel_camera_snap_world": 0.0,
            },
        },
    ]
    coverage = mc.summarize_required_field_coverage(
        rows,
        {
            "frame_count": 1,
            "pair_count": 0,
            "comparable_pair_count": 0,
            "moving_frame_pairs": 0,
            "frame_metrics": [],
        },
    )

    assert coverage["fields"]["final_product_source_key"]["raw_rows"] == 2
    assert coverage["fields"]["pixel_camera_snap_world"]["raw_rows"] == 2


def test_water_splat_sky_hole_gate_fails_on_hidden_water_winner():
    rows = [
        {
            "frame_id": 0,
            "row_type": "fl4359_jitter_cell",
            "water_splat_winner_hidden_as_sky": True,
        },
        {
            "frame_id": 1,
            "row_type": "fl4359_jitter_cell",
            "water_splat_winner_hidden_as_sky": False,
        },
    ]
    summary = {
        "frame_count": 2,
        "row_count": 2,
        "water_splat_winner_hidden_as_sky_count": 1,
    }
    coverage = mc.summarize_required_field_coverage(rows, summary)
    gates = mc.build_gate_summary(summary, _args())

    assert "water_splat_winner_hidden_as_sky" not in coverage["fields"]
    assert gates["evidence_no_water_splat_sky_holes"]["pass"] is False
    assert gates["evidence_no_water_splat_sky_holes"]["water_splat_winner_hidden_as_sky_count"] == 1


def test_required_field_coverage_gate_blocks_missing_metadata():
    summary = {
        "frame_count": 2,
        "row_count": 2,
        "pair_count": 1,
        "comparable_pair_count": 1,
        "moving_frame_pairs": 1,
        "required_field_coverage": {
            "all_present": False,
            "present": ["probe_texel_id"],
            "missing": ["probe_snap_origin"],
        },
    }
    gates = mc.build_gate_summary(summary, _args())

    assert gates["evidence_required_fields_present"]["pass"] is False
    assert "probe_snap_origin" in gates["evidence_required_fields_present"]["required_field_coverage"]["missing"]
    assert "evidence_required_fields_present" in gates["blocking_failed_gates"]


def _lifecycle_row_for_contract_tests():
    perspective = {
        "valid": True,
        "absence_reason": "",
        "scene_revision": 7,
        "view_revision": 8,
        "terrain_revision": 1,
        "water_revision": 2,
        "static_revision": 3,
        "dynamic_revision": 4,
        "capture_generation": 5,
        "recapture_reason": "",
        "recapture_mask": 0,
        "active_mask": 1,
        "transition_remaining": 0,
        "face_masks": [],
        "recaptured_faces": [],
        "families_submitted": [],
        "steady_reuse": True,
        "candidate_extent": {"x": 1, "y": 1},
        "candidate_extent_generation": 1,
        "materialized_records": 1,
    }
    return {
        "lifecycle_facts_required": True,
        "frame_trace": {
            "lifecycle_facts_required": True,
            "scene_revision": 7,
            "view_revision": 8,
            "perspective_probe_state": perspective,
            "dynamic_mover_timing": {"sample_time_seconds": 0.25},
            "water_interaction_field_metrics": {"steady_state": True},
        },
    }


def test_lifecycle_facts_valid_fixture_is_admitted():
    facts = mc.summarize_lifecycle_facts([_lifecycle_row_for_contract_tests()])
    assert facts == {
        "required": True,
        "all_valid": True,
        "required_rows": 1,
        "invalid": [],
    }


def test_matrix_identity_is_required_and_joined_when_current_producer_marks_it():
    row = _lifecycle_row_for_contract_tests()
    trace = row["frame_trace"]
    trace["perspective_probe_state"]["candidate_extent"] = {"x": 24, "y": 8}
    trace["perspective_probe_state"]["candidate_extent_generation"] = 4
    trace.update(
        {
            "matrix_identity_required": True,
            "target_size": [192, 128],
            "cell_size": [16, 32],
            "semantic_ansi_size": [12, 4],
            "matrix_identity": {
                "schema": "fl4359.matrix_configuration_identity.v1",
                "key": "192x108|192x128|192x108|16x32|12x4|24x8|g4|terminal|perspective|FF",
                "actual_image_size": [192, 108],
                "target_size": [192, 128],
                "presentation_size": [192, 108],
                "cell_size": [16, 32],
                "semantic_ansi_size": [12, 4],
                "candidate_extent": [24, 8],
                "candidate_extent_generation": 4,
                "cell_aspect": "terminal",
                "projection": "perspective",
                "presentation_mode": "FF",
            },
        }
    )
    facts = mc.summarize_lifecycle_facts([row])
    assert facts["all_valid"] is True
    trace["matrix_identity"]["cell_size"] = [24, 24]
    facts = mc.summarize_lifecycle_facts([row])
    assert facts["all_valid"] is False
    assert facts["invalid"] == [{"row_index": 0, "field": "matrix_identity"}]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("actual_image_size", [191, 108]),
        ("presentation_size", [191, 108]),
        ("candidate_extent", [22, 8]),
        ("candidate_extent_generation", 5),
        ("projection", "orthographic"),
        ("presentation_mode", "FC"),
    ],
)
def test_matrix_identity_rejects_stale_key_or_probe_join(field, value):
    row = _lifecycle_row_for_contract_tests()
    trace = row["frame_trace"]
    trace["perspective_probe_state"]["candidate_extent"] = {"x": 24, "y": 8}
    trace["perspective_probe_state"]["candidate_extent_generation"] = 4
    identity = {
        "schema": "fl4359.matrix_configuration_identity.v1",
        "key": "192x108|192x128|192x108|16x32|12x4|24x8|g4|terminal|perspective|FF",
        "actual_image_size": [192, 108],
        "target_size": [192, 128],
        "presentation_size": [192, 108],
        "cell_size": [16, 32],
        "semantic_ansi_size": [12, 4],
        "candidate_extent": [24, 8],
        "candidate_extent_generation": 4,
        "cell_aspect": "terminal",
        "projection": "perspective",
        "presentation_mode": "FF",
    }
    identity[field] = value
    trace.update(
        {
            "matrix_identity_required": True,
            "target_size": [192, 128],
            "cell_size": [16, 32],
            "semantic_ansi_size": [12, 4],
            "matrix_identity": identity,
        }
    )
    facts = mc.summarize_lifecycle_facts([row])
    assert facts["all_valid"] is False
    assert facts["invalid"] == [{"row_index": 0, "field": "matrix_identity"}]


@pytest.mark.parametrize(
    ("field", "mutation"),
    [
        ("perspective_probe_state", "missing"),
        ("dynamic_mover_timing", "missing"),
        ("water_interaction_field_metrics", "missing"),
        ("perspective_probe_state", "malformed"),
        ("dynamic_mover_timing", "malformed"),
        ("water_interaction_field_metrics", "malformed"),
        ("perspective_probe_state", "failed"),
        ("dynamic_mover_timing", "failed"),
        ("water_interaction_field_metrics", "failed"),
    ],
)
def test_lifecycle_fact_mutants_fail_closed(field, mutation):
    row = _lifecycle_row_for_contract_tests()
    trace = row["frame_trace"]
    if mutation == "missing":
        trace.pop(field)
    elif mutation == "malformed":
        trace[field] = []
    elif field == "perspective_probe_state":
        trace[field] = {"valid": False}
    else:
        trace[field] = {"pass": False}
    facts = mc.summarize_lifecycle_facts([row])
    coverage = mc.summarize_required_field_coverage(
        [row], {"frame_count": 1, "pair_count": 0, "comparable_pair_count": 0,
                "moving_frame_pairs": 0, "frame_metrics": []},
    )
    assert facts["required"] is True
    assert facts["all_valid"] is False
    assert coverage["all_present"] is False
    assert "lifecycle_facts" in coverage["missing"]


def test_deleted_probe_candidate_field_is_not_a_required_gate():
    rows = [
        {
            "frame_id": 0,
            "row_type": "fl4359_jitter_cell",
            "probe_candidate_fallback_cell": True,
        },
        {
            "frame_id": 1,
            "row_type": "fl4359_jitter_cell",
            "probe_candidate_fallback_cell": False,
        },
    ]
    summary = {
        "frame_count": 2,
        "row_count": 2,
        "probe_candidate_fallback_cell_count": 1,
    }
    coverage = mc.summarize_required_field_coverage(rows, summary)
    gates = mc.build_gate_summary(summary, _args())

    assert "probe_candidate_fallback_cell" not in coverage["fields"]
    assert "evidence_no_probe_candidate_fallback_cells" not in gates


def test_mountain_material_distribution_gate_flags_dirt_dominance():
    rows = [
        {
            "frame_id": 0,
            "selected_owner": "terrain",
            "final_product_material_family": 1,
            "final_product_material_id": 4,
            "terrain_product_detail": 1 << 3,
            "screen_cell": {"x": 1, "y": 1},
            "terrain_cell": {"x": 10, "y": 10},
        },
        {
            "frame_id": 0,
            "selected_owner": "terrain",
            "final_product_material_family": 1,
            "final_product_material_id": 2,
            "terrain_product_detail": 1 << 27,
            "screen_cell": {"x": 2, "y": 1},
            "terrain_cell": {"x": 11, "y": 10},
        },
        {
            "frame_id": 0,
            "selected_owner": "terrain",
            "final_product_material_family": 2,
            "final_product_material_id": 5,
            "terrain_product_detail": 1 << 3,
            "screen_cell": {"x": 3, "y": 1},
            "terrain_cell": {"x": 12, "y": 10},
        },
    ]
    metrics = mc.summarize_mountain_material_metrics(rows)
    gates = mc.build_gate_summary(metrics, _args())

    assert metrics["mountain_material_rows"] == 3
    assert metrics["mountain_material_role_counts"] == {"cliff_rock": 1, "dirt": 2}
    assert gates["evidence_mountain_material_distribution_present"]["pass"] is True
    assert gates["evidence_mountain_dirt_not_dominant"]["pass"] is False


def test_mountain_material_distribution_gate_accepts_mixed_cliff_dominant_rows():
    rows = [
        {
            "frame_id": 0,
            "selected_owner": "terrain",
            "final_product_material_family": 2,
            "final_product_material_id": 5,
            "terrain_product_detail": 1 << 3,
        },
        {
            "frame_id": 0,
            "selected_owner": "terrain",
            "final_product_material_family": 2,
            "final_product_material_id": 5,
            "terrain_product_detail": 1 << 27,
        },
        {
            "frame_id": 0,
            "selected_owner": "terrain",
            "final_product_material_family": 1,
            "final_product_material_id": 4,
            "terrain_product_detail": 1 << 3,
        },
    ]
    metrics = mc.summarize_mountain_material_metrics(rows)
    gates = mc.build_gate_summary(metrics, _args())

    assert metrics["mountain_dirt_ratio"] < mc.MOUNTAIN_DIRT_DOMINANCE_LIMIT
    assert gates["evidence_mountain_material_distribution_present"]["pass"] is True
    assert gates["evidence_mountain_dirt_not_dominant"]["pass"] is False
    assert gates["evidence_mountain_dirt_not_dominant"]["baseline_calibrated"] is False


def test_terrain_product_homogeneity_reports_final_product_concentration():
    rows = [_row(0, index, 1) for index in range(4)]
    for row in rows[:3]:
        row["final_product_material_id"] = 4
        row["final_product_dirt_mountain_role"] = True
        row["final_product_glyph"] = 58
        row["final_product_exact_identity"]["cell_mask_winner_glyph"] = 58
    rows[0]["final_product_fg_rgb"] = [32, 32, 32]
    rows[0]["final_product_bg_rgb"] = [16, 16, 16]
    rows[1]["final_product_fg_rgb"] = [96, 96, 96]
    rows[1]["final_product_bg_rgb"] = [32, 32, 32]
    rows[2]["final_product_fg_rgb"] = [160, 160, 160]
    rows[2]["final_product_bg_rgb"] = [64, 64, 64]
    for row in rows[:3]:
        row["final_paint_slot_fg_rgb"] = row["final_product_fg_rgb"]
        row["final_paint_slot_bg_rgb"] = row["final_product_bg_rgb"]
    rows[3]["final_product_material_id"] = 5
    rows[3]["final_product_material_family"] = 2
    rows[3]["final_product_glyph"] = 91
    rows[3]["final_product_exact_identity"]["cell_mask_winner_glyph"] = 91

    metrics = mc.summarize_terrain_product_homogeneity_metrics(rows)

    assert metrics["terrain_product_homogeneity_source"] == "final_product_b55"
    assert metrics["terrain_product_homogeneity_rows"] == 4
    assert metrics["terrain_product_homogeneity_invalid_rows"] == 0
    assert metrics["terrain_product_dominant_material_id"] == "4"
    assert metrics["terrain_product_dominant_material_ratio"] == 0.75
    assert metrics["terrain_product_glyph_source"] == "cell_mask_winner_glyph"
    assert metrics["terrain_product_dominant_glyph_id"] == "58"
    assert metrics["terrain_product_dominant_glyph_ratio"] == 0.75
    assert metrics["terrain_product_dirt_mountain_fg_luma_rows"] == 3
    assert metrics["terrain_product_material4_dirt_mountain_fg_luma_rows"] == 3
    assert metrics[
        "terrain_product_material4_dirt_mountain_fg_luma_p90_p10_spread"
    ] == pytest.approx(128.0 / 255.0)
    assert metrics[
        "terrain_product_material4_dirt_mountain_ink_contrast_p10"
    ] == pytest.approx(16.0 / 255.0)
    assert metrics[
        "terrain_product_material4_dirt_mountain_ink_contrast_p90"
    ] == pytest.approx(96.0 / 255.0)
    assert metrics["terrain_final_paint_slot_distinct_glyphs"] == 1
    assert metrics["terrain_product_final_paint_glyph_join_rows"] == 4
    assert metrics["terrain_product_final_paint_colour_join_rows"] == 4
    assert metrics["terrain_product_final_paint_glyph_equal_rows"] == 0
    assert metrics["terrain_product_final_paint_fg_equal_rows"] == 4
    assert metrics["terrain_product_final_paint_bg_equal_rows"] == 4
    assert metrics["terrain_product_final_paint_signature_equal_rows"] == 0
    assert metrics["terrain_product_final_paint_first_difference_counts"] == {
        "glyph_id": 4,
    }
    assert metrics["terrain_product_homogeneity_baseline_calibrated"] is False


def test_terrain_product_final_paint_join_excludes_actor_and_missing_mask_slots():
    direct = _row(0, 0, 1)
    actor = _row(0, 1, 1)
    actor["final_paint_slot_actor_overlay"] = True
    missing = _row(0, 2, 1)
    missing["final_paint_slot_present"] = False

    metrics = mc.summarize_terrain_product_homogeneity_metrics(
        [direct, actor, missing]
    )

    assert metrics["terrain_product_final_paint_glyph_join_rows"] == 1
    assert metrics["terrain_product_final_paint_colour_join_rows"] == 1
    assert metrics["terrain_product_final_paint_actor_overlay_rows"] == 1
    assert metrics["terrain_product_final_paint_eligible_rows"] == 2
    assert metrics["terrain_product_final_paint_missing_slot_rows"] == 1
    assert metrics["terrain_product_final_paint_signature_equal_rows"] == 1
    assert metrics["terrain_product_final_paint_first_difference_counts"] == {
        "none": 1,
    }


def test_terrain_product_final_paint_colour_rejects_legacy_cell_header_alias():
    row = _row(0, 0, 1)
    row["final_paint_slot_colour_present"] = False
    row["final_paint_slot_fg_rgb"] = None
    row["final_paint_slot_bg_rgb"] = None
    # The legacy header happens to equal Product, but it names one cell pixel,
    # not this subcell. It must not become colour-agreement evidence.
    row["fg_rgb"] = row["final_product_fg_rgb"]
    row["bg_rgb"] = row["final_product_bg_rgb"]

    metrics = mc.summarize_terrain_product_homogeneity_metrics([row])

    assert metrics["terrain_product_final_paint_glyph_join_rows"] == 1
    assert metrics["terrain_product_final_paint_colour_join_rows"] == 0
    assert metrics["terrain_product_final_paint_missing_colour_rows"] == 1
    assert metrics["terrain_product_final_paint_first_difference_counts"] == {
        "colour_witness_missing": 1,
    }


def test_terrain_product_final_paint_completeness_gate_rejects_missing_colour():
    row = _row(0, 0, 1)
    row["final_paint_slot_colour_present"] = False
    row["final_paint_slot_fg_rgb"] = None
    row["final_paint_slot_bg_rgb"] = None
    metrics = mc.summarize_terrain_product_homogeneity_metrics([row])

    gate = mc.build_gate_summary(metrics, _args())[
        "evidence_terrain_product_final_paint_join_complete"
    ]

    assert gate["pass"] is False
    assert gate["eligible_non_actor_rows"] == 1
    assert gate["glyph_join_rows"] == 1
    assert gate["colour_join_rows"] == 0
    assert gate["missing_colour_rows"] == 1


def test_terrain_product_compiler_distribution_reports_many_to_one_stage():
    rows = [_row(0, index, 1) for index in range(3)]
    for index, row in enumerate(rows):
        row["final_product_terrain_colour_witness_bits"] = (
            _terrain_colour_witness(float(index))
        )
    shared_normal = rows[0]["final_product_terrain_colour_witness_bits"][
        "continuous_normal_cast"
    ]
    rows[1]["final_product_terrain_colour_witness_bits"][
        "continuous_normal_cast"
    ] = list(shared_normal)

    metrics = mc.summarize_terrain_product_compiler_distribution(rows)
    material_4 = metrics["terrain_product_compiler_distribution_scopes"][
        "material_4"
    ]
    transition = material_4["adjacent_transitions"][
        "corner_heights->continuous_normal_cast"
    ]

    assert material_4["input_rows"] == 3
    assert material_4["invalid_witness_rows"] == 0
    assert material_4["stages"]["corner_heights"]["distinct_exact_tuples"] == 3
    assert material_4["stages"]["continuous_normal_cast"][
        "distinct_exact_tuples"
    ] == 2
    assert transition["many_to_one_successor_tuples"] == 1
    assert transition["many_to_one_rows"] == 2
    assert transition["max_predecessors_per_successor"] == 2


def test_terrain_product_compiler_distribution_fails_invalid_witness_closed():
    valid = _row(0, 0, 1)
    invalid = _row(0, 1, 1)
    invalid["final_product_terrain_colour_witness_valid"] = False

    metrics = mc.summarize_terrain_product_compiler_distribution([valid, invalid])
    all_terrain = metrics["terrain_product_compiler_distribution_scopes"][
        "all_terrain"
    ]

    assert all_terrain["input_rows"] == 2
    assert all_terrain["invalid_witness_rows"] == 1
    assert all_terrain["stages"]["corner_heights"]["valid_rows"] == 1


def test_product_admission_contingency_valid_and_flow_accounts_asymmetric_cell():
    row = _row(0, 3, 17)
    metrics = mc.summarize_product_admission_contingency([row])
    assert metrics["product_admission_contingency_valid"] is True
    assert metrics["product_admission_contingency_total"] == 1
    assert metrics["product_admission_contingency_total_matches_input"] is True
    assert metrics["product_admission_contingency_counts"][
        "terrain_full_chain_valid"
    ] == 1
    flow = metrics["product_admission_flow_histogram"]
    assert len(flow) == 1
    assert flow[0]["authored_material_id"] == 4
    assert flow[0]["classifier_family"] == 1
    assert flow[0]["b55_material_id"] == 4
    assert flow[0]["b55_material_family"] == 1
    assert flow[0]["ratio"] == 1.0


def test_product_admission_compares_elected_cell_glyph_to_b43_not_quarter_glyph():
    row = _row(0, 3, 17)
    row["final_product_glyph"] = 99

    metrics = mc.summarize_product_admission_contingency([row])

    assert metrics["product_admission_gameplay_chain_valid"] is True
    assert metrics["product_admission_contingency_counts"][
        "terrain_full_chain_valid"
    ] == 1

    row["final_product_exact_identity"]["cell_mask_winner_glyph"] = 98
    metrics = mc.summarize_product_admission_contingency([row])
    assert metrics["product_admission_contingency_counts"][
        "terrain_b55_to_b43_mismatch"
    ] == 1


def test_product_admission_contingency_missing_field_fails_closed():
    row = _row(0, 2, 11)
    del row["final_product_authored_material_id"]
    metrics = mc.summarize_product_admission_contingency([row])
    counts = metrics["product_admission_contingency_counts"]
    assert metrics["product_admission_contingency_valid"] is False
    assert counts["terrain_admission_witness_missing"] == 1
    assert metrics["product_admission_flow_histogram_total"] == 0
    assert metrics["product_admission_contingency_total"] == 1


def test_product_admission_contingency_corrupt_mapping_is_not_normalized():
    row = _row(0, 2, 11)
    row["final_product_authored_source_key"] += 1
    metrics = mc.summarize_product_admission_contingency([row])
    counts = metrics["product_admission_contingency_counts"]
    assert counts["terrain_authored_admission_to_b55_mismatch"] == 1
    assert metrics["product_admission_contingency_valid"] is True
    assert metrics["product_admission_evidence_valid"] is True
    assert metrics["product_admission_gameplay_chain_valid"] is False
    assert metrics["product_admission_first_collapse_category"] == (
        "terrain_authored_admission_to_b55_mismatch"
    )
    assert metrics["product_admission_flow_histogram_total"] == 1

    row = _row(0, 2, 11)
    row["final_product_classifier_family"] = 0
    metrics = mc.summarize_product_admission_contingency([row])
    assert metrics["product_admission_contingency_counts"][
        "terrain_classifier_to_b55_mismatch"
    ] == 1
    assert metrics["product_admission_evidence_valid"] is True
    assert metrics["product_admission_gameplay_chain_valid"] is False
    assert metrics["product_admission_first_collapse_category"] == (
        "terrain_classifier_to_b55_mismatch"
    )

    row = _row(0, 2, 11)
    row["final_paint_slot_fg_rgb"] = [88, 70, 60]
    metrics = mc.summarize_product_admission_contingency([row])
    assert metrics["product_admission_contingency_counts"][
        "terrain_b55_to_b43_mismatch"
    ] == 1
    assert metrics["product_admission_evidence_valid"] is True
    assert metrics["product_admission_gameplay_chain_valid"] is False


def test_product_admission_contingency_separates_b43_actor_overlay():
    row = _row(0, 2, 11)
    row["final_paint_slot_actor_overlay"] = True
    metrics = mc.summarize_product_admission_contingency([row])
    counts = metrics["product_admission_contingency_counts"]
    assert counts["terrain_b55_to_b43_actor_overlay"] == 1
    assert counts["terrain_b55_to_b43_missing"] == 0
    assert metrics["product_admission_contingency_valid"] is True


def test_product_admission_contingency_invalid_profile_and_role_fails_closed():
    row = _row(0, 2, 11)
    row["final_product_matlib_profile_validity_bits"] = 0
    metrics = mc.summarize_product_admission_contingency([row])
    assert metrics["product_admission_contingency_counts"][
        "terrain_admission_witness_invalid"
    ] == 1

    row = _row(0, 2, 11)
    row["final_product_material_family"] = None
    metrics = mc.summarize_product_admission_contingency([row])
    assert metrics["product_admission_contingency_counts"][
        "terrain_classifier_to_b55_mismatch"
    ] == 1
    assert metrics["product_admission_flow_histogram_total"] == 0
    assert metrics["product_admission_contingency_total"] == 1

    row = _row(0, 2, 11)
    row["final_product_authored_role_family"] = 1
    metrics = mc.summarize_product_admission_contingency([row])
    assert metrics["product_admission_contingency_counts"][
        "terrain_admission_witness_invalid"
    ] == 1


def test_legacy_capture_without_unsnapped_falls_back_to_camera_world():
    # No unsnapped field (old capture): motion must still come from camera_world.
    frames = {
        fid: [{"frame_id": fid, "camera_world": {"x": float(fid), "y": 0.0, "z": 0.0}}]
        for fid in range(4)
    }
    m = mc.summarize_shimmer_metrics(frames)
    assert m["moving_frame_pairs"] == 3


def _write_stationary_capture(path):
    # 2 frames, camera held still -> scenario_moving_camera_continuous FAILS, so
    # gates do not all pass. Used to prove --fail-on-gate exit propagation.
    import json as _json
    rows = []
    for fid in range(2):
        rows.append({
            "frame_id": fid,
            "camera_world": {"x": 0.0, "y": 0.0, "z": 0.0},
            "camera_world_unsnapped": {"x": 0.0, "y": 0.0, "z": 0.0},
            "ansi_cell_world_anchor": {"x": 10.0, "y": 0.0, "z": 10.0},
            "selected_owner": "terrain",
            "selected_product_id": 1,
            "terrain_cell": [10, 10],
        })
    with open(path, "w") as fh:
        for r in rows:
            fh.write(_json.dumps(r) + "\n")


def _write_three_frame_capture(path):
    import json as _json
    rows = []
    for fid in range(3):
        rows.append({
            "frame_id": fid,
            "camera_world": {"x": float(fid), "y": 0.0, "z": 0.0},
            "camera_world_unsnapped": {"x": float(fid), "y": 0.0, "z": 0.0},
            "ansi_cell_world_anchor": {"x": 10.0, "y": 0.0, "z": 10.0},
            "selected_owner": "terrain",
            "selected_product_id": 1,
            "terrain_cell": [10, 10],
        })
    with open(path, "w") as fh:
        for r in rows:
            fh.write(_json.dumps(r) + "\n")


def test_cli_defaults_to_all_consecutive_pairs_for_summary():
    import json as _json
    import subprocess
    import sys
    import tempfile
    import os
    fd, cap = tempfile.mkstemp(suffix=".jsonl")
    out_fd, out = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.close(out_fd)
    try:
        _write_three_frame_capture(cap)
        result = subprocess.run(
            [sys.executable, str(_MOD_PATH), cap, "--summary-json", out],
            capture_output=True,
        )
        assert result.returncode == 0
        summary = _json.loads(Path(out).read_text())
        assert summary["pair_count"] == 2
        assert [(p["frame_a"], p["frame_b"]) for p in summary["pair_results"]] == [(0, 1), (1, 2)]

        targeted = subprocess.run(
            [sys.executable, str(_MOD_PATH), cap, "--frame-a", "1", "--frame-b", "2", "--summary-json", out],
            capture_output=True,
        )
        assert targeted.returncode == 0
        targeted_summary = _json.loads(Path(out).read_text())
        assert targeted_summary["pair_count"] == 1
        assert targeted_summary["pair_results"][0]["frame_a"] == 1
        assert targeted_summary["pair_results"][0]["frame_b"] == 2
    finally:
        os.unlink(cap)
        os.unlink(out)


def test_fail_on_gate_propagates_nonzero_exit():
    # The proof wrapper relies on analyzer.returncode. The analyzer MUST exit
    # nonzero with --fail-on-gate when gates fail, and 0 without the flag.
    import subprocess
    import sys
    import tempfile
    import os
    fd, cap = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    try:
        _write_stationary_capture(cap)
        with_flag = subprocess.run(
            [sys.executable, str(_MOD_PATH), cap, "--all-frames", "--fail-on-gate"],
            capture_output=True,
        )
        without_flag = subprocess.run(
            [sys.executable, str(_MOD_PATH), cap, "--all-frames"],
            capture_output=True,
        )
        assert with_flag.returncode != 0, "stationary capture must fail gates under --fail-on-gate"
        assert without_flag.returncode == 0, "without --fail-on-gate the analyzer should not exit nonzero"
    finally:
        os.unlink(cap)


def test_expected_red_gates_do_not_block_diagnostic_exit():
    args = _args(
        min_moving_frame_pairs=4,
        min_camera_motion_ratio=0.5,
        expected_red_gate=["evidence_mountain_dirt_not_dominant"],
        min_same_key_terrain_rows=50,
    )
    summary = {
        "row_count": 2,
        "frame_count": 2,
        "projection_fields_present_frames": 2,
        "camera_projection_modes": {0: 2},
        "projection_contract_invalid_frames": 0,
        "pixel_camera_snap_world_observed_frames": 2,
        "pixel_camera_snap_world_values": [0.0],
        "pixel_camera_snap_world_nonzero_frames": 0,
        "product_time_values": [0.35],
        "product_time_constant": True,
        "moving_frame_pairs": 4,
        "camera_motion_continuous_ratio": 1.0,
        "camera_translation_total": 1.0,
        "camera_translation_max_per_frame": 0.25,
        "min_comparable_cells": 200,
        "min_nonzero_comparable_cells": 200,
        "total_comparable_cells": 800,
        "comparable_pair_count": 4,
        "comparable_pairs_meeting_min": 4,
        "required_comparable_pairs": 4,
        "water_splat_winner_hidden_as_sky_count": 0,
        "mountain_material_rows": 10,
        "mountain_dirt_ratio": 1.0,
        "mountain_material_role_counts": {"DIRT": 10},
        "terrain_product_homogeneity_source": "final_product_b55",
        "terrain_product_homogeneity_rows": 10,
        "terrain_product_homogeneity_invalid_rows": 0,
        "terrain_product_dominant_material_ratio": 0.5,
        "terrain_product_homogeneity_baseline_calibrated": True,
        "presentation_observer_population_accounting_valid": True,
        "terrain_product_final_paint_eligible_rows": 10,
        "terrain_product_final_paint_actor_overlay_rows": 0,
        "terrain_product_final_paint_glyph_join_rows": 10,
        "terrain_product_final_paint_colour_join_rows": 10,
        "terrain_product_final_paint_missing_slot_rows": 0,
        "terrain_product_final_paint_missing_colour_rows": 0,
        "foliage_fields_present_all_frames": True,
        "foliage_fields_present_frames": 2,
        "max_snap_px_delta": 0.0,
        "snap_sign_flip_rate": 0.0,
        "max_pair_jitter_pct": 0.0,
        "fixed_screen_temporal_evidence_present": True,
        "fixed_screen_temporal_comparable_cells": 800,
        "fixed_screen_visible_signature_flip_pct": 0.0,
        "motion_compensated_final_cell_instability_pct": 0.0,
        "corrected_final_cell_oracle_valid": True,
        "continuous_product_colour_stability": {
            "counts": {"comparable_stable_semantic_samples": 800},
            "invalid_terrain_row_count": 0,
            "invalid_colour_witness_row_count": 0,
            "invalid_finalization_witness_row_count": 0,
            "comparable_pair_count": 4,
            "min_comparable_samples_per_pair": 200,
            "max_instability_pct": 0.0,
        },
        "same_key_terrain_rows": 100,
        "same_key_semantic_flip_observed": False,
        "final_product_source_key_count": 100,
        "product_owner_name_transport_rows": 10,
        "product_owner_name_transport_invalid_rows": 0,
        "same_key_terrain_rows": 100,
        "surface_identity_split_diagnostics_present": True,
        "max_selected_surface_identity_churn_pct": 0.0,
        "max_post_surface_bounce_pct": 0.0,
        "required_field_coverage": {"all_present": True, "present": [], "missing": []},
        "product_anchor_provenance_complete": True,
        "cell_mask_owner_stability": {
            "counts": {"comparable_cells": 800, "stable_cells": 800},
            "invalid_cell_count": 0,
            "comparable_pair_count": 4,
            "max_instability_pct": 0.0,
        },
    }
    gates = mc.build_gate_summary(summary, args)
    assert gates["evidence_mountain_dirt_not_dominant"]["pass"] is False
    assert gates["failed_gates"] == ["evidence_mountain_dirt_not_dominant"]
    assert gates["blocking_failed_gates"] == []
    assert gates["all_pass"] is False
    assert gates["all_non_expected_pass"] is True


def test_continuous_product_colour_gate_rejects_missing_finalization_witnesses():
    args = _args(
        min_moving_frame_pairs=4,
        min_comparable_cells=200,
        min_camera_motion_ratio=0.5,
    )
    summary = {
        "moving_frame_pairs": 4,
        "camera_motion_continuous_ratio": 1.0,
        "camera_translation_total": 1.0,
        "continuous_product_colour_stability": {
            "counts": {"comparable_stable_semantic_samples": 800},
            "invalid_terrain_row_count": 0,
            "invalid_colour_witness_row_count": 0,
            "invalid_finalization_witness_row_count": 1,
            "comparable_pair_count": 4,
            "min_comparable_samples_per_pair": 200,
            "max_instability_pct": 0.0,
        },
    }

    gate = mc.build_gate_summary(summary, args)[
        "gameplay_continuous_product_colour_stability_bounded"
    ]
    assert gate["pass"] is False
    assert gate["invalid_finalization_witness_row_count"] == 1


def test_full_grid_gate_rejects_a_mislabeled_product_owner_transport():
    summary = {
        "product_owner_name_transport_rows": 160460,
        "product_owner_name_transport_invalid_rows": 42,
        "product_owner_name_transport_mismatch_pairs": {
            "water->dynamic_mesh": 42,
        },
    }

    gate = mc.build_gate_summary(summary, _args())[
        "evidence_product_owner_name_transport_valid"
    ]
    assert gate["pass"] is False
    assert gate["authority"] == "b55_numeric_product_owner_enum"
    assert gate["invalid_derived_label_rows"] == 42


def test_full_grid_profile_keeps_temporal_presentation_symptoms_blocking():
    gates = mc.build_gate_summary(
        {},
        _args(proof_profile=mc.FULL_GRID_TEMPORAL_ORACLE_PROFILE),
    )

    required = {
        "gameplay_moving_camera_jitter_bounded",
        "gameplay_content_shimmer_bounded",
        "gameplay_continuous_product_colour_stability_bounded",
        "evidence_terminal_expected_reprojection_valid",
        "gameplay_terminal_expected_cell_address_exact",
    }
    assert required <= set(gates["blocking_profile_gates"])
    assert required <= set(gates["blocking_failed_gates"])


def test_full_grid_profile_blocks_absent_or_invalid_product_admission_witness():
    absent = mc.build_gate_summary(
        {}, _args(proof_profile=mc.FULL_GRID_TEMPORAL_ORACLE_PROFILE)
    )
    assert absent["evidence_product_admission_contingency_valid"]["pass"] is False
    assert "evidence_product_admission_contingency_valid" in absent[
        "blocking_failed_gates"
    ]
    assert absent["gameplay_product_admission_chain_exact"]["pass"] is False
    assert "gameplay_product_admission_chain_exact" in absent[
        "blocking_failed_gates"
    ]
    invalid = {
        "product_admission_contingency": {
            "product_admission_contingency_valid": False,
            "product_admission_contingency_total_matches_input": True,
            "product_admission_flow_histogram_total_matches_valid_witness_rows": True,
            "product_admission_terrain_rows": 1,
            "product_admission_contingency_counts": {
                "terrain_admission_witness_missing": 1,
                "terrain_admission_witness_invalid": 0,
            },
        }
    }
    invalid_gates = mc.build_gate_summary(
        invalid, _args(proof_profile=mc.FULL_GRID_TEMPORAL_ORACLE_PROFILE)
    )
    assert invalid_gates["evidence_product_admission_contingency_valid"][
        "pass"
    ] is False
    assert invalid_gates["gameplay_product_admission_chain_exact"]["pass"] is False


def test_product_admission_classifier_red_is_structurally_valid_but_not_promotable():
    row = _row(0, 3, 17)
    row["final_product_classifier_family"] = 0
    admission = mc.summarize_product_admission_contingency([row])
    gates = mc.build_gate_summary(
        {"product_admission_contingency": admission},
        _args(proof_profile=mc.FULL_GRID_TEMPORAL_ORACLE_PROFILE),
    )
    assert admission["product_admission_evidence_valid"] is True
    assert admission["product_admission_gameplay_chain_valid"] is False
    assert gates["evidence_product_admission_contingency_valid"]["pass"] is True
    assert gates["gameplay_product_admission_chain_exact"]["pass"] is False
    assert "gameplay_product_admission_chain_exact" in gates[
        "blocking_failed_gates"
    ]


def test_terminal_reprojection_residual_stays_diagnostic_without_calibration_law():
    summary = {
        "terminal_expected_reprojection": {
            "valid": True,
            "baseline_calibrated": False,
            "acceptance_threshold_authorized": False,
            "max_residual_px": 0.0,
            "expected_cell_address_pass": True,
            "counts": {"comparable_terminal_displacements": 10},
            "invalid": {},
            "excluded": {},
        },
    }
    gates = mc.build_gate_summary(
        summary,
        _args(proof_profile=mc.FULL_GRID_TEMPORAL_ORACLE_PROFILE),
    )

    assert gates["evidence_terminal_expected_reprojection_valid"]["pass"] is True
    assert gates["gameplay_terminal_expected_cell_address_exact"]["pass"] is True
    assert gates["gameplay_terminal_expected_reprojection_bounded"]["pass"] is False
    assert (
        "gameplay_terminal_expected_reprojection_bounded"
        not in gates["blocking_failed_gates"]
    )
    assert (
        "gameplay_terminal_expected_reprojection_bounded"
        in gates["diagnostic_failed_gates"]
    )


def test_surface_bounce_gate_retains_selected_surface_churn_as_nonblocking_b64_diagnostic():
    args = _args(
        min_moving_frame_pairs=4,
        min_camera_motion_ratio=0.5,
        min_same_key_terrain_rows=50,
    )
    summary = {
        "row_count": 2,
        "frame_count": 2,
        "projection_fields_present_frames": 2,
        "pixel_camera_snap_world_observed_frames": 2,
        "pixel_camera_snap_world_values": [],
        "moving_frame_pairs": 4,
        "camera_motion_continuous_ratio": 1.0,
        "camera_translation_total": 1.0,
        "camera_translation_max_per_frame": 0.25,
        "min_comparable_cells": 200,
        "min_nonzero_comparable_cells": 200,
        "total_comparable_cells": 800,
        "comparable_pair_count": 4,
        "comparable_pairs_meeting_min": 4,
        "required_comparable_pairs": 4,
        "water_splat_winner_hidden_as_sky_count": 0,
        "mountain_material_rows": 10,
        "mountain_dirt_ratio": 0.0,
        "mountain_material_role_counts": {"cliff_rock": 10},
        "terrain_product_homogeneity_rows": 10,
        "terrain_product_homogeneity_invalid_rows": 0,
        "terrain_product_dominant_material_ratio": 0.5,
        "terrain_product_homogeneity_baseline_calibrated": True,
        "presentation_observer_population_accounting_valid": True,
        "foliage_fields_present_all_frames": True,
        "foliage_fields_present_frames": 2,
        "max_snap_px_delta": 0.0,
        "snap_sign_flip_rate": 0.0,
        "max_pair_jitter_pct": 0.0,
        "same_key_terrain_rows": 100,
        "same_key_semantic_flip_observed": False,
        "surface_identity_split_diagnostics_present": True,
        "max_selected_surface_identity_churn_pct": 100.0,
        "max_post_surface_bounce_pct": 0.0,
    }

    gates = mc.build_gate_summary(summary, args)

    gate = gates["gameplay_surface_identity_bounce_bounded"]
    assert gate["pass"] is True, gate
    assert gate["selected_surface_identity_churn_is_blocking"] is False
    assert gate["selected_surface_identity_scope"] == "b64 diagnostic; not the matched b55 Product identity"
    assert gate["max_selected_surface_identity_churn_pct"] == 100.0
    assert gate["max_post_surface_bounce_pct"] == 0.0


def test_current_airplane_wrapper_uses_moving_camera_analyzer_without_comparison_controls():
	# The current runner still fails closed through the moving-camera analyzer,
	# but it must not expose retired comparison summaries and caller-selected
	# expected-red exemptions.
	wrapper = (Path(__file__).resolve().parent / "run_fl4265_airplane_proof.py").read_text()
	assert "analyze_fl4359_moving_camera.py" in wrapper
	assert "--fail-on-gate" in wrapper
	assert "--expected-red-gate" not in wrapper
	assert "--sunday" + "-summary-json" not in wrapper
	assert "return analyzer.returncode" in wrapper
	assert "--fl4359-visual-capture-stride" in wrapper
	assert "--fl4359-visual-capture-limit" in wrapper
	assert "--fl4359-visual-capture-start-frame" in wrapper
	assert "fl4359_moving_capture" in wrapper
	assert "git_head" in wrapper
	assert "git_dirty_paths" in wrapper
	assert "res://" in wrapper
	assert "--fl4359-jitter-capture-frame-start" in wrapper
	assert "--fl4359-jitter-capture-frame-limit" in wrapper
	assert "--fl4359-jitter-capture-world-sample-stride" in wrapper
	assert "--fl4359-clean-play-frames" in wrapper
	assert "--fl4359-clean-play-no-capture" in wrapper
	assert "args.fl4359_clean_play_frames > 0" in wrapper
	assert "cmd.append(\"--fl4359-clean-play-no-capture\")" in wrapper
	assert 'GODOT_UNCAPPED_ARGS = ["--disable-vsync", "--max-fps", "0"]' in wrapper
	assert "GODOT_UNCAPPED_ARGS" in wrapper
	assert 'receipt["frame_pacing"]' in wrapper
	assert 'frame_pacing.get("uncapped") is not True' in wrapper
	assert 'runtime_frame_pacing.get("vsync_disabled") is not True' in wrapper
	assert 'receipt.get("canonical_fullscreen_surface_ready") is not True' in wrapper
	assert '"window_size"' in wrapper
	harness = (
		Path(__file__).resolve().parents[1]
		/ "godot_project/tools/proofs/run_fl4265_airplane_dogfight_proof.gd"
	).read_text()
	assert "FL4431_CANONICAL_PRESENTATION_SIZE := Vector2i(3600, 2252)" in harness
	assert "func _await_canonical_fullscreen_surface() -> Dictionary:" in harness
	assert "FL4431_FULLSCREEN_STABLE_FRAMES := 10" in harness
	assert '"canonical_fullscreen_surface_ready": canonical_surface_ready' in harness
	assert 'elif arg == "--fl4667-sustained-fire-performance":' in harness
	assert "Input.action_press(FL4265_DAMAGE_FIRE_ACTION, 1.0)" in harness
	assert 'world.get("projectile_snapshots")' in harness
	assert '"sustained_fire_projectile_frames"' in harness
	assert '"max_live_projectile_count"' in harness
	assert '"--fl4667-sustained-fire-performance"' in wrapper
	assert 'first_intent.get("fire") is not True' in wrapper
	assert 'reasons.append("sustained_fire_live_projectiles_missing")' in wrapper
	assert "--extra-user-arg" in wrapper
	assert "--godot-project" in wrapper
	assert "--godot-script" in wrapper
	assert "godot_project = Path(args.godot_project)" in wrapper
	assert "godot_script = args.godot_script" in wrapper
	assert 'receipt["godot_project"] = str(godot_project)' in wrapper
	assert 'receipt["godot_script"] = godot_script' in wrapper


def test_current_airplane_damage_route_has_one_product_evidence_path():
    wrapper = (
        Path(__file__).resolve().parent / "run_fl4265_airplane_proof.py"
    ).read_text()
    for retired_symbol in (
        "faa36357eef9dee7e29b9333cb4c1397ca27b929",
        "Baseline A",
        "FL4431_DAMAGE_BASELINE_COMMIT",
        "FL4431_DAMAGE_BASELINE_PROFILE",
        "_damage_renderer_profile",
    ):
        assert retired_symbol not in wrapper
    assert (
        'FL4431_DAMAGE_CURRENT_PROFILE = '
        '"current_retained_vsb_product_b62_mat211_v1"'
    ) in wrapper
    assert "--fl4431-damage-renderer-profile" not in wrapper
    assert "FL4431_DAMAGE_PROFILE_ARG" not in wrapper
    assert "cmd.append(FL4431_SECONDARY_EFFECT_EVIDENCE_ARG)" in wrapper
    for current_product_fact in (
        "retained_mat211_face_count",
        "product_mat211_contribution_receipt_available",
        "product_mat211_contributed_subcell_count",
        "product_mat211_contribution_bit",
        "product_mat211_style",
    ):
        assert current_product_fact in wrapper


def test_headed_movement_wrapper_passes_expected_red_gates_to_analyzer():
    wrapper = (Path(__file__).resolve().parent / "run_fl4359_headed_movement.py").read_text()
    assert "--expected-red-gate" in wrapper
    assert "args.expected_red_gate" in wrapper
    assert 'analyzer_cmd.extend(["--expected-red-gate", gate_name])' in wrapper
    assert "NOISY_COMPLETION_GUARD_ARGS" in wrapper
    assert '"--fl4057-probe-diagnostics"' in wrapper
    assert "noisy probe diagnostics are disabled for completion runs" in wrapper
    assert "RECEIPT_SUMMARY_FIELDS" in wrapper
    assert "summary[\"receipt_summary\"] = receipt_subset" in wrapper
    assert "summary[\"completion\"]" in wrapper
    assert "summary[\"provenance\"]" in wrapper
    assert "repo_provenance=repo_provenance" in wrapper
    assert '"git_head": wrapper_provenance.get("git_head", "")' in wrapper
    assert '"analyzer_rc": analyzer_rc' in wrapper
    assert "airplane_dynamic_face_count" in wrapper
    assert "movement_delta" in wrapper
    assert "camera_delta" in wrapper
    assert '"camera_yaw_turn_delta"' in wrapper
    assert 'summary["turn_metadata"]' in wrapper
    assert "motion_compensated_final_cell_instability_pct" in wrapper
    assert "sparse_capture_completed_request_count" in wrapper
    assert "proof-profile" in wrapper


def test_headed_movement_wrapper_rejects_timeout_partial_capture():
    wrapper = (Path(__file__).resolve().parent / "run_fl4359_headed_movement.py").read_text()
    assert "DEFAULT_GODOT_TIMEOUT_SECONDS = 180.0" in wrapper
    assert "DEFAULT_JITTER_CAPTURE_FRAME_LIMIT = 5" in wrapper
    # FL-4359: exhaustive coverage replaced the old stride-64 sampling default.
    assert "DEFAULT_JITTER_CAPTURE_WORLD_SAMPLE_STRIDE = 1" in wrapper
    assert "MAX_COMPLETION_JITTER_CAPTURE_FRAME_LIMIT = 5" in wrapper
    assert "MIN_COMPLETION_JITTER_CAPTURE_WORLD_SAMPLE_STRIDE = 1" in wrapper
    assert "--allow-large-jitter-capture" in wrapper
    assert "DEFAULT_AIRPLANE_METADATA_FRAME_CAP = 12" in wrapper
    assert "DEFAULT_INITIAL_TURN_FRAMES = 8" in wrapper
    assert "DEFAULT_AIRPLANE_MOVEMENT_FRAMES = 12" in wrapper
    assert "--plane-turn-yaw" not in wrapper
    assert "--plane-turn-roll" not in wrapper
    assert "default=DEFAULT_INITIAL_TURN_FRAMES" in wrapper
    assert '"timed_out_before_completion_receipt"' in wrapper
    assert '"process_timed_out": godot_rc == 124' in wrapper
    assert '"missing_completion_receipt"' in wrapper
    assert '"admissible": False' in wrapper
    assert "partial capture not analyzed" in wrapper
    assert "elif invalid_reason:" in wrapper
    assert "elif capture_path.exists():" not in wrapper
    assert "--manual-smoke" not in wrapper
    assert "manual_smoke" not in wrapper
    assert "proof_gates_bypassed" not in wrapper
    assert "a clean Godot exit and completion receipt are required" in wrapper
    assert 'summary["jitter_metadata"]' in wrapper
    assert 'summary["turn_metadata"]' in wrapper
    assert 'summary["shimmer_metadata"]' in wrapper
    assert 'summary["bounce_metadata"]' in wrapper
    assert 'summary["bounded_capture_metadata"]' in wrapper
    assert '"process_timed_out": godot_rc == 124' in wrapper
    assert 'summary["pass"] = proof_pass' in wrapper
    assert 'summary["proof_pass"] = proof_pass' in wrapper
    assert 'summary["receipt_pass"] = bool(receipt.get("pass", False))' in wrapper
    assert 'summary["receipt_reason"] = receipt.get("reason")' in wrapper
    assert 'summary["diagnostic_status"]' in wrapper
    assert 'summary["acceptance_claimed"] = False' in wrapper
    assert 'summary["closure_claimed"] = False' in wrapper
    assert 'if key in ("pass", "reason"):' in wrapper
    assert '"jitter_capture_world_sample_stride": jitter_capture_world_sample_stride' in wrapper
    assert '"route_mode": "bounded_consecutive_frame_window"' in wrapper
    assert "DIAGNOSTIC_OBSERVED" in wrapper
    assert "DIAGNOSTIC_INVALID" in wrapper


def test_headed_movement_invalid_summary_records_timeout_flag():
    runner_path = Path(__file__).resolve().parent / "run_fl4359_headed_movement.py"
    spec = importlib.util.spec_from_file_location("run_fl4359_headed_movement", runner_path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    import json as _json
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_raw:
        tmp = Path(tmp_raw)
        summary_path = tmp / "analysis_summary.json"
        receipt_path = tmp / "receipt.json"
        capture_path = tmp / "jitter_capture.jsonl"
        runner._write_invalid_summary(
            summary_path,
            reason="timed_out_before_completion_receipt",
            godot_rc=124,
            receipt_path=receipt_path,
            capture_path=capture_path,
            repo_provenance={
                "git_head": "abc123",
                "git_dirty": True,
                "git_dirty_count": 1,
                "git_dirty_paths": [" M godot_project/example.gd"],
            },
            analyzer_rc=1,
        )
        summary = _json.loads(summary_path.read_text())

    assert summary["admissible"] is False
    assert summary["process_timed_out"] is True
    assert summary["reason"] == "timed_out_before_completion_receipt"
    assert summary["git_head"] == "abc123"
    assert summary["git_dirty"] is True
    assert summary["git_dirty_paths"] == [" M godot_project/example.gd"]
    assert summary["provenance"]["runtime"]["godot_rc"] == 124
    assert summary["provenance"]["runtime"]["analyzer_rc"] == 1


def test_fl4671_lerp_receipt_diagnostic_accepts_scripted_airborne_route():
    runner_path = Path(__file__).resolve().parent / "run_fl4359_headed_movement.py"
    spec = importlib.util.spec_from_file_location("run_fl4359_headed_movement", runner_path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    summary = {"analysis_skipped": True, "reason": "fl4671_lerp_receipt_diagnostic"}
    receipt = {"route_identity": runner.SCRIPTED_AIRBORNE_AIRPLANE_ROUTE_IDENTITY}
    fl4671_summary = {
        "route_mounted_witness_present": True,
        "receipt_rows_present": True,
        "all_correction_errors_zero": True,
    }

    assert runner._fl4671_lerp_receipt_diagnostic_accepts_analysis_skip(
        summary,
        receipt,
        True,
        fl4671_summary,
    )


def test_headed_movement_zero_godot_timeout_uses_finite_default():
    runner_path = Path(__file__).resolve().parent / "run_fl4359_headed_movement.py"
    spec = importlib.util.spec_from_file_location("run_fl4359_headed_movement", runner_path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    assert runner._godot_subprocess_timeout(0) == runner.DEFAULT_GODOT_TIMEOUT_SECONDS
    assert runner._godot_subprocess_timeout(-1) == runner.DEFAULT_GODOT_TIMEOUT_SECONDS
    assert runner._godot_subprocess_timeout(2.5) == 2.5
    assert not math.isfinite(runner._godot_subprocess_timeout(float("inf")))
    assert not math.isfinite(runner._godot_subprocess_timeout(float("-inf")))
    assert not math.isfinite(runner._godot_subprocess_timeout(float("nan")))
    wrapper = runner_path.read_text()
    assert "NO_GODOT_TIMEOUT_HELP" in wrapper
    assert "cannot hang indefinitely" in wrapper
    assert "finite Godot timeout policy" in wrapper
    assert "timeout=godot_timeout" in wrapper
    assert '"timeout_policy": "finite_subprocess_timeout"' in wrapper
    assert '"requested_godot_timeout_seconds": requested_godot_timeout_seconds' in wrapper
    assert '"effective_godot_timeout_seconds": godot_timeout_seconds' in wrapper
    assert '"godot_timeout_disabled": False' in wrapper


def test_headed_airplane_report_labels_current_and_legacy_metrics():
    runner_path = Path(__file__).resolve().parent / "run_fl4359_headed_movement.py"
    spec = importlib.util.spec_from_file_location("run_fl4359_headed_movement", runner_path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    summary = {
        "moving_frame_pairs": 4,
        "camera_motion_continuous_ratio": 1.0,
        "max_snap_px_delta": 0.125,
        "avg_snap_px_delta": 0.031,
        "snap_sign_flip_rate": 0.0,
        "motion_compensated_final_cell_instability_pct": 2.5,
        "final_cell_signature_churn_pct": 3.5,
        "max_post_surface_bounce_pct": 4.5,
        "max_selected_surface_identity_churn_pct": 1.25,
        "max_product_identity_churn_pct": 0.5,
        "max_pair_jitter_pct": 0.0,
        "gates": {"scenario_moving_camera_continuous": {"pass": True}},
    }

    joined = "\n".join(runner._current_metric_report_lines(summary))

    assert "current_metric_definitions" in joined
    assert "jitter positional instability" in joined
    assert "shimmer final visible signature instability" in joined
    assert "bounce region-scale oscillation" in joined
    assert "current_jitter(positional instability)=MEASURED" in joined
    assert "current_shimmer(final visible signature instability)=MEASURED" in joined
    assert "current_bounce(region-scale oscillation)=MEASURED" in joined
    assert "legacy_narrow_pair_metric: max_pair_jitter_pct=0.00%" in joined
    assert "label=legacy_not_current_shimmer_bounce" in joined

    wrapper = runner_path.read_text()
    assert "[fl4359-headed] shimmer: frames=" not in wrapper
    assert "max_jitter=%.2f%%" not in wrapper
    assert "legacy_max_pair_jitter" in wrapper


def test_headed_airplane_report_fails_closed_for_missing_current_metrics():
    runner_path = Path(__file__).resolve().parent / "run_fl4359_headed_movement.py"
    spec = importlib.util.spec_from_file_location("run_fl4359_headed_movement", runner_path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    old_metric_only_summary = {
        "moving_frame_pairs": 4,
        "camera_motion_continuous_ratio": 1.0,
        "max_snap_px_delta": 0.0,
        "avg_snap_px_delta": 0.0,
        "snap_sign_flip_rate": 0.0,
        "max_pair_jitter_pct": 0.0,
        "gates": {"scenario_moving_camera_continuous": {"pass": True}},
    }
    old_metric_lines = "\n".join(runner._current_metric_report_lines(old_metric_only_summary))

    assert "current_jitter(positional instability)=MEASURED" in old_metric_lines
    assert (
        "current_shimmer(final visible signature instability)=UNMEASURED "
        "reason=missing_motion_compensated_final_cell_instability_pct"
    ) in old_metric_lines
    assert (
        "current_bounce(region-scale oscillation)=UNMEASURED "
        "reason=missing_post_surface_bounce_metric"
    ) in old_metric_lines
    assert "legacy_narrow_pair_metric: max_pair_jitter_pct=0.00%" in old_metric_lines

    no_motion_summary = {
        "moving_frame_pairs": 0,
        "camera_motion_continuous_ratio": 0.0,
        "max_snap_px_delta": 0.0,
        "motion_compensated_final_cell_instability_pct": 0.0,
        "max_post_surface_bounce_pct": 0.0,
        "max_pair_jitter_pct": 0.0,
        "gates": {"scenario_moving_camera_continuous": {"pass": False}},
    }
    no_motion_lines = "\n".join(runner._current_metric_report_lines(no_motion_summary))

    assert "current_jitter(positional instability)=UNMEASURED reason=missing_valid_consecutive_frame_movement_metric" in no_motion_lines
    assert "current_shimmer(final visible signature instability)=UNMEASURED reason=missing_valid_consecutive_frame_movement_metric" in no_motion_lines
    assert "current_bounce(region-scale oscillation)=UNMEASURED reason=missing_valid_consecutive_frame_movement_metric" in no_motion_lines
    assert "current_shimmer(final visible signature instability)=MEASURED" not in no_motion_lines
    assert "current_bounce(region-scale oscillation)=MEASURED" not in no_motion_lines


def test_full_grid_plane_route_preflight_owns_exact_14_frame_window_and_hard_cap():
    runner = _load_headed_runner()
    args = argparse.Namespace(
        camera_mode="plane_air",
        run_classification=runner.RUN_CLASSIFICATION_MOVEMENT_PROOF,
        godot_window_mode="fullscreen",
        turn_action="rotate_left",
        plane_throttle=0.37,
        plane_pitch_base=0.02,
        plane_pitch_amp=0.03,
        plane_roll_base=-0.12,
        plane_roll_amp=0.04,
        plane_circle_frames=12,
        turn_frames=8,
        skip_jitter_analysis=False,
        jitter_capture_frame_limit=12,
        jitter_capture_world_sample_stride=1,
        airplane_metadata_frame_cap=12,
        allow_large_jitter_capture=True,
        exact_camera_projection="perspective",
        product_time=17.0,
        day_cycle_time=0.5,
        after_frames=2,
        analyzer_proof_profile="full_grid_temporal_oracle_v1",
        extra_user_arg=[],
    )
    assert runner._invalid_airplane_proof_bounds_reason(args) == ""
    preflight = runner._airplane_route_preflight(
        args,
        effective_plane_circle_frames=12,
        effective_turn_frames=8,
        effective_jitter_capture_frame_limit=12,
        godot_timeout_seconds=600.0,
    )
    assert preflight["ok"] is True
    assert preflight["effective_total_capture_frames"] == 14
    assert preflight["checks"]["full_grid_release_and_settle_exact"] is True
    assert preflight["checks"]["full_grid_total_capture_frames_bounded"] is True

    args.day_cycle_time = 0.75
    wrong_time = runner._airplane_route_preflight(
        args, effective_plane_circle_frames=12, effective_turn_frames=8,
        effective_jitter_capture_frame_limit=12, godot_timeout_seconds=600.0,
    )
    assert wrong_time["ok"] is False
    assert "fixed_day_cycle_time" in wrong_time["failed_checks"]
    args.day_cycle_time = 0.5

    args.run_classification = runner.RUN_CLASSIFICATION_DIAGNOSTIC_ONLY
    diagnostic_preflight = runner._airplane_route_preflight(
        args,
        effective_plane_circle_frames=12,
        effective_turn_frames=8,
        effective_jitter_capture_frame_limit=12,
        godot_timeout_seconds=600.0,
    )
    assert diagnostic_preflight["ok"] is True
    assert (
        diagnostic_preflight["checks"]["diagnostic_nonproof_classification"]
        is True
    )
    args.run_classification = runner.RUN_CLASSIFICATION_MOVEMENT_PROOF

    args.jitter_capture_frame_limit = 19
    assert (
        runner._invalid_airplane_proof_bounds_reason(args)
        == "full_grid_temporal_capture_total_exceeds_20"
    )
    args.jitter_capture_frame_limit = 12
    args.after_frames = 3
    assert (
        runner._invalid_airplane_proof_bounds_reason(args)
        == "full_grid_after_frames_must_equal_two"
    )


def test_headed_plane_air_bounded_metadata_caps_long_route():
    runner_path = Path(__file__).resolve().parent / "run_fl4359_headed_movement.py"
    spec = importlib.util.spec_from_file_location("run_fl4359_headed_movement", runner_path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    bounded_plane = argparse.Namespace(
        camera_mode="plane_air",
        skip_jitter_analysis=False,
        plane_circle_frames=90,
        turn_frames=30,
        jitter_capture_frame_limit=60,
        airplane_metadata_frame_cap=12,
        jitter_capture_world_sample_stride=64,
        allow_large_jitter_capture=False,
    )
    assert runner._airplane_metadata_frame_window(bounded_plane) == (12, 12)
    assert runner._effective_airplane_turn_frames(bounded_plane, 12) == 12
    short_plane = argparse.Namespace(
        camera_mode="plane_air",
        skip_jitter_analysis=False,
        plane_circle_frames=5,
        turn_frames=8,
        jitter_capture_frame_limit=8,
        airplane_metadata_frame_cap=12,
        jitter_capture_world_sample_stride=64,
        allow_large_jitter_capture=False,
    )
    assert runner._airplane_metadata_frame_window(short_plane) == (5, 5)
    assert runner._effective_airplane_turn_frames(short_plane, 5) == 5
    visual_grid = argparse.Namespace(
        camera_mode="plane_air",
        skip_jitter_analysis=True,
        plane_circle_frames=90,
        turn_frames=0,
        jitter_capture_frame_limit=60,
        airplane_metadata_frame_cap=12,
        jitter_capture_world_sample_stride=16,
        allow_large_jitter_capture=False,
    )
    assert runner._airplane_metadata_frame_window(visual_grid) == (90, 60)
    assert runner._invalid_airplane_route_contract_reason(visual_grid, 90) == ""
    ground_capture = argparse.Namespace(
        camera_mode="perspective",
        skip_jitter_analysis=False,
        plane_circle_frames=90,
        turn_frames=0,
        jitter_capture_frame_limit=60,
        airplane_metadata_frame_cap=12,
        jitter_capture_world_sample_stride=16,
        allow_large_jitter_capture=False,
    )
    assert runner._airplane_metadata_frame_window(ground_capture) == (90, 60)
    unbounded_plane = argparse.Namespace(
        camera_mode="plane_air",
        skip_jitter_analysis=False,
        plane_circle_frames=90,
        turn_frames=8,
        jitter_capture_frame_limit=0,
        airplane_metadata_frame_cap=12,
        jitter_capture_world_sample_stride=64,
        allow_large_jitter_capture=False,
    )
    assert runner._invalid_airplane_proof_bounds_reason(unbounded_plane) == "jitter_capture_frame_limit_must_be_positive"
    uncapped_plane = argparse.Namespace(
        camera_mode="plane_air",
        skip_jitter_analysis=False,
        plane_circle_frames=90,
        turn_frames=8,
        jitter_capture_frame_limit=60,
        airplane_metadata_frame_cap=0,
        jitter_capture_world_sample_stride=64,
        allow_large_jitter_capture=False,
    )
    assert runner._invalid_airplane_proof_bounds_reason(uncapped_plane) == "airplane_metadata_frame_cap_must_be_positive"
    diagnostic_plane = argparse.Namespace(
        camera_mode="plane_air",
        skip_jitter_analysis=True,
        plane_circle_frames=90,
        turn_frames=0,
        jitter_capture_frame_limit=0,
        airplane_metadata_frame_cap=0,
        jitter_capture_world_sample_stride=1,
        allow_large_jitter_capture=False,
    )
    assert runner._invalid_airplane_proof_bounds_reason(diagnostic_plane) == ""
    dense_frame_plane = argparse.Namespace(
        camera_mode="plane_air",
        skip_jitter_analysis=False,
        plane_circle_frames=12,
        turn_frames=8,
        jitter_capture_frame_limit=8,
        airplane_metadata_frame_cap=12,
        jitter_capture_world_sample_stride=64,
        allow_large_jitter_capture=False,
    )
    assert runner._invalid_airplane_proof_bounds_reason(dense_frame_plane) == "jitter_capture_frame_limit_requires_large_capture_flag"
    dense_stride_plane = argparse.Namespace(
        camera_mode="plane_air",
        skip_jitter_analysis=False,
        plane_circle_frames=12,
        turn_frames=8,
        jitter_capture_frame_limit=5,
        airplane_metadata_frame_cap=12,
        jitter_capture_world_sample_stride=16,
        allow_large_jitter_capture=False,
    )
    # Current bounded default is exhaustive stride 1, not historical stride 64.
    # This validates output bounds only; full-grid coverage has its own gate.
    for stride in (1, 16, 64):
        dense_stride_plane.jitter_capture_world_sample_stride = stride
        assert runner._invalid_airplane_proof_bounds_reason(dense_stride_plane) == ""
    for large in (False, True):
        dense_stride_plane.allow_large_jitter_capture = large
        for stride in (0, -1):
            dense_stride_plane.jitter_capture_world_sample_stride = stride
            assert runner._invalid_airplane_proof_bounds_reason(dense_stride_plane) == "jitter_capture_world_sample_stride_must_be_positive"
    explicit_large_marker_plane = argparse.Namespace(
        camera_mode="plane_air",
        skip_jitter_analysis=False,
        plane_circle_frames=12,
        turn_frames=8,
        jitter_capture_frame_limit=8,
        airplane_metadata_frame_cap=12,
        jitter_capture_world_sample_stride=16,
        allow_large_jitter_capture=True,
    )
    assert runner._invalid_airplane_proof_bounds_reason(explicit_large_marker_plane) == ""
    no_turn_plane = argparse.Namespace(
        camera_mode="plane_air",
        skip_jitter_analysis=False,
        plane_circle_frames=12,
        turn_frames=0,
        jitter_capture_frame_limit=8,
        airplane_metadata_frame_cap=12,
        jitter_capture_world_sample_stride=64,
        allow_large_jitter_capture=True,
    )
    assert runner._invalid_airplane_route_contract_reason(no_turn_plane, 12) == "turn_frames_must_be_positive"
    no_move_plane = argparse.Namespace(
        camera_mode="plane_air",
        skip_jitter_analysis=False,
        plane_circle_frames=0,
        turn_frames=8,
        jitter_capture_frame_limit=8,
        airplane_metadata_frame_cap=12,
        jitter_capture_world_sample_stride=64,
        allow_large_jitter_capture=True,
    )
    assert runner._invalid_airplane_route_contract_reason(no_move_plane, 1) == "plane_circle_frames_must_be_positive"
    good_receipt = {
        "first_intent_frame": 0,
        "first_turn_intent_frame": 0,
        "first_measured_motion_frame": 1,
        "measured_motion_immediate": True,
        "first_active_frame_delta": 0.6,
        "first_active_camera_turn_pressed": True,
        "first_active_flight_intent": {
            "flight": {"throttle": 1.0, "pitch": 0.05, "roll": 0.75, "yaw": 0.35},
            "fire": False,
        },
        "first_active_throttle_command": 1.0,
        "first_active_pitch_command": 0.05,
        "first_active_roll_command": 0.75,
        "first_active_yaw_command": 0.35,
        "route_identity": runner.SCRIPTED_AIRBORNE_AIRPLANE_ROUTE_IDENTITY,
        "route_kind": runner.SCRIPTED_AIRBORNE_AIRPLANE_ROUTE_IDENTITY,
        "airborne_airplane_ready": True,
        "local_airplane_pilot": True,
        "airplane_world_item_count_exact": True,
        "max_airplane_world_item_count": 2,
        "camera_yaw_held": True,
        "visual_route_ticks_exact": True,
        "missing_visual_route_ticks": [],
        "visual_frame_count": 3,
        "prearmed_motion_frames": 2,
        "active_turn_frames": 8,
        "turn_intent_frames": 8,
        "camera_yaw_turn_measured": True,
        "airplane_yaw_turn_measured": True,
        "sparse_lifecycle_facts_valid": True,
    }
    assert runner._airplane_route_receipt_accepts_proof(good_receipt, "plane_air") is True
    bad_receipt = dict(good_receipt)
    bad_receipt["airborne_airplane_ready"] = False
    assert runner._airplane_route_receipt_accepts_proof(bad_receipt, "plane_air") is False
    wrong_route = dict(good_receipt)
    wrong_route["route_identity"] = runner.SERVER_TICK_AIRPLANE_ROUTE_IDENTITY
    assert runner._airplane_route_receipt_accepts_proof(wrong_route, "plane_air") is False
    missing_lifecycle = dict(good_receipt)
    missing_lifecycle.pop("sparse_lifecycle_facts_valid")
    assert runner._airplane_route_receipt_accepts_proof(missing_lifecycle, "plane_air") is False

    wrapper = runner_path.read_text()
    assert "--airplane-metadata-frame-cap" in wrapper
    assert "--allow-large-jitter-capture" in wrapper
    assert "airplane proof captures must be bounded for completion evidence" in wrapper
    assert "jitter_capture_frame_limit_requires_large_capture_flag" in wrapper
    assert "jitter_capture_world_sample_stride_requires_large_capture_flag" in wrapper
    assert "airplane proof route must start active movement on frame 0" in wrapper
    assert "default=DEFAULT_AIRPLANE_MOVEMENT_FRAMES" in wrapper
    assert "effective_plane_circle_frames" in wrapper
    assert "effective_turn_frames" in wrapper
    assert 'f"--fl4359-plane-circle-frames={effective_plane_circle_frames}"' in wrapper
    assert 'f"--fl4359-turn-frames={effective_turn_frames}"' in wrapper
    # The VSB cutover deleted the in-game jitter producer.  The legacy limit is
    # still parsed so old callers receive the explicit external-recorder error,
    # but it must not be forwarded into the active Godot command as if the game
    # still owned that evidence stream.
    assert 'f"--fl4359-jitter-capture-frame-limit={effective_jitter_capture_frame_limit}"' in wrapper
    assert '"requested_plane_circle_frames": requested_plane_circle_frames' in wrapper
    assert '"effective_plane_circle_frames": effective_plane_circle_frames' in wrapper
    assert '"requested_turn_frames": requested_turn_frames' in wrapper
    assert '"effective_turn_frames": effective_turn_frames' in wrapper
    assert '"requested_jitter_capture_frame_limit": requested_jitter_capture_frame_limit' in wrapper
    assert '"effective_jitter_capture_frame_limit": effective_jitter_capture_frame_limit' in wrapper


def test_headed_plane_air_default_artifact_dir_is_single_fl4359_directory():
    runner_path = Path(__file__).resolve().parent / "run_fl4359_headed_movement.py"
    spec = importlib.util.spec_from_file_location("run_fl4359_headed_movement", runner_path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    out_dir = runner._default_output_dir(argparse.Namespace(camera_mode="plane_air"), timestamp="20260703-120000")
    assert out_dir == runner.ROOT / ".run" / "fl4359_airplane_movement_20260703-120000"
    assert out_dir.parent == runner.ROOT / ".run"
    assert out_dir.name.startswith("fl4359_")
    assert out_dir.parent.name != "fl4359_headed_movement"


def test_current_package_has_no_dead_expected_red_gate_surface():
    wrapper = (Path(__file__).resolve().parent / "run_fl4359_acceptance_package.py").read_text()
    # This package never launches the retired in-game jitter analyzer, so a
    # caller-supplied red-gate list could not affect its admission. Keep that
    # false control physically absent instead of forwarding a no-op.
    assert "--expected-red-gate" not in wrapper
    assert "expected_red_gates" not in wrapper
    assert 'result.get("renderer") == "current"' in wrapper
    assert '"current_package_ready_for_runtime_review"' in wrapper


def test_current_package_wires_fixed_dual_projection_clean_play_arms():
    wrapper = (Path(__file__).resolve().parent / "run_fl4359_acceptance_package.py").read_text()
    assert 'PROBE_GEOMETRY_CUTOVER_DISABLE_ARG = "--no-fl4057-probe-geometry-cutover"' in wrapper
    assert 'MIN_CLEAN_PLAY_FPS = 60.0' in wrapper
    assert '"perspective": ("--fl4270-force-perspective",)' in wrapper
    assert '"ortho": ("--fl4270-force-ortho",)' in wrapper
    assert "--current-extra-user-arg" not in wrapper
    assert "--current-metric-readback" not in wrapper
    assert "--fl4359-jitter-capture-frame-start" not in wrapper
    assert "--fl4359-jitter-capture-frame-limit" not in wrapper
    assert "--fl4359-jitter-capture-world-sample-stride" not in wrapper
    assert "MOVING_CAPTURE_HARNESS" in wrapper
    assert "--godot-script" in wrapper
    assert "def run_current_clean_play_arm" in wrapper
    assert "--fl4359-clean-play-frames" in wrapper
    assert "--fl4359-clean-play-no-capture" in wrapper
    assert '"performance_evidence": True' in wrapper
    assert '"visual_evidence": False' in wrapper
    assert 'results["current_clean_play_perspective"]' in wrapper
    assert 'results["current_clean_play_ortho"]' in wrapper
    assert "current_clean_play_arm_ok(" in wrapper
    assert "fps_sample_count" in wrapper
    assert "measured_fps >= MIN_CLEAN_PLAY_FPS" in wrapper


def test_godot_capture_harness_globalizes_artifact_paths():
	root = Path(__file__).resolve().parents[1]
	harness = (root / "godot_project" / "tools" / "proofs" / "run_fl4265_airplane_dogfight_proof.gd").read_text()
	assert "func _artifact_global_path" in harness
	assert "func _artifact_resource_path" in harness
	assert "ProjectSettings.globalize_path" in harness
	assert "DirAccess.make_dir_recursive_absolute(absolute_path.get_base_dir())" in harness
	capture_body = harness.split("func _capture_frame_image() -> Dictionary:", 1)[1].split(
		"\nfunc _save_frame_image(", 1
	)[0]
	save_body = harness.split("func _save_frame_image(image: Image, path: String) -> Dictionary:", 1)[1].split(
		"\nfunc _capture_frame(", 1
	)[0]
	assert 'ascii_effect.call("capture_pixel_camera_viewport_image", vp)' in capture_body
	assert "save_png(" not in capture_body
	assert "var err := image.save_png(resource_path)" in save_body
	assert 'return {"captured": false, "reason": "save_png_failed_%d" % err}' in save_body
	assert "FileAccess.open(receipt_path" in harness
	assert "fl4359_jitter_capture_requested" in harness
	assert "if fl4359_jitter_capture_requested and ascii_effect != null" in harness
	assert "fl4359_visual_capture_start_frame" in harness
	assert "fl4359_jitter_capture_frame_start" in harness
	assert "fl4359_jitter_capture_frame_limit" in harness
	assert "fl4359_jitter_capture_world_sample_stride" in harness
	assert "fl4359_clean_play_no_capture" in harness
	assert '"measured_fps": float(total_frames) / measured_seconds' in harness
	assert "clean_play_frame_times_ms.size() == total_frames" in harness
	assert '"nonadvancing_rendered_frames": maxi(0, total_frames - moving_frames)' in harness
	assert '"clean_play_no_capture": fl4359_clean_play_no_capture' in harness
	assert "FL4359_WARMUP_MAX_FRAMES" in harness
	# The old render-frame-segment route changed its flight command on renderer
	# cadence, so a slower arm flew a different path. The active contract holds
	# one real input and measures diagnostic route ticks from one local origin.
	assert "const FL4359_ROUTE_CAPTURE_WARMUP_TICKS := 30" in harness
	assert 'const FL4359_ROUTE_FLIGHT_ACTION := "move_right"' in harness
	assert "const FL4359_FRONT_DOOR_MIN_DELTA := 0.05" in harness
	assert '"throttle": 1.0' not in harness
	assert "_set_fl4359_route_flight_input(true)" in harness
	assert "route_origin_tick = candidate_tick" in harness
	assert "current_route_tick == FL4359_ROUTE_CAPTURE_WARMUP_TICKS" in harness
	assert 'route_error = "clean_route_tick_missed"' not in harness
	assert "current_route_tick > prior_route_tick" in harness
	assert 'route_error = "clean_route_end_tick_overshoot"' in harness
	assert '"clean_route_end_tick_overshoot": clean_route_end_tick_overshoot' in harness
	assert 'route_error = "clean_route_start_tick_overshoot"' in harness
	assert '"clean_route_start_tick_overshoot": clean_route_start_tick_overshoot' in harness
	assert 'var camera_yaw_hold_required := requested_camera_mode_label == "true_perspective"' in harness
	assert "camera_yaw_held or not camera_yaw_hold_required" in harness
	assert "_fl4359_motion_position(player, camera)" in harness
	assert '"first_turn_intent_frame": first_turn_intent_frame' in harness
	assert '"active_turn_frames": 0' in harness
	assert '"measured_frame_cap": measured_frame_cap' in harness
	assert '"turn_intent_frames": total_frames' in harness
	assert '"camera_yaw_turn_delta": 0.0' in harness
	assert '"camera_yaw_turn_measured": false' in harness
	assert '"route_identity": route_identity' in harness
	assert '"clean_route_pose_samples": clean_route_pose_samples' in harness


def test_compositor_has_no_automatic_jitter_readback_in_clean_play():
	root = Path(__file__).resolve().parents[1]
	compositor = (root / "godot_project" / "render" / "api" / "ASCIICompositorEffect.gd").read_text()
	# The retired per-frame jitter sampler performed synchronous readback during
	# play. Its complete absence is the clean-timing contract; the surviving
	# final-cell readback is explicitly request-scoped and defaults inactive.
	assert "_fl4359_should_capture_jitter_frame" not in compositor
	assert "_fl4359_keep_jitter_world_sample" not in compositor
	assert "_fl4359_keep_jitter_screen_sample" not in compositor
	assert "_fl4359_jitter_capture_source_frame" not in compositor
	assert "func request_final_cell_paint_capture(frames: int = 4) -> void:" in compositor
	assert "func _final_cell_paint_capture_active() -> bool:" in compositor
	assert "return _final_cell_paint_capture_frames > 0" in compositor


def test_headed_runner_uses_moving_camera_analyzer_with_fail_on_gate():
	# The headed movement/airplane runner is the operator-facing shimmer test.
	# It must not call the deleted stationary analyzer.
	runner = (Path(__file__).resolve().parent / "run_fl4359_headed_movement.py").read_text()
	assert "analyze_fl4359_moving_camera.py" in runner
	assert "analyze_fl4359_jitter.py" not in runner
	assert "--max-content-jitter-pct" in runner
	assert "--fail-on-gate" in runner
	assert "--jitter-capture-frame-limit" in runner
	assert "--jitter-capture-world-sample-stride" in runner
	# These compatibility arguments now lead callers to the explicit
	# external-recorder ownership failure.  They must not be forwarded as live
	# in-game producer switches after the VSB cutover removed that producer.
	assert "--fl4359-jitter-capture-frame-limit=" in runner
	assert "--fl4359-jitter-capture-world-sample-stride=" not in runner


def test_headed_turn_input_starts_concurrently_with_movement():
	root = Path(__file__).resolve().parents[1]
	driver = (root / "scripts" / "adhoc" / "fl4359_external_movement_driver.gd").read_text()
	runner = (root / "scripts" / "run_fl4359_headed_movement.py").read_text()
	run_body = driver.split("func _run() -> void:", 1)[1].split("\nfunc ", 1)[0]
	assert "var active_turn_frames := mini(_turn_frames, active_frames) if (driving_ground_input or is_plane) else 0" in run_body
	assert "var total_active_frames := active_frames + active_turn_frames" not in run_body
	assert "for _turn_frame in range(active_turn_frames):" not in run_body
	move_idx = run_body.index("_set_ground_move_input(true)")
	turn_idx = run_body.index("_set_camera_turn_input(true)")
	loop_idx = run_body.index("for frame in range(active_frames):")
	assert move_idx < turn_idx < loop_idx
	assert "if is_plane and active_frames > 0:" in run_body
	assert "var plane_intent := _plane_flight_intent(frame)" in run_body
	assert "_notify(main, plane_intent)" in run_body
	assert "if frame > 0:\n\t\t\t\t_notify(main, plane_intent)" not in run_body
	notify_idx = run_body.index("_notify(main, plane_intent)")
	await_idx = run_body.index("await process_frame", notify_idx)
	assert loop_idx < notify_idx < await_idx
	before_idx = run_body.index('abs_out.path_join("00_before.png")')
	assert run_body.count("camera if _exact_camera_position_set else null") == 3
	reset_idx = run_body.index("_reset_jitter_capture_before_active_route(main, player, camera, is_perspective, exact_diagnostic_camera_owner)")
	plane_route_idx = run_body.index("if is_plane and active_frames > 0:")
	# Reset must precede observer allocation; doing it after the screenshot
	# frees the prepared immutable slots before the measured flight.
	prepare_idx = run_body.index("_prepare_final_paint_field_before_flight")
	assert reset_idx < prepare_idx < before_idx < plane_route_idx
	assert "_prepare_jitter_capture_after_settle" not in run_body
	assert "func _reset_jitter_capture_before_active_route" in driver
	reset_body = driver.split("func _reset_jitter_capture_before_active_route", 1)[1].split("\nfunc ", 1)[0]
	assert "before active flight input" in reset_body
	assert "for _i in range(4):" not in reset_body
	assert "await process_frame" not in reset_body
	assert "func _plane_flight_intent(frame: int) -> Dictionary:" in driver
	assert "func _plane_turn_axis" not in driver
	assert "var _plane_turn_roll_axis := 0.65" in driver
	assert "var _plane_turn_yaw_axis := 0.65" in driver
	assert "var roll := _plane_roll_base + sin(phase) * _plane_roll_amp" in driver
	assert "if frame < _turn_frames:" in driver
	assert "roll = sign * _plane_turn_roll_axis" in driver
	assert "yaw = sign * _plane_turn_yaw_axis" in driver
	assert '"first_intent_frame": first_intent_frame' in run_body
	assert '"first_turn_intent_frame": first_turn_intent_frame' in run_body
	assert '"first_measured_motion_frame": first_measured_motion_frame' in run_body
	assert '"measured_motion_immediate": measured_motion_immediate' in run_body
	assert '"route_identity": route_identity' in run_body
	assert '"route_kind": route_identity' in run_body
	assert '"first_active_frame_delta": first_active_frame_delta' in run_body
	assert '"first_active_camera_turn_pressed": first_active_camera_turn_pressed' in run_body
	assert '"first_active_flight_intent": first_active_flight_intent' in run_body
	assert '"first_active_throttle_command": first_active_throttle_command' in run_body
	assert '"first_active_pitch_command": first_active_pitch_command' in run_body
	assert '"first_active_roll_command": first_active_roll_command' in run_body
	assert '"first_active_yaw_command": first_active_yaw_command' in run_body
	assert '"active_route_start_position": _vec3(active_route_start_position)' in run_body
	assert '"prearmed_motion_frames": prearmed_motion_frames' in run_body
	assert "func _plane_preflight_motion_intent() -> Dictionary:" in driver
	assert '"turn_intent_frames": turn_intent_frames' in run_body
	assert "frame + 1 == active_turn_frames" in run_body
	assert "camera_yaw_release_to_settle_catchup := absf(angle_difference(" in run_body
	assert "camera_yaw_after_turn, camera_yaw_after_turn_settle_sample" in run_body
	assert "camera_yaw_post_turn_drift := absf(angle_difference(" in run_body
	assert "camera_yaw_after_turn_settle_sample, camera_yaw_after" in run_body
	assert "camera_yaw_after_turn_settle_sample = _camera_yaw(player)" in run_body
	assert "camera_yaw_post_turn_settle_ok := active_turn_frames == 0 or camera_yaw_post_turn_drift <= 0.002" in run_body
	assert "camera_yaw_turn_measured := active_turn_frames == 0 or absf(angle_difference(camera_yaw_before, camera_yaw_after_turn)) > 0.002" in run_body
	assert "airplane_yaw_turn_delta_sample := airplane_yaw_after_turn_settle_sample if is_plane else airplane_yaw_after_turn" in run_body
	assert "airplane_yaw_turn_measured := active_turn_frames == 0 or absf(airplane_yaw_turn_delta) > 0.002" in run_body
	assert "player_position_post_input_drift := player_at_input_release.distance_to(player_after) if input_release_observed else 0.0" in run_body
	assert "camera_position_post_input_drift := camera_at_input_release.distance_to(camera_after) if input_release_observed else 0.0" in run_body
	assert "camera_player_offset_post_input_drift := camera_player_offset_at_input_release.distance_to(camera_player_offset_after) if input_release_observed else 0.0" in run_body
	assert "post_input_camera_offset_settle_ok := not input_release_observed or camera_player_offset_post_input_drift <= 0.25" in run_body
	grounded_acceptance = run_body.split("else:\n\t\tok = (", 1)[1].split("\n\tok = ok and", 1)[0]
	assert "and actor_rows > 0" in grounded_acceptance
	assert "and physical_ground_action_observed" in grounded_acceptance
	assert "and (active_turn_frames == 0 or physical_turn_action_observed)" in grounded_acceptance
	assert "and camera_yaw_turn_measured" in grounded_acceptance
	assert "and camera_yaw_post_turn_settle_ok" in grounded_acceptance
	assert "and post_input_camera_offset_settle_ok" in grounded_acceptance
	assert "ok = ok and airplane_faces > 0 and camera_after.distance_to(camera_before) > 0.05 and camera_yaw_turn_measured" in run_body
	assert "camera_yaw_turn_measured and airplane_yaw_turn_measured" not in run_body
	assert "post-turn settling is the bounce symptom under test" in run_body
	assert "ok = ok and airplane_faces > 0 and camera_after.distance_to(camera_before) > 0.05 and camera_yaw_turn_measured and camera_yaw_post_turn_settle_ok" not in run_body
	assert "airplane_turn_not_measured" in run_body
	assert "airplane_flight_turn_not_measured" not in run_body
	assert "airplane_active_route_no_immediate_motion" in run_body
	assert "airplane_dynamic_faces_not_observed" in run_body
	assert "airplane_camera_motion_not_observed" in run_body
	assert '"camera_yaw_post_turn_drift": camera_yaw_post_turn_drift' in run_body
	assert '"camera_yaw_release_to_settle_catchup": camera_yaw_release_to_settle_catchup' in run_body
	assert '"screen_recording_requested": screen_recording_requested' in run_body
	assert '"screen_recording_started": screen_recording_started' in run_body
	assert '"screen_recording_stopped": screen_recording_stopped' in run_body
	assert '"screen_recording_path": screen_recording_path' in run_body
	assert '"screen_recording_bytes": screen_recording_bytes' in run_body
	assert "env[\"ASCIICKER_SCREEN_RECORDING_ALLOWED\"] = \"1\"" in runner
	assert "env[\"ASCIICKER_SCREEN_RECORDING_MIN_FREE_BYTES\"] = str(8 * 1024**3)" in runner
	assert "_launch_macos_godot_bundle(" in runner
	assert "_activate_macos_window(godot_child.pid, timeout=45.0)" in runner
	assert '(out_dir / "window_activation.json").write_text(' in runner
	godot_launch = runner.split("godot_rc = subprocess.run(", 1)[1].split(").returncode", 1)[0]
	assert "env=env" in godot_launch
	assert "var sparse_capture_requested := _capture_path != \"\" and _sparse_capture_frame_limit > 0" in run_body
	assert '"camera_yaw_turn_measured": camera_yaw_turn_measured' in run_body
	assert '"airplane_yaw_turn_measured": airplane_yaw_turn_measured' in run_body
	assert '"airplane_yaw_turn_delta": airplane_yaw_turn_delta' in run_body
	assert '"camera_yaw_post_turn_settle_ok": camera_yaw_post_turn_settle_ok' in run_body
	assert '"player_position_post_input_drift": player_position_post_input_drift' in run_body
	assert '"camera_position_post_input_drift": camera_position_post_input_drift' in run_body
	assert '"camera_player_offset_post_input_drift": camera_player_offset_post_input_drift' in run_body
	assert '"post_input_camera_offset_settle_ok": post_input_camera_offset_settle_ok' in run_body


def test_airplane_camera_preserves_manual_orbit_on_atomic_heading():
	# FL-4359: RQ-188 / FL-4481 (2026-09-09) supersedes the old world-yaw
	# lock. Test persistent orbit plus authoritative heading, not the retired rig.
	root = Path(__file__).resolve().parents[1]
	player = (root / "godot_project" / "scripts" / "player.gd").read_text()
	mouse_block = player.split("if event is InputEventMouseMotion", 1)[1].split("# FL-4320", 1)[0]
	keyboard_block = player.split("func _process_keyboard_rotation(delta):", 1)[1].split("\n\n## Ground-based movement", 1)[0]
	chase_body = player.split("func _update_airplane_chase_camera(delta: float) -> void:", 1)[1].split("\n\n# FL-4270: flight uses", 1)[0]

	assert "plane yaw must not rewrite it after input release" in player
	assert "if _is_airplane_pilot():" in mouse_block
	assert "camera_pivot.rotate_y(-event.relative.x * sensitivity)" in mouse_block
	assert "_chase_offset_yaw = wrapf(_chase_offset_yaw - event.relative.x * sensitivity, -PI, PI)" in mouse_block
	assert "if _is_airplane_pilot():" in keyboard_block
	assert "elif camera_pivot != null:" in keyboard_block
	assert "camera_pivot.rotate_y(rotate_axis * rate * delta)" in keyboard_block
	assert "_chase_offset_yaw = wrapf(_chase_offset_yaw + rotate_axis * rate * delta, -PI, PI)" in keyboard_block
	assert '_airplane_pose_usable(_frame_airplane_pose)' in chase_body
	assert 'var target_yaw := float(_frame_airplane_pose.get("facing_yaw", 0.0)) + PI + _chase_offset_yaw' in chase_body
	assert "camera_pivot.rotation.y = target_yaw" in chase_body
	pilot_body = chase_body.split("if camera_pivot == null:", 1)[1]
	assert "_chase_offset_yaw =" not in pilot_body
	assert "lerp_angle(" not in pilot_body


def test_airborne_airplane_runner_deletes_boarding_harness():
	root = Path(__file__).resolve().parents[1]
	deleted = root / "godot_project" / "scripts" / "run_fl4359_airplane_capture.gd"
	deleted_uid = root / "godot_project" / "scripts" / "run_fl4359_airplane_capture.gd.uid"
	driver = (root / "scripts" / "adhoc" / "fl4359_external_movement_driver.gd").read_text()
	runner = (root / "scripts" / "run_fl4359_headed_movement.py").read_text()
	assert not deleted.exists()
	assert not deleted_uid.exists()
	assert "AIRPLANE_SPAWN_AIRBORNE" in runner
	assert 'DEFAULT_AIRPLANE_PROOF_SPAWN_X = "-80.8"' in runner
	assert 'DEFAULT_AIRPLANE_PROOF_SPAWN_Z = "307.2"' in runner
	assert 'env.setdefault("AIRPLANE_SPAWN_X", DEFAULT_AIRPLANE_PROOF_SPAWN_X)' in runner
	assert 'env.setdefault("AIRPLANE_SPAWN_Z", DEFAULT_AIRPLANE_PROOF_SPAWN_Z)' in runner
	assert "airborne_airplane_spawn_not_observed" in driver
	assert "pickup_mount_airplane" not in driver
	assert "_board_and_climb" not in driver


# --- FL-4359 same-key proof slice (Codex) ------------------------------------

def _sk_row(fid, sk, sx, sy, glyph=10, detail=0, role="dirt"):
    return {
        "frame_id": fid,
        "selected_owner": "terrain",
        "source_key": sk,
        "terr_stable_cell": {"x": sx, "y": sy},
        "terr_stable_cell_gpu": {"x": sx, "y": sy},
        "glyph_id": glyph,
        "terrain_product_detail": detail,
        "material_role": role,
    }


def test_same_key_intra_frame_variant_exposes_race():
    # Two cells collide under ONE (source_key, stable_cell) in the SAME frame with
    # different glyphs -> a world-key history writer would race (Codex CRIT-1).
    frames = {0: [_sk_row(0, 5, 8, 8, glyph=10), _sk_row(0, 5, 8, 8, glyph=11)]}
    r = mc.summarize_same_key_flips(frames)
    assert r["same_key_terrain_rows"] == 2
    assert r["same_key_frame_groups"] == 1
    assert r["same_key_intra_frame_variant_groups"] == 1
    assert r["same_key_max_intra_frame_variant_count"] == 2
    assert r["same_key_semantic_flip_observed"] is True


def test_same_key_stable_identical_across_frames_is_no_op():
    # Identical key + identical signature across frames -> NO flip (Codex HIGH-3:
    # hysteresis here would be a no-op; the lane is not justified by this evidence).
    frames = {0: [_sk_row(0, 5, 8, 8, glyph=10, detail=7)],
              1: [_sk_row(1, 5, 8, 8, glyph=10, detail=7)]}
    r = mc.summarize_same_key_flips(frames)
    assert r["same_key_cross_frame_pairs"] == 1
    assert r["same_key_cross_frame_flip_pairs"] == 0
    assert r["same_key_intra_frame_variant_groups"] == 0
    assert r["same_key_semantic_flip_observed"] is False
    assert all(v == 0 for v in r["same_key_per_field_flip_counts"].values())


def test_same_key_cross_frame_flip_names_field():
    # Same key, glyph flips between frames (variant 1 each) -> cross-frame flip named.
    frames = {0: [_sk_row(0, 5, 8, 8, glyph=10)],
              1: [_sk_row(1, 5, 8, 8, glyph=11)]}
    r = mc.summarize_same_key_flips(frames)
    assert r["same_key_cross_frame_flip_pairs"] == 1
    assert r["same_key_per_field_flip_counts"]["glyph_id"] == 1
    assert r["same_key_semantic_flip_observed"] is True


def test_same_key_detail_fact_unpack_flips_named_subfield():
    # ramp_band lives in bits 5-7 of terrain_detail_fact; flipping only it must be
    # named as a ramp_band flip, proving the unpack matches the shader bit layout.
    frames = {0: [_sk_row(0, 5, 8, 8, detail=(1 << 5))],   # ramp_band=1
              1: [_sk_row(1, 5, 8, 8, detail=(2 << 5))]}   # ramp_band=2
    r = mc.summarize_same_key_flips(frames)
    assert r["same_key_per_field_flip_counts"]["ramp_band"] == 1
    assert r["same_key_per_field_flip_counts"]["glyph_id"] == 0


def test_same_key_uses_gpu_stable_cell_not_host_approximation():
    frames = {
        0: [{
            **_sk_row(0, 5, 8, 8, glyph=10),
            "terr_stable_cell": {"x": 100, "y": 100},
        }],
        1: [{
            **_sk_row(1, 5, 8, 8, glyph=11),
            "terr_stable_cell": {"x": 101, "y": 100},
        }],
    }
    r = mc.summarize_same_key_flips(frames)
    assert r["same_key_cross_frame_pairs"] == 1
    assert r["same_key_per_field_flip_counts"]["glyph_id"] == 1


def test_same_key_fails_closed_without_gpu_stable_cell():
    row = _sk_row(0, 5, 8, 8, glyph=10)
    row.pop("terr_stable_cell_gpu")
    r = mc.summarize_same_key_flips({0: [row]})
    assert r["same_key_terrain_rows"] == 0
    assert r["same_key_frame_groups"] == 0


def test_same_key_evidence_gate_fails_closed_without_rows():
    g0 = mc.build_gate_summary({"same_key_terrain_rows": 0}, _args())
    assert g0["evidence_same_key_terrain_present"]["pass"] is False
    g1 = mc.build_gate_summary({"same_key_terrain_rows": 100}, _args())
    assert g1["evidence_same_key_terrain_present"]["pass"] is True


if __name__ == "__main__":
    # Let pytest own discovery so a newly appended regression cannot sit after a
    # hand-maintained call list and produce a false direct-script PASS.
    raise SystemExit(pytest.main([str(Path(__file__).resolve()), "-q"]))
