#!/usr/bin/env python3
"""Shared search-pattern warning engine for Claude hooks and shell wrappers.

This is warning-only by design. It does not block tool execution; it surfaces
query-space reminders when an agent appears to be using the wrong inspection
surface for watchdog JS, run artifacts, or FL family history.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_WATCHDOG_PATH_RE = re.compile(
    r"(?:^|[\s'\"=:/])(?:multiplayer_visual_watchdog\.js|scripts/watchdog/|watchdog\.js)(?:$|[\s'\"/:])"
)
_RUN_ARTIFACT_MAP: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?:^|/)(?:recording\.jsonl)(?:$|\b)"), "python3 scripts/analyze_runs.py recorder <run-id> [flags]"),
    (re.compile(r"(?:^|/)(?:metrics\.json)(?:$|\b)"), "python3 scripts/analyze_runs.py metrics <run-id> [flags]"),
    (re.compile(r"(?:^|/)(?:slot_manifest\.json)(?:$|\b)"), "python3 scripts/analyze_runs.py slot <run-id>"),
    (re.compile(r"(?:^|/)(?:server\.log)(?:$|\b)"), "python3 scripts/analyze_runs.py server-log <run-id> [flags]"),
    (re.compile(r"(?:^|/)(?:watchdog_phase_history\.jsonl)(?:$|\b)"), "python3 scripts/analyze_runs.py phases <run-id>"),
    (re.compile(r"(?:^|/)(?:summary\.json)(?:$|\b)"), "python3 scripts/analyze_runs.py show <run-id> [--field KEY]"),
)
_FL_RE = re.compile(r"\bFL-\d{2,}\b", re.IGNORECASE)
_SEARCHY_BASH_RE = re.compile(
    r"\b(rg|grep|egrep|fgrep|find|fd|cat|head|tail|sed|awk|jq|curl|python3?\s+-c)\b"
)
_REMOTE_DEPTH_FAMILY_RE = re.compile(
    r"\b(?:allow_mesh_depth_bypass|current_blit_tracked_remote|mesh[-_\s]*depth[-_\s]*bypass|"
    r"depth[-_\s]*bypass|remote[-_\s]*overdraw|overdraw|occlusion|health[-_\s]*bar|hp[-_\s]*bar)\b",
    re.IGNORECASE,
)
_ALREADY_STRUCTURED_RE = re.compile(
    r"\b(?:python3\s+(?:\S+/)?scripts/(?:watchdog_source|analyze_runs)\.py|scripts/fl\b|watchdog_source\.py\b|analyze_runs\.py\b)"
)


@dataclass(frozen=True)
class WarningFinding:
    key: str
    message: str


def _compact(text: str) -> str:
    return " ".join((text or "").split())


def _tool_input_text(tool_name: str, tool_input: dict[str, Any]) -> str:
    if tool_name == "Bash":
        return str(tool_input.get("command", ""))
    parts: list[str] = []
    for key in ("pattern", "query", "path", "file_path", "glob", "description", "prompt"):
        value = tool_input.get(key)
        if value is not None:
            parts.append(str(value))
    return " ".join(parts)


def _artifact_guidance(text: str) -> list[str]:
    guides: list[str] = []
    for pattern, guidance in _RUN_ARTIFACT_MAP:
        if pattern.search(text):
            guides.append(guidance)
    return guides


def analyze_text(tool_name: str, text: str) -> list[WarningFinding]:
    findings: list[WarningFinding] = []
    text = _compact(text)
    if not text:
        return findings

    if _WATCHDOG_PATH_RE.search(text) and not _ALREADY_STRUCTURED_RE.search(text):
        findings.append(WarningFinding(
            key="watchdog-source",
            message=(
                "[search-guard:watchdog-source] WARNING: direct search/read on watchdog JS source "
                "detected. Prefer `python3 scripts/watchdog_source.py <search|show|gate|funcs|const|log|diff|blame>` "
                "instead of raw grep/read when that query space is covered."
            ),
        ))

    guides = _artifact_guidance(text)
    if guides and not _ALREADY_STRUCTURED_RE.search(text):
        unique_guides = ", ".join(dict.fromkeys(guides))
        findings.append(WarningFinding(
            key="run-artifacts",
            message=(
                "[search-guard:run-artifacts] WARNING: direct run-artifact parsing detected. "
                f"Use {unique_guides} instead of ad-hoc grep/cat/head/tail/jq/curl."
            ),
        ))

    if _SEARCHY_BASH_RE.search(text) or tool_name in {"Grep", "Read", "Glob", "Search"}:
        if (
            _REMOTE_DEPTH_FAMILY_RE.search(text)
            and not re.search(r"\banalyze_runs\.py\s+fl\s+family\s+FL-576\b", text, re.IGNORECASE)
        ):
            findings.append(WarningFinding(
                key="remote-depth-family",
                message=(
                    "[search-guard:remote-depth-family] WARNING: remote overdraw/depth search "
                    "touched the reverted FL-576 family. Run "
                    "`python3 scripts/analyze_runs.py fl family FL-576` before proposing another "
                    "render/occlusion exception; normal depth remains the default assumption."
                ),
            ))

        fl_refs = sorted({ref.upper() for ref in _FL_RE.findall(text)})
        for fl_ref in fl_refs:
            if re.search(rf"\banalyze_runs\.py\s+fl\s+family\s+{re.escape(fl_ref)}\b", text, re.IGNORECASE):
                continue
            findings.append(WarningFinding(
                key=f"fl-family:{fl_ref}",
                message=(
                    f"[search-guard:fl-family] WARNING: raw search touched `{fl_ref}`. "
                    f"Before reviving that lane, run `python3 scripts/analyze_runs.py fl family {fl_ref}`."
                ),
            ))

    deduped: list[WarningFinding] = []
    seen: set[str] = set()
    for finding in findings:
        if finding.key in seen:
            continue
        seen.add(finding.key)
        deduped.append(finding)
    return deduped


def analyze_hook_payload(data: dict[str, Any]) -> list[WarningFinding]:
    tool_name = str(data.get("tool_name", "")).strip()
    if tool_name not in {"Bash", "Grep", "Read", "Glob", "Search"}:
        return []
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        tool_input = {}
    text = _tool_input_text(tool_name, tool_input)
    if tool_name == "Bash" and not _SEARCHY_BASH_RE.search(text):
        return analyze_text(tool_name, text) if _FL_RE.search(text) else []
    return analyze_text(tool_name, text)


def analyze_shell_command(tool: str, argv: list[str]) -> list[WarningFinding]:
    quoted = " ".join(shlex.quote(arg) for arg in argv)
    text = f"{tool} {quoted}".strip()
    return analyze_text("Bash", text)


def _emit(findings: list[WarningFinding]) -> int:
    if not findings:
        return 0
    for finding in findings:
        sys.stderr.write(finding.message + "\n")
    return 0


def _cmd_hook() -> int:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        return 0
    if not isinstance(data, dict):
        return 0
    findings = analyze_hook_payload(data)
    if findings:
        print(json.dumps({"statusMessage": findings[0].message}))
    return _emit(findings)


def _cmd_shell(tool: str, argv: list[str]) -> int:
    findings = analyze_shell_command(tool, argv)
    return _emit(findings)


def main() -> int:
    parser = argparse.ArgumentParser(description="Warn when search commands use the wrong query surface.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("hook", help="Read Claude hook JSON from stdin and emit warning status output.")

    shell_parser = sub.add_parser("shell", help="Inspect a shell command argv vector.")
    shell_parser.add_argument("--tool", required=True, help="Executable/function name being wrapped.")
    shell_parser.add_argument("argv", nargs=argparse.REMAINDER, help="Command argv after `--`.")

    args = parser.parse_args()
    if args.cmd == "hook":
        return _cmd_hook()
    if args.cmd == "shell":
        argv = list(args.argv)
        if argv and argv[0] == "--":
            argv = argv[1:]
        return _cmd_shell(args.tool, argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
