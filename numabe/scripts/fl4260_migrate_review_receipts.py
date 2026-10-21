#!/usr/bin/env python3
# =============================================================================
# fl4260_migrate_review_receipts.py    (FL-4260 / FL-4512)
# =============================================================================
# THE PROBLEM THIS TOOL SOLVES
# ----------------------------
# The project is about to regenerate its character measurements against a newer
# catalog. Regeneration rewrites the files that describe which character wins
# which cell — and under the OLD rules that wiped out every human review decision
# at once, because decisions were validated against one global "did anything
# change?" hash.
#
# The rule the project actually wants is:
#
#     KEEP a decision when the thing that was reviewed did not change.
#     RE-REVIEW a decision only when the thing that was reviewed really moved.
#
# ``scripts/fl4260_review_subject.py`` now makes "the thing that was reviewed"
# into a precise fingerprint. This tool uses those fingerprints to carry the old
# decisions across the regeneration boundary — but only the ones that genuinely
# still apply.
#
# HOW IT WORKS, IN TWO STEPS
# --------------------------
#   STEP 1  build-manifest   RUN THIS BEFORE REGENERATING.
#           Reads the current (old) artifacts, works out the fingerprint of every
#           reviewed subject as it stands today, and writes that down next to the
#           decision that was made about it. This is the only moment the old
#           subjects can be observed — after regeneration they are gone.
#
#   STEP 2  apply            RUN THIS AFTER REGENERATING.
#           Recomputes the fingerprints from the new artifacts and compares.
#             fingerprints identical  -> the decision still applies; it is
#                                        rewritten as a current receipt with the
#                                        ORIGINAL reviewer, timestamp, action and
#                                        screenshot preserved.
#             row no longer exists    -> marked HISTORICAL. Not deleted; the
#                                        decision stays in the record as a fact
#                                        about the past.
#             fingerprint differs     -> marked UNREVIEWED. Goes back in the queue.
#             row is brand new        -> marked UNREVIEWED.
#
# WHAT THIS TOOL WILL NOT DO
# --------------------------
# It never invents acceptance. A migrated receipt is a COPY of a decision a named
# reviewer already made about a byte-for-byte identical subject. If the subject
# moved by so much as one rounded decimal in one rival glyph's measurements, the
# decision does not travel and a human has to look again.
#
# It also never rewrites who decided. The ``reviewer`` field is carried across
# untouched. That matters here specifically: most of the existing decisions were
# recorded by ``agent-prepass-2026-06-16``, not by a person, and no receipt in
# the corpus carries a screenshot. Migration must make that provenance MORE
# visible, never less.
#
# Usage:
#     python3 scripts/fl4260_migrate_review_receipts.py build-manifest
#     # ... regenerate the morphology artifacts ...
#     python3 scripts/fl4260_migrate_review_receipts.py apply --dry-run
#     python3 scripts/fl4260_migrate_review_receipts.py apply --write
#
# Exit codes: 0 success; 3 required input missing; 4 bad arguments/state.
# =============================================================================

from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fl4260_review_subject as SUBJECT  # noqa: E402
import fl4260_review_receipts as RECEIPTS  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
GEN = REPO_ROOT / "assets/glyphs/generated"
PROFILE_TABLES = GEN / "material.morphology.v2.profile_tables.json"
SHAPE_RECEIPTS = GEN / "material.morphology.v2.shape_receipts.jsonl"
MANUAL_RECEIPTS = GEN / "material.morphology.v2.manual_review_receipts.jsonl"

# Where the pre-regeneration snapshot is parked. This file is the whole point of
# step 1: once the artifacts are regenerated, the old subjects cannot be
# recomputed from anything on disk, so they have to be written down first.
MANIFEST = GEN / "material.morphology.v2.review_subject_migration_manifest.json"

MANIFEST_SCHEMA = "fl4260.review_subject_migration_manifest.v1"

