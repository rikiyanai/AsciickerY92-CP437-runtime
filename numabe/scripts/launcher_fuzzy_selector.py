"""launcher_fuzzy_selector.py — Fuzzy selector TUI component.

Provides ``fuzzy_select()`` for use in launcher menus.  Replaces bare
``_prompt_line`` / ``_numbered_picker`` calls for bounded-set prompts
(run IDs, FL IDs, recipe names, map paths, etc.).

Keyboard shortcuts (both renderer and legacy paths):

  ↑ ↓ / PgUp PgDn   Navigate list
  Home / End          Jump to first / last item
  Type                Append to filter (case-insensitive substring)
  Backspace           Remove last filter character
  ESC / q             Clear filter (if non-empty) or cancel
  Enter               Confirm selection
  o (no filter)       Open selected item in Finder / file manager

Scroll status line format matches ScrollView (FL-2957 standard):
  [N/total items]  ↑↓ PgUp/PgDn scroll  type filter  o open  Enter  q cancel

FL-3480: Universal fuzzy selector component.
"""

from __future__ import annotations

import os
import platform
import select as _select_mod
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")

_KEY_OPEN = "o"    # o (when no filter active) — Open in Finder/folder


# ── Public API ────────────────────────────────────────────────────────────────

def fuzzy_select(
    items: list[T],
    *,
    title: str = "Select",
    label_fn: Callable[[T], str] | None = None,
    path_fn: Callable[[T], "Path | str | None"] | None = None,
    default: T | None = None,
    console=None,
    renderer=None,
) -> T | None:
    """Display an interactive fuzzy selector and return the chosen item.

    Args:
        items:     Items to display.  May be any type.
        title:     Heading line shown above the filter box.
        label_fn:  Convert an item to its display string.  Defaults to str().
        path_fn:   Convert an item to a Path for Ctrl+O "Open in Finder".
                   Return None for items without a meaningful path.
        default:   Item to pre-select (cursor starts here).  Identity match.
        console:   Rich Console for legacy-path rendering (required when
                   renderer is not active).
        renderer:  Renderer instance.  When active, uses renderer content/status
                   zones; when None or inactive, falls back to console.clear().

    Returns:
        The selected item, or None if the user cancelled.
    """
    if not items:
        return None
    lf: Callable[[Any], str] = label_fn if label_fn is not None else str
    initial_cursor = 0
    if default is not None:
        try:
            initial_cursor = items.index(default)
        except ValueError:
            pass
    return FuzzySelector(
        items=items,
        title=title,
        label_fn=lf,
        path_fn=path_fn,
        initial_cursor=initial_cursor,
        console=console,
        renderer=renderer,
    ).run()


# ── FuzzySelector ─────────────────────────────────────────────────────────────

