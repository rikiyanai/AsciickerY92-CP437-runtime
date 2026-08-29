#!/usr/bin/env python3
"""PreToolUse hook — HARD BLOCK for direct FAILURE_LOG document access.

Catches: Grep, Read, Bash, Glob targeting docs/FAILURE_LOG.md directly.
Agents MUST use the proper front doors:
  python3 scripts/analyze_failure_log.py <command>

Hook interface:
  stdin: {"tool_name": "...", "tool_input": {...}}
  stdout: {"blocked": true, "message": "..."} to block
  exit 0: allow, exit 2: block
"""
from __future__ import annotations

import json
import re
import sys

# Match the actual FAILURE_LOG document path — not scripts/files that
# happen to mention "failure_log" in their own name.
_FL_DOC_PATH_RE = re.compile(
    r"(?:^|/)docs/FAILURE_LOG\.md\b"
    r"|(?:^|/)FAILURE_LOG\.md\b",
    re.IGNORECASE,
)

# Pattern to detect the proper front doors (allow these through)
_FRONT_DOOR_RE = re.compile(
    r"(?:analyze_failure_log\.py|fl_cli_contract\.py)",
    re.IGNORECASE,
)

# Bash commands that constitute "searching/parsing"
_SEARCHY_CMD_RE = re.compile(
    r"\b(grep|egrep|fgrep|rg|cat|head|tail|sed|awk|jq|wc|less|more|python3?\s+-c)\b"
)

_BLOCK_MSG = (
    "[maintainer:failure-log-guard] BLOCKED: direct FAILURE_LOG access detected. "
    "Use the proper front door:\n"
    "  python3 scripts/analyze_failure_log.py <command>\n"
    "Available commands: search, show, list, open, audit, preflight, "
    "by-file, by-symbol, lifecycle, context, categories, tags, "
    "history, related, overlay, epochs, contract\n"
    "Run `python3 scripts/analyze_failure_log.py contract --json` for the full command index."
)


def _has_fl_doc_path(text: str) -> bool:
    """True only if text references the actual FAILURE_LOG.md document."""
    return bool(_FL_DOC_PATH_RE.search(text))


def _check_grep(tool_input: dict) -> bool:
    """Block Grep tool when path or glob targets FAILURE_LOG.md."""
    path = str(tool_input.get("path", ""))
    glob_val = str(tool_input.get("glob", ""))
    if _has_fl_doc_path(path):
        return True
    if _has_fl_doc_path(glob_val):
        return True
    return False


def _check_read(tool_input: dict) -> bool:
    """Block Read tool when file_path is FAILURE_LOG.md."""
    file_path = str(tool_input.get("file_path", ""))
    return _has_fl_doc_path(file_path)


def _check_bash(tool_input: dict) -> bool:
    """Block Bash commands that grep/cat/parse FAILURE_LOG.md directly."""
    cmd = str(tool_input.get("command", ""))
    if not _has_fl_doc_path(cmd):
        return False
    # Allow if the command IS the front door
    if _FRONT_DOOR_RE.search(cmd):
        return False
    # Block if it's a searchy command targeting FAILURE_LOG.md
    if _SEARCHY_CMD_RE.search(cmd):
        return True
    return False


def _check_glob(tool_input: dict) -> bool:
    """Block Glob when pattern targets FAILURE_LOG.md."""
    pattern = str(tool_input.get("pattern", ""))
    path = str(tool_input.get("path", ""))
    return _has_fl_doc_path(pattern) or _has_fl_doc_path(path)


_CHECKERS = {
    "Grep": _check_grep,
    "Read": _check_read,
    "Bash": _check_bash,
    "Glob": _check_glob,
}


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)

    tool_name = str(data.get("tool_name", "")).strip()
    checker = _CHECKERS.get(tool_name)
    if not checker:
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        sys.exit(0)

    if checker(tool_input):
        sys.stderr.write(_BLOCK_MSG + "\n")
        print(json.dumps({"blocked": True, "message": _BLOCK_MSG}))
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
