#!/usr/bin/env python3
"""
Block commits that increase the total staged code line count above a locked ceiling.

The ceiling is stored in a JSON config file. Each config may define an
`include_globs` list to restrict counting to a named corpus of files.
Regenerate it only with explicit user approval when the repo should allow
a higher code-line total.
"""

from __future__ import annotations

import argparse
import fnmatch as _fnmatch
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


CODE_SUFFIXES = (
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hh",
    ".hpp",
    ".m",
    ".mm",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".py",
    ".sh",
    ".zsh",
    ".bash",
    ".lua",
    ".html",
    ".css",
)

HOOK_FILENAMES = {
    "pre-commit",
    "pre-push",
    "pre-rebase",
    "commit-msg",
    "prepare-commit-msg",
    "post-commit",
    "post-checkout",
    "post-merge",
    "post-rewrite",
}


def git_output(repo_root: Path, *args: str, allow_fail: bool = False) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        if allow_fail:
            return ""
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def is_code_path(path_str: str) -> bool:
    path = PurePosixPath(path_str)
    if path.name in HOOK_FILENAMES:
        return True
    return path.suffix.lower() in CODE_SUFFIXES


def matches_corpus(path_str: str, include_globs: list[str]) -> bool:
    """Return True if path_str matches any pattern in include_globs.

    Pattern rules:
      - No slash → match basename only (e.g. "game.cpp" matches "game.cpp" anywhere)
      - Ends with /** → match all files under that directory prefix
      - Otherwise → fnmatch against the full posix path
    """
    if not include_globs:
        return True
    name = PurePosixPath(path_str).name
    for pat in include_globs:
        if "/" not in pat:
            if _fnmatch.fnmatch(name, pat):
                return True
        elif pat.endswith("/**"):
            prefix = pat[:-3]
            if path_str.startswith(prefix + "/") or path_str == prefix:
                return True
        else:
            if _fnmatch.fnmatch(path_str, pat):
                return True
    return False


def comment_mode_for_path(path_str: str) -> str:
    path = PurePosixPath(path_str)
    suffix = path.suffix.lower()
    if path.name in HOOK_FILENAMES or suffix in {".py", ".sh", ".zsh", ".bash"}:
        return "hash"
    if suffix == ".lua":
        return "lua"
    if suffix == ".html":
        return "html"
    if suffix in {
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".h",
        ".hh",
        ".hpp",
        ".m",
        ".mm",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".css",
    }:
        return "c_like"
    return "plain"


def count_effective_code_lines(text: str, path_str: str) -> int:
    mode = comment_mode_for_path(path_str)
    total = 0
    in_block = False
    block_start = ""
    block_end = ""

    if mode == "c_like":
        block_start, block_end = "/*", "*/"
    elif mode == "html":
        block_start, block_end = "<!--", "-->"

    for idx, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line
        while True:
            stripped = line.lstrip()
            if in_block:
                end_idx = stripped.find(block_end)
                if end_idx < 0:
                    line = ""
                    break
                stripped = stripped[end_idx + len(block_end) :]
                line = stripped
                in_block = False
                continue

            if not stripped:
                line = ""
                break

            if mode == "hash":
                if idx == 1 and stripped.startswith("#!"):
                    total += 1
                    line = ""
                    break
                if stripped.startswith("#"):
                    line = ""
                    break
                total += 1
                line = ""
                break

            if mode == "lua":
                if stripped.startswith("--"):
                    line = ""
                    break
                total += 1
                line = ""
                break

            if mode in {"c_like", "html"}:
                if mode == "c_like" and stripped.startswith("//"):
                    line = ""
                    break
                if stripped.startswith(block_start):
                    end_idx = stripped.find(block_end, len(block_start))
                    if end_idx < 0:
                        in_block = True
                        line = ""
                        break
                    line = stripped[end_idx + len(block_end) :]
                    continue
                total += 1
                line = ""
                break

            total += 1
            line = ""
            break

    return total


def list_index_code_paths(repo_root: Path, include_globs: list[str]) -> list[str]:
    raw = git_output(repo_root, "ls-files", "--cached")
    return sorted(
        path for path in raw.splitlines()
        if path and is_code_path(path) and matches_corpus(path, include_globs)
    )


def list_head_code_paths(repo_root: Path, include_globs: list[str]) -> list[str]:
    raw = git_output(repo_root, "ls-tree", "-r", "--name-only", "HEAD")
    return sorted(
        path for path in raw.splitlines()
        if path and is_code_path(path) and matches_corpus(path, include_globs)
    )


def index_file_lines(repo_root: Path, relpath: str) -> int:
    text = git_output(repo_root, "show", f":{relpath}", allow_fail=True)
    return count_effective_code_lines(text, relpath)


def head_file_lines(repo_root: Path, relpath: str) -> int:
    text = git_output(repo_root, "show", f"HEAD:{relpath}", allow_fail=True)
    return count_effective_code_lines(text, relpath)


def total_index_code_lines(
    repo_root: Path, include_globs: list[str]
) -> tuple[int, dict[str, int]]:
    per_file: dict[str, int] = {}
    total = 0
    for relpath in list_index_code_paths(repo_root, include_globs):
        count = index_file_lines(repo_root, relpath)
        per_file[relpath] = count
        total += count
    return total, per_file