class FuzzySelector:
    """Interactive fuzzy-filter list selector.

    Works in two modes:
    - *Renderer mode*: writes to ``renderer.set_content()`` / ``set_status()``
      and reads via ``renderer.input_char(valid_keys=None)`` so all
      printable characters reach the filter as well as navigation keys.
    - *Legacy mode*: uses ``console.clear()`` + direct ``sys.stdout.write()``
      in raw mode, matching the ``_file_picker`` pattern.
    """

    def __init__(
        self,
        items: list[Any],
        *,
        title: str,
        label_fn: Callable[[Any], str],
        path_fn: Callable[[Any], "Path | str | None"] | None,
        initial_cursor: int = 0,
        console,
        renderer,
    ) -> None:
        self._items = items
        self._title = title
        self._label_fn = label_fn
        self._path_fn = path_fn
        self._console = console
        self._renderer = renderer

        self._query = ""
        self._cursor = max(0, min(initial_cursor, len(items) - 1))
        self._vtop = 0         # first visible item index (viewport top)

    # ── Entry point ───────────────────────────────────────────────────────

    def run(self) -> Any | None:
        """Run the selector loop; return chosen item or None."""
        if not _can_prompt():
            return None
        if self._renderer is not None and self._renderer.active:
            return self._run_renderer()
        return self._run_legacy()

    # ── Renderer path ─────────────────────────────────────────────────────

    def _run_renderer(self) -> Any | None:
        while True:
            filtered = self._filter()
            self._clamp(len(filtered))
            view_h = max(1, self._renderer.content_height - 3)  # -3 for title/filter/blank
            self._scroll_to_cursor(view_h)

            self._renderer.set_content(self._build_ansi_lines(filtered, view_h))
            self._renderer.set_status(_status_line(len(filtered), len(self._items), bool(self._path_fn and filtered)))
            self._renderer.render()

            key = self._renderer.input_char(valid_keys=None)
            if key is None:
                continue
            result = self._handle_key(key, filtered)
            if result is _DONE:
                return filtered[self._cursor] if filtered else None
            if result is _CANCEL:
                return None

    # ── Legacy path ───────────────────────────────────────────────────────

    def _run_legacy(self) -> Any | None:
        import termios, tty  # noqa: E401 — only available on Unix
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        result_item: Any = None
        cancelled = True
        try:
            tty.setraw(fd)
            while True:
                filtered = self._filter()
                self._clamp(len(filtered))
                cols, rows = shutil.get_terminal_size((80, 24))
                view_h = max(1, rows - 6)  # title(1) + filter(1) + blank(1) + status(2) + padding(1)
                self._scroll_to_cursor(view_h)
                self._render_raw(filtered, view_h, cols)

                key = _read_key_raw(fd)
                result = self._handle_key(key, filtered)
                if result is _DONE:
                    result_item = filtered[self._cursor] if filtered else None
                    cancelled = False
                    break
                if result is _CANCEL:
                    break
        except KeyboardInterrupt:
            pass
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except Exception:
                pass
            sys.stdout.write("\r\n")
            sys.stdout.flush()
        return None if cancelled else result_item

    # ── Filtering ─────────────────────────────────────────────────────────

    def _filter(self) -> list[Any]:
        if not self._query:
            return list(self._items)
        q = self._query.lower()
        return [item for item in self._items if q in self._label_fn(item).lower()]

    # ── Cursor / viewport ─────────────────────────────────────────────────

    def _clamp(self, n: int) -> None:
        if n == 0:
            self._cursor = 0
            self._vtop = 0
        else:
            self._cursor = max(0, min(self._cursor, n - 1))

    def _scroll_to_cursor(self, view_h: int) -> None:
        if view_h <= 0:
            return
        if self._cursor < self._vtop:
            self._vtop = self._cursor
        elif self._cursor >= self._vtop + view_h:
            self._vtop = self._cursor - view_h + 1

    # ── Key dispatch ──────────────────────────────────────────────────────

    def _handle_key(self, key: str, filtered: list[Any]) -> object:
        """Process *key*; return _DONE, _CANCEL, or None (continue)."""
        n = len(filtered)

        if key == "up":
            self._cursor = max(0, self._cursor - 1)
        elif key == "down":
            self._cursor = min(max(0, n - 1), self._cursor + 1)
        elif key == "pgup":
            self._cursor = max(0, self._cursor - 10)
        elif key == "pgdn":
            self._cursor = min(max(0, n - 1), self._cursor + 10)
        elif key == "home":
            self._cursor = 0
        elif key == "end":
            self._cursor = max(0, n - 1)
        elif key in ("\r", "\n"):
            if filtered:
                return _DONE
        elif key in ("\x1b", "q"):
            if self._query:
                # First ESC/q clears filter
                self._query = ""
                self._cursor = 0
                self._vtop = 0
            else:
                return _CANCEL
        elif key in ("\x7f", "\x08"):          # Backspace / DEL
            self._query = self._query[:-1]
            self._cursor = 0
            self._vtop = 0
        elif key == _KEY_OPEN and not self._query and self._path_fn is not None and filtered:
            _open_in_finder(self._path_fn(filtered[self._cursor]))
        elif len(key) == 1 and key.isprintable():
            self._query += key
            self._cursor = 0
            self._vtop = 0

        return None  # continue

    # ── ANSI content lines (renderer path) ────────────────────────────────

    def _build_ansi_lines(self, filtered: list[Any], view_h: int) -> list[str]:
        cols = self._renderer.cols if self._renderer is not None else 80
        lines: list[str] = []

        # Title
        lines.append(f"  \033[1m{self._title}\033[0m")
        # Filter prompt with block cursor
        lines.append(f"  > {self._query}\033[7m \033[0m")
        lines.append("")

        if not filtered:
            lines.append("  \033[2m(no matches)\033[0m")
        else:
            end = self._vtop + view_h
            for i, item in enumerate(filtered[self._vtop:end], self._vtop):
                label = _trunc(self._label_fn(item), cols - 6)
                if i == self._cursor:
                    lines.append(f"  \033[7m {label} \033[0m")
                else:
                    lines.append(f"    {label}")

        return lines

    # ── Raw render (legacy path) ──────────────────────────────────────────

    def _render_raw(self, filtered: list[Any], view_h: int, cols: int) -> None:
        out: list[str] = ["\033[2J\033[H"]  # clear screen, home cursor

        # Title + filter
        out.append(f"  \033[1m{self._title}\033[0m\r\n")
        out.append(f"  > {self._query}\033[7m \033[0m\r\n")
        out.append("\r\n")

        if not filtered:
            out.append("  \033[2m(no matches)\033[0m\r\n")
        else:
            end = self._vtop + view_h
            for i, item in enumerate(filtered[self._vtop:end], self._vtop):
                label = _trunc(self._label_fn(item), cols - 6)
                if i == self._cursor:
                    out.append(f"  \033[7m {label} \033[0m\r\n")
                else:
                    out.append(f"    {label}\r\n")

        # Scroll position + status
        n, total = len(filtered), len(self._items)
        out.append("\r\n")
        out.append(_status_line_raw(n, total, bool(self._path_fn and filtered)))
        out.append("\r\n")

        sys.stdout.write("".join(out))
        sys.stdout.flush()


