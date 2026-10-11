#!/usr/bin/env python3
"""Union-preserving FL overlay setter.

Closes the tooling gap that blocked FL-4057's unpark: `overlay --patch` is hard-disabled
(it replaced list fields and lost lineage — FL-3750/FL-1490) and `overlay --import-proposals`
is gap-fill-only (cannot override a populated ProofState or extend a populated list). The FL
overlay merge is per-key LAST-WRITE-WINS in file order (read_fl_overlay / FL-4023), and the
overlay block appends new rows at the END (latest), so a single appended row whose value for
each touched key is the FULL desired value wins cleanly.

This tool appends ONE such row:
  - scalar fields (ProofState, EpochStatus, Priority, Area, Category) are SET explicitly,
  - list fields (ComplaintRefs, CodeRefs, RQRefs, GitHubRefs, Kinds, Subsystems, TouchedFiles)
    are UNIONed with the existing effective value — existing entries are NEVER dropped.

Fail-closed: if the computed union for any list field would not be a superset of the existing
value, the tool aborts before writing. Dry-run by default; pass --write to append the row.

Usage:
  python3 scripts/fl_overlay_set.py --fl FL-4057 --proof-state RAW-OPEN-ACTIONABLE \
      --add-complaint-ref FL-4408,FL-4270,FL-4359,FL-4364 --add-rq-ref RQ-170 \
      --add-code-ref docs/plans/RUNTIME_GOAL.md --add-kind raw_open,unparked [--write]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from maintainer.lib.fl_config import CANONICAL_FAILURE_LOG  # noqa: E402
from maintainer.lib.failure_log import read_fl_overlay, fl_write_lock  # noqa: E402
from maintainer.lib.fl_overlay import normalize_overlay_list_value  # noqa: E402

_BLOCK_RE = re.compile(
    r"^## FL Metadata Overlay[ \t]*\n.*?^```jsonl[ \t]*\n(.*?)^```",
    re.MULTILINE | re.DOTALL,
)

SCALAR_FIELDS = {
    "proof_state": "ProofState",
    "epoch_status": "EpochStatus",
    "priority": "Priority",
    "area": "Area",
    "category": "Category",
}
LIST_FIELDS = {
    "add_complaint_ref": "ComplaintRefs",
    "add_code_ref": "CodeRefs",
    "add_rq_ref": "RQRefs",
    "add_github_ref": "GitHubRefs",
    "add_kind": "Kinds",
    "add_subsystem": "Subsystems",
    "add_touched_file": "TouchedFiles",
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
    for arg in LIST_FIELDS:
        ap.add_argument("--" + arg.replace("_", "-"), action="append", default=[],
                        help=f"add to {LIST_FIELDS[arg]} (csv or repeatable; union, never drops)")
    ap.add_argument("--write", action="store_true", help="append the row (default: dry-run)")
    ap.add_argument("--path", default=str(CANONICAL_FAILURE_LOG))
    args = ap.parse_args()

    path = Path(args.path)
    overlay = read_fl_overlay(path)
    current = overlay.get(args.fl)
    if current is None:
        print(f"FAIL: no existing overlay row for {args.fl} (use overlay --fill-defaults first)", file=sys.stderr)
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

    with fl_write_lock(path):
        content = path.read_text(encoding="utf-8")
        m = _BLOCK_RE.search(content)
        if not m:
            print("FAIL: overlay block not found", file=sys.stderr)
            return 4
        insert_pos = m.end(1)  # end of the JSONL rows group, before the closing fence
        line = json.dumps(new_row, separators=(",", ":")) + "\n"
        path.write_text(content[:insert_pos] + line + content[insert_pos:], encoding="utf-8")

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
        lost = [x for x in before if x not in eff]
        not_added = [x for x in additions if x not in eff]
        if lost:
            print(f"VERIFY FAIL: {field} lost {lost}", file=sys.stderr); ok = False
        if not_added:
            print(f"VERIFY FAIL: {field} missing {not_added}", file=sys.stderr); ok = False
    if not ok:
        return 5
    print("\nWROTE + VERIFIED: effective overlay now reflects the union-preserving update.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
