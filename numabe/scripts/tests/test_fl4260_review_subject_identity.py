#!/usr/bin/env python3
# =============================================================================
# test_fl4260_review_subject_identity.py    (FL-4260 / FL-4512)
# =============================================================================
# WHAT IS BEING PROTECTED HERE
# ----------------------------
# When a human accepts a text character for a rendering slot, they are judging a
# whole bundle of things at once: which character won, what it looks like, which
# slots it won, which rival characters it beat, and the measurements that decided
# the contest. That bundle is called the REVIEW SUBJECT.
#
# Until this repair, an acceptance was thrown away whenever ANY part of the
# project's character catalog was regenerated, because the check compared two
# catalog-wide hashes. On 2026-07-22 that condemned all 72 acceptances at once,
# not one of them for a reason connected to what had actually been reviewed.
#
# The repair gives every subject its own fingerprint, so the project can finally
# do the sensible thing:
#
#     KEEP the decisions whose subject did not change.
#     RE-REVIEW only the decisions whose subject really moved.
#
# These tests pin that behaviour. The most important one is
# `test_an_unrelated_cell_change_does_not_disturb_this_row` — it is the exact
# defect being repaired, stated as an executable check.
#
# THE MISTAKE THESE TESTS ALSO BLOCK
# ----------------------------------
# There is an obvious wrong way to make the gate go green: quietly treat old
# decisions as current. That would fabricate human acceptance, which is worse
# than the stale-hash problem it replaces. So there are tests proving that an
# old receipt carries NO weight on its own, and that the migration refuses to
# carry a decision across unless the subject matches byte-for-byte.
#
# Run with:  python3 -m unittest scripts.tests.test_fl4260_review_subject_identity
# =============================================================================

import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, relative_path: str):
    """Import a script by file path.

    These tools live in scripts/ rather than an installed package, so they
    cannot be imported by name.
    """
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    assert spec and spec.loader, f"cannot load {relative_path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SUBJECT = _load("fl4260_review_subject", "scripts/fl4260_review_subject.py")
RECEIPTS = _load("fl4260_review_receipts", "scripts/fl4260_review_receipts.py")
MIGRATE = _load("fl4260_migrate_review_receipts", "scripts/fl4260_migrate_review_receipts.py")
ADMISSION = _load("fl4482_native_admission", "scripts/fl4482_native_admission.py")


# ── tiny synthetic world, so the tests do not depend on shipped artifacts ────

def measurements(gid: int, density: float = 0.2, bitmap: str = "aa"):
    """One glyph's shape measurements, in the shape the real receipts use."""
    return {
        "glyph_id": gid,
        "shape6_norm": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        "shape6_density": density,
        "principal_axis_deg": 90.0,
        "external10": [0.0] * 10,
        "mass_center_xy": [0.5, 0.5],
        "edge_contact_mask": [],
        "symmetry_class": "balanced",
        "rendered_bitmap_sha256": bitmap,
    }


def cell(primary: int, candidates, role="sparse_blade", tie="shape6_then_gid"):
    return {
        "primary_glyph_ids": [primary],
        "candidate_glyph_ids": list(candidates),
        "candidate_receipt_ids": [f"FL4131-M2-{g}" for g in candidates],
        "role": role,
        "tie_rule": tie,
        "rare_cell": False,
    }


def tables(profiles):
    return {"profiles": profiles, "generated_at": "2026-08-02"}


BASE_TABLES = tables({
    "GRASS": {"N": {"D0": cell(616, [616, 562])}},
    "ROCK": {"S": {"D3": cell(999, [999, 700], role="dense_face")}},
})
BASE_RECEIPTS = {gid: measurements(gid) for gid in (616, 562, 999, 700)}


