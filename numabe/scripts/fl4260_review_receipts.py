#!/usr/bin/env python3
"""FL-4260 RQ-153c primary-winner review receipt apparatus (single owner).

The RQ-153c mechanical coverage gate (``scripts/fl4260_rq153c_coverage_gate.py``)
proves the FULL referenced-GID set is rendered, measured, and atlas-covered. It
deliberately stops short of closure: each COMMON profile cell elects a *primary
winner* glyph, and a human must accept/reject that taste call. There are 544
common cells electing 72 DISTINCT primary winners.

The wrong frame is "ask the user to go do a vague manual pass". The correct
executor deliverable is an OWNED review surface: every primary decision is
presented with full context, every accept/reject/defer action has a defined
backend effect, receipts are written with a rich auditable schema, and
review-ready status fails shut until the receipt set is settled. A human still
makes the taste call; this apparatus makes that call actionable, traceable, and
mechanically consumable. It does NOT fabricate acceptance.

The count is DERIVED, never asserted. The June-2026 artifacts happened to elect
72 distinct primaries across 544 common cells; a regenerated catalog may elect a
different set, and the gates compare sets rather than counts so that a coverage
change is reported as the rows that appeared or vanished (FL-4512).

This module is the SINGLE OWNER of:
  * the derivation of the review receipt rows (``build_review_receipt_set``)
  * the per-row state machine over manual review receipts (``review_state``)
  * the accept / reject / defer receipt writer (rich schema v3)
  * the four RQ-153c review ``evidence_*`` gates (``review_evidence_gates``)

Both ``fl4260_rq153c_coverage_gate.py`` (operator residual observation) and
``fl4260_material_morphology_review.py`` (review status refresh) import the
derivation from here so "which primaries are required, and which are accepted"
has one owner (Law 1).

Subcommands:
  list [--json] [--state STATE]   show the receipt set (optionally filtered)
  show ROW_ID [--json]            full detail for one row incl. rendered glyph
  next [--json]                   next row with no terminal (accept/reject) receipt
  accept ROW_ID --reason ...      write an accept receipt (does NOT change runtime)
  reject ROW_ID --reason ...      write a reject receipt (blocks review-ready status)
  defer  ROW_ID --reason ...      write a defer receipt (blocks review-ready status)
  gate [--json]                   the four RQ-153c review evidence_* gates

Receipts are appended to
``assets/glyphs/generated/material.morphology.v2.manual_review_receipts.jsonl``
(the same file the coverage gate observes and the review-refresh script consumes).

A review ROW_ID is ``P-<glyph_id>``: the primary winner glyph, which may win
several profile/bucket contexts (all enumerated in the row). Accepting the row
accepts that primary winner across every listed context; rejecting/deferring
blocks review-ready status until settled.

REVIEW-SUBJECT IDENTITY (schema v3, FL-4260 / FL-4512)
------------------------------------------------------
A receipt is NOT keyed by glyph id alone. It is keyed by the pair

    (row_id, review_subject_sha256)

where the second half fingerprints everything the reviewer actually judged: the
winning glyph, its drawn pixels, every cell it won, the full ordered rival list
in each cell, the role and tie rule, and the shape measurements of the winner
and of every rival. ``scripts/fl4260_review_subject.py`` owns that computation.

This supersedes the v2 rule, which compared two GLOBAL artifact hashes and so
could only ask "did anything anywhere change?". Under v2 a single unrelated edit
invalidated every acceptance at once — on 2026-07-22 all 72 died together, none
for a reason connected to what was reviewed (FL-4512). Subject keying makes the
intended rule expressible for the first time: KEEP decisions whose subject is
unchanged, RE-REVIEW only decisions whose subject really moved.

Exit codes: 0 success / gate PASS; 2 gate FAIL; 3 input missing; 4 bad row/args.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# The review-subject computation lives in a sibling script so the migration tool
# can apply the identical recipe to an OLD artifact snapshot and a NEW one.
# Inserting the scripts directory explicitly (rather than relying on it being
# sys.path[0]) lets a test load this module by file path (FL-4260).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fl4260_review_subject as SUBJECT  # noqa: E402
import fl4482_native_admission as ADMISSION  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
GEN = REPO_ROOT / "assets/glyphs/generated"
PROFILE_TABLES = GEN / "material.morphology.v2.profile_tables.json"
V2_ATLAS = REPO_ROOT / "assets/glyphs/atlases/material.morphology.v2.atlas_of_atlases.json"
V2_SHAPE_CATALOG = GEN / "material.morphology.v2.shape_catalog.json"
SHAPE_RECEIPTS = GEN / "material.morphology.v2.shape_receipts.jsonl"
MANUAL_RECEIPTS = GEN / "material.morphology.v2.manual_review_receipts.jsonl"

# Receipt schema identity. v3 keys decisions by (row_id, review_subject_sha256);
# v2 keyed them by glyph id and validated two global artifact hashes. The retired
# identity is kept named so the migration tool and its tests can recognise an old
# receipt on sight rather than guessing from which fields happen to be present.
RECEIPT_SCHEMA = "fl4260_rq153c_review_receipt.v3"
RECEIPT_SCHEMA_V2_RETIRED = "fl4260_rq153c_review_receipt.v2"

TOOL_VERSION = "fl4260_review_receipts/2.0"
RUNTIME_RESOLVER = "engine/fl4131_runtime_harri_resolver.cpp"
TERMINAL_ACTIONS = {"accept", "reject", "defer"}

# The five states a queue row can hold under subject keying.
#
#   unreviewed       nobody has ever judged this subject
#   accepted         a current accept receipt matches this exact subject
#   rejected         a current reject receipt matches this exact subject
#   deferred         a current defer receipt matches this exact subject
#   subject_changed  a decision exists for this ROW, but it was made about a
#                    DIFFERENT subject — the rivals, measurements, contexts, or
#                    the winner's own pixels have moved since. Fails closed and
#                    goes back in the queue.
#
# `subject_changed` replaces v2's `stale_hash_mismatch`. The rename is not
# cosmetic: the old name described a hash comparison, the new one describes the
# thing that actually invalidates a decision (FL-4512).
STATE_UNREVIEWED = "unreviewed"
STATE_ACCEPTED = "accepted"
STATE_REJECTED = "rejected"
STATE_DEFERRED = "deferred"
STATE_SUBJECT_CHANGED = "subject_changed"

# A decision that exists, matches the current subject, and still cannot grant
# acceptance — because no human made it. Every receipt in the corpus written
# before 2026-08-02 falls here: 66 accept plus 6 defer by an agent pre-pass, 6
# by an automated shape review, 3 by a UI dropdown, and not one screenshot among
# the 81. Preserving them as evidence is right; letting them read as human
# acceptance would put unreviewed taste calls into the compiled runtime under a
# false label (FL-4260 / FL-4512).
STATE_HISTORICAL_NONHUMAN = "historical_nonhuman"

REVIEW_STATES = (
    STATE_UNREVIEWED, STATE_ACCEPTED, STATE_REJECTED, STATE_DEFERRED,
    STATE_SUBJECT_CHANGED, STATE_HISTORICAL_NONHUMAN,
)

# ── who is allowed to grant acceptance ───────────────────────────────────────
#
# A review receipt now has to say WHO decided, not merely that a decision was
# recorded. Only a human operator working through the visual review surface can
# produce acceptance; everything else is preserved as evidence and blocks.
#
#   human_operator        a person looked at the rendered glyph and decided.
#                         Requires a visual capture identity (screenshot or
#                         capture id) — a claim of human review with nothing
#                         seen is not human review.
#   agent                 an automated process decided. Never grants.
#   historical_nonhuman   a decision carried forward from the pre-authority
#                         corpus. Never grants. This is what every migrated
#                         agent-prepass decision becomes.
AUTHORITY_HUMAN = "human_operator"
AUTHORITY_AGENT = "agent"
AUTHORITY_HISTORICAL_NONHUMAN = "historical_nonhuman"
REVIEW_AUTHORITIES = (AUTHORITY_HUMAN, AUTHORITY_AGENT, AUTHORITY_HISTORICAL_NONHUMAN)

# Deliberately a one-element set rather than an inline equality test, so the
# grant rule has exactly one definition and a test can assert its membership.
GRANTING_AUTHORITIES = frozenset({AUTHORITY_HUMAN})


# ── loaders ──────────────────────────────────────────────────────────────────

def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _missing_inputs() -> list[str]:
    return [p.name for p in (PROFILE_TABLES, V2_ATLAS, V2_SHAPE_CATALOG, SHAPE_RECEIPTS)
            if not p.exists()]


# ── current artifact provenance (informational only under schema v3) ─────────
#
# These global hashes are still RECORDED into every receipt so it says which
# artifact generation it was written against. Nothing decides review state from
# them any more — doing so was the v2 defect this repair removes (FL-4512).

def current_hashes(tables: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_table_hash": tables.get("source_catalog_hash"),
        "shape_catalog_manifest_hash": catalog.get("manifest_hash"),
        "shape_catalog_page_hash": catalog.get("page_hash"),
    }


# ── queue derivation (SINGLE OWNER of "the 72") ──────────────────────────────

def build_review_receipt_set() -> dict[str, Any]:
    """Derive the canonical RQ-153c primary-winner review receipt set.

    Returns a dict with current artifact hashes and one row per distinct COMMON
    primary winner glyph. Each row carries: assigned profiles, every bucket
    context it wins, candidate context, coverage/shape metrics, a renderable
    glyph visual (unicode char + atlas page/cell + rendered bitmap sha), and a
    runtime-decision trace pointer.
    """
    tables = load_json(PROFILE_TABLES)
    atlas = load_json(V2_ATLAS)
    catalog = load_json(V2_SHAPE_CATALOG)
    receipts = load_jsonl(SHAPE_RECEIPTS)

    cat_by_gid = {int(e["glyph_id"]): e for e in catalog.get("entries", []) if "glyph_id" in e}
    rec_by_gid = {int(r["glyph_id"]): r for r in receipts if "glyph_id" in r}
    glyph_index = atlas.get("glyph_index", {})

    # primary glyph -> aggregated context
    rows: dict[int, dict[str, Any]] = {}
    for profile, lane_table in tables.get("profiles", {}).items():
        for direction, density_cells in lane_table.items():
            for density, cell in density_cells.items():
                if not isinstance(cell, dict) or cell.get("rare_cell"):
                    continue
                prims = [int(g) for g in cell.get("primary_glyph_ids", []) if isinstance(g, int)]
                cand_ct = len(cell.get("candidate_glyph_ids", []))
                min_ct = int(cell.get("min_candidate_count", 8))
                for gid in prims:
                    row = rows.setdefault(gid, {
                        "row_id": f"P-{gid}",
                        "glyph_id": gid,
                        "profiles": set(),
                        "bucket_keys": [],
                        "roles": set(),
                        "candidate_count_min": cand_ct,
                        "min_candidate_count": min_ct,
                    })
                    row["profiles"].add(profile)
                    role = cell.get("role")
                    if role:
                        row["roles"].add(role)
                    row["bucket_keys"].append({
                        "key": f"{profile}/{direction}/{density}",
                        "profile": profile,
                        "direction": direction,
                        "density": density,
                        "role": role,
                        "candidate_count": cand_ct,
                        "min_candidate_count": min_ct,
                        "tie_rule": cell.get("tie_rule"),
                        "candidate_receipt_ids": cell.get("candidate_receipt_ids", []),
                        # Needed so the rivals can be resolved to tiles below.
                        "candidate_glyph_ids": [
                            int(g) for g in cell.get("candidate_glyph_ids", [])
                            if isinstance(g, int)
                        ],
                    })
                    row["candidate_count_min"] = min(row["candidate_count_min"], cand_ct)

    # Fingerprint what each reviewer is actually being asked to judge. Computed
    # from the SAME loaded artifacts the queue rows above came from, so a row and
    # its subject can never describe two different snapshots.
    subjects = SUBJECT.build_review_subjects(tables, rec_by_gid)

    out_rows: list[dict[str, Any]] = []
    for gid in sorted(rows):
        row = rows[gid]
        subject_row = subjects.get(row["row_id"], {})
        rec = rec_by_gid.get(gid, {})
        cat = cat_by_gid.get(gid, {})
        # rendered glyph visual (not only GlyphId): real char + atlas raster pointer
        # WHERE THE REVIEWER'S EYES GO.
        #
        # This used to consult the material.morphology.v2 atlas only, so any
        # glyph drawn in a DIFFERENT content pack — every baseline glyph, for one
        # — came back with no atlas reference at all, and the review surface
        # showed a decision with nothing to look at. The native registry knows
        # every pack, so it can locate any drawable glyph (FL-4482 / FL-4260).
        atlas_ref = ADMISSION.resolve_tile(gid)
        visual = {
            "glyph_char": rec.get("unicode_sequence") or cat.get("unicode"),
            "unicode_scalar": rec.get("unicode_scalar") or cat.get("unicode_scalar"),
            "label": cat.get("label"),
            "repertoire": cat.get("repertoire"),
            "stroke_class": cat.get("stroke_class") or rec.get("shape_role"),
            "visual_family": rec.get("visual_family"),
            "rendered_bitmap_sha256": rec.get("rendered_bitmap_sha256"),
            "atlas_ref": atlas_ref,
            # Whether the atlas can actually draw this glyph. Derived from real
            # bindings; the retired hard-coded id ceiling declared 22 currently
            # reviewable glyphs undrawable even though the registry binds them.
            "atlas_admitted": atlas_ref is not None,
        }
        metrics = {
            "shape6_norm": rec.get("shape6_norm") or cat.get("shape6_norm"),
            "shape6_density": rec.get("shape6_density") or cat.get("shape6_density"),
            "principal_axis_deg": rec.get("principal_axis_deg"),
            "void_count": rec.get("void_count"),
            "symmetry_class": rec.get("symmetry_class"),
        }
        trace = {
            "receipt_id": rec.get("receipt_id") or f"FL4131-M2-{gid}",
            "runtime_resolver": RUNTIME_RESOLVER,
            "tie_rule": row["bucket_keys"][0].get("tie_rule") if row["bucket_keys"] else None,
            "rejection_reason": rec.get("rejection_reason"),
            "review_state_source": rec.get("review_state"),
            "note": "RQ-155 Trace pointer: candidate->primary election path for this "
                    "glyph; full runtime decision trace refresh is the RQ-155 follow-on.",
        }
        out_rows.append({
            "row_id": row["row_id"],
            "glyph_id": gid,
            "profiles": sorted(row["profiles"]),
            "roles": sorted(row["roles"]),
            "context_count": len(row["bucket_keys"]),
            "bucket_keys": sorted(row["bucket_keys"], key=lambda b: b["key"]),
            "min_candidate_count": row["min_candidate_count"],
            "candidate_count_min_observed": row["candidate_count_min"],
            "visual": visual,
            "metrics": metrics,
            "trace": trace,
            # The identity a stored decision is keyed by. Both the fingerprint
            # and the readable bundle behind it travel with the row, so a
            # reviewer can be shown WHAT changed, not merely that something did.
            "review_subject_sha256": subject_row.get("review_subject_sha256"),
            "review_subject": subject_row.get("review_subject"),
            # THE RIVALS, ALSO AS PICTURES.
            #
            # A reviewer is not judging the winner in isolation — they are
            # judging it against the field it beat. Showing the winner's tile
            # while leaving the rivals as bare numbers makes the decision
            # unanswerable, so every rival across every context is resolved to a
            # real atlas tile here (FL-4482 / FL-4260).
            "rival_visuals": _rival_visuals(row["bucket_keys"], rec_by_gid, cat_by_gid),
            # A primary with no shape receipt cannot be drawn by the atlas and so
            # can never reach a runtime lane. Surfaced explicitly so the queue can
            # explain an unpromotable row instead of silently dropping it.
            "primary_has_shape_receipt": subject_row.get("primary_has_shape_receipt", False),
        })

    return {
        "fl": "FL-4260",
        "rq": "RQ-153c",
        "review_subject_schema": SUBJECT.REVIEW_SUBJECT_SCHEMA,
        "required_primary_count": len(out_rows),
        # The required set is DERIVED from the current non-rare cells above — it
        # is whatever the artifacts currently elect, never a number written into
        # this file. A hard-coded expectation (v2 asserted exactly 72) silently
        # turns a coverage change into a passing gate (FL-4512).
        "required_row_ids": sorted(r["row_id"] for r in out_rows),
        "common_cell_count": sum(
            1
            for _p, lt in tables.get("profiles", {}).items()
            for _d, dc in lt.items()
            for _dn, c in dc.items()
            if isinstance(c, dict) and not c.get("rare_cell")
        ),
        "current_hashes": current_hashes(tables, catalog),
        "table_runtime_profile_live": bool(tables.get("runtime_profile_live", True)),
        "table_review_state": tables.get("review_state"),
        "rows": out_rows,
    }



def _rival_visuals(bucket_keys: list[dict[str, Any]],
                   rec_by_gid: dict[int, dict[str, Any]],
                   cat_by_gid: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve every rival candidate across every context to a viewable tile.

    Rivals repeat across a winner's contexts, so they are de-duplicated into one
    list keyed by glyph id — the reviewer needs to see each distinct rival once,
    not once per cell.

    `atlas_admitted` is reported per rival rather than assumed: a rival the atlas
    cannot draw is itself a finding about the candidate pool, and hiding it would
    make the contest look sounder than it is.
    """
    seen: dict[int, dict[str, Any]] = {}
    for bucket in bucket_keys:
        for gid in bucket.get("candidate_glyph_ids", []):
            if gid in seen:
                continue
            receipt = rec_by_gid.get(gid, {})
            catalog_entry = cat_by_gid.get(gid, {})
            tile = ADMISSION.resolve_tile(gid)
            seen[gid] = {
                "glyph_id": gid,
                "glyph_char": receipt.get("unicode_sequence") or catalog_entry.get("unicode"),
                "unicode_scalar": (receipt.get("unicode_scalar")
                                   or catalog_entry.get("unicode_scalar")),
                "rendered_bitmap_sha256": receipt.get("rendered_bitmap_sha256"),
                "atlas_ref": tile,
                "atlas_admitted": tile is not None,
            }
    return [seen[gid] for gid in sorted(seen)]


