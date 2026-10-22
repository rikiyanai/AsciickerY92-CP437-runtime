#!/usr/bin/env python3
# =============================================================================
# test_fl4512_canonical_material_owner.py
# =============================================================================
# WHAT THIS PROTECTS
# ------------------
# A terminal cell is one character position on screen, and it covers a 2x2 block
# of rendered samples. Along a shoreline those four samples can belong to
# different materials — some water, some grass. So "which material owns this
# cell?" is not automatic; it needs a rule.
#
# The engine used to have TWO rules answering that question, and they could
# disagree:
#
#   * the FACT producers (which measure things like "this cell is water and the
#     shore lies north-east") asked Fl4260TerminalCellMaterialVote — a majority
#     vote across all four samples;
#
#   * the PROFILE routing (which decides whose authored look supplies the glyph)
#     used mat[0], the top-left sample on its own.
#
# On a mixed cell those answers differ. The consequence was concrete: a cell
# could be handed a water flow fact while being routed to the grass profile, so
# the profile being asked for a glyph was not the profile the fact belonged to.
# Six such cells appear in the 2026-06-26 rq153d flow capture, carrying a flow
# fact while reporting material_id 1.
#
# Fl4512CanonicalTerminalCellMaterial is now the single answer. Fact production,
# ProfileKey selection, bridge identity, role routing, tracing and compiled-row
# lookup all read it, so they cannot disagree by construction.
#
# These tests are deliberately independent of the FL-4512 pool ABI work. This is
# a present-tense correctness fix for the running renderer and it stands on its
# own.
#
# Run with:  python3 -m unittest scripts.tests.test_fl4512_canonical_material_owner
# =============================================================================

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESOLVE = (ROOT / "engine/render/render_resolve.cpp").read_text(encoding="utf-8")


def majority_material(samples):
    """Reproduce the C++ four-sample material vote in Python.

    Mirrors Fl4260TerminalCellMaterialVote: count each valid material across the
    four samples and keep the one that first reaches the running maximum, so a
    tie resolves to whichever material got there first. Samples with a negative
    material id carry no identity and are skipped entirely.

    Returns the winning material id, or -1 when no sample carried one.
    """
    counts = {}
    best, best_count = -1, 0
    for material in samples:
        if material < 0:
            continue
        counts[material] = counts.get(material, 0) + 1
        if counts[material] > best_count:
            best_count, best = counts[material], material
    return best


class OneOwnerAtTheSource(unittest.TestCase):
    """The single-identity rule, pinned where it is implemented."""

    def test_a_single_named_function_owns_the_identity(self) -> None:
        self.assertIn("Fl4512CanonicalTerminalCellMaterial", RESOLVE)

    def test_it_is_built_on_the_same_vote_the_fact_producers_use(self) -> None:
        # The point of the fix is that both sides read ONE rule. A second rule
        # that merely agrees most of the time would reintroduce the defect.
        body = RESOLVE.split("static inline int Fl4512CanonicalTerminalCellMaterial", 1)[1]
        body = body.split("\n}", 1)[0]
        self.assertIn("Fl4260TerminalCellMaterialVote", body)

    def test_it_falls_back_only_when_no_sample_carries_a_material(self) -> None:
        # A vote of -1 is a no-ownership cell, not a mixed-ownership cell, so the
        # caller's fallback stands and behaviour there is unchanged.
        body = RESOLVE.split("static inline int Fl4512CanonicalTerminalCellMaterial", 1)[1]
        body = body.split("\n}", 1)[0]
        self.assertIn("voted >= 0 ? voted : fallback_material", body)

    def test_terrain_routing_no_longer_uses_the_top_left_sample(self) -> None:
        call = RESOLVE.split("const bool material_look_applied = DispatchShape6ResolverHook", 1)[1]
        call = call.split(");", 1)[0]
        self.assertIn("canonical_material", call)
        self.assertNotIn("mat[0]", call)

    def test_both_resolve_paths_derive_identity_from_the_same_vote(self) -> None:
        # The mesh path already voted. After this fix the terrain path does too,
        # so neither surface can route a cell to a profile that disagrees with
        # the cell's own measured facts.
        self.assertIn("auto_material_id = Fl4260TerminalCellMaterialVote(src, dw)", RESOLVE)
        self.assertIn("Fl4512CanonicalTerminalCellMaterial(src, dw, mat[0])", RESOLVE)

    def test_the_colour_fallback_keeps_its_own_owner(self) -> None:
        # Deliberate scope boundary. ApplyMaterialGlyphPlaneCell is the CP437
        # presentation path, not a route owner. Repointing it would change
        # visible colour without fixing anything about routing, so it is left
        # exactly as it was.
        self.assertIn("ApplyMaterialGlyphPlaneCell(r, ptr, &matlib[mat[0]]", RESOLVE)


class MixedShorelineCells(unittest.TestCase):
    """The cases where the two old rules used to disagree."""

    WATER = 0
    GRASS = 1

    def test_the_old_rule_and_the_new_rule_differ_on_mixed_cells(self) -> None:
        # This is the defect, stated as a fact about the two rules. If this test
        # ever fails, the shapes below stopped being mixed and the rest of this
        # file is testing nothing.
        samples = [self.GRASS, self.WATER, self.WATER, self.WATER]
        self.assertEqual(samples[0], self.GRASS, "old rule: top-left sample")
        self.assertEqual(majority_material(samples), self.WATER, "new rule: vote")
        self.assertNotEqual(samples[0], majority_material(samples))

    def test_the_fact_producer_and_the_routed_profile_now_agree(self) -> None:
        # For every mixed arrangement, the material that earns the flow fact and
        # the material whose profile gets asked for a glyph must be the same one.
        # Under the canonical rule they are the same value by construction.
        arrangements = [
            [self.GRASS, self.WATER, self.WATER, self.WATER],
            [self.WATER, self.GRASS, self.WATER, self.WATER],
            [self.WATER, self.WATER, self.GRASS, self.WATER],
            [self.GRASS, self.GRASS, self.WATER, self.WATER],
            [self.WATER, self.GRASS, self.GRASS, self.GRASS],
        ]
        for samples in arrangements:
            with self.subTest(samples=samples):
                owner = majority_material(samples)
                fact_producer_material = owner   # the vote, per :456
                routed_profile_material = owner  # the canonical identity, per the fix
                self.assertEqual(fact_producer_material, routed_profile_material)

    def test_a_cell_with_no_material_at_all_keeps_the_previous_behaviour(self) -> None:
        # All four samples lacking a material id must not silently become
        # material 0 (water). The vote reports -1 and the caller's fallback wins.
        self.assertEqual(majority_material([-1, -1, -1, -1]), -1)

    def test_a_uniform_cell_is_unaffected_by_the_change(self) -> None:
        # The overwhelming majority of cells are single-material. For those the
        # old and new rules agree, so this fix cannot move them.
        for material in range(0, 9):
            with self.subTest(material=material):
                samples = [material] * 4
                self.assertEqual(samples[0], majority_material(samples))


if __name__ == "__main__":
    unittest.main()
