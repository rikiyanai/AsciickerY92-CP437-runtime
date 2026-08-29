"""
cli.py -- CLI entry point and user interface for the Asciicker sprite generation pipeline.

ARCHITECTURE
============
This module is the **front door** to the asset generation system.  It parses
command-line arguments, runs an optional interactive wizard, loads templates,
and delegates actual image processing to ``pipeline.py``.  No pixel work
happens here -- cli.py is pure orchestration and user interaction.

[FLOW:CLI] -- Every user invocation starts here and exits here.

Operation Modes (conceptual "subcommands")
------------------------------------------
The CLI exposes nine user-visible operations through flags and mode detection:

  1. ``--wizard``            Interactive 6-screen wizard (run_wizard_mode)
  2. ``--template NAME``     Template-driven generation (load_template_if_needed)
  3. Raw mode                All core args supplied (--name, --angles, --input)
  4. ``--list-templates``    Discovery: print available templates grouped by type
  5. ``--describe-template`` Discovery: show one template's full details
  6. ``--process-rgb-to-cp437``  Early-exit: direct RGB-to-glyph conversion
  7. ``--dry-run``           Validate configuration only, skip rendering
  8. ``--force``             Override locked template fields
  9. ``--render XP_PATH``    Export .xp to PNG (export_service path)
 10. (removed: ``--strict`` was dead code, see HARD-14-06)

Argument Resolution Chain
-------------------------
When building the final ``AssetDef`` configuration, values are resolved in
this priority order (highest wins):

::

    1. Explicit CLI flags         (e.g. --angles 8, --downscale box)
    2. Template values            (from JSON via templates/loader.py)
    3. Wizard prompts             (interactive questionary answers)
    4. Preset defaults            (from presets.py: ORC_TEMPLATE, etc.)
    5. Global fallbacks           (hardcoded in resolve_field_config)

For **locked** template fields (angles, frames, layout, cell_size, frame_size),
CLI values are rejected unless ``--force`` is passed -- see resolve_field_config().

Delegation to Pipeline
----------------------
After building a validated ``AssetDef``, main() imports ``pipeline.AssetPipeline``
and calls ``pipeline.run(template=..., algorithm=...)``.  The pipeline then
executes the 4-stage process: GENERATE -> SLICE -> PROCESS -> ASSEMBLE.

Exit Codes
----------
::

    0 = Success / dry-run pass / wizard cancel
    1 = Usage/config errors (missing args, mode conflicts)
    2 = Validation errors (template not found, dimension mismatch)
    3 = Runtime/IO errors (file not found, Blender failure)
    4 = Internal errors (unexpected exceptions)

KEY EXPORTS
-----------
  - parse_args(): Build argparse parser with all flags
  - detect_cli_mode(): Classify invocation as wizard/template/raw
  - resolve_field_config(): Merge CLI + template + defaults into config dict
  - select_algorithm(): Pick downscaling algorithm with priority chain
  - main(): Top-level entry point

PIPELINE CONTEXT
----------------
  [FLOW:CLI]        -- This module is the pipeline entry point.
  [FLOW:TEMPLATE]   -- Templates are loaded here and forwarded to pipeline.py.
  [DEPENDENCY:QUESTIONARY] -- Interactive wizard requires the questionary package.
  [DEPENDENCY:SCHEMAS]     -- AssetDef is the data contract passed downstream.
  [DEPENDENCY:PIPELINE]    -- Lazy-imported in main() to avoid circular imports.
"""

import argparse
import sys
import json
import logging
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Optional
from .schemas import AssetDef
from .presets import get_preset, ORC_TEMPLATE

# WHY: processor_core may be installed as a top-level package (when running
# inside the asset_gen package) or as a submodule of scripts (when invoked
# from the project root).  The try/except handles both import contexts.
try:
    from processor_core import ImageProcessor
except ImportError:
    from scripts.processor_core import ImageProcessor

# [FLOW:TEMPLATE] -- Template loading is done here (not in pipeline.py)
# because the CLI needs template metadata for wizard display, --describe-template,
# and field locking logic before the pipeline is ever instantiated.
from scripts.pipeline.templates.loader import TemplateLoader, TemplateLoadError
from scripts.pipeline.templates.models import Template as TemplateModel

# Wizard module imports deferred to _ensure_wizard() to avoid loading
# questionary (via wizard/navigation.py) in non-interactive mode.
# [FLOW:CLI] Non-interactive pipelines never touch wizard symbols.
_wizard_symbols = None

# Imported from the shared constants module.  The canonical definitions
# live in service/constants.py; these aliases preserve the existing names
# used throughout this module.
from scripts.pipeline.service.constants import (
    DOWNSCALE_ALGORITHMS as _SERVICE_ALGORITHMS,
    DEFAULT_DOWNSCALE_BY_SOURCE as DEFAULT_BY_SOURCE,
)
from scripts.pipeline.config_schema import PipelineConfig

DOWNSCALE_ALGORITHMS = list(_SERVICE_ALGORITHMS)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy import helpers (non-interactive guard)
# ---------------------------------------------------------------------------

class ConfigError(ValueError):
    """CLI-level configuration/flag validation error.

    Distinct from bare ValueError (which comes from pipeline/assembler logic)
    so classify_error() can map them to different exit codes:
      ConfigError -> exit 1 (user/config)
      ValueError  -> exit 2 (pipeline/processing)
    """


def _ensure_questionary():
    """Lazily import and return the questionary module.

    Called only from interactive code paths (wizard, preview, post-validation).
    If questionary is not installed, exits with a helpful message.
    """
    try:
        import questionary
        return questionary
    except ImportError:
        exit_with_code(
            1,
            "Error: 'questionary' package required for interactive mode.\n"
            "Install with: pip install questionary\n"
            "Or use --non-interactive to skip prompts.",
        )


def _ensure_wizard():
    """Lazily import wizard symbols and return them as a dict.

    Deferred to avoid loading questionary (via wizard/navigation.py) in
    non-interactive mode.  Returns a dict of symbol-name -> value.
    """
    global _wizard_symbols
    if _wizard_symbols is not None:
        return _wizard_symbols

    from scripts.pipeline.wizard import (
        WizardResult,
        WizardNav,
        WizardScreen,
        WizardErrorHandler,
        validate_intent,
        check_template_required_for_intent,
        validate_template_loadable,
        validate_input_against_template,
        format_validation_errors_with_fixes,
        check_pipeline_availability,
        format_intent_choice,
        get_unavailable_error,
        get_intent_display_name,
        is_template_required,
    )

    _wizard_symbols = {
        "WizardResult": WizardResult,
        "WizardNav": WizardNav,
        "WizardScreen": WizardScreen,
        "WizardErrorHandler": WizardErrorHandler,
        "validate_intent": validate_intent,
        "check_template_required_for_intent": check_template_required_for_intent,
        "validate_template_loadable": validate_template_loadable,
        "validate_input_against_template": validate_input_against_template,
        "format_validation_errors_with_fixes": format_validation_errors_with_fixes,
        "check_pipeline_availability": check_pipeline_availability,
        "format_intent_choice": format_intent_choice,
        "get_unavailable_error": get_unavailable_error,
        "get_intent_display_name": get_intent_display_name,
        "is_template_required": is_template_required,
    }
    return _wizard_symbols


def require_flag(value, flag_name: str, description: str, example: str = None):
    """Require a CLI flag value to be present.

    Raises ConfigError with an actionable fix message when value is None.
    Used by --import-mode path to enforce explicit metadata (no silent
    defaults, no inference).

    Args:
        value: The parsed flag value (None means missing).
        flag_name: The flag name for error messages (e.g. "--angles").
        description: Human-readable description of what the flag provides.
        example: Optional example usage string.

    Raises:
        ConfigError: When value is None, with a rustc-style diagnostic
            showing the missing flag, what it provides, and how to fix.
    """
    if value is None:
        msg = (
            f"Missing required flag: {flag_name}\n"
            f"  {description}\n"
        )
        if example is not None:
            msg += f"  Example: {example}\n"
        msg += f"  Add {flag_name} to your command."
        raise ConfigError(msg)


def resolve_interactivity(args) -> bool:
    """Determine whether the CLI session is interactive.

    Resolution order:
      1. Mutual exclusion: --non-interactive AND --interactive -> ConfigError
      2. Conflict: --non-interactive AND (--tui OR --tui-textual) -> ConfigError
      3. --non-interactive flag -> False
      4. --interactive flag -> True
      5. Auto-detect: stdin.isatty() and stdout.isatty()

    Args:
        args: Parsed argparse.Namespace.

    Returns:
        True if the session should use interactive prompts, False otherwise.

    Raises:
        ConfigError: On conflicting flag combinations.
    """
    non_interactive = getattr(args, "non_interactive", False)
    interactive = getattr(args, "interactive", False)

    if non_interactive and interactive:
        raise ConfigError(
            "Conflicting flags: --non-interactive and --interactive cannot both be set."
        )

    if non_interactive and (getattr(args, "tui", False) or getattr(args, "tui_textual", False)):
        raise ConfigError(
            "Conflicting flags: --non-interactive cannot be combined with --tui or --tui-textual."
        )

    if non_interactive:
        return False
    if interactive:
        return True

    # Auto-detect from terminal attachment
    return sys.stdin.isatty() and sys.stdout.isatty()


def classify_error(exc: Exception) -> int:
    """Map an exception to a semantic exit code.

    Exit code scheme (matches exit_with_code docstring):
      1 = ConfigError (user/config: bad flags, missing required input)
      2 = ValueError, GridError (pipeline/processing error)
      3 = FileNotFoundError, PermissionError, IOError (file/IO error)
      4 = Everything else (internal/unexpected)
    """
    # Import GridError lazily to avoid circular import at module scope
    try:
        from scripts.pipeline.grid_validator import GridError
    except ImportError:
        GridError = None

    if isinstance(exc, ConfigError):
        return 1
    if isinstance(exc, ValueError):
        # ConfigError is a ValueError subclass, but checked first above
        return 2
    if GridError is not None and isinstance(exc, GridError):
        return 2
    if isinstance(exc, (FileNotFoundError, PermissionError, IOError)):
        return 3
    return 4


def parse_keyframe_ranges(raw: str):
    """Parse --keyframe-ranges CLI value into a list of AnimationRange objects.

    Accepts two formats:
      1. JSON array: '[{"start":0,"end":0,"count":1,"name":"idle"},...]'
      2. Compact CSV: '0-0:1:idle,1-24:8:walk' (start-end:count[:name])

    Args:
        raw: Raw string from --keyframe-ranges flag.

    Returns:
        List of AnimationRange instances, or None if raw is None/empty.

    Raises:
        ConfigError: On malformed input.
    """
    if not raw:
        return None

    from .schemas import AnimationRange

    # Try JSON first
    raw_stripped = raw.strip()
    if raw_stripped.startswith("["):
        try:
            data = json.loads(raw_stripped)
        except json.JSONDecodeError as e:
            raise ConfigError(f"Invalid --keyframe-ranges JSON: {e}")

        ranges = []
        for i, entry in enumerate(data):
            try:
                ranges.append(AnimationRange(
                    count=int(entry["count"]),
                    keyframe_start=int(entry["start"]),
                    keyframe_end=int(entry["end"]),
                    name=str(entry.get("name", "")),
                ))
            except (KeyError, TypeError, ValueError) as e:
                raise ConfigError(
                    f"--keyframe-ranges entry [{i}] invalid: {e}\n"
                    f"  Required keys: start, end, count (int). Optional: name (str)."
                )
        return ranges

    # Compact CSV format: start-end:count[:name],...
    ranges = []
    for i, token in enumerate(raw_stripped.split(",")):
        token = token.strip()
        if not token:
            continue
        parts = token.split(":")
        if len(parts) < 2:
            raise ConfigError(
                f"--keyframe-ranges token [{i}] '{token}' invalid.\n"
                f"  Expected format: start-end:count[:name]\n"
                f"  Example: 0-0:1:idle,1-24:8:walk"
            )
        span = parts[0]
        count_str = parts[1]
        name = parts[2] if len(parts) > 2 else ""

        span_parts = span.split("-")
        if len(span_parts) != 2:
            raise ConfigError(
                f"--keyframe-ranges token [{i}] span '{span}' invalid.\n"
                f"  Expected format: start-end (e.g. 0-0 or 1-24)"
            )
        try:
            ks = int(span_parts[0])
            ke = int(span_parts[1])
            ct = int(count_str)
        except ValueError:
            raise ConfigError(
                f"--keyframe-ranges token [{i}] '{token}': "
                f"start, end, and count must be integers."
            )
        ranges.append(AnimationRange(
            count=ct,
            keyframe_start=ks,
            keyframe_end=ke,
            name=name,
        ))

    if not ranges:
        raise ConfigError("--keyframe-ranges resulted in empty list.")

    return ranges


