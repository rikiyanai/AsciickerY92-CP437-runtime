"""Wizard error handling with unified format and file logging.

Error format (from internal design notes):
  [WIZARD] {category}: {message} (see {log_path})

Detailed logs are written to:
  scripts/pipeline/staging/debug/wizard_errors.log

internal design notes requirements:
  - Unified error format for ALL wizard errors
  - Detailed logs in staging/debug
  - Actionable next steps for recoverable errors
  - Breadcrumb progress display
"""
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

# Initialize colorama for cross-platform colored output
try:
    from colorama import init, Fore, Style
    init(autoreset=True)
    COLORS = {
        'success': Fore.GREEN,
        'warning': Fore.YELLOW,
        'error': Fore.RED,
        'info': Fore.CYAN,
        'reset': Style.RESET_ALL,
    }
except ImportError:
    # Fallback if colorama not installed
    COLORS = {
        'success': '',
        'warning': '',
        'error': '',
        'info': '',
        'reset': '',
    }


def _get_repo_root() -> Path:
    """Find repository root by looking for .git or known markers.

    Returns:
        Path to repository root
    """
    # Start from this file's location
    current = Path(__file__).resolve().parent

    # Walk up looking for .git or the current agent doc location.
    for _ in range(10):  # Max 10 levels up
        if (current / '.git').exists() or (current / 'docs' / 'agent' / 'claude.md').exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent

    # Fallback: use scripts/pipeline/wizard parent^3
    return Path(__file__).resolve().parent.parent.parent.parent


class WizardErrorHandler:
    """Unified error handling for wizard flow.

    internal design notes requirements:
    - Consistent error message format: [WIZARD] {category}: {message}
    - Detailed logging to wizard_errors.log
    - Colored console output (via colorama)
    - Recovery suggestions for recoverable errors
    - Breadcrumb progress display

    Usage:
        handler = WizardErrorHandler()
        msg = handler.format_error("TEMPLATE", "Not found: player.json")
        # Prints: [WIZARD] TEMPLATE: Not found: player.json (see .../wizard_errors.log)

    IMPORTANT: Use format_error() for ALL wizard errors to ensure consistent format.
    Do NOT use plain print("Error: ...") - this bypasses logging and format.
    """

    # Log path is repo-root anchored (not cwd-relative)
    DEFAULT_LOG_DIR = "scripts/pipeline/staging/debug"
    DEFAULT_LOG_FILE = "wizard_errors.log"

    def __init__(self, log_dir: Optional[Path] = None):
        """Initialize error handler with log file setup.

        Args:
            log_dir: Override log directory (default: repo_root/staging/debug/)
        """
        # Anchor to repo root, not cwd
        repo_root = _get_repo_root()

        if log_dir:
            self.log_dir = log_dir
        else:
            self.log_dir = repo_root / self.DEFAULT_LOG_DIR

        self.log_path = self.log_dir / self.DEFAULT_LOG_FILE

        # Ensure log directory exists
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Configure file logger
        self.logger = logging.getLogger("wizard")
        self.logger.setLevel(logging.DEBUG)

        # Avoid duplicate handlers on repeated instantiation
        if not any(isinstance(h, logging.FileHandler) and
                   h.baseFilename == str(self.log_path.resolve())
                   for h in self.logger.handlers):
            handler = logging.FileHandler(self.log_path, mode='a')
            handler.setFormatter(logging.Formatter(
                '%(asctime)s [%(levelname)s] %(message)s'
            ))
            self.logger.addHandler(handler)

    def format_error(self, category: str, message: str,
                     details: Optional[Dict[str, Any]] = None) -> str:
        """Format error with unified structure.

        internal design notes: "Unified error format - [WIZARD] {category}: {message} (see {log_path})"

        Args:
            category: Error category (e.g., TEMPLATE, BLENDER, INPUT, UNAVAILABLE)
            message: User-facing error message
            details: Optional dict of detailed info for log file

        Returns:
            Formatted error string with log path reference
        """
        # Log detailed info to file
        if details:
            self.logger.error(f"{category}: {message} | Details: {details}")
        else:
            self.logger.error(f"{category}: {message}")

        # Return concise user message
        return f"[WIZARD] {category}: {message} (see {self.log_path})"

    def recoverable_error(self, category: str, message: str,
                          next_step: str) -> str:
        """Format recoverable error with next steps.

        internal design notes: "Include 'Next: ...' only for recoverable errors, not fatal ones"

        Args:
            category: Error category
            message: Error message
            next_step: Suggested recovery action

        Returns:
            Formatted error with "Next:" line
        """
        self.logger.warning(f"{category}: {message} | Recovery: {next_step}")
        return f"[WIZARD] {category}: {message}\nNext: {next_step}"

    def print_error(self, category: str, message: str,
                    details: Optional[Dict[str, Any]] = None) -> None:
        """Print formatted error to console with logging.

        Convenience method that calls format_error and prints with color.

        Args:
            category: Error category
            message: Error message
            details: Optional details for log
        """
        formatted = self.format_error(category, message, details)
        self.print_message('error', formatted)

    def print_message(self, level: str, message: str) -> None:
        """Print colored wizard message to console.

        Args:
            level: One of 'success', 'warning', 'error', 'info'
            message: Message to print
        """
        color = COLORS.get(level, '')
        reset = COLORS.get('reset', '')
        print(f"{color}{message}{reset}")

    def print_breadcrumb(self, steps: List[str], current_index: int) -> None:
        """Print wizard progress breadcrumb.

        internal design notes: "Breadcrumb progress - Show trail: Intent > Asset > [Template] > Input > Summary > Run"

        Args:
            steps: List of step names
            current_index: Index of current step (0-based)

        Example output:
            Intent > Asset > [Template] > Input > Summary > Run
        """
        parts = []
        for i, step in enumerate(steps):
            if i == current_index:
                # Current step in brackets
                parts.append(f"[{step}]")
            elif i < current_index:
                # Completed steps in green
                parts.append(f"{COLORS['success']}{step}{COLORS['reset']}")
            else:
                # Future steps plain
                parts.append(step)
        print(" > ".join(parts))

    def log_to_file(self, level: str, message: str,
                    details: Optional[Dict[str, Any]] = None) -> None:
        """Log message to file only (no console output).

        Args:
            level: Log level ('debug', 'info', 'warning', 'error')
            message: Message to log
            details: Optional details dict
        """
        log_method = getattr(self.logger, level, self.logger.info)
        if details:
            log_method(f"{message} | Details: {details}")
        else:
            log_method(message)
