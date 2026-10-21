#!/usr/bin/env python3
# =============================================================================
# fl4260_review_subject.py    (FL-4260 / FL-4512)
# =============================================================================
# WHAT A "REVIEW SUBJECT" IS, IN PLAIN LANGUAGE
# --------------------------------------------
# Somewhere in this project a human is asked to look at a text character and
# answer one question: "is this the right character to draw here?"
#
# But "here" is not a single place, and the question is not asked in a vacuum.
# When a reviewer accepts a character, they are really accepting a whole little
# bundle of facts that were in front of them at that moment:
#
#     * WHICH character won                  (the "primary" glyph)
#     * WHAT it actually looks like          (its rendered bitmap)
#     * WHERE it won                         (which material / direction /
#                                             density cells it was chosen for)
#     * WHAT IT BEAT                         (the full ordered list of rival
#                                             candidate characters in each cell)
#     * BY WHICH RULE it beat them           (the role and the tie-break rule)
#     * ON WHAT MEASUREMENTS                 (the shape descriptors of the
#                                             winner AND of every rival)
#
# That whole bundle is the REVIEW SUBJECT. It is the thing the human judged.
#
# This file turns that bundle into a single fingerprint — a "sha256" hash, which
# is a short string of letters and numbers that changes completely if ANY part
# of the bundle changes, and stays byte-for-byte identical if nothing changes.
#
# WHY THIS FILE HAD TO EXIST (THE DEFECT IT REPAIRS)
# --------------------------------------------------
# Until now, a reviewer's acceptance was thrown away whenever the project
# regenerated its character catalog. The old check compared two GLOBAL hashes
# covering the entire catalog and the entire table. Change one unrelated cell
# anywhere in the project and every single acceptance was marked stale at once —
# which is exactly what happened: 72 of 72 acceptances died together on
# 2026-07-22, none of them for a reason connected to what was actually reviewed.
#
# A global hash cannot answer "did THIS reviewer's subject change?". It can only
# answer "did ANYTHING change?". Those are different questions, and only the
# first one can support the rule this project actually wants:
#
#     KEEP the decisions whose subject is unchanged.
#     RE-REVIEW only the decisions whose subject really moved.
#
# The fingerprint built here answers the first question, so that rule becomes
# implementable for the first time.
#
# WHY THIS IS A SEPARATE FILE
# ---------------------------
# The migration tool has to compute fingerprints for the OLD (pre-regeneration)
# artifacts and the NEW (post-regeneration) artifacts and compare them. So the
# computation cannot read fixed file paths from disk — it has to be a pure
# function you hand already-loaded data to. That is what lives here, and both
# the receipt apparatus and the migration tool import it, so there is exactly
# one definition of "what was reviewed" (the project calls this the Law-1
# single-owner rule).
#
# Run the self-check with:
#     python3 scripts/fl4260_review_subject.py --self-check
# =============================================================================

from __future__ import annotations

import hashlib
import json
from typing import Any

# The identity of this fingerprint format. If the recipe below ever changes —
# a new field added, a field removed, a different ordering — this string MUST be
# bumped too. Otherwise two fingerprints computed by two different versions of
# this file could be compared against each other and wrongly appear equal or
# unequal. The version string is folded INTO the hash (see canonical_subject),
# so a bump automatically invalidates every previously computed fingerprint,
# which is the safe direction to fail.
REVIEW_SUBJECT_SCHEMA = "fl4260.review_subject.v1"

