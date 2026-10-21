#!/usr/bin/env python3
"""Union-preserving FL overlay setter.

FL-4738 owns safe list-member retirement under the per-key LAST-WRITE-WINS overlay merge
defined by FL-4023. `overlay --patch` is hard-disabled because whole-list replacement lost
lineage (FL-3750/FL-1490), while `overlay --import-proposals` is gap-fill-only. This tool
therefore appends a latest row carrying the full desired value for each touched key.

This tool appends ONE such row:
  - scalar fields (ProofState, EpochStatus, Priority, Area, Category,
    ComplaintCounterState) are SET explicitly,
  - list fields (ComplaintRefs, CodeRefs, RQRefs, GitHubRefs, SessionRefs,
    Kinds, Subsystems, TouchedFiles, RequiredFields, GateNames) are UNIONed
    with the existing effective value — existing entries are NEVER dropped.
  - `--replace-required-field OLD=>NEW`, `--replace-gate-name OLD=>NEW`,
    `--replace-code-ref OLD=>NEW`, and `--replace-kind OLD=>NEW` perform one
    exact ownership migration while preserving every unrelated list value.

Fail-closed: if the computed union for any list field would not be a superset of the existing
value, the tool aborts before writing. Dry-run by default; pass --write to append the row.

Usage:
  python3 scripts/fl_overlay_set.py --fl FL-4057 --proof-state RAW-OPEN-ACTIONABLE \
      --add-complaint-ref FL-4408,FL-4270,FL-4359,FL-4364 --add-rq-ref RQ-170 \
      --add-code-ref docs/plans/GODOT_GOAL.MD --add-kind raw_open,unparked [--write]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from maintainer.lib.fl_config import CANONICAL_FAILURE_LOG  # noqa: E402
from maintainer.lib.failure_log import (  # noqa: E402
    append_overlay_rows,
    fl_write_lock,
    read_fl_overlay,
)
from maintainer.lib.fl_overlay import normalize_overlay_list_value  # noqa: E402

SCALAR_FIELDS = {
    "proof_state": "ProofState",
    "epoch_status": "EpochStatus",
    "priority": "Priority",
    "area": "Area",
    "category": "Category",
    "complaint_counter_state": "ComplaintCounterState",
    # FL-4837: correcting a Description that was measured WRONG after the entry
    # shipped. The authored `**Description:**` line in docs/FAILURE_LOG.md is
    # never touched -- it stays as the record of what was believed then -- and
    # this value supersedes it for every reader, under the same per-key
    # LAST-WRITE-WINS merge as any other scalar. Because overlay rows are
    # append-only, a correction that is itself later corrected REMAINS in the
    # parts and is recoverable with `analyze_runs.py fl description-history`.
    #
    # NOT named `Description` on purpose: that would leave two surfaces both
    # claiming the same name with nothing to tell an authored description from
    # a correction, and would leave a reader of the markdown meeting the wrong
    # text with no marker on it.
    "description_correction": "DescriptionCorrection",
    "description_correction_reason": "DescriptionCorrectionReason",
}
LIST_FIELDS = {
    "add_complaint_ref": "ComplaintRefs",
    "add_code_ref": "CodeRefs",
    "add_rq_ref": "RQRefs",
    "add_github_ref": "GitHubRefs",
    # SessionRefs stores full agent-session identities. Attempt-to-session
    # joins remain in the FL receipt; this list is the reverse lookup surface.
    "add_session_ref": "SessionRefs",
    "add_kind": "Kinds",
    "add_subsystem": "Subsystems",
    "add_touched_file": "TouchedFiles",
    "add_required_field": "RequiredFields",
    "add_gate_name": "GateNames",
}
EXACT_REPLACE_FIELDS = {
    "replace_required_field": "RequiredFields",
    "replace_gate_name": "GateNames",
    # FL-4738: CodeRefs are query routing state. Replacing one stale anchor must
    # preserve every unrelated anchor and reject ambiguous duplicate sources.
    "replace_code_ref": "CodeRefs",
    # Kinds joined 2026-08-11 (FL-4735): a taxonomy correction (planned=>investigation)
    # is an ownership migration, not an addition — union-only left the stale kind in
    # place, and the effective row carried contradictory taxonomy until replaced.
    "replace_kind": "Kinds",
}


def _csv(values: list[str]) -> list[str]:
    out: list[str] = []
    for raw in values or []:
        for part in str(raw).split(","):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return out


def _union(existing: list[str], additions: list[str]) -> list[str]:
    merged = list(existing)
    for a in additions:
        if a not in merged:
            merged.append(a)
    return merged


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fl", required=True, help="FL id, e.g. FL-4057")
    ap.add_argument("--proof-state")
    ap.add_argument("--epoch-status")
    ap.add_argument("--priority")
    ap.add_argument("--area")
    ap.add_argument("--category")
    ap.add_argument("--complaint-counter-state")
    ap.add_argument(
        "--description-correction",
        help="supersede the authored **Description:** line; the authored line is kept as history",
    )
    ap.add_argument(
        "--description-correction-reason",
        help="why the superseded description was wrong (required with --description-correction)",
    )
    for arg in LIST_FIELDS:
        ap.add_argument("--" + arg.replace("_", "-"), action="append", default=[],
                        help=f"add to {LIST_FIELDS[arg]} (csv or repeatable; union, never drops)")
    for arg in EXACT_REPLACE_FIELDS:
        ap.add_argument(
            "--" + arg.replace("_", "-"),
            action="append",
            default=[],
            metavar="OLD=>NEW",
            help=f"replace one exact value in {EXACT_REPLACE_FIELDS[arg]}; every unrelated value is preserved",
        )
    ap.add_argument("--write", action="store_true", help="append the row (default: dry-run)")
    ap.add_argument("--path", default=str(CANONICAL_FAILURE_LOG))
    args = ap.parse_args()

    path = Path(args.path)
    overlay = read_fl_overlay(path)
    current = overlay.get(args.fl)
    if current is None:
        print(f"FAIL: no existing overlay row for {args.fl} (use overlay --fill-defaults first)", file=sys.stderr)
        return 2

    # FL-4837 fail-closed: a Description correction with no recorded reason is
    # a silent rewrite, not a correction. Refuse before anything is computed.
    if args.description_correction and not args.description_correction_reason:
        print(
            "FAIL: --description-correction requires --description-correction-reason "
            "(a correction with no recorded reason is not history)",
            file=sys.stderr,
        )
        return 2
    if args.description_correction_reason and not args.description_correction:
        print(
            "FAIL: --description-correction-reason has no --description-correction to explain",
            file=sys.stderr,
        )
        return 2
    if args.description_correction and "\n" in args.description_correction:
        print(
            "FAIL: --description-correction must be a single line; the ledger field "
            "reader only ever captures one line",
            file=sys.stderr,
        )
        return 2

    new_row: dict = {"fl": args.fl}
    changes: list[str] = []

    # scalars: explicit set (last-write-wins)
    for arg, field in SCALAR_FIELDS.items():
        val = getattr(args, arg)
        if val:
            old = current.get(field, "")
            if str(old) != val:
                new_row[field] = val
                changes.append(f"  {field}: {old or '<unset>'} -> {val}")

    # lists: union, fail closed on any loss
    for arg, field in LIST_FIELDS.items():
        additions = _csv(getattr(args, arg))
        if not additions:
            continue
        existing = normalize_overlay_list_value(current.get(field))
        union = _union(existing, additions)
        # fail-closed: union MUST be a superset of existing
        missing = [x for x in existing if x not in union]
        if missing:
            print(f"FAIL (fail-closed): {field} union would drop {missing}; aborting.", file=sys.stderr)
            return 3
        if union != existing:
            new_row[field] = union
            added = [x for x in additions if x not in existing]
            changes.append(f"  {field}: +{added}  (now {len(union)}: {union})")

    # Exact replacement is the delete-first path for a stale ownership token.
    # It refuses missing and ambiguous sources so callers cannot turn a typo
    # into silent list loss.
    for arg, field in EXACT_REPLACE_FIELDS.items():
        replacements = getattr(args, arg)
        if not replacements:
            continue
        existing = normalize_overlay_list_value(current.get(field))
        # A replacement and additions may target the same list in one call.
        # Start from the already-computed union so the ownership migration does
        # not silently discard the newly required receipt fields.
        updated = list(new_row.get(field, existing))
        for replacement in replacements:
            if replacement.count("=>") != 1:
                print(f"FAIL: {arg} requires exactly OLD=>NEW, got {replacement!r}", file=sys.stderr)
                return 4
            old, new = (part.strip() for part in replacement.split("=>", 1))
            if not old or not new:
                print(f"FAIL: {arg} requires non-empty OLD and NEW", file=sys.stderr)
                return 4
            old_count = updated.count(old)
            if old_count == 0:
                print(f"FAIL: {field} does not contain exact stale value {old!r}", file=sys.stderr)
                return 4
            if old_count != 1:
                print(
                    f"FAIL: {field} contains stale value {old!r} {old_count} times; expected exactly one",
                    file=sys.stderr,
                )
                return 4
            updated[updated.index(old)] = new
        deduped: list[str] = []
        for value in updated:
            if value not in deduped:
                deduped.append(value)
        new_row[field] = deduped
        changes.append(f"  {field}: exact replacements {replacements}")

    if len(new_row) == 1:
        print(f"{args.fl}: no changes (all requested values already present).")
        return 0

    print(f"=== FL overlay set: {args.fl} ===")
    print("\n".join(changes))
    print("\nAppended row (wins per-key, latest in file order):")
    print("  " + json.dumps(new_row, separators=(",", ":")))

    if not args.write:
        print("\nDRY-RUN — re-run with --write to append.")
        return 0

    # FL-4582: overlay rows live in the sidecar JSONL, not the prose ledger.
    with fl_write_lock(path):
        append_overlay_rows(path, [new_row])

    # post-write fail-closed verification
    after = read_fl_overlay(path).get(args.fl, {})
    ok = True
    for arg, field in SCALAR_FIELDS.items():
        val = getattr(args, arg)
        if val and str(after.get(field, "")) != val:
            print(f"VERIFY FAIL: {field} did not take effect ({after.get(field)!r})", file=sys.stderr)
            ok = False
    for arg, field in LIST_FIELDS.items():
        additions = _csv(getattr(args, arg))
        if not additions:
            continue
        eff = normalize_overlay_list_value(after.get(field))
        before = normalize_overlay_list_value(current.get(field))
        replaced_old_values = {
            replacement.split("=>", 1)[0].strip()
            for replace_arg, replace_field in EXACT_REPLACE_FIELDS.items()
            if replace_field == field
            for replacement in getattr(args, replace_arg)
        }
        lost = [x for x in before if x not in eff and x not in replaced_old_values]
        not_added = [x for x in additions if x not in eff]
        if lost:
            print(f"VERIFY FAIL: {field} lost {lost}", file=sys.stderr); ok = False
        if not_added:
            print(f"VERIFY FAIL: {field} missing {not_added}", file=sys.stderr); ok = False
    for arg, field in EXACT_REPLACE_FIELDS.items():
        replacements = getattr(args, arg)
        if not replacements:
            continue
        effective = normalize_overlay_list_value(after.get(field))
        for replacement in replacements:
            old, new = (part.strip() for part in replacement.split("=>", 1))
            if old in effective:
                print(f"VERIFY FAIL: {field} retained stale value {old!r}", file=sys.stderr); ok = False
            if new not in effective:
                print(f"VERIFY FAIL: {field} missing replacement {new!r}", file=sys.stderr); ok = False
    if not ok:
        return 5
    print("\nWROTE + VERIFIED: effective overlay now reflects the union-preserving update.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
