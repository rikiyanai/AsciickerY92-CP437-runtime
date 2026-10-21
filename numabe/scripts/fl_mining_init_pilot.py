#!/usr/bin/env python3
"""FL History Transcript-Mining — Pilot Init.

One-shot operator script that bootstraps the pilot run work directory.
Implements U3 of ``docs/plans/2026-05-16-001-feat-fl-history-mining-skills-plan.md``.

Pilot mode:
  - Copies ``bundle_refactor_fl_history_corrected.md`` from the operator's
    Desktop into the run work directory (operator originals stay untouched).
  - Parses the copy for ``^- FL-(\\d+)`` list-item entries (the canonical entry
    shape in the bundle-refactor history file).
  - Takes the 12 lowest-numbered distinct FL-NNNN ids.
  - Seeds the ledger with 12 ``pending`` ``fl_row`` rows.

Excluded from the parse:
  - HTML section markers like ``<!-- FL-NNNN-...-START -->``.
  - In-prose mentions like ``## 2026-05-16 Correction - FL-4049 ...``.
  - Anything not anchored at the start of a markdown list item.

Idempotency: refuses to overwrite an existing non-empty ledger.

Usage::

    python3 scripts/fl_mining_init_pilot.py \\
        --mode pilot \\
        --work-dir docs/audits/2026-05-16-fl-history-mining/

The full-run mode (``--mode full``) covering the 314-entry PROCESS FAILURES
file is deferred to a follow-up plan, gated on pilot U9 GO decision per
origin scope.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from fl_mining_ledger import Ledger  # noqa: E402

PILOT_SOURCE_FILENAME = "bundle_refactor_fl_history_corrected.md"
PILOT_TARGET_COUNT = 12
LEDGER_FILENAME = "ledger.jsonl"

# Matches "- FL-NNNN" at the start of a markdown list item (the canonical
# entry shape in the bundle-refactor history file). Excludes HTML section
# markers (which start with "<") and in-prose mentions (which never start
# with "- FL-").
_FL_ENTRY_LINE_RE = re.compile(r"^-\s+FL-(\d+)\b", re.MULTILINE)


# Exit codes
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_LEDGER_EXISTS = 3
EXIT_SOURCE_MISSING = 4
EXIT_NO_ENTRIES = 5


def extract_fl_ids(text: str) -> list[int]:
    """Extract distinct FL-NNNN numeric ids from ``- FL-NNNN`` list items.

    Returns numerics sorted ascending. Duplicates dropped.
    """
    seen: set[int] = set()
    for m in _FL_ENTRY_LINE_RE.finditer(text):
        seen.add(int(m.group(1)))
    return sorted(seen)


def seed_ledger(
    ledger_path: Path,
    fl_ids: list[int],
    source_file: str,
) -> int:
    """Write ``pending`` fl_row rows for each FL-id. Returns count written."""
    ledger = Ledger(ledger_path)
    for fid in fl_ids:
        ledger.write_row({
            "type": "fl_row",
            "fl_id": f"FL-{fid}",
            "source_file": source_file,
            "status": "pending",
            "last_update": "1970-01-01T00:00:00.000000Z",
        })
    return len(fl_ids)


def run_pilot(
    work_dir: Path,
    desktop_dir: Path,
    *,
    target_count: int = PILOT_TARGET_COUNT,
    print_fn=print,
) -> int:
    """Pilot-mode bootstrap. Returns process exit code."""
    source = desktop_dir / PILOT_SOURCE_FILENAME
    if not source.exists():
        print(
            f"ERROR: pilot source file not found: {source}",
            file=sys.stderr,
        )
        return EXIT_SOURCE_MISSING

    work_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = work_dir / LEDGER_FILENAME
    if ledger_path.exists() and ledger_path.stat().st_size > 0:
        print(
            f"ERROR: ledger already exists at {ledger_path}; "
            f"refusing to overwrite. Move or remove it before re-init.",
            file=sys.stderr,
        )
        return EXIT_LEDGER_EXISTS

    work_copy = work_dir / PILOT_SOURCE_FILENAME
    shutil.copy2(source, work_copy)
    print_fn(f"copied source: {source} -> {work_copy}")

    text = work_copy.read_text(encoding="utf-8")
    all_ids = extract_fl_ids(text)
    if not all_ids:
        print(
            f"ERROR: no FL-NNNN entries found in {work_copy}. "
            f"Expected ``- FL-NNNN`` list items.",
            file=sys.stderr,
        )
        return EXIT_NO_ENTRIES

    selected = all_ids[:target_count]
    if len(selected) < target_count:
        print(
            f"WARN: only {len(selected)} distinct FL-NNNN entries found "
            f"(target was {target_count}); seeding all of them.",
            file=sys.stderr,
        )

    written = seed_ledger(ledger_path, selected, source_file=PILOT_SOURCE_FILENAME)
    print_fn(f"seeded ledger: {ledger_path} with {written} pending rows")
    print_fn(
        f"FL-ids seeded: {', '.join('FL-' + str(i) for i in selected)}"
    )
    return EXIT_OK


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fl_mining_init_pilot.py",
        description=__doc__.split("\n\n")[0] if __doc__ else "Pilot init",
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=["pilot"],
        help="Bootstrap mode. Only 'pilot' is implemented; 'full' is deferred.",
    )
    parser.add_argument(
        "--work-dir",
        required=True,
        type=Path,
        help="Run work directory (e.g., docs/audits/2026-05-16-fl-history-mining/).",
    )
    parser.add_argument(
        "--desktop-dir",
        type=Path,
        default=Path(os.path.expanduser("~/Desktop")),
        help="Where the operator-curated source file(s) live (default: ~/Desktop).",
    )
    parser.add_argument(
        "--target-count",
        type=int,
        default=PILOT_TARGET_COUNT,
        help=f"Number of lowest-numbered FL-ids to seed (default: {PILOT_TARGET_COUNT}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.mode == "pilot":
        return run_pilot(
            work_dir=args.work_dir,
            desktop_dir=args.desktop_dir,
            target_count=args.target_count,
        )
    print(f"ERROR: mode {args.mode!r} not implemented", file=sys.stderr)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
