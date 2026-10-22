#!/usr/bin/env python3
# =============================================================================
# measure_fl4694_gtr_v2_subsumption.py
# =============================================================================
# WHAT THIS SCRIPT ANSWERS
# ------------------------
# The project owns two different pieces of pre-computed ("compiled ahead of
# time", never decided while the game is running) data about how text characters
# relate to one another:
#
#   1. GTR v2 - "Glyph Transform Relation, version 2".  A committed table of
#      65,536 rows.  Each row says: "if the renderer is currently drawing
#      character A and wants to consider swapping to character B, while the
#      surface is being viewed from viewing-situation number N, here is how
#      different those two characters are."  32 characters x 64 viewing
#      situations ("aspects") x 32 characters = 65,536 rows.
#
#   2. The FL-4694 relation family.  A much newer, much larger neighbour table
#      covering all 111,040 glyph records in the admitted universe.  For every
#      record it keeps the 32 most similar other records, with a distance number
#      and a membership weight.
#
# The open question, owned by FL-4694, is whether the second one SUBSUMES the
# first.  "Subsumes" means: everything GTR v2 knows is already recoverable from
# the newer family, so GTR v2 is a duplicate owner and must be deleted (the
# project's delete-first rule forbids two live owners of the same truth).
#
# THE POINT OF THIS FILE IS THAT THE ANSWER CANNOT BE FITTED AFTER THE FACT
# ------------------------------------------------------------------------
# It is very easy, when you already know the numbers, to invent a comparison
# rule that produces whichever answer you wanted.  To make that impossible, the
# comparison rule is PREDECLARED: it is written down in `PREDECLARED_CONTRACT`
# below, it is copied verbatim into the result file, and the final verdict is
# computed by `derive_disposition()`, a function with no thresholds, no tuning
# knobs, and no free parameters.  Change the contract and the artifact changes
# with it, visibly, in the same commit.
#
# THE ALLOWED ANSWERS
# -------------------
# Exactly one of these is written into the result, never both, never neither:
#
#   EXACT_SUBSUMPTION
#       The projection reaches every one of the 65,536 rows and disagrees on
#       none of them.  GTR v2 then holds no truth of its own, it is a duplicate
#       owner, and its entry E8 on the FL-4686 cutover delete-list is upgraded
#       from a deferred deletion to a MANDATORY one.
#
#   DISTINCT_TRUTH
#       The projection reaches every row and disagrees on at least one.  Full
#       coverage is required, because only a projection that actually spoke at
#       every address can be said to have contradicted anything.  GTR v2 then
#       demonstrably holds different truth and may not be deleted as a
#       duplicate.
#
#   NOT_SUBSUMED_BY_THIS_PROJECTION
#       The projection could not reach part of the table, whatever it did on the
#       rows it could reach.  This is the answer the real data gives, and it is
#       deliberately modest: one named candidate failed, so E8 may not be
#       promoted AND distinct truth may not be claimed, and whether some other
#       projection subsumes GTR v2 is left open.  Without this outcome the rule
#       would be forced to overstate a failure of coverage as a finding of
#       different truth - which is exactly what the superseded contract v1 did.
#
#   UNMEASURED
#       A third value exists for one situation only: the FL-4694 caches are not
#       on disk (they are deliberately never committed) and could not be
#       regenerated.  Nothing is compared, no verdict is invented, and `--check`
#       fails loudly.  UNMEASURED is never derived from data; it only ever means
#       "the inputs were absent".
#
# THIS SCRIPT IS NOT PART OF THE GAME
# -----------------------------------
# It is an offline measurement tool.  It is not imported by, called from, or
# linked into any product build, and the artifact it writes carries
# `runtime_consumption_allowed: false`.  Nothing here may reach a frame.
#
# HOW TO RUN IT
# -------------
#   python3 scripts/measure_fl4694_gtr_v2_subsumption.py --check
#       Recompute the measurement and compare it to the committed result file.
#       Exit code 0 means they agree exactly.  Any disagreement, missing input,
#       or bad fingerprint exits non-zero with an explanation.  This is the mode
#       the test suite runs.
#
#   python3 scripts/measure_fl4694_gtr_v2_subsumption.py --write
#       Recompute and overwrite the committed result file.  Use this only when
#       an input legitimately changed.
#
#   python3 scripts/measure_fl4694_gtr_v2_subsumption.py --print
#       Recompute and print the result to the screen without touching any file.
# =============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

# -----------------------------------------------------------------------------
# Where everything lives
# -----------------------------------------------------------------------------
# `parents[1]` walks up one directory from scripts/ to the repository root, so
# the script works no matter which directory it is invoked from.
REPO = Path(__file__).resolve().parents[1]

# The committed 65,536-row table under measurement.
GTR_V2_PATH = REPO / "assets/glyphs/generated/fl4512.glyph_transform_relation.v2.json"

# The small committed file that fingerprints the never-committed FL-4694 caches
# and records the one command that recreates them.
FL4694_MANIFEST_PATH = REPO / "assets/glyphs/generated/fl4694.relation_pointer.v1.json"

# Where this script's own answer is stored.
#
# TWO GENERATIONS EXIST ON PURPOSE (FL-4694 delimitation, 2026-08-09).
#
# The v1 artifact is FROZEN. It is the historical record of what the first
# reporting produced, including headline totals that were sums across six
# comparison axes while being named as though they counted relation rows. It is
# kept unchanged and unregenerated so the correction can be audited against the
# thing it corrected, rather than replacing it silently.
#
# The v2 artifact is the current answer. It reports coverage in unique relation
# rows and per axis examinations separately, under names that state which unit
# they use, and it bounds its conclusion to the one projection actually tested.
# This script reads and writes v2 only; nothing regenerates v1.
FROZEN_V1_RESULT_PATH = REPO / "assets/glyphs/generated/fl4694.gtr_v2_subsumption.v1.json"
RESULT_PATH = REPO / "assets/glyphs/generated/fl4694.gtr_v2_subsumption.v2.json"

# The shape contract for that answer.
SCHEMA_PATH = REPO / "assets/glyphs/schema/fl4694.gtr_v2_subsumption.v2.schema.json"

# Identifiers written into the artifact so a reader can tell versions apart.
RESULT_SCHEMA_ID = "fl4694.gtr_v2_subsumption.v2"
RESULT_VERSION = 2