class SubjectIdentityIsSelective(unittest.TestCase):
    """The heart of the repair: only real change to THIS row should count."""

    def subjects(self, tbl, recs=None):
        return SUBJECT.build_review_subjects(tbl, recs or BASE_RECEIPTS)

    def test_identical_inputs_produce_an_identical_fingerprint(self) -> None:
        # Without this, nothing could ever be preserved across a regeneration.
        first = self.subjects(BASE_TABLES)["P-616"]["review_subject_sha256"]
        second = self.subjects(BASE_TABLES)["P-616"]["review_subject_sha256"]
        self.assertEqual(first, second)

    def test_an_unrelated_cell_change_does_not_disturb_this_row(self) -> None:
        # THE DEFECT, stated as a check. Editing the ROCK cell must leave the
        # GRASS reviewer's decision alone. The old global-hash rule failed here,
        # which is how 72 acceptances died for one unrelated catalog edit.
        moved_rock = tables({
            "GRASS": {"N": {"D0": cell(616, [616, 562])}},
            "ROCK": {"S": {"D3": cell(999, [999, 700, 562], role="dense_face")}},
        })
        before = self.subjects(BASE_TABLES)
        after = self.subjects(moved_rock)
        self.assertEqual(
            before["P-616"]["review_subject_sha256"],
            after["P-616"]["review_subject_sha256"],
            "an unrelated cell edit invalidated this row — the repaired defect returned")
        self.assertNotEqual(
            before["P-999"]["review_subject_sha256"],
            after["P-999"]["review_subject_sha256"],
            "the row that actually changed kept its old fingerprint")

    def test_a_changed_rival_set_invalidates_the_decision(self) -> None:
        changed = tables({"GRASS": {"N": {"D0": cell(616, [616, 562, 700])}}})
        self.assertNotEqual(
            self.subjects(BASE_TABLES)["P-616"]["review_subject_sha256"],
            self.subjects(changed)["P-616"]["review_subject_sha256"])

    def test_reordered_rivals_invalidate_the_decision(self) -> None:
        # Position in the candidate list IS rank, so a different order is a
        # different contest even though the same glyphs took part.
        reordered = tables({"GRASS": {"N": {"D0": cell(616, [562, 616])}}})
        self.assertNotEqual(
            self.subjects(BASE_TABLES)["P-616"]["review_subject_sha256"],
            self.subjects(reordered)["P-616"]["review_subject_sha256"])

    def test_a_remeasured_rival_invalidates_the_decision(self) -> None:
        # The rival list is unchanged, but one rival's shape moved, so the
        # comparison the reviewer saw is no longer the comparison on file.
        remeasured = dict(BASE_RECEIPTS)
        remeasured[562] = measurements(562, density=0.9)
        self.assertNotEqual(
            self.subjects(BASE_TABLES)["P-616"]["review_subject_sha256"],
            self.subjects(BASE_TABLES, remeasured)["P-616"]["review_subject_sha256"])

    def test_the_winners_own_pixels_are_part_of_the_subject(self) -> None:
        redrawn = dict(BASE_RECEIPTS)
        redrawn[616] = measurements(616, bitmap="ff")
        self.assertNotEqual(
            self.subjects(BASE_TABLES)["P-616"]["review_subject_sha256"],
            self.subjects(BASE_TABLES, redrawn)["P-616"]["review_subject_sha256"])

    def test_rare_cells_are_excluded_from_review(self) -> None:
        # A rare cell is one the generator judged too thin to elect a trustworthy
        # winner. Including it would invent review work no gate ever asked for.
        rare = cell(555, [555])
        rare["rare_cell"] = True
        with_rare = tables({"GRASS": {"N": {"D0": cell(616, [616, 562]), "D1": rare}}})
        self.assertNotIn("P-555", self.subjects(with_rare))

    def test_the_schema_version_is_folded_into_the_fingerprint(self) -> None:
        # Otherwise a future change to the recipe could produce a fingerprint that
        # collides with one made by today's recipe, silently granting acceptance.
        subject = self.subjects(BASE_TABLES)["P-616"]["review_subject"]
        self.assertEqual(subject["schema"], SUBJECT.REVIEW_SUBJECT_SCHEMA)


