"""BackgroundSpec editor panel widget."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, Label, Select, Static


class BackgroundPanel(Vertical):
    """Panel for editing BackgroundSpec fields."""

    DEFAULT_CSS = """
    BackgroundPanel {
        border: solid $primary;
        padding: 1;
        margin: 0 1;
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("[bold]Background[/bold]", classes="panel-title")
        yield Label("Mode:", classes="form-label")
        yield Select(
            [
                ("Key Color", "key_color"),
                ("Alpha", "alpha"),
                ("None", "none"),
            ],
            value="key_color",
            id="bg_mode",
        )
        yield Label("Key Color (hex):", classes="form-label")
        yield Input(value="FF00FF", id="bg_color")
        yield Label("Tolerance:", classes="form-label")
        yield Input(value="8", id="bg_tolerance")

    def apply_to_state(self, state) -> None:
        """Read widget values and write them into TUIState."""
        mode_select = self.query_one("#bg_mode", Select)
        state.bg_mode = str(mode_select.value) if mode_select.value else "key_color"

        color_input = self.query_one("#bg_color", Input)
        state.bg_color = self._parse_hex_color(color_input.value.strip())

        tolerance_input = self.query_one("#bg_tolerance", Input)
        try:
            state.bg_tolerance = int(tolerance_input.value.strip())
        except (ValueError, AttributeError):
            state.bg_tolerance = 8

    def load_from_state(self, state) -> None:
        """Populate widgets from TUIState."""
        color_hex = "{:02X}{:02X}{:02X}".format(*state.bg_color)
        self.query_one("#bg_color", Input).value = color_hex

        tolerance_input = self.query_one("#bg_tolerance", Input)
        tolerance_input.value = str(state.bg_tolerance)

    @staticmethod
    def _parse_hex_color(hex_str: str) -> tuple:
        """Parse hex color string to RGB tuple."""
        hex_str = hex_str.lstrip("#")
        if len(hex_str) == 6:
            try:
                r = int(hex_str[0:2], 16)
                g = int(hex_str[2:4], 16)
                b = int(hex_str[4:6], 16)
                return (r, g, b)
            except ValueError:
                pass
        return (255, 0, 255)
