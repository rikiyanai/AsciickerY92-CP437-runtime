#!/usr/bin/env python3
"""Focused format-v6 tests for the FL-4512 D2/D3 Harri sidecar compiler."""

from __future__ import annotations

import importlib.util
import json
import struct
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
COMPILER_PATH = ROOT / "scripts/compile_fl4683_glyph_program.py"
SIDECAR = ROOT / "assets/glyphs/generated/fl4683.glyph_program.v1.bin"
RECEIPT = ROOT / "assets/glyphs/generated/fl4683.glyph_program_pin.v1.json"

SPEC = importlib.util.spec_from_file_location("compile_fl4683_glyph_program", COMPILER_PATH)
assert SPEC is not None and SPEC.loader is not None
compiler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compiler)


def _inputs(scorer_id: str = "fl4512.shape6_f8_w4.v2"):
    is_mesh = scorer_id in ("u7.mask.v1", "S_pf.v1")
    profile_id = "mesh:200" if is_mesh else "terrain:1"
    namespace = "mesh" if is_mesh else "terrain"
    material_key = 200 if is_mesh else 1
    fact = {
        "fact_id": "glyphfact://test/v1/fact",
        "fact_sha256": "11" * 32,
        "source_profile_id": profile_id,
        "scene_membership_key": {
            "row_sha256": "22" * 32,
            "capacity": 1,
            "scorer_id": scorer_id,
            "profile_namespace": namespace,
            "material_key": material_key,
            "route_key": (
                "mesh_feature.bundle.v1"
                if scorer_id == "u7.mask.v1"
                else "filled.field.v1"
                if scorer_id == "S_pf.v1"
                else "edge.v1"
            ),
        },
        "glyph_members": [
            {"glyph_record_id": 7, "membership_weight_q0_16": 65535}
        ],
        "subset_weight_sum_q0_16": 65535,
        "relation_projection": {"edges": []},
    }
    glyph_record = {
        "glyph_record_id": 7,
        "morphology": {
            "shape6_norm_q0_16": [101, 202, 303, 404, 505, 606]
        },
        "connectivity": {"mask8_u64_le_hex": "0102030405060708"},
        "structure": {"endpoint_count": 9, "junction_count": 10},
    }
    profile = {
        "profile_id": profile_id,
        # 0.03125 * 16 is exactly 0.5 and therefore distinguishes required
        # round-half-up from Python's default ties-to-even rounding.
        "role_weights": [0, 0.03125, 0.5, 1.0, 1.5, 2.0],
    }
    candidate_descriptor = {
        "glyph_id": 7,
        "mask8_u64_le_hex": "1112131415161718",
        "filled_region_class": 7,
        "detail_u16": 56173,
    }
    if scorer_id == "u7.mask.v1":
        candidate_descriptor = {
            "glyph_id": 7,
            "mask8_u64_le_hex": "0102030405060708",
            "endpoint_count": 9,
            "junction_count": 10,
        }
    elif scorer_id == "S_pf.v1":
        glyph_record["connectivity"]["mask8_u64_le_hex"] = "1112131415161718"

    pool = {
        "rows": [
            {
                "row_sha256": "22" * 32,
                "scorer_id": scorer_id,
                "candidate_descriptors": [candidate_descriptor],
            }
        ],
        "materials": [
            {
                "profile_namespace": namespace,
                "material_key": material_key,
                "filled_region_class": 7,
                "filled_priority_u16": 140,
                "projected_feature_extent_admission": {
                    "filled_threshold": {"enter_q": 8192, "exit_q": 4096}
                },
                "no_match_policy": {
                    "structural_degenerate_declines": True,
                    "structural_decline_fallback": "tonal",
                    "tonal_constant_operand_legal": True,
                    "max_error_q": {"structural": 700000, "tonal": 650000},
                },
            }
        ],
    }
    return [fact], [glyph_record], [profile], pool


def _encode(scorer_id: str = "fl4512.shape6_f8_w4.v2"):
    facts, glyph_records, profiles, pool = _inputs(scorer_id)
    return compiler.encode_fact_table(facts, glyph_records, profiles, pool)