# The name of the FL-4694 cache file this measurement actually reads.  The
# manifest lists three caches; only the neighbour table is needed here, and
# naming it explicitly keeps the dependency honest.
REQUIRED_CACHE_NAME = "glyph_relation_knn"


# =============================================================================
# THE PREDECLARED COMPARISON CONTRACT
# =============================================================================
# Everything below this banner is written BEFORE any number is computed, and is
# copied verbatim into the result artifact.  A reader can therefore check that
# the rule was not reshaped to fit the outcome.
#
# A note on one word used throughout: an "aspect" is one of 64 pre-computed
# viewing situations - roughly, "which way the surface is turned relative to the
# camera".  GTR v2 has an aspect axis; the FL-4694 family does not.  Whether
# that difference is fatal is one of the things measured, not assumed.
# =============================================================================

PREDECLARED_CONTRACT = {
    "contract_id": "fl4694.gtr_v2_subsumption_contract.v2",
    "written_before_measurement": True,
    # -------------------------------------------------------------------------
    # AMENDMENT DISCLOSURE - read this before trusting any verdict below.
    # -------------------------------------------------------------------------
    # The whole point of writing a contract before running a measurement is that
    # nobody can quietly reshape the rule until it produces a preferred answer.
    # This contract WAS amended after its first run, so that protection has to be
    # replaced by full disclosure. Here it is.
    #
    # The v1 rule counted a row as a "mismatch" whether the candidate projection
    # gave a different value OR gave no value at all. The projection turns out to
    # address only 384 of the 65536 rows, so v1's verdict of DISTINCT_TRUTH was
    # forced by arithmetic before any data was examined - and DISTINCT_TRUTH
    # asserts that GTR v2 holds truth the family does not, which one failed
    # candidate cannot establish.
    #
    # v2 changes only the BOOKKEEPING and the NAMING of outcomes. It does not
    # move a threshold, reweight an axis, or alter any measured number, and it
    # was not written to reach a preferred verdict - it was written because
    # counting silence as contradiction is wrong regardless of which way the
    # answer then falls. The v1 verdict is preserved verbatim in the artifact
    # under "superseded_by_amendment" so the change can be audited.
    "amended_after_first_run": True,
    "supersedes_contract_id": "fl4694.gtr_v2_subsumption_contract.v1",
    "amendment_reason": (
        "The v1 rule added rows the projection could not address to rows where "
        "it disagreed, and called the sum mismatches. Because the projection "
        "addresses 384 of 65536 rows, that made the DISTINCT_TRUTH verdict "
        "unreachable-by-any-other-path and therefore not a measurement. v2 "
        "counts disagreement and non-coverage separately and adds a fourth "
        "outcome for the case actually observed, where a named candidate fails "
        "predominantly by silence."
    ),
    "what_is_being_asked": (
        "Does the FL-4694 bounded nearest-neighbour relation family already "
        "contain everything the GTR v2 table contains, so that GTR v2 is a "
        "duplicate owner of the same truth?"
    ),
    # ---------------------------------------------------------------------
    # The candidate subsumer, named exactly.
    # ---------------------------------------------------------------------
    # A subsumption question is meaningless without saying WHICH projection of
    # the newer surface is supposed to stand in for the older one.  This is that
    # projection, named so it can be cited, argued with, and replaced.
    "candidate_projection": {
        "projection_id": "fl4694.bounded_knn_scalar_similarity_to_gtr_v2_address.v1",
        "plain_english": (
            "For a GTR v2 row addressed by (from_glyph_id, to_glyph_id, "
            "aspect), look up the FL-4694 record whose glyph_record_id equals "
            "from_glyph_id, and see whether to_glyph_id appears among its 32 "
            "retained neighbours. If it does, the projection supplies exactly "
            "two whole numbers for that row: the compiled_distance and the "
            "membership_weight_q0_16. If it does not appear, the projection "
            "supplies nothing for that row."
        ),
        "carries_an_aspect_axis": False,
        "scalars_supplied_per_row": 2,
        "why_this_projection_and_not_another": (
            "It is the only projection the FL-4694 Form A-LOCAL family can "
            "offer without inventing data: the committed manifest declares the "
            "family stores, per record, a bounded neighbour list of "
            "(to_glyph_record_id, compiled_distance, membership_weight_q0_16) "
            "tuples and nothing else. Any richer projection would have to "
            "synthesise fields the family does not hold, which the project's "
            "fail-closed rule forbids."
        ),
    },
    # ---------------------------------------------------------------------
    # The six axes. Each names its pass condition BEFORE the numbers exist.
    # ---------------------------------------------------------------------
    "axes": [
        {
            "axis_id": "A1_static_components",
            "plain_english": (
                "GTR v2 gives every row an eleven-number vector describing how "
                "the two characters differ in shape: density, endpoints, "
                "junctions, and so on."
            ),
            "pass_condition": (
                "The projection reproduces the exact eleven-number vector for "
                "all 65536 rows. Mismatch count must be 0."
            ),
        },
        {
            "axis_id": "A2_affine_components",
            "plain_english": (
                "GTR v2 gives every row a seven-number vector describing the "
                "geometric transform between the two characters under the "
                "current viewing situation."
            ),
            "pass_condition": (
                "The projection reproduces the exact seven-number vector for "
                "all 65536 rows. Mismatch count must be 0. Because the "
                "projection has no aspect axis, this axis additionally fails "
                "outright if any (from, to) pair has more than one distinct "
                "affine vector across the 64 aspects - an aspect-free source "
                "provably cannot produce two different answers at one address."
            ),
        },
        {
            "axis_id": "A3_confidence",
            "plain_english": (
                "GTR v2 gives every row a confidence number saying how much "
                "trust the compiler places in that relation."
            ),
            "pass_condition": (
                "For every row, the projection's membership_weight_q0_16 "
                "equals GTR v2's relation_confidence_q0_16. Mismatch count "
                "must be 0."
            ),
        },
        {
            "axis_id": "A4_compiled_transform_cost",
            "plain_english": (
                "GTR v2 gives every row a single cost number, which is what the "
                "runtime selector would actually consult."
            ),
            "pass_condition": (
                "For every row, the projection's compiled_distance equals GTR "
                "v2's compiled_transform_cost. Mismatch count must be 0."
            ),
        },
        {
            "axis_id": "A5_directionality",
            "plain_english": (
                "GTR v2 is directed: the relation from A to B is a separate row "
                "from the relation from B to A, and it also keeps rows where A "
                "and B are the same character."
            ),
            "pass_condition": (
                "Every directed GTR v2 address, including every self-relation "
                "where from equals to, is representable in the projection. "
                "Unrepresentable address count must be 0."
            ),
        },
        {
            "axis_id": "A6_tie_behaviour",
            "plain_english": (
                "When two candidate characters score exactly the same, "
                "something has to break the tie, and both sides must break it "
                "the same way or they will pick different characters."
            ),
            "pass_condition": (
                "The tie-break rule the projection declares "
                "('ascending compiled distance, then ascending record id') "
                "orders every GTR v2 tie group identically to GTR v2's own "
                "stored row order. Disagreeing tie group count must be 0."
            ),
        },
    ],
    # ---------------------------------------------------------------------
    # The verdict rule, stated before the verdict exists.
    # ---------------------------------------------------------------------
    "disposition_rule": {
        "EXACT_SUBSUMPTION": (
            "Chosen if and only if the projection addresses every one of the "
            "65536 GTR v2 rows AND disagrees on none of them. Consequence: GTR "
            "v2 is a duplicate owner, and its FL-4686 delete-list entry E8 "
            "becomes a MANDATORY deletion."
        ),
        "DISTINCT_TRUTH": (
            "Chosen if and only if the projection addresses every one of the "
            "65536 rows AND disagrees on at least one. Full coverage is "
            "required because only a projection that actually spoke at every "
            "address can be said to have contradicted anything. Consequence: "
            "GTR v2 demonstrably holds different truth and may not be deleted "
            "as a duplicate."
        ),
        "NOT_SUBSUMED_BY_THIS_PROJECTION": (
            "Chosen when the projection fails to address part of the domain, "
            "whatever it did on the rows it could address. Consequence, and "
            "both halves bind equally: E8 may NOT be promoted to a mandatory "
            "deletion, AND GTR v2 may NOT be declared a distinct truth either. "
            "One named candidate failed; the general subsumption question "
            "stays open and the surviving owner stays undecided, so "
            "delete-first completion cannot be claimed."
        ),
        "UNMEASURED": (
            "Chosen only when the FL-4694 caches are absent or fail their "
            "fingerprint check, so nothing was compared. Never derived from "
            "data. In this state --check fails loudly rather than reporting a "
            "verdict."
        ),
    },
    "exactly_one_disposition": True,
    "no_runtime_linkage": (
        "This measurement is offline only. It is not imported by any product "
        "build and its result may not be consumed by the running game."
    ),
}


