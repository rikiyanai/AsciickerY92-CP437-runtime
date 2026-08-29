"""Backend module: finds and manages the asciiid binary process.

Two execution modes:

1. **Batch mode** (recommended for pipelines) — ``run_batch()``:
   Spawns asciiid with ``--headless-batch``, writes all commands to stdin,
   reads all stdout, returns parsed output.  No daemon, no socket, no
   threading.
   One invocation ≈ 1–2 s startup; multiple commands amortised for free.

2. **Daemon mode** (for interactive long-running sessions) — ``AsciiidProcess``:
   Persistent daemon holds the subprocess; commands sent via Unix socket.
   Use when asciiid needs to stay alive between unrelated CLI calls.

Requires: asciiid binary built from the Asciicker engine source.
Batch mode does not require a visible GL editor window. Daemon mode still does.
"""

import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

from cli_anything.asciiid.utils.asciiid_daemon import (
    SOCK_PATH, PID_PATH,
    start_daemon, daemon_alive, stop_daemon,
    END_MARKER,
)


class AsciiidNotFound(RuntimeError):
    """Raised when the asciiid binary cannot be located."""
    pass


class AsciiidProcessError(RuntimeError):
    """Raised when the asciiid process encounters an error."""
    pass


def find_asciiid(project_root: str | None = None) -> str:
    """Locate the asciiid binary.

    Search order:
    1. <project_root>/.run/asciiid
    2. PATH lookup via shutil.which('asciiid')

    Args:
        project_root: Path to the Asciicker project root.

    Returns:
        Absolute path to the asciiid binary.

    Raises:
        AsciiidNotFound: If the binary cannot be found.
    """
    if project_root:
        local = Path(project_root) / ".run" / "asciiid"
        if local.is_file() and os.access(local, os.X_OK):
            return str(local.resolve())

    path = shutil.which("asciiid")
    if path:
        return path

    raise AsciiidNotFound(
        "asciiid binary not found. Build it with:\n"
        "  cd <asciicker-root> && make -f makefile_asciiid\n"
        "Binary expected at: .run/asciiid"
    )


