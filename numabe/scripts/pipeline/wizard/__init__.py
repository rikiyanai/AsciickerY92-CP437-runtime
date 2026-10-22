"""
__init__.py -- Wizard module for interactive asset generation flow.

This module provides the structured return types and error handling
for the 6-screen interactive wizard in cli.py.

Key exports:
- WizardResult: Dataclass for wizard return contract (WIZ-03)
- WizardErrorHandler: Unified error logging (internal design notes error format)
- WizardNav: Navigation stack for back-button support
- WizardScreen: Screen enum for navigation
- validate_intent: Validate intent and populate WizardResult (WIZ-02)
- VALID_INTENTS: Set of supported intents
- check_pipeline_availability: Dependency checking for intents
- PipelineAvailability: Availability status container
"""
from .result import WizardResult
from .errors import WizardErrorHandler
from .navigation import WizardNav, WizardScreen
from .intents import (
    validate_intent,
    VALID_INTENTS,
    INTENT_HANDLERS,
    TEMPLATE_REQUIRED_INTENTS,
    TEMPLATE_REQUIRED_UNLESS_CUSTOM,
    is_template_required,
    get_intent_display_name,
    detect_template_from_xp,
)
from .availability import (
    check_pipeline_availability,
    PipelineAvailability,
    DependencyStatus,
    format_intent_choice,
    get_unavailable_error,
)
from .validation import (
    validate_template_loadable,
    validate_input_against_template,
    check_template_required_for_intent,
    format_validation_errors_with_fixes,
)

__all__ = [
    # Core types
    'WizardResult',
    'WizardErrorHandler',
    'WizardNav',
    'WizardScreen',
    # Intent validation
    'validate_intent',
    'VALID_INTENTS',
    'INTENT_HANDLERS',
    'TEMPLATE_REQUIRED_INTENTS',
    'TEMPLATE_REQUIRED_UNLESS_CUSTOM',
    'is_template_required',
    'get_intent_display_name',
    'detect_template_from_xp',
    # Availability checking
    'check_pipeline_availability',
    'PipelineAvailability',
    'DependencyStatus',
    'format_intent_choice',
    'get_unavailable_error',
    # Template validation
    'validate_template_loadable',
    'validate_input_against_template',
    'check_template_required_for_intent',
    'format_validation_errors_with_fixes',
]
