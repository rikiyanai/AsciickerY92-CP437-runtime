#!/usr/bin/env python3
"""Pinned fixtures for the exact FL-4512 ``u7.mask.v1`` primitives."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/fl4512_u7_mask.py"
SPEC = importlib.util.spec_from_file_location("fl4512_u7_mask", MODULE_PATH)
assert SPEC and SPEC.loader
U7 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(U7)


def bit(x: int, y: int) -> int:
    return 1 << (8 * y + x)


def page16(block_coverages: dict[tuple[int, int], int]) -> list[int]:
    rgba = [0] * (16 * 16 * 4)
    for (bx, by), coverage in block_coverages.items():
        for sample in range(coverage):
            x = 2 * bx + sample % 2
            y = 2 * by + sample // 2
            p = (y * 16 + x) * 4
            rgba[p:p + 4] = [255, 255, 255, 255]
    return rgba


def test_page16_predicate_majority_bit_order_and_empty_rejection():
    mask = U7.mask8_from_page16_rgba(page16({(0, 0): 2, (7, 7): 4, (1, 0): 1}), 16, 0, 0)
    assert mask == bit(0, 0) | bit(7, 7)
    assert U7.u64_le_hex(mask) == "0100000000000080"

    rgb_zero = page16({(0, 0): 2})
    rgb_zero[0:3] = [0, 0, 0]
    rgb_zero[4:7] = [0, 0, 0]
    with pytest.raises(U7.U7MaskError, match="mask8_empty"):
        U7.mask8_from_page16_rgba(rgb_zero, 16, 0, 0)


def test_zhang_suen_fixed_point_preserves_line_endpoints():
    thick_line = 0
    for y in range(1, 7):
        thick_line |= bit(3, y) | bit(4, y)
    skeleton, endpoints, junctions = U7.topology_counts(thick_line)
    assert U7.zhang_suen(skeleton) == skeleton
    assert endpoints == 2
    assert junctions == 0


def test_adjacent_junction_pixels_collapse_to_one_cluster():
    # Two adjacent CN>=3 center pixels form one fat junction under the ABI.
    mask = (
        bit(3, 3) | bit(4, 3)
        | bit(2, 2) | bit(2, 4)
        | bit(5, 2) | bit(5, 4)
    )
    skeleton, endpoints, junctions = U7.topology_counts(mask)
    assert skeleton == mask
    assert endpoints == 4
    assert junctions == 1


def test_integer_score_and_lower_gid_tie_key():
    horizontal = sum(bit(x, 3) for x in range(1, 7))
    vertical = sum(bit(3, y) for y in range(1, 7))
    same = U7.score_num(horizontal, horizontal)
    changed = U7.score_num(horizontal, vertical)
    assert same == (0, 0, 2, 2, 0, 0)
    assert changed[0] == 70 * changed[1]
    assert 0 < changed[0] <= 6400

    candidates = [(changed[0], 601), (changed[0], 580)]
    assert min(candidates) == (changed[0], 580)


def test_descriptor_carries_exact_versions_and_no_quantizer():
    mask = sum(bit(x, 3) for x in range(1, 7))
    record = U7.descriptor(mask)
    assert record["scorer_id"] == "u7.mask.v1"
    assert record["quantization_version"] is None
    assert record["reduction_version"] == "coverage_ge_2.v1"
    assert record["skeleton_version"] == "zhang_suen.v1"
    assert record["mask8_u64_le_hex"] == mask.to_bytes(8, "little").hex()
    assert record["beta0"] == 1
    assert record["beta1"] == 0
    assert record["euler_characteristic"] == 1


def test_betti_uses_eight_connected_ink_and_four_connected_holes():
    diagonal_pair = bit(1, 1) | bit(2, 2)
    assert U7.betti_numbers(diagonal_pair) == (1, 0)

    ring = 0
    for x in range(1, 7):
        ring |= bit(x, 1) | bit(x, 6)
    for y in range(2, 6):
        ring |= bit(1, y) | bit(6, y)
    assert U7.betti_numbers(ring) == (1, 1)


def test_shipped_page16_mesh_candidates_have_nonempty_descriptors():
    aoa = json.loads(
        (ROOT / "assets/glyphs/atlases/material.additive.v1.atlas_of_atlases.json").read_text()
    )
    page = json.loads(
        (ROOT / "assets/glyphs/atlases/material.additive.v1.page16_rgba8.json").read_text()
    )
    profile = json.loads(
        (ROOT / "assets/glyphs/profiles/material_rendering_profiles.v1.json").read_text()
    )
    mesh = next(row for row in profile["profiles"] if row["material_id"] == "mesh:200")
    for gid in mesh["role_buckets"]["mesh_bundle"]["candidates"]:
        page_id, x0, y0, x1, y1 = aoa["glyph_index"][str(gid)]
        assert page_id == 0
        assert (x1 - x0, y1 - y0) == (16, 16)
        mask = U7.mask8_from_page16_rgba(page["rgba8"], page["width"], x0, y0)
        record = U7.descriptor(mask)
        assert record["mask8_u64_le_hex"] != "0000000000000000"
        assert 0 <= record["endpoint_count"] <= 64
        assert 0 <= record["junction_count"] <= 64


def test_prediction_uses_compiled_pool_and_lower_gid_tie_key():
    horizontal = sum(bit(x, 3) for x in range(1, 7))
    vertical = sum(bit(3, y) for y in range(1, 7))
    artifact = {
        "rows": [{
            "material_key": 200,
            "role": "mesh_feature",
            "route_key": "mesh_feature.bundle.v1",
            "lane": 0,
            "candidate_descriptors": [
                {"glyph_id": 601, "mask8_u64_le_hex": U7.u64_le_hex(vertical)},
                {"glyph_id": 580, "mask8_u64_le_hex": U7.u64_le_hex(horizontal)},
            ],
        }]
    }
    bridge = [{
        "kind": "cell",
        "x": 3,
        "y": 4,
        "material_id": 200,
        "axis_routing": "mesh_bundle",
        "mesh_bundle_valid": 1,
        "mesh_bundle_merged_mask_8x8": f"{horizontal:016x}",
        "final_gid": 601,
    }]
    predicted = U7.predict_mesh_bundle_winners(artifact, bridge, 200)
    assert predicted == [{
        "x": 3,
        "y": 4,
        "material_id": 200,
        "before_gid": 601,
        "predicted_gid": 580,
        "score_num": 0,
        "mesh_bundle_merged_mask_8x8": f"{horizontal:016x}",
        "scorer_id": "u7.mask.v1",
    }]


def test_prediction_excludes_the_predeclared_removed_candidate():
    horizontal = sum(bit(x, 3) for x in range(1, 7))
    vertical = sum(bit(3, y) for y in range(1, 7))
    artifact = {
        "rows": [{
            "material_key": 200,
            "role": "mesh_feature",
            "route_key": "mesh_feature.bundle.v1",
            "lane": 0,
            "candidate_descriptors": [
                {"glyph_id": 568, "mask8_u64_le_hex": U7.u64_le_hex(horizontal)},
                {"glyph_id": 580, "mask8_u64_le_hex": U7.u64_le_hex(vertical)},
            ],
        }]
    }
    bridge = [{
        "kind": "cell",
        "x": 9,
        "y": 7,
        "material_id": 200,
        "axis_routing": "mesh_bundle",
        "mesh_bundle_valid": 1,
        "mesh_bundle_merged_mask_8x8": f"{horizontal:016x}",
        "final_gid": 568,
    }]
    predicted = U7.predict_mesh_bundle_winners(
        artifact, bridge, 200, frozenset({568})
    )
    assert predicted[0]["before_gid"] == 568
    assert predicted[0]["predicted_gid"] == 580
