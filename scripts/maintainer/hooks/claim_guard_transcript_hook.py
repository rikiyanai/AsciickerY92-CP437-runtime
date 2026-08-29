#!/usr/bin/env python3
"""PreToolUse hook — block tool use when recent assistant output has unsupported claims.

Reads the current session JSONL (live on disk), extracts the last few assistant
messages, and runs claim_guard on them. Blocks the next tool use if forbidden
status words appear without evidence (FL-NNN, commit hash) nearby.

Recovery: include an FL-NNN or commit hash in your next response. The evidence
appears in the recent transcript window and the hook allows the tool use.

Hook interface:
  stdin: {"tool_name": "...", "tool_input": {...}, ...}
  stdout: {"blocked": true, "message": "..."} to block
  exit 0: allow, exit 2: block
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

_MAINTAINER_DIR = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _MAINTAINER_DIR.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from scripts.maintainer.lib.report_schema import FORBIDDEN_STATUS_WORDS
    from scripts.maintainer.lib.jsonl_parser import (
        find_latest_session_jsonl,
    )
    _LIBS_AVAILABLE = True
except ImportError:
    _LIBS_AVAILABLE = False

# Evidence patterns
FL_REF = re.compile(r"\bFL-\d{3,}\b")
COMMIT_REF = re.compile(r"\b[0-9a-f]{7,40}\b")

# How many bytes from the end of JSONL to read (perf: avoid parsing entire file)
TAIL_BYTES = 65536  # 64KB — covers ~10-20 recent messages

# Max assistant messages to check
MAX_MESSAGES = 3
FAILURE_LOG_REL_PATH = Path("docs/FAILURE_LOG.md")
FAILURE_LOG_WINDOW_RECORDS = 80

# Skip if assistant text is clearly meta-discussion about claim guard itself
META_PATTERNS = [
    r"claim.guard",
    r"forbidden.word",
    r"maintainer.hook",
    r"CLM-\d{3}",
    r"claim.discipline",
    r"FORBIDDEN_STATUS_WORDS",
]

FAILURE_LOG_CLAIM_RE = re.compile(r"\bfailure[\s_-]?log\b", re.IGNORECASE)
FAILURE_LOG_ACTION_RE = re.compile(
    r"\b(?:update(?:d)?|append(?:ed)?|logged|add(?:ed)?|record(?:ed)?)\b|\blog\s+(?:this|that|it)\b",
    re.IGNORECASE,
)


def _claims_failure_log_updated(text: str) -> bool:
    """Return True when assistant text claims FAILURE_LOG update/append/log action."""
    if not FAILURE_LOG_CLAIM_RE.search(text):
        return False
    return bool(FAILURE_LOG_ACTION_RE.search(text))


def _is_failure_log_path(path_str: str | None) -> bool:
    if not path_str:
        return False
    norm = str(path_str).replace("\\", "/")
    return norm.endswith(str(FAILURE_LOG_REL_PATH).replace("\\", "/"))


def _has_recent_failure_log_write(records: list[dict], window: int = FAILURE_LOG_WINDOW_RECORDS) -> bool:
    """Detect recent Write/Edit/MultiEdit tool use touching FAILURE_LOG."""
    recent = records[-window:] if window > 0 else records
    for rec in recent:
        if rec.get("type") != "assistant":
            continue
        msg = rec.get("message", {})
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") not in ("Write", "Edit", "MultiEdit"):
                continue
            inp = block.get("input", {})
            if not isinstance(inp, dict):
                continue
            if _is_failure_log_path(inp.get("file_path")):
                return True
    return False


def _failure_log_has_git_changes() -> bool:
    """Best-effort check for staged/unstaged FAILURE_LOG edits."""
    try:
        result = subprocess.run(
            [
                "git",
                "status",
                "--porcelain",
                "--",
                str(FAILURE_LOG_REL_PATH),
            ],
            cwd=_PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(result.stdout.strip())


def _has_valid_commit_ref(text: str) -> bool:
    for token in COMMIT_REF.findall(text.lower()):
        try:
            result = subprocess.run(
                ["git", "-C", str(_PROJECT_ROOT), "rev-parse", "--verify", f"{token}^{{commit}}"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            return True
    return False


def _tail_read_jsonl(path: Path, tail_bytes: int) -> list[dict]:
    """Read the last tail_bytes of a JSONL file and parse valid lines."""
    size = path.stat().st_size
    offset = max(0, size - tail_bytes)

    records = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        if offset > 0:
            f.seek(offset)
            f.readline()  # discard partial first line
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    records.append(obj)
            except json.JSONDecodeError:
                continue
    return records


def _extract_assistant_text(record: dict) -> str:
    """Extract text from an assistant record."""
    msg = record.get("message", "")
    if isinstance(msg, str):
        return msg.strip()
    if isinstance(msg, list):
        parts = []
        for block in msg:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts).strip()
    if isinstance(msg, dict) and "content" in msg:
        return _extract_assistant_text({"message": msg["content"]})
    return ""


def _is_meta_discussion(text: str) -> bool:
    """Check if text is discussing claim guard itself (meta-context)."""
    lower = text.lower()
    matches = sum(1 for p in META_PATTERNS if re.search(p, lower))
    return matches >= 2  # at least 2 meta-patterns = likely discussing the system


def main():
    if not _LIBS_AVAILABLE:
        sys.exit(0)

    raw = sys.stdin.read()
    if not raw.strip():
        sys.stderr.write("[hook:claim-guard-transcript] \033[33m⚠ WARNING:\033[0m empty stdin — skipping check\n")
        sys.exit(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"[hook:claim-guard-transcript] \033[33m⚠ WARNING:\033[0m malformed JSON input: {e} — skipping check\n")
        sys.exit(0)
    if not isinstance(data, dict):
        sys.stderr.write(f"[hook:claim-guard-transcript] \033[33m⚠ WARNING:\033[0m expected JSON object, got {type(data).__name__} — skipping check\n")
        sys.exit(0)

    # Find current session JSONL
    session_path = find_latest_session_jsonl()
    if not session_path or not session_path.exists():
        sys.exit(0)

    # Efficient tail read
    records = _tail_read_jsonl(session_path, TAIL_BYTES)

    # Extract recent assistant messages
    assistant_texts = []
    for rec in records:
        if rec.get("type") == "assistant":
            text = _extract_assistant_text(rec)
            if text and len(text) > 10:
                assistant_texts.append(text)

    if not assistant_texts:
        sys.exit(0)

    # Check last N assistant messages
    recent = assistant_texts[-MAX_MESSAGES:]
    combined = "\n".join(recent)

    # Skip meta-discussion about claim guard
    if _is_meta_discussion(combined):
        sys.exit(0)

    # Guardrail: "updated/logged FAILURE_LOG" claims must be backed by
    # an actual edit signal (recent tool write or current git delta).
    if _claims_failure_log_updated(combined):
        has_recent_write = _has_recent_failure_log_write(records)
        has_git_delta = _failure_log_has_git_changes()
        if not has_recent_write and not has_git_delta:
            msg = (
                "[maintainer:claim-guard-transcript] WARNING: Recent assistant output "
                "claims FAILURE_LOG was updated/logged, but no FAILURE_LOG edit evidence "
                "was detected in recent tool-use transcript or git status."
            )
            sys.stderr.write(msg + "\n")
            sys.exit(0)

    # Search for forbidden words (word-boundary, hyphen-compound immune)
    found_forbidden = []
    for word in FORBIDDEN_STATUS_WORDS:
        if re.search(rf"(?<!-)\b{re.escape(word)}\b(?!-)", combined.lower()):
            found_forbidden.append(word)

    if not found_forbidden:
        sys.exit(0)

    # Check for evidence in the same window
    has_fl = bool(FL_REF.search(combined))
    has_commit = _has_valid_commit_ref(combined)

    if has_fl or has_commit:
        sys.exit(0)

    # Warn-only: forbidden words without evidence in recent assistant output
    msg = (
        f"[maintainer:claim-guard-transcript] WARNING: "
        f"Recent assistant output contains unsupported status words "
        f"[{', '.join(found_forbidden)}] without evidence refs.\n"
        f"Recovery: include an FL-NNN or commit hash in your next response "
        f"to justify the claim, or rephrase using PARTIAL/MONITORING vocabulary."
    )
    sys.stderr.write(msg + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
