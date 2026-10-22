#!/usr/bin/env python3
"""FL-4582 — relocate the overlay JSONL out of FAILURE_LOG.md into sharded parts.

History:
  v1 (2026-07-12): moved the ~93 MB inline ```jsonl block out of
  docs/FAILURE_LOG.md into a single sidecar docs/FAILURE_LOG.overlay/.
  v2 (2026-07-12, this revision): splits that sidecar into sharded parts under
  docs/FAILURE_LOG.overlay/part-NNNN.jsonl, each kept under 10 MiB, so that
  appends only mutate the active (highest) part and old parts are frozen (never
  repushed). Each part stays well under GitHub's 50 MiB warning.

This tool is idempotent and handles any starting state:
  - sharded parts already present  -> no-op (report sizes).
  - v1 monolithic sidecar present  -> split into parts, delete the monolith.
  - inline block still in the .md -> extract to parts, replace with pointer.

Usage:
    python3 scripts/fl_overlay_migrate_sidecar.py            # dry-run summary
    python3 scripts/fl_overlay_migrate_sidecar.py --write     # perform the move
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from maintainer.lib.failure_log import (  # noqa: E402
    _FL_OVERLAY_BLOCK_RE,
    _overlay_dir,
    _overlay_sidecar_path,
    read_overlay_raw_rows,
    rewrite_overlay_rows,
)

FL_PATH = REPO_ROOT / "docs" / "FAILURE_LOG.md"
OVERLAY_DIR = _overlay_dir(FL_PATH)
MONOLITH = _overlay_sidecar_path(FL_PATH)

_SECTION_RE = re.compile(
    r"^## FL Metadata Overlay[ \t]*\n.*?^```jsonl[ \t]*\n.*?^```[ \t]*\n?",
    re.MULTILINE | re.DOTALL,
)

POINTER_NOTE = """## FL Metadata Overlay

Machine-owned append-only overlay rows live in the sharded sidecar directory
`docs/FAILURE_LOG.overlay/` (FL-4582, 2026-07-12) as `part-NNNN.jsonl`, each
kept under 10 MiB so only the active (highest) part is mutated by an append
and old parts are never repushed. The prose ledger keeps narrative only; this
section is a pointer, not the data store.

Read/write contract: `scripts/maintainer/lib/failure_log.py`
(`read_fl_overlay`, `append_overlay_rows`, `rewrite_overlay_rows`,
`iter_overlay_rows_with_lines`). Query via `scripts/analyze_failure_log.py`
or `scripts/analyze_runs.py fl ...`.
"""


def _raw_lines_from_inline(content: str) -> list[str]:
    block = _FL_OVERLAY_BLOCK_RE.search(content)
    if not block:
        return []
    return [ln for ln in block.group(1).splitlines() if ln.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="perform the migration (default: dry-run)")
    args = ap.parse_args()

    if not FL_PATH.exists():
        print(f"error: {FL_PATH} not found", file=sys.stderr)
        return 1

    parts = sorted(OVERLAY_DIR.glob("part-*.jsonl")) if OVERLAY_DIR.exists() else []
    parts.sort(key=lambda p: int(p.stem.split("-")[1]))
    content = FL_PATH.read_text(encoding="utf-8")
    # Detect a REAL overlay section only in the preamble (before the first entry),
    # so an unrelated ```jsonl fence deep in an FL entry is not mistaken for it.
    first_entry = content.find("\n### FL-")
    preamble = content[:first_entry] if first_entry != -1 else content
    inline_lines = _raw_lines_from_inline(preamble)
    inline_present = bool(inline_lines)

    print("=== FL-4582 overlay sharding migration ===")
    print(f"  overlay dir:          {OVERLAY_DIR}")
    print(f"  existing parts:       {len(parts)}")
    if parts:
        for p in parts:
            print(f"    {p.name}: {p.stat().st_size:,} bytes")
    print(f"  v1 monolith present:  {MONOLITH.exists()}"
          + (f" ({MONOLITH.stat().st_size:,} bytes)" if MONOLITH.exists() else ""))
    print(f"  inline block present: {inline_present} ({len(inline_lines)} rows)")

    if parts and not inline_present and not MONOLITH.exists() and not args.write:
        print("\nAlready sharded — re-run with --write to re-emit under the current cap.")
        return 0

    if not args.write:
        print("\nDRY-RUN — re-run with --write to perform the migration.")
        return 0

    # 1) Gather all raw rows from whichever source has them.
    rows: list[dict] = []
    if parts:
        rows = read_overlay_raw_rows(FL_PATH)
    elif MONOLITH.exists():
        for ln in MONOLITH.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if not s:
                continue
            try:
                r = json.loads(s)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(r, dict):
                rows.append(r)
    elif inline_present:
        for ln in inline_lines:
            try:
                r = json.loads(ln)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(r, dict):
                rows.append(r)
    else:
        print("\nNo overlay rows found anywhere — nothing to migrate.")
        return 0

    # 2) Emit rows into fresh sharded parts (each under the cap).
    OVERLAY_DIR.mkdir(parents=True, exist_ok=True)
    rewrite_overlay_rows(FL_PATH, rows)

    # 3) Remove the v1 monolith if it exists.
    if MONOLITH.exists():
        MONOLITH.unlink()

    # 4) Update the .md pointer note (safe literal swap; never a greedy span).
    #    The v1 pointer note references the monolith file; v2 points at the
    #    sharded directory. We replace only that exact note text -- the rest of
    #    the prose ledger is untouched.
    V1_NOTE = ("## FL Metadata Overlay\n\n"
        "Machine-owned append-only overlay rows live in the sidecar file\n"
        "`docs/FAILURE_LOG.overlay/` (FL-4582, 2026-07-12). The prose ledger\n"
        "keeps narrative only; this section is a pointer, not the data store.\n\n"
        "Read/write contract: `scripts/maintainer/lib/failure_log.py`\n"
        "(`read_fl_overlay`, `append_overlay_rows`, `rewrite_overlay_rows`,\n"
        "`iter_overlay_rows_with_lines`). Query via `scripts/analyze_failure_log.py`\n"
        "or `scripts/analyze_runs.py fl ...`.\n")
    if V1_NOTE in content:
        FL_PATH.write_text(content.replace(V1_NOTE, POINTER_NOTE, 1), encoding="utf-8")
    elif POINTER_NOTE not in content:
        # No recognized pointer note and not already v2: leave the prose alone
        # (do not risk a greedy regex over the ledger body).
        print("  note: .md pointer note not recognized; left untouched")

    # Report.
    new_parts = sorted(OVERLAY_DIR.glob("part-*.jsonl"))
    new_parts.sort(key=lambda p: int(p.stem.split("-")[1]))
    print("\nDONE.")
    print(f"  parts written: {len(new_parts)}")
    total = 0
    for p in new_parts:
        sz = p.stat().st_size
        total += sz
        print(f"    {p.name}: {sz:,} bytes ({sz / 1e6:.1f} MB)")
    print(f"  total overlay bytes: {total:,}")
    print(f"  monolith removed:     {not MONOLITH.exists()}")
    print(f"  FAILURE_LOG.md size:  {FL_PATH.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
