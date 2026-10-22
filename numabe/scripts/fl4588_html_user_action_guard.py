#!/usr/bin/env python3
"""Reject ASCIIID browser tests that bypass visible physical input.

FL-4588 and Law 12b permit read-only DOM inspection plus physical pointer,
wheel, keyboard, and text choreography. Product mutation must begin at one
visible registered leaf. JavaScript mutation, synthetic DOM events, direct
transport messages, and direct application-service calls are inadmissible.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELF = Path("scripts/fl4588_html_user_action_guard.py")
TEST = Path("tests/scripts_tests/test_fl4588_html_user_action_guard.py")
SCAN_SUFFIXES = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".py"}
SCAN_EXCLUDED_PREFIXES = (
    # Product code owns the real typed transport and React-controlled values;
    # this guard audits proof drivers, not the implementation being exercised.
    Path("web/asciiid-authoring-shell/src"),
    # Built bundles and retained journey packages are immutable evidence, not
    # executable proof-driver source.
    Path("dist/ASCIID FL4512 Presenter HTML Authoring.app/Contents/Resources/shell"),
    Path("docs/agent/asciiid-html-architecture/headed-journey-artifacts"),
)
ASCIIID_PATH = re.compile(
    r"(?i)(?:asciiid|fl4588|fl4548|web_authoring|html_authoring|action_impact)"
)

FORBIDDEN = (
    ("JAVASCRIPT_ELEMENT_CLICK", re.compile(r"(?<![\w.])(?:element|el|node|button|input)\.click\s*\(")),
    ("SYNTHETIC_DOM_EVENT", re.compile(r"\bdispatchEvent\s*\(|\bnew\s+(?:MouseEvent|KeyboardEvent|InputEvent)\s*\(")),
    ("DIRECT_DOM_VALUE_WRITE", re.compile(r"\.(?:value|checked|selectedIndex)\s*=")),
    ("DIRECT_DOM_MUTATION", re.compile(r"\b(?:setAttribute|removeAttribute|replaceChildren|insertAdjacentHTML)\s*\(")),
    ("PLAYWRIGHT_NONPHYSICAL_FORM_WRITE", re.compile(r"\.(?:fill|selectOption|setChecked|check|uncheck)\s*\(")),
    ("DIRECT_MUTATION_TRANSPORT", re.compile(
        r"(?is)(?:fetch|WebSocket|postMessage|send)\s*\([^\n]{0,240}"
        r"(?:history\.undo|history\.redo|asciiid\.action\.v1)"
    )),
    ("DIRECT_APPLICATION_SERVICE_MUTATION", re.compile(
        r"\b(?:AsciiidApplicationService|applicationService|application_service)"
        r"\s*(?:\.|->)\s*(?:Undo|Redo|SaveDocument|OpenDocument)\s*\("
    )),
)


def findings(text: str) -> list[str]:
    return sorted(name for name, pattern in FORBIDDEN if pattern.search(text))


def relevant_path(path: Path) -> bool:
    return (
        path.suffix in SCAN_SUFFIXES
        and not any(path.is_relative_to(prefix) for prefix in SCAN_EXCLUDED_PREFIXES)
        and bool(ASCIIID_PATH.search(path.as_posix()))
    )


def repository_findings() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    result: list[str] = []
    for raw in proc.stdout.splitlines():
        rel = Path(raw)
        if rel in {SELF, TEST} or not relevant_path(rel):
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        for finding in findings(path.read_text(encoding="utf-8", errors="replace")):
            result.append(f"{rel}:{finding}")
    return sorted(set(result))


def staged_findings() -> list[str]:
    proc = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    result: list[str] = []
    for raw in proc.stdout.splitlines():
        rel = Path(raw)
        if rel in {SELF, TEST} or not relevant_path(rel):
            continue
        diff = subprocess.run(
            ["git", "diff", "--cached", "--unified=0", "--", raw],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        ).stdout
        added = "\n".join(
            line[1:] for line in diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        for finding in findings(added):
            result.append(f"{rel}:{finding}")
    return sorted(set(result))


def hook_findings(payload: dict) -> list[str]:
    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return []
    text = "\n".join(str(value) for value in tool_input.values())
    if not ASCIIID_PATH.search(text) and not any(
        token in text for token in ("data-leaf-id", "runtime-intent")
    ):
        return []
    return findings(text)


def fail(result: list[str], context: str) -> int:
    if not result:
        return 0
    message = (
        f"[maintainer:fl4588-html-user-action-guard] BLOCKED {context}: "
        f"{', '.join(result)}. Drive a visible data-leaf-id through physical "
        "pointer, wheel, keyboard, and text input. Read-only observation is permitted."
    )
    sys.stderr.write(message + "\n")
    print(json.dumps({"blocked": True, "message": message}))
    return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hook", action="store_true")
    parser.add_argument("--staged", action="store_true")
    args = parser.parse_args()
    if args.hook:
        raw = sys.stdin.read()
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return fail(["MALFORMED_HOOK_PAYLOAD"], "agent tool request")
        return fail(hook_findings(payload), "agent tool request")
    # Pre-commit owns only the staged transaction. Repository-wide audit mode
    # remains available explicitly, but existing unrelated findings cannot
    # deadlock every commit that does not touch an FL-4588 proof driver.
    result = staged_findings() if args.staged else repository_findings()
    return fail(sorted(set(result)), "repository guard")


if __name__ == "__main__":
    raise SystemExit(main())
