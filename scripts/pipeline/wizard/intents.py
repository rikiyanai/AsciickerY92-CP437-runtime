"""
intents.py -- Intent validation for wizard flow (WIZ-02).

ARCHITECTURE NOTE: Handlers validate and populate WizardResult fields.
They do NOT execute pipelines. The flow is:

  1. Wizard collects user input across 6 screens
  2. Handler validates intent-specific requirements
  3. Handler returns WizardResult with populated fields
  4. main() extracts fields from WizardResult
  5. main() builds AssetDef from extracted fields
  6. main() invokes existing pipeline stages

TEMPLATE ENFORCEMENT (from internal design notes):
  - new_character: REQUIRES template (hard block)
  - convert_sheet: require template unless explicit Custom/raw selected
  - render_blender: require template unless explicit Custom/raw selected
  - modify_xp: auto-detect template from metadata/filename, lock to detected

Supported intents:
- new_character: Create new character sprite (REQUIRES template)
- convert_sheet: Convert existing sprite sheet to .xp
- render_blender: Render from Blender scene
- modify_xp: Modify existing .xp asset

KEY EXPORTS:
  - INTENT_HANDLERS: Strategy dispatch dictionary
  - validate_intent: Main validation entry point
  - VALID_INTENTS: Set of supported intents
  - TEMPLATE_REQUIRED_INTENTS: Intents that MUST have template
  - TEMPLATE_REQUIRED_UNLESS_CUSTOM: Intents that need template unless custom mode

PIPELINE CONTEXT:
  [FLOW:WIZARD] -- Validates intent and returns WizardResult for main()
"""
from typing import Callable, Dict, Optional, Any, Tuple
from pathlib import Path

from .result import WizardResult


# Type alias for handler functions
IntentHandler = Callable[[Dict[str, Any]], WizardResult]


# Intents that REQUIRE a template (hard block without)
TEMPLATE_REQUIRED_INTENTS = frozenset({'new_character'})

# Intents that require template UNLESS Custom/raw mode explicitly selected
TEMPLATE_REQUIRED_UNLESS_CUSTOM = frozenset({'convert_sheet', 'render_blender', 'import_mesh'})


def detect_template_from_xp(xp_path: Path) -> Tuple[Optional[str], Optional[str]]:
    """Auto-detect template from .xp file metadata or filename.

    internal design notes: "modify_xp inherits template - From original sprite, unless custom/non-character asset"
    Detection methods:
    1. Embedded metadata (if stored in .xp)
    2. Filename pattern inference

    Args:
        xp_path: Path to existing .xp file

    Returns:
        Tuple of (detected_template_name, detection_source) or (None, None)
    """
    # Method 1: Check filename patterns
    filename = xp_path.stem.lower()

    # Known character patterns
    character_patterns = {
        'idle': 'character_idle_walk',
        'walk': 'character_idle_walk',
        'run': 'character_idle_walk',
        'attack': 'character_attack',
        'fall': 'character_fall',
        'jump': 'character_fall',
        'player': 'character_idle_walk',
        'enemy': 'character_idle_walk',
        'npc': 'character_idle_walk',
    }

    for pattern, template in character_patterns.items():
        if pattern in filename:
            return template, f"filename contains '{pattern}'"

    # Method 2: Try to read embedded metadata (if xp_core supports it)
    try:
        # Import lazily to avoid circular deps
        from ..xp_core import read_xp_file
        xp_data = read_xp_file(str(xp_path))

        # Check for template metadata (future: stored in .xp header)
        if hasattr(xp_data, 'metadata') and xp_data.metadata:
            template_name = xp_data.metadata.get('template')
            if template_name:
                return template_name, "embedded metadata"
    except Exception:
        pass  # Metadata extraction failed, continue without

    return None, None


def handle_new_character(wizard_state: Dict[str, Any]) -> WizardResult:
    """Validate new character creation - REQUIRES template.

    Character sprites have strict template requirements:
    - Angles (1, 4, or 8)
    - Frame counts per animation
    - Cell size constraints

    Args:
        wizard_state: Dict with keys: template, input_path, source_type, etc.

    Returns:
        WizardResult with validated fields for main() to use
    """
    template = wizard_state.get('template')
    is_custom_mode = wizard_state.get('is_custom_mode', False)

    # Enforce template requirement for characters (WIZ-04)
    # NO EXCEPTIONS - even Custom mode cannot bypass for new_character
    if not template:
        return WizardResult(
            success=False,
            errors=[
                "Character sprites require a template.",
                "If this is a logo/sign/UI sprite, choose Item or UI (or Custom raw mode)."
            ],
            intent='new_character'
        )

    # Validate and return populated result for main() to use
    return WizardResult(
        success=True,
        intent='new_character',
        template=template,
        input_path=wizard_state.get('input_path'),
        source_type=wizard_state.get('source_type'),
        blend_file_path=wizard_state.get('blend_file_path'),
        blender_object=wizard_state.get('blender_object'),
    )


