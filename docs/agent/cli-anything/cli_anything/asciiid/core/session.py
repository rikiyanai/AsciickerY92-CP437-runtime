"""Session state persistence for asciiid CLI.

Tracks the running editor process and project state in a JSON file
at ~/.cli-anything-asciiid/session.json.
"""

import json
import os
import time
from pathlib import Path


SESSION_DIR = Path.home() / ".cli-anything-asciiid"
SESSION_FILE = SESSION_DIR / "session.json"


def _ensure_dir():
    SESSION_DIR.mkdir(parents=True, exist_ok=True)


def save_session(pid: int, binary_path: str, project_root: str,
                 loaded_map: str = "", modified: bool = False):
    """Save current session state."""
    _ensure_dir()
    data = {
        "pid": pid,
        "binary_path": binary_path,
        "project_root": project_root,
        "loaded_map": loaded_map,
        "modified": modified,
        "started_at": time.time(),
        "updated_at": time.time(),
    }
    SESSION_FILE.write_text(json.dumps(data, indent=2))


def load_session() -> dict | None:
    """Load saved session state, or None if no session exists."""
    if not SESSION_FILE.exists():
        return None
    try:
        data = json.loads(SESSION_FILE.read_text())
        # Verify process is still alive
        pid = data.get("pid")
        if pid and not _pid_alive(pid):
            clear_session()
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def update_session(**kwargs):
    """Update specific fields in the current session."""
    data = load_session()
    if not data:
        return
    data.update(kwargs)
    data["updated_at"] = time.time()
    SESSION_FILE.write_text(json.dumps(data, indent=2))


def clear_session():
    """Remove the session file."""
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()


def _pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False