# ── Sentinel objects ──────────────────────────────────────────────────────────

_DONE = object()
_CANCEL = object()


# ── Status line helpers ───────────────────────────────────────────────────────

def _status_line(n: int, total: int, has_open: bool) -> str:
    """Rich-markup status line matching ScrollView format."""
    count = f"{n}/{total}" if n != total else str(n)
    parts = [
        f"[dim]  [{count} items]  ↑↓ PgUp/PgDn scroll  type filter",
    ]
    if has_open:
        parts.append("  o open")
    parts.append("  Enter select  q/ESC cancel[/dim]")
    return "".join(parts)


def _status_line_raw(n: int, total: int, has_open: bool) -> str:
    """Plain ANSI status line for legacy path."""
    count = f"{n}/{total}" if n != total else str(n)
    hint = f"  [{count} items]  arrows navigate  type filter"
    if has_open:
        hint += "  o open"
    hint += "  Enter select  q cancel"
    return f"  \033[2m{hint}\033[0m"


# ── Open in Finder / file manager ─────────────────────────────────────────────

def _open_in_finder(raw: "Path | str | None") -> None:
    """Reveal *raw* in the platform file manager (non-blocking)."""
    if raw is None:
        return
    p = Path(raw).expanduser().resolve()
    try:
        sys_name = platform.system()
        if sys_name == "Darwin":
            if p.is_file():
                subprocess.Popen(["open", "-R", str(p)])   # reveal file in Finder
            else:
                subprocess.Popen(["open", str(p if p.is_dir() else p.parent)])
        elif sys_name == "Linux":
            target = str(p if p.is_dir() else p.parent)
            subprocess.Popen(["xdg-open", target])
        else:  # Windows
            subprocess.Popen(["explorer", f"/select,{p}"])
    except Exception:
        pass


# ── Raw key reader (legacy path) ──────────────────────────────────────────────

def _read_key_raw(fd: int) -> str:
    """Read one raw-mode keypress; return a normalized name or character.

    Named keys: "up" "down" "left" "right" "home" "end" "pgup" "pgdn"
    Control chars: returned as-is (e.g. ``\\x03`` for ^C, ``\\x7f`` for DEL)
    Regular chars: returned as single lowercase character
    Raises KeyboardInterrupt on Ctrl+C.
    """
    raw = os.read(fd, 1)
    if raw == b"\x03":
        raise KeyboardInterrupt

    if raw == b"\x1b":
        ready = _select_mod.select([fd], [], [], 0.05)
        if not ready[0]:
            return "\x1b"   # bare ESC
        b = os.read(fd, 1)
        if b != b"[":
            return "\x1b"
        ready = _select_mod.select([fd], [], [], 0.05)
        if not ready[0]:
            return "\x1b"
        c = os.read(fd, 1)
        _SIMPLE = {
            b"A": "up", b"B": "down", b"C": "right", b"D": "left",
            b"H": "home", b"F": "end",
        }
        if c in _SIMPLE:
            return _SIMPLE[c]
        if c in {b"5", b"6", b"1", b"4"}:
            ready = _select_mod.select([fd], [], [], 0.05)
            if ready[0]:
                os.read(fd, 1)  # consume trailing '~'
            return {b"5": "pgup", b"6": "pgdn", b"1": "home", b"4": "end"}.get(c, "\x1b")
        return "\x1b"

    try:
        return raw.decode(errors="ignore").lower()
    except UnicodeDecodeError:
        return ""


# ── Misc helpers ──────────────────────────────────────────────────────────────

def _can_prompt() -> bool:
    return (
        sys.stdin.isatty()
        and sys.stdout.isatty()
        and not os.environ.get("CI")
        and os.environ.get("LAUNCHER_AGENT") != "1"
    )


def _trunc(s: str, max_width: int) -> str:
    """Truncate *s* to *max_width* characters, appending '…' if truncated."""
    if max_width <= 0:
        return ""
    if len(s) <= max_width:
        return s
    return s[: max(0, max_width - 1)] + "\u2026"