def handle_convert_sheet(wizard_state: Dict[str, Any]) -> WizardResult:
    """Validate sprite sheet conversion to .xp format.

    internal design notes: require template unless explicit Custom/raw selected
    Template ensures proper grid alignment and layer interpretation.

    Args:
        wizard_state: Dict with keys: template, input_path, is_custom_mode

    Returns:
        WizardResult with validated fields for main() to use
    """
    input_path = wizard_state.get('input_path')
    template = wizard_state.get('template')
    is_custom_mode = wizard_state.get('is_custom_mode', False)

    if not input_path:
        return WizardResult(
            success=False,
            errors=["Input path required for convert_sheet intent"],
            intent='convert_sheet'
        )

    # Enforce template unless Custom mode explicitly selected
    if not template and not is_custom_mode:
        return WizardResult(
            success=False,
            errors=[
                "Sprite sheet conversion requires a template for proper grid alignment.",
                "Select a template or choose Custom (raw mode) if this is a non-standard sprite."
            ],
            warnings=["Without a template, frame/angle detection may be incorrect"],
            intent='convert_sheet'
        )

    # Validate and return populated result
    return WizardResult(
        success=True,
        intent='convert_sheet',
        template=template,
        input_path=Path(input_path) if isinstance(input_path, str) else input_path,
        source_type='file',
    )


def handle_render_blender(wizard_state: Dict[str, Any]) -> WizardResult:
    """Validate Blender scene rendering request.

    internal design notes: require template unless explicit Custom/raw selected
    Requires both .blend file path and object name to render.

    Args:
        wizard_state: Dict with keys: blend_file_path, blender_object, template

    Returns:
        WizardResult with validated fields for main() to use
    """
    blend_file = wizard_state.get('blend_file_path')
    blender_object = wizard_state.get('blender_object')
    template = wizard_state.get('template')
    is_custom_mode = wizard_state.get('is_custom_mode', False)

    if not blend_file:
        return WizardResult(
            success=False,
            errors=["Blender .blend file path required"],
            intent='render_blender'
        )

    if not blender_object:
        return WizardResult(
            success=False,
            errors=["Blender object name required"],
            intent='render_blender'
        )

    # Enforce template unless Custom mode explicitly selected
    if not template and not is_custom_mode:
        return WizardResult(
            success=False,
            errors=[
                "Blender rendering requires a template for angle/frame configuration.",
                "Select a template or choose Custom (raw mode) for non-standard renders."
            ],
            warnings=["Without a template, render angles and frame timing must be specified manually"],
            intent='render_blender'
        )

    # Validate and return populated result
    return WizardResult(
        success=True,
        intent='render_blender',
        template=template,
        blend_file_path=Path(blend_file) if isinstance(blend_file, str) else blend_file,
        blender_object=blender_object,
        source_type='blender',
    )


def handle_modify_xp(wizard_state: Dict[str, Any]) -> WizardResult:
    """Validate modification of existing .xp asset.

    internal design notes: Auto-detect template from metadata/filename, lock to detected template if found
    Template detection uses:
    1. Embedded metadata (if stored in .xp)
    2. Filename pattern inference
    3. User confirmation always shown with all detected sources

    Args:
        wizard_state: Dict with keys: input_path (existing .xp), template (optional override)

    Returns:
        WizardResult with validated fields for main() to use
    """
    input_path = wizard_state.get('input_path')
    user_template = wizard_state.get('template')

    if not input_path:
        return WizardResult(
            success=False,
            errors=["Existing .xp file path required for modify_xp intent"],
            intent='modify_xp'
        )

    # Validate input is .xp file
    path = Path(input_path) if isinstance(input_path, str) else input_path
    if path.suffix.lower() != '.xp':
        return WizardResult(
            success=False,
            errors=[f"Expected .xp file, got: {path.suffix}"],
            warnings=["modify_xp intent is for existing .xp files only"],
            intent='modify_xp'
        )

    # Auto-detect template from file
    detected_template, detection_source = detect_template_from_xp(path)

    # Determine final template
    final_template = user_template
    warnings = []

    if detected_template:
        if user_template is None:
            # Use detected template
            final_template = detected_template
            warnings.append(f"Auto-detected template '{detected_template}' from {detection_source}")
        elif user_template != detected_template:
            # User override differs from detected - warn
            warnings.append(
                f"User template '{user_template}' differs from detected '{detected_template}' ({detection_source})"
            )

    # Validate and return populated result
    return WizardResult(
        success=True,
        intent='modify_xp',
        input_path=path,
        source_type='xp',
        template=final_template,  # May be None for non-character assets
        warnings=warnings,
    )