class OldReceiptsCarryNoWeight(unittest.TestCase):
    """Fail-shut: a decision that cannot name its subject grants nothing."""

    def queue(self):
        subjects = SUBJECT.build_review_subjects(BASE_TABLES, BASE_RECEIPTS)
        return {
            "rows": [{
                "row_id": row_id,
                "glyph_id": data["glyph_id"],
                "review_subject_sha256": data["review_subject_sha256"],
                "review_subject": data["review_subject"],
            } for row_id, data in sorted(subjects.items())],
            "required_row_ids": sorted(subjects),
        }

    def test_a_v2_receipt_alone_does_not_grant_acceptance(self) -> None:
        # Every receipt written before this repair looks like this: a glyph id, an
        # action, and two global hashes. It must NOT count, because nothing in it
        # says which subject was judged.
        legacy = [{
            "schema_version": RECEIPTS.RECEIPT_SCHEMA_V2_RETIRED,
            "glyph_id": 616, "action": "accept", "reviewer": "agent-prepass-2026-06-16",
            "provenance_hashes": {"source_table_hash": "x", "shape_catalog_manifest_hash": "y"},
        }]
        queue = self.queue()
        counts = RECEIPTS.review_state(queue, legacy)
        self.assertEqual(counts[RECEIPTS.STATE_ACCEPTED], 0,
                         "a subject-less legacy receipt granted acceptance")
        row = next(r for r in queue["rows"] if r["row_id"] == "P-616")
        # It fails on BOTH counts — no subject fingerprint and no human
        # authority — and the authority failure is reported, because that is the
        # one a reviewer has to act on.
        self.assertEqual(row["review_state"], RECEIPTS.STATE_HISTORICAL_NONHUMAN)

    def test_a_prior_decision_is_shown_rather_than_discarded(self) -> None:
        # A subject-changed row must hand the reviewer their own previous call as
        # context. Reporting it as bare "unreviewed" would throw away history.
        legacy = [{"glyph_id": 616, "action": "accept", "reviewer": "someone",
                   "reason": "prior call"}]
        queue = self.queue()
        RECEIPTS.review_state(queue, legacy)
        row = next(r for r in queue["rows"] if r["row_id"] == "P-616")
        self.assertIsNotNone(row["review_receipt"])
        self.assertEqual(row["review_receipt"]["reason"], "prior call")

    def test_a_row_nobody_ever_judged_is_unreviewed_not_changed(self) -> None:
        queue = self.queue()
        RECEIPTS.review_state(queue, [])
        for row in queue["rows"]:
            self.assertEqual(row["review_state"], RECEIPTS.STATE_UNREVIEWED)

    def test_a_receipt_for_the_current_subject_is_honoured(self) -> None:
        # Matching the current subject is necessary but NOT sufficient: the
        # decision also has to come from a human who saw the glyph.
        queue = self.queue()
        row = next(r for r in queue["rows"] if r["row_id"] == "P-616")
        current = [{
            "schema_version": RECEIPTS.RECEIPT_SCHEMA,
            "row_id": "P-616", "glyph_id": 616, "action": "accept",
            "review_subject_sha256": row["review_subject_sha256"],
            "review_authority": RECEIPTS.AUTHORITY_HUMAN,
            "screenshot_path": "docs/agent/captures/p-616.png",
        }]
        counts = RECEIPTS.review_state(queue, current)
        self.assertEqual(counts[RECEIPTS.STATE_ACCEPTED], 1)
        self.assertEqual(row["review_state"], RECEIPTS.STATE_ACCEPTED)

    def test_the_state_vocabulary_names_the_cause_not_the_mechanism(self) -> None:
        # "stale_hash_mismatch" described a hash comparison. "subject_changed"
        # describes the thing that actually invalidates a decision.
        self.assertIn(RECEIPTS.STATE_SUBJECT_CHANGED, RECEIPTS.REVIEW_STATES)
        self.assertNotIn("stale_hash_mismatch", RECEIPTS.REVIEW_STATES)