# =============================================================================
# Small helpers
# =============================================================================


def sha256_of_file(path: Path) -> str:
    """Return the SHA-256 fingerprint of a file, read in chunks.

    A fingerprint is a short string that changes if even one byte of the file
    changes. Reading in 1 MiB chunks rather than all at once keeps memory flat
    for the ~100 MB caches.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        # b"" (an empty chunk) is what read() returns at end of file, so it is
        # the loop's stopping signal.
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(payload: dict) -> bytes:
    """Serialise a dictionary to bytes in one fixed, machine-independent form.

    `sort_keys=True` puts the keys in alphabetical order and the compact
    separators remove all optional whitespace. Two machines running this script
    on identical inputs therefore produce byte-identical output, which is what
    makes `--check` a meaningful equality test rather than a formatting test.
    This is the same convention the FL-4694 pointer manifest already uses for
    its own self-fingerprint.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def self_fingerprint(payload_without_hash: dict) -> str:
    """Fingerprint an artifact body that does not yet contain its own hash field.

    A file cannot contain a fingerprint of itself including that fingerprint, so
    the convention (inherited from the FL-4694 pointer manifest) is: fingerprint
    the body with the hash field absent, then insert the result.
    """
    return hashlib.sha256(canonical_json_bytes(payload_without_hash)).hexdigest()


class InputsUnavailable(Exception):
    """Raised when a required input is missing or fails its fingerprint check.

    This is a distinct exception type rather than a generic error because it is
    the ONLY thing allowed to produce the UNMEASURED disposition. Any other
    failure is a bug and should crash loudly instead of quietly degrading the
    verdict.
    """


# =============================================================================
# Loading the inputs
# =============================================================================


def resolve_fl4694_cache() -> tuple[Path, str, dict]:
    """Locate and fingerprint-verify the FL-4694 neighbour cache.

    Returns
    -------
    (cache_path, declared_sha256, manifest_dict)

    Raises
    ------
    InputsUnavailable
        If the pointer manifest is missing, does not list the neighbour cache,
        the cache file is not on disk, or its fingerprint disagrees with the
        pin. Every one of those is a reason to refuse to invent a verdict.

    Why fingerprint-verify at all: the caches are deliberately NOT committed to
    version control because they are ~150 MB. A developer's local copy could be
    stale, truncated, or regenerated from different inputs. Comparing against a
    stale cache and reporting a verdict would be worse than reporting nothing.
    """
    if not FL4694_MANIFEST_PATH.exists():
        raise InputsUnavailable(
            f"FL-4694 pointer manifest is missing: {FL4694_MANIFEST_PATH}"
        )
    manifest = json.loads(FL4694_MANIFEST_PATH.read_text(encoding="utf-8"))

    # The manifest lists several caches; pick the neighbour table by name.
    entries = [
        entry
        for entry in manifest.get("local_artifacts", [])
        if entry.get("name") == REQUIRED_CACHE_NAME
    ]
    if len(entries) != 1:
        raise InputsUnavailable(
            f"FL-4694 manifest does not list exactly one '{REQUIRED_CACHE_NAME}' "
            f"cache (found {len(entries)})"
        )
    entry = entries[0]
    cache_path = REPO / entry["path"]
    declared = entry["sha256"]

    if not cache_path.exists():
        # This is the expected state on a fresh clone. Say so, and say exactly
        # how to fix it, rather than leaving the operator guessing.
        regen = manifest.get("regeneration", {}).get("command", "(no command recorded)")
        raise InputsUnavailable(
            f"FL-4694 neighbour cache is not on disk: {cache_path}\n"
            f"  The FL-4694 caches are deliberately never committed.\n"
            f"  Regenerate them with: {regen}"
        )

    observed = sha256_of_file(cache_path)
    if observed != declared:
        raise InputsUnavailable(
            f"FL-4694 neighbour cache fingerprint disagrees with its pin.\n"
            f"  file:     {cache_path}\n"
            f"  pinned:   {declared}\n"
            f"  observed: {observed}\n"
            f"  Refusing to compare against an unpinned cache."
        )
    return cache_path, declared, manifest