def total_head_code_lines(
    repo_root: Path, include_globs: list[str]
) -> tuple[int, dict[str, int]]:
    per_file: dict[str, int] = {}
    total = 0
    for relpath in list_head_code_paths(repo_root, include_globs):
        count = head_file_lines(repo_root, relpath)
        per_file[relpath] = count
        total += count
    return total, per_file


def staged_code_paths(repo_root: Path, include_globs: list[str]) -> list[str]:
    raw = git_output(repo_root, "diff", "--cached", "--name-only", "--diff-filter=ACMR")
    return sorted(
        path for path in raw.splitlines()
        if path and is_code_path(path) and matches_corpus(path, include_globs)
    )


def write_baseline(repo_root: Path, baseline_path: Path) -> int:
    existing: dict = {}
    if baseline_path.exists():
        existing = json.loads(baseline_path.read_text(encoding="utf-8"))
    include_globs: list[str] = existing.get("include_globs", [])

    total, _ = total_index_code_lines(repo_root, include_globs)
    head = git_output(repo_root, "rev-parse", "HEAD").strip()
    corpus_label = existing.get("corpus_label", "all")
    payload = {
        "corpus_label": corpus_label,
        "count_mode": "non_comment_code_lines",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "head_commit": head,
        "include_globs": include_globs,
        "max_code_lines": total,
        "max_staged_delta": existing.get("max_staged_delta", 0),
        "note": existing.get("note", "Raise only with explicit user approval."),
    }
    baseline_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    corpus_label = payload["corpus_label"]
    print(f"code-line-ceiling({corpus_label}): wrote baseline {total} -> {baseline_path}")
    return 0


def check_baseline(repo_root: Path, baseline_path: Path) -> int:
    if not baseline_path.exists():
        print(
            f"pre-commit(code-lines): BLOCKED - missing baseline file: {baseline_path}"
        )
        return 1

    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    max_code_lines = int(payload["max_code_lines"])
    max_staged_delta = int(payload.get("max_staged_delta", 0))
    include_globs: list[str] = payload.get("include_globs", [])
    corpus_label: str = payload.get("corpus_label", "all")

    staged_total, staged_per_file = total_index_code_lines(repo_root, include_globs)
    head_total, head_per_file = total_head_code_lines(repo_root, include_globs)
    staged_delta = staged_total - head_total

    ok = False
    if staged_total <= max_code_lines:
        if max_staged_delta >= 0:
            ok = staged_delta <= max_staged_delta
        else:
            ok = staged_delta >= max_staged_delta

    if ok:
        print(
            f"pre-commit(code-lines/{corpus_label}): OK "
            f"(staged={staged_total}, ceiling={max_code_lines}, "
            f"head={head_total}, delta={staged_delta}, delta_ceiling={max_staged_delta})"
        )
        return 0

    print(f"pre-commit(code-lines/{corpus_label}): BLOCKED")
    print(f"  corpus:            {corpus_label}")
    print(f"  staged code lines: {staged_total}")
    print(f"  locked ceiling:    {max_code_lines}")
    print(f"  total over by:     {staged_total - max_code_lines}")
    print(f"  staged delta:      {staged_delta}")
    print(f"  delta ceiling:     {max_staged_delta}")
    print("")
    print("  staged code deltas:")

    touched = staged_code_paths(repo_root, include_globs)
    touched_deltas: list[tuple[int, str, int, int]] = []
    for relpath in touched:
        head_count = head_per_file.get(relpath, 0)
        staged_count = staged_per_file.get(relpath, 0)
        touched_deltas.append(
            (staged_count - head_count, relpath, head_count, staged_count)
        )

    touched_deltas.sort(key=lambda item: (-item[0], item[1]))
    for diff, relpath, head_count, staged_count in touched_deltas[:20]:
        sign = "+" if diff >= 0 else ""
        print(f"    {sign}{diff:>4}  {relpath}  ({head_count} -> {staged_count})")

    print("")
    print("  HOW TO FIX:")
    print("    use /simplify skill to find and remove dead code")
    print("    check: crucial positions, shared modules, snapshot paths")
    print("    server-authoritative rule: server owns ALL gameplay state;")
    print("      client sends intent only — never writes back computed state")
    print("    AIM FOR SIMPLICITY. NO ADDING NEW SURFACES.")
    print("")
    print("  patterns to eliminate:")
    print("    - always-true guards wrapping logic")
    print("    - dead variables / fields with no readers")
    print("    - duplicate acceptance-policy or stale-guard logic")
    print("    - functions that exist for one caller and add no clarity")
    print("    - client-local state gating server snapshot application")
    print("")
    print("  before adding anything: does the server already own this?")
    print("  before keeping anything: is it actually called / read?")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".", help="repo/worktree root")
    parser.add_argument(
        "--baseline-file",
        default="scripts/hooks/code_line_ceiling_game.json",
        help="path to locked ceiling json, relative to repo root",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="write the ceiling file from the current staged index",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    baseline_path = (repo_root / args.baseline_file).resolve()

    if args.write_baseline:
        return write_baseline(repo_root, baseline_path)
    return check_baseline(repo_root, baseline_path)


if __name__ == "__main__":
    raise SystemExit(main())
