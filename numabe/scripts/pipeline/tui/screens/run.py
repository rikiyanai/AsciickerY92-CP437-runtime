"""Run screen — pipeline execution with live log output."""

import io
import sys
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Header, ProgressBar, RichLog, Static
from textual.worker import Worker, WorkerState

from ..widgets.breadcrumb import Breadcrumb


class _LineCallbackIO(io.TextIOBase):
    """StringIO replacement that calls a callback on each line written.

    Used to intercept pipeline print() calls and stream them to the
    RichLog widget line-by-line in real time.
    """

    def __init__(self, callback):
        self._callback = callback
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line:
                self._callback(line)
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            self._callback(self._buffer)
            self._buffer = ""

    def writable(self) -> bool:
        return True


class RunScreen(Screen):
    """Executes the pipeline with live log output."""

    CSS = """
    RunScreen {
        layout: vertical;
    }

    #run-status {
        height: 3;
        margin: 1;
        text-align: center;
    }

    #run-progress {
        margin: 0 2;
        height: 1;
    }

    #run-log {
        height: 1fr;
        margin: 1;
        border: solid $surface;
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
        yield Breadcrumb(current=2)
        yield Header()
        yield Static("[bold]Running pipeline...[/bold]", id="run-status")
        yield ProgressBar(id="run-progress", show_eta=False)
        yield RichLog(id="run-log", highlight=True, markup=True)
        with Horizontal(classes="btn-row"):
            yield Button("Back", id="back-btn", disabled=True)

    def on_mount(self) -> None:
        progress = self.query_one("#run-progress", ProgressBar)
        progress.update(total=None)  # indeterminate
        self._start_pipeline()

    def _start_pipeline(self) -> None:
        self.run_worker(self._pipeline_worker, thread=True, name="pipeline")

    def _log_line(self, line: str) -> None:
        """Post a line to the RichLog from the main thread."""
        log = self.query_one("#run-log", RichLog)
        log.write(line)

    def _pipeline_worker(self) -> object:
        """Run the pipeline, capturing stdout for the log widget."""
        from scripts.pipeline.service.asset_service import AssetService

        state = self.app.tui_state
        job = state.to_job_config()

        svc = AssetService()

        # Intercept stdout to stream pipeline output to RichLog
        capture = _LineCallbackIO(
            lambda line: self.app.call_from_thread(self._log_line, line)
        )
        old_stdout = sys.stdout
        sys.stdout = capture
        try:
            output = svc.run(job)
        finally:
            capture.flush()
            sys.stdout = old_stdout

        return output

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name != "pipeline":
            return

        if event.state == WorkerState.SUCCESS:
            output = event.worker.result
            self.app.tui_state.last_output = output
            self.query_one("#run-status", Static).update(
                "[bold green]Pipeline complete![/bold green]"
            )
            self.query_one("#back-btn", Button).disabled = False

            # Auto-advance to result screen
            from .result import ResultScreen
            self.app.push_screen(ResultScreen())

        elif event.state == WorkerState.ERROR:
            error = event.worker.error
            self.query_one("#run-status", Static).update(
                f"[bold red]Pipeline failed[/bold red]"
            )
            log = self.query_one("#run-log", RichLog)
            log.write(f"[red]Error: {error}[/red]")
            self.query_one("#back-btn", Button).disabled = False

        elif event.state == WorkerState.CANCELLED:
            self.query_one("#run-status", Static).update(
                "[yellow]Pipeline cancelled[/yellow]"
            )
            self.query_one("#back-btn", Button).disabled = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back-btn":
            self.app.pop_screen()
