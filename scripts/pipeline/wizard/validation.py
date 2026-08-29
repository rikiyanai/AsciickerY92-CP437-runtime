"""Template constraint validation for wizard flow (WIZ-04).

internal design notes requirements:
  - Pre-validate: template is loadable and well-formed
  - Post-validate: input matches template constraints (dimensions, frame counts)
  - Constraint failure handling: errors with auto-fix suggestions

Two-pass validation strategy:
  1. Pre-validate: template is loadable (before wizard proceeds)
  2. Post-validate: input matches template constraints (before pipeline execution)

Gap closure: This module's functions are called from:
  - run_wizard_mode(): validate_template_loadable() on template selection
  - main(): validate_input_against_template() before pipeline execution
"""
from typing import List, Optional, Tuple, TYPE_CHECKING, Dict, Any
from pathlib import Path

if TYPE_CHECKING:
    from ..templates.models import Template

from .result import WizardResult


def validate_template_loadable(template_path: Path) -> Tuple[bool, List[str]]:
    """Pre-validate: check template file is loadable.

    Called from run_wizard_mode() when user selects a template.
    Ensures template is valid before proceeding to input collection.

    Args:
        template_path: Path to template JSON file

    Returns:
        Tuple of (is_valid, list of error messages)
    """
    errors = []

    if not template_path.exists():
        errors.append(f"Template file not found: {template_path}")
        return False, errors

    if template_path.suffix.lower() != '.json':
        errors.append(f"Template must be JSON file, got: {template_path.suffix}")
        return False, errors

    # Try to load and parse
    try:
        from ..templates.loader import TemplateLoader, TemplateLoadError
        TemplateLoader.from_file(template_path)
    except TemplateLoadError as e:
        errors.append(f"Template validation failed: {e}")
        return False, errors
    except Exception as e:
        errors.append(f"Unexpected error loading template: {e}")
        return False, errors

    return True, []


def validate_input_against_template(
    input_path: Path,
    template: 'Template',
) -> Tuple[bool, List[str], List[str], List[Dict[str, Any]]]:
    """Post-validate: check input matches template constraints.

    Called from main() BEFORE pipeline execution.
    internal design notes: "Post-validate: input matches template constraints"

    Validates:
    - Input file exists and is readable
    - Dimensions match template expectations (angles * frames * cell_size)
    - Frame count is compatible with template

    Args:
        input_path: Path to input PNG file
        template: Loaded Template object

    Returns:
        Tuple of (is_valid, error_messages, warning_messages, auto_fix_suggestions)
        auto_fix_suggestions is list of dicts with 'option', 'description', 'action' keys
    """
    errors = []
    warnings = []
    auto_fixes = []

    # Check file exists
    if not input_path.exists():
        errors.append(f"Input file not found: {input_path}")
        return False, errors, warnings, auto_fixes

    # Check file is image (PNG)
    if input_path.suffix.lower() not in ['.png', '.jpg', '.jpeg']:
        errors.append(f"Input must be image file (PNG/JPG), got: {input_path.suffix}")
        return False, errors, warnings, auto_fixes

    # Try to load image and check dimensions
    try:
        from PIL import Image
        with Image.open(input_path) as img:
            width, height = img.size

        # Calculate expected dimensions from template
        expected_width, expected_height = _calculate_expected_dimensions(template)

        if expected_width and width != expected_width:
            # Offer potential fixes (internal design notes: auto-fix suggestions)
            if width > expected_width and width % expected_width == 0:
                ratio = width // expected_width
                auto_fixes.append({
                    'option': 'split',
                    'description': f"Split into {ratio} separate files",
                    'action': f"Input width {width} is {ratio}x expected {expected_width}. May contain multiple animations."
                })
                warnings.append(
                    f"Input width {width} is multiple of expected {expected_width}. "
                    f"Consider splitting into separate files."
                )
            elif width < expected_width:
                # Calculate how many frames are missing
                frames_found = width // _get_cell_width(template) if _get_cell_width(template) else 0
                frames_expected = expected_width // _get_cell_width(template) if _get_cell_width(template) else 0

                auto_fixes.append({
                    'option': 'pad',
                    'description': f"Pad with empty frames (add {frames_expected - frames_found} frames)",
                    'action': f"Add transparent frames to match template"
                })
                auto_fixes.append({
                    'option': 'truncate_template',
                    'description': f"Use only {frames_found} frames from template",
                    'action': f"Reduce template frame count to match input"
                })

                errors.append(
                    f"Input has {frames_found} frames, template requires {frames_expected}. "
                    f"Options: 1) Pad with empty 2) Truncate template 3) Ask Claude 4) Cancel"
                )
            else:
                errors.append(
                    f"Input width {width} doesn't match template requirement {expected_width}. "
                    f"Check angles ({getattr(template, 'angles', '?')}) and frames ({getattr(template, 'frames', '?')})."
                )

        if expected_height and height != expected_height:
            if height > expected_height:
                auto_fixes.append({
                    'option': 'downscale',
                    'description': f"Downscale from {height}px to {expected_height}px",
                    'action': f"Automatic downscaling will be applied"
                })
            warnings.append(
                f"Input height {height} differs from expected {expected_height}. "
                f"Downscaling will be applied."
            )

    except ImportError:
        warnings.append("PIL not installed, skipping dimension validation")
    except Exception as e:
        warnings.append(f"Could not validate image dimensions: {e}")

    return len(errors) == 0, errors, warnings, auto_fixes