def handle_import_mesh(wizard_state: Dict[str, Any]) -> WizardResult:
    """Validate 3D mesh import request.

    Requires an input path to a supported mesh file (.obj, .stl, .fbx,
    .gltf, .glb, .ply). The mesh is auto-converted to .blend and the
    object name is detected from the import.

    Args:
        wizard_state: Dict with keys: input_path, template, is_custom_mode

    Returns:
        WizardResult with validated fields for main() to use
    """
    input_path = wizard_state.get('input_path')
    template = wizard_state.get('template')
    is_custom_mode = wizard_state.get('is_custom_mode', False)

    if not input_path:
        return WizardResult(
            success=False,
            errors=["Mesh file path required for import_mesh intent"],
            intent='import_mesh'
        )

    path = Path(input_path)
    from ..schemas import MESH_EXTENSIONS
    if path.suffix.lower() not in MESH_EXTENSIONS:
        return WizardResult(
            success=False,
            errors=[f"Unsupported mesh format: {path.suffix}. Supported: {', '.join(sorted(MESH_EXTENSIONS))}"],
            intent='import_mesh'
        )

    # Enforce template unless Custom mode explicitly selected
    if not template and not is_custom_mode:
        return WizardResult(
            success=False,
            errors=[
                "Mesh import requires a template for angle/frame configuration.",
                "Select a template or choose Custom (raw mode) for non-standard imports."
            ],
            intent='import_mesh'
        )

    return WizardResult(
        success=True,
        intent='import_mesh',
        input_path=path,
        source_type='mesh',
        template=template,
    )


# Dispatch dictionary mapping intents to handlers
INTENT_HANDLERS: Dict[str, IntentHandler] = {
    'new_character': handle_new_character,
    'convert_sheet': handle_convert_sheet,
    'render_blender': handle_render_blender,
    'import_mesh': handle_import_mesh,
    'modify_xp': handle_modify_xp,
}

# Valid intents for validation
VALID_INTENTS = frozenset(INTENT_HANDLERS.keys())


def validate_intent(intent: str, wizard_state: Dict[str, Any]) -> WizardResult:
    """Validate intent and populate WizardResult fields.

    NOTE: This function validates and returns data. It does NOT execute
    pipelines. The caller (main()) uses the returned WizardResult fields
    to build AssetDef and invoke the existing pipeline.

    Args:
        intent: One of 'new_character', 'convert_sheet', 'render_blender', 'modify_xp'
        wizard_state: Dict containing all wizard-gathered state

    Returns:
        WizardResult with validated fields or error messages
    """
    handler = INTENT_HANDLERS.get(intent)

    if not handler:
        return WizardResult(
            success=False,
            errors=[f"Unknown intent: {intent}"],
            warnings=[f"Valid intents: {', '.join(VALID_INTENTS)}"],
        )

    return handler(wizard_state)


def get_intent_display_name(intent: str) -> str:
    """Get human-readable display name for intent.

    Args:
        intent: Internal intent key

    Returns:
        Display name for UI
    """
    names = {
        'new_character': 'New character asset',
        'convert_sheet': 'Convert sprite sheet to XP format',
        'render_blender': 'Render from Blender scene',
        'import_mesh': 'Import 3D model (OBJ/STL/FBX/GLTF)',
        'modify_xp': 'Modify existing XP asset',
    }
    return names.get(intent, intent)


def is_template_required(intent: str, is_custom_mode: bool = False) -> bool:
    """Check if template is required for this intent.

    Args:
        intent: The intent being validated
        is_custom_mode: Whether user explicitly selected Custom/raw mode

    Returns:
        True if template is required
    """
    if intent in TEMPLATE_REQUIRED_INTENTS:
        return True  # No exceptions for these

    if intent in TEMPLATE_REQUIRED_UNLESS_CUSTOM:
        return not is_custom_mode

    return False  # modify_xp uses detection, not requirement