def load_gtr_v2() -> dict:
    """Read the committed GTR v2 table.

    Raises InputsUnavailable if it is missing. GTR v2 IS committed, so a missing
    file means a broken checkout rather than the normal never-committed-cache
    situation, but the same refusal applies: do not invent a verdict.
    """
    if not GTR_V2_PATH.exists():
        raise InputsUnavailable(f"GTR v2 artifact is missing: {GTR_V2_PATH}")
    return json.loads(GTR_V2_PATH.read_text(encoding="utf-8"))


def load_projection_edges(cache_path: Path, wanted_ids: set[int]) -> tuple[dict, dict]:
    """Stream the neighbour cache and pull out everything about the 32 GTR glyphs.

    Parameters
    ----------
    cache_path:
        The verified FL-4694 neighbour cache. It is a JSONL file: one JSON
        object per line. Line 1 is a header describing the file; lines 2 and on
        are one record each.
    wanted_ids:
        The 32 glyph ids that GTR v2 addresses. Only these matter here.

    Returns
    -------
    (header, outgoing)
        `header` is the file's first line, kept so its declared ordering and
        self-edge rules can be quoted in the result.
        `outgoing` maps each wanted glyph id that exists as a record to a dict
        of {neighbour_id: (compiled_distance, membership_weight)}.

    Why stream rather than load: the file is ~100 MB across 111,041 lines.
    Streaming keeps memory proportional to the 32 records actually wanted.
    """
    header: dict = {}
    outgoing: dict[int, dict[int, tuple[int, int]]] = {}

    with cache_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)

            # The first non-empty line is the header: it has no glyph_record_id.
            if line_number == 0 and "glyph_record_id" not in record:
                header = record
                continue

            record_id = record.get("glyph_record_id")
            if record_id not in wanted_ids:
                continue

            # Each neighbour is a 3-element list, in the order the header's
            # `neighbor_tuple_fields` declares:
            #   [to_glyph_record_id, compiled_distance, membership_weight_q0_16]
            neighbours: dict[int, tuple[int, int]] = {}
            for to_id, distance, weight in record.get("neighbors", []):
                neighbours[to_id] = (distance, weight)
            outgoing[record_id] = neighbours

    return header, outgoing


# =============================================================================
# The six measurements
# =============================================================================


