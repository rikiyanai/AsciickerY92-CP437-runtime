#!/usr/bin/env python3
# =============================================================================
# scripts/tests/test_fl4512_phase2_compiler_policy.py
# =============================================================================
# FL-4512 Phase 2, compiler side of the role-correct abstention contract.
#
# WHAT FORMAT VERSION 6 ADDED, in plain terms:
#
#   * Every fact record's former padding byte is now `policy_flags`. Bit 0x1
#     (DEGENERATE_OPERAND_ABSTAIN) says: when the live six-sample Harri operand
#     is completely flat (all six dims equal), this row must DECLINE its claim
#     instead of confidently picking the nearest glyph.
#   * The record's trailing u32 is now scoped by scorer kind: the primitive
#     scorer keeps its extent-enter gate, and the two distance scorers carry a
#     compiled no-match bound (0 = unbounded).
#
# THE ROLE RULES THIS FILE PROVES THE COMPILER ENFORCES:
#
#   * Only STRUCTURAL shape6 rows (edge, direction, flow) may carry the flag,
#     and only when the owning material authored
#     no_match_policy.structural_degenerate_declines.
#   * TONAL shape6 rows (ramp, density) are NEVER flagged -- a constant tonal
#     operand legally describes a flat surface -- but they DO carry the tonal
#     no-match bound.
#   * U7 mask rows carry NO flag and a bound of exactly 0, because the authored
#     bounds are in shape6 weighted-L2 units and the U7 mask scorer's units
#     differ; comparing unlike quantities silently would be a defect.
#   * A pool without no_match_policy (revision 2 or older) fails the compile
#     CLOSED with a stable, greppable reason. No guessed bounds, ever.
#
# These tests run no game and read no 93 MiB artifact: they drive the compiler
# module's own encoding functions over small synthetic inputs and unpack the
# exact bytes those functions produce.
#
# Run with:
#     python3 -m pytest scripts/tests/test_fl4512_phase2_compiler_policy.py -q
# =============================================================================

from __future__ import annotations

import hashlib
import importlib.util
import json
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPILER_PATH = ROOT / "scripts/compile_fl4683_glyph_program.py"
POOL_COMPILER_PATH = ROOT / "scripts/compile_fl4512_pools.py"
LOADER_H = ROOT / "platform/fl4683_glyph_program_loader.h"
POOL = ROOT / "assets/glyphs/generated/fl4512.compiled_pool.v1.json"