def run_batch(
    commands: list[str],
    *,
    binary_path: str | None = None,
    project_root: str | None = None,
    timeout: float = 60.0,
) -> dict:
    """Run asciiid in batch mode: all commands in one invocation, no daemon.

    Spawns ``asciiid --headless-batch``, writes *commands* to stdin (one per
    line),
    closes stdin, waits for the process to exit, and returns all output.

    Args:
        commands:     List of MCP command strings (e.g. ``["LOAD_MAP foo.a3d",
                      "QUERY_TERRAIN_GRID 0 0 256 256 1"]``).
        binary_path:  Path to the asciiid binary (auto-detected if omitted).
        project_root: Working directory for asciiid (auto-detected if omitted).
        timeout:      Maximum wall time in seconds (default 60).

    Returns:
        ``{"lines": [...], "mcp": [...], "returncode": int}``

        * ``lines`` — every stdout line (stripped).
        * ``mcp``   — lines that started with ``[MCP]``, with the prefix
                      stripped.  Use these for structured response parsing.
        * ``returncode`` — process exit code.

    Raises:
        AsciiidNotFound:    Binary not found.
        AsciiidProcessError: Process timed out or returned non-zero.
    """
    if project_root is None:
        project_root = str(Path(__file__).parents[5])  # repo root
    if binary_path is None:
        binary_path = find_asciiid(project_root)

    stdin_text = "\n".join(commands) + "\n"

    try:
        proc = subprocess.run(
            [binary_path, "--headless-batch"],
            input=stdin_text,
            capture_output=True,
            text=True,
            cwd=project_root,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise AsciiidProcessError(
            f"asciiid --headless-batch timed out after {timeout}s"
        ) from e

    if proc.returncode != 0:
        raise AsciiidProcessError(
            f"asciiid --headless-batch exited with code {proc.returncode}: "
            f"{proc.stderr.strip()[:200] if proc.stderr else '(no stderr)'}"
        )

    all_lines = proc.stdout.splitlines()
    mcp_lines = []
    for ln in all_lines:
        if ln.startswith("[MCP]"):
            mcp_lines.append(ln[5:].strip())

    return {
        "lines": all_lines,
        "mcp": mcp_lines,
        "returncode": proc.returncode,
    }


def _send_socket(cmd: str, timeout: float = 30.0) -> list[str]:
    """Connect to daemon socket, send command, collect raw stdout lines.

    Returns list of raw output lines (not stripped of [MCP] prefix here).
    Raises AsciiidProcessError on failure.
    """
    if not daemon_alive():
        raise AsciiidProcessError(
            "No asciiid daemon running. Start one with: editor start"
        )

    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(str(SOCK_PATH))
        s.sendall((cmd + '\n').encode())

        lines = []
        buf = b''
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                decoded = line.decode(errors='replace')
                if decoded == END_MARKER:
                    s.close()
                    return lines
                lines.append(decoded)
        s.close()
        return lines
    except socket.timeout:
        raise AsciiidProcessError(f"Timeout ({timeout}s) waiting for response to: {cmd}")
    except OSError as e:
        raise AsciiidProcessError(f"Socket error: {e}")


class AsciiidProcess:
    """Client handle for the asciiid daemon.

    Provides the same public API as before but communicates over a Unix
    socket instead of holding the subprocess directly. This means the
    handle can be constructed in any CLI invocation and will reach the
    same running asciiid instance.
    """

    def __init__(self, binary_path: str, project_root: str):
        self.binary_path = binary_path
        self.project_root = project_root

    @property
    def running(self) -> bool:
        return daemon_alive()

    @property
    def pid(self) -> int | None:
        if not PID_PATH.exists():
            return None
        try:
            return int(PID_PATH.read_text().strip())
        except (ValueError, OSError):
            return None

    def start(self, timeout: float = 15.0):
        """Launch the asciiid daemon if not already running."""
        if daemon_alive():
            return  # already up
        start_daemon(self.binary_path, self.project_root, timeout=timeout + 5)

    def stop(self):
        """Stop the asciiid daemon."""
        stop_daemon()

    def send(self, command: str, timeout: float = 10.0) -> list[str]:
        """Send an MCP command and return [MCP]-stripped response lines."""
        if not self.running:
            raise AsciiidProcessError(
                "No asciiid process running. Start one with: editor start"
            )
        raw = _send_socket(command, timeout=timeout)
        lines = []
        for line in raw:
            if line.startswith('[MCP]'):
                lines.append(line[5:].strip())
        return lines

    def send_render(self, timeout: float = 30.0) -> dict:
        """Send RENDER command and parse structured response."""
        if not self.running:
            raise AsciiidProcessError("asciiid process is not running")

        raw = _send_socket('RENDER', timeout=timeout)

        result = {"width": 0, "height": 0, "format": "", "data": ""}
        in_data = False
        data_lines = []

        for line in raw:
            if "[RENDER_DATA_START]" in line:
                in_data = True
                m = re.search(r"w=(\d+)\s+h=(\d+)\s+format=(\w+)", line)
                if m:
                    result["width"] = int(m.group(1))
                    result["height"] = int(m.group(2))
                    result["format"] = m.group(3)
                continue
            if "[RENDER_DATA_END]" in line:
                in_data = False
                continue
            if in_data:
                data_lines.append(line.strip())

        result["data"] = "".join(data_lines)
        return result

    def send_terrain_grid(self, cx: float, cy: float, gw: int, gh: int,
                          scale: float, timeout: float = 15.0) -> dict:
        """Send QUERY_TERRAIN_GRID and parse structured response."""
        if not self.running:
            raise AsciiidProcessError("asciiid process is not running")

        cmd = f"QUERY_TERRAIN_GRID {cx} {cy} {gw} {gh} {scale}"
        raw = _send_socket(cmd, timeout=timeout)

        result = {
            "width": 0, "height": 0,
            "cx": cx, "cy": cy, "scale": scale,
            "grid": [],
        }
        in_data = False

        for line in raw:
            # Strip [MCP] prefix if present
            if line.startswith("[MCP]"):
                line = line[5:].lstrip()

            if "[TERRAIN_GRID_START]" in line:
                in_data = True
                m = re.search(
                    r"w=(\d+)\s+h=(\d+)\s+cx=([\d.\-]+)\s+cy=([\d.\-]+)"
                    r"\s+scale=([\d.\-]+)",
                    line,
                )
                if m:
                    result["width"] = int(m.group(1))
                    result["height"] = int(m.group(2))
                    result["cx"] = float(m.group(3))
                    result["cy"] = float(m.group(4))
                    result["scale"] = float(m.group(5))
                continue

            if "[TERRAIN_GRID_END]" in line:
                in_data = False
                continue

            if in_data and line.strip():
                row = []
                for cell in line.strip().split():
                    parts = cell.split(",")
                    if len(parts) == 2:
                        try:
                            row.append((int(parts[0]), int(parts[1])))
                        except ValueError:
                            row.append((-1, 0))
                result["grid"].append(row)

        return result

    def send_mesh_footprints(self, cx: float, cy: float, gw: int, gh: int,
                              scale: float, min_size: float = 16.0,
                              timeout: float = 15.0) -> dict:
        """Send QUERY_MESH_FOOTPRINTS and parse structured response."""
        if not self.running:
            raise AsciiidProcessError("asciiid process is not running")

        cmd = f"QUERY_MESH_FOOTPRINTS {cx} {cy} {gw} {gh} {scale} {min_size}"
        raw = _send_socket(cmd, timeout=timeout)

        result = {
            "count": 0, "cx": cx, "cy": cy,
            "scale": scale, "min_size": min_size,
            "footprints": [],
        }
        in_data = False

        for line in raw:
            if line.startswith("[MCP]"):
                line = line[5:].lstrip()

            if "[MESH_FOOTPRINTS_START]" in line:
                in_data = True
                m = re.search(r"count=(\d+)", line)
                if m:
                    result["count"] = int(m.group(1))
                continue

            if "[MESH_FOOTPRINTS_END]" in line:
                in_data = False
                continue

            if in_data and line.strip():
                parts = line.strip().split()
                if len(parts) >= 5:
                    try:
                        result["footprints"].append({
                            "name": parts[0],
                            "x_min": float(parts[1]),
                            "x_max": float(parts[2]),
                            "y_min": float(parts[3]),
                            "y_max": float(parts[4]),
                        })
                    except ValueError:
                        pass

        return result

    def __del__(self):
        pass  # daemon persists independently; nothing to clean up here
