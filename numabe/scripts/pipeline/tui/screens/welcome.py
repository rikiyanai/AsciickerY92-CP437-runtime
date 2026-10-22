"""Welcome screen — intent selection."""

from textual.app import ComposeResult
from textual.containers import Center, Vertical
from textual.screen import Screen
from textual.widgets import Button, Header, RadioButton, RadioSet, Static

from ..widgets.breadcrumb import Breadcrumb


INTENTS = [
    ("convert_sheet", "Convert Sprite Sheet", "Convert an existing PNG sprite sheet to .xp format"),
    ("new_character", "New Character", "Create a new character sprite (requires template)"),
    ("render_blender", "Render from Blender", "Render a Blender object into a sprite sheet"),
    ("import_mesh", "Import Mesh", "Import a 3D mesh file (.akm) as a sprite"),
    ("modify_xp", "Modify XP", "Modify an existing .xp asset"),
]


class WelcomeScreen(Screen):
    """First screen: select what you want to do."""

    CSS = """
    WelcomeScreen {
        align: center middle;
    }

    #welcome-box {
        width: 70;
        height: auto;
        border: solid $primary;
        padding: 2;
    }

    #welcome-title {
        text-align: center;
        text-style: bold;
        color: $primary;
        margin: 0 0 1 0;
    }

    #welcome-subtitle {
        text-align: center;
        color: $text-muted;
        margin: 0 0 2 0;
    }

    RadioSet {
        margin: 1 2;
    }

    #next-btn {
        margin: 2 2 0 2;
        width: 100%;
    }
    """

    def compose(self) -> ComposeResult:
        yield Breadcrumb(current=0)
        yield Header()
        with Center():
            with Vertical(id="welcome-box"):
                yield Static("Asciicker Asset Generator", id="welcome-title")
                yield Static("What would you like to do?", id="welcome-subtitle")
                with RadioSet(id="intent-set"):
                    for intent_id, label, description in INTENTS:
                        yield RadioButton(f"{label} — {description}", name=intent_id)
                yield Button("Next", variant="primary", id="next-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "next-btn":
            self._advance()

    def _advance(self) -> None:
        radio_set = self.query_one("#intent-set", RadioSet)
        if radio_set.pressed_index < 0:
            self.notify("Please select an option", severity="warning")
            return

        intent_id = INTENTS[radio_set.pressed_index][0]
        self.app.tui_state.intent = intent_id

        from .configure import ConfigScreen
        self.app.push_screen(ConfigScreen())
