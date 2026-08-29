"""Full-screen renderer that owns the alternate screen for the launcher session.

Generalises the rain animation's proven rendering pattern (altscreen + cursor
positioning + frame buffer) to all launcher views.  Rich is used as a string
formatter only — nothing in this module writes Rich output to stdout.

Pattern origin: scripts/launcher.py _rain_root_choice() / _render_rain_root_frame()
FL references: FL-1924 (DECSTBM falsified), FL-1878 (Spinner suppression)
"""

from __future__ import annotations

import atexit
import contextlib
import io
import os
import shutil
import signal
import sys
from typing import Callable

# Platform-guarded imports for raw terminal input
_HAS_TERMIOS = False
try:
    import select
    import termios
    import tty

    _HAS_TERMIOS = True
except ImportError:
    pass  # Windows or non-TTY — renderer degrades gracefully (R13)

# ── ANSI helpers ──────────────────────────────────────────────────────────────

_CSI = "\033["
_ENTER_ALTSCREEN = f"{_CSI}?1049h"
_EXIT_ALTSCREEN = f"{_CSI}?1049l"
_HIDE_CURSOR = f"{_CSI}?25l"
_SHOW_CURSOR = f"{_CSI}?25h"
_CURSOR_HOME = f"{_CSI}H"


def _move_to(row: int, col: int = 1) -> str:
    return f"{_CSI}{row};{col}H"


def _erase_line() -> str:
    return f"{_CSI}K"


# ── Renderer ──────────────────────────────────────────────────────────────────


