"""TUI entry point for the Asciicker Asset Generator."""


def run_tui():
    """Launch the Textual-based TUI application."""
    from .app import AssetTUI
    app = AssetTUI()
    app.run()
