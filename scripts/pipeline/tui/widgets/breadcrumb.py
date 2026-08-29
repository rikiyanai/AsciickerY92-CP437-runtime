"""Breadcrumb bar showing wizard progress."""

from textual.widgets import Static


STEPS = ["Welcome", "Configure", "Run", "Results"]


class Breadcrumb(Static):
    """Shows wizard progress: Welcome > Configure > [Run] > [Results].

    The current step is highlighted with brackets.
    """

    DEFAULT_CSS = """
    Breadcrumb {
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 2;
    }
    """

    def __init__(self, current: int = 0, **kwargs):
        self._current = current
        super().__init__(**kwargs)

    def on_mount(self) -> None:
        self._render_breadcrumb()

    def set_step(self, step: int) -> None:
        self._current = step
        self._render_breadcrumb()

    def _render_breadcrumb(self) -> None:
        parts = []
        for i, name in enumerate(STEPS):
            if i == self._current:
                parts.append(f"[bold cyan][{name}][/bold cyan]")
            elif i < self._current:
                parts.append(f"[green]{name}[/green]")
            else:
                parts.append(f"[dim]{name}[/dim]")
        self.update(" > ".join(parts))
