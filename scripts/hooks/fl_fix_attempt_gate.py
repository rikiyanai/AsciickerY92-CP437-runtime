#!/usr/bin/env python3
"""
FL fix-attempt gate — pre-commit advisory by default.

Warns when commits modify repo task surfaces without a staged fix-attempt entry in
docs/FAILURE_LOG.md. Called by scripts/hooks/pre-commit.

Exit codes:
  0  Passed / advisory only (no repo task files staged, or fix-attempt present,
     or warn-only missing-entry path)
  1  Blocked in strict mode, or on internal git errors

Repo task trigger set:
  Any staged text/code/config/doc file in the active repo surface, except for
  explicit non-task exclusions such as vendor/, maintainer/, archive ledgers,
  generated/binary assets, and docs/FAILURE_LOG.md itself.

Exemptions (do NOT trigger this gate):
  vendor/, maintainer/, archive-only docs, generated/runtime directories,
  binary assets, and docs/FAILURE_LOG.md itself
  Any commit where docs/FAILURE_LOG.md is staged with "fix attempt" in the diff

Limitation:
  This gate verifies that *some* fix-attempt entry is staged — it does NOT
  verify that the entry belongs to the FL entry relevant to the staged code
  change. Citing the correct FL entry is a process obligation; see docs/agent/agents.md.
  This gate catches "forgot entirely," not "cited the wrong entry."

Modes:
  default   Warn-only advisory. This avoids blocking commits when the helper is a
            no-op because the matching fix-attempt is already present in the log.
  --strict  Restore hard-block behavior for automation or audits that still want it.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo task trigger set
# ---------------------------------------------------------------------------

_EXEMPT_PREFIXES = (
    "maintainer/",
    "vendor/",
    ".run/",
    ".worktrees/",
    ".git/",
    ".o_",
    ".d_",
    "docs/research/ascii/verification/archive/",
)
_EXEMPT_PATH_RE = re.compile(
    r"^(?:tests?/fixtures/|docs/templates/|docs/api/|docs/research/.+/archive/)"
)
_BINARY_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".mp4",
    ".mov",
    ".xp",
    ".wasm",
    ".a3d",
    ".akm",
    ".otf",
    ".ttf",
    ".woff",
    ".woff2",
)

_FL_PATH = "docs/FAILURE_LOG.md"
_FIX_ATTEMPT_PATTERN = re.compile(r'fix attempt', re.IGNORECASE)


def _emit_missing_fix_attempt_message(task_staged: list[str], *, fl_staged: bool, strict: bool) -> int:
    level = "BLOCKED" if strict else "WARNING"
    mode_hint = "strict mode" if strict else "duplicate-index advisory only"
    if not fl_staged:
        print(
            f"FL-FIX-ATTEMPT: {level} — repo task files staged without a fix-attempt entry.\n"
            f"  Staged task files: {', '.join(task_staged)}\n"
            f"\n"
            f"  Run: python3 scripts/analyze_failure_log.py fix-attempt FL-NNN \"what was changed\"\n"
            f"  Then: git add {_FL_PATH}\n"
            f"  Note: warn-only path enabled ({mode_hint}).\n"
        )
        return 1 if strict else 0

    print(
        f"FL-FIX-ATTEMPT: {level} — docs/FAILURE_LOG.md staged but diff contains no 'fix attempt' marker.\n"
        f"  Staged task files: {', '.join(task_staged)}\n"
        f"\n"
        f"  Run: python3 scripts/analyze_failure_log.py fix-attempt FL-NNN \"what was changed\"\n"
        f"  Then: git add {_FL_PATH}\n"
        f"  Note: warn-only path enabled ({mode_hint}).\n"
    )
    return 1 if strict else 0


def get_staged_files() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True, text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        print("FL-FIX-ATTEMPT: ERROR — git diff --cached timed out; cannot verify staged files",
              file=sys.stderr)
        sys.exit(1)
    if result.returncode != 0:
        print(f"FL-FIX-ATTEMPT: ERROR — git diff --cached failed (rc={result.returncode}); "
              "cannot verify staged files — failing closed", file=sys.stderr)
        sys.exit(1)
    return [f.strip() for f in result.stdout.splitlines() if f.strip()]


def is_repo_task_file(path: str) -> bool:
    norm = path.strip().lstrip("./")
    if not norm or _normalize_fl_path(norm) == _normalize_fl_path(_FL_PATH):
        return False
    if any(norm.startswith(prefix) for prefix in _EXEMPT_PREFIXES):
        return False
    if _EXEMPT_PATH_RE.match(norm):
        return False
    if norm.lower().endswith(_BINARY_SUFFIXES):
        return False
    return True


def _normalize_fl_path(path: str) -> str:
    """Normalize path for case-insensitive filesystem comparison."""
    return path.lower()


def get_staged_fl_diff() -> str:
    """Return the staged diff for docs/FAILURE_LOG.md (added/modified lines only)."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--", _FL_PATH],
            capture_output=True, text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        print("FL-FIX-ATTEMPT: ERROR — git diff --cached timed out", file=sys.stderr)
        sys.exit(1)
    if result.returncode != 0:
        print(f"FL-FIX-ATTEMPT: ERROR — git diff for docs/FAILURE_LOG.md failed (rc={result.returncode})",
              file=sys.stderr)
        sys.exit(1)
    return result.stdout


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    strict = "--strict" in args
    staged = get_staged_files()
    task_staged = [f for f in staged if is_repo_task_file(f)]

    if not task_staged:
        return 0  # No repo task files staged — gate passes

    # Check if docs/FAILURE_LOG.md is staged with a fix-attempt entry
    # Normalize for case-insensitive filesystems (macOS APFS)
    fl_staged = any(_normalize_fl_path(f) == _normalize_fl_path(_FL_PATH) for f in staged)
    if not fl_staged:
        return _emit_missing_fix_attempt_message(task_staged, fl_staged=False, strict=strict)

    # docs/FAILURE_LOG.md is staged — check for fix attempt substring in diff
    fl_diff = get_staged_fl_diff()
    added_lines = [line[1:] for line in fl_diff.splitlines() if line.startswith('+') and not line.startswith('+++')]
    added_text = '\n'.join(added_lines)

    if not _FIX_ATTEMPT_PATTERN.search(added_text):
        return _emit_missing_fix_attempt_message(task_staged, fl_staged=True, strict=strict)

    return 0  # Gate passed


if __name__ == "__main__":
    raise SystemExit(main())