def test_format_v6_member_and_fact_tails_carry_harri_inputs() -> None:
    fact_bytes, member_bytes, edge_bytes, member_count, edge_count = _encode()

    assert len(fact_bytes) == compiler.FACT_RECORD_SIZE == 128
    assert len(member_bytes) == compiler.FACT_MEMBER_SIZE == 20
    assert edge_bytes == b""
    assert (member_count, edge_count) == (1, 0)

    member = struct.unpack("<IH6HH", member_bytes)
    assert member[0] == 7
    assert member[1] == 65535
    assert member[2:8] == (101, 202, 303, 404, 505, 606)
    assert member[8] == 0

    scorer_kind, *tail = struct.unpack_from("<B6BBHHI", fact_bytes, 112)
    assert scorer_kind == 1
    assert tail[:6] == [0, 1, 8, 16, 24, 24]
    assert tail[6:] == [1, 0, 0, 700000]


def test_u7_member_payload_carries_mask_and_topology() -> None:
    _fact_bytes, member_bytes, *_ = _encode("u7.mask.v1")
    record_id, membership = struct.unpack_from("<IH", member_bytes)
    mask8, endpoints, junctions, payload_reserved, member_reserved = (
        struct.unpack_from("<QBBHH", member_bytes, 6)
    )
    assert (record_id, membership) == (7, 65535)
    assert mask8 == int.from_bytes(bytes.fromhex("0102030405060708"), "little")
    assert (endpoints, junctions, payload_reserved, member_reserved) == (9, 10, 0, 0)


def test_primitive_member_and_fact_payloads_carry_all_s_pf_inputs() -> None:
    fact_bytes, member_bytes, *_ = _encode("S_pf.v1")
    mask8, region_class, detail_u16, member_reserved = struct.unpack_from(
        "<QHHH", member_bytes, 6
    )
    assert mask8 == int.from_bytes(bytes.fromhex("1112131415161718"), "little")
    assert (region_class, detail_u16, member_reserved) == (7, 56173, 0)

    tail = struct.unpack_from("<B6BBHHI", fact_bytes, 112)
    assert tail[0] == 3
    assert tail[8:] == (7, 140, 8192)


@pytest.mark.parametrize(
    ("scorer_id", "expected_kind"),
    [
        ("fl4512.shape6_f8_w4.v2", 1),
        ("u7.mask.v1", 2),
        ("S_pf.v1", 3),
    ],
)
def test_exact_scorer_vocabulary_maps_to_nonzero_kinds(
    scorer_id: str, expected_kind: int
) -> None:
    fact_bytes, *_ = _encode(scorer_id)
    assert fact_bytes[112] == expected_kind


@pytest.mark.parametrize("scorer_id", [None, "", "shape6_weighted_l2", "unknown.v1"])
def test_missing_or_unknown_scorer_fails_closed(scorer_id: object) -> None:
    facts, glyph_records, profiles, pool = _inputs()
    facts[0]["scene_membership_key"]["scorer_id"] = scorer_id
    with pytest.raises(compiler.CompileError, match="missing or unknown scorer_id"):
        compiler.encode_fact_table(facts, glyph_records, profiles, pool)


def test_missing_source_profile_fails_closed() -> None:
    facts, glyph_records, profiles, pool = _inputs()
    facts[0]["source_profile_id"] = "terrain:missing"
    with pytest.raises(compiler.CompileError, match="does not name an artifact profile"):
        compiler.encode_fact_table(facts, glyph_records, profiles, pool)


@pytest.mark.parametrize(
    "bad_weights",
    [
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1, True],
        [1, 1, 1, 1, 1, float("nan")],
        [1, 1, 1, 1, 1, float("inf")],
    ],
)
def test_invalid_profile_weights_fail_closed(bad_weights: list[object]) -> None:
    facts, glyph_records, profiles, pool = _inputs()
    profiles[0]["role_weights"] = bad_weights
    with pytest.raises(compiler.CompileError, match="role_weight|role weight"):
        compiler.encode_fact_table(facts, glyph_records, profiles, pool)


def test_missing_member_shape6_fails_closed() -> None:
    facts, glyph_records, profiles, pool = _inputs()
    del glyph_records[0]["morphology"]["shape6_norm_q0_16"]
    with pytest.raises(compiler.CompileError, match="missing morphology.shape6_norm"):
        compiler.encode_fact_table(facts, glyph_records, profiles, pool)


def test_missing_u7_operands_fail_closed() -> None:
    facts, glyph_records, profiles, pool = _inputs("u7.mask.v1")
    del glyph_records[0]["connectivity"]["mask8_u64_le_hex"]
    with pytest.raises(compiler.CompileError, match="u7 member id 7 mask8"):
        compiler.encode_fact_table(facts, glyph_records, profiles, pool)


