#!/usr/bin/env python3
"""FL History Transcript-Mining — Finalize.

Reads the run ledger + per-source sidecar ``.annotated.md`` files, validates
all rows are in terminal states, and merges each ``verified`` row's
annotation block into the corresponding location in the original Desktop
source file. Idempotent: re-runs replace marker-wrapped blocks in place.

Implements U6 of ``docs/plans/2026-05-16-001-feat-fl-history-mining-skills-plan.md``.

Usage::

    python3 scripts/fl_mining_finalize.py \\
        --work-dir docs/audits/2026-05-16-fl-history-mining/ \\
        --originals-dir ~/Desktop/ \\
        [--dry-run] \\
        [--allow-needs-review]

The finalize step is operator-driven and runs ONCE after the pilot reaches
its go/no-go gate. Defaults to refusing if any ledger row is not in a
terminal state (``verified`` or ``needs_human_review``). With
``--allow-needs-review``, proceeds while surfacing ``needs_human_review``
rows as a stderr punch list.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Iterable

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from fl_mining_ledger import Ledger  # noqa: E402

LEDGER_FILENAME = "ledger.jsonl"
# extension_denied was removed from fl_row schema (only valid on extension_request
# resolution field) in plan-39fa929c code review fixes — eef8b46c.
TERMINAL_STATUSES = {"verified", "needs_human_review"}

# Exit codes
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NON_TERMINAL = 3
EXIT_SIDECAR_MISSING = 4
EXIT_SOURCE_MISSING = 5
EXIT_ANNOTATION_MISSING = 6
EXIT_MALFORMED_SIDECAR = 7


class MalformedSidecarError(ValueError):
    """Raised when a sidecar annotation block contains nested MINING markers,
    which would allow forged-attribution attacks at finalize time (P0 #4 from
    plan-39fa929c code review)."""

# Sidecar block delimiters
_BLOCK_START_TMPL = "<!-- {fl_id}-MINING-START -->"
_BLOCK_END_TMPL = "<!-- {fl_id}-MINING-END -->"

# Source file FL-entry pattern (matches ^- FL-NNNN at start of list item)
_FL_ENTRY_LINE_RE = re.compile(r"^-\s+FL-(\d+)\b")


_NESTED_MARKER_RE = re.compile(r"<!--\s*FL-\d+-MINING-(?:START|END)\s*-->")


def extract_annotation_blocks(sidecar_text: str) -> dict[str, str]:
    """Parse a sidecar into ``{fl_id: block_body}`` mapping.

    Block body is the text BETWEEN the START/END markers (markers excluded).
    Multiple blocks for the same FL-id: last-write-wins (idempotent miner).

    Raises ``MalformedSidecarError`` if any block body contains nested
    ``<!-- FL-NNNN-MINING-START -->`` or ``<!-- FL-NNNN-MINING-END -->`` markers
    — a forged-attribution attack surface (P0 #4 from plan-39fa929c code review:
    a malicious miner could inject a marker inside one block's body, causing
    finalize to attribute that text to a different FL-id).
    """
    blocks: dict[str, str] = {}
    pattern = re.compile(
        r"<!--\s+(FL-\d+)-MINING-START\s+-->\n?(.*?)\n?<!--\s+\1-MINING-END\s+-->",
        re.DOTALL,
    )
    for m in pattern.finditer(sidecar_text):
        fl_id = m.group(1)
        body = m.group(2).strip("\n")
        nested = _NESTED_MARKER_RE.search(body)
        if nested:
            raise MalformedSidecarError(
                f"nested mining marker inside {fl_id} block body: "
                f"{nested.group(0)!r} — refuse to merge"
            )
        blocks[fl_id] = body
    return blocks


def find_source_entry_line(source_lines: list[str], fl_id: str) -> int | None:
    """Return 0-based index of the line matching ``- FL-NNNN`` for fl_id."""
    target_num = int(fl_id.split("-")[1])
    for i, line in enumerate(source_lines):
        m = _FL_ENTRY_LINE_RE.match(line)
        if m and int(m.group(1)) == target_num:
            return i
    return None


def merge_block_into_source(
    source_lines: list[str],
    fl_id: str,
    block_body: str,
) -> list[str]:
    """Return new source_lines with ``fl_id``'s annotation block inserted /
    replaced immediately after the entry line. Idempotent (existing
    marker-wrapped block for ``fl_id`` is replaced, not duplicated).

    Scans for an existing block only within ``fl_id``'s slot — defined as the
    half-open range ``[entry_idx + 1, next_fl_entry_idx)``. This prevents two
    classes of bugs found in plan-39fa929c code review:

    - P1 #11 (anchor 100, 3-reviewer): the previous fixed 200-line scan window
      missed existing blocks for annotations longer than 200 lines, causing
      silent duplicate insertion on idempotent re-runs.
    - Testing gap #13: adjacent FL entries (``- FL-100\\n- FL-101\\n``) could
      collide if FL-100's annotation block was scanned past FL-101's entry line.
    """
    entry_idx = find_source_entry_line(source_lines, fl_id)
    if entry_idx is None:
        raise ValueError(f"{fl_id} not found in source")

    start_marker = _BLOCK_START_TMPL.format(fl_id=fl_id)
    end_marker = _BLOCK_END_TMPL.format(fl_id=fl_id)

    # Slot upper bound = the line index of the next ``- FL-NNNN`` entry, or EOF.
    next_entry_idx = len(source_lines)
    for j in range(entry_idx + 1, len(source_lines)):
        if _FL_ENTRY_LINE_RE.match(source_lines[j]):
            next_entry_idx = j
            break

    # Look for an existing block strictly inside this fl_id's slot.
    existing_start = None
    existing_end = None
    for j in range(entry_idx + 1, next_entry_idx):
        if existing_start is None and source_lines[j].rstrip("\n") == start_marker:
            existing_start = j
        elif existing_start is not None and source_lines[j].rstrip("\n") == end_marker:
            existing_end = j
            break

    new_block_lines = [
        start_marker + "\n",
        block_body + ("\n" if not block_body.endswith("\n") else ""),
        end_marker + "\n",
    ]

    if existing_start is not None and existing_end is not None:
        # replace existing block
        return (
            source_lines[:existing_start]
            + new_block_lines
            + source_lines[existing_end + 1 :]
        )

    # insert immediately after the entry line (handle EOF case: entry is the last line)
    return source_lines[: entry_idx + 1] + list(new_block_lines) + source_lines[entry_idx + 1 :]


def validate_terminal(
    rows: Iterable[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    """Partition fl_rows into (verified, needs_human_review, non_terminal).

    Returns (verified_rows, needs_review_rows, non_terminal_rows).
    """
    verified: list[dict] = []
    needs_review: list[dict] = []
    non_terminal: list[dict] = []
    for r in rows:
        if r.get("type") != "fl_row":
            continue
        status = r.get("status")
        if status == "verified":
            verified.append(r)
        elif status == "needs_human_review":
            needs_review.append(r)
        elif status in TERMINAL_STATUSES:
            # extension_denied — skip silently
            continue
        else:
            non_terminal.append(r)
    return verified, needs_review, non_terminal


def run_finalize(
    work_dir: Path,
    originals_dir: Path,
    *,
    dry_run: bool = False,
    allow_needs_review: bool = False,
    print_fn=print,
    err_fn=lambda *a, **kw: print(*a, file=sys.stderr, **kw),
) -> int:
    """Finalize-mode merge. Returns process exit code."""
    ledger_path = work_dir / LEDGER_FILENAME
    if not ledger_path.exists():
        err_fn(f"ERROR: ledger not found at {ledger_path}")
        return EXIT_USAGE

    led = Ledger(ledger_path)
    rows = led.read_all()
    verified, needs_review, non_terminal = validate_terminal(rows)

    if non_terminal:
        err_fn(
            f"ERROR: {len(non_terminal)} ledger row(s) are not in terminal "
            f"state; finalize refuses to proceed:"
        )
        for r in non_terminal:
            err_fn(f"  {r.get('fl_id')}: status={r.get('status')!r}")
        return EXIT_NON_TERMINAL

    if needs_review and not allow_needs_review:
        err_fn(
            f"ERROR: {len(needs_review)} ledger row(s) are in "
            f"needs_human_review; pass --allow-needs-review to proceed "
            f"(those rows will NOT be merged into originals; surfaced "
            f"as a punch list):"
        )
        for r in needs_review:
            err_fn(
                f"  {r.get('fl_id')}: verify_result="
                f"{r.get('verify_result', {})!r}"
            )
        return EXIT_NON_TERMINAL

    if needs_review and allow_needs_review:
        err_fn(
            f"PUNCH LIST: {len(needs_review)} row(s) in needs_human_review "
            f"(NOT merged into originals):"
        )
        for r in needs_review:
            err_fn(
                f"  {r.get('fl_id')}: verify_result="
                f"{r.get('verify_result', {})!r}"
            )

    # Group verified rows by source_file
    by_source: dict[str, list[dict]] = {}
    for r in verified:
        by_source.setdefault(r["source_file"], []).append(r)

    total_blocks_merged = 0
    for source_file, file_rows in by_source.items():
        sidecar_path = work_dir / f"{source_file}.annotated.md"
        if not sidecar_path.exists():
            err_fn(f"ERROR: sidecar not found at {sidecar_path}")
            return EXIT_SIDECAR_MISSING

        original_path = originals_dir / source_file
        if not original_path.exists():
            err_fn(f"ERROR: original source file not found at {original_path}")
            return EXIT_SOURCE_MISSING

        sidecar_text = sidecar_path.read_text(encoding="utf-8")
        try:
            blocks = extract_annotation_blocks(sidecar_text)
        except MalformedSidecarError as e:
            err_fn(f"ERROR: malformed sidecar {sidecar_path}: {e}")
            return EXIT_MALFORMED_SIDECAR

        source_lines = original_path.read_text(encoding="utf-8").splitlines(keepends=True)
        if source_lines and not source_lines[-1].endswith("\n"):
            source_lines[-1] = source_lines[-1] + "\n"

        merged_count = 0
        for r in file_rows:
            fl_id = r["fl_id"]
            body = blocks.get(fl_id)
            if body is None:
                err_fn(
                    f"ERROR: annotation block for {fl_id} not found in "
                    f"sidecar {sidecar_path}"
                )
                return EXIT_ANNOTATION_MISSING
            try:
                source_lines = merge_block_into_source(source_lines, fl_id, body)
            except ValueError as e:
                err_fn(f"ERROR: {e} in {original_path}")
                return EXIT_ANNOTATION_MISSING
            merged_count += 1

        if dry_run:
            print_fn(
                f"DRY-RUN: would merge {merged_count} annotation block(s) "
                f"into {original_path}"
            )
        else:
            original_path.write_text("".join(source_lines), encoding="utf-8")
            print_fn(
                f"merged {merged_count} annotation block(s) into {original_path}"
            )

        total_blocks_merged += merged_count

    if dry_run:
        print_fn(f"DRY-RUN summary: would merge {total_blocks_merged} block(s) total")
    else:
        print_fn(f"finalize summary: merged {total_blocks_merged} block(s) total")
    return EXIT_OK


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fl_mining_finalize.py",
        description=__doc__.split("\n\n")[0] if __doc__ else "Finalize",
    )
    parser.add_argument(
        "--work-dir",
        required=True,
        type=Path,
        help="Run work directory containing ledger.jsonl and sidecar files.",
    )
    parser.add_argument(
        "--originals-dir",
        type=Path,
        default=Path(os.path.expanduser("~/Desktop")),
        help="Directory holding the original operator source file(s) (default: ~/Desktop).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show proposed diffs without writing originals.",
    )
    parser.add_argument(
        "--allow-needs-review",
        action="store_true",
        help="Proceed even if some rows are in needs_human_review (those are skipped, surfaced as punch list).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return run_finalize(
        work_dir=args.work_dir,
        originals_dir=args.originals_dir,
        dry_run=args.dry_run,
        allow_needs_review=args.allow_needs_review,
    )


if __name__ == "__main__":
    sys.exit(main())
