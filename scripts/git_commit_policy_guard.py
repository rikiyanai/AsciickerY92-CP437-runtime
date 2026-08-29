#!/usr/bin/env python3
"""Local commit-policy guard for the non-LFS rebuilt repo."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_ARCHIVE = "scripts/launcher_helper_bot/index/chroma.tar.gz"
BANNED_FL4359_STATIONARY_ANALYZER = "scripts/analyze_fl4359_jitter.py"
LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"
CLAUDE_COAUTHOR_RE = re.compile(r"^Co-Authored-By:\s*Claude(?:\s|<|$)", re.IGNORECASE | re.MULTILINE)
BANNED_CHOICE_TOKEN = chr(111) + chr(114)
BANNED_CHOICE_TOKEN_RE = re.compile(rf"(?<![A-Za-z0-9_]){BANNED_CHOICE_TOKEN}(?![A-Za-z0-9_])", re.IGNORECASE)
LFS_CONFIG_PATTERNS = (
	"filter=lfs",
	"diff=lfs",
	"merge=lfs",
	"git-lfs",
	"git lfs",
)
FL4359_STATIONARY_JITTER_MARKERS = (
	"stationary" + "_snap",
	"stationary residual " + "gate",
	"evidence_fl4359_stationary" + "_snap_sanity",
)


def _git(*args: str, text: bool = True) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=text,
    )


def staged_paths() -> list[str]:
    proc = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip() or "git diff --cached failed")
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def staged_blob_text(path: str) -> str:
    proc = _git("show", f":{path}")
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip() or f"git show :{path} failed")
    return proc.stdout


def pre_commit_error(paths: list[str] | None = None, blob_loader=staged_blob_text) -> str | None:
    for path in paths if paths is not None else staged_paths():
        if path == FORBIDDEN_ARCHIVE:
            return (
                "GIT GUARD BLOCKED: scripts/launcher_helper_bot/index/chroma.tar.gz must not be recommitted. "
                "Keep helper-bot archives local and move to the documented chunked lane after cutover."
            )
        if path == BANNED_FL4359_STATIONARY_ANALYZER:
            return (
                "GIT GUARD BLOCKED: scripts/analyze_fl4359_jitter.py is banned. "
                "FL-4359 accepts moving-camera evidence only."
            )
        try:
            blob_text = blob_loader(path)
        except (RuntimeError, UnicodeDecodeError):
            continue
        if path.startswith("scripts/") and any(marker in blob_text for marker in FL4359_STATIONARY_JITTER_MARKERS):
            return (
                f"GIT GUARD BLOCKED: stationary FL-4359 jitter evidence is banned in {path}. "
                "Use moving-camera evidence only."
            )
        preview = "\n".join(blob_text.splitlines()[:5])
        if path == ".gitattributes" and any(pattern in blob_text for pattern in LFS_CONFIG_PATTERNS):
            return "GIT GUARD BLOCKED: Git LFS config is forbidden in this rebuilt repo."
        if preview.startswith(LFS_POINTER_PREFIX):
            return f"GIT GUARD BLOCKED: LFS pointer blobs are forbidden ({path})."
    return None


def commit_msg_error(message_text: str) -> str | None:
    if CLAUDE_COAUTHOR_RE.search(message_text):
        return 'GIT GUARD BLOCKED: "Co-Authored-By: Claude" is forbidden in commit messages.'
    if BANNED_CHOICE_TOKEN_RE.search(message_text):
        return "GIT GUARD BLOCKED: standalone choice token is banned in commit messages."
    return None


def cmd_pre_commit(_: argparse.Namespace) -> int:
    error = pre_commit_error()
    if error:
        print(error, file=sys.stderr)
        return 1
    return 0


def cmd_commit_msg(args: argparse.Namespace) -> int:
    message_text = Path(args.message_file).read_text(encoding="utf-8")
    error = commit_msg_error(message_text)
    if error:
        print(error, file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    pre_commit = sub.add_parser("pre-commit", help="Validate staged content before commit.")
    pre_commit.set_defaults(func=cmd_pre_commit)

    commit_msg = sub.add_parser("commit-msg", help="Validate the commit message file.")
    commit_msg.add_argument("message_file")
    commit_msg.set_defaults(func=cmd_commit_msg)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(f"GIT GUARD BLOCKED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
