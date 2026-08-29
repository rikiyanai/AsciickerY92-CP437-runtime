"""Editor lifecycle management.

Handles starting, stopping, and connecting to a running asciiid --mcp process.
The process is held by a persistent daemon (asciiid_daemon); this module is
a thin session layer on top.
"""

from cli_anything.asciiid.core.session import (
    save_session, load_session, clear_session, update_session,
)
from cli_anything.asciiid.utils.asciiid_backend import (
    AsciiidProcess, find_asciiid, AsciiidNotFound, AsciiidProcessError,
)
from cli_anything.asciiid.utils.asciiid_daemon import daemon_alive


# Module-level client handle (reconstructed per-invocation from session/daemon)
_process: AsciiidProcess | None = None


def _make_client(project_root: str, binary_path: str) -> AsciiidProcess:
    """Construct (or re-use) the AsciiidProcess client handle."""
    global _process
    if _process is None or not _process.running:
        _process = AsciiidProcess(binary_path, project_root)
    return _process


def start(project_root: str, binary_path: str | None = None,
          timeout: float = 15.0) -> dict:
    """Launch (or reconnect to) the asciiid daemon.

    Args:
        project_root: Path to the Asciicker project root.
        binary_path: Optional explicit path to the asciiid binary.
        timeout: Startup timeout in seconds.

    Returns:
        Dict with status, pid, binary_path, project_root.
    """
    global _process

    if not binary_path:
        binary_path = find_asciiid(project_root)

    if daemon_alive():
        _process = _make_client(project_root, binary_path)
        session = load_session()
        return {
            "status": "already_running",
            "pid": _process.pid,
            "binary_path": binary_path,
            "project_root": project_root,
            "loaded_map": session.get("loaded_map", "") if session else "",
        }

    _process = AsciiidProcess(binary_path, project_root)
    _process.start(timeout=timeout)

    save_session(
        pid=_process.pid or 0,
        binary_path=binary_path,
        project_root=project_root,
    )

    return {
        "status": "started",
        "pid": _process.pid,
        "binary_path": binary_path,
        "project_root": project_root,
    }


def stop() -> dict:
    """Stop the running asciiid daemon."""
    global _process

    if daemon_alive():
        pid = _process.pid if _process else None
        if _process:
            _process.stop()
        _process = None
        clear_session()
        return {"status": "stopped", "pid": pid}

    _process = None
    clear_session()
    return {"status": "not_running"}


def status() -> dict:
    """Check the current editor/daemon status."""
    global _process

    if daemon_alive():
        session = load_session()
        pid = None
        if _process:
            pid = _process.pid
        elif session:
            pid = session.get("pid")
        return {
            "status": "running",
            "pid": pid,
            "loaded_map": session.get("loaded_map", "") if session else "",
            "modified": session.get("modified", False) if session else False,
        }

    # Check for stale session
    session = load_session()
    if session:
        return {
            "status": "stale_session",
            "pid": session.get("pid"),
            "message": "Session file exists but daemon is not running",
        }

    return {"status": "not_running"}


def get_process() -> AsciiidProcess:
    """Get an active process handle, reconnecting from session if needed.

    Returns:
        An AsciiidProcess that talks to the running daemon.

    Raises:
        AsciiidProcessError: If no daemon is running.
    """
    global _process

    if daemon_alive():
        if _process is None:
            # Reconstruct client from session
            session = load_session()
            if session:
                binary_path = session.get("binary_path", "")
                project_root = session.get("project_root", ".")
                _process = AsciiidProcess(binary_path, project_root)
            else:
                # Daemon running but no session: minimal client
                _process = AsciiidProcess("", ".")
        return _process

    raise AsciiidProcessError(
        "No asciiid process running. Start one with: editor start"
    )


def send(command: str, timeout: float = 10.0) -> list[str]:
    """Send an MCP command to the running editor."""
    return get_process().send(command, timeout=timeout)
