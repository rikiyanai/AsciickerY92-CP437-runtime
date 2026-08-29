#!/usr/bin/env python3
"""PreToolUse:Write+Edit+MultiEdit hook — block forbidden claim words in file content.

Extends claim guard beyond git commits to file content being written or edited.
Checks the content/new_string for forbidden status words without evidence refs
in the same content.

Hook interface:
  stdin: {"tool_name": "Write"|"Edit"|"MultiEdit", "tool_input": {...}}
  stdout: {"blocked": true, "message": "..."} to block
  exit 0: allow, exit 2: block
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

_MAINTAINER_DIR = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _MAINTAINER_DIR.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from scripts.maintainer.claim_guard import check_message
except ImportError:
    sys.path.insert(0, str(_MAINTAINER_DIR))
    from claim_guard import check_message

# Only check these file patterns — skip generated/binary/config files
WATCHED_PATTERNS = [
    r"\.md$",
    r"\.rs$",
    r"\.toml$",
    r"\.py$",
    r"\.ts$",
    r"\.js$",
    r"\.json$",
]

# Skip files where claim words are expected (e.g., the claim guard itself,
# skill definitions which discuss forbidden vocabulary as examples to avoid)
# RQ-052 / FL-1838: FAILURE_LOG.md is NO LONGER skipped — status changes in the
# FL must be checked by the FL-specific status-change detector below (warn-only).
SKIP_PATTERNS = [
    r"claim_guard",
    r"maintainer/",
    r"POLICY\.md$",
    r"node_modules/",
    r"\.claude/hooks/",
    r"\.claude/skills/",
    # Pure code modules where claim words are field/key names, not status claims.
    r"scripts/recovery_policy\.py$",
]

# FL status transition pattern (RQ-052): detect direct status-change markers in
# new_string without a fix-attempt anchor nearby.
_FL_STATUS_CHANGE_RE = re.compile(
    r"\*\*Status:\*\*\s*(OPEN|PARTIAL|MONITORING|IMPLEMENTED|IMPLEMENTED-UNPROVEN|VERIFYING|PROVEN|RESOLVED|DONE|NEEDS PLANNING)",
    re.IGNORECASE,
)
_FL_FIX_ATTEMPT_RE = re.compile(r"\*\*Fix attempt", re.IGNORECASE)


def _status_change_without_fix_attempt(
    *,
    old_content: str,
    new_content: str,
) -> bool:
    """Return True when a status line changes without a new fix-attempt anchor."""
    diff = difflib.ndiff(old_content.splitlines(), new_content.splitlines())
    status_changed = False
    fix_attempt_added = False
    for line in diff:
        if not line or line[0] not in "+-":
            continue
        body = line[2:]
        if _FL_STATUS_CHANGE_RE.search(body):
            status_changed = True
        if line[0] == "+" and _FL_FIX_ATTEMPT_RE.search(body):
            fix_attempt_added = True
    return status_changed and not fix_attempt_added


def _check_fl_status_change(file_path: str, content: str, *, tool_name: str) -> None:
    """Warn (never block) when a FL status change appears without a fix-attempt anchor.

    RQ-052 / FL-1838: The FAILURE_LOG is no longer silently exempt from all
    enforcement.  This detector catches status-header edits that lack an
    accompanying '**Fix attempt' block in the same hunk.  Exit 0 — warn only.
    """
    if not re.search(r"FAILURE_LOG\.md$", file_path):
        return
    if tool_name == "Write":
        old_content = ""
        current_path = Path(file_path)
        try:
            if current_path.exists():
                old_content = current_path.read_text(encoding="utf-8")
        except OSError:
            old_content = ""
        should_warn = _status_change_without_fix_attempt(
            old_content=old_content,
            new_content=content,
        )
    else:
        has_status_change = bool(_FL_STATUS_CHANGE_RE.search(content))
        has_fix_attempt = bool(_FL_FIX_ATTEMPT_RE.search(content))
        should_warn = has_status_change and not has_fix_attempt

    if should_warn:
        sys.stderr.write(
            "[hook:claim-guard-content] \033[33m⚠ WARNING:\033[0m "
            "FL status change detected without a **Fix attempt block in this commit. "
            "Add a **Fix attempt section with evidence refs (commit hash, test, etc.) "
            "before changing ProofState or Status headers.\n"
        )


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        sys.stderr.write("[hook:claim-guard-content] \033[33m⚠ WARNING:\033[0m empty stdin — skipping check\n")
        sys.exit(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"[hook:claim-guard-content] \033[33m⚠ WARNING:\033[0m malformed JSON input: {e} — skipping check\n")
        sys.exit(0)
    if not isinstance(data, dict):
        sys.stderr.write(f"[hook:claim-guard-content] \033[33m⚠ WARNING:\033[0m expected JSON object, got {type(data).__name__} — skipping check\n")
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Write", "Edit", "MultiEdit"):
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    # Only check watched file types
    if not any(re.search(p, file_path) for p in WATCHED_PATTERNS):
        sys.exit(0)

    # Skip files where claim words are expected
    if any(re.search(p, file_path) for p in SKIP_PATTERNS):
        sys.exit(0)

    # Extract content being written
    content = ""
    if tool_name == "Write":
        content = tool_input.get("content", "")
    elif tool_name == "Edit":
        content = tool_input.get("new_string", "")
    elif tool_name == "MultiEdit":
        # MultiEdit payload contains multiple edit operations against one file.
        edits = tool_input.get("edits", [])
        chunks = []
        if isinstance(edits, list):
            for edit in edits:
                if isinstance(edit, dict):
                    new_string = edit.get("new_string", "")
                    if isinstance(new_string, str) and new_string.strip():
                        chunks.append(new_string)
        content = "\n".join(chunks)

    if not content:
        sys.exit(0)

    # FL-specific status-change detection (warn only — RQ-052 / FL-1838)
    _check_fl_status_change(file_path, content, tool_name=tool_name)

    # FAILURE_LOG.md uses status vocabulary intentionally — skip the full
    # claim guard after running the targeted FL detector above.
    if re.search(r"FAILURE_LOG\.md$", file_path):
        sys.exit(0)

    # Run claim guard on the content
    report = check_message(content, mode="block")

    unsupported = [
        f for f in report.findings if f.category == "unsupported_claim"
    ]
    if not unsupported:
        sys.exit(0)

    detail = unsupported[0].summary
    msg = (
        f"[maintainer:claim-guard-content] BLOCKED: {detail}\n"
        f"File: {file_path}\n"
        f"Add evidence refs (FL-NNN, commit hash) to justify status claims, "
        f"or use PARTIAL/MONITORING vocabulary instead."
    )
    sys.stderr.write(msg + "\n")
    print(json.dumps({"blocked": True, "message": msg}))
    sys.exit(2)


if __name__ == "__main__":
    main()
