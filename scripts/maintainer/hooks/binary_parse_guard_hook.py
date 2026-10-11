#!/usr/bin/env python3
"""PreToolUse hook — HARD BLOCK for ad-hoc A3D/AKM binary parsing.

Catches: Bash commands that run inline Python struct.unpack against
A3D or AKM binary files. This is a documented recurring failure pattern:
format assumptions are always wrong, results are garbage.

Agents MUST use proper committed CLI tools, not inline python3 -c scripts.

Hook interface:
  stdin: {"tool_name": "...", "tool_input": {...}}
  stdout: {"blocked": true, "message": "..."} to block
  exit 0: allow, exit 2: block
"""
from __future__ import annotations

import json
import re
import sys

# Detect inline Python execution
_INLINE_PYTHON_RE = re.compile(
    r"\bpython3?\s+-c\b"
)

# Detect struct.unpack or binary parsing patterns
_STRUCT_PARSE_RE = re.compile(
    r"\bstruct\.(unpack|pack|calcsize|iter_unpack)\b"
    r"|\bfrom\s+struct\s+import\b"
    r"|\bimport\s+struct\b"
)

# Detect A3D/AKM file references
_BINARY_FORMAT_RE = re.compile(
    r"\.(a3d|akm)\b",
    re.IGNORECASE,
)

# Also catch direct open/read of binary format files with ad-hoc scripts
_ADHOC_BINARY_READ_RE = re.compile(
    r"""(?:open|read)\s*\(.*\.(?:a3d|akm)""",
    re.IGNORECASE,
)

_BLOCK_MSG = (
    "[maintainer:binary-parse-guard] BLOCKED: ad-hoc A3D/AKM binary parsing detected. "
    "NEVER write inline python3 -c struct.unpack scripts for binary formats.\n"
    "The format assumptions are always wrong (format_version, instance struct size, AKM header).\n"
    "Use proper committed CLI tools:\n"
    "  python3 scripts/minimap.py <a3d-file>           — A3D map rendering/inspection\n"
    "  use native repo front doors for artifact analysis; deleted proof analyzers are not valid\n"
    "If no tool exists for your query, BUILD one as a committed script with tests first."
)


def _check_bash(tool_input: dict) -> bool:
    """Block inline Python struct parsing of A3D/AKM files."""
    cmd = str(tool_input.get("command", ""))
    if not cmd:
        return False

    # Pattern 1: python3 -c with struct and .a3d/.akm
    if _INLINE_PYTHON_RE.search(cmd):
        if _STRUCT_PARSE_RE.search(cmd) and _BINARY_FORMAT_RE.search(cmd):
            return True
        if _ADHOC_BINARY_READ_RE.search(cmd):
            return True

    # Pattern 2: piped commands parsing binary files
    # e.g., "hexdump file.a3d | awk ..."
    if _BINARY_FORMAT_RE.search(cmd):
        if re.search(r"\b(hexdump|xxd|od)\b", cmd):
            return True

    return False


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
    if tool_name != "Bash":
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        sys.exit(0)

    if _check_bash(tool_input):
        sys.stderr.write(_BLOCK_MSG + "\n")
        print(json.dumps({"blocked": True, "message": _BLOCK_MSG}))
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
