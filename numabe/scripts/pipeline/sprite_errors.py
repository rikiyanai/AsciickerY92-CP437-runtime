"""
sprite_errors.py -- Custom exceptions for sprite validation.

ARCHITECTURE:
    Centralized exception classes for sprite pipeline validation failures.
    These provide structured error data for programmatic handling plus
    formatted messages matching the C++ error format.

[DATA-CONTRACT:XP] All sprite validation errors use consistent format:
    "[SPRITE] path: reason (expected X, got Y)"
"""


class SpriteValidationError(Exception):
    """Raised when sprite data fails validation against XP_LAYER_SPEC.

    Attributes:
        path: Path to the .xp file that failed validation
        expected: Expected value or condition
        got: Actual value that triggered failure
        message: Human-readable error description

    The formatted error message matches C++ sprite loader format:
        "[SPRITE] path: message (expected X, got Y)"
    """
    def __init__(self, path: str, expected, got, message: str):
        self.path = path
        self.expected = expected
        self.got = got
        self.message = message
        # Match C++ error format: "[SPRITE] path: reason (expected X, got Y)"
        super().__init__(f"[SPRITE] {path}: {message} (expected {expected}, got {got})")