# ── receipt state machine ────────────────────────────────────────────────────

def receipt_authority(rec: dict[str, Any]) -> str:
    """Who made this decision, defaulting to a state that cannot grant.

    A receipt with no ``review_authority`` field predates the authority rule, so
    it is treated as ``historical_nonhuman``. This is the fail-shut direction: an
    old receipt might have been written by a person, but nothing in it says so,
    and guessing in the permissive direction would launder unattributed decisions
    into human acceptance.

    A receipt that CLAIMS human authority without a visual capture identity is
    also demoted. Human review of a rendered glyph means somebody looked at the
    rendered glyph; a claim with nothing seen is not evidence of that.
    """
    claimed = rec.get("review_authority")
    if claimed not in REVIEW_AUTHORITIES:
        return AUTHORITY_HISTORICAL_NONHUMAN
    if claimed == AUTHORITY_HUMAN:
        has_capture = bool(rec.get("screenshot_path")) or bool(rec.get("capture_id"))
        if not has_capture:
            return AUTHORITY_HISTORICAL_NONHUMAN
    return claimed


def receipt_subject_key(rec: dict[str, Any]) -> tuple[str, str] | None:
    """The (row_id, subject fingerprint) pair a receipt speaks for.

    Returns None for any receipt that cannot name its subject — which is every
    v2 and pre-v2 receipt ever written, because subject fingerprints did not
    exist when they were recorded. That is deliberate and fail-closed: an old
    receipt is not evidence about a current subject, and the ONLY sanctioned way
    for one to become current is the migration in
    ``scripts/fl4260_migrate_review_receipts.py``, which re-derives the old
    subject and demands a byte-for-byte match before carrying the decision over.
    """
    row_id = rec.get("row_id")
    subject_sha = rec.get("review_subject_sha256")
    if isinstance(row_id, str) and isinstance(subject_sha, str) and subject_sha:
        return (row_id, subject_sha)
    return None