def publish_sprite(xp_path: str, force: bool = False) -> dict:
    """
    Publish a staging .xp sprite to the production assets/sprites/ directory.

    [FLOW:CLI] Staging-to-production workflow:
    1. Validate the staging sprite (Python validation + optional C++)
    2. Check naming convention (alphanumeric + hyphen + underscore)
    3. Copy to assets/sprites/ directory
    4. Move source to staging/archive/ for history preservation

    Args:
        xp_path: Path to staging .xp file to publish
        force: If True, overwrite existing sprite in assets/sprites/

    Returns:
        dict with keys:
            - success: bool - Whether publish succeeded
            - errors: list of error messages
            - warnings: list of warnings
            - published_path: str - Path to published sprite (if success)
            - archived_path: str - Path to archived source (if success)

    Exit codes (when called from CLI):
        0 = Success
        2 = Validation error
        3 = File not found or I/O error
    """
    import shutil
    import re
    from .validator import validate_xp
    from .staging import STAGING_DIR, ensure_staging_structure

    result = {
        "success": False,
        "errors": [],
        "warnings": [],
        "published_path": None,
        "archived_path": None,
    }

    source_path = Path(xp_path)

    # ========================================================================
    # Step 1: Check source file exists
    # ========================================================================
    if not source_path.exists():
        result["errors"].append(f"Source file not found: {xp_path}")
        return result

    if source_path.suffix.lower() != ".xp":
        result["errors"].append(f"Not an .xp file: {xp_path}")
        return result

    # ========================================================================
    # Step 2: Validate the sprite with Python
    # ========================================================================
    print(f"[PUBLISH] Validating: {xp_path}")
    validation_result = validate_xp(source_path)

    if not validation_result["valid"]:
        result["errors"].append("Validation failed:")
        result["errors"].extend(validation_result["errors"])
        return result

    print(f"[PUBLISH] Validation: PASSED")
    print(f"  Metadata: angles={validation_result['metadata'].get('angles')}, anims={validation_result['metadata'].get('anims')}")

    # ========================================================================
    # Step 3: Check naming convention
    # ========================================================================
    sprite_name = source_path.stem
    # Allow alphanumeric, hyphen, underscore
    valid_name_pattern = r"^[a-zA-Z0-9_-]+$"

    if not re.match(valid_name_pattern, sprite_name):
        result["errors"].append(
            f"Invalid sprite name '{sprite_name}': Use only alphanumeric, hyphen, underscore"
        )
        return result

    # ========================================================================
    # Step 4: Prepare destination paths
    # ========================================================================
    sprites_dir = Path("assets/sprites")
    if not sprites_dir.exists():
        result["errors"].append(f"assets/sprites/ directory not found: {sprites_dir}")
        return result

    dest_path = sprites_dir / source_path.name

    # Check if destination already exists
    if dest_path.exists() and not force:
        result["errors"].append(
            f"Sprite already exists: {dest_path} (use --force to overwrite)"
        )
        return result

    # Ensure staging structure has archive directory
    ensure_staging_structure()
    archive_dir = STAGING_DIR / "archive"
    archive_path = archive_dir / source_path.name

    # ========================================================================
    # Step 5: Copy to assets/sprites/
    # ========================================================================
    try:
        if dest_path.exists():
            result["warnings"].append(f"Overwriting existing sprite: {dest_path}")

        shutil.copy2(source_path, dest_path)
        result["published_path"] = str(dest_path)
        print(f"[PUBLISH] Copied to: {dest_path}")

    except Exception as e:
        result["errors"].append(f"Failed to copy to assets/sprites/: {e}")
        return result

    # ========================================================================
    # Step 6: Archive source
    # ========================================================================
    try:
        # If archive already has a file with same name, add timestamp
        if archive_path.exists():
            import datetime
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_path = archive_dir / f"{source_path.stem}_{timestamp}{source_path.suffix}"
            result["warnings"].append(f"Archived with timestamp (duplicate name)")

        shutil.move(source_path, archive_path)
        result["archived_path"] = str(archive_path)
        print(f"[PUBLISH] Archived to: {archive_path}")

    except Exception as e:
        # Non-fatal: sprite was published but archive failed
        result["warnings"].append(f"Failed to archive source: {e}")

    # ========================================================================
    # Success
    # ========================================================================
    result["success"] = True
    print(f"\n[PUBLISH] SUCCESS: {sprite_name}.xp published to assets/sprites/")

    return result


def parse_args():
    """
    Build and parse the CLI argument parser.  [FLOW:CLI]

    The parser defines three conceptual argument groups:
      - Mode selection: --wizard vs --template (mutually exclusive)
      - Template discovery: --list-templates, --describe-template (can run standalone)
      - Asset configuration: --name, --angles, --frames, --source-type, etc.

    Returns:
        argparse.Namespace with all parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Asciicker Asset Generation Pipeline",
        epilog="""