def _load_compiler():
    """Import the compiler script as a module without executing its CLI."""
    spec = importlib.util.spec_from_file_location(
        "fl4683_compiler_under_test", COMPILER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


C = _load_compiler()


def _load_pool_compiler():
    spec = importlib.util.spec_from_file_location(
        "fl4512_pool_compiler_under_test", POOL_COMPILER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


POOL_COMPILER = _load_pool_compiler()

# The fact record layout the loader header declares. Byte 119 is the policy
# byte (after 112 fixed bytes, scorer_kind at 112, six role weights at
# 113..118) and bytes 124..128 are the scorer-scoped u32. These offsets are
# asserted against raw packed bytes below, so a silent re-layout on either
# side fails here rather than at runtime.
FACT_RECORD_SIZE = 128
FACT_POLICY_BYTE_OFFSET = 119
FACT_THRESHOLD_U32_OFFSET = 124
FACT_STRUCT = "<32s32s32sIHHIHHB6BBHHI"


def make_policy(**overrides):
    """One valid authored no_match_policy, the revision-3 pool shape."""
    policy = {
        "structural_degenerate_declines": True,
        "structural_decline_fallback": "tonal",
        "tonal_constant_operand_legal": True,
        # Distinct numbers on purpose: if the compiler ever swapped the two
        # scopes, every equality below would still catch it.
        "max_error_q": {"structural": 700000, "tonal": 650000},
    }
    policy.update(overrides)
    return policy


def make_material(policy=make_policy()):
    material = {
        "profile_namespace": "terrain",
        "material_key": 4,
        # S_pf fields, so the same synthetic material can own a primitive fact.
        "filled_region_class": 3,
        "filled_priority_u16": 900,
        "projected_feature_extent_admission": {
            "filled_threshold": {"enter_q": 8192}
        },
    }
    if policy is not None:
        material["no_match_policy"] = policy
    return material


def key(route_key, namespace="terrain", material_key=4):
    return {
        "profile_namespace": namespace,
        "material_key": material_key,
        "route_key": route_key,
    }


def materials_index(material):
    return {(material["profile_namespace"], material["material_key"]): material}


class TestPolicyDerivation(unittest.TestCase):
    """fact_shape6_no_match_policy: the exact flag/bound scoping rules."""

    def setUp(self):
        self.materials = materials_index(make_material())

    def flags_bound(self, route_key, scorer_kind=None, materials=None):
        return C.fact_shape6_no_match_policy(
            C.SCORER_KIND_SHAPE6_WEIGHTED_L2 if scorer_kind is None else scorer_kind,
            key(route_key),
            self.materials if materials is None else materials,
            0,
        )

    def test_structural_roles_are_flagged_and_carry_the_structural_bound(self):
        for route_key in ("edge.v1", "direction.v1", "flow.v1"):
            self.assertEqual(
                self.flags_bound(route_key),
                (C.FACT_POLICY_DEGENERATE_OPERAND_ABSTAIN, 700000),
                route_key,
            )

    def test_tonal_roles_are_never_flagged_but_carry_the_tonal_bound(self):
        for route_key in ("ramp.v1", "density.v1"):
            self.assertEqual(self.flags_bound(route_key), (0, 650000), route_key)

    def test_declines_false_removes_the_flag_but_keeps_the_bound(self):
        materials = materials_index(
            make_material(make_policy(structural_degenerate_declines=False))
        )
        self.assertEqual(
            self.flags_bound("edge.v1", materials=materials), (0, 700000)
        )

    def test_zero_authored_bounds_are_unbounded(self):
        materials = materials_index(
            make_material(make_policy(max_error_q={"structural": 0, "tonal": 0}))
        )
        self.assertEqual(
            self.flags_bound("edge.v1", materials=materials),
            (C.FACT_POLICY_DEGENERATE_OPERAND_ABSTAIN, 0),
        )
        self.assertEqual(self.flags_bound("ramp.v1", materials=materials), (0, 0))

    def test_u7_and_primitive_scorers_get_no_flag_and_a_zero_bound(self):
        # The unit constraint: authored bounds are shape6 weighted-L2 units, so
        # the U7 mask row must stay unbounded rather than compare unlike units.
        # The primitive row keeps its own extent semantics in the same u32,
        # emitted by fact_primitive_policy, so this function contributes zeros.
        for scorer_kind in (C.SCORER_KIND_U7_MASK, C.SCORER_KIND_S_PF):
            self.assertEqual(
                self.flags_bound("mesh_feature.bundle.v1", scorer_kind=scorer_kind),
                (0, 0),
            )

    def test_a_shape6_route_outside_both_roles_fails_closed(self):
        with self.assertRaises(C.CompileError):
            self.flags_bound("filled.field.v1")

    def test_a_revision_2_pool_fails_closed_with_the_stable_reason(self):
        materials = materials_index(make_material(policy=None))
        with self.assertRaises(C.CompileError) as caught:
            self.flags_bound("edge.v1", materials=materials)
        self.assertIn("no_match_policy_missing", str(caught.exception))

    def test_an_unknown_policy_field_fails_closed_by_name(self):
        materials = materials_index(
            make_material(make_policy(surprise_field=1))
        )
        with self.assertRaises(C.CompileError) as caught:
            self.flags_bound("edge.v1", materials=materials)
        self.assertIn("no_match_policy_unknown_field", str(caught.exception))
        self.assertIn("surprise_field", str(caught.exception))

    def test_policy_provenance_field_is_not_a_compiler_input(self):
        materials = materials_index(
            make_material(
                make_policy(
                    max_error_q_evidence={
                        "status": "unmeasured",
                        "scope": "test",
                    }
                )
            )
        )
        with self.assertRaises(C.CompileError) as caught:
            self.flags_bound("edge.v1", materials=materials)
        self.assertIn("no_match_policy_unknown_field", str(caught.exception))
        self.assertIn("max_error_q_evidence", str(caught.exception))

    def test_pool_compiler_rejects_policy_provenance_and_accepts_zero_bounds(self):
        profile = {
            "material_id": "terrain:test",
            "no_match_policy": make_policy(
                max_error_q={"structural": 0, "tonal": 0}
            ),
        }
        self.assertEqual(
            POOL_COMPILER.compile_no_match_policy(profile)["max_error_q"],
            {"structural": 0, "tonal": 0},
        )
        profile["no_match_policy"]["max_error_q_evidence"] = {
            "status": "unmeasured",
            "scope": "test",
        }
        with self.assertRaises(POOL_COMPILER.CompileFailure):
            POOL_COMPILER.compile_no_match_policy(profile)

    def test_a_non_tonal_fallback_fails_closed(self):
        # The binary format encodes exactly one fallback: pass-1 rearbitration
        # over the tonal pair. Authored intent it cannot carry must not ship.
        materials = materials_index(
            make_material(make_policy(structural_decline_fallback="filled"))
        )
        with self.assertRaises(C.CompileError) as caught:
            self.flags_bound("edge.v1", materials=materials)
        self.assertIn("no_match_policy_fallback_unsupported", str(caught.exception))

    def test_illegal_tonal_constant_operands_fail_closed(self):
        # There is no carrier for flagging tonal rows, so a pool that authored
        # tonal constants ILLEGAL cannot be honored and must refuse.
        materials = materials_index(
            make_material(make_policy(tonal_constant_operand_legal=False))
        )
        with self.assertRaises(C.CompileError) as caught:
            self.flags_bound("ramp.v1", materials=materials)
        self.assertIn("no_match_policy_tonal_flag_uncarriable", str(caught.exception))

    def test_a_nonzero_authored_epsilon_fails_closed(self):
        # The compiled flag encodes an epsilon of exactly zero (raw equality).
        # A nonzero epsilon has no carrier and must never silently degrade.
        materials = materials_index(
            make_material(make_policy(degenerate_epsilon_q0_16=3))
        )
        with self.assertRaises(C.CompileError) as caught:
            self.flags_bound("edge.v1", materials=materials)
        self.assertIn("no_match_policy_epsilon_uncarriable", str(caught.exception))
        # An explicit zero epsilon is the same statement the flag makes.
        materials = materials_index(
            make_material(make_policy(degenerate_epsilon_q0_16=0))
        )
        self.assertEqual(
            self.flags_bound("edge.v1", materials=materials),
            (C.FACT_POLICY_DEGENERATE_OPERAND_ABSTAIN, 700000),
        )

    def test_malformed_bounds_fail_closed(self):
        for bad in (
            {"structural": 700000},                       # missing tonal
            {"structural": -1, "tonal": 1},               # negative
            {"structural": 1 << 32, "tonal": 1},          # too wide for u32
            {"structural": True, "tonal": 1},             # bool is not a count
        ):
            materials = materials_index(make_material(make_policy(max_error_q=bad)))
            with self.assertRaises(C.CompileError):
                self.flags_bound("edge.v1", materials=materials)


class TestFactRecordBytes(unittest.TestCase):
    """encode_fact_table writes the policy byte and scoped u32 where the loader
    reads them, over one synthetic fact of every scorer kind."""

    # -- synthetic inputs ----------------------------------------------------
    SHAPE6 = [100, 200, 300, 400, 500, 600]
    U7_MASK_HEX = "0102030405060708"
    PF_MASK_HEX = "1112131415161718"

    def build_inputs(self):
        def glyph_record(record_id):
            return {
                "glyph_record_id": record_id,
                "morphology": {"shape6_norm_q0_16": list(self.SHAPE6)},
                "connectivity": {
                    "mask8_u64_le_hex": self.U7_MASK_HEX
                    if record_id == 30
                    else self.PF_MASK_HEX
                },
                "structure": {"endpoint_count": 2, "junction_count": 1},
            }

        def fact(name, route_key, lane_name, scorer_id, member_id):
            return {
                "fact_id": f"glyphfact://test/{name}",
                "fact_sha256": hashlib.sha256(name.encode()).hexdigest(),
                "source_profile_id": "terrain:4",
                "subset_weight_sum_q0_16": 65535,
                "scene_membership_key": {
                    "profile_namespace": "terrain",
                    "material_key": 4,
                    "route_key": route_key,
                    "lane_name": lane_name,
                    "scorer_id": scorer_id,
                    "capacity": 4,
                    "row_sha256": hashlib.sha256(
                        f"row:{name}".encode()
                    ).hexdigest(),
                },
                "glyph_members": [
                    {"glyph_record_id": member_id, "membership_weight_q0_16": 65535}
                ],
                "relation_projection": {"edges": []},
            }

        facts = [
            fact("edge", "edge.v1", "edge[5]", "fl4512.shape6_f8_w4.v2", 10),
            fact("ramp", "ramp.v1", "ramp[2]", "fl4512.shape6_f8_w4.v2", 20),
            fact("u7", "mesh_feature.bundle.v1", "mesh_feature_bundle",
                 "u7.mask.v1", 30),
            fact("pf", "filled.field.v1", "filled_field", "S_pf.v1", 40),
        ]
        glyph_records = [glyph_record(i) for i in (10, 20, 30, 40)]
        profiles = [{"profile_id": "terrain:4",
                     "role_weights": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]}]
        pool = {
            "materials": [make_material()],
            "rows": [
                {
                    "row_sha256": fact_row["scene_membership_key"]["row_sha256"],
                    "scorer_id": fact_row["scene_membership_key"]["scorer_id"],
                    "candidate_descriptors": [
                        {
                            "glyph_id": fact_row["glyph_members"][0][
                                "glyph_record_id"
                            ],
                            "mask8_u64_le_hex": self.U7_MASK_HEX
                            if fact_row["scene_membership_key"]["scorer_id"]
                            == "u7.mask.v1"
                            else self.PF_MASK_HEX,
                            "endpoint_count": 2,
                            "junction_count": 1,
                            "filled_region_class": 3,
                            "detail_u16": 7,
                        }
                    ],
                }
                for fact_row in facts
            ],
        }
        return facts, glyph_records, profiles, pool

    def unpack_records(self, fact_bytes):
        self.assertEqual(len(fact_bytes) % FACT_RECORD_SIZE, 0)
        records = []
        for start in range(0, len(fact_bytes), FACT_RECORD_SIZE):
            raw = fact_bytes[start:start + FACT_RECORD_SIZE]
            fields = struct.unpack(FACT_STRUCT, raw)
            records.append({
                "raw": raw,
                "scorer_kind": fields[9],
                "policy_flags": fields[16],
                "threshold_u32": fields[19],
            })
        return records

    def test_each_scorer_kind_gets_its_exact_policy_byte_and_u32(self):
        facts, glyph_records, profiles, pool = self.build_inputs()
        fact_bytes, _members, _edges, _mc, _ec = C.encode_fact_table(
            facts, glyph_records, profiles, pool
        )
        edge, ramp, u7, primitive = self.unpack_records(fact_bytes)

        # Structural shape6: flagged, structural bound.
        self.assertEqual(edge["scorer_kind"], C.SCORER_KIND_SHAPE6_WEIGHTED_L2)
        self.assertEqual(
            edge["policy_flags"], C.FACT_POLICY_DEGENERATE_OPERAND_ABSTAIN
        )
        self.assertEqual(edge["threshold_u32"], 700000)

        # Tonal shape6: never flagged, tonal bound.
        self.assertEqual(ramp["policy_flags"], 0)
        self.assertEqual(ramp["threshold_u32"], 650000)

        # U7: never flagged, bound pinned to 0 by the unit constraint.
        self.assertEqual(u7["scorer_kind"], C.SCORER_KIND_U7_MASK)
        self.assertEqual(u7["policy_flags"], 0)
        self.assertEqual(u7["threshold_u32"], 0)

        # Primitive: never flagged, u32 keeps the v5 extent-enter meaning.
        self.assertEqual(primitive["scorer_kind"], C.SCORER_KIND_S_PF)
        self.assertEqual(primitive["policy_flags"], 0)
        self.assertEqual(primitive["threshold_u32"], 8192)

    def test_the_byte_offsets_match_the_loader_struct(self):
        # The loader reads policy_flags at record byte 119 and the scoped u32
        # at bytes 124..128. Assert the raw bytes, not just the unpack, so the
        # two sides can never disagree about WHERE the policy lives.
        facts, glyph_records, profiles, pool = self.build_inputs()
        fact_bytes, *_ = C.encode_fact_table(facts, glyph_records, profiles, pool)
        edge = fact_bytes[:FACT_RECORD_SIZE]
        self.assertEqual(
            edge[FACT_POLICY_BYTE_OFFSET], C.FACT_POLICY_DEGENERATE_OPERAND_ABSTAIN
        )
        self.assertEqual(
            struct.unpack_from("<I", edge, FACT_THRESHOLD_U32_OFFSET)[0], 700000
        )
        ramp = fact_bytes[FACT_RECORD_SIZE:2 * FACT_RECORD_SIZE]
        self.assertEqual(ramp[FACT_POLICY_BYTE_OFFSET], 0)
        self.assertEqual(
            struct.unpack_from("<I", ramp, FACT_THRESHOLD_U32_OFFSET)[0], 650000
        )

    def test_a_policy_free_pool_fails_the_whole_fact_table_closed(self):
        facts, glyph_records, profiles, pool = self.build_inputs()
        for material in pool["materials"]:
            material.pop("no_match_policy")
        with self.assertRaises(C.CompileError) as caught:
            C.encode_fact_table(facts, glyph_records, profiles, pool)
        self.assertIn("no_match_policy_missing", str(caught.exception))


class TestStructuralPresentationPolicy(unittest.TestCase):
    def test_mesh_structural_only_policy_compiles_to_one_hashed_instruction(self):
        profile = {
            "material_id": "mesh:200",
            "presentation_policy": {
                "schema": "fl4512.structural_presentation.v1",
                "mode": "structural_only",
                "admitted_feature_kind_mask": 11,
                "background_glyph_id": 32,
                "background_palette_index": 0,
            },
        }
        compiled = POOL_COMPILER.compile_presentation_policy(profile)
        self.assertEqual(compiled["mode"], "structural_only")
        self.assertEqual(compiled["background_glyph_id"], 32)
        self.assertEqual(len(compiled["content_sha256"]), 64)

    def test_structural_only_policy_refuses_unmodeled_or_non_mesh_authoring(self):
        policy = {
            "schema": "fl4512.structural_presentation.v1",
            "mode": "structural_only",
            "admitted_feature_kind_mask": 11,
            "background_glyph_id": 32,
            "background_palette_index": 0,
        }
        with self.assertRaises(POOL_COMPILER.CompileFailure):
            POOL_COMPILER.compile_presentation_policy({
                "material_id": "terrain:1", "presentation_policy": policy
            })
        policy["background_glyph_id"] = 46
        with self.assertRaises(POOL_COMPILER.CompileFailure):
            POOL_COMPILER.compile_presentation_policy({
                "material_id": "mesh:200", "presentation_policy": policy
            })


class TestContractPins(unittest.TestCase):
    """Compiler constants must mirror the landed loader contract."""

    def test_format_version_and_flag_values_agree_with_the_loader(self):
        loader_text = LOADER_H.read_text()
        self.assertEqual(C.SIDECAR_FORMAT_VERSION, 6)
        self.assertIn("#define FL4683_GLYPH_PROGRAM_FORMAT_VERSION 6u", loader_text)
        self.assertIn(
            "FL4683_FACT_POLICY_DEGENERATE_OPERAND_ABSTAIN = 0x1u", loader_text
        )
        self.assertEqual(C.FACT_POLICY_DEGENERATE_OPERAND_ABSTAIN, 0x1)

    def test_the_role_partition_covers_every_shape6_route(self):
        # Structural and tonal must partition cleanly; a route in both would
        # make the bound scoping ambiguous, a route in neither fails compile.
        self.assertEqual(
            C.STRUCTURAL_SHAPE6_ROUTE_KEYS & C.TONAL_SHAPE6_ROUTE_KEYS, frozenset()
        )
        self.assertEqual(
            C.STRUCTURAL_SHAPE6_ROUTE_KEYS,
            frozenset({"edge.v1", "direction.v1", "flow.v1"}),
        )
        self.assertEqual(
            C.TONAL_SHAPE6_ROUTE_KEYS, frozenset({"ramp.v1", "density.v1"})
        )


class TestAgainstTheRealPool(unittest.TestCase):
    """When the revision-3 pool has landed, the real join must hold."""

    def test_real_pool_policy_if_present(self):
        pool = json.loads(POOL.read_text())
        materials = pool.get("materials", [])
        carrying = [m for m in materials if isinstance(m.get("no_match_policy"), dict)]
        if not carrying or len(carrying) != len(materials):
            self.skipTest(
                "compiled pool is still revision 2 (no no_match_policy); "
                "the fail-closed path is covered synthetically above"
            )
        # Every landed policy must be encodable by this compiler: validating
        # them all is exactly the compile-time gate, run here without the
        # 93 MiB artifact.
        pool_materials = C.build_pool_material_index(pool)
        for (namespace, material_key), _material in sorted(pool_materials.items()):
            C.material_no_match_policy(
                {"profile_namespace": namespace, "material_key": material_key},
                pool_materials,
                0,
            )


if __name__ == "__main__":
    unittest.main()