class TheRequiredSetIsDerived(unittest.TestCase):
    """No hard-coded count may stand in for real coverage."""

    def test_the_literal_72_gate_is_gone(self) -> None:
        source = (ROOT / "scripts/fl4260_review_receipts.py").read_text(encoding="utf-8")
        self.assertNotIn("required == 72", source,
                         "the hard-coded required-count gate is back")

    def test_the_gate_compares_sets_not_counts(self) -> None:
        # A count cannot notice that the artifacts now elect a DIFFERENT set of
        # winners of the same size. Set comparison reports which rows moved.
        source = (ROOT / "scripts/fl4260_review_receipts.py").read_text(encoding="utf-8")
        body = source.split("def review_evidence_gates", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("missing_from_manifest", body)
        self.assertIn("unexpected_in_manifest", body)

    def test_the_required_ids_come_from_the_artifacts(self) -> None:
        subjects = SUBJECT.build_review_subjects(BASE_TABLES, BASE_RECEIPTS)
        self.assertEqual(sorted(subjects), ["P-616", "P-999"])


class MigrationGrantsOnlyOnAnExactMatch(unittest.TestCase):
    """Carrying a decision forward is a copy, never an invention."""

    def manifest_row(self, subject_sha, action="accept"):
        return {
            "row_id": "P-616", "glyph_id": 616, "action": action,
            "old_review_subject_sha256": subject_sha,
            "reviewer": "agent-prepass-2026-06-16",
            "reviewer_timestamp": "2026-06-16T21:55:36Z",
            "screenshot_path": "", "capture_id": "",
            "reason": "pre-pass shape judgement",
            "source_commit": None,
            "source_commit_provenance": "reconstructed_from_file_history",
            "original_schema_version": RECEIPTS.RECEIPT_SCHEMA_V2_RETIRED,
        }

    def test_a_matching_subject_carries_the_decision_over(self) -> None:
        subjects = SUBJECT.build_review_subjects(BASE_TABLES, BASE_RECEIPTS)
        sha = subjects["P-616"]["review_subject_sha256"]
        receipt = MIGRATE._migrated_receipt(self.manifest_row(sha), subjects["P-616"])
        self.assertEqual(receipt["review_subject_sha256"], sha)
        self.assertEqual(receipt["schema_version"], RECEIPTS.RECEIPT_SCHEMA)

    def test_migration_preserves_who_decided_and_when(self) -> None:
        # Direction 7. This matters concretely: most existing decisions were made
        # by an agent pre-pass, not a person, and none carry a screenshot.
        # Migration must make that provenance MORE visible, never less.
        subjects = SUBJECT.build_review_subjects(BASE_TABLES, BASE_RECEIPTS)
        row = self.manifest_row(subjects["P-616"]["review_subject_sha256"])
        receipt = MIGRATE._migrated_receipt(row, subjects["P-616"])
        self.assertEqual(receipt["reviewer"], "agent-prepass-2026-06-16")
        self.assertEqual(receipt["reviewer_timestamp"], "2026-06-16T21:55:36Z")
        self.assertEqual(receipt["action"], "accept")
        self.assertEqual(receipt["screenshot_path"], "")

    def test_a_migrated_receipt_is_visibly_a_migration(self) -> None:
        # So nobody later mistakes a carried-over decision for a fresh review.
        subjects = SUBJECT.build_review_subjects(BASE_TABLES, BASE_RECEIPTS)
        row = self.manifest_row(subjects["P-616"]["review_subject_sha256"])
        receipt = MIGRATE._migrated_receipt(row, subjects["P-616"])
        self.assertIn("migrated_from", receipt)
        self.assertEqual(receipt["migrated_from"]["original_schema_version"],
                         RECEIPTS.RECEIPT_SCHEMA_V2_RETIRED)
        self.assertEqual(receipt["migrated_from"]["source_commit_provenance"],
                         "reconstructed_from_file_history")

    def test_a_reject_migrates_as_a_reject(self) -> None:
        # Migration is action-preserving. Silently upgrading a rejection into an
        # acceptance would be the worst possible failure of this tool.
        subjects = SUBJECT.build_review_subjects(BASE_TABLES, BASE_RECEIPTS)
        row = self.manifest_row(subjects["P-616"]["review_subject_sha256"], action="reject")
        self.assertEqual(MIGRATE._migrated_receipt(row, subjects["P-616"])["action"], "reject")

    def test_the_outcome_vocabulary_covers_all_four_cases(self) -> None:
        for outcome in (MIGRATE.OUTCOME_MIGRATED, MIGRATE.OUTCOME_HISTORICAL,
                        MIGRATE.OUTCOME_SUBJECT_CHANGED, MIGRATE.OUTCOME_NEW):
            self.assertIsInstance(outcome, str)
        self.assertEqual(
            len({MIGRATE.OUTCOME_MIGRATED, MIGRATE.OUTCOME_HISTORICAL,
                 MIGRATE.OUTCOME_SUBJECT_CHANGED, MIGRATE.OUTCOME_NEW}), 4)


class TheSubjectDeltaExplainsItself(unittest.TestCase):
    """A reviewer must be told what moved, not merely that something did."""

    def test_a_rival_swap_is_reported_precisely(self) -> None:
        before = SUBJECT.build_review_subjects(BASE_TABLES, BASE_RECEIPTS)["P-616"]["review_subject"]
        after_tables = tables({"GRASS": {"N": {"D0": cell(616, [616, 700])}}})
        after = SUBJECT.build_review_subjects(after_tables, BASE_RECEIPTS)["P-616"]["review_subject"]
        delta = SUBJECT.subject_delta(before, after)
        change = delta["candidates_changed"][0]
        self.assertEqual(change["context"], "GRASS/N/D0")
        self.assertEqual(change["added"], [700])
        self.assertEqual(change["removed"], [562])
        self.assertFalse(change["reordered_only"])

    def test_pure_reordering_is_labelled_as_such(self) -> None:
        before = SUBJECT.build_review_subjects(BASE_TABLES, BASE_RECEIPTS)["P-616"]["review_subject"]
        after_tables = tables({"GRASS": {"N": {"D0": cell(616, [562, 616])}}})
        after = SUBJECT.build_review_subjects(after_tables, BASE_RECEIPTS)["P-616"]["review_subject"]
        self.assertTrue(SUBJECT.subject_delta(before, after)["candidates_changed"][0]["reordered_only"])

    def test_a_glyph_leaving_the_catalog_is_called_out(self) -> None:
        # This is the case that produced 22 of the original stale rows: primaries
        # naming glyphs a later catalog trim removed outright.
        before = SUBJECT.build_review_subjects(BASE_TABLES, BASE_RECEIPTS)["P-616"]["review_subject"]
        trimmed = {gid: m for gid, m in BASE_RECEIPTS.items() if gid != 562}
        after = SUBJECT.build_review_subjects(BASE_TABLES, trimmed)["P-616"]["review_subject"]
        delta = SUBJECT.subject_delta(before, after)
        vanished = [d["glyph_id"] for d in delta["descriptors_changed"] if d["vanished_from_catalog"]]
        self.assertEqual(vanished, [562])

    def test_a_new_context_is_reported(self) -> None:
        before = SUBJECT.build_review_subjects(BASE_TABLES, BASE_RECEIPTS)["P-616"]["review_subject"]
        wider = tables({"GRASS": {"N": {"D0": cell(616, [616, 562]),
                                        "D1": cell(616, [616, 562])}}})
        after = SUBJECT.build_review_subjects(wider, BASE_RECEIPTS)["P-616"]["review_subject"]
        self.assertEqual(SUBJECT.subject_delta(before, after)["contexts_added"], ["GRASS/N/D1"])


class TheShippedArtifactsAreConsistent(unittest.TestCase):
    """Checks against the real files, so the tools cannot drift from them."""

    GEN = ROOT / "assets/glyphs/generated"

    def test_every_migrated_receipt_names_a_subject(self) -> None:
        path = self.GEN / "material.morphology.v2.manual_review_receipts.jsonl"
        if not path.exists():
            self.skipTest("receipt corpus not present")
        migrated = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip() and "migrated_from" in line]
        self.assertTrue(migrated, "no migrated receipts found to check")
        for receipt in migrated:
            self.assertTrue(receipt.get("review_subject_sha256"),
                            f"{receipt.get('row_id')} migrated without a subject fingerprint")
            self.assertEqual(receipt["schema_version"], RECEIPTS.RECEIPT_SCHEMA)

    def test_no_migrated_receipt_invented_a_reviewer(self) -> None:
        # Every migrated decision must name the person or process that originally
        # made it. A migration that authored its own reviewer would be fabricating
        # acceptance under a different name.
        path = self.GEN / "material.morphology.v2.manual_review_receipts.jsonl"
        if not path.exists():
            self.skipTest("receipt corpus not present")
        migrated = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip() and "migrated_from" in line]
        for receipt in migrated:
            self.assertNotIn("migrate", str(receipt.get("reviewer", "")).lower(),
                             "a migrated receipt names the migration tool as its reviewer")
            self.assertTrue(receipt.get("reviewer"))
            self.assertTrue(receipt.get("reviewer_timestamp"))



