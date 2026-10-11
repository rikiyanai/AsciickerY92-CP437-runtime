"""launcher_input.py — Input routing for the asciicker launcher.

Separates input routing from rain/render/legacy IO branching.
Owns ``_getch``, ``_prompt_char``, ``_prompt_choice``, ``_prompt_line``,
``_can_prompt``, and ``_pause``.

FL-2790: Input owner extraction.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from launcher_io import ConsoleManager
    from launcher_rain import RainEngine
    from scripts.launcher_ui import MenuScrollView, Renderer

from scripts.launcher_ui import MenuScrollView, _visible_len as _ansi_len, _move_to as _cursor_to


class InputManager:
    """Single owner of all launcher input handling.

    Depends on:
    - ``renderer`` — for renderer-path input (input_char, cooked_input, etc.)
    - ``io_mgr`` — for submenu buffering state and console
    - ``rain_engine`` — for animated rain input overlay
    - ``audit_flag_char`` — Ctrl+F character for audit issue reporting
    - ``audit_mode`` — whether in non-interactive audit mode
    """

    def __init__(
        self,
        renderer: Renderer,
        io_mgr: ConsoleManager,
        rain_engine: RainEngine | None = None,
        *,
        audit_flag_char: str = "\x06",
        audit_mode: bool = False,
    ) -> None:
        self._renderer = renderer
        self._io_mgr = io_mgr
        self._rain_engine = rain_engine
        self._audit_flag_char = audit_flag_char
        self._audit_mode = audit_mode
        self._menu_scroll_view = MenuScrollView(renderer, flag_char=audit_flag_char)

    def set_audit_mode(self, enabled: bool) -> None:
        self._audit_mode = enabled

    @staticmethod
    def _getch() -> str:
        if os.name == "nt":
            import msvcrt
            return msvcrt.getwch()

        import termios
        import tty

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    @staticmethod
    def can_prompt() -> bool:
        return (
            sys.stdin.isatty()
            and sys.stdout.isatty()
            and not os.environ.get("CI")
            and os.environ.get("LAUNCHER_AGENT") != "1"
        )

    def can_prompt_interactive(self) -> bool:
        """Like can_prompt() but also checks audit mode exclusion.

        NOTE: _AUDIT_MODE intentionally excluded — audit agents navigate menus
        via tmux and need can_prompt_interactive()=True to enter submenus.
        Destructive actions are blocked separately.
        """
        return self.can_prompt()

    def prompt_char(self, prompt: str = "> ", flag_issue_callback=None) -> str:
        """Read a single character from the user.

        Handles three paths:
        1. Renderer + animated rain (submenu buffering + rain active)
        2. Renderer + static (submenu buffering, no rain)
        3. Legacy (no renderer active)
        """
        flush_and_render = self._io_mgr.flush_and_render

        if self._renderer.active and self._io_mgr.submenu_buffering:
            menu_lines = flush_and_render(prompt)

            # FL-3640: MENU mode scroll viewport when content overflows.
            # This routes before the rain branch so scroll keys and menu
            # command keys coexist without interference.
            if len(menu_lines) > self._renderer.content_height:
                return self._menu_scroll_view.prompt_char(
                    menu_lines,
                    prompt,
                    flag_issue_callback=flag_issue_callback,
                    refresh_lines=lambda: flush_and_render(prompt),
                )

            if self._rain_engine is not None and self._rain_engine.rain_ui_enabled() and menu_lines:
                floor_row = len(self._renderer._banner_lines)
                if floor_row >= 3:
                    cols = self._renderer.cols
                    if self._rain_engine.submenu_rain.get("scanline_pending"):
                        self._rain_engine.submenu_rain["scanline_pending"] = False
                        self._rain_engine.run_scanline_reveal(floor_row, cols)
                    self._rain_engine.ensure_submenu_rain(floor_row, cols)
                    rule_line = menu_lines[0] if menu_lines else ""
                    while True:
                        self._rain_engine.tick_submenu_rain(floor_row, cols)
                        self._rain_engine.render_submenu_rain(floor_row, cols, rule_line)
                        ch = self._renderer.input_char(timeout=0.040)
                        if ch is None:
                            continue
                        if ch == self._audit_flag_char:
                            if flag_issue_callback:
                                flag_issue_callback()
                            menu_lines = flush_and_render(prompt)
                            self._rain_engine.ensure_submenu_rain(floor_row, cols)
                            rule_line = menu_lines[0] if menu_lines else ""
                            continue
                        if ch in {"\r", "\n"}:
                            continue
                        return ch.lower()

            while True:
                ch = self._renderer.input_char()
                if ch is None:
                    continue
                if ch == self._audit_flag_char:
                    if flag_issue_callback:
                        flag_issue_callback()
                    self._renderer.set_content(menu_lines)
                    self._renderer.set_status(prompt)
                    self._renderer.render()
                    continue
                if ch in {"\r", "\n"}:
                    continue
                return ch.lower()

        if not self.can_prompt():
            self._io_mgr.console.print(prompt, end="", markup=False)
            try:
                return input().strip()[:1].lower()
            except EOFError:
                return "q"
        self._io_mgr.console.print(prompt, end="", highlight=False)
        while True:
            ch = self._getch()
            if ch == self._audit_flag_char:
                if flag_issue_callback:
                    flag_issue_callback()
                self._io_mgr.console.print(prompt, end="", highlight=False)
                continue
            if ch in {"\r", "\n"}:
                continue
            self._io_mgr.console.print(ch)
            return ch.lower()

    def prompt_choice(self, prompt: str = "> ", default: str = "") -> str:
        shown = f" [{default}]" if default else ""
        separator = "" if prompt.endswith((" ", ": ")) else ": "
        full_prompt = f"{prompt}{shown}{separator}"

        if self._renderer.active:
            if self._io_mgr.submenu_buffering:
                lines = self._io_mgr.read_buffer_lines()
                self._renderer.set_content(lines)
            self._renderer.set_status(full_prompt)
            self._renderer.render()
            with self._renderer.cooked_input():
                status_row = self._renderer.rows - len(self._renderer._status_lines) + 1
                prompt_col = _ansi_len(full_prompt) + 1
                sys.stdout.write(_cursor_to(status_row, prompt_col))
                sys.stdout.flush()
                try:
                    raw = input().strip()
                except EOFError:
                    return default
            return raw if raw else default

        self._io_mgr.console.print(full_prompt, end="", markup=False, highlight=False)
        try:
            raw = input().strip()
        except EOFError:
            return default
        result = raw if raw else default
        if self._audit_mode and not result:
            return "__audit_test__"
        return result

    def prompt_line(self, prompt: str, default: str = "") -> str:
        if self._renderer.active:
            if self._io_mgr.submenu_buffering:
                self._io_mgr.flush_and_render()
            raw = self._renderer.input_line(prompt, default)
            return "" if raw.lower() == "q" else raw

        shown = default or "blank"
        self._io_mgr.console.print(f"{prompt} [{shown}]: ", end="", markup=False)
        try:
            raw = input().strip()
        except EOFError:
            return default
        if raw.lower() == "q":
            return ""
        if self._audit_mode and raw == "":
            return "__audit_test__"
        return default if raw == "" else raw

    def prompt_command(
        self,
        prompt: str = "> ",
        complete_fn: Callable[[str], list] | None = None,
        reserved_lines: int = 0,
    ) -> str:
        """Read a command line with auto-suggest and tab completion.
        Returns typed text (possibly empty). Never raises — returns "" on cancel.
        """
        if self._renderer.active:
            return self._renderer.input_command(
                prompt,
                complete_fn=complete_fn,
                reserved_content_lines=reserved_lines,
            )
        if self.can_prompt():
            return self._prompt_command_raw(prompt, complete_fn)
        # CI / agent / non-interactive
        try:
            return input(prompt).strip()
        except EOFError:
            return ""

    def prompt_command_seeded(
        self,
        prompt: str = "> ",
        seed: str = "",
        complete_fn: Callable[[str], list] | None = None,
        reserved_lines: int = 0,
    ) -> str:
        """Like prompt_command() but starts with *seed* already in the buffer."""
        if self._renderer.active:
            return self._renderer.input_command(
                prompt,
                complete_fn=complete_fn,
                reserved_content_lines=reserved_lines,
                seed=seed,
            )
        if self.can_prompt():
            return self._prompt_command_raw(prompt, complete_fn, seed=seed)
        try:
            extra = input(prompt).strip()
        except EOFError:
            extra = ""
        return (seed + extra).strip()

    def _prompt_command_raw(
        self,
        prompt: str,
        complete_fn: Callable[[str], list] | None = None,
        seed: str = "",
    ) -> str:
        """Legacy raw-mode fallback for command-line input."""
        import termios
        import tty
        buffer = seed
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while True:
                sys.stdout.write(f"\r\033[K{prompt}{buffer}")
                sys.stdout.flush()
                raw = os.read(fd, 1)
                if raw == b"\x03":
                    raise KeyboardInterrupt
                if raw == b"\r" or raw == b"\n":
                    sys.stdout.write("\r\n")
                    sys.stdout.flush()
                    return buffer.strip()
                if raw == b"\x7f" or raw == b"\x08":
                    buffer = buffer[:-1]
                    continue
                if raw == b"\x15":
                    buffer = ""
                    continue
                if raw == b"\x17":
                    if buffer:
                        i = len(buffer) - 1
                        while i >= 0 and buffer[i] == " ":
                            i -= 1
                        while i >= 0 and buffer[i] != " ":
                            i -= 1
                        buffer = buffer[: i + 1]
                    continue
                if raw == b"\t":
                    if complete_fn and buffer:
                        matches = complete_fn(buffer)
                        top = matches[0] if matches else None
                        if top is not None:
                            if isinstance(top, tuple):
                                top_str = str(top[0])
                            else:
                                top_str = str(top)
                            buffer = top_str
                    continue
                if raw == b"\x1b":
                    sys.stdout.write("\r\n")
                    sys.stdout.flush()
                    return ""
                try:
                    ch = raw.decode(errors="ignore")
                except UnicodeDecodeError:
                    continue
                if ord(ch) >= 32:
                    buffer += ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def pause(self, message: str) -> None:
        if self._renderer.active:
            if self._io_mgr.submenu_buffering:
                self._io_mgr.flush_and_render(message)
            self._renderer.wait_for_key(message)
            return

        if not self.can_prompt():
            return
        try:
            input(message)
        except EOFError:
            return
