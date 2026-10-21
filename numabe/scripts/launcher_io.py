"""launcher_io.py — Console and I/O buffer manager for the asciicker launcher.

Owns the hidden module-global console swap that was previously scattered across
launcher.py as `_real_console`, `_buffer_io`, `_submenu_buffering`, and three
`global console` declarations.  All buffer/swap state is now encapsulated here.

FL-2790: ConsoleManager extraction.  This module is the single owner of:
  - Console creation and stderr routing
  - Submenu content buffering (StringIO swap)
  - ScrollView capture buffering
  - Banner caching
  - Audit-mode stream routing
  - stdout text helpers
  - Rich bootstrap and spinner

Usage in launcher.py::

    from launcher_io import ConsoleManager

    io_mgr = ConsoleManager(renderer=_renderer)

    # Replace module-level console:
    console = io_mgr.console

    # Submenu buffer:
    io_mgr.start_submenu_buffer()
    lines = io_mgr.flush_and_render(status="Choose:")
    io_mgr.stop_submenu_buffer()

    # ScrollView capture:
    with io_mgr.capturing_console() as buf:
        console.print(...)
    if buf is not None:
        sv = ScrollView(renderer)
        sv.show_lines(buf.getvalue().rstrip("\\n").split("\\n"))
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.launcher_ui import Renderer

REPO_ROOT = Path(__file__).parent.parent.resolve()

# ── Rich bootstrap ────────────────────────────────────────────────────────────

def bootstrap_rich() -> None:
    for candidate in (
        REPO_ROOT / ".venv" / "lib",
        REPO_ROOT / ".venv" / "Lib",
    ):
        if candidate.exists():
            for path in candidate.glob("python*/site-packages"):
                if str(path) not in sys.path:
                    sys.path.insert(0, str(path))


bootstrap_rich()

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from rich.console import Console
except ImportError:
    print("ERROR: 'rich' not installed. Run: make setup")
    sys.exit(1)

try:
    from cli_style import Spinner, SPINNER_BLOCK, COLOR_PURPLE
except ImportError:
    Spinner = None

_SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from scripts.launcher_lib.banner import _build_banner_str, _banner_target_cols
from scripts.launcher_ui import _visible_len as _ansi_len


# ── ConsoleManager ────────────────────────────────────────────────────────────

class ConsoleManager:
    """Single owner of the console-swap state machine and banner caches.

    Previously, launcher.py managed console swapping via three ``global console``
    declarations, a ``_real_console`` reference, a ``_buffer_io`` StringIO, and a
    ``_submenu_buffering`` bool.  Those are now instance attributes on this class.

    The class exposes the same functions as the old module-level helpers, but
    all state is encapsulated:

    - ``self.console`` — the current Rich Console (swaps on buffer start/stop)
    - ``self._real_console`` — the permanent, non-buffered Console
    - ``self._buffer_io`` — the StringIO used for submenu content capture
    - ``self.submenu_buffering`` — whether the console is currently swapped

    Banner caches (``_banner_cache``, ``_submenu_banner_cache``) are also owned
    here so that resize-aware rendering doesn't need module globals.
    """

    def __init__(self, renderer: Renderer, *, audit_mode: bool = False) -> None:
        self._renderer = renderer
        self._audit_mode = audit_mode
        self._real_console = Console(stderr=self._diagnostic_stream_is_stderr())
        self.console = self._real_console
        self._buffer_io = io.StringIO()
        self.submenu_buffering: bool = False
        self._banner_cache: tuple[int, list[str]] | None = None
        self._submenu_banner_cache: tuple[int, int, list[str]] | None = None

    # ── Stream routing ────────────────────────────────────────────────────

    def _diagnostic_stream_is_stderr(self) -> bool:
        if self._audit_mode:
            return False
        return (
            not sys.stdout.isatty()
            or os.environ.get("LAUNCHER_AGENT") == "1"
            or os.environ.get("NO_COLOR") == "1"
        )

    @staticmethod
    def write_stdout_text(text: str) -> None:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()

    @staticmethod
    def write_stdout_lines(lines: list[str]) -> None:
        for line in lines:
            ConsoleManager.write_stdout_text(line)

    # ── Loading spinner ────────────────────────────────────────────────────

    @contextlib.contextmanager
    def loading(self, msg: str = "Loading"):
        if Spinner is not None:
            with Spinner(msg, frames=SPINNER_BLOCK, color=COLOR_PURPLE, stream=sys.stderr):
                yield
        else:
            sys.stderr.write(f"  {msg}...\r")
            sys.stderr.flush()
            try:
                yield
            finally:
                sys.stderr.write("\r\033[K")
                sys.stderr.flush()

    # ── Submenu buffer (StringIO-swap protocol) ──────────────────────────

    def start_submenu_buffer(self) -> None:
        if not self._renderer.active:
            return
        self._buffer_io.truncate(0)
        self._buffer_io.seek(0)
        self.console = Console(
            file=self._buffer_io,
            force_terminal=True,
            color_system="truecolor",
            width=self._renderer.cols,
        )
        self.submenu_buffering = True

    def read_buffer_lines(self) -> list[str]:
        text = self._buffer_io.getvalue()
        self._buffer_io.truncate(0)
        self._buffer_io.seek(0)
        if not text.strip():
            return []
        return text.rstrip("\n").split("\n")

    def flush_and_render(self, status: str = "") -> list[str]:
        lines = self.read_buffer_lines()
        self._renderer.set_content(lines)
        self._renderer.set_status(status)
        self._renderer.render()
        return lines

    def stop_submenu_buffer(self) -> None:
        self.console = self._real_console
        self.submenu_buffering = False
        self._buffer_io.truncate(0)
        self._buffer_io.seek(0)

    @contextlib.contextmanager
    def capturing_console(self):
        """Context manager for scrollable info-card guide functions.

        When the renderer is active, swaps console to a fresh StringIO buffer
        sized to the renderer's column width.  On exit, yields the buffer so
        the caller can feed captured lines to ScrollView.show_lines().

        When the renderer is inactive, yields None (caller falls back to
        _pause()).
        """
        if not self._renderer.active:
            yield None
            return
        width = max(60, self._renderer.cols - 4)
        cap_io = io.StringIO()
        cap_con = Console(
            file=cap_io,
            force_terminal=True,
            color_system="truecolor",
            width=width,
        )
        saved_console = self.console
        saved_buffering = self.submenu_buffering
        self.console = cap_con
        self.submenu_buffering = False
        try:
            yield cap_io
        finally:
            self.console = saved_console
            self.submenu_buffering = saved_buffering

    # ── Banner cache ──────────────────────────────────────────────────────

    def cached_banner_lines(self, cols: int) -> list[str]:
        target = _banner_target_cols(cols)
        if self._banner_cache is not None and self._banner_cache[0] == target:
            return list(self._banner_cache[1])
        banner = _build_banner_str(target)
        lines = banner.splitlines() if banner else []
        self._banner_cache = (target, lines)
        return list(lines)

    def cached_submenu_banner_lines(self, cols: int) -> list[str]:
        target = max(8, int(_banner_target_cols(cols) * 0.70))
        if (
            self._submenu_banner_cache is not None
            and self._submenu_banner_cache[0] == target
            and self._submenu_banner_cache[1] == cols
        ):
            return list(self._submenu_banner_cache[2])
        banner = _build_banner_str(target)
        raw_lines = banner.splitlines() if banner else []
        centered: list[str] = []
        for line in raw_lines:
            vis_w = _ansi_len(line)
            pad = max(0, (cols - vis_w) // 2)
            centered.append(" " * pad + line)
        self._submenu_banner_cache = (target, cols, centered)
        return list(centered)

    # ── Diagnostic helpers (for audit mode) ───────────────────────────────

    def set_audit_mode(self, enabled: bool) -> None:
        self._audit_mode = enabled
        self._real_console = Console(stderr=self._diagnostic_stream_is_stderr())
        if not self.submenu_buffering:
            self.console = self._real_console