# The per-glyph measurements the winner-election actually reads. A reviewer
# looking at a cell was implicitly judging the outcome of a comparison made over
# exactly these numbers, so a change to any of them changes what was reviewed.
#
#   shape6_norm          six numbers summarising the glyph's ink distribution
#   shape6_density       how much of the cell is inked, 0.0 (blank) to 1.0 (full)
#   principal_axis_deg   the dominant stroke angle in degrees
#   external10           ink coverage sampled at ten points around the border
#   mass_center_xy       where the "centre of gravity" of the ink sits
#   edge_contact_mask    which cell edges the ink actually touches
#   symmetry_class       coarse label: "balanced" or "asymmetric"
#   rendered_bitmap_sha256   fingerprint of the drawn pixels themselves
#
# The last one deserves a note: it is included even though the others are
# derived from the same pixels, because the derived numbers are ROUNDED. Two
# visibly different glyphs can round to identical descriptors. Including the raw
# bitmap fingerprint closes that gap.
DESCRIPTOR_FIELDS = (
    "shape6_norm",
    "shape6_density",
    "principal_axis_deg",
    "external10",
    "mass_center_xy",
    "edge_contact_mask",
    "symmetry_class",
    "rendered_bitmap_sha256",
)

# Written into a descriptor slot when a glyph named by the table has no shape
# receipt at all. This happens for real: the June-2026 tables named glyphs from a
# PLANNED inventory that was never rendered, and a later catalog trim removed
# them outright. "Absent" is itself a meaningful part of the subject — a cell
# whose rival list used to contain a now-deleted glyph is a genuinely different
# comparison, so it must change the fingerprint rather than be silently skipped.
DESCRIPTOR_ABSENT = "absent"


