#!/usr/bin/env python3
# =============================================================================
# test_fl4512_reviewed_lane_promotion.py    (FL-4512)
# =============================================================================
# WHAT IS BEING PROMOTED, AND WHY IT NEEDS RULES
# ----------------------------------------------
# Two files matter here.
#
#   material.morphology.v2.profile_tables.json is GENERATED. An offline tool
#   measured where each glyph's ink sits — top-heavy, right-heavy, and so on —
#   and worked out which compass direction each glyph appears to point. A human
#   then reviewed the resulting cells and accepted them.
#
#   material_rendering_profiles.v1.json is AUTHORED. It is the durable owner of
#   what the renderer actually uses.
#
# Generated output is evidence; authored data is authority. Evidence never
# becomes a runtime owner on its own — it has to be PROMOTED across in an
# explicit transaction that records where it came from. These tests pin the
# rules of that transaction.
#
# TWO SHAPE MISMATCHES THE TRANSACTION HAS TO RESOLVE
# ---------------------------------------------------
# 1. The generated table uses SIXTEEN compass labels (N, NNE, NE, ENE, E, ...)
#    plus "NONE" for glyphs with no directional bias. The runtime has exactly
#    EIGHT direction facts, because eight is what its steepest-descent producer
#    can report. Promoting a "north-north-east" lane would invent a fact the
#    engine never emits, so only the eight exact matches cross over.
#
# 2. The generated table splits every direction across FOUR density cells
#    (D0..D3). In the runtime, density is a separate and independent coordinate
#    owned by the tonal ramp. A direction lane must not smuggle density in
#    through the back door, so the four cells are collapsed into one list by a
#    single deterministic rule with no human in the loop.
#
# Run with:  python3 -m unittest scripts.tests.test_fl4512_reviewed_lane_promotion
# =============================================================================

import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location(
    "seed_material_rendering_profiles", ROOT / "scripts/seed_material_rendering_profiles.py")
SEEDER = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SEEDER)


def cell(candidates, primaries=(), accepted=True):
    """Build one reviewed density cell the way the generated table stores them.

    `candidates` is the reviewed candidate list IN REVIEWED ORDER — position is
    rank, so the first entry is the best-placed glyph in that cell. `primaries`
    are the glyphs the review named as that cell's outright winner.
    """
    return {
        "candidate_glyph_ids": list(candidates),
        "primary_glyph_ids": list(primaries),
        "runtime_state": "review_accepted" if accepted else "review_incomplete",
    }


def direction_row(d0, d1, d2, d3):
    """Build one direction's four density cells."""
    return {"D0": d0, "D1": d1, "D2": d2, "D3": d3}


class OnlyTheEightRuntimeDirectionsAreImported(unittest.TestCase):

    def test_exactly_eight_labels_map_across(self) -> None:
        self.assertEqual(len(SEEDER.D8_TABLE_LABEL_BY_FACT), 8)
        self.assertEqual(
            sorted(SEEDER.D8_TABLE_LABEL_BY_FACT.values()),
            sorted(["N", "NE", "E", "SE", "S", "SW", "W", "NW"]))

    def test_the_runtime_fact_names_are_the_ones_the_engine_parses(self) -> None:
        # The mapping keys must be the runtime fact strings, in the runtime's own
        # bin order, or a promoted lane would land on the wrong bearing.
        self.assertEqual(list(SEEDER.D8_TABLE_LABEL_BY_FACT), list(SEEDER.DIRECTION_FACTS))

    def test_the_finer_labels_are_refused_by_name(self) -> None:
        # Listed explicitly in the source so the refusal is visible rather than
        # implied by omission.
        for label in ("NNE", "ENE", "ESE", "SSE", "SSW", "WSW", "WNW", "NNW", "NONE"):
            with self.subTest(label=label):
                self.assertIn(label, SEEDER.NON_RUNTIME_TABLE_LABELS)
                self.assertNotIn(label, SEEDER.D8_TABLE_LABEL_BY_FACT.values())


