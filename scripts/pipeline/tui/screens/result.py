"""Result screen — output summary and next actions."""

import subprocess
import sys
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Header, Static

from ..widgets.breadcrumb import Breadcrumb


class ResultScreen(Screen):
    """Shows pipeline output and offers next actions."""

    CSS = """
    ResultScreen {
        layout: vertical;
    }

    #result-title {
        height: 3;
        margin: 1;
        text-align: center;
        text-style: bold;
        color: $success;
    }

    #result-table {
        height: 1fr;
        margin: 1;
    }

    .btn-row {
        layout: horizontal;
        align: center middle;
        height: auto;
        margin: 1 0;
        padding: 0 2;
    }

    .btn-row Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Breadcrumb(current=3)
        yield Header()
        yield Static("Asset Generated Successfully!", id="result-title")
        yield DataTable(id="result-table")
        with Horizontal(classes="btn-row"):
            yield Button("Open in xp_tool", variant="primary", id="preview-btn")
            yield Button("Convert Another", variant="warning", id="another-btn")
            yield Button("Quit", id="quit-btn")

    def on_mount(self) -> None:
        self._populate_table()

    def _populate_table(self) -> None:
        output = self.app.tui_state.last_output
        table = self.query_one("#result-table", DataTable)
        table.add_columns("Property", "Value")

        if output is None:
            table.add_row("Status", "No output available")
            return

        table.add_row("XP Path", str(getattr(output, "xp_path", "?")))
        table.add_row("Checksum (SHA-256)", str(getattr(output, "checksum_sha256", "?"))[:16] + "...")

        metadata = getattr(output, "metadata", {})
        table.add_row("Angles", str(metadata.get("angles", "?")))
        table.add_row("Projections", str(metadata.get("projs", "?")))
        table.add_row("Animations", str(metadata.get("anims", "?")))

        resolved = getattr(output, "resolved_slice_spec", None)
        if resolved:
            table.add_row("Cell Size", f"{resolved.cell_w_px} x {resolved.cell_h_px}")
            table.add_row("Grid", f"{resolved.cols} cols x {resolved.rows} rows")

        table.add_row("Job ID", str(getattr(output, "job_id", "?")))
        table.add_row("Created", str(getattr(output, "created_at", "?")))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "preview-btn":
            self._open_xp_tool()
        elif event.button.id == "another-btn":
            self._reset_and_restart()
        elif event.button.id == "quit-btn":
            self.app.exit()

    def _open_xp_tool(self) -> None:
        """Launch xp_tool as a subprocess."""
        output = self.app.tui_state.last_output
        if output is None:
            self.notify("No output to preview", severity="warning")
            return

        xp_path = str(getattr(output, "xp_path", ""))
        if not xp_path:
            self.notify("No XP path available", severity="warning")
            return

        try:
            subprocess.Popen(
                [sys.executable, "-m", "scripts.pipeline.xp_tool", xp_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.notify(f"Opened xp_tool: {xp_path}")
        except Exception as e:
            self.notify(f"Failed to launch xp_tool: {e}", severity="error")

    def _reset_and_restart(self) -> None:
        """Reset state and go back to welcome."""
        from ..state import TUIState
        self.app.tui_state = TUIState()

        # Pop all screens and push fresh welcome
        while len(self.app.screen_stack) > 1:
            self.app.pop_screen()

        from .welcome import WelcomeScreen
        self.app.push_screen(WelcomeScreen())