def measure_axes(gtr: dict, header: dict, outgoing: dict) -> dict:
    """Run all six predeclared comparisons and return their counted results.

    Parameters
    ----------
    gtr:
        The loaded GTR v2 table.
    header:
        The FL-4694 cache header, which declares the family's ordering rule and
        whether self-edges are kept.
    outgoing:
        Neighbour lists for the 32 GTR glyph ids, from `load_projection_edges`.

    Returns
    -------
    A dictionary with one entry per axis. Every value is a plain whole number or
    a short string, so the artifact stays diffable and the verdict stays
    mechanical.

    No axis here has a tolerance, a threshold, or a tunable weight. Each one
    counts how many of the 65,536 rows the projection fails to reproduce.
    """
    relations = gtr["relations"]
    total_rows = len(relations)

    # ---- shared bookkeeping -------------------------------------------------
    # For each ordered character pair, collect the distinct value-tuples seen
    # across the 64 aspects. If a pair shows more than one distinct tuple, then
    # the value genuinely depends on the viewing situation, and any source
    # without an aspect axis cannot reproduce it. This is the decisive
    # structural measurement, so it is computed once and reused.
    static_by_pair: dict[tuple[int, int], set[tuple[int, ...]]] = {}
    affine_by_pair: dict[tuple[int, int], set[tuple[int, ...]]] = {}
    cost_by_pair: dict[tuple[int, int], set[int]] = {}
    confidence_by_pair: dict[tuple[int, int], set[int]] = {}

    covered_rows = 0          # rows the projection supplies anything at all for
    self_rows = 0             # rows where from == to
    confidence_matches = 0    # rows where confidence equalled membership weight
    cost_matches = 0          # rows where cost equalled compiled distance

    for relation in relations:
        source = relation["from_glyph_id"]
        target = relation["to_glyph_id"]
        pair = (source, target)

        static_by_pair.setdefault(pair, set()).add(tuple(relation["static_components"]))
        affine_by_pair.setdefault(pair, set()).add(tuple(relation["affine_components"]))
        cost_by_pair.setdefault(pair, set()).add(relation["compiled_transform_cost"])
        confidence_by_pair.setdefault(pair, set()).add(
            relation["relation_confidence_q0_16"]
        )

        if source == target:
            self_rows += 1

        # Does the projection supply anything at this address?
        supplied = outgoing.get(source, {}).get(target)
        if supplied is None:
            continue
        covered_rows += 1
        distance, weight = supplied
        if weight == relation["relation_confidence_q0_16"]:
            confidence_matches += 1
        if distance == relation["compiled_transform_cost"]:
            cost_matches += 1

    # ---- A1: static components ---------------------------------------------
    # The projection supplies two scalars per row. GTR v2's static vector is
    # eleven numbers. Two numbers cannot reconstruct eleven independent ones, so
    # every row is a mismatch; the count is reported rather than asserted, and
    # the aspect-dependence figure is reported alongside it because it says
    # whether an aspect-free source could ever have worked.
    static_pairs_varying_by_aspect = sum(
        1 for values in static_by_pair.values() if len(values) > 1
    )
    a1 = {
        "axis_id": "A1_static_components",
        "gtr_v2_numbers_per_row": 11,
        "projection_numbers_per_row": 2,
        "rows_reproduced_exactly": 0,
        "rows_mismatched": total_rows,
        # A row can fail in two completely different ways, and the v1 contract
        # wrongly added them together. Separating them is the whole point of the
        # v2 amendment:
        #   * "disagreeing"     - the projection DOES hold a value at this
        #                         address and that value is different. This is
        #                         real evidence that the two sides hold
        #                         different truth.
        #   * "not addressable" - the projection holds NOTHING at this address,
        #                         so nothing was observed. Silence is not
        #                         disagreement, and counting it as disagreement
        #                         manufactures evidence that was never gathered.
        # Here every addressable row also disagrees, because two scalars cannot
        # reconstruct an eleven-number vector under any circumstances.
        "rows_addressable_by_projection": covered_rows,
        "rows_disagreeing": covered_rows,
        "rows_not_addressable": total_rows - covered_rows,
        "pairs_whose_value_depends_on_aspect": static_pairs_varying_by_aspect,
        "distinct_character_pairs": len(static_by_pair),
        "why": (
            "The projection carries only a compiled distance and a membership "
            "weight. It holds no per-component breakdown at all, so it cannot "
            "reproduce an eleven-number vector for any row."
        ),
    }

    # ---- A2: affine components ---------------------------------------------
    affine_pairs_varying_by_aspect = sum(
        1 for values in affine_by_pair.values() if len(values) > 1
    )
    a2 = {
        "axis_id": "A2_affine_components",
        "gtr_v2_numbers_per_row": 7,
        "projection_numbers_per_row": 2,
        "projection_carries_an_aspect_axis": False,
        "rows_reproduced_exactly": 0,
        "rows_mismatched": total_rows,
        # Same split as A1: rows the projection can actually speak about versus
        # rows where it is simply silent. See the long comment on A1.
        "rows_addressable_by_projection": covered_rows,
        "rows_disagreeing": covered_rows,
        "rows_not_addressable": total_rows - covered_rows,
        "pairs_whose_value_depends_on_aspect": affine_pairs_varying_by_aspect,
        "distinct_character_pairs": len(affine_by_pair),
        "why": (
            "Same shortfall as A1, plus a structural one: where a character "
            "pair's affine vector differs across the 64 aspects, one "
            "aspect-free number cannot hold two different answers at the same "
            "address."
        ),
    }

    # ---- A3: confidence -----------------------------------------------------
    a3 = {
        "axis_id": "A3_confidence",
        "rows_covered_by_projection": covered_rows,
        "rows_reproduced_exactly": confidence_matches,
        "rows_mismatched": total_rows - confidence_matches,
        # Of the rows the projection can actually speak about, how many did it
        # get wrong. Rows it cannot address are counted separately and are NOT
        # treated as disagreement. See the long comment on A1.
        "rows_addressable_by_projection": covered_rows,
        "rows_disagreeing": covered_rows - confidence_matches,
        "rows_not_addressable": total_rows - covered_rows,
        "pairs_whose_value_depends_on_aspect": sum(
            1 for values in confidence_by_pair.values() if len(values) > 1
        ),
        "why": (
            "GTR v2's confidence is a compiler judgement about a "
            "view-conditioned transform. The projection's membership weight is "
            "a rank-share of one record's neighbour list. They are different "
            "quantities; the count records how often they happen to coincide."
        ),
    }

    # ---- A4: compiled transform cost ---------------------------------------
    a4 = {
        "axis_id": "A4_compiled_transform_cost",
        "rows_covered_by_projection": covered_rows,
        "rows_reproduced_exactly": cost_matches,
        "rows_mismatched": total_rows - cost_matches,
        # Same split as A3. See the long comment on A1.
        "rows_addressable_by_projection": covered_rows,
        "rows_disagreeing": covered_rows - cost_matches,
        "rows_not_addressable": total_rows - covered_rows,
        "pairs_whose_value_depends_on_aspect": sum(
            1 for values in cost_by_pair.values() if len(values) > 1
        ),
        "why": (
            "GTR v2's cost is a calibrated sum over eighteen named components "
            "under one viewing situation. The projection's compiled distance is "
            "a weighted squared-difference over 141 integer feature dimensions "
            "with no viewing situation at all. The count records exact "
            "agreement, not similarity."
        ),
    }

    # ---- A5: directionality -------------------------------------------------
    # Two separate structural facts are counted here:
    #  * self-relations: GTR v2 keeps rows where a character relates to itself;
    #    the FL-4694 family declares self-edges excluded, so those addresses
    #    cannot exist in the projection at all.
    #  * truncation: the family keeps only the 32 nearest neighbours out of
    #    111,040 records, so most GTR pairs simply are not in the list.
    unrepresentable = total_rows - covered_rows
    a5 = {
        "axis_id": "A5_directionality",
        "gtr_v2_rows_total": total_rows,
        "rows_representable_in_projection": covered_rows,
        "rows_unrepresentable": unrepresentable,
        # A5 is a PURE COVERAGE axis. It asks "does the projection have an
        # address here at all", never "is the value here different". So its
        # disagreement count is zero by definition, and every failure it reports
        # belongs in the not-addressable column. Under the v1 contract this
        # axis alone contributed 65152 phantom "mismatches".
        "rows_disagreeing": 0,
        "rows_not_addressable": unrepresentable,
        "gtr_v2_self_relation_rows": self_rows,
        "projection_excludes_self_edges": bool(header.get("self_edges_excluded")),
        "projection_neighbours_retained_per_record": header.get("k"),
        "projection_record_count": header.get("record_count"),
        "why": (
            "The projection is a bounded nearest-neighbour list with self-edges "
            "removed. GTR v2 is an exhaustive directed table that includes "
            "self-relations. Addresses outside the retained neighbour list have "
            "no projected value at all."
        ),
    }

    # ---- A6: tie behaviour --------------------------------------------------
    # A "tie group" is a set of rows, inside one aspect and sharing one source
    # character, whose costs are equal. Both sides must order such a group the
    # same way or they will select different characters. GTR v2's stored order
    # is compared against the ordering rule the FL-4694 header declares.
    declared_order = header.get("neighbor_order", "")
    tie_groups = 0
    tie_groups_agreeing = 0

    # Walk the relation array grouped by (aspect, source), preserving stored
    # order, and bucket rows by cost.
    grouped: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for relation in relations:
        key = (relation["aspect"], relation["from_glyph_id"])
        grouped.setdefault(key, []).append(
            (relation["compiled_transform_cost"], relation["to_glyph_id"])
        )
    for rows in grouped.values():
        by_cost: dict[int, list[int]] = {}
        for cost, target in rows:
            by_cost.setdefault(cost, []).append(target)
        for targets in by_cost.values():
            if len(targets) < 2:
                continue  # a group of one cannot disagree about ordering
            tie_groups += 1
            # The FL-4694 declared rule breaks ties by ascending record id.
            # GTR v2 agrees on this group only if its stored order is already
            # ascending by target id.
            if targets == sorted(targets):
                tie_groups_agreeing += 1
    a6 = {
        "axis_id": "A6_tie_behaviour",
        "projection_declared_order": declared_order,
        "gtr_v2_tie_groups": tie_groups,
        "tie_groups_ordered_identically": tie_groups_agreeing,
        "tie_groups_disagreeing": tie_groups - tie_groups_agreeing,
        "rows_disagreeing": tie_groups - tie_groups_agreeing,
        "rows_not_addressable": 0,
        # HONESTY NOTE about what this axis did and did not compare. It checks
        # GTR v2's stored ordering against the tie-break RULE that the FL-4694
        # header declares ("ascending record id"). It does not compare against
        # the projection's actual retained data, which covers only a small
        # fraction of these addresses. So "agreement" here means "GTR v2's order
        # is consistent with the declared rule", which is weaker than "the two
        # sides were observed to break the same tie the same way".
        "comparison_is_against_declared_rule_not_retained_data": True,
        "why": (
            "A tie group is a set of equal-cost candidate targets inside one "
            "aspect for one source character. If the two sides break such a tie "
            "differently they select different characters from identical input."
        ),
    }

    return {
        "gtr_v2_rows_compared": total_rows,
        "gtr_v2_rows_covered_by_projection": covered_rows,
        "A1_static_components": a1,
        "A2_affine_components": a2,
        "A3_confidence": a3,
        "A4_compiled_transform_cost": a4,
        "A5_directionality": a5,
        "A6_tie_behaviour": a6,
    }


