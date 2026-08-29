"""Analyze screen — image analysis with auto-suggestion."""

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Header, Label, RichLog, Static
from textual.worker import Worker, WorkerState

from ..widgets.breadcrumb import Breadcrumb


class AnalyzeScreen(Screen):
    """Runs AssetService.analyze() and shows suggested parameters."""

    CSS = """
    AnalyzeScreen {
        layout: vertical;
    }

    #analyze-status {
        height: 3;
        margin: 1;
        text-align: center;
        color: $text-muted;
    }

    #analyze-table {
        height: 1fr;
        margin: 1;
    }

    #analyze-warnings {
        height: auto;
        max-height: 6;
        margin: 1;
        color: $warning;
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
        yield Breadcrumb(current=1)
        yield Header()
        yield Static("Analyzing image...", id="analyze-status")
        yield DataTable(id="analyze-table")
        yield Static("", id="analyze-warnings")
        with Horizontal(classes="btn-row"):
            yield Button("Back", id="back-btn")
            yield Button("Apply Suggestions", variant="primary", id="apply-btn", disabled=True)

    def on_mount(self) -> None:
        table = self.query_one("#analyze-table", DataTable)
        table.add_columns("Property", "Value")
        self._run_analysis()

    def _run_analysis(self) -> None:
        self.run_worker(self._analyze_worker, thread=True, name="analyze")

    def _analyze_worker(self) -> dict:
        """Run analysis in a background thread."""
        from scripts.pipeline.service.asset_service import AssetService

        state = self.app.tui_state
        svc = AssetService()

        hints = {}
        if state.angles > 0:
            hints["angles"] = state.angles

        return svc.analyze(state.source_path, hints)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name != "analyze":
            return

        if event.state == WorkerState.SUCCESS:
            result = event.worker.result
            self.app.tui_state.analysis = result
            self._show_results(result)
            self.query_one("#analyze-status", Static).update(
                "[green]Analysis complete[/green]"
            )
            self.query_one("#apply-btn", Button).disabled = False

        elif event.state == WorkerState.ERROR:
            error = event.worker.error
            self.query_one("#analyze-status", Static).update(
                f"[red]Analysis failed: {error}[/red]"
            )

    def _show_results(self, result: dict) -> None:
        table = self.query_one("#analyze-table", DataTable)
        table.clear()

        w, h = result.get("dimensions", (0, 0))
        table.add_row("Dimensions", f"{w} x {h} px")
        table.add_row("Suggested Angles", str(result.get("suggested_angles", "?")))
        table.add_row("Suggested Cols", str(result.get("suggested_cols", "?")))
        table.add_row("Suggested Rows", str(result.get("suggested_rows", "?")))
        table.add_row("Suggested Cell W", f"{result.get('suggested_cell_w', '?')} px")
        table.add_row("Suggested Cell H", f"{result.get('suggested_cell_h', '?')} px")
        table.add_row("Suggested Frames", str(result.get("suggested_frames", "?")))
        table.add_row("Suggested Projs", str(result.get("suggested_projs", "?")))
        table.add_row("Detected Background", result.get("detected_background", "?"))

        # Display layout suggestions when available
        layout_suggestions = result.get("layout_suggestions", [])
        if layout_suggestions:
            table.add_row("---", "--- Layout Suggestions ---")
            for suggestion in layout_suggestions:
                confidence = suggestion.get("confidence", "low")
                marker = {"high": "[+]", "medium": "[~]", "low": "[?]"}.get(
                    confidence, "[?]"
                )
                label = suggestion.get("label", "")
                order = suggestion.get("order", "")
                rationale = suggestion.get("rationale", "")
                table.add_row(
                    f"{marker} {label}",
                    f"--order {order}  ({rationale})",
                )

        warnings = result.get("warnings", [])
        if warnings:
            warning_text = "\n".join(f"  * {w}" for w in warnings)
            self.query_one("#analyze-warnings", Static).update(
                f"[yellow]Warnings:[/yellow]\n{warning_text}"
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back-btn":
            self.app.pop_screen()
        elif event.button.id == "apply-btn":
            self._apply_suggestions()
            self.notify("Suggestions applied to configuration")
            self.app.pop_screen()

    def _apply_suggestions(self) -> None:
        """Copy analysis results back to TUIState."""
        result = self.app.tui_state.analysis
        if not result:
            return

        state = self.app.tui_state
        state.angles = result.get("suggested_angles", state.angles)
        state.cols = result.get("suggested_cols", state.cols)
        state.rows = result.get("suggested_rows", state.rows)
        state.cell_w = result.get("suggested_cell_w", state.cell_w)
        state.cell_h = result.get("suggested_cell_h", state.cell_h)

        suggested_frames = result.get("suggested_frames")
        if suggested_frames:
            state.frames = ",".join(str(f) for f in suggested_frames)

        bg = result.get("detected_background", "")
        if bg in ("key_color", "alpha", "none"):
            state.bg_mode = bg
