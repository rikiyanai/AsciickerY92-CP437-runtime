#!/usr/bin/env python3
"""FL-4895 delete-first helper: apply deletion steps to the worktree AND to HEAD blobs.

Why: other agents keep uncommitted edits in the same files. A deletion commit
must carry only its own hunks, built on HEAD, while the worktree gets the
same deletion on top of the foreign edits.

Usage: python3 scripts/fl4895_delete_step.py [--head-only|--sequence] step1.py [step2.py ...]
Each step module defines EDITS = {path: [op, ...]} and optional DELETE = [paths].
HEAD gets every listed step in order. By default, the worktree gets every step
only when their edited paths are disjoint. Use --sequence when earlier steps
are already in the worktree and only the final step should be applied there.
Overlapping modules without --sequence are refused instead of silently
committing a change the worktree does not contain.
ops: ("sub", old, new)       exact, must occur once
     ("cut", start, end)     remove from the line containing `start` up to (not
                             including) the line containing `end`
     ("re", pattern, repl, min_count)
Every op must apply to both HEAD and the worktree, or nothing is written.
Prints `<blob> <path> <base>` (base = the HEAD blob it was built on) for HEAD+all steps and `DEL <path>` for tracked deletions;
commit them from a temporary index (git update-index --cacheinfo / git rm --cached).
"""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def apply(text: str, ops, path: str, *, reject_already_applied: bool = False) -> str:
    for op in ops:
        if op[0] == "sub":
            _, old, new = op
            if reject_already_applied and new in text:
                raise AssertionError(
                    f"{path}: already applied; use --head-only to rebuild the blob"
                )
            n = text.count(old)
            assert n == 1, f"{path}: sub count {n}: {old[:70]!r}"
            text = text.replace(old, new)
        elif op[0] == "cut":
            _, start, end = op
            assert text.count(start) == 1, f"{path}: cut start count {text.count(start)}: {start[:70]!r}"
            s = text.index(start)
            s = text.rfind("\n", 0, s) + 1
            assert text.count(end, s) >= 1, f"{path}: cut end missing: {end[:70]!r}"
            e = text.index(end, s)
            e = text.rfind("\n", 0, e) + 1
            text = text[:s] + text[e:]
        elif op[0] == "re":
            _, pat, repl, minimum = op
            text, n = re.subn(pat, repl, text)
            assert n >= minimum, f"{path}: re {pat!r} matched {n}"
        else:
            raise SystemExit(f"unknown op {op[0]!r}")
    return text


def load(path):
    spec = importlib.util.spec_from_file_location("step", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def select_work_steps(steps, *, head_only: bool, sequence: bool):
    """Select the edits that may touch the existing worktree.

    HEAD always receives the full ordered sequence; this selector makes the
    older "only the last module" assumption explicit and testable.
    """
    if head_only:
        return []
    if sequence:
        return [steps[-1]]
    work_paths = [
        p for st in steps for p in (*st.EDITS, *getattr(st, "DELETE", []))
    ]
    if len(work_paths) != len(set(work_paths)):
        raise SystemExit(
            "multiple step modules overlap; use --sequence only when "
            "earlier modules are already applied, or run one module per call"
        )
    return list(steps)


def main(argv) -> int:
    head_only = bool(argv) and argv[0] == "--head-only"
    sequence = bool(argv) and argv[0] == "--sequence"
    if head_only or sequence:
        argv = argv[1:]
    steps = [load(p) for p in argv]
    if not steps:
        raise SystemExit("provide at least one step module")
    paths = []
    for st in steps:
        for p in st.EDITS:
            if p not in paths:
                paths.append(p)
    deleted = [p for st in steps for p in getattr(st, "DELETE", [])]

    # The historical multi-module form applied every module to HEAD but only
    # the last module to the worktree. That was safe only when the caller had
    # already applied every earlier module. Make that assumption explicit and
    # allow disjoint modules to behave in the unsurprising all-apply mode.
    work_steps = select_work_steps(steps, head_only=head_only, sequence=sequence)
    work_edits = {p: [] for p in paths}
    for st in work_steps:
        for p, ops in st.EDITS.items():
            work_edits[p].extend(ops)
    work_deleted = {p for st in work_steps for p in getattr(st, "DELETE", [])}
    planned = []
    for path in paths:
        full = ROOT / path
        head = subprocess.run(["git", "show", "HEAD:" + path], cwd=ROOT, capture_output=True, text=True)
        head_text = head.stdout if head.returncode == 0 else None
        base = subprocess.run(["git", "rev-parse", "HEAD:" + path], cwd=ROOT, capture_output=True,
                              text=True).stdout.strip() if head_text is not None else ""
        if head_text is not None:
            for st in steps:
                head_text = apply(head_text, st.EDITS.get(path, []), "HEAD:" + path)
        work = None
        if not head_only and work_edits[path]:
            work = apply(
                full.read_text(encoding="utf-8"),
                work_edits[path],
                path,
                reject_already_applied=True,
            )
        planned.append((path, full, head_text, work, base))
    for path, full, head_text, work, base in planned:
        if work is not None:
            full.write_text(work, encoding="utf-8")
        if head_text is not None:
            blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=ROOT, input=head_text,
                                  capture_output=True, text=True, check=True).stdout.strip()
            # Third column: the HEAD blob this was built on. Commit with
            # --blob PATH=BLOB@BASE; the committer refuses a moved HEAD.
            print(blob, path, base)
    for path in deleted:
        for p in (path, path + ".uid"):
            full = ROOT / p
            if full.exists() and not head_only and path in work_deleted:
                full.unlink()
            if subprocess.run(["git", "cat-file", "-e", "HEAD:" + p], cwd=ROOT,
                              capture_output=True).returncode == 0:
                print("DEL", p)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