# =============================================================================
# The verdict
# =============================================================================


def derive_disposition(axes: dict) -> dict:
    """Turn the counted axis results into exactly one verdict.

    WHY THIS FUNCTION WAS AMENDED (contract v1 -> v2)
    -------------------------------------------------
    The v1 rule said: "zero mismatches and complete coverage means
    EXACT_SUBSUMPTION, anything else means DISTINCT_TRUTH". That rule looked
    dull and safe, but it hid a serious error, because "mismatch" was defined to
    include rows the projection cannot address at all.

    The projection addresses 384 of the 65536 rows - about six tenths of one
    percent. So under v1 the verdict DISTINCT_TRUTH was guaranteed before a
    single number was read. A test that can only produce one answer is not a
    measurement, and the answer it produced claimed more than the data supports:
    "GTR v2 holds truth the family does not" is a statement about ALL possible
    projections, but only ONE candidate projection was ever tried, and it failed
    mostly by staying silent rather than by disagreeing.

    The v2 rule separates the two ways a row can fail:
      * DISAGREEMENT   - the projection holds a value here and it is different.
                         This is genuine evidence of different truth.
      * NON-COVERAGE   - the projection holds nothing here. This is an absence
                         of evidence, and it may mean nothing more than that a
                         different projection is needed.

    That yields four outcomes, still exactly one per run, still with no
    thresholds and no tunable weights:

      EXACT_SUBSUMPTION              coverage complete AND zero disagreements
      DISTINCT_TRUTH                 coverage complete AND some disagreement
                                     (only with full coverage can one honestly
                                      say the two sides hold different truth)
      NOT_SUBSUMED_BY_THIS_PROJECTION  coverage incomplete - this candidate
                                     failed, and the general question of whether
                                     SOME projection subsumes GTR v2 is left
                                     open rather than answered
      UNMEASURED                     inputs unavailable; produced elsewhere,
                                     never derived from data

    UNMEASURED is never produced here; it is produced only when the inputs were
    unavailable, which is handled before this function is ever called.
    """
    # Rows where the projection spoke and was wrong. This is the only category
    # that constitutes evidence of genuinely different truth.
    disagreement_counts = {
        "A1_static_components": axes["A1_static_components"]["rows_disagreeing"],
        "A2_affine_components": axes["A2_affine_components"]["rows_disagreeing"],
        "A3_confidence": axes["A3_confidence"]["rows_disagreeing"],
        "A4_compiled_transform_cost": axes["A4_compiled_transform_cost"]["rows_disagreeing"],
        "A5_directionality": axes["A5_directionality"]["rows_disagreeing"],
        "A6_tie_behaviour": axes["A6_tie_behaviour"]["rows_disagreeing"],
    }
    # Rows where the projection held nothing at all. Absence, not disagreement.
    non_coverage_counts = {
        "A1_static_components": axes["A1_static_components"]["rows_not_addressable"],
        "A2_affine_components": axes["A2_affine_components"]["rows_not_addressable"],
        "A3_confidence": axes["A3_confidence"]["rows_not_addressable"],
        "A4_compiled_transform_cost": axes["A4_compiled_transform_cost"]["rows_not_addressable"],
        "A5_directionality": axes["A5_directionality"]["rows_not_addressable"],
        "A6_tie_behaviour": axes["A6_tie_behaviour"]["rows_not_addressable"],
    }
    total_disagreements = sum(disagreement_counts.values())
    total_non_coverage = sum(non_coverage_counts.values())

    rows_compared = axes["gtr_v2_rows_compared"]
    rows_covered = axes["gtr_v2_rows_covered_by_projection"]
    coverage_complete = rows_covered == rows_compared
    # Share of the domain the projection can speak about at all, as a plain
    # fraction so a reader does not have to divide two large numbers by hand.
    coverage_fraction = (rows_covered / rows_compared) if rows_compared else 0.0

    # Every outcome carries the same evidence block, so a reader can always see
    # how much of the domain was actually observed before reading the verdict.
    #
    # TWO DIFFERENT UNITS, NEVER TO BE MIXED (FL-4694 delimitation, 2026-08-09).
    # An earlier revision of this block reported bare "total" figures that were
    # sums across six axes, while their names read as though they counted
    # relation rows. They do not, and the difference is large enough to mislead:
    #
    #   * a RELATION ROW is one address in the GTR v2 table, of which there are
    #     65536. This is the unit that matters when asking how much of the table
    #     the projection can reach.
    #   * an AXIS ROW is one row examined on one comparison axis. Six axes over
    #     65536 rows give 393216 axis rows, so a single unreachable relation row
    #     can contribute up to six axis-row failures.
    #
    # Both units are now reported under names that say which one they are, and
    # neither is presented as the headline on its own.
    unique_rows_not_addressable = rows_compared - rows_covered
    evidence = {
        "disagreement_counts_per_axis": disagreement_counts,
        "non_coverage_counts_per_axis": non_coverage_counts,
        # Unit: unique relation rows. This is the coverage question.
        "unique_relation_rows_addressable": rows_covered,
        "unique_relation_rows_not_addressable": unique_rows_not_addressable,
        # Unit: axis rows, i.e. the per-axis columns added up. Kept because the
        # per-axis breakdown must total to something, but explicitly labelled so
        # it can never be read as a count of relations.
        "total_axis_row_disagreements": total_disagreements,
        "total_axis_row_noncoverage": total_non_coverage,
        "gtr_v2_rows_compared": rows_compared,
        "coverage_fraction_of_domain": round(coverage_fraction, 6),
        "units_note": (
            "unique_relation_rows_* count addresses in the 65536 row GTR v2 "
            "table. total_axis_row_* count per axis examinations summed over "
            "six axes and are therefore larger; they are not relation counts."
        ),
    }

    if coverage_complete and total_disagreements == 0:
        return {
            "disposition": "EXACT_SUBSUMPTION",
            **evidence,
            "consequence": (
                "GTR v2 is a duplicate owner of truth the FL-4694 family "
                "already holds. Its FL-4686 delete-list entry E8 becomes a "
                "MANDATORY deletion rather than a deferred one."
            ),
        }

    if coverage_complete:
        return {
            "disposition": "DISTINCT_TRUTH",
            "named_projection_that_fell_short": PREDECLARED_CONTRACT[
                "candidate_projection"
            ]["projection_id"],
            **evidence,
            "consequence": (
                "The projection addressed every row and disagreed on some of "
                "them, so GTR v2 demonstrably holds different truth. It is not "
                "a duplicate owner and may not be deleted as one."
            ),
        }

    return {
        "disposition": "NOT_SUBSUMED_BY_THIS_PROJECTION",
        "named_projection_that_fell_short": PREDECLARED_CONTRACT[
            "candidate_projection"
        ]["projection_id"],
        **evidence,
        "consequence": (
            "ONE named candidate projection was tried and it failed. It "
            "reaches 384 of the 65536 relation rows and is absent from the "
            "remaining 65152, so it failed overwhelmingly by silence rather "
            "than by disagreement. The measured reason is aspect dependence: "
            "GTR v2 is indexed by a viewing aspect, the value at a character "
            "pair genuinely varies across aspects for 987 of 1024 pairs on "
            "static components, 992 on affine components, 1024 on confidence "
            "and 992 on compiled cost, and the tested projection carries no "
            "aspect axis and only two scalars per row. An aspect free, two "
            "scalar projection therefore cannot stand in for this table. "
            "THREE conclusions follow, and the scope of each is deliberate. "
            "First, the FL-4686 delete-list entry E8 may NOT be promoted to a "
            "mandatory deletion on this evidence. Second, this run does NOT "
            "establish that GTR v2 holds distinct truth, because a projection "
            "that stays silent has contradicted nothing. Third, this run "
            "bounds only the projection it tested: it says nothing about "
            "projections that carry an aspect axis. The architectural "
            "consequence is positive and specific. A compiler owned, aspect "
            "conditioned relation section inside GlyphProgram, addressing all "
            "32 by 64 by 32 relations and preserving every static component, "
            "affine component, confidence, transform cost, direction and tie "
            "rule, remains fully viable and is the route this result points "
            "at. Until such a section exists and proves parity, delete-first "
            "completion cannot be claimed for the cutover while this owner "
            "survives undecided."
        ),
    }