class OnlyAHumanCanGrantAcceptance(unittest.TestCase):
    """Subject identity says WHAT was judged. It never says WHO judged it.

    An earlier version of the state machine checked only the subject, so 51
    migrated agent pre-pass decisions read as `accepted` — automated taste calls
    about to enter the compiled runtime under a human label. These tests pin the
    second condition.
    """

    def queue(self):
        subjects = SUBJECT.build_review_subjects(BASE_TABLES, BASE_RECEIPTS)
        return {
            "rows": [{
                "row_id": row_id,
                "glyph_id": data["glyph_id"],
                "review_subject_sha256": data["review_subject_sha256"],
                "review_subject": data["review_subject"],
            } for row_id, data in sorted(subjects.items())],
            "required_row_ids": sorted(subjects),
        }

    def receipt(self, row_id, subject_sha, **overrides):
        base = {
            "schema_version": RECEIPTS.RECEIPT_SCHEMA,
            "row_id": row_id, "glyph_id": int(row_id.split("-")[1]),
            "action": "accept", "review_subject_sha256": subject_sha,
        }
        base.update(overrides)
        return base

    def state_of(self, row_id, **overrides):
        queue = self.queue()
        row = next(r for r in queue["rows"] if r["row_id"] == row_id)
        RECEIPTS.review_state(
            queue, [self.receipt(row_id, row["review_subject_sha256"], **overrides)])
        return row["review_state"]

    def test_only_the_human_authority_can_grant(self) -> None:
        self.assertEqual(RECEIPTS.GRANTING_AUTHORITIES, frozenset({RECEIPTS.AUTHORITY_HUMAN}))

    def test_a_matching_agent_receipt_does_not_accept(self) -> None:
        self.assertEqual(
            self.state_of("P-616", review_authority=RECEIPTS.AUTHORITY_AGENT),
            RECEIPTS.STATE_HISTORICAL_NONHUMAN)

    def test_a_matching_historical_receipt_does_not_accept(self) -> None:
        self.assertEqual(
            self.state_of("P-616", review_authority=RECEIPTS.AUTHORITY_HISTORICAL_NONHUMAN),
            RECEIPTS.STATE_HISTORICAL_NONHUMAN)

    def test_a_receipt_with_no_authority_field_does_not_accept(self) -> None:
        # Every pre-2026-08 receipt looks like this. Absence must fail shut.
        self.assertEqual(self.state_of("P-616"), RECEIPTS.STATE_HISTORICAL_NONHUMAN)

    def test_a_human_claim_without_a_visual_capture_does_not_accept(self) -> None:
        # Reviewing a rendered glyph means having seen the rendered glyph.
        self.assertEqual(
            self.state_of("P-616", review_authority=RECEIPTS.AUTHORITY_HUMAN),
            RECEIPTS.STATE_HISTORICAL_NONHUMAN)

    def test_a_human_review_with_a_capture_accepts(self) -> None:
        self.assertEqual(
            self.state_of("P-616",
                          review_authority=RECEIPTS.AUTHORITY_HUMAN,
                          screenshot_path="docs/agent/captures/p-616.png"),
            RECEIPTS.STATE_ACCEPTED)

    def test_a_capture_id_also_satisfies_the_visual_requirement(self) -> None:
        self.assertEqual(
            self.state_of("P-616",
                          review_authority=RECEIPTS.AUTHORITY_HUMAN,
                          capture_id="fl4260-review-0001"),
            RECEIPTS.STATE_ACCEPTED)

    def test_every_non_accepted_state_still_awaits_a_human(self) -> None:
        queue = self.queue()
        RECEIPTS.review_state(queue, [])
        for row in queue["rows"]:
            self.assertTrue(row["awaits_human_review"])

    def test_the_writer_refuses_a_defaulted_reviewer(self) -> None:
        with self.assertRaises(ValueError):
            RECEIPTS.write_receipt("P-616", "accept", "looks right", "",
                                   "", "", "", authority=RECEIPTS.AUTHORITY_AGENT)

    def test_the_writer_refuses_a_human_claim_with_nothing_seen(self) -> None:
        with self.assertRaises(ValueError):
            RECEIPTS.write_receipt("P-616", "accept", "looks right", "a-person",
                                   "", "", "", authority=RECEIPTS.AUTHORITY_HUMAN)

    def test_the_cli_no_longer_defaults_attribution_to_operator(self) -> None:
        # The default let any automated invocation manufacture operator identity.
        source = (ROOT / "scripts/fl4260_review_receipts.py").read_text(encoding="utf-8")
        self.assertNotIn('"--reviewer", default="operator"', source)
        self.assertIn('"--reviewer", required=True', source)

    def test_migrated_decisions_are_structurally_unable_to_grant(self) -> None:
        subjects = SUBJECT.build_review_subjects(BASE_TABLES, BASE_RECEIPTS)
        manifest_row = {
            "row_id": "P-616", "glyph_id": 616, "action": "accept",
            "old_review_subject_sha256": subjects["P-616"]["review_subject_sha256"],
            "reviewer": "agent-prepass-2026-06-16",
            "reviewer_timestamp": "2026-06-16T21:55:36Z",
            "screenshot_path": "", "capture_id": "", "reason": "pre-pass",
            "source_commit": None, "source_commit_provenance": "reconstructed_from_file_history",
            "original_schema_version": RECEIPTS.RECEIPT_SCHEMA_V2_RETIRED,
        }
        receipt = MIGRATE._migrated_receipt(manifest_row, subjects["P-616"])
        self.assertEqual(receipt["review_authority"],
                         RECEIPTS.AUTHORITY_HISTORICAL_NONHUMAN)
        self.assertFalse(receipt["migrated_from"]["grants_acceptance"])
        # And prove it end to end through the state machine, not just by field.
        queue = self.queue()
        row = next(r for r in queue["rows"] if r["row_id"] == "P-616")
        RECEIPTS.review_state(queue, [receipt])
        self.assertEqual(row["review_state"], RECEIPTS.STATE_HISTORICAL_NONHUMAN)


