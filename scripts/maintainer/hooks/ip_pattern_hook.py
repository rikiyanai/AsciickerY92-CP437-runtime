#!/usr/bin/env python3
"""PreToolUse:Write+Edit+MultiEdit hook — warn on production IP addresses in staged content.

Scans content being written or edited for IPv4 address patterns that look like
production infrastructure.  Common non-production addresses (127.0.0.1, 0.0.0.0,
link-local, private RFC-1918 ranges) are excluded.

This is a warn-only hook (exit 0).  It never blocks — just prints a warning so
the operator can decide whether the IP should be replaced with a placeholder
such as <candidate-host> or <current-host>.

Hook interface:
  stdin: {"tool_name": "Write"|"Edit"|"MultiEdit", "tool_input": {...}}
  stdout: (unused — warn only)
  exit 0: always (warn only, never blocks)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# ── path resolution (canon §3: __file__-relative) ──────────────────────
_HOOKS_DIR = Path(__file__).resolve().parent
_MAINTAINER_DIR = _HOOKS_DIR.parent
_PROJECT_ROOT = _MAINTAINER_DIR.parent.parent

# ── IPv4 detection ──────────────────────────────────────────────────────
_IP_RE = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")

# Non-production IP prefixes / exact matches to ignore
_SAFE_EXACT = frozenset({
    "127.0.0.1",
    "0.0.0.0",
    "255.255.255.255",
})

_SAFE_PREFIXES = (
    "127.",        # loopback
    "10.",         # RFC-1918 Class A private
    "192.168.",    # RFC-1918 Class C private
    "169.254.",    # link-local
)


def _is_safe_ip(ip: str) -> bool:
    """Return True if *ip* is non-production (loopback, private, link-local)."""
    if ip in _SAFE_EXACT:
        return True
    if ip.startswith(_SAFE_PREFIXES):
        return True
    # RFC-1918 172.16.0.0/12
    parts = ip.split(".")
    try:
        if parts[0] == "172" and 16 <= int(parts[1]) <= 31:
            return True
    except (IndexError, ValueError):
        pass
    # Validate each octet is 0-255; skip malformed matches
    try:
        if any(int(p) > 255 for p in parts):
            return True  # not a real IP — skip
    except ValueError:
        return True
    return False


# File patterns where IP addresses are expected / acceptable
_SKIP_PATTERNS = [
    r"node_modules/",
    r"\.git/",
    r"package-lock\.json$",
    r"yarn\.lock$",
]


def _extract_production_ips(text: str) -> list[str]:
    """Return de-duplicated list of production-looking IPs found in *text*."""
    seen: set[str] = set()
    result: list[str] = []
    for m in _IP_RE.finditer(text):
        ip = m.group(1)
        if ip in seen:
            continue
        seen.add(ip)
        if not _is_safe_ip(ip):
            result.append(ip)
    return result


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    if not isinstance(data, dict):
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Write", "Edit", "MultiEdit"):
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    # Skip files where IPs are expected
    if any(re.search(p, file_path) for p in _SKIP_PATTERNS):
        sys.exit(0)

    # Extract content being written
    content = ""
    if tool_name == "Write":
        content = tool_input.get("content", "")
    elif tool_name == "Edit":
        content = tool_input.get("new_string", "")
    elif tool_name == "MultiEdit":
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

    found = _extract_production_ips(content)
    if not found:
        sys.exit(0)

    ip_list = ", ".join(found)
    sys.stderr.write(
        f"[hook:ip-pattern] \033[33m\u26a0 WARNING:\033[0m "
        f"Production-looking IP address(es) detected: {ip_list}\n"
        f"  File: {file_path}\n"
        f"  Consider replacing with placeholders like <candidate-host> or <current-host>.\n"
    )
    # Warn only — never block
    sys.exit(0)


if __name__ == "__main__":
    main()