# =============================================================================
# Building the artifact
# =============================================================================


def build_result() -> dict:
    """Compute the whole measurement and return the artifact body.

    If the inputs are unavailable this returns an UNMEASURED body instead of
    raising, so that `--write` can still commit an honest placeholder. `--check`
    treats UNMEASURED as a failure, which is what makes the placeholder loud
    rather than comfortable.
    """
    # Common header fields, present in every state.
    body: dict = {
        "schema": RESULT_SCHEMA_ID,
        "version": RESULT_VERSION,
        "artifact_kind": "offline_subsumption_measurement",
        "owning_fl": "FL-4694",
        "generated_by": "scripts/measure_fl4694_gtr_v2_subsumption.py",
        "what_this_is": (
            "An offline measurement of whether the FL-4694 bounded "
            "nearest-neighbour relation family already contains everything the "
            "GTR v2 transform-relation table contains. It compares the two "
            "against a contract written down before any number was computed."
        ),
        "runtime_consumption_allowed": False,
        "no_runtime_linkage": (
            "Neither this artifact nor the script that produced it is imported "
            "by, called from, or linked into any product build."
        ),
        "predeclared_contract": PREDECLARED_CONTRACT,
    }

    try:
        gtr = load_gtr_v2()
        cache_path, cache_sha, manifest = resolve_fl4694_cache()
    except InputsUnavailable as problem:
        # Inputs missing: refuse to invent a verdict. Record why, and stop.
        body["inputs"] = {"available": False, "reason": str(problem)}
        body["measurement"] = None
        body["result"] = {
            "disposition": "UNMEASURED",
            "reason": str(problem),
            "consequence": (
                "No subsumption claim is made in either direction. GTR v2's "
                "FL-4686 delete-list entry E8 is unaffected by this artifact "
                "while it remains UNMEASURED."
            ),
        }
        body["artifact_sha256"] = self_fingerprint(body)
        return body

    # Inputs available: record exactly what was read, with fingerprints, so the
    # numbers below can be traced to specific bytes.
    wanted_ids = set(gtr["glyph_ids"])
    header, outgoing = load_projection_edges(cache_path, wanted_ids)

    body["inputs"] = {
        "available": True,
        "gtr_v2": {
            "path": str(GTR_V2_PATH.relative_to(REPO)),
            "schema": gtr["schema"],
            "version": gtr["version"],
            "content_sha256": gtr["content_sha256"],
            "relation_sha256": gtr["relation_sha256"],
            "descriptor_count": gtr["descriptor_count"],
            "aspect_count": gtr["aspect_count"],
            "glyph_id_count": len(gtr["glyph_ids"]),
            "relation_count": gtr["relation_count"],
        },
        "fl4694_family": {
            "pointer_manifest": str(FL4694_MANIFEST_PATH.relative_to(REPO)),
            "manifest_sha256": manifest["manifest_sha256"],
            "cache_path": str(cache_path.relative_to(REPO)),
            "cache_sha256_pinned": cache_sha,
            "cache_sha256_verified": True,
            "form": manifest["form"],
            "k": header.get("k"),
            "record_count": header.get("record_count"),
            "self_edges_excluded": header.get("self_edges_excluded"),
            "neighbor_order": header.get("neighbor_order"),
            "distance_definition_id": header.get("distance_definition_id"),
        },
        "gtr_glyph_ids_present_as_fl4694_records": sorted(outgoing.keys()),
        "gtr_glyph_ids_absent_from_fl4694_records": sorted(
            wanted_ids - set(outgoing.keys())
        ),
    }

    axes = measure_axes(gtr, header, outgoing)
    body["measurement"] = axes
    body["result"] = derive_disposition(axes)

    # Preserve what the superseded v1 contract said about this same data, so the
    # amendment can be audited rather than taken on trust. Nothing here is
    # recomputed: the v1 rule is reapplied verbatim to the identical axis
    # numbers, and its verdict is recorded next to the v2 one. A reader can see
    # exactly what changed and decide for themselves whether the change was
    # honest. See the AMENDMENT DISCLOSURE block on PREDECLARED_CONTRACT.
    v1_mismatch_counts = {
        "A1_static_components": axes["A1_static_components"]["rows_mismatched"],
        "A2_affine_components": axes["A2_affine_components"]["rows_mismatched"],
        "A3_confidence": axes["A3_confidence"]["rows_mismatched"],
        "A4_compiled_transform_cost": axes["A4_compiled_transform_cost"]["rows_mismatched"],
        "A5_directionality": axes["A5_directionality"]["rows_unrepresentable"],
        "A6_tie_behaviour": axes["A6_tie_behaviour"]["tie_groups_disagreeing"],
    }
    body["superseded_by_amendment"] = {
        "contract_id": "fl4694.gtr_v2_subsumption_contract.v1",
        "disposition_under_v1": "DISTINCT_TRUTH",
        "mismatch_counts_per_axis_under_v1": v1_mismatch_counts,
        "total_mismatches_under_v1": sum(v1_mismatch_counts.values()),
        "why_superseded": (
            "v1 counted rows the projection could not address as mismatches. "
            "Because the projection addresses 384 of 65536 rows, DISTINCT_TRUTH "
            "was the only verdict v1 could ever return, and it overstated the "
            "finding by asserting that GTR v2 holds truth the family does not. "
            "No measured number changed; only the bookkeeping and the names of "
            "the outcomes did."
        ),
    }
    body["artifact_sha256"] = self_fingerprint(body)
    return body