def _latest_terminal_by_subject(
    receipts: list[dict[str, Any]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, dict[str, Any]]]:
    """Index terminal (accept/reject/defer) receipts two ways, last-write-wins.

    Returns a pair:

      by_subject  keyed by (row_id, subject fingerprint). This is the index that
                  can GRANT a state — a hit here means the stored decision was
                  made about exactly the subject now being asked about.

      by_row      keyed by row_id alone, ignoring subject. This index can never
                  grant anything; it exists only so a row whose subject moved can
                  be reported as `subject_changed` (with the superseded decision
                  attached for the reviewer to see) rather than as a bare
                  `unreviewed` row with its history thrown away.

    Later rows override earlier ones in both indexes so a reviewer can change
    their mind by appending rather than rewriting the file.
    """
    by_subject: dict[tuple[str, str], dict[str, Any]] = {}
    by_row: dict[str, dict[str, Any]] = {}
    for rec in receipts:
        if rec.get("action") not in TERMINAL_ACTIONS:
            continue
        # Old receipts carry glyph_id but no row_id; synthesise the row id so
        # their history can still be surfaced under by_row.
        row_id = rec.get("row_id")
        if not isinstance(row_id, str):
            gid = rec.get("glyph_id")
            row_id = f"P-{int(gid)}" if isinstance(gid, int) else None
        if row_id is None:
            continue
        by_row[row_id] = rec
        key = receipt_subject_key(rec)
        if key is not None:
            by_subject[key] = rec
    return by_subject, by_row