class TheCollapseRule(unittest.TestCase):
    """Four reviewed density cells become one lane, deterministically."""

    ADMITTED = set(range(500, 700))

    def collapse(self, row):
        return SEEDER.collapse_reviewed_density_cells(row, self.ADMITTED)

    def test_an_accepted_primary_outranks_a_mere_alternate(self) -> None:
        # 601 appears in every cell; 600 appears once but is a named winner.
        # Primary membership is the first key, so 600 leads regardless.
        row = direction_row(
            cell([601, 600], primaries=[600]),
            cell([601]), cell([601]), cell([601]))
        self.assertEqual(self.collapse(row)[0], 600)

    def test_more_density_cells_beats_fewer(self) -> None:
        # Neither is primary, so occurrence count decides: 610 survived in all
        # four cells, 611 in one.
        row = direction_row(
            cell([611, 610]), cell([610]), cell([610]), cell([610]))
        self.assertEqual(self.collapse(row), [610, 611])

    def test_best_reviewed_rank_breaks_an_occurrence_tie(self) -> None:
        # Both appear twice and both reach rank 0 once, so the summed rank
        # decides and the order must still be total.
        row = direction_row(
            cell([620, 621]), cell([621, 620]), cell([]), cell([]))
        result = self.collapse(row)
        self.assertEqual(sorted(result), [620, 621])
        self.assertEqual(result[0], 620)

    def test_summed_rank_breaks_a_best_rank_tie(self) -> None:
        # Both reach rank 0 once. 630 is second the other time; 631 is third.
        row = direction_row(
            cell([630, 999, 631]), cell([631, 630]), cell([]), cell([]))
        result = self.collapse(row)
        self.assertLess(result.index(630), result.index(631))

    def test_glyph_id_is_the_final_tie_break(self) -> None:
        # Identical in every other respect, so the order must still be total.
        row = direction_row(
            cell([641, 640]), cell([640, 641]), cell([]), cell([]))
        self.assertEqual(self.collapse(row), [640, 641])

    def test_capacity_is_a_hard_cap(self) -> None:
        many = list(range(500, 560))  # 60 candidates, all admitted
        row = direction_row(cell(many), cell(many), cell(many), cell(many))
        self.assertEqual(len(self.collapse(row)), SEEDER.PROMOTION_CAPACITY)

    def test_unrendered_glyphs_are_filtered_out(self) -> None:
        # The generated table was measured over a PLANNED inventory that reaches
        # past the rendered atlas. Promoting an id the atlas cannot draw would
        # produce a lane the pool compiler rejects outright.
        row = direction_row(
            cell([650, 9001, 9002]), cell([650]), cell([]), cell([]))
        self.assertEqual(self.collapse(row), [650])

    def test_cells_the_review_did_not_accept_contribute_nothing(self) -> None:
        row = direction_row(
            cell([660], accepted=False), cell([661]), cell([]), cell([]))
        self.assertEqual(self.collapse(row), [661])

    def test_the_result_is_reproducible(self) -> None:
        # Same input, same bytes out. The rule must not depend on set or dict
        # iteration order.
        row = direction_row(
            cell([603, 601, 602], primaries=[602]),
            cell([601, 603]), cell([602, 601]), cell([603]))
        self.assertEqual(self.collapse(row), self.collapse(row))


class PromotionRefusesStaleEvidence(unittest.TestCase):
    """Promotion copies human decisions; it must prove they are current."""

    def test_the_shipped_table_is_currently_refused(self) -> None:
        # The table has been regenerated (2026-08-02) against the current shape
        # catalog, so the hash mismatch that used to block promotion is settled.
        # The refusal has correctly MOVED ON to the next real blocker rather than
        # disappearing: regeneration resets review metadata, so the table now
        # declares itself review_incomplete with runtime_profile_live false.
        #
        # That is the whole point of the review-subject repair (FL-4260 /
        # FL-4512): decisions are carried across the regeneration boundary by
        # scripts/fl4260_migrate_review_receipts.py only where the reviewed
        # subject is byte-for-byte unchanged, and the remainder go back to a
        # human. Until that queue is settled and review metadata is refreshed,
        # promotion must stay refused.
        tables = json.loads(
            (ROOT / "assets/glyphs/generated/material.morphology.v2.profile_tables.json")
            .read_text(encoding="utf-8"))
        with self.assertRaises(SystemExit) as caught:
            SEEDER.validate_morphology_tables(tables, None)
        message = str(caught.exception)
        self.assertIn("PROMOTION REFUSED", message)
        self.assertIn("review_state", message)

    def test_the_catalog_hash_blocker_is_settled(self) -> None:
        # Guards the direction of travel: the table must now agree with the
        # catalog on disk. If this regresses, the artifacts drifted apart again
        # and every downstream reachability count is measured on stale evidence.
        import hashlib
        tables = json.loads(
            (ROOT / "assets/glyphs/generated/material.morphology.v2.profile_tables.json")
            .read_text(encoding="utf-8"))
        catalog_path = ROOT / "assets/glyphs/generated/material.additive.v1.shape_catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        # The generator hashes the catalog as canonicalised JSON, not raw bytes.
        canonical = hashlib.sha256(
            json.dumps(catalog, sort_keys=True).encode("utf-8")).hexdigest()
        self.assertEqual(tables["source_catalog_hash"], canonical)

    def test_the_validator_checks_every_promotion_precondition(self) -> None:
        source = (ROOT / "scripts/seed_material_rendering_profiles.py").read_text(encoding="utf-8")
        body = source.split("def validate_morphology_tables", 1)[1]
        body = body.split("\ndef ", 1)[0]
        for precondition in ("schema_version", "review_state", "runtime_profile_live",
                             "source_catalog_hash", "input_hash",
                             "pending_cells", "rejected_cells", "deferred_cells"):
            with self.subTest(precondition=precondition):
                self.assertIn(precondition, body)

    def test_the_catalog_hash_uses_the_generators_canonicalisation(self) -> None:
        # The morphology generator hashes the catalog as canonicalised JSON
        # (sorted keys). Hashing raw file bytes instead would mismatch on every
        # cosmetic reformat and make this gate cry wolf.
        source = (ROOT / "scripts/seed_material_rendering_profiles.py").read_text(encoding="utf-8")
        body = source.split("def validate_morphology_tables", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("sort_keys=True", body)


class WaterOwnsFlow(unittest.TestCase):

    def test_terrain_zero_is_water_by_morphology_authority(self) -> None:
        self.assertEqual(SEEDER.MORPHOLOGY_NAME_BY_MATERIAL[0], "WATER")

    def test_flow_promotion_never_rebuilds_the_retired_vocabulary(self) -> None:
        # The old wave/wind/grass_stroke labels addressed no runtime lane at all.
        # Flow is directional or it is nothing.
        source = (ROOT / "scripts/seed_material_rendering_profiles.py").read_text(encoding="utf-8")
        body = source.split("def promote_water_flow_lanes", 1)[1].split("\ndef ", 1)[0]
        for retired in ('"wave"', '"wind"', '"grass_stroke"'):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, body)


if __name__ == "__main__":
    unittest.main()
