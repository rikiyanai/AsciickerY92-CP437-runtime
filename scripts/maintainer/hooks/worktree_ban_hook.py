#!/usr/bin/env python3
"""PreToolUse:Bash hook - block creation of git worktrees.

Repo policy: this project must never create temporary git worktrees. Auditing
existing worktrees is allowed; creation is blocked before the Bash tool runs.

Hook interface:
  stdin: {"tool_name": "Bash", "tool_input": {"command": "..."}}
  stdout: {"blocked": true, "message": "..."} to block
  exit 0: allow; exit 2: block
"""
from __future__ import annotations

import json
import re
import sys

_SHELL_WORD_RE = r"""(?:"[^"]*"|'[^']*'|[^\s;&|()]+)"""
_GIT_WORKTREE_ADD_RE = re.compile(
    rf"""
    \b
    git
    (?:
        \s+
        (?:
            -C\s+{_SHELL_WORD_RE}
            |-c\s+{_SHELL_WORD_RE}
            |--git-dir(?:=|\s+){_SHELL_WORD_RE}
            |--work-tree(?:=|\s+){_SHELL_WORD_RE}
            |--[A-Za-z0-9][A-Za-z0-9-]*(?:={_SHELL_WORD_RE})?
        )
    )*
    \s+
    worktree
    \s+
    add
    \b
    """,
    re.VERBOSE,
)


def _command_from_stdin() -> str:
    raw = sys.stdin.read()
    if not raw.strip():
        return ""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, EOFError):
        return ""
    if not isinstance(data, dict):
        return ""
    if data.get("tool_name") != "Bash":
        return ""
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return ""
    return str(tool_input.get("command", ""))


def main() -> None:
    cmd = _command_from_stdin()
    if not cmd:
        sys.exit(0)

    if not _GIT_WORKTREE_ADD_RE.search(cmd):
        sys.exit(0)

    msg = (
        "[maintainer:worktree-ban] BLOCKED: `git worktree add` is banned in "
        "asciicker-Y9-2. Use the main checkout. If a clean tree is needed, "
        "stop and ask the user."
    )
    sys.stderr.write(msg + "\n")
    print(json.dumps({"blocked": True, "message": msg}))
    sys.exit(2)


if __name__ == "__main__":
    main()
