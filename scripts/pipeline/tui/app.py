"""Main Textual application for the Asciicker Asset Generator TUI."""

from textual.app import App, ComposeResult
from textual.binding import Binding

from .state import TUIState


class AssetTUI(App):
    """Asciicker Asset Generator — Textual TUI Application.

    Manages the screen stack (Welcome → Configure → Analyze → Run → Result)
    and holds shared TUIState that all screens read/write.
    """

    TITLE = "Asciicker Asset Generator"
    CSS = """
    Screen {
        align: center middle;
    }

    #title-bar {
        dock: top;
        height: 3;
        background: $primary-background;
        color: $text;
        text-align: center;
        padding: 1;
        text-style: bold;
    }

    .breadcrumb {
        dock: top;
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 2;
    }

    .form-group {
        height: auto;
        margin: 1 0;
    }

    .form-label {
        height: 1;
        margin: 0 0 0 1;
        color: $text-muted;
    }

    .btn-primary {
        margin: 1 2;
    }

    .btn-row {
        height: auto;
        layout: horizontal;
        align: center middle;
        margin: 1 0;
    }

    .panel {
        border: solid $primary;
        padding: 1;
        margin: 0 1;
        height: auto;
    }

    .panel-title {
        text-style: bold;
        color: $primary;
        margin: 0 0 1 0;
    }

    RichLog {
        border: solid $surface;
        height: 1fr;
        margin: 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("escape", "go_back", "Back", show=True),
    ]

    def __init__(self):
        super().__init__()
        self.tui_state = TUIState()

    def on_mount(self) -> None:
        from .screens.welcome import WelcomeScreen
        self.push_screen(WelcomeScreen())

    def action_go_back(self) -> None:
        """Pop current screen unless we're on the root."""
        if len(self.screen_stack) > 1:
            self.pop_screen()
