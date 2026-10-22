#!/usr/bin/env python3
# =============================================================================
# test_fl4512_lane_reachability_audit.py
# =============================================================================
# WHAT THIS FILE CHECKS
# ---------------------
# FL-4512's compiled glyph pool has 264 "lanes". A lane is one place the renderer
# can look up which text character to draw, addressed by three things together:
#
#     which material's authored look owns the cell ("terrain:1", "mesh:200", ...)
#     which KIND of question is being asked   (tonal / edge / direction / flow /
#                                              filled / mesh_feature)
#     which specific answer inside that kind  ("ridge", "north_east", ...)
#
# Before anyone can decide whether a lane is authored correctly, a prior question
# has to be answered: can the running engine EVER deliver that lane's fact to
# that material? A lane the engine can never reach needs no author and represents
# no defect. A lane it CAN reach, sitting empty and unexplained, is a real gap.
#
# scripts/fl4512_route_reachability.py answers that question, and this file is
# the audit that keeps its answers honest. These tests are OBSERVATIONAL — they
# read the engine and the authored data and check that the reachability table
# agrees with them. Nothing here changes what the renderer does.
#
# THE THREE REASONING MISTAKES THESE TESTS BLOCK
# ----------------------------------------------
# 1. "Inference from omission" — deciding a lane is unreachable because nobody
#    wrote it down in the authoring file. Silence in a JSON file is not evidence
#    about what the C++ engine can do.
#
# 2. "Inference from missing UI" — deciding a lane is unreachable because the
#    ASCIIID editor has no control for it. Whether a human can currently click
#    something is an authoring-surface question (FL-4260) and says nothing about
#    runtime routing.
#
# 3. "Inference from a bug" — deciding a lane IS reachable because runtime was
#    once observed doing it. Behaviour that contradicts the route contract is a
#    defect report, not a capability. Writing it into the table would make the
#    defect permanent, because lanes marked reachable get authored and authored
#    content makes the bug load-bearing.
#
# All three were used in earlier attempts and all three were rejected. Every
# verdict must instead be anchored to an actual fact-producing function.
#
# Run with:  python3 -m unittest scripts.tests.test_fl4512_lane_reachability_audit
# =============================================================================
# WHAT THIS FILE CHECKS
# ---------------------
# FL-4512's compiled glyph pool has 264 "lanes". A lane is one place the renderer
# can look up which text character to draw, addressed by three things together:
#
#     which material's authored look owns the cell ("terrain:1", "mesh:200", ...)
#     which KIND of question is being asked   (tonal / edge / direction / flow /
#                                              filled / mesh_feature)
#     which specific answer inside that kind  ("ridge", "north_east", ...)
#
# Under the old ABI v1, every lane without glyph candidates was written into the
# artifact as one undifferentiated state called "empty_absence". That single word
# was covering four different situations, one of which is an outright defect —
# a lane the engine really can reach, that nobody authored, and that nobody
# noticed was missing.
#
# ABI v2 splits that state apart. These tests pin the new contract so it cannot
# quietly collapse back.
#
# THE TWO REASONING MISTAKES THESE TESTS EXIST TO BLOCK
# ----------------------------------------------------
# 1. "Inference from omission" — deciding a lane is unreachable because nobody
#    wrote it down in the authoring file. Silence in a JSON file is not evidence
#    about what the C++ engine can do.
#
# 2. "Inference from missing UI" — deciding a lane is unreachable because the
#    ASCIIID editor has no control for it. Whether a human can currently click
#    something is an authoring-surface question (FL-4260) and says nothing about
#    runtime routing.
#
# Both were used in an earlier attempt and both were rejected in review. The
# tests below therefore check that every reachability verdict is anchored to an
# actual fact-producing function in the engine.
#
# Run with:  python3 -m unittest scripts.tests.test_fl4512_abi_v2_lane_contract
# =============================================================================

import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, relative_path: str):
    """Import a script by file path.

    The compiler and its helpers live in scripts/ rather than an installed
    package, so they cannot be imported by name. This does the same
    load-from-path dance the compiler itself uses for its sibling modules.
    """
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    assert spec and spec.loader, f"cannot load {relative_path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REACH = _load("fl4512_route_reachability", "scripts/fl4512_route_reachability.py")

# The C++ files that own the fact producers. Every reachability verdict cites a
# line in one of these, and the tests below read them to confirm the cited
# behaviour is really there rather than trusting the comment.
RESOLVE = (ROOT / "engine/render/render_resolve.cpp").read_text(encoding="utf-8")
RESOLVER = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text(encoding="utf-8")