class AdmissionComesFromTheNativeRegistry(unittest.TestCase):
    """The atlas decides what is drawable, not a number frozen into source."""

    def test_the_registry_admits_far_beyond_the_retired_ceiling(self) -> None:
        admitted = ADMISSION.admitted_glyph_ids()
        above = {gid for gid in admitted if gid > ADMISSION.RETIRED_FROZEN_CEILING}
        self.assertTrue(above, "registry admits nothing above the retired ceiling")
        self.assertGreater(len(admitted), 1000)

    def test_no_admission_owner_still_compares_against_the_frozen_ceiling(self) -> None:
        # The stale rule declared 22 currently reviewable glyphs undrawable.
        for relative in ("scripts/seed_material_rendering_profiles.py",
                         "scripts/fl4260_review_receipts.py"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(file=relative):
                # The executable form of the retired rule: a comprehension or
                # comparison that filters glyph ids by the frozen number.
                self.assertNotIn("if gid <= 671", source)
                self.assertNotIn("glyph_id <= 671", source)

    def test_the_seeder_derives_admission_from_the_registry(self) -> None:
        source = (ROOT / "scripts/seed_material_rendering_profiles.py").read_text(encoding="utf-8")
        self.assertIn("ADMISSION.admitted_glyph_ids()", source)

    def test_a_glyph_above_the_ceiling_resolves_to_a_real_tile(self) -> None:
        above = sorted(gid for gid in ADMISSION.admitted_glyph_ids()
                       if gid > ADMISSION.RETIRED_FROZEN_CEILING)
        tile = ADMISSION.resolve_tile(above[0])
        self.assertIsNotNone(tile)
        self.assertTrue(tile["page16_path"])
        self.assertEqual(len(tile["rect_xywh"]), 4)

    def test_an_unbound_glyph_resolves_to_nothing(self) -> None:
        unbound = max(ADMISSION.admitted_glyph_ids()) + 1
        self.assertIsNone(ADMISSION.resolve_tile(unbound))
        self.assertFalse(ADMISSION.is_admitted(unbound))


class EveryRequiredSubjectIsVisuallyAddressable(unittest.TestCase):
    """Cross-surface: the review queue and the atlas must agree, row by row.

    A reviewer cannot judge what they cannot see. These run against the real
    shipped artifacts rather than a synthetic fixture, because the claim being
    protected is about THIS queue against THIS atlas.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not (ROOT / "assets/glyphs/generated"
                       "/material.morphology.v2.profile_tables.json").exists():
            raise unittest.SkipTest("morphology artifacts not present")
        cls.queue = RECEIPTS.build_review_receipt_set()
        RECEIPTS.review_state(cls.queue, RECEIPTS.load_jsonl(RECEIPTS.MANUAL_RECEIPTS))

    def test_every_winner_is_native_bound(self) -> None:
        unbound = [r["row_id"] for r in self.queue["rows"]
                   if not ADMISSION.is_admitted(r["glyph_id"])]
        self.assertEqual(unbound, [], "required rows are not drawable by the atlas")

    def test_every_winner_resolves_to_an_exact_tile(self) -> None:
        for row in self.queue["rows"]:
            with self.subTest(row=row["row_id"]):
                ref = row["visual"]["atlas_ref"]
                self.assertIsNotNone(ref, "winner has no atlas tile")
                self.assertTrue(ref["page16_path"])
                self.assertTrue(row["visual"]["atlas_admitted"])

    def test_every_rival_resolves_to_an_exact_tile(self) -> None:
        # The decision is a comparison, so the field has to be viewable too.
        unaddressable = [(row["row_id"], rival["glyph_id"])
                         for row in self.queue["rows"]
                         for rival in row["rival_visuals"]
                         if not rival["atlas_admitted"]]
        self.assertEqual(unaddressable, [])

    def test_no_required_row_is_excluded_as_undrawable(self) -> None:
        # The reported 9-of-21 split came from the frozen ceiling, not from the
        # atlas. Under registry admission the split does not exist.
        above = [r["row_id"] for r in self.queue["rows"]
                 if r["glyph_id"] > ADMISSION.RETIRED_FROZEN_CEILING]
        self.assertTrue(above, "no rows above the ceiling; this test proves nothing")
        for row_id in above:
            row = next(r for r in self.queue["rows"] if r["row_id"] == row_id)
            with self.subTest(row=row_id):
                self.assertTrue(row["visual"]["atlas_admitted"])

    def test_the_whole_queue_currently_awaits_a_human(self) -> None:
        # The honest outstanding count. Not 21, not 9.
        awaiting = [r["row_id"] for r in self.queue["rows"] if r["awaits_human_review"]]
        self.assertEqual(len(awaiting), len(self.queue["rows"]))

    def test_no_row_is_accepted_on_non_human_evidence(self) -> None:
        accepted = [r["row_id"] for r in self.queue["rows"]
                    if r["review_state"] == RECEIPTS.STATE_ACCEPTED]
        self.assertEqual(accepted, [])


class TheGatesAgreeAndDoNotCrash(unittest.TestCase):
    """Stale state names turned a gate into a KeyError instead of a verdict."""

    def test_the_coverage_gate_no_longer_subscripts_a_retired_state(self) -> None:
        # The crash was `counts["stale_hash_mismatch"]` against a dict that no
        # longer carries that key.
        source = (ROOT / "scripts/fl4260_rq153c_coverage_gate.py").read_text(encoding="utf-8")
        self.assertNotIn('counts["stale_hash_mismatch"]', source)

    def test_the_metadata_refresh_gate_derives_blocked_states(self) -> None:
        # It used to hand-write the state names, so a renamed state produced a
        # silently empty blocker list.
        source = (ROOT / "scripts/fl4260_material_morphology_review.py").read_text(encoding="utf-8")
        self.assertIn("for state in rq.REVIEW_STATES", source)
        self.assertNotIn('r["review_state"] == "stale_hash_mismatch"', source)

    def test_the_metadata_refresh_gate_requires_human_authority(self) -> None:
        # reduce_reviews used to grant acceptance from the action alone, which is
        # a second way agent decisions could reach the runtime-facing table.
        source = (ROOT / "scripts/fl4260_material_morphology_review.py").read_text(encoding="utf-8")
        body = source.split("def reduce_reviews", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("GRANTING_AUTHORITIES", body)


if __name__ == "__main__":
    unittest.main()
