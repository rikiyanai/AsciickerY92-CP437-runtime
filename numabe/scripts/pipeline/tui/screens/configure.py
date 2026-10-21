"""Configure screen — source, geometry, slicing, and background settings."""

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Header, Input, Label, Select, Static

from ..widgets.breadcrumb import Breadcrumb
from ..widgets.slicing_panel import SlicingPanel
from ..widgets.bg_panel import BackgroundPanel


class ConfigScreen(Screen):
    """Second screen: configure all pipeline parameters."""

    CSS = """
    ConfigScreen {
        layout: vertical;
    }

    #config-scroll {
        height: 1fr;
    }

    #config-columns {
        layout: horizontal;
        height: auto;
        min-height: 30;
    }

    .config-col {
        width: 1fr;
        height: auto;
        padding: 0 1;
    }

    .section-title {
        text-style: bold;
        color: $primary;
        margin: 1 0 0 1;
    }

    .form-row {
        layout: horizontal;
        height: auto;
        margin: 0 1;
    }

    .form-row Label {
        width: 18;
        height: 1;
        content-align: right middle;
        margin: 0 1 0 0;
    }

    .form-row Input {
        width: 1fr;
    }

    .form-row Select {
        width: 1fr;
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

    #blender-panel {
        height: auto;
        display: none;
    }

    #blender-panel.visible {
        display: block;
    }
    """

    def compose(self) -> ComposeResult:
        yield Breadcrumb(current=1)
        yield Header()
        with ScrollableContainer(id="config-scroll"):
            with Horizontal(id="config-columns"):
                # Left column: Source + Geometry
                with Vertical(classes="config-col"):
                    yield Static("Source", classes="section-title")
                    with Horizontal(classes="form-row"):
                        yield Label("Source Type:")
                        yield Select(
                            [
                                ("File (PNG)", "file"),
                                ("Blender", "blender"),
                                ("AI Generate", "ai"),
                            ],
                            value="file",
                            id="source_type",
                        )
                    with Horizontal(classes="form-row"):
                        yield Label("File Path:")
                        yield Input(placeholder="/path/to/sprite.png", id="source_path")
                    with Horizontal(classes="form-row"):
                        yield Label("Asset Name:")
                        yield Input(value="unnamed", id="name")

                    # Blender-specific fields (shown when source_type=blender)
                    with Vertical(id="blender-panel"):
                        yield Static("Blender Settings", classes="section-title")
                        with Horizontal(classes="form-row"):
                            yield Label("Object Name:")
                            yield Input(
                                placeholder="Cube",
                                value="",
                                id="blender_object",
                            )
                        with Horizontal(classes="form-row"):
                            yield Label("Render (px/cell):")
                            yield Select(
                                [
                                    ("12px (1x) fast", "12"),
                                    ("24px (2x) recommended", "24"),
                                    ("48px (4x) high detail", "48"),
                                    ("96px (8x) very high", "96"),
                                ],
                                value="24",
                                id="render_resolution",
                            )

                    yield Static("Geometry", classes="section-title")
                    with Horizontal(classes="form-row"):
                        yield Label("Angles:")
                        yield Select(
                            [
                                ("1", "1"),
                                ("2 (left/right)", "2"),
                                ("4", "4"),
                                ("6 (hex grid)", "6"),
                                ("8", "8"),
                                ("12 (30-deg)", "12"),
                            ],
                            value="1",
                            id="angles",
                        )
                    with Horizontal(classes="form-row"):
                        yield Label("Frames:")
                        yield Input(value="1", placeholder="e.g. 1,8", id="frames")
                    with Horizontal(classes="form-row"):
                        yield Label("Options:")
                        yield Checkbox("Transparency", id="transparency")
                    with Horizontal(classes="form-row"):
                        yield Label("")
                        yield Checkbox("Normalization", id="normalization")

                # Right column: Slicing + Background
                with Vertical(classes="config-col"):
                    yield SlicingPanel(id="slicing-panel")
                    yield BackgroundPanel(id="bg-panel")

        with Horizontal(classes="btn-row"):
            yield Button("Back", id="back-btn")
            yield Button("Analyze", variant="warning", id="analyze-btn")
            yield Button("Run", variant="primary", id="run-btn")

    def on_mount(self) -> None:
        self._load_state()
        self._update_blender_visibility()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "source_type":
            self._update_blender_visibility()

    def _update_blender_visibility(self) -> None:
        """Show/hide Blender-specific fields based on source type."""
        source_select = self.query_one("#source_type", Select)
        blender_panel = self.query_one("#blender-panel")
        is_blender = str(source_select.value) == "blender"
        if is_blender:
            blender_panel.add_class("visible")
        else:
            blender_panel.remove_class("visible")

    def _load_state(self) -> None:
        """Populate form from TUIState."""
        state = self.app.tui_state

        if state.source_path:
            self.query_one("#source_path", Input).value = state.source_path
        if state.name:
            self.query_one("#name", Input).value = state.name
        self.query_one("#frames", Input).value = state.frames

        if state.blender_object:
            self.query_one("#blender_object", Input).value = state.blender_object
        self.query_one("#render_resolution", Select).value = str(state.render_resolution)

        self.query_one("#slicing-panel", SlicingPanel).load_from_state(state)
        self.query_one("#bg-panel", BackgroundPanel).load_from_state(state)

    def _save_state(self) -> None:
        """Write form values back into TUIState."""
        state = self.app.tui_state

        source_select = self.query_one("#source_type", Select)
        state.source_type = str(source_select.value) if source_select.value else "file"

        state.source_path = self.query_one("#source_path", Input).value.strip()
        state.name = self.query_one("#name", Input).value.strip() or "unnamed"

        # Blender fields
        state.blender_object = self.query_one("#blender_object", Input).value.strip()
        render_res_select = self.query_one("#render_resolution", Select)
        try:
            state.render_resolution = int(render_res_select.value) if render_res_select.value else 24
        except (ValueError, TypeError):
            state.render_resolution = 24

        angles_select = self.query_one("#angles", Select)
        try:
            state.angles = int(angles_select.value) if angles_select.value else 1
        except (ValueError, TypeError):
            state.angles = 1

        state.frames = self.query_one("#frames", Input).value.strip() or "1"
        state.transparency = self.query_one("#transparency", Checkbox).value
        state.normalization = self.query_one("#normalization", Checkbox).value

        self.query_one("#slicing-panel", SlicingPanel).apply_to_state(state)
        self.query_one("#bg-panel", BackgroundPanel).apply_to_state(state)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back-btn":
            self.app.pop_screen()
        elif event.button.id == "analyze-btn":
            self._save_state()
            from .analyze import AnalyzeScreen
            self.app.push_screen(AnalyzeScreen())
        elif event.button.id == "run-btn":
            self._save_state()
            if not self._validate():
                return
            from .run import RunScreen
            self.app.push_screen(RunScreen())

    def _validate(self) -> bool:
        """Basic validation before running."""
        state = self.app.tui_state
        if state.source_type == "file" and not state.source_path:
            self.notify("Please provide a source file path", severity="error")
            return False
        if state.source_type == "blender":
            if not state.source_path:
                self.notify("Please provide a .blend file path", severity="error")
                return False
            if not state.blender_object:
                self.notify("Please provide a Blender object name", severity="error")
                return False
        if not state.name or state.name == "unnamed":
            self.notify("Please provide an asset name", severity="warning")
        return True