class ReachabilityIsAnchoredToRealProducers(unittest.TestCase):
    """The reachability table must agree with the engine, not with the JSON."""

    def test_mesh_cells_can_never_carry_terrain_facts(self) -> None:
        # The mesh resolve path assigns "unknown" to vertical relation, direction
        # and flow unconditionally — they are constants, not measurements. So a
        # mesh-owned cell can never route into an edge, direction or flow lane.
        self.assertIn("auto_vertical_relation = (int8_t)FL4260_VERTICAL_RELATION_UNKNOWN", RESOLVE)
        self.assertIn("auto_direction = (int8_t)FL4260_DIRECTION_UNKNOWN", RESOLVE)
        self.assertIn("auto_flow = (int8_t)FL4260_FLOW_UNKNOWN", RESOLVE)
        for role in ("edge", "direction", "flow"):
            with self.subTest(role=role):
                verdict = REACH.lane_disposition("mesh", 200, role, "north")
                self.assertFalse(verdict.addressable)
                self.assertIn("render_resolve.cpp", verdict.citation)

    def test_terrain_cells_can_never_carry_mesh_facts(self) -> None:
        # The deleted raster-occupancy producer leaves filled operands invalid on
        # both paths. The terrain path also hard-wires the mesh feature index to
        # "unknown", while the mesh path retains its real feature producer.
        self.assertNotIn("Fl4512BuildOwnerClippedPrimitiveRefV1", RESOLVE)
        self.assertIn(
            "Generic same-material raster occupancy is not a structural primitive",
            RESOLVE,
        )
        self.assertIn("(int8_t)FL4512_MESH_FEATURE_UNKNOWN", RESOLVE)
        self.assertFalse(REACH.lane_disposition("terrain", 1, "filled", None).addressable)
        self.assertFalse(REACH.lane_disposition("mesh", 200, "filled", None).addressable)
        self.assertFalse(
            REACH.lane_disposition("terrain", 1, "mesh_feature", None).addressable
        )
        self.assertTrue(
            REACH.lane_disposition("mesh", 200, "mesh_feature", None).addressable
        )

    def test_overhang_is_reachable_but_has_no_producer(self) -> None:
        # `overhang` is the one honest "missing producer" case in the whole pool.
        # The sole vertical-relation producer can return FLAT, RISE, FALL, RIDGE,
        # VALLEY and WALL, and never OVERHANG. Confirm that by reading the
        # function body: the six producible names appear as return values inside
        # it, and the seventh does not.
        # Slice out the producer's own body. Split on the DEFINITION, not on the
        # bare name — the name also appears at the call site further down the
        # file, and splitting on that would hand back the wrong region.
        body = RESOLVE.split(
            "static inline int8_t ComputeFl4260VerticalRelationFromSamples", 1
        )[1]
        body = body.split("static inline int8_t ComputeFl4260DirectionFromSamples", 1)[0]
        for producible in ("FLAT", "RISE", "FALL", "RIDGE", "VALLEY", "WALL"):
            with self.subTest(fact=producible):
                self.assertIn(f"FL4260_VERTICAL_RELATION_{producible}", body)
        self.assertNotIn("FL4260_VERTICAL_RELATION_OVERHANG", body)

        verdict = REACH.lane_disposition("terrain", 1, "edge", "overhang")
        self.assertTrue(verdict.addressable, "the lane is on a live terrain route")
        self.assertFalse(verdict.producer_present, "but nothing ever computes it")

    def test_edge_and_direction_apply_no_material_test(self) -> None:
        # Both producers work purely from neighbouring terrain heights. Neither
        # takes a material argument, so neither can be restricted to particular
        # materials — every terrain ProfileKey is addressable for both.
        for role in ("edge", "direction"):
            for material in (1, 2, 3, 4, 5, 6, 8):
                with self.subTest(role=role, material=material):
                    fact = "ridge" if role == "edge" else "north"
                    verdict = REACH.lane_disposition("terrain", material, role, fact)
                    self.assertTrue(verdict.addressable)
                    self.assertTrue(verdict.producer_present)

    def test_flow_belongs_to_the_water_material_alone(self) -> None:
        # The flow producer refuses any cell whose material is not water, so
        # terrain:0 is the only ProfileKey that can hold a flow lane.
        self.assertIn("kWaterMaterial = 0", RESOLVE)
        self.assertIn("Fl4260TerminalCellMaterialVote(src, sample_stride) != kWaterMaterial", RESOLVE)
        self.assertTrue(REACH.lane_disposition("terrain", 0, "flow", "north").addressable)
        for material in (1, 2, 3, 4, 5, 6, 8):
            with self.subTest(material=material):
                self.assertFalse(
                    REACH.lane_disposition("terrain", material, "flow", "north").addressable
                )

    def test_a_measured_ownership_defect_is_not_a_capability(self) -> None:
        # The correction this test guards. In the 2026-06-26 rq153d flow capture,
        # six cells carry a flow fact while reporting material_id 1 — because two
        # owners disagreed about which material the cell belonged to. An earlier
        # revision read those six cells as proof that flow was reachable for
        # terrain:1, which would have frozen the ownership bug into the ABI: the
        # lanes would have been authored, the authored content would have made the
        # divergence load-bearing, and the bug would have become undeletable.
        #
        # The reachability table must describe what the engine is SPECIFIED to do.
        # Runtime that contradicts the contract is a defect report, not a verdict.
        self.assertFalse(REACH.lane_disposition("terrain", 1, "flow", "north").addressable)
        self.assertEqual(REACH.FLOW_OWNER_PROFILE_KEY, ("terrain", 0))