# The four outcomes a manifest row can reach when the migration is applied.
OUTCOME_MIGRATED = "migrated"            # subject identical; decision carried over
OUTCOME_HISTORICAL = "historical"        # the reviewed row no longer exists
OUTCOME_SUBJECT_CHANGED = "subject_changed"  # row exists, subject moved -> re-review
OUTCOME_NEW = "new_unreviewed"           # row appeared; nobody has judged it


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _file_history_commits(path: Path, limit: int = 5) -> list[str]:
    """The recent commits that touched a file.

    Used to reconstruct provenance for receipts written before ``source_commit``
    was a field. This is genuinely weaker evidence than a recorded commit, so the
    manifest labels it as reconstructed rather than presenting it as if the
    original reviewer had stamped it.
    """
    try:
        out = subprocess.run(
            ["git", "log", f"-{limit}", "--format=%H", "--", str(path.relative_to(REPO_ROOT))],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=20,
        )
        if out.returncode != 0:
            return []
        return [line.strip() for line in out.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def _shape_receipts_by_gid() -> dict[int, dict[str, Any]]:
    """Every glyph's measurements, keyed by integer glyph id."""
    return {int(r["glyph_id"]): r for r in load_jsonl(SHAPE_RECEIPTS) if "glyph_id" in r}


def _latest_decision_per_row(receipts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Last-write-wins terminal decision per row, across ALL receipt schemas.

    Deliberately schema-agnostic: the corpus this migration has to read contains
    three different shapes of receipt written over two months, including three
    rows with no schema field at all. What matters for migration is only that a
    named reviewer recorded a terminal action against a glyph.
    """
    latest: dict[str, dict[str, Any]] = {}
    for rec in receipts:
        if rec.get("action") not in RECEIPTS.TERMINAL_ACTIONS:
            continue
        row_id = rec.get("row_id")
        if not isinstance(row_id, str):
            gid = rec.get("glyph_id")
            if not isinstance(gid, int):
                continue
            row_id = f"P-{gid}"
        latest[row_id] = rec
    return latest


# ── step 1: capture the pre-regeneration snapshot ────────────────────────────

def build_manifest() -> dict[str, Any]:
    """Record every reviewed subject as it stands RIGHT NOW, with its decision.

    Must run before regeneration. Produces one manifest row per prior decision,
    carrying the fields direction 7 requires preserved — reviewer, timestamp,
    action, screenshot, source commit — alongside the fingerprint of exactly what
    that reviewer was judging.
    """
    tables = load_json(PROFILE_TABLES)
    subjects = SUBJECT.build_review_subjects(tables, _shape_receipts_by_gid())
    decisions = _latest_decision_per_row(load_jsonl(MANUAL_RECEIPTS))

    # Provenance reconstruction for receipts predating the source_commit field.
    reconstructed_commits = _file_history_commits(MANUAL_RECEIPTS)

    rows: list[dict[str, Any]] = []
    for row_id in sorted(decisions, key=lambda r: int(r.split("-", 1)[1])):
        decision = decisions[row_id]
        subject_row = subjects.get(row_id)
        rows.append({
            "row_id": row_id,
            "glyph_id": decision.get("glyph_id"),
            # The fingerprint of what was reviewed, as of this snapshot. None
            # when the decision names a row the current tables no longer elect —
            # that decision is already historical before regeneration even runs.
            "old_review_subject_sha256": (
                subject_row["review_subject_sha256"] if subject_row else None
            ),
            "old_review_subject": subject_row["review_subject"] if subject_row else None,
            "old_context_keys": subject_row["context_keys"] if subject_row else [],
            # ── preserved decision (direction 7) ──────────────────────────────
            "action": decision.get("action"),
            "reviewer": decision.get("reviewer"),
            "reviewer_timestamp": decision.get("reviewer_timestamp"),
            "screenshot_path": decision.get("screenshot_path", ""),
            "capture_id": decision.get("capture_id", ""),
            "reason": decision.get("reason"),
            "source_commit": decision.get("source_commit"),
            "original_schema_version": decision.get("schema_version"),
            # Honest labelling: none of the old receipts recorded a commit, so
            # any commit shown for them is inferred from file history.
            "source_commit_provenance": (
                "recorded" if decision.get("source_commit") else "reconstructed_from_file_history"
            ),
        })

    # Facts a reader needs to judge the corpus without re-deriving them.
    reviewers: dict[str, int] = {}
    for row in rows:
        reviewers[str(row["reviewer"])] = reviewers.get(str(row["reviewer"]), 0) + 1

    return {
        "schema": MANIFEST_SCHEMA,
        "review_subject_schema": SUBJECT.REVIEW_SUBJECT_SCHEMA,
        "captured_at": _now_iso(),
        "captured_at_commit": RECEIPTS.current_source_commit(),
        "source_table_generated_at": load_json(PROFILE_TABLES).get("generated_at"),
        "source_catalog_hash": tables.get("source_catalog_hash"),
        "reconstructed_receipt_file_commits": reconstructed_commits,
        "decision_count": len(rows),
        "decisions_by_reviewer": dict(sorted(reviewers.items())),
        "decisions_with_screenshot": sum(1 for r in rows if r.get("screenshot_path")),
        "note": "Captured BEFORE regeneration. Old subject fingerprints cannot be "
                "recomputed once the artifacts are rewritten, so this file is the "
                "only record of what each prior decision was actually about.",
        "rows": rows,
    }


# ── step 2: carry decisions across the boundary ──────────────────────────────

def apply_migration(manifest: dict[str, Any]) -> dict[str, Any]:
    """Compare old fingerprints against the regenerated ones and classify.

    Returns a report. Writes nothing — the caller decides whether to commit the
    migrated receipts, so a reviewer can inspect the outcome first.
    """
    tables = load_json(PROFILE_TABLES)
    new_subjects = SUBJECT.build_review_subjects(tables, _shape_receipts_by_gid())

    manifest_rows = {row["row_id"]: row for row in manifest.get("rows", [])}
    outcomes: list[dict[str, Any]] = []
    migrated_receipts: list[dict[str, Any]] = []

    # Every row a prior decision exists for.
    for row_id, row in sorted(manifest_rows.items(), key=lambda kv: int(kv[0].split("-", 1)[1])):
        new_row = new_subjects.get(row_id)
        old_sha = row.get("old_review_subject_sha256")

        if new_row is None:
            # The regenerated artifacts no longer elect this glyph anywhere. The
            # decision is not wrong, it simply has nothing left to apply to.
            outcome = OUTCOME_HISTORICAL
            new_sha = None
        elif old_sha is not None and new_row["review_subject_sha256"] == old_sha:
            # Byte-for-byte identical subject: the reviewer would be shown exactly
            # what they were shown before, so their decision still stands.
            outcome = OUTCOME_MIGRATED
            new_sha = new_row["review_subject_sha256"]
        else:
            outcome = OUTCOME_SUBJECT_CHANGED
            new_sha = new_row["review_subject_sha256"]

        outcomes.append({
            "row_id": row_id,
            "outcome": outcome,
            "action": row.get("action"),
            "reviewer": row.get("reviewer"),
            "old_review_subject_sha256": old_sha,
            "new_review_subject_sha256": new_sha,
        })

        if outcome == OUTCOME_MIGRATED:
            migrated_receipts.append(_migrated_receipt(row, new_row))

    # Rows the regenerated artifacts require that nobody has ever judged.
    for row_id, new_row in sorted(new_subjects.items(), key=lambda kv: int(kv[0].split("-", 1)[1])):
        if row_id in manifest_rows:
            continue
        outcomes.append({
            "row_id": row_id,
            "outcome": OUTCOME_NEW,
            "action": None,
            "reviewer": None,
            "old_review_subject_sha256": None,
            "new_review_subject_sha256": new_row["review_subject_sha256"],
        })

    counts: dict[str, int] = {}
    for entry in outcomes:
        counts[entry["outcome"]] = counts.get(entry["outcome"], 0) + 1

    return {
        "schema": "fl4260.review_subject_migration_report.v1",
        "applied_at": _now_iso(),
        "applied_at_commit": RECEIPTS.current_source_commit(),
        "counts": counts,
        "required_row_count": len(new_subjects),
        "migrated_receipt_count": len(migrated_receipts),
        # The re-review queue, which is the number that actually matters to a
        # human: rows whose subject moved plus rows that never existed before.
        # Rows whose SUBJECT moved or is new. This is a subject-currency queue,
        # NOT the human-review queue: a migrated row has a current subject but
        # still no human decision, so it also awaits review. The authoritative
        # outstanding count comes from the review gate's awaiting_human_review.
        "subject_currency_queue": sorted(
            entry["row_id"] for entry in outcomes
            if entry["outcome"] in (OUTCOME_SUBJECT_CHANGED, OUTCOME_NEW)
        ),
        "historical_rows": sorted(
            entry["row_id"] for entry in outcomes if entry["outcome"] == OUTCOME_HISTORICAL
        ),
        "outcomes": outcomes,
        "migrated_receipts": migrated_receipts,
    }


def _migrated_receipt(manifest_row: dict[str, Any], new_row: dict[str, Any]) -> dict[str, Any]:
    """Rewrite a preserved decision as a current schema-v3 receipt.

    Every field describing WHO decided and WHEN is copied verbatim from the
    original. The only additions are the subject identity that makes the receipt
    checkable, and a ``migrated_from`` block that keeps the migration itself
    auditable — so nobody later mistakes a carried-over decision for a fresh one.
    """
    return {
        "schema_version": RECEIPTS.RECEIPT_SCHEMA,
        "fl": "FL-4260",
        "rq": "RQ-153c",
        "row_id": manifest_row["row_id"],
        "glyph_id": manifest_row.get("glyph_id"),
        "action": manifest_row.get("action"),
        # WHO decided — and this is the whole point of migrating rather than
        # accepting. Not one receipt in the pre-2026-08 corpus was written by a
        # person: 66 accept plus 6 defer by an agent pre-pass, 6 by an automated
        # shape review, 3 by a UI dropdown, zero screenshots among all 81. So a
        # migrated decision is preserved as historical evidence and is
        # STRUCTURALLY unable to grant acceptance, no matter how exactly its
        # subject matches (FL-4260 / FL-4512).
        "review_authority": RECEIPTS.AUTHORITY_HISTORICAL_NONHUMAN,
        # ── preserved verbatim from the original decision (direction 7) ───────
        "reviewer": manifest_row.get("reviewer"),
        "reviewer_timestamp": manifest_row.get("reviewer_timestamp"),
        "screenshot_path": manifest_row.get("screenshot_path", ""),
        "capture_id": manifest_row.get("capture_id", ""),
        "reason": manifest_row.get("reason"),
        # ── the identity that makes this receipt checkable ────────────────────
        "review_subject_sha256": new_row["review_subject_sha256"],
        "review_subject_schema": SUBJECT.REVIEW_SUBJECT_SCHEMA,
        "review_subject": new_row["review_subject"],
        # ── migration audit trail ─────────────────────────────────────────────
        "migrated_from": {
            "original_schema_version": manifest_row.get("original_schema_version"),
            "old_review_subject_sha256": manifest_row.get("old_review_subject_sha256"),
            "source_commit": manifest_row.get("source_commit"),
            "source_commit_provenance": manifest_row.get("source_commit_provenance"),
            "migrated_at": _now_iso(),
            "migrated_by": "fl4260_migrate_review_receipts/1.0",
            "basis": "old and new review-subject fingerprints matched byte-for-byte; "
                     "the reviewer would be shown an identical subject, so their "
                     "decision carries over unchanged AS EVIDENCE",
            "grants_acceptance": False,
            "why_not": "the original decision was not made by a human operator, so it "
                       "is preserved as historical_nonhuman and still requires review",
        },
        "tool_version": RECEIPTS.TOOL_VERSION,
    }


# ── command line ─────────────────────────────────────────────────────────────

def cmd_build_manifest(args) -> int:
    for required in (PROFILE_TABLES, SHAPE_RECEIPTS):
        if not required.exists():
            print(f"missing required input: {required}", file=sys.stderr)
            return 3

    manifest = build_manifest()

    if args.write:
        with MANIFEST.open("w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        print(f"wrote {MANIFEST.relative_to(REPO_ROOT)}")
    else:
        print("DRY RUN — pass --write to save the manifest")

    print(f"  captured at commit : {manifest['captured_at_commit'][:12]}")
    print(f"  table generated_at : {manifest['source_table_generated_at']}")
    print(f"  decisions captured : {manifest['decision_count']}")
    print(f"  with screenshot    : {manifest['decisions_with_screenshot']}")
    print("  by reviewer:")
    for reviewer, count in manifest["decisions_by_reviewer"].items():
        print(f"      {count:>4}  {reviewer}")
    unresolvable = [r["row_id"] for r in manifest["rows"] if r["old_review_subject_sha256"] is None]
    if unresolvable:
        print(f"  decisions naming a row the tables no longer elect: {len(unresolvable)}")
        print(f"      {', '.join(unresolvable[:12])}")
    return 0


def cmd_apply(args) -> int:
    if not MANIFEST.exists():
        print(f"no migration manifest at {MANIFEST.relative_to(REPO_ROOT)} — run "
              f"build-manifest BEFORE regenerating the artifacts", file=sys.stderr)
        return 3

    manifest = load_json(MANIFEST)
    report = apply_migration(manifest)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("FL-4260 review-subject migration")
        print(f"  manifest captured at : {manifest.get('captured_at')} "
              f"({str(manifest.get('captured_at_commit'))[:12]})")
        print(f"  required rows now    : {report['required_row_count']}")
        print("  outcomes:")
        for outcome, count in sorted(report["counts"].items()):
            print(f"      {count:>4}  {outcome}")
        print(f"  subject-currency queue: {len(report['subject_currency_queue'])} row(s)")
        if report["subject_currency_queue"]:
            print(f"      {', '.join(report['subject_currency_queue'][:16])}"
                  f"{' ...' if len(report['subject_currency_queue']) > 16 else ''}")
        print("  NOTE: migrated rows are historical_nonhuman evidence, not acceptance.")
        print("        Every required row still awaits a human operator receipt.")
        if report["historical_rows"]:
            print(f"  historical (row gone): {len(report['historical_rows'])}")

    if args.write:
        if not report["migrated_receipts"]:
            print("nothing to write: no decision survived the subject comparison")
            return 0
        with MANUAL_RECEIPTS.open("a", encoding="utf-8") as f:
            for receipt in report["migrated_receipts"]:
                f.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
        print(f"appended {len(report['migrated_receipts'])} migrated receipt(s) to "
              f"{MANUAL_RECEIPTS.relative_to(REPO_ROOT)}")
    elif not args.json:
        # Suppressed under --json so the stream stays parseable: a human-readable
        # trailer appended after a JSON document makes the whole output invalid.
        print("DRY RUN — pass --write to append the migrated receipts")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser(
        "build-manifest",
        help="capture old subjects + decisions BEFORE regenerating the artifacts")
    p_build.add_argument("--write", action="store_true", help="save the manifest (default: dry run)")
    p_build.set_defaults(func=cmd_build_manifest)

    p_apply = sub.add_parser(
        "apply", help="compare against regenerated artifacts and classify each decision")
    p_apply.add_argument("--write", action="store_true",
                         help="append migrated receipts (default: dry run)")
    p_apply.add_argument("--dry-run", action="store_true", help="explicit no-op default")
    p_apply.add_argument("--json", action="store_true")
    p_apply.set_defaults(func=cmd_apply)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