def _calculate_expected_dimensions(template: 'Template') -> Tuple[Optional[int], Optional[int]]:
    """Calculate expected input dimensions from template.

    Delegates to template.expected_dimensions() for consistency with
    pipeline.py's grid validation. This ensures wizard validation uses
    the same dimension calculation as the main pipeline.

    Args:
        template: Template object with expected_dimensions() method

    Returns:
        Tuple of (expected_width, expected_height) - None if cannot be determined
    """
    try:
        if hasattr(template, 'expected_dimensions'):
            return template.expected_dimensions()
        # Fallback for templates without the method
        return None, None
    except Exception:
        return None, None


def _get_cell_width(template: 'Template') -> Optional[int]:
    """Get cell width from template.

    Args:
        template: Template object

    Returns:
        Cell width or None
    """
    cell_size = getattr(template, 'cell_size', None)
    if cell_size is None:
        return None
    return cell_size[0] if isinstance(cell_size, (list, tuple)) else cell_size


def check_template_required_for_intent(intent: str, template: Optional['Template'],
                                       is_custom_mode: bool = False) -> WizardResult:
    """Check if template is required for this intent.

    internal design notes requirements:
    - new_character: REQUIRES template (no exceptions)
    - convert_sheet: require template unless explicit Custom/raw selected
    - render_blender: require template unless explicit Custom/raw selected

    Args:
        intent: The selected intent
        template: The template (or None if not selected)
        is_custom_mode: Whether user explicitly chose Custom/raw mode

    Returns:
        WizardResult - success if OK, failure with message if template required but missing
    """
    from .intents import TEMPLATE_REQUIRED_INTENTS, TEMPLATE_REQUIRED_UNLESS_CUSTOM

    if intent in TEMPLATE_REQUIRED_INTENTS and template is None:
        # Hard block - no exceptions
        return WizardResult(
            success=False,
            errors=[
                "Character sprites require a template.",
                "If this is a logo/sign/UI sprite, choose Item or UI (or Custom raw mode)."
            ],
            intent=intent,
        )

    if intent in TEMPLATE_REQUIRED_UNLESS_CUSTOM and template is None and not is_custom_mode:
        # Block unless Custom mode
        return WizardResult(
            success=False,
            errors=[
                f"{intent} requires a template for proper configuration.",
                "Select a template or choose Custom (raw mode) if this is a non-standard asset."
            ],
            intent=intent,
        )

    return WizardResult(
        success=True,
        intent=intent,
        template=template,
    )


def format_validation_errors_with_fixes(
    errors: List[str],
    auto_fixes: List[Dict[str, Any]],
    include_claude_option: bool = True
) -> str:
    """Format validation errors with auto-fix options for display.

    internal design notes: "Constraint failure handling - Offer auto-fix suggestions AND 'Ask Claude' button"

    Args:
        errors: List of error messages
        auto_fixes: List of auto-fix suggestion dicts
        include_claude_option: Whether to include "Ask Claude" option

    Returns:
        Formatted string for display
    """
    lines = []

    for err in errors:
        lines.append(f"  - {err}")

    if auto_fixes or include_claude_option:
        lines.append("")
        lines.append("Options:")
        for i, fix in enumerate(auto_fixes, 1):
            lines.append(f"  {i}) {fix['description']}")
        if include_claude_option:
            lines.append(f"  {len(auto_fixes) + 1}) Ask Claude")
        lines.append(f"  {len(auto_fixes) + 2}) Cancel")

    return "\n".join(lines)
