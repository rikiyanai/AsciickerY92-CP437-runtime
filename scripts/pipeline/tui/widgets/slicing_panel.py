"""SlicingSpec editor panel widget."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, Label, Select, Static


class SlicingPanel(Vertical):
    """Panel for editing SlicingSpec fields."""

    DEFAULT_CSS = """
    SlicingPanel {
        border: solid $primary;
        padding: 1;
        margin: 0 1;
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("[bold]Slicing[/bold]", classes="panel-title")
        yield Label("Cell Width (px):", classes="form-label")
        yield Input(placeholder="auto", id="cell_w")
        yield Label("Cell Height (px):", classes="form-label")
        yield Input(placeholder="auto", id="cell_h")
        yield Label("Columns:", classes="form-label")
        yield Input(placeholder="auto", id="cols")
        yield Label("Rows:", classes="form-label")
        yield Input(placeholder="auto", id="rows")
        yield Label("Margin X (px):", classes="form-label")
        yield Input(value="0", id="margin_x")
        yield Label("Margin Y (px):", classes="form-label")
        yield Input(value="0", id="margin_y")
        yield Label("Spacing X (px):", classes="form-label")
        yield Input(value="0", id="spacing_x")
        yield Label("Spacing Y (px):", classes="form-label")
        yield Input(value="0", id="spacing_y")
        yield Label("Origin:", classes="form-label")
        yield Select(
            [("Top Left", "top_left"), ("Bottom Left", "bottom_left")],
            value="top_left",
            id="origin",
        )
        yield Label("Order:", classes="form-label")
        yield Select(
            [
                ("Row-per-angle (default)", "angle_major"),
                ("Row-per-animation", "animation_major"),
                ("Column-major (legacy)", "frame_major"),
            ],
            value="angle_major",
            id="order",
        )

    def apply_to_state(self, state) -> None:
        """Read widget values and write them into TUIState."""
        state.cell_w = self._parse_optional_int("cell_w")
        state.cell_h = self._parse_optional_int("cell_h")
        state.cols = self._parse_optional_int("cols")
        state.rows = self._parse_optional_int("rows")
        state.margin_x = self._parse_int("margin_x", 0)
        state.margin_y = self._parse_int("margin_y", 0)
        state.spacing_x = self._parse_int("spacing_x", 0)
        state.spacing_y = self._parse_int("spacing_y", 0)

        origin_select = self.query_one("#origin", Select)
        state.origin = str(origin_select.value) if origin_select.value else "top_left"

        order_select = self.query_one("#order", Select)
        state.order = str(order_select.value) if order_select.value else "angle_major"

    def load_from_state(self, state) -> None:
        """Populate widgets from TUIState."""
        self._set_input("cell_w", state.cell_w)
        self._set_input("cell_h", state.cell_h)
        self._set_input("cols", state.cols)
        self._set_input("rows", state.rows)
        self._set_input("margin_x", state.margin_x)
        self._set_input("margin_y", state.margin_y)
        self._set_input("spacing_x", state.spacing_x)
        self._set_input("spacing_y", state.spacing_y)

    def _parse_optional_int(self, widget_id: str):
        """Parse an Input value to int or None."""
        inp = self.query_one(f"#{widget_id}", Input)
        val = inp.value.strip()
        if not val or val.lower() == "auto":
            return None
        try:
            return int(val)
        except ValueError:
            return None

    def _parse_int(self, widget_id: str, default: int) -> int:
        inp = self.query_one(f"#{widget_id}", Input)
        try:
            return int(inp.value.strip())
        except (ValueError, AttributeError):
            return default

    def _set_input(self, widget_id: str, value) -> None:
        inp = self.query_one(f"#{widget_id}", Input)
        inp.value = str(value) if value is not None else ""