def review_state(queue: dict[str, Any], receipts: list[dict[str, Any]]) -> dict[str, Any]:
    """Annotate each queue row with its current review state and return counts.

    The rule, in one sentence: a decision counts only when it was made about the
    exact subject now in front of the reviewer.

    Concretely, for each row the current subject fingerprint is looked up in the
    subject index. A hit yields accepted/rejected/deferred according to the
    recorded action. A miss means one of two things, and the two are reported
    differently because they need different work:

      * no decision exists for this row at all      -> unreviewed
      * a decision exists but for a different subject -> subject_changed

    Nothing here consults a global artifact hash. That comparison was the v2
    defect: it could not tell an unrelated catalog edit apart from a real change
    to the reviewed material, so it condemned all 72 decisions together
    (FL-4512).
    """
    by_subject, by_row = _latest_terminal_by_subject(receipts)
    counts = {state: 0 for state in REVIEW_STATES}

    for row in queue["rows"]:
        subject_sha = row.get("review_subject_sha256")
        current = by_subject.get((row["row_id"], subject_sha)) if subject_sha else None
        superseded = by_row.get(row["row_id"])

        # TWO independent conditions must BOTH hold before a row can be accepted:
        #
        #   1. the decision was made about the subject now on screen, and
        #   2. a human operator made it, having actually seen the glyph.
        #
        # Subject identity alone is not authority. An earlier version of this
        # function checked only condition 1, which meant 51 migrated agent
        # pre-pass decisions read as `accepted` — automated taste calls about to
        # enter the compiled runtime labelled as human review (FL-4512).
        authority = receipt_authority(current) if current is not None else None

        if current is not None and authority in GRANTING_AUTHORITIES:
            state = {
                "accept": STATE_ACCEPTED,
                "reject": STATE_REJECTED,
            }.get(current.get("action"), STATE_DEFERRED)
            receipt = current
        elif current is not None:
            # Right subject, wrong authority. The decision is real evidence and
            # is kept visible, but it can never grant.
            state = STATE_HISTORICAL_NONHUMAN
            receipt = current
        elif superseded is not None:
            # A decision exists for this glyph, but the thing it judged has moved.
            # If that decision was not human either, say so plainly rather than
            # implying a human decision is merely out of date.
            state = (STATE_SUBJECT_CHANGED
                     if receipt_authority(superseded) in GRANTING_AUTHORITIES
                     else STATE_HISTORICAL_NONHUMAN)
            receipt = superseded
        else:
            state = STATE_UNREVIEWED
            receipt = None

        row["review_state"] = state
        row["review_receipt"] = receipt
        row["review_authority"] = (
            receipt_authority(receipt) if receipt is not None else None
        )
        # Spelled out so a reviewer reading JSON can see WHY a prior decision did
        # not carry over, without having to recompute fingerprints themselves.
        row["prior_decision_subject_sha256"] = (
            superseded.get("review_subject_sha256") if superseded else None
        )
        # Every state except `accepted` still needs a human. Surfaced as one
        # boolean so downstream gates cannot disagree about what counts.
        row["awaits_human_review"] = state != STATE_ACCEPTED
        counts[state] += 1
    return counts


