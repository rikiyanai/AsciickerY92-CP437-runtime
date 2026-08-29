"""Rich-to-string adapter — captures Rich output without touching stdout.

Rich Console, Table, and markup are retained as formatting tools.  This
module wraps them so their output goes to a StringIO buffer, never to the
terminal.  The Renderer positions the resulting strings on screen.

Pattern origin: tests/scripts_tests/test_launcher_option_tree.py:24-26
    Console(file=buffer, force_terminal=True, color_system="truecolor")
"""

from __future__ import annotations

import io

try:
    from rich.console import Console
    from rich.markup import escape  # noqa: F401 — re-exported
    from rich.table import Table  # noqa: F401 — re-exported
except ImportError:
    raise SystemExit("rich is required for launcher_ui.rich_format")


class RichFormatter:
    """Formats Rich objects to strings without writing to stdout."""

    def __init__(self, width: int = 80) -> None:
        self._width = width

    @property
    def width(self) -> int:
        return self._width

    @width.setter
    def width(self, value: int) -> None:
        self._width = max(20, value)

    def _make_console(self) -> tuple[Console, io.StringIO]:
        buf = io.StringIO()
        c = Console(
            file=buf,
            force_terminal=True,
            color_system="truecolor",
            width=self._width,
        )
        return c, buf

    def format_markup(self, text: str) -> list[str]:
        """Render Rich markup to a list of terminal-ready strings."""
        c, buf = self._make_console()
        c.print(text, highlight=False)
        return buf.getvalue().rstrip("\n").split("\n")

    def format_table(self, table: "Table") -> list[str]:
        """Render a Rich Table to a list of terminal-ready strings."""
        c, buf = self._make_console()
        c.print(table)
        return buf.getvalue().rstrip("\n").split("\n")

    def format_rule(self, title: str = "", style: str = "dim") -> list[str]:
        """Render a Rich rule (horizontal divider) to strings."""
        c, buf = self._make_console()
        if title:
            c.rule(title, style=style)
        else:
            c.rule(style=style)
        return buf.getvalue().rstrip("\n").split("\n")

    def format_print(self, *args, **kwargs) -> list[str]:
        """Generic console.print() capture — same API as Rich Console.print()."""
        c, buf = self._make_console()
        c.print(*args, **kwargs)
        return buf.getvalue().rstrip("\n").split("\n")
