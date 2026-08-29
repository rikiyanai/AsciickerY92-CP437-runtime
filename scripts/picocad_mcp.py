#!/usr/bin/env python3
"""
picocad_mcp.py — AsciiidBatchSession adapter + module-level pipeline helpers.

Provides an ``AsciiidBatchSession`` context manager that drives asciiid's
``--batch`` stdin/stdout MCP protocol, and module-level ``convert_picocad``,
``audit_picocad``, and ``pipeline_picocad`` functions that compose conversion
with batch-session operations as a single call.

Requirements
------------
- asciiid ``--batch`` binary (default: repo-relative ``.run/asciiid``)
- ``DISPLAY`` set or ``xvfb-run`` in PATH (asciiid requires a display)
- ``python3`` in PATH (checked at runtime)
- ``scripts/picocad_to_akm.py`` in the same repo for import

Usage
-----
    from scripts.picocad_mcp import AsciiidBatchSession, convert_picocad

    # Convert a GLTF to AKM (no asciiid needed)
    akm_path = convert_picocad("model.gltf")

    # Run a full pipeline inside a batch session
    result = pipeline_picocad("model.gltf", "map.a3d", 50, 50, 0)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ensure_assets_dir(repo: Path) -> None:
    """Create ``assets/meshes/`` if it does not exist."""
    (repo / "assets" / "meshes").mkdir(parents=True, exist_ok=True)


def find_repo_root(start: Path | None = None) -> Path:
    """Walk parents from *start* (or CWD) looking for ``assets/meshes/``.

    Raises ``FileNotFoundError`` if not found.  Does not create directories.
    """
    if start is None:
        start = Path.cwd()
    for parent in [start] + list(start.parents):
        if (parent / "assets" / "meshes").is_dir():
            return parent
        # Also accept the repo root marker (but do NOT create directories here)
        if (parent / "scripts" / "picocad_to_akm.py").is_file():
            return parent
    raise FileNotFoundError(
        "Could not find repo root (looked for assets/meshes/ or "
        "scripts/picocad_to_akm.py from " + str(start.resolve()) + ")"
    )


def _resolve_asciiid_bin(asciiid_bin: str | None = None) -> str:
    """Resolve the asciiid binary path.

    Precedence: explicit arg > ``ASCIIID_BIN`` env var > ``.run/asciiid``.
    """
    if asciiid_bin is None:
        asciiid_bin = os.environ.get("ASCIIID_BIN")
    if asciiid_bin is None:
        repo = find_repo_root()
        asciiid_bin = str(repo / ".run" / "asciiid")
    return asciiid_bin


# ---------------------------------------------------------------------------
# AsciiidBatchSession
# ---------------------------------------------------------------------------

class AsciiidBatchSession:
    """Context manager wrapping an asciiid ``--batch`` subprocess.

    Dispatches commands via stdin and parses ``[MCP]``-prefixed responses
    from stdout.  The first ``[MCP]`` line per command is always the echo
    (``[MCP] Received command: …`` printed *before* the handler runs);
    ``send()`` skips it and returns the next ``[MCP]`` line (the actual
    Success or Error response).

    Parameters
    ----------
    asciiid_bin : str, optional
        Path to the asciiid binary.  Auto-resolved if omitted.
    map_path : str, optional
        If given, ``LOAD_MAP`` is sent immediately after entering.
    timeout : float
        Seconds to wait for a response from ``send()`` (default 30).
    """

    def __init__(
        self,
        asciiid_bin: str | None = None,
        map_path: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.asciiid_bin = _resolve_asciiid_bin(asciiid_bin)
        self.map_path = map_path
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __enter__(self) -> "AsciiidBatchSession":
        # --- display pre-flight ---
        # macOS (Darwin) uses SDL/Cocoa and does not need DISPLAY or xvfb-run.
        # Only gate on Linux where X11/DISPLAY is required.
        import platform
        if platform.system() != "Darwin":
            if not os.environ.get("DISPLAY") and shutil.which("xvfb-run") is None:
                raise RuntimeError(
                    "asciiid requires a display: set DISPLAY or install xvfb-run "
                    "before launching AsciiidBatchSession"
                )

        if not os.path.isfile(self.asciiid_bin):
            raise FileNotFoundError(
                f"asciiid binary not found at {self.asciiid_bin}"
            )

        # Binary pipes (bufsize=0, text=False): stdin and stdout are raw byte
        # streams.  We decode lines ourselves in _stdout_readline via os.read(),
        # which sidesteps Python's text-mode buffering bug on macOS where
        # select.select() reports data-ready but readline() still returns ''.
        # NOTE: do NOT call .readline() or .read() on self._proc.stdout — that
        # would silently drain the BufferedReader buffer, corrupting _raw_buf.
        # All stdout reads must go through _stdout_readline.
        self._proc = subprocess.Popen(
            [self.asciiid_bin, "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            text=False,
        )

        # If a map was requested, load it immediately
        if self.map_path:
            resp = self.send_until(f"LOAD_MAP {self.map_path}", "Map loaded")
            if "Error:" in resp:
                raise RuntimeError(
                    f"AsciiidBatchSession: LOAD_MAP {self.map_path} failed: {resp}"
                )

        return self

    def __exit__(self, *exc_info) -> None:
        if self._proc is None:
            return
        try:
            self._proc.stdin.write(b"QUIT\n")
            self._proc.stdin.flush()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=5)
        self._proc = None

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------

    def send(self, command: str) -> str:
        """Write *command* to stdin and return the ``[MCP]`` response.

        The echo line (``[MCP] Received command: …``) is consumed and
        discarded; the first subsequent ``[MCP]`` line is returned.
        For commands that produce multiple ``[MCP]`` lines (e.g.
        ``LOAD_MAP``), use :meth:`send_until` instead.

        Raises ``AsciiidTimeoutError`` if no response arrives within *timeout*.
        """
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("AsciiidBatchSession is not running")

        self._proc.stdin.write((command + "\n").encode())
        self._proc.stdin.flush()

        deadline = time.monotonic() + self.timeout

        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            line = self._stdout_readline(timeout_remaining=remaining)
            if line is None:
                ret = self._proc.poll()
                stderr = ""
                if self._proc.stderr:
                    stderr = self._proc.stderr.read()
                raise RuntimeError(
                    f"asciiid process exited (code={ret}) while waiting for "
                    f"response to '{command}'. stderr: {stderr.strip()}"
                )

            if "[MCP]" not in line:
                continue

            # Skip the echo line (content-based detection is more robust than
            # positional — future commands that produce non-echo MCP output
            # before the real response would break a positional counter)
            if "Received command" in line:
                continue

            return line.strip()

        raise AsciiidTimeoutError(
            f"AsciiidBatchSession.send('{command}') timed out after "
            f"{self.timeout}s"
        )

    def send_until(self, command: str, pattern: str) -> str:
        """Send *command* and keep reading until a line contains *pattern*.

        The echo line (``[MCP] Received command: …``) is consumed and
        discarded.  All intermediate ``[MCP]`` lines are discarded until
        one containing *pattern* **or** ``Error:`` is found — that line
        is returned.

        This is needed for commands like ``LOAD_MAP`` that emit multiple
        ``[MCP]`` progress lines before the final response.  Error lines
        are returned immediately so callers don't hang waiting for a
        pattern that will never arrive.
        """
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("AsciiidBatchSession is not running")

        self._proc.stdin.write((command + "\n").encode())
        self._proc.stdin.flush()

        deadline = time.monotonic() + self.timeout

        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            line = self._stdout_readline(timeout_remaining=remaining)
            if line is None:
                ret = self._proc.poll()
                stderr = ""
                if self._proc.stderr:
                    stderr = self._proc.stderr.read()
                raise RuntimeError(
                    f"asciiid process exited (code={ret}) while waiting for "
                    f"response to '{command}'. stderr: {stderr.strip()}"
                )

            if "[MCP]" not in line:
                continue

            # Skip the echo line
            if "Received command" in line:
                continue

            # Return immediately on error or target pattern
            if "Error:" in line or pattern in line:
                return line.strip()

        raise AsciiidTimeoutError(
            f"AsciiidBatchSession.send_until('{command}', '{pattern}') "
            f"timed out after {self.timeout}s"
        )

    def render(self) -> str:
        """Send ``RENDER`` and return the base64-encoded ANSI data.

        Reads lines between ``[RENDER_DATA_START]`` and
        ``[RENDER_DATA_END]`` markers.
        """
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("AsciiidBatchSession is not running")

        self._proc.stdin.write(b"RENDER\n")
        self._proc.stdin.flush()

        deadline = time.monotonic() + self.timeout
        collecting = False
        lines: list[str] = []

        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            line = self._stdout_readline(timeout_remaining=remaining)
            if line is None:
                ret = self._proc.poll()
                raise RuntimeError(
                    f"asciiid process exited (code={ret}) during render"
                )

            stripped = line.strip()

            if stripped.startswith("[RENDER_DATA_START]"):
                collecting = True
                continue
            if stripped == "[RENDER_DATA_END]":
                break
            if collecting:
                lines.append(stripped)
        else:
            raise AsciiidTimeoutError(
                f"AsciiidBatchSession.render() timed out after {self.timeout}s"
            )

        return "".join(lines)

    # ------------------------------------------------------------------
    # Thin wrappers
    # ------------------------------------------------------------------

    def load_map(self, path: str) -> str:
        """Send ``LOAD_MAP <path>`` and return the response."""
        return self.send_until(f"LOAD_MAP {path}", "Map loaded")

    def query_terrain_height(self, x: float, y: float) -> float:
        """Return the raw terrain height at world position (x, y).

        The value is the uint16 from the A3D height map — pass it directly
        as *z* to :meth:`place_mesh`.  Raises ``RuntimeError`` if no
        terrain is loaded or no patch exists at the given position.
        """
        resp = self.send(f"QUERY_TERRAIN_HEIGHT {x} {y}")
        if "Error:" in resp:
            raise RuntimeError(f"query_terrain_height({x}, {y}) failed: {resp}")
        try:
            return float(resp.split("->")[-1].strip())
        except (IndexError, ValueError) as exc:
            raise RuntimeError(
                f"query_terrain_height: unexpected response: {resp}"
            ) from exc

    def place_mesh(self, name: str, x: float, y: float, z: float,
                   scale: float = 1.0) -> str:
        """Send ``PLACE_MESH`` and return the response."""
        return self.send(f"PLACE_MESH {name} {x} {y} {z} {scale}")

    def save(self) -> str:
        """Send ``SAVE`` and return the response."""
        return self.send("SAVE")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _stdout_readline(self, timeout_remaining: float = 0.5) -> str | None:
        """Read one line from stdout, assembling it from a raw byte buffer.

        Uses ``select.select`` to detect when data is ready, then reads
        raw bytes via ``os.read()`` into an internal buffer (``self._raw_buf``).
        Complete lines (ending in ``\n``) are decoded and returned.  If the
        buffer has a partial line when select times out, the caller will
        re-check its deadline and loop — we do not block waiting for more.
        A line that ends with EOF (no trailing ``\n``) is returned as-is;
        subsequent calls return ``None``.
        """
        import os as _os
        import select as _select

        if self._proc is None or self._proc.stdout is None:
            return None

        if not hasattr(self, "_raw_buf"):
            self._raw_buf: bytes = b""

        while True:
            nl = self._raw_buf.find(b"\n")
            if nl >= 0:
                line_bytes = self._raw_buf[: nl + 1]
                self._raw_buf = self._raw_buf[nl + 1 :]
                try:
                    return line_bytes.decode("utf-8", errors="replace")
                except Exception:
                    return None

            if self._proc.poll() is not None:
                if self._raw_buf:
                    buf = self._raw_buf
                    self._raw_buf = b""
                    try:
                        return buf.decode("utf-8", errors="replace")
                    except Exception:
                        return None
                return None

            fd = self._proc.stdout.fileno()
            if timeout_remaining <= 0:
                try:
                    chunk = _os.read(fd, 65536)
                except (BlockingIOError, OSError):
                    chunk = b""
                if not chunk:
                    return None
                self._raw_buf += chunk
                continue

            ready, _, _ = _select.select([fd], [], [], timeout_remaining)
            if not ready:
                return ""

            try:
                chunk = _os.read(fd, 65536)
            except (BlockingIOError, OSError):
                chunk = b""
            if not chunk:
                if self._raw_buf:
                    buf = self._raw_buf
                    self._raw_buf = b""
                    try:
                        return buf.decode("utf-8", errors="replace")
                    except Exception:
                        return None
                return None
            self._raw_buf += chunk



# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AsciiidTimeoutError(Exception):
    """Raised when an asciiid batch command times out."""
    pass

def convert_picocad(
    gltf_path: str,
    output: str | None = None,
    merge: bool = False,
    passthrough: bool = False,
) -> Path:
    """Convert a GLTF file to AKM format via ``picocad_to_akm``.

    The output AKM is placed in ``assets/meshes/<stem>.akm`` (unless
    *output* is given explicitly).

    Returns the ``Path`` to the output AKM file.
    """
    from scripts.picocad_to_akm import convert_gltf_to_akm

    gltf = Path(gltf_path)
    if not gltf.is_file():
        raise FileNotFoundError(f"GLTF file not found: {gltf_path}")

    repo = find_repo_root()
    if output is None:
        stem = gltf.stem
        output = str(repo / "assets" / "meshes" / f"{stem}.akm")
        ensure_assets_dir(repo)

    result = convert_gltf_to_akm(
        str(gltf.resolve()),
        output_path=output,
        merge=merge,
        solid=True,
    )
    return Path(result) if isinstance(result, str) else Path(output)


def audit_picocad(akm_path: str) -> int:
    """Audit an AKM file for SAFE_LEVELS compliance.

    Returns 0 if all colors are palette-safe, 1 if violations found.
    """
    from scripts.picocad_to_akm import audit_akm
    return audit_akm(akm_path)


def pipeline_picocad(
    gltf_path: str,
    map_path: str,
    x: float,
    y: float,
    z: float | None = None,
    scale: float = 1.0,
    render: bool = False,
    save: bool = False,
    asciiid_bin: str | None = None,
) -> dict:
    """One-shot pipeline: convert → copy → place → (render) → (save).

    Parameters
    ----------
    gltf_path : str
        Source GLTF file to convert.
    map_path : str
        Map file (``.a3d``) to load.
    x, y : float
        World-space position for ``PLACE_MESH``.
    z : float or None
        Height for ``PLACE_MESH``.  When *None* (default), the terrain
        height at (x, y) is queried automatically via
        ``QUERY_TERRAIN_HEIGHT`` and used as z.  Pass an explicit value
        only when you want to override the terrain surface.
    scale : float
        Scale factor for the mesh.
    render : bool
        If True, also run ``RENDER`` and return base64 data.
    save : bool
        If True, send ``SAVE`` after placing the mesh.
    asciiid_bin : str, optional
        Path to asciiid binary (auto-resolved if omitted).

    Returns
    -------
    dict
        Keys: ``akm_path`` (``Path``), ``response`` (str),
        ``render_b64`` (str or None), ``z_used`` (float).

    Raises
    ------
    RuntimeError
        If any stage fails (AKM is left on disk on failure).
    """
    # Step 1: convert
    akm = convert_picocad(gltf_path)
    akm_path = Path(akm)
    stem = akm_path.stem

    # Step 2: open session, load map, (auto-snap z), place mesh
    with AsciiidBatchSession(asciiid_bin=asciiid_bin) as sess:
        resp_load = sess.load_map(map_path)
        if "[MCP] Error:" in resp_load:
            raise RuntimeError(
                f"pipeline_picocad: LOAD_MAP failed: {resp_load}"
            )

        if z is None:
            z = sess.query_terrain_height(x, y)

        resp_place = sess.place_mesh(stem, x, y, z, scale)
        if "[MCP] Error:" in resp_place:
            raise RuntimeError(
                f"pipeline_picocad: PLACE_MESH failed: {resp_place}"
            )

        if save:
            sess.save()

        render_b64: str | None = None
        if render:
            render_b64 = sess.render()

    return {
        "akm_path": akm_path,
        "response": resp_place,
        "render_b64": render_b64,
        "z_used": z,
    }


# ---------------------------------------------------------------------------
# CLI entry point (quick testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="picocad_mcp adapter tools")
    parser.add_argument("action", choices=["convert", "audit", "pipeline"])
    parser.add_argument("input", help="GLTF or AKM path")
    parser.add_argument("--map", help="Map file for pipeline")
    parser.add_argument("--at", nargs=3, type=float, metavar=("X", "Y", "Z"),
                        help="Position for PLACE_MESH")
    parser.add_argument("--output", help="Output path")
    args = parser.parse_args()

    if args.action == "convert":
        out = convert_picocad(args.input, output=args.output)
        print(f"Wrote: {out}")
    elif args.action == "audit":
        rc = audit_picocad(args.input)
        sys.exit(rc)
    elif args.action == "pipeline":
        if not args.map or not args.at:
            parser.error("pipeline requires --map and --at")
        result = pipeline_picocad(
            args.input, args.map, *args.at, render=True
        )
        print(f"AKM: {result['akm_path']}")
        print(f"Place response: {result['response']}")
        if result["render_b64"]:
            print(f"Render data: {len(result['render_b64'])} bytes base64")