# ── evidence gates (RQ-153c review completeness) ─────────────────────────────

def review_evidence_gates() -> tuple[dict[str, Any], int]:
    missing = _missing_inputs()
    if missing:
        return {"verdict": "INPUT_MISSING", "missing": missing}, 3

    queue = build_review_receipt_set()
    receipts = load_jsonl(MANUAL_RECEIPTS)
    counts = review_state(queue, receipts)
    required = queue["required_primary_count"]

    unreviewed = [r["row_id"] for r in queue["rows"] if r["review_state"] == STATE_UNREVIEWED]
    rejected = [r["row_id"] for r in queue["rows"] if r["review_state"] == STATE_REJECTED]
    deferred = [r["row_id"] for r in queue["rows"] if r["review_state"] == STATE_DEFERRED]
    changed = [r["row_id"] for r in queue["rows"] if r["review_state"] == STATE_SUBJECT_CHANGED]
    accepted = [r["row_id"] for r in queue["rows"] if r["review_state"] == STATE_ACCEPTED]
    nonhuman = [r["row_id"] for r in queue["rows"]
                if r["review_state"] == STATE_HISTORICAL_NONHUMAN]
    # The single honest count of outstanding human work.
    awaiting = [r["row_id"] for r in queue["rows"] if r.get("awaits_human_review")]

    gates: dict[str, dict[str, Any]] = {}

    # 1. receipt-set coverage — the rows present must be EXACTLY the rows the
    #    current artifacts require, no more and no fewer, and each must carry a
    #    drawable glyph, at least one cell context, and a subject fingerprint.
    #
    #    v2 asserted a literal required count of 72. A count cannot notice that the
    #    artifacts now elect a different set of winners: add a profile cell and
    #    the gate simply fails with no explanation, remove one and it fails while
    #    the real coverage question goes unasked. The required set is now derived
    #    from the artifacts and compared as a SET, so a coverage change is
    #    reported as the specific rows that appeared or vanished (FL-4512).
    required_ids = set(queue["required_row_ids"])
    present_ids = {r["row_id"] for r in queue["rows"]}
    missing_from_manifest = sorted(required_ids - present_ids)
    unexpected_in_manifest = sorted(present_ids - required_ids)
    malformed = [r["row_id"] for r in queue["rows"]
                 if not r["bucket_keys"]
                 or r["visual"].get("glyph_char") in (None, "")
                 or not r.get("review_subject_sha256")]
    gates["evidence_fl4260_rq153c_review_receipts_complete"] = {
        "pass": (not missing_from_manifest and not unexpected_in_manifest and not malformed),
        "required_primary_count": required,
        "rows": len(queue["rows"]),
        "missing_from_manifest": missing_from_manifest[:16],
        "unexpected_in_manifest": unexpected_in_manifest[:16],
        "malformed_rows": malformed[:16],
        "note": "required set is derived from every current non-rare profile cell; "
                "exact set coverage is verified rather than a hard-coded count.",
    }

    # 2. acceptance receipts present — every required primary is accepted.
    # Acceptance here means HUMAN acceptance of the CURRENT subject. Counting any
    # matching receipt would readmit 51 migrated agent pre-pass decisions as if a
    # person had signed them off (FL-4512).
    gates["evidence_fl4260_primary_acceptance_receipts_present"] = {
        "pass": len(accepted) == required and not awaiting,
        "accepted_by_human_operator": len(accepted),
        "required": required,
        "awaiting_human_review": len(awaiting),
        "unreviewed_rows": unreviewed[:32],
        "historical_nonhuman_rows": nonhuman[:32],
        "note": "only human_operator receipts carrying a visual capture identity and "
                "the current review-subject hash can grant acceptance.",
    }

    # 3. rejections fail shut — any reject / defer / subject-changed row blocks
    #    review-ready state (FL-4260 RQ-153c).
    blockers = rejected + deferred + changed + nonhuman
    review_consistent = not (queue.get("table_review_state") == "all_cells_review_accepted" and blockers)
    gates["evidence_fl4260_primary_rejections_fail_closed"] = {
        "pass": review_consistent,
        "blocker_count": len(blockers),
        "rejected_rows": rejected[:16],
        "deferred_rows": deferred[:16],
        "subject_changed_rows": changed[:16],
        "historical_nonhuman_rows": nonhuman[:16],
        "table_review_state": queue.get("table_review_state"),
        "note": "fail-shut: all-cells review-ready state requires zero "
                "reject / defer / subject-changed blockers (FL-4260 RQ-153c).",
    }

    # 4. every decision speaks for the subject now in front of the reviewer.
    #    Replaces v2's global-hash currency check. The distinction that matters:
    #    this gate fails ONLY for rows whose own reviewed material moved, so an
    #    unrelated catalog edit no longer condemns decisions wholesale (FL-4512).
    gates["evidence_fl4260_receipts_match_current_subject"] = {
        "pass": len(changed) == 0,
        "subject_changed_count": len(changed),
        "subject_changed_rows": changed[:16],
        "review_subject_schema": queue.get("review_subject_schema"),
        # Retained as informational provenance only. Nothing decides state from
        # these values any more; they are recorded so a receipt still says which
        # artifact generation it was written against.
        "current_hashes": queue["current_hashes"],
    }

    review_complete = all(g["pass"] for g in gates.values())
    review_ready = (
        review_complete
        and len(accepted) == required
        and not blockers
    )
    result = {
        "fl": "FL-4260",
        "rq": "RQ-153c",
        "review_evidence_verdict": "PASS" if review_complete else "FAIL",
        "review_ready": review_ready,
        "closure": False,
        "counts": counts,
        "required_primary_count": required,
        "awaiting_human_review": len(awaiting),
        "accepted_by_human_operator": len(accepted),
        "gates": gates,
        "note": "Review-evidence PASS means the derived receipt set covers exactly the "
                "currently required primaries, every one has an accept receipt keyed to its "
                "CURRENT review subject, no reject/defer/subject-changed blocker "
                "remains, and review state is consistent. review_ready is the precondition "
                "for refreshing review metadata without changing candidate pools. "
                "It is NOT closure: final RQ-155 Trace, canonical RQ-156, Law 15 VPS two-tab, "
                "and Law 16 operator signoff remain open.",
    }
    return result, (0 if review_complete else 2)


