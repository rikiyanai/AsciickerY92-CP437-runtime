"""MenuScrollView — mode-aware scrollable viewport for launcher submenus.

MENU mode rules (FL-3640):
- Up/Down/PgUp/PgDn/Home/End adjust the viewport offset and re-render.
- Ctrl+F runs the launcher issue reporter callback, then refreshes buffered content.
- Enter/Return are ignored (not consumed as menu commands).
- Any other key is returned lowercased to the caller as a menu command.
- `q` is never consumed by the scroller; it returns to the caller as a menu command.

This is NOT a modal pager — scroll keys and command keys coexist.  The viewport
renders a slice of buffered submenu lines through the Renderer, and only the
scroll keys are handled internally.  All other input passes through to the menu
handler with no valid_keys restriction.

OUTPUT mode (existing ScrollView) remains separate: Enter/q exits the output
viewer.  INPUT/SELECTOR/TUI mode widgets are never wrapped by MenuScrollView.

Pattern origin: scripts/launcher_ui/scroll_view.py::ScrollView
FL references: FL-3640
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from scripts.launcher_ui import Renderer


class MenuScrollView:
    """Scrollable viewport for buffered launcher submenu content.

    Stores the current visible slice offset and re-renders through the
    Renderer's content zone on scroll actions.  Not a pager — q and all
    command keys are forwarded to the calling menu handler.

    Args:
        renderer: The launcher Renderer instance.
        flag_char: The Ctrl+F equivalent character for issue reporting.
            Must match InputManager._audit_flag_char.
    """

    def __init__(self, renderer: Renderer, flag_char: str = "\x06") -> None:
        self._renderer = renderer
        self._flag_char = flag_char
        self._lines: list[str] = []
        self._offset: int = 0
        # Monotonic version counter: incremented on every prompt_char() call.
        # Used to detect content changes and reset scroll offset.
        self._version: int = 0

    def reset(self) -> None:
        """Clear stored lines and reset scroll offset."""
        self._lines.clear()
        self._offset = 0
        self._version = 0

    def prompt_char(
        self,
        lines: list[str],
        prompt: str = "> ",
        *,
        flag_issue_callback: Callable[[], None] | None = None,
        refresh_lines: Callable[[], list[str]] | None = None,
    ) -> str:
        """Enter MENU mode scroll loop and return the first command key pressed.

        Args:
            lines: Buffered submenu content lines.
            prompt: Status prompt to display.
            flag_issue_callback: Called when the flag character (default Ctrl+F)
                is pressed.
            refresh_lines: Callback to re-read buffered lines after the flag
                character is pressed.

        Returns:
            Lowercased menu command key (single character).
        """
        # Every prompt_char() call is a fresh menu prompt: accept new lines
        # and reset the scroll offset to the top.
        self._version += 1
        self._lines = list(lines)
        self._offset = 0

        renderer = self._renderer

        while True:
            # Re-clamp offset on every iteration to handle SIGWINCH resizes.
            self._clamp_offset()

            view_h = renderer.content_height
            visible = self._lines[self._offset : self._offset + view_h]
            renderer.set_content(visible)

            # Status line with scroll position indicator.
            # FL-3666: compact format at ≤60 columns to keep prompt visible.
            total = len(self._lines)
            if total <= view_h:
                status = prompt
            else:
                top = self._offset + 1
                bot = min(self._offset + view_h, total)
                if renderer.cols <= 60:
                    status = f"[{top}-{bot}/{total}] \u2191\u2193 scroll  {prompt}"
                else:
                    status = (
                        f"  [lines {top}-{bot} of {total}]  "
                        f"\u2191\u2193 PgUp/PgDn Home/End scroll  "
                        f"{prompt}"
                    )
            renderer.set_status(status)
            renderer.render()

            # Read one key with open-ended valid_keys (no restriction)
            ch = renderer.input_char(valid_keys=None)

            if ch is None:
                continue

            # Enter/Return: ignore in MENU mode
            if ch in {"\r", "\n"}:
                continue

            # Flag character (Ctrl+F by default): issue callback then refresh
            if ch == self._flag_char:
                if flag_issue_callback:
                    flag_issue_callback()
                if refresh_lines is not None:
                    self._lines = list(refresh_lines())
                continue

            # Scroll keys: adjust offset and continue loop
            if ch in {"up", "down", "pgup", "pgdn", "home", "end"}:
                self._handle_scroll_key(ch)
                continue

            # q is a menu command in MENU mode, not a pager exit key.
            if ch in {"q", "Q"}:
                return "q"
            if ch == "\x1b":
                continue  # Escape never exits submenu

            # Any other key: return to menu handler as lowercased command
            return ch.lower()

    def _handle_scroll_key(self, key: str) -> None:
        """Adjust the scroll offset in response to a navigation key."""
        if not self._lines:
            return

        view_h = self._renderer.content_height
        max_offset = max(0, len(self._lines) - view_h)

        if key == "up":
            self._offset = max(0, self._offset - 1)
        elif key == "down":
            self._offset = min(max_offset, self._offset + 1)
        elif key == "pgup":
            self._offset = max(0, self._offset - view_h)
        elif key == "pgdn":
            self._offset = min(max_offset, self._offset + view_h)
        elif key == "home":
            self._offset = 0
        elif key == "end":
            self._offset = max_offset

    def _clamp_offset(self) -> None:
        """Clamp current offset so viewport end does not exceed line count."""
        if not self._lines:
            self._offset = 0
            return
        view_h = self._renderer.content_height
        max_offset = max(0, len(self._lines) - view_h)
        self._offset = max(0, min(self._offset, max_offset))