Examples:
  # Process AI-generated sprite sheet with magenta transparency:
  python -m scripts.pipeline --source-type ai --input sprites.png --name my_char --type character --angles 8 --frames 4

  # Render from Blender scene via MCP:
  python -m scripts.pipeline --source-type blender --blender-object TestCube --name test_cube --type item

  # Load regular PNG file:
  python -m scripts.pipeline --source-type file --input image.png --name asset --type character

  # Direct RGB processing (early exit):
  python -m scripts.pipeline --image-path image.png --process-rgb-to-cp437
        """,
    )

    # Execution mode flags (interactivity control)
    # WHY: Placed before mode_group so --non-interactive is available for
    # conflict checking with --wizard/--tui before mode detection runs.
    exec_group = parser.add_argument_group("Execution Mode")
    exec_group.add_argument(
        "--non-interactive",
        action="store_true",
        default=False,
        help="Disable all interactive prompts (for CI/scripts/piped input)",
    )
    exec_group.add_argument(
        "--interactive",
        action="store_true",
        default=False,
        help="Force interactive prompts even when stdin is not a TTY",
    )
    exec_group.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        default=False,
        help="Output structured JSON result to stdout (progress goes to stderr)",
    )

    # Import Mode flags: explicit import routing through ImportRequest
    # WHY: as_is mode preserves input PNG pixel data (no generation/render),
    # and requires explicit --angles/--frames to eliminate inference ambiguity.
    # sheet_explicit is a synonym for as_is with explicit grid params.
    # "roundtrip" is deferred (not exposed in CLI yet).
    import_group = parser.add_argument_group("Import Mode")
    import_group.add_argument(
        "--import-mode",
        choices=["as_is", "sheet_explicit"],
        default=None,
        help=(
            "Import mode for PNG-to-XP conversion. "
            "as_is: preserve pixels, skip generation (requires --angles, --frames). "
            "sheet_explicit: explicit grid parameters. "
            "When set, routes through ImportRequest instead of raw AssetDef construction."
        ),
    )

    # WHY: --wizard and --template are mutually exclusive because both attempt
    # to build a full AssetDef.  Allowing both would create ambiguous precedence
    # for locked fields like angles/frames.  Raw mode (no flag) is the implicit
    # third option detected by detect_cli_mode() when core args are present.
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--wizard", action="store_true", help="Interactive wizard for guided setup"
    )
    mode_group.add_argument(
        "--template", metavar="NAME", type=str, help="Load template by name"
    )
    mode_group.add_argument(
        "--tui", action="store_true", help="Launch console TUI wizard (no extra dependencies)"
    )
    mode_group.add_argument(
        "--tui-textual", action="store_true", help="Launch rich Textual TUI (requires 'textual' package)"
    )

    # Template discovery flags (allowed with any mode)
    parser.add_argument(
        "--list-templates", action="store_true", help="List available templates"
    )
    parser.add_argument(
        "--describe-template", metavar="NAME", type=str, help="Show template details"
    )

    # Validation flags
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate only, skip rendering"
    )
    parser.add_argument(
        "--force", action="store_true", help="Override locked template fields"
    )
    # NOTE: --strict was removed (HARD-14-06). It was registered but never
    # wired to any pipeline behavior.  The validator (validator.py) has its
    # own --strict that IS wired.  If CI-mode strict pipeline warnings are
    # needed in the future, implement via a warning-capture mechanism.

    # Web API server flag
    parser.add_argument(
        "--serve",
        action="store_true",
        default=False,
        help="Start the Flask HTTP API server for web UI interaction",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5000,
        help="Port for --serve mode (default: 5000)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host for --serve mode (default: 127.0.0.1)",
    )

    # Existing flags (preserved)
    parser.add_argument(
        "prompt", nargs="?", help="Text prompt for the asset (e.g. 'Orc warrior')"
    )
    parser.add_argument(
        "--type",
        choices=["character", "item", "custom"],
        default="custom",
        help="Asset type preset",
    )
    parser.add_argument("--name", help="Output name (default: derived from prompt)")
    parser.add_argument(
        "--angles", type=int, help="Number of angles (common: 1, 4, 8)"
    )
    # WHY: --frames is a string (not int) because multi-angle sprites have
    # varying frame counts per animation row, expressed as CSV (e.g. "1,8"
    # means 1 idle frame + 8 walk frames).  Parsed to list[int] in raw mode.
    parser.add_argument(
        "--frames", type=str, help="Frames per angle (e.g. '8' or '1,8')"
    )
    parser.add_argument(
        "--size", type=str, help="Asset size in cells (e.g. '12,12')"
    )
    parser.add_argument(
        "--source-type",
        choices=["file", "ai", "blender", "mesh"],
        default="file",
        help="Source type: file (load PNG), ai (PNG with magenta transparency), blender (render from scene), mesh (import 3D model)",
    )
    # WHY: --input has --image-path as an alias for backward compatibility with
    # older scripts that used --image-path.  nargs="?" allows it to be optional
    # (template or wizard modes may supply the path interactively).
    # TODO(PIPELINE-FIX): The dual purpose (image path OR blend file path)
    # depending on source_type is confusing.  Consider splitting into --input
    # and --blend-file for clarity.
    parser.add_argument(
        "--input",
        "--image-path",
        help="Path to image file or blend file for processing",
        nargs="?",
    )
    parser.add_argument(
        "--blender-object",
        help="Object name to render (for source_type=blender)",
    )
    parser.add_argument(
        "--transparency",
        action="store_true",
        help="Indicate PNG has magenta (255,0,255) transparency",
    )
    # [FLOW:CLI] Early-exit path: bypasses the entire template/wizard/pipeline
    # flow and directly invokes processor_core to convert a single image.
    parser.add_argument(
        "--process-rgb-to-cp437",
        action="store_true",
        help="Process RGB image to CP437 glyphs and exit (early exit)",
    )
    parser.add_argument(
        "--downscale",
        choices=DOWNSCALE_ALGORITHMS,
        help="Downscaling algorithm (auto if template specifies)",
    )
    parser.add_argument(
        "--normalization",
        action="store_true",
        help="Enable auto-normalization (resize height & pad per angle)",
    )
    parser.add_argument(
        "--target-cells-high",
        type=int,
        default=0,
        help="Target height in cells per angle (0=auto, requires --normalization)",
    )

    # [FLOW:CLI] Publish command for staging-to-production workflow
    parser.add_argument(
        "--publish",
        metavar="XP_PATH",
        type=str,
        help="Publish staging .xp to assets/sprites/ (validates, copies, archives source)"
    )

    # [FLOW:REFORMAT] Reformatter argument group
    # WHY: The reformatter is a pre-pipeline stage that assembles individual
    # AI-generated frame PNGs into a sprite sheet. When --reformat is set,
    # the reformatter runs first and its output feeds into the standard pipeline.
    reformat_group = parser.add_argument_group("Reformatter")
    reformat_group.add_argument(
        "--reformat",
        metavar="FRAME_DIR",
        type=str,
        help="Reformat individual frame PNGs from FRAME_DIR into a sprite sheet before pipeline"
    )
    reformat_group.add_argument(
        "--reflection-dim",
        type=float,
        default=0.5,
        help="Reflection brightness factor (0.0-1.0, default 0.5)"
    )
    reformat_group.add_argument(
        "--reflection-policy",
        choices=["none", "generate", "detect"],
        default=None,
        help="Reflection handling policy (default: generate). 'detect' is diagnostic-only."
    )
    reformat_group.add_argument(
        "--alpha-to-magenta",
        action="store_true",
        default=True,
        help="Convert RGBA alpha to magenta key (default: on)"
    )
    reformat_group.add_argument(
        "--no-alpha-to-magenta",
        action="store_true",
        help="Disable alpha-to-magenta conversion"
    )
    reformat_group.add_argument(
        "--manifest",
        type=str,
        default=None,
        help="Path to guidance manifest JSON for validation"
    )
    reformat_group.add_argument(
        "--write-meta",
        type=str,
        default=None,
        help="Write reformatter output metadata to JSON"
    )

    # [FLOW:AI-BATCH] AI Batch argument group
    # WHY: AI batch generation uses a manifest + prompt pack to generate frames
    # via an AI provider (stub or gemini), then optionally reformats and emits .xp.
    # The --manifest arg is shared with the Reformatter group (already defined above).
    ai_batch_group = parser.add_argument_group("AI Batch")
    ai_batch_group.add_argument(
        "--ai-batch",
        action="store_true",
        default=False,
        help="Run AI batch generation using manifest + prompt pack"
    )
    ai_batch_group.add_argument(
        "--prompt-pack",
        type=str,
        default=None,
        help="Path to prompt pack JSON for AI batch generation"
    )
    ai_batch_group.add_argument(
        "--ai-provider",
        choices=["stub", "gemini", "gemini-cli"],
        default="stub",
        help="AI provider for batch generation (default: stub)"
    )
    ai_batch_group.add_argument(
        "--ai-seed",
        type=int,
        default=None,
        help="Seed for reproducible AI generation"
    )
    ai_batch_group.add_argument(
        "--snap-magenta",
        action="store_true",
        default=False,
        help="Snap near-magenta colors to pure magenta after generation"
    )
    ai_batch_group.add_argument(
        "--verify-stages",
        type=str,
        default=None,
        help="Comma-separated verification stages to run (A,B,C,D,E,F)"
    )
    ai_batch_group.add_argument(
        "--emit-xp",
        action="store_true",
        default=False,
        help="Emit .xp file after reformatting"
    )

    # [FLOW:CLI] Analyze mode: inspect image and suggest parameters
    parser.add_argument(
        "--analyze",
        metavar="IMAGE_PATH",
        type=str,
        help="Inspect image, suggest layout parameters, and exit",
    )

    # [FLOW:CLI] Render mode: XP-to-PNG export via _render_core
    parser.add_argument(
        "--render",
        metavar="XP_PATH",
        type=str,
        help="Render .xp sprite to PNG and exit (uses export_service)",
    )
    parser.add_argument(
        "--render-output",
        metavar="PNG_PATH",
        type=str,
        help="Output path for --render (default: <name>.png next to input)",
    )
    parser.add_argument(
        "--render-scale",
        type=int,
        default=1,
        help="Scale factor for --render output (default: 1)",
    )

    # [FLOW:CLI] Batch manifest mode: declarative multi-file conversion
    parser.add_argument(
        "--batch-manifest",
        metavar="MANIFEST.json",
        type=str,
        help="Process batch manifest with per-file overrides and exit",
    )

    # Explicit slicing flags (Phase 1C)
    slice_group = parser.add_argument_group("Explicit Slicing")
    slice_group.add_argument(
        "--slice-spec",
        metavar="FILE.json",
        type=str,
        help="Full SlicingSpec as JSON file",
    )
    slice_group.add_argument("--cell-w", type=int, help="Cell width in pixels")
    slice_group.add_argument("--cell-h", type=int, help="Cell height in pixels")
    slice_group.add_argument("--cols", type=int, help="Column count")
    slice_group.add_argument("--rows", type=int, help="Row count")
    slice_group.add_argument("--spacing-x", type=int, default=0, help="Horizontal spacing between frames")
    slice_group.add_argument("--spacing-y", type=int, default=0, help="Vertical spacing between frames")
    slice_group.add_argument("--margin-x", type=int, default=0, help="Left/right margin")
    slice_group.add_argument("--margin-y", type=int, default=0, help="Top/bottom margin")
    slice_group.add_argument(
        "--order",
        choices=["angle_major", "frame_major", "animation_major"],
        default="angle_major",
        help=(
            "Sheet layout order. "
            "angle_major = row-per-angle (default, each row is one rotation). "
            "animation_major = row-per-animation (each row is one animation frame). "
            "frame_major = column-major (legacy, columns outer loop)"
        ),
    )
    slice_group.add_argument(
        "--origin",
        choices=["top_left", "bottom_left"],
        default="top_left",
        help="Sheet origin corner",
    )
    slice_group.add_argument(
        "--angle-row-map",
        type=str,
        default=None,
        metavar="MAP",
        help=(
            "Comma-separated angle row permutation (zero-based). "
            "Example: '2,1,0,7,6,5,4,3'. "
            "map[target_angle] = source_row. "
            "Length must equal --angles. "
            "Cannot be used with --order frame_major."
        ),
    )

    # Pre-slice hooks (Phase 12)
    preslice_group = parser.add_argument_group("Pre-Slice Hooks")
    preslice_group.add_argument(
        "--pre-slice-check",
        action="store_true",
        default=False,
        help="Enable grid extractor check before slicing (warn on mismatch)",
    )
    preslice_group.add_argument(
        "--pre-slice-check-strict",
        action="store_true",
        default=False,
        help="Upgrade pre-slice-check mismatch from warning to hard fail",
    )
    preslice_group.add_argument(
        "--pixel-perfect-mode",
        choices=["off", "auto_adjust"],
        default="off",
        help="Pixel-perfect normalization mode (default: off). auto_adjust runs crop_and_center_frames on each cell before slicing.",
    )

    # Background handling flags (Phase 1C)
    bg_group = parser.add_argument_group("Background")
    bg_group.add_argument(
        "--bg-mode",
        choices=["key_color", "alpha", "none"],
        help="Background handling mode",
    )
    bg_group.add_argument(
        "--bg-color",
        type=str,
        help="Key color as hex (e.g. '#FF00FF')",
    )
    bg_group.add_argument(
        "--bg-tolerance",
        type=int,
        default=8,
        help="Color distance tolerance for key color matching",
    )
    bg_group.add_argument(
        "--alpha-threshold",
        type=int,
        default=128,
        help="Alpha value below which pixels are treated as transparent (0-255, default 128). Only used with --bg-mode alpha.",
    )

    # Reflection handling flags (Phase 6)
    # NOTE: --reflection-policy already exists in the Reformatter group above
    # (choices: none/generate/detect, default: generate). That flag controls
    # the reformatter pre-pipeline stage. The pipeline path reads the same
    # args.reflection_policy value via the adapter. Do NOT add a duplicate.
    refl_group = parser.add_argument_group("Reflection Handling")
    refl_group.add_argument(
        "--source-projs",
        type=int,
        choices=[1, 2],
        default=None,
        dest="source_projs",
        help="Source image projection count: 1=single projection (reflections "
             "will be generated), 2=pre-baked reflections in source sheet. "
             "Default: 1 for file imports.",
    )
    refl_group.add_argument(
        "--projs",
        type=int,
        choices=[1, 2],
        default=None,
        help="Explicit projection count (1=no reflections, 2=with reflections). "
             "Default: derived from angles (angles>0 implies projs=2).",
    )
    refl_group.add_argument(
        "--synthesize-angles",
        type=int,
        default=None,
        metavar="N",
        help="Synthesize N viewing angles from a single-angle source. "
             "Requires explicit --angles 1 input. Never implicit.",
    )

    # Keyframe range support (BLEND-15-03)
    blender_group = parser.add_argument_group("Blender Keyframe Ranges")
    blender_group.add_argument(
        "--keyframe-ranges",
        type=str,
        default=None,
        metavar="RANGES",
        help=(
            "Per-animation Blender keyframe ranges. "
            'JSON format: \'[{"start":0,"end":0,"count":1,"name":"idle"},{"start":1,"end":24,"count":8,"name":"walk"}]\'. '
            "Or compact format: 'start-end:count:name,...' e.g. '0-0:1:idle,1-24:8:walk'. "
            "Sum of counts must equal sum of --frames. Only used with --source-type blender."
        ),
    )

    # 4-track pipeline schema flags (Phase 19)
    # Additive only: these do not alter current execution paths yet.
    track_group = parser.add_argument_group("4-Track Pipeline")
    track_group.add_argument(
        "--bg-colors",
        type=str,
        default=None,
        help="Semicolon-separated RGB triplets, e.g. '255,0,255;0,0,0'",
    )
    track_group.add_argument(
        "--min-sprite-size",
        type=str,
        default=None,
        help="Minimum extracted sprite size as WIDTHxHEIGHT (e.g. 30x30)",
    )
    track_group.add_argument(
        "--extraction-mode",
        choices=["shape", "bbox", "both"],
        default=None,
        help="Stage 0 extractor output mode",
    )
    track_group.add_argument(
        "--max-coverage",
        type=float,
        default=None,
        help="Max fraction of source area a single extracted sprite may cover (0.0-1.0). "
             "Set to 1.0 to disable full-sheet rejection.",
    )
    track_group.add_argument(
        "--grid-method",
        choices=["fft", "gradient", "both"],
        default=None,
        help="Grid detection strategy for Perfect Pixel stage",
    )
    track_group.add_argument(
        "--slice-mode",
        choices=["global_grid", "rowcol"],
        default=None,
        help="Stage 2 slicing mode (legacy global grid or row/column detection)",
    )
    track_group.add_argument(
        "--rowcol-fallback-to-grid",
        dest="rowcol_fallback_to_grid",
        action="store_true",
        default=None,
        help="Fallback to global-grid slicing when row/column detection is weak",
    )
    track_group.add_argument(
        "--no-rowcol-fallback-to-grid",
        dest="rowcol_fallback_to_grid",
        action="store_false",
        default=None,
        help="Disable fallback and hard-fail when row/column detection is weak",
    )
    track_group.add_argument(
        "--sample-method",
        choices=["center", "median", "majority"],
        default=None,
        help="Grid cell sampling method",
    )
    track_group.add_argument(
        "--min-grid-size",
        type=float,
        default=None,
        help="Minimum detected pixel-grid size",
    )
    track_group.add_argument(
        "--refine-intensity",
        type=float,
        default=None,
        help="Grid refinement intensity (0.0-1.0)",
    )
    track_group.add_argument(
        "--content-correction",
        action="store_true",
        default=False,
        help="Enable content-aware crop correction after slicing",
    )
    track_group.add_argument(
        "--content-correction-max-shift",
        type=float,
        default=None,
        help="Maximum crop shift ratio for content correction (0.0-0.5)",
    )
    track_group.add_argument(
        "--hard-fail-split-ratio",
        type=float,
        default=None,
        help="Hard-fail threshold for split_ratio diagnostics after slicing",
    )
    track_group.add_argument(
        "--hard-fail-bleed-ratio",
        type=float,
        default=None,
        help="Hard-fail threshold for bleed_ratio diagnostics after slicing",
    )
    track_group.add_argument(
        "--require-human-stage2-review",
        action="store_true",
        default=False,
        help="Require human signoff JSON after Stage 2 slicing",
    )
    track_group.add_argument(
        "--human-stage2-review-path",
        type=str,
        default=None,
        help="Path to Stage 2 human signoff JSON file",
    )
    track_group.add_argument(
        "--process-mode",
        choices=["literal", "standard-legacy", "quality"],
        default=None,
        help="Stage 3 processor mode (standard is deprecated; use quality or auto)",
    )
    track_group.add_argument(
        "--source-cell-px",
        type=int,
        default=None,
        help="Detected/declared source pixels-per-cell",
    )
    track_group.add_argument(
        "--target-cell-px",
        type=int,
        default=None,
        help="Target engine pixels-per-cell",
    )
    track_group.add_argument(
        "--error-diffusion",
        choices=["none", "intra_cell", "cross_cell"],
        default=None,
        help="Error diffusion scope for quality mode",
    )
    track_group.add_argument(
        "--color-metric",
        choices=["euclidean", "perceptual"],
        default=None,
        help="Color distance metric for quantization/matching",
    )
    track_group.add_argument(
        "--max-standard-cell-ratio",
        type=int,
        default=None,
        help="Block standard mode when source frame dimensions exceed CELL_SIZE * ratio",
    )
    track_group.add_argument(
        "--auto-prune",
        action="store_true",
        default=False,
        help="Enable automatic branch pruning",
    )
    track_group.add_argument(
        "--no-auto-prune",
        action="store_true",
        default=False,
        help="Disable automatic branch pruning",
    )
    track_group.add_argument(
        "--max-branches-per-stage",
        type=int,
        default=None,
        help="Cap active branches advanced per stage",
    )
    track_group.add_argument(
        "--max-global-branches",
        type=int,
        default=None,
        help="Cap total active branches across a job",
    )
    track_group.add_argument(
        "--expand-all",
        action="store_true",
        default=False,
        help="Show pruned branches in branch viewer",
    )
    track_group.add_argument(
        "--tie-break-order",
        type=str,
        default=None,
        help="Comma-separated deterministic track tie-break order",
    )
    track_group.add_argument(
        "--enable-branch-loop",
        action="store_true",
        default=False,
        help="Enable multi-track branching in the pipeline",
    )

    return parser.parse_args()


def detect_cli_mode(args) -> Optional[str]:
    """
    Determine CLI mode (wizard/template/raw) from parsed arguments.

    Returns:
        "wizard" if args.wizard is True
        "template" if args.template is specified
        "raw" if all core args present (--name, --angles, --input)
        None if no mode detected (user needs to specify mode or provide core args)
    """
    # WHY: getattr with defaults instead of args.wizard because detect_cli_mode
    # is designed to also accept hand-built Namespace objects from tests or
    # other callers that may not have all fields.  [FLOW:CLI]
    if getattr(args, "wizard", False):
        return "wizard"
    if getattr(args, "template", None):
        return "template"
    # [FLOW:CLI] Check for raw mode: all three core args must be present.
    # If any is missing, return None to trigger the usage error in main().
    # WHY: --reformat substitutes for --input as the image source, so
    # (name + angles + reformat) is also a valid raw mode entry.
    has_input = getattr(args, "input", None) or getattr(args, "reformat", None)
    if (
        getattr(args, "name", None)
        and getattr(args, "angles", None)
        and has_input
    ):
        return "raw"
    return None


def resolve_pipeline_config(args) -> PipelineConfig:
    """Build and validate the shared PipelineConfig from CLI args.

    The config is attached to args for downstream adapters and future stages.
    """
    try:
        config = PipelineConfig.from_cli_args(args)
        args._pipeline_config = config
        args._pipeline_config_dict = config.to_dict()
        return config
    except ValueError as exc:
        raise ConfigError(f"Invalid 4-track pipeline flags: {exc}") from exc


def exit_with_code(code: int, message: str = ""):
    """
    Exit with semantic exit code.

    Exit codes:
        0 = Success
        1 = Usage/config errors (missing args, mode conflicts)
        2 = Validation errors (template load, dimension mismatches)
        3 = Runtime/IO errors (file not found, Blender failure)
        4 = Internal errors (unexpected exceptions)
    """
    if message:
        print(message, file=sys.stderr)
    sys.exit(code)


def load_template_if_needed(
    mode: Optional[str], template_name: Optional[str]
) -> Optional[TemplateModel]:
    """
    Load template from file if mode == "template".

    Args:
        mode: CLI mode (wizard/template/raw)
        template_name: Template name to load

    Returns:
        Template object if loaded, None otherwise

    Raises:
        TemplateLoadError: If template not found or invalid
    """
    if mode != "template" or not template_name:
        return None

    # WHY: Templates are matched by the "name" field inside the JSON, not by
    # filename.  This allows filenames to follow any convention (e.g.
    # character_idle_walk.json) while the user-facing --template flag uses the
    # human-readable name (e.g. "idle_walk").
    # TODO(PIPELINE-FIX): This linear scan opens and parses every JSON file on
    # each invocation.  Consider building a name->path index at startup or
    # caching results for repeated calls.
    templates_dir = Path("scripts/pipeline/templates")

    # [FLOW:TEMPLATE] Search by JSON "name" field, not by filename
    template_path = None
    for f in templates_dir.glob("*.json"):
        try:
            with open(f, "r") as file:
                data = json.load(file)
                if data.get("name") == template_name:
                    template_path = f
                    break
        except Exception:
            continue

    if not template_path:
        exit_with_code(
            2, f"Template not found: {template_name}.json (searched in scripts/pipeline/templates/)"
        )

    try:
        logger.info(f"Loading template from {template_path}")
        template = TemplateLoader.from_file(template_path)
        logger.info(f"Successfully loaded template: {template.name}")
        return template
    except TemplateLoadError as e:
        exit_with_code(2, f"Template load failed: {e}")
    except Exception as e:
        exit_with_code(4, f"Unexpected error loading template: {e}")


def get_templates_by_type(asset_type: Optional[str] = None) -> list:
    """
    Load all templates from scripts/pipeline/templates/, optionally filtered by type.
    [FLOW:CLI] [FLOW:TEMPLATE] Used by the wizard (Screen 3) and --list-templates.

    Args:
        asset_type: Filter to only templates of this type (character, item, ui, etc.).
                    Pass None to return all templates regardless of type.

    Returns:
        list[dict]: Template dictionaries with keys:
            - name (str): Human-readable template name from JSON "name" field.
            - type (str): Asset type (character, item, ui, custom).
            - description (str): Auto-generated summary string.
            - angles (int): Number of rotation angles.
            - frames (list[int] | int): Frames per angle.
            - file (Path): Path to the source JSON file.

    Template description format examples:
      - Character/idle_walk: "8-direction character animation (Angles: 8 * Frames: 1,8)"
      - Item/static: "Item asset (Angles: 1 * Frames: 1)"
    """
    templates_dir = Path("scripts/pipeline/templates")
    templates = []

    if not templates_dir.exists():
        logger.warning(f"Templates directory not found: {templates_dir}")
        return templates

    for template_file in templates_dir.glob("*.json"):
        try:
            with open(template_file, "r") as f:
                data = json.load(f)

            # Apply type filter if specified
            if asset_type and data.get("type") != asset_type:
                continue

            # Build description
            t_type = data.get("type", "custom")
            angles = data.get("angles", 1)
            frames = data.get("frames", [1])
            frames_str = (
                ",".join(str(f) for f in frames)
                if isinstance(frames, list)
                else str(frames)
            )
            total_frames = sum(frames) if isinstance(frames, list) else int(frames)

            description = f"Angles: {angles} • Frames: {frames_str}"
            if t_type == "character":
                description = f"8-direction character animation ({description})"
            elif t_type == "item":
                description = f"Item asset ({description})"
            elif t_type == "ui":
                description = f"UI element ({description})"

            templates.append(
                {
                    "name": data.get("name", template_file.stem),
                    "type": t_type,
                    "description": description,
                    "angles": angles,
                    "frames": frames,
                    "file": template_file,
                }
            )

        except Exception as e:
            logger.warning(f"Failed to read {template_file}: {e}")

    return templates


def run_wizard_mode() -> 'WizardResult':
    """
    Interactive wizard flow with real back navigation.  [FLOW:CLI]

    internal design notes implementation:
    - Back navigation: Real loop that returns to previous screen
    - Availability markers: Shows [unavailable] on intents with missing deps
    - Breadcrumb: Shows progress trail at each screen
    - Error format: ALL errors use WizardErrorHandler.format_error()
    - Template validation: Pre-validate (loadable) and post-validate (constraints)

    Returns WizardResult dataclass (WIZ-03) with all fields needed for main()
    to build AssetDef and invoke the pipeline.

    Screens:
      1. Intent selection (with availability markers)
      2. Asset type
      3. Template selection (with enforcement per internal design notes)
      4. Input source + path
      5. Summary + confirmation

    Returns:
        WizardResult with success status, template, paths, and any errors.
    """
    # [FLOW:CLI] Lazy-load wizard symbols and questionary (only in interactive mode).
    _wiz = _ensure_wizard()
    questionary = _ensure_questionary()
    WizardNav = _wiz["WizardNav"]
    WizardScreen = _wiz["WizardScreen"]
    WizardErrorHandler = _wiz["WizardErrorHandler"]
    WizardResult = _wiz["WizardResult"]
    check_pipeline_availability = _wiz["check_pipeline_availability"]
    format_intent_choice = _wiz["format_intent_choice"]
    get_unavailable_error = _wiz["get_unavailable_error"]
    get_intent_display_name = _wiz["get_intent_display_name"]
    is_template_required = _wiz["is_template_required"]
    check_template_required_for_intent = _wiz["check_template_required_for_intent"]
    validate_template_loadable = _wiz["validate_template_loadable"]
    validate_intent = _wiz["validate_intent"]

    nav = WizardNav()
    error_handler = WizardErrorHandler()

    # Check pipeline availability at startup (internal design notes requirement)
    availability = check_pipeline_availability()
    for warning in availability.warnings:
        error_handler.print_message('warning', warning)

    print("\n" + "=" * 60)
    print("Asciicker Asset Generation Wizard")
    print("=" * 60 + "\n")

    # Main wizard loop with real back navigation
    current_screen = WizardScreen.INTENT

    while True:
        # Print breadcrumb (internal design notes requirement)
        error_handler.print_breadcrumb(
            nav.get_breadcrumb_steps(),
            nav.get_breadcrumb_index() if nav.get_current_screen() else 0
        )
        print()

        # ================================================================
        # Screen 1: Intent selection (with availability markers)
        # ================================================================
        if current_screen == WizardScreen.INTENT:
            # Build choices with availability markers
            intent_choices = [
                format_intent_choice('new_character', 'New character asset', availability),
                format_intent_choice('convert_sheet', 'Convert sprite sheet to XP format', availability),
                format_intent_choice('render_blender', 'Render from Blender scene', availability),
                format_intent_choice('modify_xp', 'Modify existing XP asset', availability),
                {"name": "Ask Claude for help", "value": "ask_claude"},
            ]

            intent = questionary.select(
                "What do you want to create?",
                choices=intent_choices,
            ).ask()

            if intent is None:
                error_handler.print_message('info', "Wizard cancelled.")
                return WizardResult.cancelled()

            # Handle "Ask Claude" option
            if intent == 'ask_claude':
                error_handler.print_message('info', "\nClaude Help:")
                error_handler.print_message('info', "  - new_character: Create animated character sprites")
                error_handler.print_message('info', "  - convert_sheet: Convert existing PNG to .xp format")
                error_handler.print_message('info', "  - render_blender: Render 3D model to sprite sheet")
                error_handler.print_message('info', "  - modify_xp: Edit existing .xp file")

                sub_choice = questionary.select(
                    "What would you like to do?",
                    choices=[
                        {"name": "Try again", "value": "retry"},
                        {"name": "Open Claude Code", "value": "claude"},
                        {"name": "Exit", "value": "exit"},
                    ]
                ).ask()

                if sub_choice == 'retry':
                    continue  # Loop back to intent selection
                elif sub_choice == 'claude':
                    error_handler.print_message('info', f"\nWizard state: {nav.state}")
                    error_handler.print_message('info', "Open Claude Code and describe what you want to create.")
                    return WizardResult.cancelled()
                else:
                    return WizardResult.cancelled()

            # Check if selected intent is available
            intent_status = availability.intents.get(intent)
            if intent_status and not intent_status.available:
                # Block with specific error and install instructions
                error_msg = get_unavailable_error(intent, availability, error_handler.log_path)
                error_handler.print_error('UNAVAILABLE', f"{intent} cannot be used", {
                    'reason': intent_status.reason,
                    'instructions': intent_status.install_instructions
                })
                print(error_msg)
                # Return to menu (continue loop)
                continue

            nav.set_state('intent', intent)
            nav.push_screen(WizardScreen.INTENT)
            current_screen = WizardScreen.ASSET_TYPE

        # ================================================================
        # Screen 2: Asset type selection
        # ================================================================
        elif current_screen == WizardScreen.ASSET_TYPE:
            asset_type = nav.ask_with_back(
                "What type of asset?",
                choices=[
                    {"name": "Character (animated sprite)", "value": "character"},
                    {"name": "Item (static object)", "value": "item"},
                    {"name": "UI element (interface graphics)", "value": "ui"},
                    {"name": "Custom (raw mode)", "value": "custom"},
                ],
                screen=WizardScreen.ASSET_TYPE,
                show_back=True
            )

            if asset_type is None:
                error_handler.print_message('info', "Wizard cancelled.")
                return WizardResult.cancelled()

            if asset_type == nav.BACK_SENTINEL:
                current_screen = nav.go_back() or WizardScreen.INTENT
                continue

            nav.set_state('asset_type', asset_type)

            # Handle custom mode with intent-specific guidance
            if asset_type == "custom":
                intent = nav.get_state('intent')
                if intent == 'new_character':
                    # internal design notes: "Character sprites require a template. If this is a logo/sign/UI sprite..."
                    error_handler.print_error('TEMPLATE',
                        "Character sprites require a template. If this is a logo/sign/UI sprite, choose Item or UI (or Custom raw mode).",
                        {'intent': intent, 'asset_type': asset_type}
                    )
                    # Return to asset type selection
                    continue
                else:
                    error_handler.print_message('info', "Custom mode selected - proceeding without template constraints.")
                    nav.set_state('is_custom_mode', True)

            current_screen = WizardScreen.TEMPLATE

        # ================================================================
        # Screen 3: Template selection
        # ================================================================
        elif current_screen == WizardScreen.TEMPLATE:
            asset_type = nav.get_state('asset_type')
            intent = nav.get_state('intent')
            is_custom_mode = nav.get_state('is_custom_mode', False)

            # Skip template selection for custom mode
            if is_custom_mode:
                current_screen = WizardScreen.SOURCE
                continue

            templates = get_templates_by_type(asset_type)

            if not templates:
                # Check if template is required
                check_result = check_template_required_for_intent(intent, None, is_custom_mode)
                if not check_result.success:
                    error_handler.print_error('TEMPLATE', check_result.errors[0])
                    if len(check_result.errors) > 1:
                        error_handler.print_message('info', check_result.errors[1])
                    # Go back to asset type to choose different type
                    current_screen = nav.go_back() or WizardScreen.ASSET_TYPE
                    continue

                error_handler.print_message('warning', f"No templates found for type: {asset_type}")
                error_handler.print_message('info', "Proceeding without template constraints.")
                nav.set_state('template', None)
                current_screen = WizardScreen.SOURCE
                continue

            # Build template choices
            template_choices = []
            for t in templates:
                template_choices.append({
                    "name": f"{t['name']} - {t['description']}",
                    "value": t["file"],
                })

            # Add custom option with warning for character intent
            if is_template_required(intent, is_custom_mode=False):
                template_choices.append({
                    "name": "Custom (raw mode) [NOT RECOMMENDED for this intent]",
                    "value": "__custom__"
                })
            else:
                template_choices.append({"name": "Custom (raw mode)", "value": "__custom__"})

            template_path = nav.ask_with_back(
                f"Select a {asset_type} template:",
                choices=template_choices,
                screen=WizardScreen.TEMPLATE,
                show_back=True
            )

            if template_path is None:
                error_handler.print_message('info', "Wizard cancelled.")
                return WizardResult.cancelled()

            if template_path == nav.BACK_SENTINEL:
                current_screen = nav.go_back() or WizardScreen.ASSET_TYPE
                continue

            if template_path == "__custom__":
                # Check if template is required for this intent
                check_result = check_template_required_for_intent(intent, None, is_custom_mode=False)
                if not check_result.success:
                    error_handler.print_error('TEMPLATE', check_result.errors[0])
                    if len(check_result.errors) > 1:
                        error_handler.print_message('info', check_result.errors[1])
                    continue  # Stay on template screen

                nav.set_state('is_custom_mode', True)
                nav.set_state('template', None)
                current_screen = WizardScreen.SOURCE
                continue

            # Pre-validate template is loadable (CONTEXT.MD requirement)
            is_valid, errors = validate_template_loadable(Path(template_path))
            if not is_valid:
                error_handler.print_error('TEMPLATE', f"Failed to load: {errors[0]}")
                continue  # Stay on template screen to try again

            # Load selected template
            try:
                template = TemplateLoader.from_file(template_path)
                error_handler.print_message('success', f"Loaded template: {template.name}")
                nav.set_state('template', template)
            except (TemplateLoadError, Exception) as e:
                error_handler.print_error('TEMPLATE', f"Failed to load: {e}")
                continue  # Stay on template screen

            current_screen = WizardScreen.SOURCE

        # ================================================================
        # Screen 4: Source selection and input path
        # ================================================================
        elif current_screen == WizardScreen.SOURCE:
            intent = nav.get_state('intent')

            # Special handling for modify_xp: ask for XP file directly and launch editor
            if intent == 'modify_xp':
                xp_path = nav.ask_text_with_back(
                    "Path to existing .xp file:",
                    screen=WizardScreen.SOURCE,
                    default="assets/sprites/player.xp",
                    show_back=True
                )

                if xp_path is None:
                    error_handler.print_message('info', "Wizard cancelled.")
                    return WizardResult.cancelled()

                if xp_path == nav.BACK_SENTINEL:
                    current_screen = nav.go_back() or WizardScreen.TEMPLATE
                    continue

                # Validate it's an .xp file
                if not xp_path.endswith('.xp'):
                    error_handler.print_error('INPUT', f"Expected .xp file, got: {xp_path}")
                    continue  # Stay on this screen

                # Validate file exists
                xp_path_obj = Path(xp_path)
                if not xp_path_obj.exists():
                    error_handler.print_error('INPUT', f"File not found: {xp_path}")
                    continue  # Stay on this screen

                # Launch xp_tool.py directly - this is the modify_xp intent purpose
                xp_tool_module = "scripts.pipeline.xp_tool"
                project_root = Path(__file__).parent.parent.parent
                print(f"\nLaunching XP editor...")
                proc = subprocess.Popen(
                    [sys.executable, "-m", xp_tool_module, str(xp_path_obj.resolve())],
                    cwd=str(project_root),
                    start_new_session=True
                )
                print(f"  → xp_tool launched (PID: {proc.pid})")
                print(f"  → File: {xp_path_obj.name}")

                # Return success - no pipeline execution needed for modify intent
                return WizardResult(
                    success=True,
                    intent='modify_xp',
                    input_path=xp_path_obj,
                    output_path=xp_path_obj,  # Output is the same file being edited
                )

            # Pre-populate source_type based on intent (BLEND-15-06).
            # The suggested option is placed first so questionary highlights it
            # as the default cursor position.  User can still override.
            _source_choices = [
                {"name": "File (PNG sprite sheet)", "value": "file"},
                {"name": "AI (PNG with magenta transparency)", "value": "ai"},
                {"name": "Blender (render from scene)", "value": "blender"},
                {"name": "3D Mesh (OBJ/STL/FBX/GLTF/GLB/PLY)", "value": "mesh"},
            ]
            _intent_to_source = {
                "render_blender": "blender",
                "convert_sheet": "file",
                "new_character": "ai",
            }
            _suggested = _intent_to_source.get(intent)
            if _suggested:
                # Move suggested choice to front of list
                _source_choices = sorted(
                    _source_choices,
                    key=lambda c: 0 if c["value"] == _suggested else 1,
                )

            source_type = nav.ask_with_back(
                "Source type:",
                choices=_source_choices,
                screen=WizardScreen.SOURCE,
                show_back=True
            )

            if source_type is None:
                error_handler.print_message('info', "Wizard cancelled.")
                return WizardResult.cancelled()

            if source_type == nav.BACK_SENTINEL:
                current_screen = nav.go_back() or WizardScreen.TEMPLATE
                continue

            nav.set_state('source_type', source_type)
            current_screen = WizardScreen.INPUT_PATH

        elif current_screen == WizardScreen.INPUT_PATH:
            source_type = nav.get_state('source_type')

            if source_type not in ("blender",):
                if source_type == "mesh":
                    path_hint = "model.obj"
                elif source_type == "file":
                    path_hint = "sprites.png"
                else:
                    path_hint = "ai_output.png"
                input_path = nav.ask_text_with_back(
                    f"Input path ({path_hint}):",
                    screen=WizardScreen.INPUT_PATH,
                    default=path_hint if source_type == "file" else "ai_sprites.png",
                    show_back=True
                )

                if input_path is None:
                    error_handler.print_message('info', "Wizard cancelled.")
                    return WizardResult.cancelled()

                if input_path == nav.BACK_SENTINEL:
                    current_screen = nav.go_back() or WizardScreen.SOURCE
                    continue

                nav.set_state('input_path', input_path)
            else:
                # Blender mode needs blend file and object name
                blend_file_path = nav.ask_text_with_back(
                    "Blender .blend file path:",
                    screen=WizardScreen.INPUT_PATH,
                    default="player_idle_walk.blend",
                    show_back=True
                )

                if blend_file_path is None or blend_file_path == nav.BACK_SENTINEL:
                    if blend_file_path == nav.BACK_SENTINEL:
                        current_screen = nav.go_back() or WizardScreen.SOURCE
                        continue
                    error_handler.print_message('info', "Wizard cancelled.")
                    return WizardResult.cancelled()

                blender_object = questionary.text(
                    "Blender object name to render:",
                    default="Cube"
                ).ask()

                if blender_object is None:
                    error_handler.print_message('info', "Wizard cancelled.")
                    return WizardResult.cancelled()

                nav.set_state('blend_file_path', blend_file_path)
                nav.set_state('blender_object', blender_object)

            current_screen = WizardScreen.CONFIGURE

        # ================================================================
        # Screen 5: Configure asset parameters
        # ================================================================
        elif current_screen == WizardScreen.CONFIGURE:
            template = nav.get_state('template')

            # Derive defaults from template when available
            default_angles = getattr(template, 'angles', 8) if template else 8
            default_frames_str = (
                ",".join(str(f) for f in template.frames)
                if template and hasattr(template, 'frames') and template.frames
                else "1"
            )

            print("\n" + "-" * 60)
            print("Configuration")
            print("-" * 60)

            # 1. Render resolution
            render_res_answer = questionary.text(
                "Render resolution (pixels per cell, default 24):",
                default="24",
                validate=lambda val: (
                    True if val.isdigit() and 12 <= int(val) <= 96
                    else "Enter a number between 12 and 96"
                ),
            ).ask()

            if render_res_answer is None:
                error_handler.print_message('info', "Wizard cancelled.")
                return WizardResult.cancelled()

            wizard_render_resolution = int(render_res_answer)

            # 2. Angles
            angles_answer = questionary.select(
                "Number of rotation angles:",
                choices=[
                    {"name": "1 (single view)", "value": "1"},
                    {"name": "2", "value": "2"},
                    {"name": "4 (cardinal)", "value": "4"},
                    {"name": "8 (full rotation)", "value": "8"},
                ],
                default=str(default_angles),
            ).ask()

            if angles_answer is None:
                error_handler.print_message('info', "Wizard cancelled.")
                return WizardResult.cancelled()

            wizard_angles = int(angles_answer)

            # 3. Frames
            frames_answer = questionary.text(
                "Animation frame counts (comma-separated, e.g. '1,8' for idle+walk):",
                default=default_frames_str,
                validate=lambda val: (
                    True if all(
                        p.strip().isdigit() and int(p.strip()) >= 1
                        for p in val.split(",") if p.strip()
                    ) and val.strip()
                    else "Enter comma-separated positive integers (e.g. 1,8)"
                ),
            ).ask()

            if frames_answer is None:
                error_handler.print_message('info', "Wizard cancelled.")
                return WizardResult.cancelled()

            wizard_frames_str = frames_answer.strip()

            # 4. Cell size
            cell_size_answer = questionary.text(
                "Output cell size (usually matches render_resolution):",
                default=str(wizard_render_resolution),
                validate=lambda val: (
                    True if val.isdigit() and 8 <= int(val) <= 128
                    else "Enter a number between 8 and 128"
                ),
            ).ask()

            if cell_size_answer is None:
                error_handler.print_message('info', "Wizard cancelled.")
                return WizardResult.cancelled()

            wizard_cell_size = int(cell_size_answer)

            # 5. Background mode
            bg_mode_answer = questionary.select(
                "Background mode:",
                choices=[
                    {"name": "Magenta key color", "value": "magenta"},
                    {"name": "Alpha (transparent)", "value": "alpha"},
                    {"name": "Key color (custom)", "value": "key_color"},
                ],
                default="magenta",
            ).ask()

            if bg_mode_answer is None:
                error_handler.print_message('info', "Wizard cancelled.")
                return WizardResult.cancelled()

            # 6. Keyframe ranges (BLEND-15-03)
            # Offer keyframe mapping when source is Blender and multiple animations
            wizard_keyframe_ranges = None
            wiz_source_type = nav.get_state('source_type')
            wiz_frames_list = [int(x.strip()) for x in wizard_frames_str.split(",") if x.strip()]
            if wiz_source_type == "blender" and len(wiz_frames_list) > 1:
                kr_answer = questionary.confirm(
                    "Define Blender keyframe ranges per animation?",
                    default=False,
                ).ask()
                if kr_answer is None:
                    error_handler.print_message('info', "Wizard cancelled.")
                    return WizardResult.cancelled()

                if kr_answer:
                    from .schemas import AnimationRange
                    kr_list = []
                    for idx, fc in enumerate(wiz_frames_list):
                        anim_label = f"Animation {idx + 1} ({fc} frames)"
                        kr_name = questionary.text(
                            f"  {anim_label} — name (optional):",
                            default="",
                        ).ask()
                        if kr_name is None:
                            error_handler.print_message('info', "Wizard cancelled.")
                            return WizardResult.cancelled()

                        kr_start = questionary.text(
                            f"  {anim_label} — start keyframe:",
                            default="0",
                            validate=lambda v: True if v.isdigit() else "Enter a non-negative integer",
                        ).ask()
                        if kr_start is None:
                            error_handler.print_message('info', "Wizard cancelled.")
                            return WizardResult.cancelled()

                        kr_end = questionary.text(
                            f"  {anim_label} — end keyframe:",
                            default=kr_start,
                            validate=lambda v: True if v.isdigit() else "Enter a non-negative integer",
                        ).ask()
                        if kr_end is None:
                            error_handler.print_message('info', "Wizard cancelled.")
                            return WizardResult.cancelled()

                        kr_list.append(AnimationRange(
                            count=fc,
                            keyframe_start=int(kr_start),
                            keyframe_end=int(kr_end),
                            name=kr_name.strip(),
                        ))
                    wizard_keyframe_ranges = kr_list

            # Store in nav state for use in job creation and summary
            nav.set_state('render_resolution', wizard_render_resolution)
            nav.set_state('angles', wizard_angles)
            nav.set_state('frames', wizard_frames_str)
            nav.set_state('cell_size', wizard_cell_size)
            nav.set_state('bg_mode', bg_mode_answer)
            if wizard_keyframe_ranges:
                nav.set_state('keyframe_ranges', wizard_keyframe_ranges)

            nav.push_screen(WizardScreen.CONFIGURE)
            current_screen = WizardScreen.SUMMARY

        # ================================================================
        # Screen 6: Summary and confirmation
        # ================================================================
        elif current_screen == WizardScreen.SUMMARY:
            template = nav.get_state('template')
            intent = nav.get_state('intent')
            asset_type = nav.get_state('asset_type')
            source_type = nav.get_state('source_type')
            input_path = nav.get_state('input_path')
            blend_file_path = nav.get_state('blend_file_path')
            blender_object = nav.get_state('blender_object')

            # Configuration values from CONFIGURE screen
            cfg_render_res = nav.get_state('render_resolution', 24)
            cfg_angles = nav.get_state('angles', 8)
            cfg_frames = nav.get_state('frames', '1')
            cfg_cell_size = nav.get_state('cell_size', 24)
            cfg_bg_mode = nav.get_state('bg_mode', 'magenta')

            print("\n" + "-" * 60)
            print("Summary")
            print("-" * 60)
            if template:
                print(f"Template: {template.name}")
            print(f"Intent: {get_intent_display_name(intent)}")
            print(f"Type: {asset_type}")
            print(f"Source: {source_type}")
            if input_path:
                print(f"Input: {input_path}")
            if blend_file_path:
                print(f"Blend file: {blend_file_path}")
                print(f"Object: {blender_object}")
            print(f"Render resolution: {cfg_render_res}px")
            print(f"Angles: {cfg_angles}")
            print(f"Frames: {cfg_frames}")
            print(f"Cell size: {cfg_cell_size}px")
            print(f"Background: {cfg_bg_mode}")
            print("-" * 60)

            proceed_choice = questionary.select(
                "Proceed with these settings?",
                choices=[
                    {"name": "Yes, proceed", "value": "proceed"},
                    {"name": "Go back", "value": "back"},
                    {"name": "Cancel", "value": "cancel"},
                ]
            ).ask()

            if proceed_choice is None or proceed_choice == "cancel":
                error_handler.print_message('info', "Wizard cancelled.")
                return WizardResult.cancelled()

            if proceed_choice == "back":
                current_screen = nav.go_back() or WizardScreen.CONFIGURE
                continue

            # Proceed with wizard completion
            break

    # ================================================================
    # Build and validate wizard state
    # ================================================================
    error_handler.print_message('info', "\nWizard complete. Validating configuration...")

    wizard_state = {
        'intent': nav.get_state('intent'),
        'template': nav.get_state('template'),
        'asset_type': nav.get_state('asset_type'),
        'source_type': nav.get_state('source_type'),
        'input_path': nav.get_state('input_path'),
        'blend_file_path': nav.get_state('blend_file_path'),
        'blender_object': nav.get_state('blender_object'),
        'is_custom_mode': nav.get_state('is_custom_mode', False),
        # CONFIGURE screen values (BLEND-15-02)
        'render_resolution': nav.get_state('render_resolution', 24),
        'angles': nav.get_state('angles', 8),
        'frames': nav.get_state('frames', '1'),
        'cell_size': nav.get_state('cell_size', 24),
        'bg_mode': nav.get_state('bg_mode', 'magenta'),
    }

    # Validate intent-specific requirements (WIZ-02)
    result = validate_intent(wizard_state['intent'], wizard_state)

    # Add log path
    if result.log_path is None:
        result.log_path = error_handler.log_path

    # Print any warnings
    for warning in result.warnings:
        error_handler.print_message('warning', warning)

    # Attach CONFIGURE screen values to result (BLEND-15-02)
    result.configure = {
        'render_resolution': wizard_state['render_resolution'],
        'angles': wizard_state['angles'],
        'frames': wizard_state['frames'],
        'cell_size': wizard_state['cell_size'],
        'bg_mode': wizard_state['bg_mode'],
    }
    # Attach keyframe ranges if configured (BLEND-15-03)
    wiz_kr = nav.get_state('keyframe_ranges')
    if wiz_kr:
        result.configure['keyframe_ranges'] = wiz_kr

    return result


def resolve_field_config(args, template: Optional["TemplateModel"] = None) -> dict:
    """
    Resolve field configuration from CLI args, template, and global defaults.
    [FLOW:CLI] [FLOW:TEMPLATE] -- Central merge point for the argument resolution chain.

    Field resolution order (three tiers with different override rules):

    - **Locked fields** (angles, frames, layout, cell_size, frame_size):
      CLI (if args.force) -> template. Error if CLI conflicts without --force.
      WHY: These define the sprite sheet geometry -- changing them post-template
      would silently produce misaligned output that fails at assembly time.
    - **Tunable fields** (downscale, transparency, tolerance, palette):
      CLI > template > global. Log warning when CLI overrides template.
    - **Metadata fields** (name, output_path, tags):
      CLI > template > global (no warning needed).

    Args:
        args: Parsed CLI arguments
        template: Optional Template object

    Returns:
        Config dict with resolved values
    """
    config = {}

    if template:
        # Locked fields (no override without --force)
        locked_fields = ["angles", "frames", "layout", "cell_size", "frame_size"]
        for field in locked_fields:
            template_val = getattr(template, field, None)
            cli_val = getattr(args, field, None)

            if cli_val is not None and template_val is not None:
                if getattr(args, "force", False):
                    config[field] = cli_val
                    logger.warning(
                        f"Overriding template '{template.name}' field: {field} (CLI: {cli_val}, template: {template_val})"
                    )
                else:
                    exit_with_code(
                        2,
                        f"Cannot override template '{template.name}' field '{field}' "
                        f"(use --force to override, or remove this flag)",
                    )
            else:
                config[field] = template_val

        # Tunable fields (CLI takes precedence, log warning on override)
        tunable_fields = ["downscale", "transparency", "tolerance", "palette", "normalization", "target_cells_high"]
        for field in tunable_fields:
            cli_val = getattr(args, field, None)
            template_val = getattr(template, field, None) if template else None
            global_val = {
                "downscale": "nearest",  # Default fallback
                "transparency": False,
                "normalization": False,
                "target_cells_high": 0,
                "tolerance": None,
                "palette": None,
            }.get(field)

            if (
                cli_val is not None
                and template_val is not None
                and cli_val != template_val
            ):
                logger.warning(
                    f"CLI --{field}={cli_val} overrides template {field}={template_val}"
                )

            # TODO(PIPELINE-FIX): The `or` chain treats falsy values (0, False, "")
            # as unset.  This means --transparency=False or --target-cells-high=0
            # will fall through to the template/global value instead of being
            # respected as explicit user choices.  Use `if x is not None` checks.
            config[field] = cli_val or template_val or global_val

        # Metadata fields (CLI > template > global, no warning)
        metadata_fields = ["name", "output_path", "tags"]
        for field in metadata_fields:
            cli_val = getattr(args, field, None)
            template_val = getattr(template, field, None) if template else None
            config[field] = cli_val or template_val or None
    else:
        # No template: use CLI values or defaults
        config["name"] = getattr(args, "name", None)
        config["angles"] = getattr(args, "angles", None)
        config["transparency"] = getattr(args, "transparency", False)
        config["downscale"] = getattr(args, "downscale", None)

    return config


def select_algorithm(
    source_type: str,
    cli_algorithm: str | None = None,
    template_default: str | None = None,
) -> str:
    """
    Select downscaling algorithm based on priority order.
    [FLOW:CLI] [PIPELINE:PROCESS] -- Result is passed to pipeline.run(algorithm=...).

    1. CLI override (--downscale flag) - highest priority
    2. Template default (processing.downscale field)
    3. Source-type default (from DEFAULT_BY_SOURCE)
    4. Fallback to "nearest"

    Args:
        source_type: Type of source ("ai", "blender", "file")
        cli_algorithm: Algorithm specified via CLI --downscale flag
        template_default: Algorithm specified in template's processing.downscale

    Returns:
        Selected downscaling algorithm name
    """
    # Priority 1: CLI override
    if cli_algorithm:
        # WHY: "auto" is a pseudo-value meaning "don't override, let the
        # template or source-type default decide."  It's useful when a user
        # wants to explicitly request automatic selection in a script.
        if cli_algorithm == "auto":
            pass
        else:
            return cli_algorithm

    # Priority 2: Template default
    if template_default:
        return template_default

    # Priority 3: Source-type default
    algorithm = DEFAULT_BY_SOURCE.get(source_type, "nearest")

    # Validate final choice
    if algorithm not in DOWNSCALE_ALGORITHMS:
        import warnings

        warnings.warn(f"Invalid algorithm '{algorithm}', falling back to 'nearest'")
        algorithm = "nearest"

    return algorithm


def _show_preview_options(output_path: Path) -> None:
    """
    Show preview options after pipeline completes.

    Offers terminal preview (debug_sprite), GUI editor (xp_tool), or skip.
    Auto-launches xp_tool if user selects GUI option.

    Args:
        output_path: Absolute path to the generated .xp file.
    """
    import subprocess
    import os

    print("\n" + "─" * 50)
    print("PREVIEW OPTIONS")
    print("─" * 50)
    print(f"Output: {output_path}")
    print()

    # Get the scripts directory for tool paths
    scripts_dir = Path(__file__).parent.parent

    try:
        q = _ensure_questionary()
        choice = q.select(
            "How would you like to preview?",
            choices=[
                q.Choice("Terminal preview (ASCII art)", value="terminal"),
                q.Choice("GUI editor (xp_tool)", value="gui"),
                q.Choice("Visual preview (PNG)", value="png"),
                q.Choice("Skip preview", value="skip"),
            ],
        ).ask()

        if choice == "terminal":
            # Run debug_sprite.py for terminal preview
            print("\n" + "─" * 50)
            from .debug_sprite import debug_xp_file
            debug_xp_file(str(output_path))

        elif choice == "gui":
            # Launch xp_tool.py as a module (handles imports correctly)
            xp_tool_module = "scripts.pipeline.xp_tool"
            project_root = Path(__file__).parent.parent.parent
            print(f"\nLaunching GUI editor...")
            proc = subprocess.Popen(
                [sys.executable, "-m", xp_tool_module, str(output_path)],
                cwd=str(project_root),
                start_new_session=True
            )
            print(f"  → xp_tool launched (PID: {proc.pid})")
            print(f"  → File: {output_path.name}")

        elif choice == "png":
            # Generate PNG using xp_to_png.py
            print(f"\nGenerating visual preview...")
            xp_to_png_script = scripts_dir / "xp_to_png.py"
            font_path = scripts_dir.parent / "assets" / "fonts" / "cp437_12x12.png.bdf"
            png_output = output_path.with_suffix(".png")
            
            cmd = [
                sys.executable, str(xp_to_png_script),
                str(output_path),
                "-o", str(png_output),
                "--font", str(font_path),
                "--scale", "2"
            ]
            
            try:
                subprocess.run(cmd, check=True)
                print(f"  → PNG generated: {png_output.name}")
                # On macOS, try to open it
                if sys.platform == "darwin":
                    subprocess.run(["open", str(png_output)])
            except Exception as e:
                print(f"  → Failed to generate PNG: {e}")

        elif choice == "skip" or choice is None:
            print("\nSkipping preview.")

    except (KeyboardInterrupt, EOFError):
        print("\nSkipping preview.")


def main():
    """
    Top-level entry point for the asset generation CLI.  [FLOW:CLI]

    Execution flow:
      1. Parse arguments (parse_args)
      2. Handle early-exit flags: --list-templates, --describe-template,
         --process-rgb-to-cp437  (these return immediately)
      3. Detect mode: wizard / template / raw (detect_cli_mode)
      4. Build AssetDef via the detected mode's code path
      5. Validate the AssetDef (asset.validate)
      6. If not --dry-run, instantiate AssetPipeline and call run()
         [PIPELINE:GENERATE -> PIPELINE:SLICE -> PIPELINE:PROCESS -> PIPELINE:ASSEMBLE]
    """
    args = parse_args()

    # [FLOW:CLI] JSON mode: activate BEFORE interactivity resolution so
    # early config errors (e.g. --non-interactive --interactive) emit JSON.
    _original_stdout = sys.stdout
    _json_output = getattr(args, "json_output", False)
    if _json_output:
        sys.stdout = sys.stderr

    # [FLOW:CLI] Resolve interactivity BEFORE any mode detection or prompt path.
    # This catches --non-interactive + --tui conflicts early.
    try:
        is_interactive = resolve_interactivity(args)
    except ConfigError as exc:
        if _json_output:
            import json as _json_mod
            _json_result = {
                "status": "error",
                "output_path": None,
                "warnings": [],
                "errors": [str(exc)],
                "trace_id": None,
            }
            sys.stdout = _original_stdout
            print(_json_mod.dumps(_json_result))
            sys.exit(1)
        exit_with_code(1, f"Error: {exc}")

    # Parse/validate shared 4-track schema flags.
    try:
        resolve_pipeline_config(args)
    except ConfigError as exc:
        if _json_output:
            import json as _json_mod
            _json_result = {
                "status": "error",
                "output_path": None,
                "warnings": [],
                "errors": [str(exc)],
                "trace_id": None,
            }
            sys.stdout = _original_stdout
            print(_json_mod.dumps(_json_result))
            sys.exit(1)
        exit_with_code(1, f"Error: {exc}")

    # [FLOW:CLI] GAP-11-05: Detect positional arg that looks like an image file.
    # Users often try `cli.py myimage.png` expecting it to be --input, but the
    # positional arg is actually for AI text prompts.  Catch this early.
    _prompt_val = getattr(args, "prompt", None)
    if _prompt_val:
        _prompt_path = Path(_prompt_val)
        if _prompt_path.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp", ".gif"):
            exit_with_code(
                1,
                f"Error: '{_prompt_val}' looks like an image file, but the positional "
                f"argument is for AI text prompts.\n"
                f"  Did you mean: --input {_prompt_val}",
            )

    # [FLOW:CLI] GAP-11-07: Warn when --frames not specified and source is wide.
    # Placed in CLI layer (not pipeline) so the user sees it before pipeline runs.
    _raw_frames = getattr(args, "frames", None)
    _input_path = getattr(args, "input", None)
    if not _raw_frames and _input_path:
        try:
            from PIL import Image as _Img
            with _Img.open(_input_path) as _im:
                if _im.width > _im.height * 1.5:
                    print(
                        "Warning: --frames not specified and source image "
                        "is wide. If your sheet has multiple animation "
                        "frames, add --frames.",
                        file=sys.stderr,
                    )
        except Exception:
            pass

    # [FLOW:CLI] Early-exit: --tui launches the plain console wizard.
    if getattr(args, "tui", False):
        from scripts.pipeline.console_tui import run_console_tui
        run_console_tui()
        sys.exit(0)

    # [FLOW:CLI] Early-exit: --tui-textual launches the rich Textual TUI.
    if getattr(args, "tui_textual", False):
        from scripts.pipeline.tui import run_tui
        run_tui()
        sys.exit(0)

    # [FLOW:CLI] Early-exit: --serve starts the Flask HTTP API server.
    if getattr(args, "serve", False):
        from scripts.pipeline.web_api import create_app
        host = getattr(args, "host", "127.0.0.1")
        port = getattr(args, "port", 5000)
        app = create_app()
        print(f"Starting asset pipeline API on http://{host}:{port}/", file=sys.stderr)
        app.run(host=host, port=port, debug=False, use_reloader=False)
        sys.exit(0)

    # [FLOW:CLI] Early-exit: --list-templates prints and returns without
    # entering any pipeline mode.
    if args.list_templates:
        templates_dir = Path("scripts/pipeline/templates")
        if not templates_dir.exists():
            print(f"Templates directory not found: {templates_dir}", file=sys.stderr)
            sys.exit(2)

        # [FLOW:TEMPLATE] Group templates by type from JSON files.
        # WHY: We read raw JSON here instead of using get_templates_by_type()
        # because --list-templates needs grouping by type key (dict of lists)
        # while get_templates_by_type returns a flat list.
        templates_by_type = {}
        for template_file in templates_dir.glob("*.json"):
            try:
                with open(template_file, "r") as f:
                    data = json.load(f)
                template_type = data.get("type", "custom")
                template_name = data.get("name", template_file.stem)
                if template_type not in templates_by_type:
                    templates_by_type[template_type] = []
                templates_by_type[template_type].append(template_name)
            except Exception as e:
                logger.warning(f"Failed to read {template_file}: {e}")

        # Print grouped templates
        for template_type in sorted(templates_by_type.keys()):
            print(f"\n{template_type.capitalize()}:")
            for name in sorted(templates_by_type[template_type]):
                print(f"  - {name}")

        return

    # [FLOW:CLI] Early-exit: --describe-template prints one template's details.
    # TODO(PIPELINE-FIX): This looks up by filename (describe_template + ".json"),
    # but --template and load_template_if_needed look up by JSON "name" field.
    # A template named "idle_walk" in file "character_idle_walk.json" would be
    # found by --template idle_walk but NOT by --describe-template idle_walk.
    # Unify to always search by JSON "name" field.
    if args.describe_template:
        templates_dir = Path("scripts/pipeline/templates")
        template_path = templates_dir / f"{args.describe_template}.json"

        if not template_path.exists():
            exit_with_code(2, f"Template not found: {args.describe_template}")

        try:
            with open(template_path, "r") as f:
                data = json.load(f)

            print(f"\nTemplate: {data.get('name', args.describe_template)}")
            print(f"Type: {data.get('type', 'custom')}")
            print(f"Description: {data.get('description', 'No description')}")
            print(f"Angles: {data.get('angles', 'N/A')}")
            print(f"Frames: {data.get('frames', 'N/A')}")
            print(f"Layout: {data.get('layout', 'N/A')}")

            if "processing" in data:
                print("\nProcessing Settings:")
                for key, val in data["processing"].items():
                    print(f"  - {key}: {val}")

        except Exception as e:
            exit_with_code(2, f"Failed to describe template: {e}")

        return

    # [FLOW:CLI] [PIPELINE:PROCESS] Early-exit: direct image-to-glyph conversion
    # bypasses the full pipeline.  Used for quick one-off testing of the processor.
    if args.process_rgb_to_cp437:
        if not args.input:
            exit_with_code(1, "--input is required when using --process-rgb-to-cp437")

        processor = ImageProcessor()
        for grid_x, grid_y, glyph_idx, color_idx in processor.process_image(args.input):
            print(f"({grid_x},{grid_y}): glyph={glyph_idx}, color={color_idx}")
        return

    # [FLOW:CLI] Early-exit: publish staging sprite to assets/sprites/ directory
    if args.publish:
        result = publish_sprite(args.publish, force=args.force)
        if result["success"]:
            sys.exit(0)
        else:
            for err in result["errors"]:
                print(f"Error: {err}", file=sys.stderr)
            sys.exit(2 if "Validation" in str(result["errors"]) else 3)

    # [FLOW:CLI] Early-exit: --analyze inspects image and suggests parameters.
    if args.analyze:
        from scripts.pipeline.service.asset_service import AssetService

        img_path = Path(args.analyze)
        if not img_path.exists():
            exit_with_code(3, f"Image not found: {args.analyze}")

        svc = AssetService()
        hints = {}
        if args.angles:
            hints["angles"] = args.angles

        result = svc.analyze(str(img_path), hints=hints)
        w, h = result["dimensions"]
        sa = result["suggested_angles"]
        sc = result["suggested_cols"]
        sr = result["suggested_rows"]
        cw = result["suggested_cell_w"]
        ch = result["suggested_cell_h"]
        sf = result["suggested_frames"]
        sp = result["suggested_projs"]
        bg = result["detected_background"]

        print(f"\nImage: {w}x{h} px ({w // 12}x{h // 12} cells at 12px/cell)")
        print(f"\nSuggested layout:")
        print(f"  angles: {sa} ({sr} rows of {ch}px)")
        print(f"  cols: {sc} ({sc} columns of {cw}px)")
        print(f"  cell_size: {cw}x{ch} px")
        print(f"  frames: {sf}")
        print(f"  projs: {sp}")
        print(f"  background: {bg}")

        # Grid diagnostics section
        grid_diag = result.get("grid_diagnostics", {})
        if grid_diag:
            method = grid_diag.get("method", "unknown")
            divisible = grid_diag.get("divisible", False)
            confidence = grid_diag.get("confidence", "unknown")
            rx = grid_diag.get("remainder_x", 0)
            ry = grid_diag.get("remainder_y", 0)
            print(f"\nGrid analysis:")
            print(f"  inference mode: {method}")
            print(f"  divisibility: {'OK' if divisible else 'FAILED'} "
                  f"(confidence: {confidence})")
            if not divisible:
                print(f"  remainder: {rx}px x {ry}px")
                diag_error = grid_diag.get("error")
                if diag_error:
                    print(f"  issue: {diag_error}")

        if result["warnings"]:
            print(f"\nWarnings:")
            for warn in result["warnings"]:
                print(f"  - {warn}")

        # Suggested explicit flags -- shown whenever inference is non-standard
        suggested_flags = result.get("suggested_flags", [])
        if suggested_flags:
            print(f"\nSuggested explicit flags (override inference):")
            print(f"  {' '.join(suggested_flags)}")
        elif grid_diag.get("confidence") == "high":
            print(f"\nGrid inference: high confidence (no overrides needed)")

        # Low-confidence advisory
        if grid_diag.get("confidence") == "failed":
            print(f"\nAction required: grid inference failed.")
            print(f"  Specify --cell-w/--cell-h or --cols/--rows explicitly.")

        # Layout suggestions (heuristic analysis)
        layout_suggestions = result.get("layout_suggestions", [])
        if layout_suggestions:
            print(f"\nLayout suggestions:")
            confidence_markers = {"high": "[+]", "medium": "[~]", "low": "[?]"}
            for suggestion in layout_suggestions:
                marker = confidence_markers.get(suggestion["confidence"], "[?]")
                print(
                    f"  {marker} {suggestion['label']} "
                    f"(--order {suggestion['order']}): "
                    f"{suggestion['rationale']}"
                )

        # Determine if a high-confidence non-default order should be suggested
        suggested_order_flag = ""
        for suggestion in layout_suggestions:
            if (
                suggestion["confidence"] == "high"
                and suggestion["order"] != "angle_major"
            ):
                suggested_order_flag = f" --order {suggestion['order']}"
                break

        print(f"\nTo convert:")
        frames_str = " ".join(str(f) for f in sf)
        print(f"  python -m scripts.pipeline.cli --name <name> \\")
        print(f"    --angles {sa} --frames {frames_str} \\")
        if cw != 12 or ch != 12:
            print(f"    --cell-w {cw} --cell-h {ch} \\")
        print(f"    --cols {sc} --rows {sr} \\")
        print(f"    --bg-mode {bg}{suggested_order_flag} --input {args.analyze}")
        return

    # [FLOW:CLI] [FLOW:EXPORT] Early-exit: --render exports .xp to PNG.
    if args.render:
        from scripts.pipeline.export_service import export_xp_to_png

        xp_path = Path(args.render)
        if not xp_path.exists():
            exit_with_code(3, f"XP file not found: {args.render}")

        render_scale = getattr(args, "render_scale", 1)
        try:
            img = export_xp_to_png(
                str(xp_path),
                scale=render_scale,
            )
        except (ValueError, FileNotFoundError) as exc:
            exit_with_code(3, f"Render failed: {exc}")

        out_path = getattr(args, "render_output", None)
        if not out_path:
            out_path = str(xp_path.with_suffix(".png"))

        img.save(out_path, format="PNG")
        print(f"Rendered {xp_path.name} -> {out_path} ({img.width}x{img.height}px, scale={render_scale})")
        return

    # [FLOW:CLI] Early-exit: --batch-manifest processes a multi-file manifest.
    if args.batch_manifest:
        from scripts.pipeline.service.asset_service import AssetService
        from scripts.pipeline.service.manifest import load_manifest, run_manifest

        manifest_path = Path(args.batch_manifest)
        if not manifest_path.exists():
            exit_with_code(3, f"Manifest not found: {args.batch_manifest}")

        manifest = load_manifest(manifest_path)
        svc = AssetService()
        results = run_manifest(manifest, svc)

        succeeded = sum(1 for r in results if r is not None)
        failed = sum(1 for r in results if r is None)
        print(f"\nBatch complete: {succeeded} succeeded, {failed} failed")
        sys.exit(0 if failed == 0 else 3)

    # Validate --downscale argument if provided
    if args.downscale and args.downscale not in DOWNSCALE_ALGORITHMS:
        exit_with_code(
            1,
            f"Invalid algorithm '{args.downscale}'. "
            f"Available: {', '.join(DOWNSCALE_ALGORITHMS)}",
        )

    # [FLOW:CLI] Detect which of the three main code paths to follow.
    # WHY: Mode detection is separate from argparse because "raw" mode is implicit
    # (no flag needed -- just supply the core args).  argparse can't express
    # "if these three args are all present, treat it as a mode."
    mode = detect_cli_mode(args)

    # ================================================================
    # [FLOW:CLI] Import-mode path: routes through ImportRequest when
    # --import-mode is set, bypassing manual AssetDef construction.
    # As-is mode always requires explicit --angles/--frames (no inference).
    # ================================================================
    _import_mode = getattr(args, "import_mode", None)
    if _import_mode is not None and mode in ("raw", None):
        # Build ImportRequest from validated CLI args
        from scripts.pipeline.service.adapters import (
            ImportRequest,
            build_job_from_import_request,
            _build_slice_spec_from_args,
            _build_background_spec_from_args,
        )
        from scripts.pipeline.service.asset_service import AssetService

        # All validation (require_flag, frames parse, spec construction)
        # is inside one try/except ConfigError so any validation failure
        # exits with code 1 and an actionable message.
        try:
            require_flag(
                getattr(args, "name", None),
                "--name",
                "Output sprite name is required for import mode.",
                example="--name my_sprite",
            )
            require_flag(
                getattr(args, "input", None),
                "--input",
                "Source PNG path is required for import mode.",
                example="--input sheet.png",
            )
            require_flag(
                getattr(args, "angles", None),
                "--angles",
                "Number of rotation angles (no inference in import mode).",
                example="--angles 8",
            )
            require_flag(
                getattr(args, "frames", None),
                "--frames",
                "Frame counts per animation (no inference in import mode).",
                example='--frames "1,8"',
            )

            # Parse frames string to list
            _frames_raw = getattr(args, "frames", "1")
            try:
                _frames_list = [int(x.strip()) for x in _frames_raw.split(",")]
            except (ValueError, AttributeError) as exc:
                raise ConfigError(
                    f"Invalid --frames value: {_frames_raw!r}\n"
                    f"  Expected comma-separated integers (e.g. \"1,8\").\n"
                    f"  Each value is the frame count for one animation."
                ) from exc

            # Build slice_spec and background from CLI flags (same helpers
            # used by the raw-mode path in create_job_from_cli_args).
            _slice_spec = _build_slice_spec_from_args(args)
            _background = _build_background_spec_from_args(args)
            _explicit_projs = getattr(args, "projs", None)
            if _explicit_projs is None:
                _explicit_projs = getattr(args, "_reformat_projs", None)

            _source_projs = getattr(args, "source_projs", None)
            if _source_projs is None:
                _source_projs = 1  # fail-closed default for file imports

            _request = ImportRequest(
                name=args.name,
                source_path=args.input,
                frames=_frames_list,
                source_type=getattr(args, "source_type", "file") or "file",
                angles=args.angles,
                import_mode=_import_mode,
                reflection_policy=getattr(args, "reflection_policy", None) or "generate",
                source_projs=_source_projs,
                asset_type=getattr(args, "type", "custom") or "custom",
                explicit_projs=_explicit_projs,
                slice_spec=_slice_spec,
                background=_background,
                synthesize_angles=getattr(args, "synthesize_angles", None),
            )
        except ConfigError as exc:
            exit_with_code(1, f"Error: {exc}")

        _job = build_job_from_import_request(_request)
        _svc = AssetService()

        # Dry-run: validate and exit
        if getattr(args, "dry_run", False):
            print("\nImport mode validation passed (--dry-run active, skipping pipeline)")
            sys.exit(0)

        try:
            _output = _svc.run(_job)
            output_path = str(_output.xp_path)
            print("\nImport mode pipeline complete!")

            if is_interactive:
                _show_preview_options(output_path)
            else:
                print(f"Output: {output_path}")

            if _json_output:
                _json_result = {
                    "status": "success",
                    "output_path": output_path,
                    "warnings": [],
                    "errors": [],
                    "trace_id": getattr(_output, "trace_id", None),
                }
                sys.stdout = _original_stdout
                print(json.dumps(_json_result))

        except Exception as e:
            code = classify_error(e)
            if _json_output:
                _json_result = {
                    "status": "error",
                    "output_path": None,
                    "warnings": [],
                    "errors": [str(e)],
                    "trace_id": None,
                }
                sys.stdout = _original_stdout
                print(json.dumps(_json_result))
            else:
                print(f"Error: {e}", file=sys.stderr)
                import traceback
                traceback.print_exc()
            sys.exit(code)

        return

    if mode is None:
        if not is_interactive:
            exit_with_code(
                1,
                "Error: No mode detected in non-interactive mode.\n"
                "Required flags for raw mode: --name <name> --angles <N> --input <file>\n"
                "Alternatives:\n"
                "  --template <name>  Load a template by name\n"
                "  --import-mode as_is --name <name> --angles <N> --frames <F> --input <file>\n"
                "  --batch-manifest manifest.json  Process batch manifest\n"
                "  --tui              Launch console wizard (no extra deps)\n"
                "  --tui-textual      Launch rich Textual TUI (requires 'textual')\n"
                "  Remove --non-interactive to use the interactive wizard.",
            )
        exit_with_code(
            1,
            "Error: No mode specified. Use --wizard, --template <name>, --tui, --tui-textual, or provide all core args (--name, --angles, --input)",
        )

    # Initialize template variable (may be set by template/wizard modes)
    template = None

    # [FLOW:CLI] [FLOW:TEMPLATE] Template mode: load JSON template, merge with
    # CLI overrides via resolve_field_config, build AssetDef.
    if mode == "template":
        template = load_template_if_needed(mode, args.template)
        if not template:
            exit_with_code(2, f"Failed to load template: {args.template}")

        config = resolve_field_config(args, template)

        # [FLOW:TEMPLATE] Build AssetDef from merged config.
        # WHY: downscale is NOT stored in AssetDef because it's a processing
        # parameter (how to resize), not a structural parameter (what to resize to).
        # It's passed separately to pipeline.run(algorithm=...).
        asset_kwargs = {
            "name": config.get("name") or template.name or "template_asset",
            "type": getattr(template, "type", "custom") or "custom",
            "angles": config.get("angles") or getattr(template, "angles", 1) or 1,
            "source_type": getattr(template, "source_type", "file") or "file",
            "transparency": config.get("transparency", False),
            "normalization": config.get("normalization", False),
            "target_cells_high": config.get("target_cells_high", 0),
        }

        # Add frames if present in template
        if hasattr(template, "frames") and template.frames:
            asset_kwargs["frames"] = template.frames

        asset = AssetDef(**asset_kwargs)

    # ============================================================
    # WIZARD MODE - Interactive flow with WizardResult integration
    # ============================================================
    elif mode == "wizard":
        # [FLOW:CLI] Wizard requires interactive mode.  In non-interactive
        # mode (--non-interactive or piped stdin) exit with helpful message.
        if not is_interactive:
            exit_with_code(
                1,
                "Error: --wizard requires interactive mode.\n"
                "Use --template <name> with explicit flags for non-interactive execution,\n"
                "or pass --interactive to force prompts.",
            )

        # Run wizard to collect user input
        wizard_result = run_wizard_mode()

        # Initialize error handler for consistent formatting
        _wiz = _ensure_wizard()
        error_handler = _wiz["WizardErrorHandler"]()

        # Handle wizard cancellation or error
        if not wizard_result.success:
            if wizard_result.errors:
                for err in wizard_result.errors:
                    error_handler.print_error('WIZARD', err)
            sys.exit(0)  # Clean exit on cancellation/error

        # ============================================================
        # [FLOW:AI-BATCH] Wizard-driven ai_batch dispatch
        # ============================================================
        # WHY: When the wizard collected ai_batch source type, we bypass the
        # normal pipeline and delegate to nanobanana_batch.run_batch() directly.
        # AD-6: --wizard takes precedence over --ai-batch (this block only runs
        # in wizard mode, the CLI-driven --ai-batch block is below).
        if wizard_result.source_type == "ai_batch" or (
            hasattr(wizard_result, "ai_provider") and wizard_result.ai_provider
        ):
            from scripts.pipeline.nanobanana_batch import run_batch

            report = run_batch(
                manifest_path=str(wizard_result.input_path),  # INPUT_PATH = manifest
                prompt_pack_path=str(wizard_result.ai_prompt_pack_path),
                output_dir=str(
                    wizard_result.ai_output_dir or Path("output/ai_batch")
                ),
                provider_name=wizard_result.ai_provider or "stub",
                do_reformat=True,
                emit_xp=True,
                asset_name=(
                    wizard_result.template.name
                    if wizard_result.template
                    else "wizard_ai_asset"
                ),
            )

            print(f"\n[AI-BATCH] Generation complete!")
            print(f"  Total frames: {report.total_frames}")
            print(f"  Completed: {report.completed_frames}")
            if report.failed_frames > 0:
                print(f"  Failed: {report.failed_frames}")
            if report.xp_output_path:
                print(f"  XP output: {report.xp_output_path}")
            sys.exit(0)

        # ============================================================
        # FIELD EXTRACTION - Populate args namespace from WizardResult
        # ============================================================
        # The existing pipeline expects values in an args namespace.
        # We extract from WizardResult and set on args for compatibility.

        # Extract template
        template = wizard_result.template

        # Extract and convert paths
        input_path = str(wizard_result.input_path) if wizard_result.input_path else None
        blend_file_path = str(wizard_result.blend_file_path) if wizard_result.blend_file_path else None

        # Extract source type
        source_type = wizard_result.source_type

        # Handle blender source type - use blend_file_path as source, keep object name separate
        if source_type == "blender":
            # source_path is the .blend file
            if blend_file_path:
                input_path = blend_file_path
            # blender_object is passed separately to AssetDef (handled below)

        # ============================================================
        # POST-VALIDATION: Check input matches template constraints
        # ============================================================
        # internal design notes: "Post-validate: input matches template constraints (before pipeline execution)"
        # BLOCKING BY DEFAULT: Validation errors stop pipeline unless user explicitly proceeds

        if template and input_path and source_type != "blender":
            input_path_obj = Path(input_path)
            if input_path_obj.exists():
                _post_wiz = _ensure_wizard()
                _validate_input = _post_wiz["validate_input_against_template"]
                _format_fixes = _post_wiz["format_validation_errors_with_fixes"]

                is_valid, errors, warnings, auto_fixes = _validate_input(
                    input_path_obj, template
                )

                # Print warnings (non-blocking)
                for warning in warnings:
                    error_handler.print_message('warning', warning)

                # Handle validation errors - BLOCKING BY DEFAULT
                if not is_valid:
                    error_handler.print_error('VALIDATION', "Input does not match template constraints")
                    print(_format_fixes(errors, auto_fixes))

                    # Build choice list with auto-fixes + standard options
                    fix_choices = [
                        {"name": fix['description'], "value": f"fix_{i}"}
                        for i, fix in enumerate(auto_fixes)
                    ]
                    fix_choices.extend([
                        {"name": "Ask Claude", "value": "claude"},
                        {"name": "Proceed anyway (may fail)", "value": "proceed"},
                        {"name": "Cancel", "value": "cancel"},
                    ])

                    q = _ensure_questionary()
                    choice = q.select(
                        "How would you like to proceed?",
                        choices=fix_choices
                    ).ask()

                    # BLOCKING: Exit unless user explicitly chooses to proceed
                    if choice == "cancel" or choice is None:
                        error_handler.print_message('info', "Pipeline cancelled due to validation errors.")
                        sys.exit(1)  # Exit with error code
                    elif choice == "claude":
                        error_handler.print_message('info', "Open Claude Code and describe the validation issue.")
                        error_handler.print_message('info', f"Input: {input_path}")
                        error_handler.print_message('info', f"Template: {template.name}")
                        error_handler.print_message('info', f"Errors: {errors}")
                        sys.exit(1)  # Exit - user needs to resolve with Claude
                    elif choice == "proceed":
                        # User explicitly chose to proceed despite errors
                        error_handler.print_message('warning', "Proceeding despite validation errors - pipeline may fail.")
                        error_handler.log_to_file('warning', f"User chose to proceed despite validation errors: {errors}")
                    elif choice.startswith("fix_"):
                        # Auto-fix selected - implement fix logic here
                        fix_index = int(choice.replace("fix_", ""))
                        if fix_index < len(auto_fixes):
                            fix = auto_fixes[fix_index]
                            error_handler.print_message('info', f"Applying fix: {fix['description']}")
                            error_handler.log_to_file('info', f"Auto-fix applied: {fix}")
                            # TODO: Implement actual fix logic based on fix['option']
                            # For now, continue with warning
                            error_handler.print_message('warning', f"Fix '{fix['option']}' not yet implemented - proceeding with original input")

        # ============================================================
        # ARGS NAMESPACE POPULATION - For resolve_field_config()
        # ============================================================

        args.input = input_path
        args.blender_object = wizard_result.blender_object
        args.template = template.name if template else None
        args.source_type = source_type
        args.blend_file = blend_file_path

        # ============================================================
        # PIPELINE ENTRY - Build AssetDef from wizard result
        # ============================================================

        # Template selected from wizard - create AssetDef
        config = resolve_field_config(args, template)

        # Create AssetDef from template + resolved config
        # Priority: wizard configure > template > schema defaults (BLEND-15-02)
        wiz_cfg = wizard_result.configure or {}

        asset_kwargs = {
            "name": config.get("name") or (template.name if template else "wizard_asset"),
            "type": (getattr(template, "type", "custom") if template else "custom") or "custom",
            "angles": wiz_cfg.get("angles") or config.get("angles") or (getattr(template, "angles", 1) if template else 1) or 1,
            "source_type": source_type or "file",
            "source_path": blend_file_path if source_type == "blender" else input_path,
            "mesh_source_path": input_path if source_type == "mesh" else None,
            "transparency": config.get("transparency", False),
            "normalization": config.get("normalization", False),
            "target_cells_high": config.get("target_cells_high", 0),
            "render_resolution": wiz_cfg.get("render_resolution", 24),
        }

        # Frames: wizard > template > default
        wiz_frames_str = wiz_cfg.get("frames")
        if wiz_frames_str:
            asset_kwargs["frames"] = [int(x.strip()) for x in wiz_frames_str.split(",") if x.strip()]
        elif template and hasattr(template, "frames") and template.frames:
            asset_kwargs["frames"] = template.frames

        # Add blender_object if source type is blender
        if source_type == "blender":
            asset_kwargs["blender_object"] = args.blender_object

        # Wire keyframe ranges from wizard configure dict (BLEND-15-03)
        wiz_kr = wiz_cfg.get("keyframe_ranges")
        if wiz_kr:
            asset_kwargs["keyframe_ranges"] = wiz_kr

        asset = AssetDef(**asset_kwargs)

        # Wire wizard CONFIGURE values to args for create_job_from_cli_args (BLEND-15-02)
        # This ensures cell_size and bg_mode flow through the adapter into AssetJobConfig
        if wiz_cfg:
            wiz_cell = wiz_cfg.get("cell_size")
            if wiz_cell:
                args.cell_w = wiz_cell
                args.cell_h = wiz_cell
            wiz_bg = wiz_cfg.get("bg_mode")
            if wiz_bg:
                args.bg_mode = wiz_bg
            # render_resolution already set on asset via asset_kwargs
            # Wire keyframe_ranges for adapter (BLEND-15-03)
            if wiz_kr:
                args._parsed_keyframe_ranges = wiz_kr

    # [FLOW:CLI] Raw mode: no template, no wizard.  Build AssetDef directly from
    # CLI args.  Starts with a preset base (character/item) or a blank custom
    # slate, then overlays every CLI flag that was explicitly provided.
    elif mode == "raw":
        # 1. Start with base
        if args.type == "custom":
            # WHY: "custom" starts from a minimal AssetDef rather than a preset,
            # because presets enforce opinionated defaults (e.g. 8 angles for
            # characters) that would silently override the user's CLI args.
            asset = AssetDef(
                name="custom_asset", type="custom", angles=1, source_type="file"
            )
        else:
            # Load preset
            preset = get_preset(args.type)
            if not preset:
                exit_with_code(1, f"Error: Unknown preset {args.type}")
            # Create a copy
            asset = AssetDef(**asdict(preset))

        # 2. Override with CLI args
        if args.prompt:
            asset.prompt = args.prompt
            if not args.name:
                 asset.name = args.prompt.replace(" ", "_").lower()
        
        # Override name
        if args.name:
            asset.name = args.name

        # Override angles
        if args.angles:
            asset.angles = args.angles

        # Override frames (parse string "1,8" -> [1, 8])
        if args.frames:
            try:
                frames_list = [int(x.strip()) for x in args.frames.split(',')]
                asset.frames = frames_list
            except ValueError:
                exit_with_code(1, f"Invalid frames format '{args.frames}'")

        # Override size (parse string "12,12" -> (12, 12))
        if args.size:
            try:
                size_parts = [int(x.strip()) for x in args.size.split(',')]
                if len(size_parts) == 2:
                    asset.size = (size_parts[0], size_parts[1])
                else:
                    exit_with_code(1, f"Invalid size format '{args.size}'. Expected 'W,H'")
            except ValueError:
                exit_with_code(1, f"Invalid size format '{args.size}'")

        # Set normalization and target_cells_high
        if args.normalization:
            asset.normalization = True
        
        if args.target_cells_high:
            asset.target_cells_high = args.target_cells_high
            
        # Set transparency
        if args.transparency:
            asset.transparency = True

        # [FLOW:CLI] Second override pass (raw mode).
        # TODO(PIPELINE-FIX): The block below duplicates the overrides already
        # applied above (args.name at line ~880, args.angles at line ~884,
        # args.transparency at line ~903).  These double-writes are harmless
        # but indicate the raw-mode override logic was extended without cleanup.
        # Consolidate into a single pass.
        if args.name:
            asset.name = args.name

        if args.angles:
            asset.angles = args.angles

        if args.source_type:
            asset.source_type = args.source_type

        if args.input:
            asset.source_path = args.input

        # WHY: For Blender source, the blend file path goes into source_path
        # so the generator knows which .blend file to open.
        if hasattr(args, 'blend_file') and args.blend_file:
            asset.source_path = args.blend_file

        if args.transparency:
            asset.transparency = True

        if args.blender_object:
            asset.blender_object = args.blender_object

        # Wire keyframe ranges from CLI flag (BLEND-15-03)
        kr_raw = getattr(args, "keyframe_ranges", None)
        if kr_raw:
            parsed_kr = parse_keyframe_ranges(kr_raw)
            asset.keyframe_ranges = parsed_kr
            # Stash on args for adapter to pick up
            args._parsed_keyframe_ranges = parsed_kr

    # TODO(PIPELINE-FIX): This branch is dead code.  mode=None already triggers
    # exit_with_code(1) above (line ~790).  If the wizard cancelled, it exits
    # with code 0 inside the wizard branch.  This block can be safely removed.
    if mode is None:
        pass  # Asset already created by wizard

    # WHY: This is a defensive guard against code paths that fail to create an
    # AssetDef (e.g. a new mode added without building `asset`).  Using
    # `"asset" not in locals()` instead of try/except avoids masking real errors.
    # TODO(PIPELINE-FIX): Replace this runtime locals() check with a type-safe
    # Optional[AssetDef] initialized to None at the top of main().
    if "asset" not in locals():
        exit_with_code(
            1,
            "No asset configuration loaded. Provide --template, --wizard, or core raw arguments.",
        )

    # [FLOW:CLI] Create staging directories (staging/png/, staging/xp/, etc.)
    # before validation or pipeline run, so file-existence checks don't fail.
    # WHY: Lazy import -- staging.py pulls in filesystem helpers that are only
    # needed if we actually reach the pipeline execution path (not early-exit).
    from scripts.pipeline.staging import ensure_staging_structure, STAGING_DIR

    ensure_staging_structure()

    # ================================================================
    # [FLOW:AI-BATCH] AI batch generation mode (CLI-driven)
    # ================================================================
    # WHY: When --ai-batch is set via CLI (not wizard), dispatch to
    # nanobanana_batch.run_batch() and exit early.  This block only runs
    # when mode is NOT "wizard" (wizard dispatch happens above).
    # AD-6: --wizard takes precedence because detect_cli_mode() checks
    # args.wizard first and returns "wizard", so this block is unreachable
    # in wizard mode.
    if getattr(args, "ai_batch", False):
        from scripts.pipeline.nanobanana_batch import run_batch

        manifest_path = getattr(args, "manifest", None)
        prompt_pack_path = getattr(args, "prompt_pack", None)

        if not manifest_path:
            exit_with_code(1, "--manifest is required when using --ai-batch")
        if not prompt_pack_path:
            exit_with_code(1, "--prompt-pack is required when using --ai-batch")

        verify_stages = None
        if getattr(args, "verify_stages", None):
            verify_stages = set(args.verify_stages.upper().split(","))

        output_dir = STAGING_DIR / "ai_batch" / asset.name

        report = run_batch(
            manifest_path=manifest_path,
            prompt_pack_path=prompt_pack_path,
            output_dir=str(output_dir),
            provider_name=getattr(args, "ai_provider", "stub"),
            seed=getattr(args, "ai_seed", None),
            snap_magenta=getattr(args, "snap_magenta", False),
            verify_stages=verify_stages,
            do_reformat=getattr(args, "reformat", None) is not None or True,
            emit_xp=getattr(args, "emit_xp", False),
            asset_name=asset.name,
        )

        print(f"\n[AI-BATCH] Generation complete!")
        print(f"  Total: {report.total_frames}, Completed: {report.completed_frames}, Failed: {report.failed_frames}")
        if report.xp_output_path:
            print(f"  XP: {report.xp_output_path}")
        print(f"  Report: {output_dir / 'run_report.json'}")
        sys.exit(0)

    # ================================================================
    # [FLOW:REFORMAT] Pre-pipeline reformatter stage
    # ================================================================
    # WHY: When --reformat is set, we assemble individual frame PNGs into a
    # sprite sheet BEFORE the pipeline runs. This overrides source_path,
    # disables normalization (reformatter handles sizing), and sets
    # projs from the reformatter result.
    if getattr(args, "reformat", None):
        from scripts.pipeline.reformatter import run_reformatter

        reformat_dir = Path(args.reformat)
        reformat_output = STAGING_DIR / "sheets" / f"{asset.name}_reformatted.png"

        frames_list = asset.frames if isinstance(asset.frames, list) else [asset.frames]
        do_alpha = not getattr(args, "no_alpha_to_magenta", False)

        print(f"[REFORMAT] Assembling frames from {reformat_dir}...")
        reformat_result = run_reformatter(
            input_dir=reformat_dir,
            output=reformat_output,
            angles=asset.angles,
            frames=frames_list,
            target_cells_high=getattr(args, "target_cells_high", 8) or 8,
            reflection_dim=getattr(args, "reflection_dim", 0.5),
            reflection_policy=getattr(args, "reflection_policy", None) or "generate",
            alpha_to_magenta=do_alpha,
            alpha_threshold=128,
        )

        # Override asset source to point at the reformatted sheet
        asset.source_path = str(reformat_result.output_path)
        # Disable normalization -- reformatter already handled sizing
        asset.normalization = False
        # Set projs from reformatter result (2 when reflections baked)
        asset.projs = reformat_result.projs
        # When reflections are baked (projs=2), double frames for the slicer.
        # Mark that frame counts now include projection multiplication.
        frames_include_projs = reformat_result.projs > 1
        setattr(asset, "frames_include_projs", frames_include_projs)
        if reformat_result.projs > 1:
            asset.frames = [f * reformat_result.projs for f in reformat_result.frames]

        # Sync reformat results to args so create_job_from_cli_args() sees them
        args.input = str(reformat_result.output_path)
        args.normalization = False
        args._reformat_projs = reformat_result.projs
        args._frames_include_projs = bool(frames_include_projs)
        if reformat_result.projs > 1:
            doubled = [f * reformat_result.projs for f in reformat_result.frames]
            args.frames = ",".join(str(f) for f in doubled)

        print(f"[REFORMAT] Sheet: {reformat_result.sheet_width}x{reformat_result.sheet_height}")
        print(f"[REFORMAT] Projs: {reformat_result.projs}, Reflections: {reformat_result.reflections_applied}")

        for w in reformat_result.warnings:
            print(f"[REFORMAT] Warning: {w}")

        # Write metadata if requested
        if getattr(args, "write_meta", None):
            import json as _json
            meta = {
                "output_path": str(reformat_result.output_path),
                "sheet_width": reformat_result.sheet_width,
                "sheet_height": reformat_result.sheet_height,
                "projs": reformat_result.projs,
                "angles": reformat_result.angles,
                "frames": reformat_result.frames,
                "reflections_applied": reformat_result.reflections_applied,
            }
            with open(args.write_meta, "w") as _f:
                _json.dump(meta, _f, indent=2)

    # Validation: source_type=blender requires blender_object
    if getattr(asset, "source_type", "file") == "blender" and not getattr(
        asset, "blender_object", None
    ):
        exit_with_code(
            1, "--blender-object is required when using --source-type blender"
        )

    # 3. Validate
    errors = asset.validate()
    if errors:
        print("Validation Errors:", file=sys.stderr)
        for e in errors:
            print(f"- {e}", file=sys.stderr)
        sys.exit(1)

    # 4. Run pipeline (skip if --dry-run)
    if args.dry_run:
        print("\nValidation passed (--dry-run active, skipping rendering)")
        sys.exit(0)

    try:
        # [FLOW:CLI] -> AssetService.run() -> [PIPELINE:GENERATE -> SLICE -> PROCESS -> ASSEMBLE]
        # WHY: Route through AssetService so SlicingSpec/BackgroundSpec flow
        # from CLI flags all the way to the slicer and assembler.
        from scripts.pipeline.service.adapters import create_job_from_cli_args
        from scripts.pipeline.service.asset_service import AssetService

        # Sync remaining asset fields to args for the adapter
        # (handles template/wizard/raw modes which already populated `asset`)
        if not hasattr(args, "input") or not args.input:
            args.input = getattr(asset, "source_path", None)
        if not hasattr(args, "name") or not args.name:
            args.name = getattr(asset, "name", "unnamed")
        if not hasattr(args, "angles") or not args.angles:
            args.angles = getattr(asset, "angles", 1)
        if not hasattr(args, "source_type") or not args.source_type:
            args.source_type = getattr(asset, "source_type", "file")
        if not hasattr(args, "blender_object") or not args.blender_object:
            args.blender_object = getattr(asset, "blender_object", None)
        if not hasattr(args, "transparency"):
            args.transparency = getattr(asset, "transparency", False)
        if not hasattr(args, "normalization"):
            args.normalization = getattr(asset, "normalization", False)
        if not hasattr(args, "target_cells_high"):
            args.target_cells_high = getattr(asset, "target_cells_high", 0)
        # Sync frames from asset if not already set from reformat or CLI
        if not getattr(args, "_reformat_projs", None):
            asset_frames = getattr(asset, "frames", [1])
            if isinstance(asset_frames, list):
                args.frames = ",".join(str(f) for f in asset_frames)
            else:
                args.frames = str(asset_frames)

        job = create_job_from_cli_args(args, template)
        svc = AssetService()
        output = svc.run(job)
        output_path = str(output.xp_path)
        print("\nPipeline complete!")

        # --- Preview Options (interactive only) ---
        if is_interactive:
            _show_preview_options(output_path)
        else:
            print(f"Output: {output_path}")

        # [FLOW:CLI] JSON mode: emit structured result to original stdout.
        if _json_output:
            _json_result = {
                "status": "success",
                "output_path": output_path,
                "warnings": [],
                "errors": [],
                "trace_id": getattr(output, "trace_id", None),
            }
            sys.stdout = _original_stdout
            print(json.dumps(_json_result))

    except Exception as e:
        code = classify_error(e)

        if _json_output:
            _json_result = {
                "status": "error",
                "output_path": None,
                "warnings": [],
                "errors": [str(e)],
                "trace_id": None,
            }
            sys.stdout = _original_stdout
            print(json.dumps(_json_result))
        else:
            print(f"Error: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()

        sys.exit(code)


# WHY: Guard allows ``python -m asset_gen.cli`` invocation while preventing
# main() from running when the module is imported as a library (e.g. by tests).
if __name__ == "__main__":
    main()