# ── receipt writer ───────────────────────────────────────────────────────────

def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def current_source_commit() -> str:
    """The repository revision a decision is being recorded against.

    Stored in every receipt so a future reader can check out the exact tree the
    reviewer was looking at. Returns "unknown" rather than raising when git is
    unavailable or this is not a checkout — a missing provenance field must not
    stop a reviewer from recording their call.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else "unknown"
    except Exception:
        return "unknown"


def write_receipt(row_id: str, action: str, reason: str, reviewer: str,
                  screenshot_path: str, capture_id: str, termpp_context: str,
                  authority: str = AUTHORITY_AGENT) -> dict[str, Any]:
    """Record one review decision.

    `authority` says who is deciding. It defaults to `agent` — the state that
    cannot grant acceptance — so a caller has to ASK for human authority rather
    than receive it by omission. That default direction matters: the previous
    version defaulted the reviewer name to the literal string "operator", so any
    automated invocation minted operator attribution for free (FL-4260).

    Human authority additionally requires a visual capture identity. Reviewing a
    rendered glyph means having seen the rendered glyph; a human claim with
    nothing captured is refused here rather than quietly demoted later.
    """
    if action not in TERMINAL_ACTIONS:
        raise ValueError(f"action must be one of {sorted(TERMINAL_ACTIONS)}, got {action!r}")
    if not reason or not reason.strip():
        raise ValueError("a non-empty --reason is required for every review action")
    if authority not in REVIEW_AUTHORITIES:
        raise ValueError(f"authority must be one of {list(REVIEW_AUTHORITIES)}, got {authority!r}")
    if not reviewer or not reviewer.strip():
        raise ValueError("a non-empty --reviewer is required; attribution is never defaulted")
    if authority == AUTHORITY_HUMAN and not (screenshot_path or capture_id):
        raise ValueError(
            "human_operator authority requires a visual capture identity: pass "
            "--screenshot and/or --capture-id naming what the reviewer actually saw")

    queue = build_review_receipt_set()
    row = next((r for r in queue["rows"] if r["row_id"] == row_id), None)
    if row is None:
        raise KeyError(row_id)

    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "fl": "FL-4260",
        "rq": "RQ-153c",
        "row_id": row_id,
        "glyph_id": row["glyph_id"],
        "action": action,
        "assigned_profiles": row["profiles"],
        "bucket_keys": [b["key"] for b in row["bucket_keys"]],
        "covers_contexts": row["context_count"],
        "primary_glyph_id": row["glyph_id"],
        "glyph_char": row["visual"].get("glyph_char"),
        "unicode_scalar": row["visual"].get("unicode_scalar"),
        "rendered_bitmap_sha256": row["visual"].get("rendered_bitmap_sha256"),
        # THE identity this decision is keyed by. Everything below it is context.
        "review_subject_sha256": row["review_subject_sha256"],
        "review_subject_schema": SUBJECT.REVIEW_SUBJECT_SCHEMA,
        # The readable bundle behind the fingerprint, stored in full. Without it
        # a future reader could verify that a hash matches but could never see
        # what was judged, which is the difference between an audit trail and a
        # checksum.
        "review_subject": row["review_subject"],
        # WHO decided. Only human_operator can ever grant acceptance.
        "review_authority": authority,
        # WHERE the reviewer's eyes went, and which atlas generation drew it.
        "reviewed_atlas_ref": row["visual"].get("atlas_ref"),
        "admission_provenance": ADMISSION.admission_provenance(),
        # Which tree the reviewer was looking at.
        "source_commit": current_source_commit(),
        # Informational provenance only — no gate decides state from these any
        # more (that was the v2 defect, FL-4512).
        "provenance_hashes": queue["current_hashes"],
        "screenshot_path": screenshot_path or "",
        "capture_id": capture_id or "",
        "termpp_context": termpp_context or "",
        "reviewer": reviewer or "operator",
        "reason": reason.strip(),
        "reviewer_timestamp": _now_iso(),
        "tool_version": TOOL_VERSION,
    }
    with MANUAL_RECEIPTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
    return receipt


# ── rendering helpers ────────────────────────────────────────────────────────

def _fmt_row_line(row: dict[str, Any]) -> str:
    v = row["visual"]
    ch = v.get("glyph_char") or "?"
    state = row.get("review_state", "?")
    profs = ",".join(row["profiles"])
    authority = row.get("review_authority") or "-"
    return (f"  [{state:>19}] {row['row_id']:>7}  glyph={row['glyph_id']:>4} '{ch}' "
            f"ctx={row['context_count']:>2}  by={authority:<20} {profs}")


def cmd_list(args) -> int:
    missing = _missing_inputs()
    if missing:
        print(json.dumps({"verdict": "INPUT_MISSING", "missing": missing}))
        return 3
    queue = build_review_receipt_set()
    counts = review_state(queue, load_jsonl(MANUAL_RECEIPTS))
    rows = queue["rows"]
    if args.state:
        rows = [r for r in rows if r["review_state"] == args.state]
    if args.json:
        print(json.dumps({"counts": counts, "required_primary_count": queue["required_primary_count"],
                          "rows": rows}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print(f"FL-4260 RQ-153c primary-winner review receipts  ({queue['required_primary_count']} required primaries)")
    print(f"  table runtime_profile_live={queue['table_runtime_profile_live']}  "
          f"review_state={queue['table_review_state']}")
    print(f"  state counts: {counts}")
    for r in rows:
        print(_fmt_row_line(r))
    return 0


def cmd_show(args) -> int:
    missing = _missing_inputs()
    if missing:
        print(json.dumps({"verdict": "INPUT_MISSING", "missing": missing}))
        return 3
    queue = build_review_receipt_set()
    review_state(queue, load_jsonl(MANUAL_RECEIPTS))
    row = next((r for r in queue["rows"] if r["row_id"] == args.row_id), None)
    if row is None:
        print(f"no such review receipt row: {args.row_id}", file=sys.stderr)
        return 4
    if args.json:
        print(json.dumps(row, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    v, m, t = row["visual"], row["metrics"], row["trace"]
    print(f"FL-4260 RQ-153c review receipt row {row['row_id']}  (state: {row['review_state']})")
    print(f"  primary glyph_id : {row['glyph_id']}")
    print(f"  rendered glyph   : '{v.get('glyph_char')}'  U+{(v.get('unicode_scalar') or 0):04X}  "
          f"label={v.get('label')}  repertoire={v.get('repertoire')}")
    print(f"  atlas admitted   : {v.get('atlas_admitted')}   stroke_class={v.get('stroke_class')}  "
          f"visual_family={v.get('visual_family')}")
    print(f"  rendered bmp sha : {v.get('rendered_bitmap_sha256')}")
    if v.get("atlas_ref"):
        a = v["atlas_ref"]
        # The exact pixels to look at: which sheet, and where on it.
        print(f"  atlas tile       : {a.get('content_pack_id')} "
              f"{a.get('page16_path')} rect={a.get('rect_xywh')} "
              f"(col {a.get('source_column')}, row {a.get('source_row')})")
    else:
        print("  atlas tile       : NONE — this glyph has no native binding and "
              "cannot be shown to a reviewer")

    # The field the winner beat, each rival as a viewable tile rather than a
    # bare number. Without this the decision is unanswerable.
    rivals = row.get("rival_visuals") or []
    if rivals:
        drawable = sum(1 for r in rivals if r.get("atlas_admitted"))
        print(f"  rivals           : {len(rivals)} distinct ({drawable} drawable)")
        for rival in rivals:
            ref = rival.get("atlas_ref") or {}
            mark = " " if rival.get("atlas_admitted") else "!"
            print(f"     {mark}{rival['glyph_id']:>6} '{rival.get('glyph_char') or '?'}'  "
                  f"{ref.get('page16_path') or 'NO TILE'} rect={ref.get('rect_xywh')}")
    print(f"  shape6_norm      : {m.get('shape6_norm')}")
    print(f"  profiles         : {', '.join(row['profiles'])}   roles: {', '.join(row['roles'])}")
    print(f"  wins {row['context_count']} bucket contexts (min candidate floor {row['min_candidate_count']}):")
    for b in row["bucket_keys"]:
        print(f"      {b['key']:<18} role={b.get('role')} candidates={b.get('candidate_count')} tie={b.get('tie_rule')}")
    print(f"  trace            : receipt_id={t.get('receipt_id')} resolver={t.get('runtime_resolver')} "
          f"tie_rule={t.get('tie_rule')}")
    print(f"  review subject   : {row.get('review_subject_sha256')}")
    if row.get("review_receipt"):
        rr = row["review_receipt"]
        label = "current receipt" if row["review_state"] != STATE_SUBJECT_CHANGED else "prior decision"
        print(f"  {label:<16} : action={rr.get('action')} reviewer={rr.get('reviewer')} "
              f"at={rr.get('reviewer_timestamp')} reason={rr.get('reason')!r}")
        if rr.get("migrated_from"):
            mf = rr["migrated_from"]
            print(f"                     migrated from subject {str(mf.get('old_review_subject_sha256'))[:16]}... "
                  f"at {mf.get('migrated_at')}")

    # For a row whose subject moved, showing the fingerprint mismatch alone would
    # tell the reviewer nothing actionable. The migration manifest still holds the
    # subject they originally judged, so spell out exactly which part moved.
    if row["review_state"] == STATE_SUBJECT_CHANGED:
        _print_subject_change(row)
    return 0


def _print_subject_change(row: dict[str, Any]) -> None:
    """Explain what moved under a reviewer's feet, from the migration manifest."""
    manifest_path = GEN / "material.morphology.v2.review_subject_migration_manifest.json"
    if not manifest_path.exists():
        print("  subject changed  : no migration manifest on disk; the previously "
              "reviewed subject cannot be reconstructed")
        return

    manifest = load_json(manifest_path)
    prior = next((m for m in manifest.get("rows", []) if m["row_id"] == row["row_id"]), None)
    if prior is None:
        print("  subject changed  : this row has no prior decision (newly required)")
        return

    delta = SUBJECT.subject_delta(prior.get("old_review_subject"), row.get("review_subject"))
    print(f"  SUBJECT CHANGED  : previously reviewed as "
          f"{str(prior.get('old_review_subject_sha256'))[:16]}...")
    if delta["primary_bitmap_changed"]:
        print("      * the winning glyph's own drawn pixels changed")
    for key in delta["contexts_added"]:
        print(f"      * now also wins {key}")
    for key in delta["contexts_removed"]:
        print(f"      * no longer wins {key}")
    for change in delta["candidates_changed"]:
        if change["reordered_only"]:
            print(f"      * {change['context']}: same rivals, different rank order")
        else:
            print(f"      * {change['context']}: rivals +{change['added']} -{change['removed']}")
    # Descriptor drift is the commonest cause and the least visible one, so it is
    # summarised rather than listed glyph by glyph.
    vanished = [d["glyph_id"] for d in delta["descriptors_changed"] if d["vanished_from_catalog"]]
    arrived = [d["glyph_id"] for d in delta["descriptors_changed"] if d["new_to_catalog"]]
    remeasured = [d["glyph_id"] for d in delta["descriptors_changed"]
                  if not d["vanished_from_catalog"] and not d["new_to_catalog"]]
    if vanished:
        print(f"      * rival glyphs removed from the catalog: {vanished}")
    if arrived:
        print(f"      * rival glyphs new to the catalog: {arrived}")
    if remeasured:
        print(f"      * {len(remeasured)} glyph(s) re-measured: {remeasured[:12]}")