class LaneCensusFixture(unittest.TestCase):
    """The fact-keyed census is the compiler's regression fixture.

    It records, for all 264 lanes, which fact the lane really is, what the
    authoring file says about it, and whether the engine can reach it. Pinning
    it means a future edit that quietly re-collapses the taxonomy — or
    re-introduces the positional join — fails a test instead of shipping.
    """

    CENSUS = json.loads(
        (ROOT / "scripts/tests/fixtures/fl4512_lane_census_v1.json").read_text(encoding="utf-8")
    )

    def test_census_covers_every_compiled_lane(self) -> None:
        self.assertEqual(self.CENSUS["lane_count"], 264)
        self.assertEqual(len(self.CENSUS["lanes"]), 264)
        # The unique lane address needs route_key. The tonal role carries TWO
        # route keys — ramp.v1 and density.v1 — and both number their lanes 0..3,
        # so (profile_key, role, lane) collides on 32 of the 264 rows. Anything
        # that indexes lanes without route_key silently merges those pairs.
        addresses = {(lane["profile_key"], lane["role"], lane["route_key"], lane["lane"])
                     for lane in self.CENSUS["lanes"]}
        self.assertEqual(len(addresses), 264, "every lane address must be unique")
        short = {(lane["profile_key"], lane["role"], lane["lane"])
                 for lane in self.CENSUS["lanes"]}
        self.assertEqual(len(short), 232, "route_key is load-bearing in the lane address")

    def test_census_reachability_matches_the_live_table(self) -> None:
        # The census is a snapshot; the reachability table is the authority. If
        # the two ever disagree, the snapshot is stale and must be regenerated
        # rather than trusted.
        for lane in self.CENSUS["lanes"]:
            namespace, material = lane["profile_key"].split(":")
            with self.subTest(lane=lane["profile_key"] + "/" + lane["lane_name"]):
                verdict = REACH.lane_disposition(
                    namespace, int(material), lane["role"], lane["fact"]
                )
                self.assertEqual(verdict.addressable, lane["addressable"])
                self.assertEqual(verdict.producer_present, lane["producer_present"])

    def test_census_records_the_fact_name_for_every_indexed_lane(self) -> None:
        # A compiled row stores only a lane index. Without the fact name the
        # census would be no better than the positional join it replaces.
        for lane in self.CENSUS["lanes"]:
            if lane["role"] in ("edge", "direction", "flow"):
                with self.subTest(lane=lane["lane_name"]):
                    self.assertIsNotNone(lane["fact"])
            else:
                self.assertIsNone(lane["fact"])

    def test_census_shows_terrain_5_edge_facts_at_their_real_indices(self) -> None:
        # The concrete case that broke the earlier positional join: this profile
        # authors ridge/valley/wall/overhang, which live at indices 3-6.
        edges = {lane["lane"]: lane["fact"] for lane in self.CENSUS["lanes"]
                 if lane["profile_key"] == "terrain:5" and lane["role"] == "edge"}
        self.assertEqual(edges[3], "ridge")
        self.assertEqual(edges[4], "valley")
        self.assertEqual(edges[5], "wall")
        self.assertEqual(edges[6], "overhang")


if __name__ == "__main__":
    unittest.main()