class Renderer:
    """Single-owner full-screen renderer for the launcher.

    Lifecycle::

        r = Renderer()
        if r.can_render():
            r.activate()      # enter altscreen + raw mode
            ...
            r.deactivate()    # exit altscreen + restore terminal

    Zone model::

        ┌─── banner zone (top, persistent) ───┐
        │ ASCII banner + status badges         │
        ├─── content zone (middle, variable) ──┤
        │ Menu items / rain / scroll view      │
        ├─── status zone (bottom 1-2 rows) ────┤
        │ Breadcrumb / prompt / scroll pos     │
        └──────────────────────────────────────┘
    """

    def __init__(self) -> None:
        self._active = False

        # Terminal state
        self._cols = 80
        self._rows = 24
        self._old_termios: list | None = None

        # Zone contents (lists of pre-formatted strings, one per row)
        self._banner_lines: list[str] = []
        self._content_lines: list[str] = []
        self._status_lines: list[str] = []

        # Resize tracking
        self._resize_pending = False
        self._prev_sigwinch: signal.Handlers = signal.SIG_DFL

        # Callbacks
        self._on_spinner_msg: Callable[[str], None] | None = None

    # ── Capability detection ──────────────────────────────────────────────

    def can_render(self) -> bool:
        """Whether the renderer can take over the screen.

        Returns False for non-TTY, CI, TERM=dumb, LAUNCHER_AGENT, audit mode,
        or missing termios (Windows).  Matches _rain_ui_enabled() guards.
        """
        if os.environ.get("ASCIICKER_AUDIT_MODE") == "1":
            return False
        if not _HAS_TERMIOS:
            return False
        if not sys.stdout.isatty() or not sys.stdin.isatty():
            return False
        if os.environ.get("CI"):
            return False
        if os.environ.get("LAUNCHER_AGENT") == "1":
            return False
        term = os.environ.get("TERM", "")
        if term == "dumb":
            return False
        if os.environ.get("ASCIICKER_LAUNCHER_PLAIN") == "1":
            return False
        return True

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def activate(self) -> None:
        """Enter alternate screen and raw mode.  Call once at session start."""
        if self._active:
            return
        self._query_size()
        fd = sys.stdin.fileno()
        self._old_termios = termios.tcgetattr(fd)
        tty.setraw(fd)
        sys.stdout.write(_ENTER_ALTSCREEN + _HIDE_CURSOR)
        sys.stdout.flush()
        self._install_sigwinch()
        self._active = True
        atexit.register(self._atexit_cleanup)

    def deactivate(self) -> None:
        """Exit alternate screen and restore terminal.  Safe to call when inactive."""
        if not self._active:
            return
        self._active = False
        self._uninstall_sigwinch()
        sys.stdout.write(_EXIT_ALTSCREEN + _SHOW_CURSOR)
        sys.stdout.flush()
        if self._old_termios is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_termios)
            except (OSError, ValueError):
                pass
            self._old_termios = None

    @property
    def active(self) -> bool:
        return self._active

    @property
    def cols(self) -> int:
        return self._cols

    @property
    def rows(self) -> int:
        return self._rows

    # ── Pause / Resume (for native binaries and unported submenus) ────────

    @contextlib.contextmanager
    def paused(self):
        """Context manager: exit altscreen + restore cooked mode, re-enter on exit.

        Used for _run_or_build() (native SDL binaries / make builds) and
        the unported-submenu fallback path (R9).
        """
        if not self._active:
            yield
            return
        # Save current state and exit
        sys.stdout.write(_EXIT_ALTSCREEN + _SHOW_CURSOR)
        sys.stdout.flush()
        if self._old_termios is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_termios)
            except (OSError, ValueError):
                pass
        self._active = False
        try:
            yield
        finally:
            # Re-enter altscreen + raw mode
            self._query_size()
            if self._old_termios is None:
                self._old_termios = termios.tcgetattr(sys.stdin.fileno())
            tty.setraw(sys.stdin.fileno())
            sys.stdout.write(_ENTER_ALTSCREEN + _HIDE_CURSOR)
            sys.stdout.flush()
            self._active = True
            self.render()

    # ── Cooked input (for input() calls) ──────────────────────────────────

    @contextlib.contextmanager
    def cooked_input(self):
        """Context manager: temporarily restore cooked termios for input().

        Stays in altscreen — only switches the terminal line discipline so
        input() gets echo, backspace, and Enter termination.
        """
        if not self._active or self._old_termios is None:
            yield
            return
        # Restore cooked settings (echo, line editing, signals)
        try:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_termios)
        except (OSError, ValueError):
            yield
            return
        # Show cursor during line input
        sys.stdout.write(_SHOW_CURSOR)
        sys.stdout.flush()
        try:
            yield
        finally:
            # Re-enter raw mode
            tty.setraw(sys.stdin.fileno())
            sys.stdout.write(_HIDE_CURSOR)
            sys.stdout.flush()

    # ── Zone setters ──────────────────────────────────────────────────────

    def set_banner(self, lines: list[str]) -> None:
        self._banner_lines = list(lines)

    def set_content(self, lines: list[str]) -> None:
        self._content_lines = list(lines)

    def set_status(self, lines: list[str] | str) -> None:
        if isinstance(lines, str):
            lines = [lines]
        self._status_lines = list(lines)

    # ── Rendering ─────────────────────────────────────────────────────────

    def render(self) -> None:
        """Repaint the full screen from current zone contents."""
        if not self._active:
            return

        if self._resize_pending:
            self._resize_pending = False
            self._query_size()

        rows, cols = self._rows, self._cols
        banner_h = len(self._banner_lines)
        status_h = len(self._status_lines)
        content_h = max(0, rows - banner_h - status_h)

        frame = io.StringIO()
        frame.write(_CURSOR_HOME)

        # Banner zone
        for i, line in enumerate(self._banner_lines[:rows]):
            frame.write(_pad_or_truncate(line, cols))
            if i < rows - 1:
                frame.write("\r\n")

        # Content zone
        content_start = banner_h
        for i in range(content_h):
            if content_start + i >= rows:
                break
            if i < len(self._content_lines):
                frame.write(_pad_or_truncate(self._content_lines[i], cols))
            else:
                frame.write(" " * cols)
            if content_start + i < rows - 1:
                frame.write("\r\n")

        # Status zone
        status_start = rows - status_h
        for i, line in enumerate(self._status_lines):
            if status_start + i >= rows:
                break
            # Position cursor at status row
            frame.write(_move_to(status_start + i + 1, 1))
            frame.write(_pad_or_truncate(line, cols))

        buf = frame.getvalue()
        sys.stdout.write(buf)
        sys.stdout.flush()

    @property
    def content_height(self) -> int:
        """Rows available for content zone given current banner/status."""
        banner_h = len(self._banner_lines)
        status_h = len(self._status_lines)
        return max(0, self._rows - banner_h - status_h)

    # ── Input ─────────────────────────────────────────────────────────────

    def input_char(self, valid_keys: set[str] | None = None, timeout: float | None = None) -> str | None:
        """Read a single character in raw mode.

        If *timeout* is given (seconds), returns None on timeout — used for
        animated views like rain.  If *valid_keys* is given, loops until a
        matching key is pressed.

        Ctrl+C (0x03) raises KeyboardInterrupt (matching rain pattern).
        """
        if not self._active:
            # Fallback: cooked-mode input
            try:
                ch = input().strip()[:1].lower()
            except EOFError:
                return "q"
            return ch

        fd = sys.stdin.fileno()
        while True:
            if timeout is not None:
                ready = select.select([fd], [], [], timeout)
                if not ready[0]:
                    return None  # timeout — caller renders next frame

            raw = os.read(fd, 1)
            if raw == b"\x03":
                raise KeyboardInterrupt

            # Skip bare Enter/Return
            if raw in {b"\r", b"\n"}:
                if valid_keys and "\n" not in valid_keys and "\r" not in valid_keys:
                    continue
                # If Enter is a valid key, return it
                return "\n"

            # Parse escape sequences (arrow keys, PgUp/PgDn)
            if raw == b"\x1b":
                seq = self._read_escape_sequence(fd)
                if seq:
                    if valid_keys is None or seq in valid_keys:
                        return seq
                    continue

            try:
                ch = raw.decode(errors="ignore").lower()
            except UnicodeDecodeError:
                continue

            if valid_keys is None or ch in valid_keys:
                return ch

    def input_command(
        self,
        prompt: str = "> ",
        *,
        complete_fn: Callable[[str], list] | None = None,
        reserved_content_lines: int = 0,
        seed: str = "",
    ) -> str:
        """Read a command line with auto-suggest and tab completion.

        Renders in the content zone below *reserved_content_lines*.
        Returns typed text (possibly empty). Never raises — returns "" on cancel.
        """
        if not self._active:
            # Fallback: cooked-mode line input
            sys.stdout.write(_SHOW_CURSOR)
            sys.stdout.flush()
            try:
                raw = input(prompt).strip()
            except EOFError:
                return ""
            return raw

        buffer = seed
        cursor = len(seed)
        fd = sys.stdin.fileno()
        suggestions: list[str] = []
        selected = 0

        def _refresh():
            """Re-render prompt + buffer + suggestions."""
            banner_h = len(self._banner_lines)
            status_h = len(self._status_lines)
            available = max(0, self._rows - banner_h - status_h)
            start_row = banner_h + reserved_content_lines + 1

            frame = io.StringIO()
            # Clear reserved area below menu items
            for i in range(start_row - 1, self._rows - status_h):
                frame.write(_move_to(i + 1, 1))
                frame.write(_erase_line())

            # Prompt line
            frame.write(_move_to(start_row, 1))
            prompt_vis = _visible_len(prompt)
            buf_vis = _visible_len(buffer)
            # Cursor rendering
            if cursor < len(buffer):
                before = buffer[:cursor]
                at = buffer[cursor]
                after = buffer[cursor + 1 :]
                line = f"{prompt}{before}\033[7m{at}\033[0m{after}"
            else:
                line = f"{prompt}{buffer}\033[7m \033[0m"
            frame.write(line)
            # Pad remainder
            pad = max(0, self._cols - prompt_vis - buf_vis - (1 if cursor >= len(buffer) else 0))
            frame.write(" " * pad)

            # Suggestions (max available - 2 lines)
            max_sug = max(0, available - reserved_content_lines - 2)
            for i, sug in enumerate(suggestions[:max_sug]):
                row = start_row + 1 + i
                if row >= self._rows - status_h:
                    break
                frame.write(_move_to(row, 1))
                prefix = "  > " if i == selected else "    "
                display = f"{prefix}{sug}"
                frame.write(_pad_or_truncate(display, self._cols))

            sys.stdout.write(frame.getvalue())
            sys.stdout.flush()

        def _update_suggestions():
            nonlocal suggestions, selected
            if complete_fn and buffer:
                raw = complete_fn(buffer)
                # complete_fn may return list of tuples or list of strings
                processed: list[str] = []
                for item in raw:
                    if isinstance(item, tuple) and len(item) >= 1:
                        obj = item[0]
                        processed.append(getattr(obj, "name", str(obj)))
                    else:
                        processed.append(str(item))
                suggestions = processed[:8]
            else:
                suggestions = []
            selected = 0

        _update_suggestions()
        _refresh()

        while True:
            ready = select.select([fd], [], [], None)
            if not ready[0]:
                continue
            raw = os.read(fd, 1)
            if raw == b"\x03":
                raise KeyboardInterrupt
            if raw == b"\x1b":
                seq = self._read_escape_sequence(fd)
                if seq == "up":
                    if suggestions:
                        selected = (selected - 1) % len(suggestions)
                        _refresh()
                    continue
                if seq == "down":
                    if suggestions:
                        selected = (selected + 1) % len(suggestions)
                        _refresh()
                    continue
                if seq is None or seq == "":
                    # Bare escape — clear buffer
                    buffer = ""
                    cursor = 0
                    _update_suggestions()
                    _refresh()
                    continue
                # Unknown escape — ignore
                continue
            if raw == b"\t":
                # Tab completion
                if suggestions:
                    if len(suggestions) == 1:
                        buffer = suggestions[0]
                        cursor = len(buffer)
                    elif 0 <= selected < len(suggestions):
                        buffer = suggestions[selected]
                        cursor = len(buffer)
                _update_suggestions()
                _refresh()
                continue
            if raw in {b"\r", b"\n"}:
                # Enter
                sys.stdout.write(_move_to(self._rows - len(self._status_lines), 1))
                sys.stdout.write(_erase_line())
                sys.stdout.flush()
                if suggestions and buffer.strip() not in suggestions:
                    return suggestions[selected].strip()
                return buffer.strip()
            if raw == b"\x7f" or raw == b"\x08":
                # Backspace
                if cursor > 0:
                    buffer = buffer[: cursor - 1] + buffer[cursor:]
                    cursor -= 1
                    _update_suggestions()
                    _refresh()
                continue
            if raw == b"\x15":
                # Ctrl+U — clear line
                buffer = ""
                cursor = 0
                _update_suggestions()
                _refresh()
                continue
            if raw == b"\x17":
                # Ctrl+W — delete last word
                if cursor > 0:
                    # Walk back to start of word
                    i = cursor - 1
                    while i >= 0 and buffer[i] == " ":
                        i -= 1
                    while i >= 0 and buffer[i] != " ":
                        i -= 1
                    buffer = buffer[: i + 1] + buffer[cursor:]
                    cursor = i + 1
                    _update_suggestions()
                    _refresh()
                continue
            if raw == b"\x04":
                # Ctrl+D at empty buffer = cancel
                if not buffer:
                    return ""
                continue
            try:
                ch = raw.decode(errors="ignore")
            except UnicodeDecodeError:
                continue
            if ord(ch) >= 32:
                buffer = buffer[:cursor] + ch + buffer[cursor:]
                cursor += 1
                _update_suggestions()
                _refresh()

    def input_line(self, prompt: str, default: str = "") -> str:
        """Read a full line of text input.

        Temporarily restores cooked termios so input() works with echo,
        backspace, and Enter termination.  Renders *prompt* in the status
        zone before switching.
        """
        if not self._active:
            shown = default or "blank"
            try:
                raw = input(f"{prompt} [{shown}]: ").strip()
            except EOFError:
                return default
            return default if raw == "" else raw

        # Position cursor in content zone below current content
        content_bottom = len(self._banner_lines) + len(self._content_lines) + 1
        shown = default or "blank"
        prompt_text = f"{prompt} [{shown}]: "

        with self.cooked_input():
            sys.stdout.write(_move_to(min(content_bottom, self._rows), 1))
            sys.stdout.write(prompt_text)
            sys.stdout.flush()
            try:
                raw = input().strip()
            except EOFError:
                return default
        return default if raw == "" else raw

    def wait_for_key(self, prompt: str = "  Press Enter to continue.") -> None:
        """Show prompt and wait for any key press."""
        self.set_status(prompt)
        self.render()
        if self._active:
            fd = sys.stdin.fileno()
            os.read(fd, 1)
        else:
            try:
                input(prompt)
            except EOFError:
                pass

    # ── Escape sequence parsing ───────────────────────────────────────────

    @staticmethod
    def _read_escape_sequence(fd: int) -> str | None:
        """Parse multi-byte escape sequences after ESC (0x1b).

        Returns virtual key names: 'up', 'down', 'pgup', 'pgdn', 'home', 'end'.
        Returns None for unrecognised sequences.
        """
        # Read next byte with short timeout
        ready = select.select([fd], [], [], 0.05)
        if not ready[0]:
            return None  # bare Escape
        b = os.read(fd, 1)
        if b != b"[":
            return None  # not a CSI sequence

        # Read the CSI parameter
        ready = select.select([fd], [], [], 0.05)
        if not ready[0]:
            return None
        c = os.read(fd, 1)

        # Simple sequences: \033[A-D
        _SIMPLE = {b"A": "up", b"B": "down", b"C": "right", b"D": "left",
                   b"H": "home", b"F": "end"}
        if c in _SIMPLE:
            return _SIMPLE[c]

        # Extended sequences: \033[N~ (PgUp=5, PgDn=6, Home=1, End=4)
        if c in {b"5", b"6", b"1", b"4"}:
            # Read the tilde
            ready = select.select([fd], [], [], 0.05)
            if ready[0]:
                os.read(fd, 1)  # consume '~'
            _EXTENDED = {b"5": "pgup", b"6": "pgdn", b"1": "home", b"4": "end"}
            return _EXTENDED.get(c)

        return None

    # ── SIGWINCH ──────────────────────────────────────────────────────────

    def _install_sigwinch(self) -> None:
        if not hasattr(signal, "SIGWINCH"):
            return
        try:
            self._prev_sigwinch = signal.signal(signal.SIGWINCH, self._on_sigwinch)
        except (OSError, ValueError):
            pass

    def _uninstall_sigwinch(self) -> None:
        if not hasattr(signal, "SIGWINCH"):
            return
        try:
            handler = self._prev_sigwinch
            if handler is None:
                handler = signal.SIG_DFL
            signal.signal(signal.SIGWINCH, handler)
        except (OSError, ValueError):
            pass

    def _on_sigwinch(self, signum: int, frame: object) -> None:
        self._resize_pending = True
        prev = self._prev_sigwinch
        if callable(prev) and prev not in (signal.SIG_DFL, signal.SIG_IGN):
            prev(signum, frame)

    # ── Terminal size ─────────────────────────────────────────────────────

    def _query_size(self) -> None:
        try:
            size = shutil.get_terminal_size(fallback=(80, 24))
            self._cols = size.columns
            self._rows = max(size.lines, 4)  # minimum 4 rows
        except (OSError, ValueError):
            self._cols = 80
            self._rows = 24

    # ── Cleanup ───────────────────────────────────────────────────────────

    def _atexit_cleanup(self) -> None:
        """Safety net: restore terminal on unclean exit."""
        if self._active:
            try:
                self.deactivate()
            except Exception:
                # Last resort — at least try to restore the screen
                try:
                    sys.stdout.write(_EXIT_ALTSCREEN + _SHOW_CURSOR)
                    sys.stdout.flush()
                except Exception:
                    pass


# ── Helpers ───────────────────────────────────────────────────────────────────


import re as _re

_ANSI_ESCAPE_RE = _re.compile(r"\033\[[0-9;]*[A-Za-z]")


def _visible_len(s: str) -> int:
    """Visible length of a string, ignoring ANSI escape sequences."""
    return len(_ANSI_ESCAPE_RE.sub("", s))


def _pad_or_truncate(line: str, width: int) -> str:
    """Pad or truncate *line* to exactly *width* visible characters.

    Walks the string character-by-character to handle ANSI escapes correctly,
    ensuring truncation never splits an escape sequence mid-stream.
    """
    vis = _visible_len(line)
    if vis <= width:
        return line + " " * (width - vis) + _erase_line()
    # ANSI-aware truncation: walk chars, skip escape sequences, count visible
    out: list[str] = []
    count = 0
    i = 0
    while i < len(line) and count < width:
        if line[i] == "\033":
            m = _ANSI_ESCAPE_RE.match(line, i)
            if m:
                out.append(m.group())
                i = m.end()
                continue
        out.append(line[i])
        count += 1
        i += 1
    # Reset any open styles so they don't bleed into padding
    out.append("\033[0m")
    return "".join(out) + _erase_line()