def cmd_next(args) -> int:
    missing = _missing_inputs()
    if missing:
        print(json.dumps({"verdict": "INPUT_MISSING", "missing": missing}))
        return 3
    queue = build_review_receipt_set()
    review_state(queue, load_jsonl(MANUAL_RECEIPTS))
    nxt = next((r for r in queue["rows"]
                if r.get("awaits_human_review")), None)
    if nxt is None:
        print("no rows await review: every required primary has a terminal receipt "
              "keyed to its current review subject.")
        return 0
    if args.json:
        print(json.dumps(nxt, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print(_fmt_row_line(nxt).strip())
    print(f"  -> review with: fl4260_review_receipts.py show {nxt['row_id']}")
    return 0


def cmd_action(args, action: str) -> int:
    missing = _missing_inputs()
    if missing:
        print(json.dumps({"verdict": "INPUT_MISSING", "missing": missing}))
        return 3
    try:
        receipt = write_receipt(
            args.row_id, action, args.reason, args.reviewer,
            args.screenshot, args.capture_id, args.termpp_context,
            authority=(AUTHORITY_HUMAN if args.human_operator else AUTHORITY_AGENT),
        )
    except KeyError:
        print(f"no such review receipt row: {args.row_id}", file=sys.stderr)
        return 4
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 4
    print(f"wrote {action} receipt for {receipt['row_id']} (glyph {receipt['glyph_id']} "
          f"'{receipt['glyph_char']}', covers {receipt['covers_contexts']} contexts)")
    print("  NOTE: this receipt does not change runtime. Run "
          "fl4260_material_morphology_review.py --fl4260-rq153c-live-review to refresh review metadata "
          "(fails shut until every required primary is accepted against its current "
          "review subject, with zero reject/defer/subject-changed blockers).")
    return 0


def cmd_gate(args) -> int:
    result, code = review_evidence_gates()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return code
    if result.get("verdict") == "INPUT_MISSING":
        print("RQ-153c review evidence gate: INPUT MISSING")
        for m in result["missing"]:
            print(f"  - missing {m}")
        return code
    print("FL-4260 RQ-153c review evidence gates")
    print(f"  review_evidence_verdict: {result['review_evidence_verdict']}   "
          f"review_ready: {result['review_ready']}   closure: {result['closure']}")
    print(f"  counts: {result['counts']}")
    for name, g in result["gates"].items():
        label = "PASS" if g["pass"] else "FAIL"
        print(f"    [{label}] {name}")
    print(f"  {result['note']}")
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="show the review receipt set")
    p_list.add_argument("--json", action="store_true")
    p_list.add_argument("--state", choices=list(REVIEW_STATES))
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="full detail for one row")
    p_show.add_argument("row_id")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_show)

    p_next = sub.add_parser("next", help="next unreviewed row")
    p_next.add_argument("--json", action="store_true")
    p_next.set_defaults(func=cmd_next)

    for act in ("accept", "reject", "defer"):
        pa = sub.add_parser(act, help=f"write a {act} receipt")
        pa.add_argument("row_id")
        pa.add_argument("--reason", required=True, help="non-empty justification (required)")
        # REQUIRED, never defaulted. A default of "operator" let any automated
        # invocation manufacture human attribution (FL-4260).
        pa.add_argument("--reviewer", required=True,
                        help="who is deciding (required; never defaulted)")
        pa.add_argument("--human-operator", action="store_true",
                        help="assert a person performed this review through the visual "
                             "surface; requires --screenshot and/or --capture-id. Without "
                             "this flag the receipt is recorded as agent authority and "
                             "can never grant acceptance.")
        pa.add_argument("--screenshot", default="", help="headed-review screenshot path")
        pa.add_argument("--capture-id", default="", help="capture id when mounted")
        pa.add_argument("--termpp-context", default="", help="TERM++ runtime context if mounted")
        pa.set_defaults(func=lambda a, _act=act: cmd_action(a, _act))

    p_gate = sub.add_parser("gate", help="RQ-153c review evidence gates")
    p_gate.add_argument("--json", action="store_true")
    p_gate.set_defaults(func=cmd_gate)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