def _canonical_json(value: Any) -> str:
    """Serialise a value to the one exact byte string this module hashes.

    Two rules make this deterministic across machines and Python versions:

      sort_keys=True   dictionary keys always come out in alphabetical order, so
                       a dict built in a different insertion order still hashes
                       the same.
      separators       pins the punctuation to "," and ":" with no spaces, so
                       cosmetic formatting cannot change the bytes.

    ensure_ascii=True (the default) is deliberately left on: it escapes non-ASCII
    characters to \\uXXXX form, which keeps the byte string identical regardless
    of the platform's text encoding.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def descriptor_digest(receipt: dict[str, Any] | None) -> str:
    """Reduce one glyph's shape measurements to a short fingerprint.

    `receipt` is that glyph's row from the shape-receipts file, or None when the
    glyph has no receipt (it was planned but never rendered, or it was removed
    from the catalog).

    Returns the literal string "absent" for a missing glyph, otherwise the first
    16 hex characters of the sha256 over the descriptor fields listed in
    DESCRIPTOR_FIELDS. Sixteen characters is 64 bits — far more than enough to
    make an accidental collision between a few thousand glyphs impossible in
    practice, while keeping the stored subject readable by a human scanning it.
    """
    if receipt is None:
        return DESCRIPTOR_ABSENT
    # Pull exactly the listed fields, in the listed order, so an unrelated new
    # field appearing in the receipts file later cannot disturb the fingerprint.
    payload = {field: receipt.get(field) for field in DESCRIPTOR_FIELDS}
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()[:16]


def canonical_subject(
    primary_glyph_id: int,
    rendered_bitmap_sha256: str | None,
    contexts: list[dict[str, Any]],
    descriptor_hashes: dict[str, str],
) -> dict[str, Any]:
    """Assemble the bundle that a reviewer's decision was actually about.

    Parameters
    ----------
    primary_glyph_id
        The winning glyph — the character the reviewer said yes or no to.
    rendered_bitmap_sha256
        Fingerprint of that winner's drawn pixels. None when it has no receipt.
    contexts
        One entry per profile/direction/density cell this glyph won, each already
        carrying its own ordered rival list, role and tie rule. Sorted here by
        cell key so the caller cannot affect the outcome by iterating in a
        different order.
    descriptor_hashes
        Map of glyph id (as a STRING, because JSON object keys are always
        strings) to that glyph's descriptor digest. Covers the winner and every
        rival in every context.

    Returns the assembled bundle as a plain dictionary. It is returned rather
    than hashed immediately so it can be stored alongside the fingerprint in a
    receipt — a future reader can then see exactly WHAT was reviewed, not merely
    that some opaque hash matched.
    """
    return {
        # Folded in so a future change to this recipe cannot silently compare as
        # equal against fingerprints made by the current recipe.
        "schema": REVIEW_SUBJECT_SCHEMA,
        "primary_glyph_id": int(primary_glyph_id),
        "rendered_bitmap_sha256": rendered_bitmap_sha256,
        # Sorting by the human-readable cell key ("GRASS/N/D0") gives a stable
        # order that does not depend on dictionary iteration.
        "contexts": sorted(contexts, key=lambda c: c["key"]),
        "descriptor_hashes": dict(sorted(descriptor_hashes.items())),
    }


def review_subject_sha256(subject: dict[str, Any]) -> str:
    """Fingerprint an assembled subject bundle.

    Full 64-character sha256 here, not the shortened form used for individual
    descriptors: this value is the identity a stored acceptance is keyed by, so
    it is the one place where collision resistance genuinely matters.
    """
    return hashlib.sha256(_canonical_json(subject).encode("utf-8")).hexdigest()


def build_review_subjects(
    tables: dict[str, Any],
    shape_receipts_by_gid: dict[int, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Derive every current review subject from a profile-tables snapshot.

    This is the function the migration compares across time: hand it the OLD
    tables and you get the old subjects, hand it the NEW tables and you get the
    new ones. Nothing here touches the filesystem, so the same code can describe
    two different points in the project's history.

    Parameters
    ----------
    tables
        A loaded material.morphology.v2.profile_tables.json structure.
    shape_receipts_by_gid
        Every glyph's measurements, keyed by integer glyph id.

    Returns a mapping of row id ("P-616") to a record holding the subject bundle,
    its fingerprint, and the plain facts a reviewer needs shown to them.

    Only NON-RARE cells participate. A cell flagged `rare_cell` is one the
    generator judged too thinly populated to elect a trustworthy winner, and the
    review queue has always excluded them; including them here would invent
    review work that no gate ever asked for.
    """
    # First pass: group every cell a glyph wins under that glyph, because one
    # character routinely wins several cells and the reviewer judged it once for
    # all of them.
    contexts_by_gid: dict[int, list[dict[str, Any]]] = {}
    for profile, lane_table in tables.get("profiles", {}).items():
        for direction, density_cells in lane_table.items():
            for density, cell in density_cells.items():
                if not isinstance(cell, dict) or cell.get("rare_cell"):
                    continue
                # The rival field. Order is preserved exactly as the generator
                # emitted it, because position in this list IS rank — the first
                # entry is the best-placed candidate. Re-sorting it would erase
                # the very information the tie rule operates on.
                candidate_ids = [int(g) for g in cell.get("candidate_glyph_ids", [])]
                candidate_receipt_ids = list(cell.get("candidate_receipt_ids", []))
                for gid in cell.get("primary_glyph_ids", []):
                    if not isinstance(gid, int):
                        continue
                    contexts_by_gid.setdefault(int(gid), []).append({
                        "key": f"{profile}/{direction}/{density}",
                        "profile": profile,
                        "direction": direction,
                        "density": density,
                        "role": cell.get("role"),
                        "tie_rule": cell.get("tie_rule"),
                        "candidate_glyph_ids": candidate_ids,
                        "candidate_receipt_ids": candidate_receipt_ids,
                    })

    # Second pass: turn each group into a fingerprinted subject.
    subjects: dict[str, dict[str, Any]] = {}
    for gid, contexts in contexts_by_gid.items():
        # Collect the descriptor digest for the winner and for every rival it
        # was compared against, across all of its cells. A change to ANY of them
        # changes the comparison the reviewer saw, so all of them belong in the
        # subject.
        involved: set[int] = {gid}
        for context in contexts:
            involved.update(context["candidate_glyph_ids"])
        descriptor_hashes = {
            str(other): descriptor_digest(shape_receipts_by_gid.get(other))
            for other in sorted(involved)
        }

        primary_receipt = shape_receipts_by_gid.get(gid)
        subject = canonical_subject(
            primary_glyph_id=gid,
            rendered_bitmap_sha256=(
                primary_receipt.get("rendered_bitmap_sha256") if primary_receipt else None
            ),
            contexts=contexts,
            descriptor_hashes=descriptor_hashes,
        )
        subjects[f"P-{gid}"] = {
            "row_id": f"P-{gid}",
            "glyph_id": gid,
            "review_subject": subject,
            "review_subject_sha256": review_subject_sha256(subject),
            # Convenience facts pulled out for the review surface and for gates,
            # so callers do not have to walk into the nested subject bundle.
            "context_count": len(contexts),
            "context_keys": sorted(c["key"] for c in contexts),
            "profiles": sorted({c["profile"] for c in contexts}),
            # A glyph the atlas cannot actually draw can never be promoted into a
            # runtime lane, so surfacing it here lets the queue explain WHY a row
            # is unpromotable instead of silently dropping it.
            "primary_has_shape_receipt": primary_receipt is not None,
        }
    return subjects


