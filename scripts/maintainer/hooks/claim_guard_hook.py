#!/usr/bin/env python3
"""PreToolUse:Bash hook — block unsupported claims in git commit messages.

Reads Claude Code hook stdin JSON, extracts commit message from git commit -m,
runs claim_guard analysis. Hard-block mode: exits 2 on unsupported claims.

Hook interface:
  stdin: {"tool_name": "Bash", "tool_input": {"command": "..."}, "transcript": "..."}
  stdout: {"blocked": true, "message": "..."} to block
  exit 0: allow, exit 2: block
"""
from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

# Add parent to path for claim_guard imports
_MAINTAINER_DIR = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _MAINTAINER_DIR.parent.parent
sys.path.insert(0, str(_MAINTAINER_DIR.parent.parent))

_SHELL_WORD_RE = r"""(?:"[^"]*"|'[^']*'|[^\s;&|()]+)"""
_RAW_GIT_RESTORE_OR_CHECKOUT_RE = re.compile(
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
    (restore|checkout)
    \b
    """,
    re.VERBOSE,
)


def _resolve_commit_message_file(file_path: str) -> Path | None:
    candidate = Path(file_path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        return None
    try:
        resolved.relative_to(_PROJECT_ROOT)
    except ValueError:
        return None
    return resolved


def _extract_commit_message(cmd: str) -> str | None:
    """Extract commit message from a git commit command string.

    Handles:
      - git commit -m "message" / -m 'message'
      - git commit --message="message" / --message 'message'
      - git commit -m "$(cat <<'EOF'\\nmessage\\nEOF\\n)"  (heredoc)
      - git commit -F <file>  / --file <file>  (returns placeholder — file not read)
      - Multiple -m flags: git commit -m "line1" -m "line2" (concatenated)

    Returns None for non-commit commands or editor-driven commits (no -m/-F).
    """
    m = re.search(r'git\s+commit\b', cmd)
    if not m:
        return None

    # Pattern: heredoc style -m "$(cat <<'EOF'\n...\nEOF\n)"
    m_heredoc = re.search(r"-m\s+\"\$\(cat\s+<<'?EOF'?\n(.+?)\nEOF", cmd, re.DOTALL)
    if m_heredoc:
        return m_heredoc.group(1)

    # Pattern: -F / --file <path> — use shlex to handle quoted paths with spaces.
    # Falls back to regex if shlex fails (e.g. unfinished heredoc in cmd string).
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = []
    for i, tok in enumerate(tokens):
        if tok in ("-F", "--file") and i + 1 < len(tokens):
            file_path = tokens[i + 1]
            resolved = _resolve_commit_message_file(file_path)
            if resolved is None:
                return None
            try:
                return resolved.read_text(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                return None

    # Pattern: --message="msg" or --message='msg' or --message msg
    m_long = re.search(r'''--message[= ]["'](.+?)["']''', cmd, re.DOTALL)
    if m_long:
        return m_long.group(1)

    # Pattern: repeated -m flags → concatenate with newlines (git behavior)
    parts = re.findall(r'''-m\s+["'](.+?)["']''', cmd, re.DOTALL)
    if parts:
        return "\n\n".join(parts)

    return None


def _block_raw_git_restore_or_checkout(cmd: str) -> str | None:
    """Return a block message for raw git restore/checkout shell commands.

    These commands can silently clobber tracked files. In this repo we require
    explicit safe wrappers instead:
      - `git gswitch` / `git gcheckout <branch>` for branch changes
      - `python3 scripts/git_guardrails.py restore -- <paths...>` for file restore
    """
    match = _RAW_GIT_RESTORE_OR_CHECKOUT_RE.search((cmd or "").strip())
    if not match:
        return None
    action = match.group(1)
    return (
        "[maintainer:claim-guard] BLOCKED: raw "
        f"`git {action}` can silently clobber tracked files. "
        "Use `git gswitch` / `git gcheckout <branch>` for branch changes, or "
        "`python3 scripts/git_guardrails.py restore -- <paths...>` for non-canon file restore."
    )


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        sys.stderr.write("[hook:claim-guard] \033[33m⚠ WARNING:\033[0m empty stdin — skipping check\n")
        sys.exit(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"[hook:claim-guard] \033[33m⚠ WARNING:\033[0m malformed JSON input: {e} — skipping check\n")
        sys.exit(0)
    if not isinstance(data, dict):
        sys.stderr.write(f"[hook:claim-guard] \033[33m⚠ WARNING:\033[0m expected JSON object, got {type(data).__name__} — skipping check\n")
        sys.exit(0)

    if data.get("tool_name") != "Bash":
        sys.exit(0)

    cmd = data.get("tool_input", {}).get("command", "")
    dangerous_git_msg = _block_raw_git_restore_or_checkout(cmd)
    if dangerous_git_msg:
        sys.stderr.write(dangerous_git_msg + "\n")
        print(json.dumps({"blocked": True, "message": dangerous_git_msg}))
        sys.exit(2)

    message = _extract_commit_message(cmd)
    if not message:
        sys.exit(0)

    # Import claim_guard logic
    try:
        from scripts.maintainer.claim_guard import check_message
    except ImportError:
        # Fallback: direct import
        sys.path.insert(0, str(_MAINTAINER_DIR))
        from claim_guard import check_message

    report = check_message(message)

    if not report.findings:
        sys.exit(0)

    unsupported = [
        f for f in report.findings if f.category == "unsupported_claim"
    ]
    if unsupported:
        detail = unsupported[0].summary
        msg = (
            f"[maintainer:claim-guard] BLOCKED: {detail} "
            f"Add evidence refs (FL-NNN, commit hash) or rephrase."
        )
        sys.stderr.write(msg + "\n")
        print(json.dumps({"blocked": True, "message": msg}))
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
