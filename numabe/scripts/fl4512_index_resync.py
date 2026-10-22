#!/usr/bin/env python3
"""FL-4512: resync shared-index entries left stale by temporary-index commits.

Lanes commit through a private index (scripts/fl4512_commit_owned.py), which
moves HEAD without touching the shared .git/index. A shared-index entry that
still holds an older committed blob of its path then stages a silent revert,
and the next plain `git commit` from that index (another agent's) undoes the
lane's work. This happened on 2026-09-28 (task-observer 0077, 0079).

Rule: an entry equal to the blob of its path in one of the last N commits
(but not HEAD) was never staged on purpose; it is set to HEAD's blob. An entry
matching no committed version was staged by someone and is left alone. Retries
while another process holds index.lock.

usage: fl4512_index_resync.py [--depth N] [--dry-run]
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("FL4512_REPO_ROOT", str(Path(__file__).resolve().parents[1]))).resolve()


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


LEDGER = "docs/FAILURE_LOG.md"


def set_entry(mode: str, blob: str, path: str) -> None:
    for attempt in range(20):
        done = subprocess.run(["git", "update-index", "--cacheinfo", f"{mode},{blob},{path}"],
                              cwd=ROOT, capture_output=True, text=True)
        if done.returncode == 0:
            break
        if "index.lock" not in done.stderr or attempt == 19:
            raise RuntimeError(f"update-index {path}: {done.stderr.strip()}")
        time.sleep(0.25)
    if index_entry(path) != f"{mode} {blob} {path}":
        raise RuntimeError(f"update-index {path}: index did not retain the requested entry")


def index_entry(path: str) -> str:
    """Return one shared-index entry exactly as Git reports it."""
    return git("ls-files", "-s", "--", path, check=False).strip()


def _head_blob(path: str) -> str:
    return git("rev-parse", "--verify", "-q", f"HEAD:{path}", check=False).strip()


def _entry_has_blob(entry: str, blob: str) -> bool:
    fields = entry.split()
    return len(fields) >= 2 and fields[1] == blob


def reset_path_if_unchanged(path: str, before: str) -> str:
    """Reset one path only when the shared index still has its pre-commit entry.

    A changed entry belongs to a concurrent staging operation and is left alone.
    A reset is verified against the new HEAD and retried when another process
    temporarily owns ``index.lock``. The caller can therefore fail loudly when
    its own stale entry was not repaired instead of relying on a best-effort
    ``git reset``.
    """
    expected_blob = _head_blob(path)
    for attempt in range(20):
        current = index_entry(path)
        if current != before:
            return "foreign"
        done = subprocess.run(
            ["git", "reset", "-q", "--", path],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if done.returncode:
            if "index.lock" in done.stderr and attempt < 19:
                time.sleep(0.25)
                continue
            raise RuntimeError(f"reset {path}: {done.stderr.strip()}")
        after = index_entry(path)
        if (expected_blob and _entry_has_blob(after, expected_blob)) or (
            not expected_blob and not after
        ):
            return "reset"
        # A reset that did not establish the current HEAD entry is not a
        # successful repair. Re-read the entry before trying again so a sibling
        # stage is treated as foreign rather than overwritten blindly.
        if after != before:
            return "foreign"
    raise RuntimeError(f"reset {path}: stale shared-index entry remains after retries")


def resync_committed_paths(before_entries: dict[str, str]) -> list[str]:
    """Repair committed paths whose shared-index entries stayed at the old HEAD.

    ``before_entries`` is captured before a temporary-index commit. This
    identity check is stricter than comparing only against a parent blob: a
    sibling may have staged a different blob after the commit and must retain
    that work. Every repaired path is checked against the new HEAD.
    """
    repaired = []
    for path, before in before_entries.items():
        outcome = reset_path_if_unchanged(path, before)
        if outcome == "reset":
            repaired.append(path)
    return repaired


def rebase_ledger(staged_text: str, head_text: str) -> tuple[str, int]:
    """HEAD's ledger plus every staged fix-attempt block HEAD lacks, placed at
    the end of the same FL section. Nothing in HEAD is removed."""
    new = head_text
    blocks = staged_text.split("\n#### Fix attempt")
    head_blocks = {("#### Fix attempt" + tail.split("\n### ")[0]).strip()
                   for tail in head_text.split("\n#### Fix attempt")[1:]}
    added = 0
    for index, tail in enumerate(blocks[1:], 1):
        block = ("#### Fix attempt" + tail.split("\n### ")[0]).rstrip("\n") + "\n"
        if block.strip() in head_blocks:
            continue
        before = "\n#### Fix attempt".join(blocks[:index])
        section = before[before.rfind("\n### FL-") + 1:].split("\n", 1)[0]
        if section not in new:
            raise RuntimeError(f"ledger section missing in HEAD: {section[:60]}")
        start = new.index(section)
        end = new.find("\n### FL-", start + 5)
        end = len(new) if end < 0 else end + 1
        new = new[:end].rstrip("\n") + "\n\n" + block + "\n" + new[end:]
        added += 1
    return new, added


def resync(depth: int, dry_run: bool) -> list[str]:
    commits = git("rev-list", f"-{depth}", "HEAD").split()
    changed = []
    for path in git("diff", "--cached", "--name-only").split():
        entry = git("ls-files", "-s", "--", path).split()
        if not entry:
            continue
        mode, staged = entry[0], entry[1]
        head_blob = _head_blob(path)
        if not head_blob or staged == head_blob:
            continue
        if path == LEDGER:
            # Staged ledger entries that HEAD lacks are someone's pending
            # records; lines HEAD has but the stage lacks are a stale base.
            staged_text = git("cat-file", "-p", staged)
            head_text = git("cat-file", "-p", head_blob)
            staged_lines = set(staged_text.splitlines())
            missing = [line for line in head_text.splitlines()
                       if line.strip() and line not in staged_lines]
            if not missing:
                continue
            new, added = rebase_ledger(staged_text, head_text)
            if not dry_run:
                blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=ROOT, input=new,
                                      capture_output=True, text=True, check=True).stdout.strip()
                set_entry(mode, blob, path)
            changed.append(f"{path} (rebuilt on HEAD with {added} staged-only blocks)")
            continue
        for commit in commits[1:]:
            old = git("rev-parse", "--verify", "-q", f"{commit}:{path}", check=False).strip()
            if old == staged:
                if not dry_run:
                    set_entry(mode, head_blob, path)
                changed.append(f"{path} (stale copy of {commit[:9]})")
                break
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--depth", type=int, default=200)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    for line in resync(args.depth, args.dry_run):
        print(("would reset " if args.dry_run else "reset ") + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