def subject_delta(old: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    """Explain, in reviewable terms, how two subjects differ.

    A fingerprint mismatch tells a reviewer that something moved but not WHAT —
    and "look at all of it again" is precisely the useless instruction this whole
    repair exists to avoid. This walks the two bundles and reports the specific
    differences, so a re-review can be as narrow as the change actually was.

    Returns a dict with four keys, each empty when that aspect is unchanged:

      contexts_added / contexts_removed
          cells this glyph started or stopped winning
      candidates_changed
          per shared cell, the before/after rival lists (order included, since
          position in the list is rank)
      descriptors_changed
          glyphs whose shape measurements moved, with a note when a glyph
          appeared or vanished from the catalog entirely
      primary_bitmap_changed
          True when the winner's own drawn pixels changed
    """
    if old is None or new is None:
        return {"contexts_added": [], "contexts_removed": [], "candidates_changed": [],
                "descriptors_changed": [], "primary_bitmap_changed": False,
                "note": "no prior subject to compare against"}

    old_contexts = {c["key"]: c for c in old.get("contexts", [])}
    new_contexts = {c["key"]: c for c in new.get("contexts", [])}

    candidates_changed = []
    for key in sorted(set(old_contexts) & set(new_contexts)):
        before = old_contexts[key].get("candidate_glyph_ids", [])
        after = new_contexts[key].get("candidate_glyph_ids", [])
        if before != after:
            candidates_changed.append({
                "context": key,
                "before": before,
                "after": after,
                # Membership changes matter more than pure reordering, so they
                # are called out separately rather than left for the reader to
                # spot by comparing two long lists.
                "added": sorted(set(after) - set(before)),
                "removed": sorted(set(before) - set(after)),
                "reordered_only": set(before) == set(after),
            })

    old_desc = old.get("descriptor_hashes", {})
    new_desc = new.get("descriptor_hashes", {})
    descriptors_changed = []
    for gid in sorted(set(old_desc) | set(new_desc), key=lambda g: int(g)):
        before, after = old_desc.get(gid), new_desc.get(gid)
        if before == after:
            continue
        descriptors_changed.append({
            "glyph_id": int(gid),
            "before": before,
            "after": after,
            "vanished_from_catalog": after == DESCRIPTOR_ABSENT,
            "new_to_catalog": before == DESCRIPTOR_ABSENT,
        })

    return {
        "contexts_added": sorted(set(new_contexts) - set(old_contexts)),
        "contexts_removed": sorted(set(old_contexts) - set(new_contexts)),
        "candidates_changed": candidates_changed,
        "descriptors_changed": descriptors_changed,
        "primary_bitmap_changed": (
            old.get("rendered_bitmap_sha256") != new.get("rendered_bitmap_sha256")
        ),
    }


def _self_check() -> int:
    """Prove the three properties the migration depends on.

    Kept runnable as a script so the guarantees can be demonstrated without a
    test harness — useful when explaining the repair to a reviewer.
    """
    receipts = {
        616: {"shape6_norm": [0.1], "shape6_density": 0.2, "rendered_bitmap_sha256": "aa"},
        562: {"shape6_norm": [0.3], "shape6_density": 0.4, "rendered_bitmap_sha256": "bb"},
        999: {"shape6_norm": [0.5], "shape6_density": 0.6, "rendered_bitmap_sha256": "cc"},
    }

    def tables_with(grass_candidates, rock_candidates=(999,)):
        return {"profiles": {
            "GRASS": {"N": {"D0": {
                "primary_glyph_ids": [616], "candidate_glyph_ids": list(grass_candidates),
                "candidate_receipt_ids": [f"FL4131-M2-{g}" for g in grass_candidates],
                "role": "sparse_blade", "tie_rule": "shape6_then_gid"}}},
            "ROCK": {"S": {"D3": {
                "primary_glyph_ids": [999], "candidate_glyph_ids": list(rock_candidates),
                "candidate_receipt_ids": [f"FL4131-M2-{g}" for g in rock_candidates],
                "role": "dense_face", "tie_rule": "shape6_then_gid"}}},
        }}

    base = build_review_subjects(tables_with([616, 562]), receipts)

    # PROPERTY 1 — stability. Same inputs must give byte-identical fingerprints,
    # or nothing could ever be preserved across a regeneration.
    again = build_review_subjects(tables_with([616, 562]), receipts)
    assert base["P-616"]["review_subject_sha256"] == again["P-616"]["review_subject_sha256"], \
        "identical input produced a different fingerprint"

    # PROPERTY 2 — selectivity. This is the whole point of the repair: changing
    # an UNRELATED row must leave this row's fingerprint untouched. Under the old
    # global-hash rule this is precisely what failed.
    unrelated = build_review_subjects(tables_with([616, 562], rock_candidates=(999, 562)), receipts)
    assert base["P-616"]["review_subject_sha256"] == unrelated["P-616"]["review_subject_sha256"], \
        "an unrelated cell change disturbed this row (the defect being repaired)"
    assert base["P-999"]["review_subject_sha256"] != unrelated["P-999"]["review_subject_sha256"], \
        "the row that really changed did not change fingerprint"

    # PROPERTY 3 — sensitivity. A change to what the reviewer actually saw must
    # invalidate the decision. Three separate ways in:
    #   (a) the rival list changes
    changed_rivals = build_review_subjects(tables_with([616, 562, 999]), receipts)
    assert base["P-616"]["review_subject_sha256"] != changed_rivals["P-616"]["review_subject_sha256"], \
        "adding a rival candidate left the fingerprint unchanged"
    #   (b) rival ORDER changes (order is rank, so this is a different contest)
    reordered = build_review_subjects(tables_with([562, 616]), receipts)
    assert base["P-616"]["review_subject_sha256"] != reordered["P-616"]["review_subject_sha256"], \
        "reordering the rivals left the fingerprint unchanged"
    #   (c) a rival's own measurements change
    moved = dict(receipts)
    moved[562] = {"shape6_norm": [0.31], "shape6_density": 0.4, "rendered_bitmap_sha256": "bb"}
    remeasured = build_review_subjects(tables_with([616, 562]), moved)
    assert base["P-616"]["review_subject_sha256"] != remeasured["P-616"]["review_subject_sha256"], \
        "a rival's descriptor change left the fingerprint unchanged"

    print("fl4260_review_subject self-check: OK")
    print(f"  schema           : {REVIEW_SUBJECT_SCHEMA}")
    print(f"  sample row       : P-616")
    print(f"  sample fingerprint: {base['P-616']['review_subject_sha256'][:24]}...")
    print("  proven: stable under no change; selective against unrelated change;")
    print("          sensitive to rival set, rival order, and rival measurements.")
    return 0


if __name__ == "__main__":
    import sys
    if "--self-check" in sys.argv:
        raise SystemExit(_self_check())
    print(__doc__)
    print("run with --self-check to prove the stability/selectivity/sensitivity properties")
    raise SystemExit(0)
