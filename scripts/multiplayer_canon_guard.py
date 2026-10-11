#!/usr/bin/env python3
"""Hard gate for the canon-doc rule.

This guard exists to prevent stale standalone handoff docs from becoming
parallel authority again. The filename is legacy; the guarded canon spec is now
repo-scoped while still retaining the multiplayer compatibility appendix.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


CANONICAL_SPEC_REL = Path("docs/plans/2026-03-22-multiplayer-canonical-spec.md")
FAILURE_LOG_REL = Path("docs/FAILURE_LOG.md")
ARCHIVE_REL = Path("docs/research/ascii/verification/archive/MULTIPLAYER_DOCS_ARCHIVE.md")
HANDOFF_DIR_REL = Path("docs/plans/handoffs")
FAILURE_LOG_EXACT_RE = re.compile(r"- Exact count: \*\*(\d+)\*\* events\.")
FAILURE_LOG_EVENT_RE = re.compile(r"^(\d+)\. `20\d\d-", re.MULTILINE)
FAILURE_LOG_COUNTER_UPDATE_RE = re.compile(
    r"process-failure complaint counter increased to `(\d+)`"
)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _check_failure_log_counter_integrity(text: str) -> tuple[list[str], list[str]]:
    violations: list[str] = []
    notes: list[str] = []

    exact_match = FAILURE_LOG_EXACT_RE.search(text)
    if not exact_match:
        violations.append("failure log missing exact complaint counter line")
        return violations, notes
    exact_count = int(exact_match.group(1))

    anchor = "Event counter (chronological):"
    if anchor not in text:
        violations.append("failure log missing complaint event counter section")
        return violations, notes
    after_anchor = text.split(anchor, 1)[1]
    stop_tokens = ("\n> **[", "\n### ", "\n## ")
    stop_positions = [after_anchor.find(token) for token in stop_tokens if after_anchor.find(token) != -1]
    event_block = after_anchor[: min(stop_positions)] if stop_positions else after_anchor

    event_numbers = [int(match.group(1)) for match in FAILURE_LOG_EVENT_RE.finditer(event_block)]
    if not event_numbers:
        violations.append("failure log complaint event counter has no numbered entries")
        return violations, notes

    expected = list(range(1, max(event_numbers) + 1))
    if event_numbers != expected:
        violations.append(
            "failure log complaint event counter is not a contiguous append-only tail"
        )
    highest_event = max(event_numbers)
    if exact_count != highest_event:
        violations.append(
            f"failure log exact complaint count {exact_count} != highest numbered event {highest_event}"
        )

    referenced_counts = [
        int(match.group(1)) for match in FAILURE_LOG_COUNTER_UPDATE_RE.finditer(text)
    ]
    if referenced_counts:
        max_referenced = max(referenced_counts)
        if max_referenced > highest_event:
            violations.append(
                f"failure log references complaint counter {max_referenced} beyond numbered tail {highest_event}"
            )
        else:
            notes.append(
                f"failure log complaint counter tail consistent through event {highest_event}"
            )
    else:
        notes.append("failure log has no later complaint-counter update references")

    return violations, notes


def check_multiplayer_canon(repo_root: Path) -> tuple[bool, list[str], list[str]]:
    repo_root = repo_root.resolve()
    violations: list[str] = []
    notes: list[str] = []

    canonical_spec = repo_root / CANONICAL_SPEC_REL
    failure_log = repo_root / FAILURE_LOG_REL
    archive = repo_root / ARCHIVE_REL
    handoff_dir = repo_root / HANDOFF_DIR_REL

    for label, path in (
        ("canonical spec", canonical_spec),
        ("failure log", failure_log),
        ("archive ledger", archive),
    ):
        if not path.exists():
            violations.append(f"missing {label}: {path}")
        else:
            notes.append(f"{label} present")

    if handoff_dir.exists():
        live_files = sorted(p.name for p in handoff_dir.iterdir() if p.is_file())
        if live_files:
            notes.append(
                "live handoff docs present under docs/plans/handoffs/: "
                + ", ".join(live_files)
            )
        else:
            notes.append("docs/plans/handoffs/ is empty")
    else:
        notes.append("docs/plans/handoffs/ missing (treated as retired)")

    spec_text = _read_text(canonical_spec)
    if spec_text:
        required_phrase = "Standalone multiplayer handoff docs are not authority"
        if required_phrase not in spec_text:
            violations.append("canonical spec missing standalone-handoff ban language")
        else:
            notes.append("canonical spec includes standalone-handoff ban")

    failure_log_text = _read_text(failure_log)
    if failure_log_text:
        counter_violations, counter_notes = _check_failure_log_counter_integrity(
            failure_log_text
        )
        violations.extend(counter_violations)
        notes.extend(counter_notes)

    ok = not violations
    return ok, violations, notes


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Check the canon-doc rule.")
    p.add_argument("--repo-root", default=".", help="Repository root to validate.")
    p.add_argument("--json", action="store_true", help="Emit JSON result.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).expanduser().resolve()
    ok, violations, notes = check_multiplayer_canon(repo_root)
    payload = {
        "ok": ok,
        "repo_root": str(repo_root),
        "violations": violations,
        "notes": notes,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        if ok:
            print("multiplayer-canon-guard: PASS")
            for note in notes:
                print(f"  {note}")
        else:
            print("multiplayer-canon-guard: BLOCKED")
            for violation in violations:
                print(f"  {violation}")

    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
