"""tmux-based session driver for audit-mode launcher.

CLI interface for Haiku subagents to drive a live launcher instance
step-by-step through persistent tmux sessions.

Usage::

    python3 scripts/launcher_lib/audit_session.py start --session-id <id>
    python3 scripts/launcher_lib/audit_session.py key <k> --session-id <id>
    python3 scripts/launcher_lib/audit_session.py read --session-id <id>
    python3 scripts/launcher_lib/audit_session.py close --session-id <id>

All commands print a single JSON line to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent.resolve()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_POLL_INTERVAL = 0.3
_POLL_TIMEOUT = 30.0
_SESSION_PREFIX = "audit-"


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from *text*."""
    return _ANSI_RE.sub("", text)


def _session_name(session_id: str) -> str:
    """Return the tmux session name for *session_id*."""
    return f"{_SESSION_PREFIX}{session_id}"


def _state_dir_path(session_id: str) -> Path:
    """Return a predictable temp-dir path for session state isolation."""
    return Path(tempfile.gettempdir()) / f"audit-state-{session_id}"


def _capture_pane(session: str) -> str | None:
    """Capture tmux pane content.  Returns ``None`` if the session is dead."""
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", session, "-p"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _wait_for_prompt(
    session: str,
    timeout: float = _POLL_TIMEOUT,
) -> tuple[str, bool]:
    """Poll ``capture-pane`` until a ``\"> \"`` prompt appears or *timeout*.

    Returns ``(screen_text, prompt_found)``.
    """
    deadline = time.monotonic() + timeout
    last_text = ""
    while time.monotonic() < deadline:
        raw = _capture_pane(session)
        if raw is None:
            return last_text, False  # session dead
        stripped = _strip_ansi(raw)
        last_text = stripped
        # Check for prompt on the last non-empty line
        lines = stripped.rstrip().split("\n")
        if lines:
            last_line = lines[-1].rstrip()
            if last_line.endswith("> ") or last_line == ">":
                return stripped, True
        time.sleep(_POLL_INTERVAL)
    return last_text, False  # timeout


# ----------------------------------------------------------------------- #
# Commands                                                                  #
# ----------------------------------------------------------------------- #


def cmd_start(args: argparse.Namespace) -> dict:
    """Create a tmux session running the launcher in audit mode."""
    session = _session_name(args.session_id)

    # Check tmux is available
    if not shutil.which("tmux"):
        return {"status": "error", "message": "tmux not installed"}

    # Detect session collision
    check = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True,
        timeout=5,
    )
    if check.returncode == 0:
        return {"status": "error", "message": "session already exists"}

    # Create isolated state directory
    state_dir = _state_dir_path(args.session_id)
    state_dir.mkdir(parents=True, exist_ok=True)

    # Build the shell command that tmux will execute
    launcher = str(REPO_ROOT / "scripts" / "launcher.py")
    env_cmd = f"LAUNCHER_STATE_DIR={state_dir} ASCIICKER_AUDIT_MODE=1"
    cmd = f"{env_cmd} {sys.executable} {launcher} --audit-mode"

    subprocess.run(
        ["tmux", "new-session", "-d", "-s", session, "-x", "80", "-y", "24", cmd],
        capture_output=True,
        timeout=10,
    )

    # Wait for the initial prompt
    screen, found = _wait_for_prompt(session)
    if not found:
        if not screen:
            return {
                "status": "error",
                "message": "[SESSION_EOF] Launcher exited before showing prompt",
            }
        return {"status": "timeout", "screen": screen}

    return {"status": "ok", "screen": screen}


def cmd_key(args: argparse.Namespace) -> dict:
    """Send a key followed by Enter, then wait for the prompt."""
    session = _session_name(args.session_id)

    try:
        subprocess.run(
            ["tmux", "send-keys", "-t", session, args.key, "Enter"],
            capture_output=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "tmux send-keys timed out"}

    screen, found = _wait_for_prompt(session)
    if not found:
        raw = _capture_pane(session)
        if raw is None:
            return {
                "status": "error",
                "message": "[SESSION_EOF] Launcher exited",
                "screen": screen,
            }
        return {"status": "timeout", "screen": screen}

    return {"status": "ok", "screen": screen}


def cmd_read(args: argparse.Namespace) -> dict:
    """Capture the current pane without sending any key."""
    session = _session_name(args.session_id)
    raw = _capture_pane(session)
    if raw is None:
        return {
            "status": "error",
            "message": "[SESSION_EOF] Launcher exited",
            "screen": "",
        }
    return {"status": "ok", "screen": _strip_ansi(raw)}


def cmd_close(args: argparse.Namespace) -> dict:
    """Kill the tmux session and clean up its state directory."""
    session = _session_name(args.session_id)
    subprocess.run(
        ["tmux", "kill-session", "-t", session],
        capture_output=True,
        timeout=5,
    )
    # Clean up state dir
    state_dir = _state_dir_path(args.session_id)
    if state_dir.exists():
        shutil.rmtree(state_dir, ignore_errors=True)
    return {"status": "ok"}


# ----------------------------------------------------------------------- #
# Argument parser                                                           #
# ----------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python3 scripts/launcher_lib/audit_session.py",
        description="tmux-based session driver for audit-mode launcher",
    )
    sub = p.add_subparsers(dest="command")

    start_p = sub.add_parser("start")
    start_p.add_argument("--session-id", required=True)

    key_p = sub.add_parser("key")
    key_p.add_argument("key_value", metavar="key")  # positional
    key_p.add_argument("--session-id", required=True)

    read_p = sub.add_parser("read")
    read_p.add_argument("--session-id", required=True)

    close_p = sub.add_parser("close")
    close_p.add_argument("--session-id", required=True)

    return p


# ----------------------------------------------------------------------- #
# Entry point                                                               #
# ----------------------------------------------------------------------- #

_DISPATCH = {
    "start": cmd_start,
    "key": cmd_key,
    "read": cmd_read,
    "close": cmd_close,
}


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    handler = _DISPATCH.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    # Normalise the positional key arg
    if args.command == "key":
        args.key = args.key_value

    result = handler(args)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
