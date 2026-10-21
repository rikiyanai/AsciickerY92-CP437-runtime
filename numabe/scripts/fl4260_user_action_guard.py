#!/usr/bin/env python3
"""Block ASCIIID proof paths that bypass visible user input.

The executable proof surface may observe state, capture frames, record control
rectangles, and dispatch the same mouse/keyboard events a person can perform.
It may not select tabs, scroll panels, load maps, move cameras, open TERM++, or
mutate authored/runtime state through a backend command. FL-4260 / FL-4548.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "apps/asciicker-presenter/asciiid.cpp"

EXACT_ALLOWED = {
    "ECHO",
    "FL4260_KB_LIST",
    "FL4260_KB_STATUS",
    "FL4260_PROFILE_COLOR_STATUS",
    "FL4207_VERIFY_TERMPP_EXTENDED_ONLY",
    "FL4548_DESCRIBE_CELL",
}

# FL4588-RETIRE-NATIVE-PROOF-BACKDOORS (FL-4588): these native ImGui command families
# were hard-deleted from the editor and host backend. The guard rejects each one (even if
# a broad allow prefix would otherwise match) so no retired native backdoor can run.
RETIRED_NATIVE_PROOF_EXACT = {
    "FL4131_ORIGINAL_Y8_INSPECTION",
    "CAPTURE_UI_FRAME",
    "CAPTURE_CLEAN_FRAME",
    "RUN_MOUSE_CLICK_PROBE",
    "RUN_MOUSE_MOVE_PROBE",
    "RUN_MOUSE_DRAG_PROBE",
    "RUN_MOUSE_RIGHT_CLICK_PROBE",
    "RUN_SDL_MOUSE_DRAG_PROBE",
    "RUN_SDL_KEY",
    "FL4260_CTRL_RECTS_RECORD",
    "FL4131_DUMP_PRESET_UI_RECTS",
    "FL4131_DUMP_EXTENDED_PICKER_UI_RECTS",
}
# Family patterns so a NEW backdoor in any of these retired shapes is also rejected.
# Scoped to the retired families only: CAPTURE_UI/CLEAN_FRAME (NOT the still-live
# CAPTURE_FRAME / CAPTURE_TERMPP_FRAME* engine/artifact reads), and the two named
# FL4131 control-rectangle dumps (NOT other FL*_DUMP_*_UI_RECTS observation commands).
RETIRED_NATIVE_PROOF_RE = re.compile(
    r"^(?:CAPTURE_(?:UI|CLEAN)_FRAME"
    r"|FL[0-9]+_ORIGINAL_"
    r"|RUN_MOUSE_[A-Z_]*PROBE"
    r"|RUN_SDL_(?:KEY|MOUSE_[A-Z_]*PROBE)"
    r"|FL4260_CTRL_RECTS_RECORD"
    r"|FL4131_DUMP_(?:PRESET|EXTENDED_PICKER)_UI_RECTS)$"
)


def retired_native_proof(name: str) -> bool:
    return name in RETIRED_NATIVE_PROOF_EXACT or bool(RETIRED_NATIVE_PROOF_RE.match(name))

COMMAND_TOKEN = re.compile(r'strcmp\(cmd,\s*"([A-Z][A-Z0-9_]+)"')
DRIVER_CALL = re.compile(
    r'(?:(?:[A-Za-z_][A-Za-z0-9_\.]*)\.(?:call|send|command|request)'
    r'|\b(?:sendCdp|send_cdp|send_command|run_command))'
    r'\s*\(\s*["\']([A-Z][A-Z0-9_]+)["\']'
    r'|["\']method["\']\s*:\s*["\']([A-Z][A-Z0-9_]+)["\']'
)
HANDLER_CALL = re.compile(r'strcmp\(cmd,\s*"([A-Z][A-Z0-9_]+)"')
FORBIDDEN_LAUNCH = re.compile(
    r'(?<![A-Za-z0-9_])(?:--map|--sprite|--test-script|--profile-workspace|'
    r'--fl4131-morphology-profile-proof|--legacy-material-ui|--fl4207-e2e-proof|'
    r'--batch|--headless-batch)'
    r'(?![A-Za-z0-9_-])|ASCIICKER_INITIAL_WINDOW_(?:WIDTH|HEIGHT)'
    r'|ASCIICKER_LEGACY_MATERIAL_UI'
)
REPO_FORBIDDEN_LAUNCH = re.compile(
    r'(?i)(?:\.run/(?:asciiid|asciicker-presenter)|\b(?:asciiid|asciicker-presenter)\b)[^\n]{0,240}'
    r'(?:--map|--sprite|--test-script|--profile-workspace|'
    r'--fl4131-morphology-profile-proof|--legacy-material-ui|--fl4207-e2e-proof|'
    r'--batch|--headless-batch)'
    r'|ASCIICKER_INITIAL_WINDOW_(?:WIDTH|HEIGHT)|ASCIICKER_LEGACY_MATERIAL_UI'
)

SCAN_SUFFIXES = {".py", ".js", ".ts", ".cpp", ".h", ".sh"}
SCAN_EXCLUDED_PARTS = {
    ".git",
    ".codex-render",
    ".codex_tmp",
    ".cursor",
    "classic_asciiid",
    "node_modules",
    "tmp",
    "vendor",
}
SCAN_EXCLUDED = {
    Path("scripts/fl4260_user_action_guard.py"),
    Path("tests/scripts_tests/test_fl4260_user_action_guard.py"),
    # FL-4588: these are offline asset-construction programs, not ASCIIID
    # product launchers and never grouped-proof drivers. The agent hook below
    # still inspects either file when an agent tries to execute it, so their
    # legacy native batch phases cannot enter a proof package.
    Path("scripts/attach_sbu_assets.py"),
    Path("scripts/sbu_e2e_run.py"),
}


def allowed(name: str) -> bool:
    # FL4588-RETIRE-NATIVE-PROOF-BACKDOORS: retired native backdoors are rejected FIRST,
    # before any broad allow prefix can admit them.
    if retired_native_proof(name):
        return False
    if name in EXACT_ALLOWED:
        return True
    # CAPTURE_ stays allowed for the still-live engine/artifact reads (CAPTURE_FRAME,
    # CAPTURE_TERMPP_FRAME*); the retired CAPTURE_UI/CLEAN_FRAME are rejected above.
    if re.match(r"^(?:CAPTURE_|DUMP_|EXPORT_|GET_|LIST_|PROBE_|QUERY_)", name):
        return True
    # ORIGINAL dropped: FL*_ORIGINAL_* was a synthetic staging owner, not an observation.
    if re.match(r"^FL[0-9]+_(?:DUMP|GET|LIST|DESCRIBE|VERIFY|INFO)_", name):
        return True
    if re.match(r"^FL[0-9]+_[A-Z0-9_]*_(?:DUMP|STATUS)$", name):
        return True
    return False


def process_mcp_body(source: str) -> str:
    start = source.find("void ProcessMCPCommand(char* line)")
    end = source.find("// WHY single", start)
    if start < 0 or end < 0:
        raise RuntimeError("ProcessMCPCommand boundary missing")
    return source[start:end]


def invoked_commands(text: str) -> set[str]:
    found: set[str] = set()
    for match in DRIVER_CALL.finditer(text):
        name = match.group(1) or match.group(2)
        if name:
            found.add(name)
    return found


def forbidden_calls(text: str) -> set[str]:
    return {name for name in invoked_commands(text) if not allowed(name)}


def source_findings() -> list[str]:
    body = process_mcp_body(EDITOR.read_text())
    return sorted({name for name in COMMAND_TOKEN.findall(body) if not allowed(name)})


def active_commands() -> set[str]:
    return set(COMMAND_TOKEN.findall(process_mcp_body(EDITOR.read_text())))


def repository_findings() -> list[str]:
    active = active_commands()
    findings: list[str] = []
    proc = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    for raw_rel in proc.stdout.splitlines():
        rel = Path(raw_rel)
        if any(part in SCAN_EXCLUDED_PARTS for part in rel.parts):
            continue
        path = ROOT / rel
        if not path.is_file() or path.suffix not in SCAN_SUFFIXES or rel in SCAN_EXCLUDED:
            continue
        text = path.read_text(errors="replace")
        commands = set() if rel == Path("apps/asciicker-presenter/asciiid.cpp") else invoked_commands(text)
        for name in sorted(commands):
            if name not in active:
                findings.append(f"{rel}:DEAD_ASCIIID_COMMAND:{name}")
            elif not allowed(name):
                findings.append(f"{rel}:HIDDEN_ASCIIID_ACTION:{name}")
        if REPO_FORBIDDEN_LAUNCH.search(text):
            findings.append(f"{rel}:FORBIDDEN_PROOF_LAUNCH_SHORTCUT")
    return sorted(set(findings))


def staged_findings() -> list[str]:
    names = subprocess.run(
        [
            "git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "--",
            "*.py", "*.cpp", "*.h",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    findings: set[str] = set()
    for raw_rel in names.stdout.splitlines():
        rel = Path(raw_rel)
        if rel in SCAN_EXCLUDED or rel.parts[:1] == ("tests",) or rel.parts[:2] == ("scripts", "tests"):
            continue
        proc = subprocess.run(
            ["git", "diff", "--cached", "--unified=0", "--", raw_rel],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        added = "\n".join(
            line[1:] for line in proc.stdout.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        findings.update(forbidden_calls(added))
        findings.update(
            name for name in HANDLER_CALL.findall(added) if not allowed(name)
        )
        if REPO_FORBIDDEN_LAUNCH.search(added):
            findings.add("FORBIDDEN_PROOF_LAUNCH_SHORTCUT")
    return sorted(findings)


def invoked_python_files(command: str) -> list[Path]:
    try:
        words = shlex.split(command)
    except ValueError:
        return []
    paths: list[Path] = []
    for index, word in enumerate(words[:-1]):
        if Path(word).name.startswith("python"):
            candidate = Path(words[index + 1])
            if candidate.suffix == ".py":
                if not candidate.is_absolute():
                    candidate = ROOT / candidate
                if candidate.exists():
                    paths.append(candidate)
    return paths


def hook_findings(payload: dict) -> list[str]:
    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return []
    text = "\n".join(str(value) for value in tool_input.values())
    findings = forbidden_calls(text)
    if REPO_FORBIDDEN_LAUNCH.search(text):
        findings.add("FORBIDDEN_PROOF_LAUNCH_SHORTCUT")
    if payload.get("tool_name") == "Bash":
        command = str(tool_input.get("command", ""))
        for path in invoked_python_files(command):
            findings.update(forbidden_calls(path.read_text(errors="replace")))
            if REPO_FORBIDDEN_LAUNCH.search(path.read_text(errors="replace")):
                findings.add(f"FORBIDDEN_PROOF_LAUNCH_SHORTCUT:{path}")
    return sorted(findings)


def fail(findings: list[str], context: str) -> int:
    if not findings:
        return 0
    joined = ", ".join(findings)
    message = (
        f"[maintainer:fl4260-user-action-guard] BLOCKED {context}: {joined}. "
        "FL4588-RETIRE-NATIVE-PROOF-BACKDOORS: native ImGui capture (CAPTURE_UI_FRAME / "
        "CAPTURE_CLEAN_FRAME), synthetic input (RUN_MOUSE_*_PROBE / RUN_SDL_MOUSE_DRAG_PROBE / "
        "RUN_SDL_KEY), the FL*_ORIGINAL_* staging owner, and the control-rectangle recorder "
        "(FL4260_CTRL_RECTS_RECORD / FL*_DUMP_*_UI_RECTS) are retired and rejected. Drive user "
        "actions through physical Chrome/Playwright input on the visible shell; use read-only "
        "observation/DUMP/GET/LIST/QUERY commands otherwise."
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
            return fail(["MALFORMED_HOOK_PAYLOAD"], "hook input")
        return fail(hook_findings(payload), "agent tool request")
    findings = source_findings() + repository_findings()
    if args.staged:
        findings.extend(staged_findings())
    return fail(sorted(set(findings)), "repository guard")


if __name__ == "__main__":
    raise SystemExit(main())
