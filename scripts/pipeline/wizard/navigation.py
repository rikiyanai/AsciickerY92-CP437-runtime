"""
navigation.py -- Manual back navigation for questionary wizard (Pattern 3 from RESEARCH.md).

ARCHITECTURE:
  internal design notes requirement: "Every screen has 'Back' option to return to previous step"

  Architecture:
    - Screen functions return (NextScreen, state) or (BACK_SENTINEL, state)
    - Main wizard loop handles the navigation based on return values
    - History stack tracks which screens have been visited
    - State dict accumulates user answers across screens

  Usage:
      nav = WizardNav()

      while True:
          current_screen = nav.get_current_screen()
          next_screen, should_continue = screen_functions[current_screen](nav)

          if next_screen == WizardNav.BACK_SENTINEL:
              nav.go_back()  # Returns to previous screen
          elif next_screen is None:
              break  # Wizard cancelled
          else:
              nav.advance_to(next_screen)

KEY EXPORTS:
  - WizardNav: Navigation stack with history and state
  - WizardScreen: Screen enum for navigation

PIPELINE CONTEXT:
  [FLOW:WIZARD] -- Handles navigation through 6-screen wizard flow
"""
from typing import Optional, List, Dict, Any, Tuple
from enum import Enum, auto

try:
    import questionary
except ImportError:
    questionary = None  # Will fail gracefully in tests


class WizardScreen(Enum):
    """Wizard screen identifiers for navigation."""
    INTENT = auto()
    ASSET_TYPE = auto()
    TEMPLATE = auto()
    SOURCE = auto()
    AI_CONFIG = auto()
    INPUT_PATH = auto()
    CONFIGURE = auto()
    SUMMARY = auto()
    ADVANCED = auto()
    DONE = auto()


class WizardNav:
    """Manual navigation stack for questionary wizard.

    Provides:
    - History stack tracking visited screens
    - State dict accumulating user answers
    - Back button injection into choice lists
    - Ctrl+C handling (returns None)
    - Real back navigation (not just restart)

    internal design notes requirements implemented:
    - "Every screen has 'Back' option to return to previous step"
    - Breadcrumb support via get_breadcrumb_steps()
    """

    BACK_SENTINEL = "__BACK__"
    BACK_CHOICE = {"name": "<- Back", "value": "__BACK__"}

    # Screen order for breadcrumb display
    SCREEN_ORDER = [
        WizardScreen.INTENT,
        WizardScreen.ASSET_TYPE,
        WizardScreen.TEMPLATE,
        WizardScreen.SOURCE,
        WizardScreen.AI_CONFIG,
        WizardScreen.INPUT_PATH,
        WizardScreen.CONFIGURE,
        WizardScreen.SUMMARY,
        WizardScreen.ADVANCED,
    ]

    SCREEN_NAMES = {
        WizardScreen.INTENT: "Intent",
        WizardScreen.ASSET_TYPE: "Asset",
        WizardScreen.TEMPLATE: "Template",
        WizardScreen.SOURCE: "Source",
        WizardScreen.AI_CONFIG: "AI Config",
        WizardScreen.INPUT_PATH: "Input",
        WizardScreen.CONFIGURE: "Configure",
        WizardScreen.SUMMARY: "Summary",
        WizardScreen.ADVANCED: "Advanced",
        WizardScreen.DONE: "Run",
    }

    def __init__(self):
        """Initialize navigation with empty history and state."""
        self.history: List[WizardScreen] = []  # Stack of visited screens
        self.state: Dict[str, Any] = {}  # Accumulated answers
        self.current_screen: Optional[WizardScreen] = None

    def push_screen(self, screen: WizardScreen) -> None:
        """Push screen to history stack and set as current.

        Args:
            screen: Screen being entered
        """
        if self.current_screen is not None:
            self.history.append(self.current_screen)
        self.current_screen = screen

    def go_back(self) -> Optional[WizardScreen]:
        """Go back to previous screen.

        Returns:
            Previous screen, or None if at start
        """
        if self.history:
            self.current_screen = self.history.pop()
            return self.current_screen
        return None

    def can_go_back(self) -> bool:
        """Check if back navigation is possible.

        Returns:
            True if there's history to go back to
        """
        return len(self.history) > 0

    def get_current_screen(self) -> Optional[WizardScreen]:
        """Get current screen.

        Returns:
            Current screen or None if not started
        """
        return self.current_screen

    def set_state(self, key: str, value: Any) -> None:
        """Store value in wizard state.

        Args:
            key: State key
            value: Value to store
        """
        self.state[key] = value

    def get_state(self, key: str, default: Any = None) -> Any:
        """Get value from wizard state.

        Args:
            key: State key
            default: Default if key not found

        Returns:
            Stored value or default
        """
        return self.state.get(key, default)

    def clear_state(self, key: str) -> None:
        """Remove key from wizard state.

        Args:
            key: State key to remove
        """
        self.state.pop(key, None)

    def get_breadcrumb_index(self) -> int:
        """Get current screen index for breadcrumb display.

        Returns:
            Index of current screen in SCREEN_ORDER (0-based)
        """
        if self.current_screen is None:
            return -1
        try:
            return self.SCREEN_ORDER.index(self.current_screen)
        except ValueError:
            return -1

    def get_breadcrumb_steps(self) -> List[str]:
        """Get list of step names for breadcrumb display.

        Returns:
            List of screen names in order
        """
        return [self.SCREEN_NAMES.get(s, str(s)) for s in self.SCREEN_ORDER]

    def ask_with_back(self, prompt: str, choices: List[Dict],
                      screen: WizardScreen, show_back: bool = True) -> Optional[str]:
        """Ask question with automatic back button.

        Args:
            prompt: Question to ask
            choices: List of choice dicts with 'name' and 'value' keys
            screen: Current screen (for history)
            show_back: Whether to show back option (False for first screen)

        Returns:
            Selected value, BACK_SENTINEL for back, or None for Ctrl+C
        """
        if questionary is None:
            raise ImportError("questionary is required for wizard navigation")

        # Add back button if we have history and show_back is True
        all_choices = list(choices)  # Copy to avoid mutation
        if show_back and self.can_go_back():
            all_choices.insert(0, self.BACK_CHOICE)

        answer = questionary.select(prompt, choices=all_choices).ask()

        if answer is None:  # Ctrl+C
            return None

        if answer == self.BACK_SENTINEL:
            # Don't push to history on back navigation
            return self.BACK_SENTINEL

        # Push current screen to history for future back navigation
        self.push_screen(screen)
        return answer

    def ask_text_with_back(self, prompt: str, screen: WizardScreen,
                           default: str = "", show_back: bool = True) -> Optional[str]:
        """Ask text input with back option via special prefix.

        For text inputs, we can't inject a back button, so we use a
        special prefix: typing "<-" triggers back navigation.

        Args:
            prompt: Question to ask
            screen: Current screen
            default: Default value
            show_back: Whether to mention back option in prompt

        Returns:
            Entered text, BACK_SENTINEL for "<-", or None for Ctrl+C
        """
        if questionary is None:
            raise ImportError("questionary is required for wizard navigation")

        actual_prompt = prompt
        if show_back and self.can_go_back():
            actual_prompt = f"{prompt} (type '<-' for back)"

        answer = questionary.text(actual_prompt, default=default).ask()

        if answer is None:  # Ctrl+C
            return None

        if answer.strip() == '<-':
            return self.BACK_SENTINEL

        self.push_screen(screen)
        return answer

    def reset(self) -> None:
        """Reset navigation state for new wizard run."""
        self.history.clear()
        self.state.clear()
        self.current_screen = None
