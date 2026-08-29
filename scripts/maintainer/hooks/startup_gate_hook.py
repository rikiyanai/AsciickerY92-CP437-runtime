#!/usr/bin/env python3
"""PreToolUse hook — enforce hard startup preflight before any work."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MAINTAINER_DIR = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _MAINTAINER_DIR.parent.parent
_ARTIFACT_DIR = _PROJECT_ROOT / "artifacts" / "maintainer"
_STATE_PATH = _ARTIFACT_DIR / "startup_gate_state.json"
_HOOK_HOME = _PROJECT_ROOT / ".maintainer-hook-home"

_STARTUP_PATTERNS = [
    re.compile(r"python3\s+(?:\S+/)?scripts/maintainer/install_hooks\.py\s+--verify"),
    re.compile(r"python3\s+(?:\S+/)?scripts/maintainer/install_hooks\.py\s+--apply"),
    re.compile(r"python3\s+(?:\S+/)?scripts/maintainer/startup_preflight\.py\b"),
    re.compile(r"python3\s+-m\s+compileall\s+-q\s+scripts/maintainer\b"),
]

def _read_state() -> dict[str, Any]:
    if not _STATE_PATH.exists():
        return {"sessions_ok": {}, "state_valid": True}
    try:
        data = json.loads(_STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {"sessions_ok": {}, "state_valid": False}
    if not isinstance(data, dict):
        return {"sessions_ok": {}, "state_valid": False}
    sessions = data.get("sessions_ok")
    if not isinstance(sessions, dict):
        return {"sessions_ok": {}, "state_valid": False}
    return {"sessions_ok": sessions, "state_valid": True}


def _write_state(state: dict[str, Any]) -> None:
    _ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state, indent=2))


def _session_key(data: dict[str, Any]) -> str:
    """Build a session key with resilient fallback when transcript is unavailable."""
    # Prefer explicit identifiers if present in hook payload.
    for key in ("session_id", "conversation_id", "thread_id", "request_id"):
        raw = data.get(key)
        if raw is not None:
            value = str(raw).strip()
            if value:
                return hashlib.sha1(f"{key}:{value}".encode("utf-8")).hexdigest()

    transcript = data.get("transcript", "")
    if isinstance(transcript, str) and transcript.strip():
        head = transcript[:4000]
        return hashlib.sha1(head.encode("utf-8", errors="ignore")).hexdigest()

    # Last resort when transcript is absent: derive from parent CLI process identity.
    # This avoids false reuse across distinct sessions started within the same time bucket.
    ppid = os.getppid()
    pstart = "unknown"
    try:
        ps = subprocess.run(
            ["ps", "-p", str(ppid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if ps.returncode == 0 and ps.stdout.strip():
            pstart = " ".join(ps.stdout.strip().split())
    except Exception:
        pass
    seed = f"fallback-ppid:{ppid}-start:{pstart}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()


def _is_startup_command(command: str) -> bool:
    if not command:
        return False
    return any(rx.search(command) for rx in _STARTUP_PATTERNS)


def _tail(text: str) -> str:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1] if lines else "no output"


def _run_startup_trio() -> tuple[bool, list[dict[str, str]]]:
    _HOOK_HOME.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(_PROJECT_ROOT / "scripts" / "maintainer" / "startup_preflight.py"),
        "--repo-root",
        str(_PROJECT_ROOT),
        "--mode",
        "claude",
        "--json",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=int(os.environ.get("MAINTAINER_STARTUP_TEST_TIMEOUT", "240")),
            cwd=str(_PROJECT_ROOT),
        )
    except subprocess.TimeoutExpired:
        return False, [{
            "name": "startup-preflight",
            "status": "timeout",
            "summary": "timed out",
        }]
    except Exception as exc:
        return False, [{
            "name": "startup-preflight",
            "status": "error",
            "summary": str(exc),
        }]

    try:
        payload = json.loads((proc.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        payload = {}
    raw_results = payload.get("results", [])
    results: list[dict[str, str]] = []
    if isinstance(raw_results, list):
        for row in raw_results:
            if not isinstance(row, dict):
                continue
            results.append({
                "name": str(row.get("name", "startup-preflight")),
                "status": str(row.get("status", "unknown")),
                "summary": str(row.get("summary", "")),
            })
    if not results:
        results = [{
            "name": "startup-preflight",
            "status": "ok" if proc.returncode == 0 else "fail",
            "summary": _tail(proc.stderr or proc.stdout),
        }]
    return proc.returncode == 0, results


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        msg = (
            "[maintainer:startup-gate] BLOCKED: hook stdin was malformed or empty; "
            "cannot prove startup preflight state. Rerun the tool after the hook payload is healthy."
        )
        sys.stderr.write(msg + "\n")
        print(json.dumps({"blocked": True, "message": msg}))
        sys.exit(2)

    tool_name = str(data.get("tool_name", "")).strip()
    if not tool_name:
        sys.exit(0)

    tool_input = data.get("tool_input", {}) or {}
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""

    # Never block explicit startup commands; allow user/operator to run them manually.
    if tool_name == "Bash" and _is_startup_command(command):
        sys.exit(0)

    key = _session_key(data if isinstance(data, dict) else {})

    state = _read_state()
    if not state.get("state_valid", True):
        msg = (
            "[maintainer:startup-gate] BLOCKED: startup gate state file is malformed. "
            "Remove artifacts/maintainer/startup_gate_state.json and rerun the startup preflight."
        )
        sys.stderr.write(msg + "\n")
        print(json.dumps({"blocked": True, "message": msg}))
        sys.exit(2)
    sessions_ok = state.get("sessions_ok", {})
    if key in sessions_ok:
        sys.exit(0)

    ok, results = _run_startup_trio()
    now = datetime.now(timezone.utc).isoformat()
    if ok:
        sessions_ok[key] = {
            "checked_at": now,
            "results": results,
        }
        # Keep state file bounded.
        if len(sessions_ok) > 50:
            for old_key in list(sessions_ok.keys())[: len(sessions_ok) - 50]:
                sessions_ok.pop(old_key, None)
        state["sessions_ok"] = sessions_ok
        _write_state(state)
        sys.exit(0)

    detail = "; ".join(f"{r['name']}={r['status']} ({r['summary']})" for r in results)
    msg = (
        "[maintainer:startup-gate] BLOCKED: start-of-session protocol failed. "
        f"{detail}. "
        "Run: "
        "`python3 scripts/maintainer/install_hooks.py --verify` ; "
        "`python3 scripts/maintainer/startup_preflight.py --mode claude --json`."
    )
    sys.stderr.write(msg + "\n")
    print(json.dumps({"blocked": True, "message": msg}))
    sys.exit(2)


if __name__ == "__main__":
    main()