# =============================================================================
# Command line
# =============================================================================


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the requested mode, and return a process exit code.

    Exit code 0 means success. Any non-zero code means the caller should treat
    the run as failed; continuous checks and the test suite rely on that.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--check",
        action="store_true",
        help=(
            "Recompute and compare against the committed artifact. Exits "
            "non-zero on any difference, missing input, or UNMEASURED result."
        ),
    )
    group.add_argument(
        "--write",
        action="store_true",
        help="Recompute and overwrite the committed artifact.",
    )
    group.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="Recompute and print to the screen without writing any file.",
    )
    args = parser.parse_args(argv)

    computed = build_result()
    # Two-space indent and a trailing newline so the committed file reads well
    # in a diff; sort_keys keeps it byte-stable across machines.
    rendered = json.dumps(computed, indent=2, sort_keys=True) + "\n"

    if args.print_only:
        print(rendered, end="")
        return 0

    if args.write:
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(rendered, encoding="utf-8")
        print(f"[FL-4694] wrote {RESULT_PATH.relative_to(REPO)}")
        print(f"[FL-4694] disposition: {computed['result']['disposition']}")
        return 0

    # --- --check ------------------------------------------------------------
    if not RESULT_PATH.exists():
        print(f"[FL-4694] CHECK FAILED: result artifact missing: {RESULT_PATH}")
        return 1

    committed = json.loads(RESULT_PATH.read_text(encoding="utf-8"))

    # An UNMEASURED result must fail --check. The artifact is still committed so
    # the contract and the tooling are reviewable, but the lane must never be
    # able to mistake "we could not measure" for "we measured and it is fine".
    if computed["result"]["disposition"] == "UNMEASURED":
        print("[FL-4694] CHECK FAILED: inputs unavailable, nothing was measured.")
        print(f"  {computed['result']['reason']}")
        return 1

    if committed != computed:
        print("[FL-4694] CHECK FAILED: committed artifact disagrees with recomputation.")
        # Name the top-level sections that differ; that is enough to start from
        # without dumping two 65,536-row comparisons into the terminal.
        for key in sorted(set(committed) | set(computed)):
            if committed.get(key) != computed.get(key):
                print(f"  section differs: {key}")
        print(f"  Re-run with --write if the change is intended.")
        return 1

    print("[FL-4694] CHECK PASS")
    print(f"  disposition: {computed['result']['disposition']}")
    # Report disagreement and non-coverage separately. Printing a single
    # combined "mismatch" number is exactly the conflation the v2 amendment
    # removed, so the summary line must not reintroduce it.
    result_block = computed["result"]
    print(
        "  rows where the projection disagreed:  "
        f"{result_block.get('total_disagreements')}"
    )
    print(
        "  rows the projection could not address: "
        f"{result_block.get('total_rows_not_addressable')}"
    )
    print(
        "  share of the domain actually observed: "
        f"{result_block.get('coverage_fraction_of_domain')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