def test_u7_glyph_and_pool_operand_disagreement_fails_closed() -> None:
    facts, glyph_records, profiles, pool = _inputs("u7.mask.v1")
    pool["rows"][0]["candidate_descriptors"][0]["endpoint_count"] = 8
    with pytest.raises(compiler.CompileError, match="disagrees with the compiled-pool"):
        compiler.encode_fact_table(facts, glyph_records, profiles, pool)


def test_missing_primitive_candidate_operands_fail_closed() -> None:
    facts, glyph_records, profiles, pool = _inputs("S_pf.v1")
    pool["rows"][0]["candidate_descriptors"] = []
    with pytest.raises(compiler.CompileError, match="no compiled-pool candidate"):
        compiler.encode_fact_table(facts, glyph_records, profiles, pool)


def test_missing_primitive_fact_policy_fails_closed() -> None:
    facts, glyph_records, profiles, pool = _inputs("S_pf.v1")
    del pool["materials"][0]["projected_feature_extent_admission"]
    with pytest.raises(compiler.CompileError, match="primitive_extent_enter_q"):
        compiler.encode_fact_table(facts, glyph_records, profiles, pool)


def test_committed_sidecar_and_receipt_declare_format_v6() -> None:
    blob = SIDECAR.read_bytes()
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))

    assert blob[:8] == b"FL4683GP"
    assert struct.unpack_from("<I", blob, 8)[0] == 6
    assert receipt["version"] == 6
    assert receipt["sidecar"]["format_version"] == 6
    assert receipt["sidecar"]["fact_record_size"] == 128
    assert receipt["sidecar"]["fact_member_size"] == 20
    assert receipt["harri_selector_contract"]["reserved_fields_zero"] is True


def test_all_committed_v6_harri_policy_fields_and_scorers_are_valid() -> None:
    blob = SIDECAR.read_bytes()
    fact_count = struct.unpack_from("<I", blob, 148)[0]
    fact_offset = struct.unpack_from("<I", blob, 164)[0]
    member_offset = struct.unpack_from("<I", blob, 168)[0]
    member_count = struct.unpack_from("<I", blob, 172)[0]

    scorer_kinds = []
    for index in range(fact_count):
        fact_base = fact_offset + index * 128
        tail = struct.unpack_from("<B6BBHHI", blob, fact_base + 112)
        scorer_kinds.append(tail[0])
        assert all(0 <= value <= 24 for value in tail[1:7])
        if tail[0] == 3:
            assert tail[7] == 0
            assert tail[8:] == (7, 140, 8192)
        elif tail[0] == 4:
            # FL-4763 structural-decline rows are populated addresses with
            # no scorer operand, no primitive fields, and no threshold.
            assert tail[7:] == (0, 0, 0, 0)
        else:
            # Format v6 scopes the trailing u32 by scorer kind. The two H
            # fields remain the primitive-only payload and stay zero for
            # shape6/U7; U7 is always unbounded while shape6 carries its
            # role-scoped bound, including zero for an unmeasured profile.
            assert tail[8:10] == (0, 0)
            assert tail[10] >= 0
            if tail[0] == 2:
                assert tail[7] == 0
                assert tail[10] == 0
            else:
                assert tail[0] == 1
                assert tail[7] in (0, 1)

        first_member = struct.unpack_from("<I", blob, fact_base + 96)[0]
        members_in_fact = struct.unpack_from("<H", blob, fact_base + 100)[0]
        for member_index in range(first_member, first_member + members_in_fact):
            member_base = member_offset + member_index * 20
            assert struct.unpack_from("<H", blob, member_base + 18)[0] == 0
            if tail[0] == 2:
                mask8, endpoints, junctions, payload_reserved = struct.unpack_from(
                    "<QBBH", blob, member_base + 6
                )
                assert mask8 != 0
                assert endpoints <= 64 and junctions <= 64
                assert payload_reserved == 0
            elif tail[0] == 3:
                mask8, region_class, detail_u16 = struct.unpack_from(
                    "<QHH", blob, member_base + 6
                )
                assert mask8 != 0
                assert (region_class, detail_u16) in {(7, 56173), (7, 65535)}
    assert set(scorer_kinds) == {1, 2, 3, 4}
    assert member_count > 0
