#!/usr/bin/env python3
"""
Git workflow guardrails for branch/stash hygiene.

Commands:
  audit         - Print current safety state.
  session-end   - Enforce end-of-session checklist.
  switch <ref>  - Safe wrapper around `git switch` (blocks dirty tree).
  restore <p>   - Safe wrapper around `git restore -- <paths...>`.
  pull [args]   - Safe wrapper around `git pull --ff-only`.
  tag <topic>   - Create annotated safety tag (safety/YYYY-MM-DD-topic).
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path
from pathlib import PurePosixPath


REPO_ROOT = Path(__file__).resolve().parents[1]
PROTECTED_RESTORE_PATHS = (
    "docs/FAILURE_LOG.md",
    "docs/plans/2026-03-22-multiplayer-canonical-spec.md",
    "docs/multiplayer-vps-regression-ledger.md",
)


def _run(cmd: list[str], check: bool = True) -> str:
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    if check and proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip() or f"Command failed: {' '.join(cmd)}")
    return proc.stdout.strip()


def _git(*args: str, check: bool = True) -> str:
    return _run(["git", *args], check=check)


def _porcelain_lines() -> list[str]:
    out = _git("status", "--porcelain=v1", check=True)
    return [line for line in out.splitlines() if line.strip()]


def _tracked_changed_paths() -> list[str]:
    paths: list[str] = []
    for line in _porcelain_lines():
        if line.startswith("?? "):
            continue
        path = line[3:].strip()
        if path:
            paths.append(path)
    return paths


def _tracked_changes_count() -> int:
    return sum(1 for line in _porcelain_lines() if not line.startswith("?? "))


def _untracked_count() -> int:
    return sum(1 for line in _porcelain_lines() if line.startswith("?? "))


def _stash_count() -> int:
    out = _git("stash", "list", check=True)
    return len([line for line in out.splitlines() if line.strip()])


def _current_branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD", check=True)


def _worktree_count() -> int:
    out = _git("worktree", "list", "--porcelain", check=True)
    return sum(1 for line in out.splitlines() if line.startswith("worktree "))


def _branches_ahead_of_main() -> list[tuple[str, int, int]]:
    out = _git("for-each-ref", "refs/heads", "--format=%(refname:short)", check=True)
    branches = [b.strip() for b in out.splitlines() if b.strip()]
    if "main" not in branches:
        return []
    rows: list[tuple[str, int, int]] = []
    for branch in branches:
        if branch == "main":
            continue
        counts = _git("rev-list", "--left-right", "--count", f"main...{branch}", check=True)
        behind_str, ahead_str = counts.split()
        behind = int(behind_str)
        ahead = int(ahead_str)
        if ahead > 0:
            rows.append((branch, ahead, behind))
    return rows


def _require_clean_tree_for(op_name: str) -> None:
    tracked_paths = _tracked_changed_paths()
    tracked = len(tracked_paths)
    if tracked > 0:
        fl_note = ""
        if "docs/FAILURE_LOG.md" in tracked_paths:
            fl_note = " docs/FAILURE_LOG.md is dirty; switching branches risks dropping or conflicting with those entries."
        raise RuntimeError(
            f"{op_name} blocked: working tree has tracked changes ({tracked}). "
            f"Commit or stash first.{fl_note}"
        )


def _normalize_rel_pathspec(pathspec: str) -> PurePosixPath:
    text = (pathspec or "").strip()
    if text.startswith("./"):
        text = text[2:]
    return PurePosixPath(text or ".")


def _protected_restore_targets(pathspecs: list[str]) -> list[str]:
    protected = [PurePosixPath(p) for p in PROTECTED_RESTORE_PATHS]
    hits: list[str] = []
    for raw in pathspecs:
        norm = _normalize_rel_pathspec(raw)
        norm_text = norm.as_posix()
        if norm_text in {"", "."}:
            hits.append(raw)
            continue
        for target in protected:
            target_text = target.as_posix()
            if norm == target:
                hits.append(raw)
                break
            if target_text.startswith(norm_text.rstrip("/") + "/"):
                hits.append(raw)
                break
    return hits


def cmd_audit(_: argparse.Namespace) -> int:
    branch = _current_branch()
    tracked = _tracked_changes_count()
    untracked = _untracked_count()
    stashes = _stash_count()
    worktrees = _worktree_count()
    ahead_rows = _branches_ahead_of_main()

    print(f"branch: {branch}")
    print(f"tracked_changes: {tracked}")
    print(f"untracked_files: {untracked}")
    print(f"stashes: {stashes}")
    print(f"worktrees: {worktrees}")

    if ahead_rows:
        print("branches_ahead_of_main:")
        for name, ahead, behind in ahead_rows:
            print(f"  - {name}: ahead {ahead}, behind {behind}")
    else:
        print("branches_ahead_of_main: none")

    violations: list[str] = []
    if tracked > 0:
        violations.append("tracked changes present")
    if stashes > 0:
        violations.append("stash count > 0")
    if len(ahead_rows) > 1:
        violations.append("more than one branch is ahead of main")
    if worktrees > 1:
        violations.append("multiple worktrees active")

    if violations:
        print("guardrail_status: BLOCKED")
        for v in violations:
            print(f"  - {v}")
        return 1

    print("guardrail_status: OK")
    return 0


def cmd_session_end(_: argparse.Namespace) -> int:
    rc = cmd_audit(argparse.Namespace())
    if rc != 0:
        print("session_end: FAIL")
        return rc
    print("session_end: PASS")
    return 0


def cmd_switch(args: argparse.Namespace) -> int:
    _require_clean_tree_for("switch")
    _git("switch", args.ref, check=True)
    print(f"switched_to: {_current_branch()}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    if not args.paths:
        raise RuntimeError("restore blocked: pass at least one explicit path.")
    protected_hits = _protected_restore_targets(args.paths)
    if protected_hits:
        joined = ", ".join(protected_hits)
        raise RuntimeError(
            "restore blocked: requested pathspec touches protected canon docs "
            f"({joined}). Raw restore of docs/FAILURE_LOG.md, the canon spec, and "
            "the multiplayer regression ledger is forbidden."
        )
    _git("restore", "--", *args.paths, check=True)
    print("restore_status: OK")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    _require_clean_tree_for("pull")
    cmd = ["git", "pull", "--ff-only", *args.pull_args]
    _run(cmd, check=True)
    print("pull_status: OK")
    return 0


def cmd_tag(args: argparse.Namespace) -> int:
    topic = re.sub(r"[^a-z0-9._-]+", "-", args.topic.strip().lower()).strip("-")
    if not topic:
        raise RuntimeError("Tag topic cannot be empty after sanitization.")
    today = dt.date.today().isoformat()
    tag = f"safety/{today}-{topic}"
    msg = args.message or f"Safety snapshot before risky operation: {topic}"
    _git("tag", "-a", tag, "-m", msg, check=True)
    print(f"created_tag: {tag}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Git guardrails for safe workflow.")
    sub = p.add_subparsers(dest="cmd", required=True)

    audit = sub.add_parser("audit", help="Print guardrail safety status.")
    audit.set_defaults(func=cmd_audit)

    session = sub.add_parser("session-end", help="Enforce end-of-session checklist.")
    session.set_defaults(func=cmd_session_end)

    sw = sub.add_parser("switch", help="Safe wrapper for `git switch`.")
    sw.add_argument("ref", help="Branch or ref to switch to.")
    sw.set_defaults(func=cmd_switch)

    restore = sub.add_parser("restore", help="Safe wrapper for `git restore -- <paths...>`.")
    restore.add_argument("paths", nargs="+", help="One or more explicit paths to restore.")
    restore.set_defaults(func=cmd_restore)

    pull = sub.add_parser("pull", help="Safe wrapper for `git pull --ff-only`.")
    pull.add_argument("pull_args", nargs="*", help="Optional args passed to git pull.")
    pull.set_defaults(func=cmd_pull)

    tag = sub.add_parser("tag", help="Create safety tag.")
    tag.add_argument("topic", help="Topic slug for tag name.")
    tag.add_argument("--message", help="Optional custom tag message.")
    tag.set_defaults(func=cmd_tag)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
