"""
XP file validation utilities.

Provides validation for XP files, sprite sheet specifications, animation timing metadata,
and palette compatibility checking. Includes a CLI for standalone validation.

ARCHITECTURE:
    This module is the data-contract enforcement layer for the asset pipeline's
    final output -- the ``.xp`` file.  It reads a serialized XP file back from
    disk, verifies structural invariants (layer count, dimension bounds,
    metadata encoding), and optionally cross-checks a sprite-sheet PNG against
    the grid layout the XP claims to describe.

    The validation stages:
      - ``validate_xp``           -- standalone post-write check on any .xp file
      - ``validate_timing``       -- check animation timing sidecar JSON
      - ``validate_palette``      -- check Layer 2+ for off-palette colors (L1 distance)
      - ``validate_all``          -- orchestrate all validation checks
      - ``validate_sheet_specs``  -- pre-assembly check that a PNG matches the
                                     expected column/row grid

CLI USAGE:
    python -m scripts.pipeline.validator assets/sprites/player.xp [--strict] [--verbose]

    Exit codes: 0=clean, 1=errors (or strict+warnings), 2=warnings-only

    Flags:
      --strict      Treat warnings as errors (CI mode)
      --quiet       One-line summary only
      --verbose     Show all checks including info messages
      --color       Control ANSI colors (always|auto|never)
      --palette     Override palette (JSON file path)

KEY EXPORTS:
    - validate_xp:               Validate .xp file structure, metadata, layers
    - validate_timing:           Validate animation timing metadata from sidecar JSON
    - validate_palette:          Validate Layer 2+ palette compatibility (L1 distance)
    - validate_all:              Orchestrate all validation checks
    - validate_sheet_specs:      Validate sprite sheet PNG dimensions vs grid
    - ValidationResult:          Structured validation outcome with severity separation
    - print_validation_result:   Print formatted validation result with severity-aware output
    - print_validation_report:   Pretty-print a validation result dict (legacy)
    - warn_staging_staleness:    Check and warn about stale staging files
    - main:                      CLI entry point

PIPELINE CONTEXT:
    [DATA-CONTRACT:XP]       -- Enforces the invariants documented in xp_core.py:
        angles in {1, 4, 8}, anims list non-empty, frame counts 1..35.
    [DATA-CONTRACT:PALETTE]  -- Validates Layer 2+ colors against PALETTE_RGB using
        L1 (Manhattan) distance with threshold 15.
    [PIPELINE:ASSEMBLE]      -- Called after XPAssembler writes the final .xp to
        confirm the output is well-formed before downstream consumption.
    [DEPENDENCY:XP_CORE]     -- Loads XP files via ``xp_core.XPFile``.
    [DEPENDENCY:PALETTE]     -- Imports PALETTE_RGB for validation.
    [DEPENDENCY:PIL]         -- validate_sheet_specs opens PNGs via Pillow.
    [DEPENDENCY:STAGING]     -- Uses staging module for staleness checks.
"""

from pathlib import Path
from typing import Dict, Any, List, Tuple
from dataclasses import dataclass, field
from collections import Counter
import sys
import os
import json
import argparse

# WHY: xp_core.py is a sibling in asset_gen/. Use relative import for package
# consistency when imported as 'from scripts.pipeline.validator import ...'
# The try/except handles both package import and direct invocation cases.
try:
    from .xp_core import XPFile
except ImportError:
    # Fallback for direct invocation (python validator.py)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from xp_core import XPFile

# Import canonical palette for validation
try:
    from .palette import PALETTE_RGB
except ImportError:
    from palette import PALETTE_RGB

# Import staging utilities for staleness checks
# WHY: Use relative import since staging/ is a sibling directory within asset_gen/
from .staging import check_staging_staleness, print_staleness_warnings


@dataclass
class ValidationResult:
    """Structured validation outcome with severity separation.

    [DATA-CONTRACT:VALIDATION] Used by all validation functions to return
    results with error/warning separation and consistent exit code logic.
    """
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    info: List[str] = field(default_factory=list)  # For notices like "no timing file"

    def is_clean(self) -> bool:
        """No errors or warnings."""
        return self.valid and not self.warnings

    def exit_code(self, strict: bool = False) -> int:
        """Determine exit code: 0=clean, 1=errors, 2=warnings-only.

        Args:
            strict: If True, treat warnings as errors (return 1 instead of 2)
        """
        if not self.valid:
            return 1
        if self.warnings:
            return 2 if not strict else 1
        return 0

    def merge(self, other: 'ValidationResult') -> 'ValidationResult':
        """Combine two results, preserving all messages."""
        return ValidationResult(
            valid=self.valid and other.valid,
            errors=self.errors + other.errors,
            warnings=self.warnings + other.warnings,
            info=self.info + other.info
        )


# ANSI color codes for terminal output
ANSI_RED = "\033[31m"
ANSI_YELLOW = "\033[33m"
ANSI_CYAN = "\033[36m"
ANSI_RESET = "\033[0m"


def should_use_color(color_mode: str = "auto") -> bool:
    """Determine if ANSI color codes should be used.

    Args:
        color_mode: "always", "never", or "auto" (default)

    Returns:
        True if colors should be enabled
    """
    if color_mode == "always":
        return True
    if color_mode == "never":
        return False
    return sys.stdout.isatty()


def format_error(msg: str, use_color: bool) -> str:
    """Format an error message with optional color."""
    if use_color:
        return f"{ANSI_RED}ERROR:{ANSI_RESET} {msg}"
    return f"ERROR: {msg}"


def format_warning(msg: str, use_color: bool) -> str:
    """Format a warning message with optional color."""
    if use_color:
        return f"{ANSI_YELLOW}WARNING:{ANSI_RESET} {msg}"
    return f"WARNING: {msg}"


def format_info(msg: str, use_color: bool) -> str:
    """Format an info message with optional color."""
    if use_color:
        return f"{ANSI_CYAN}INFO:{ANSI_RESET} {msg}"
    return f"INFO: {msg}"


def print_validation_result(
    result: ValidationResult,
    label: str = "Validation",
    quiet: bool = False,
    verbose: bool = False,
    color_mode: str = "auto"
) -> None:
    """Print formatted validation result with severity-aware output.

    Args:
        result: ValidationResult to print
        label: Label for the validation (e.g., "XP File", "Timing")
        quiet: Suppress details, show one-line summary only
        verbose: Show all messages including info
        color_mode: "always", "auto", or "never"
    """
    use_color = should_use_color(color_mode)

    if quiet:
        # One-line summary only
        if result.valid and not result.warnings:
            print(f"{label}: OK")
        elif result.valid:
            print(f"{label}: OK ({len(result.warnings)} warning(s))")
        else:
            print(f"{label}: FAILED ({len(result.errors)} error(s))")
        return

    # Full output
    if result.valid and not result.warnings:
        print(f"[PASS] {label}")
    elif result.valid:
        print(f"[WARN] {label} ({len(result.warnings)} warning(s))")
    else:
        print(f"[FAIL] {label} ({len(result.errors)} error(s))")

    for msg in result.errors:
        print(f"  {format_error(msg, use_color)}")

    for msg in result.warnings:
        print(f"  {format_warning(msg, use_color)}")

    if verbose:
        for msg in result.info:
            print(f"  {format_info(msg, use_color)}")


def load_timing_metadata(xp_path: Path) -> tuple:
    """Load timing metadata from sidecar JSON if present.

    [DATA-CONTRACT:TIMING] Timing metadata is stored in a sidecar file
    {sprite_name}.timing.json next to the .xp file.

    Args:
        xp_path: Path to .xp file (e.g., sprite.xp)

    Returns:
        Tuple of (data: dict or None, error: str or None)
        - If file exists and is valid JSON: (data, None)
        - If file doesn't exist: (None, None) - not an error
        - If file exists but invalid: (None, error_message)
    """
    # Construct sidecar path: sprite.xp -> sprite.timing.json
    timing_path = xp_path.with_suffix("").with_name(
        xp_path.stem + ".timing.json"
    )

    if not timing_path.exists():
        return (None, None)

    try:
        with open(timing_path, "r") as f:
            data = json.load(f)
        return (data, None)
    except json.JSONDecodeError as e:
        return (None, f"Invalid JSON in {timing_path.name}: {e}")
    except IOError as e:
        return (None, f"Could not read {timing_path.name}: {e}")


def validate_timing(
    xp_path: Path,
    xp_metadata: dict = None
) -> ValidationResult:
    """Validate animation timing metadata from sidecar JSON.

    [DATA-CONTRACT:TIMING] Checks:
    - frame_ms values are positive integers in range [10, 5000]
    - loop_mode is one of: "none", "loop", "pingpong"
    - Duration count matches expected frame count (if xp_metadata provided)

    Args:
        xp_path: Path to .xp file (timing sidecar is derived from this)
        xp_metadata: Optional dict with 'anims' key from xp_file.get_metadata()
                    Used to validate frame count matches

    Returns:
        ValidationResult with errors/warnings/info
    """
    errors = []
    warnings = []
    info = []

    # Load sidecar
    timing_data, load_error = load_timing_metadata(xp_path)

    if load_error:
        # File exists but couldn't be parsed - this is an error
        errors.append(load_error)
        return ValidationResult(valid=False, errors=errors, warnings=warnings, info=info)

    if timing_data is None:
        # No sidecar file - info-level notice, not a warning
        info.append(f"No timing metadata ({xp_path.stem}.timing.json not found)")
        return ValidationResult(valid=True, errors=errors, warnings=warnings, info=info)

    # Validate structure
    if not isinstance(timing_data, dict):
        errors.append("Timing metadata must be a JSON object")
        return ValidationResult(valid=False, errors=errors, warnings=warnings, info=info)

    if "animations" not in timing_data:
        errors.append("Timing metadata missing 'animations' field")
        return ValidationResult(valid=False, errors=errors, warnings=warnings, info=info)

    animations = timing_data.get("animations", {})
    if not isinstance(animations, dict):
        errors.append("'animations' must be an object mapping animation names to timing data")
        return ValidationResult(valid=False, errors=errors, warnings=warnings, info=info)

    # Validate each animation's timing
    valid_loop_modes = ["none", "loop", "pingpong"]

    for anim_name, anim_data in animations.items():
        if not isinstance(anim_data, dict):
            errors.append(f"Animation '{anim_name}': timing data must be an object")
            continue

        # Validate frame_ms
        if "frame_ms" not in anim_data:
            errors.append(f"Animation '{anim_name}': missing 'frame_ms' array")
            continue

        frame_ms = anim_data.get("frame_ms", [])
        if not isinstance(frame_ms, list):
            errors.append(f"Animation '{anim_name}': 'frame_ms' must be an array")
            continue

        if len(frame_ms) == 0:
            errors.append(f"Animation '{anim_name}': 'frame_ms' array is empty")
            continue

        # Validate each frame duration
        for idx, duration in enumerate(frame_ms):
            if not isinstance(duration, (int, float)):
                errors.append(
                    f"Animation '{anim_name}' frame {idx}: "
                    f"duration must be numeric, got {type(duration).__name__}"
                )
            elif duration < 10:
                errors.append(
                    f"Animation '{anim_name}' frame {idx}: "
                    f"duration {duration}ms below minimum (10ms)"
                )
            elif duration > 5000:
                errors.append(
                    f"Animation '{anim_name}' frame {idx}: "
                    f"duration {duration}ms above maximum (5000ms)"
                )

        # Validate loop_mode if present
        loop_mode = anim_data.get("loop_mode")
        if loop_mode is not None:
            if loop_mode not in valid_loop_modes:
                errors.append(
                    f"Animation '{anim_name}': "
                    f"invalid loop_mode '{loop_mode}', must be one of {valid_loop_modes}"
                )

    # Cross-check with XP metadata if provided
    if xp_metadata and "anims" in xp_metadata:
        xp_anims = xp_metadata["anims"]
        # Note: We can't match by name since XP metadata only has counts by index
        # Just warn if timing file has different number of animations
        if len(animations) != len(xp_anims):
            warnings.append(
                f"Timing file has {len(animations)} animation(s), "
                f"but XP metadata has {len(xp_anims)} animation(s)"
            )

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        info=info
    )


def compute_min_palette_distance(rgb: tuple, palette: list = None) -> int:
    """Calculate minimum L1 (Manhattan) distance from rgb to any palette color.

    [DATA-CONTRACT:PALETTE] Uses L1 distance for consistency with palette.py's
    is_transparent() function which uses the same metric family.

    Args:
        rgb: (R, G, B) tuple with values 0-255
        palette: List of (R, G, B) tuples (default: PALETTE_RGB)

    Returns:
        Minimum Manhattan distance to nearest palette color
    """
    if palette is None:
        palette = PALETTE_RGB

    r, g, b = rgb
    min_dist = float('inf')

    for pr, pg, pb in palette:
        dist = abs(r - pr) + abs(g - pg) + abs(b - pb)
        if dist < min_dist:
            min_dist = dist
            if dist == 0:  # Early exit on exact match
                return 0

    return int(min_dist)


def load_custom_palette(palette_path: str) -> tuple:
    """Load a custom palette from file.

    Supports two formats:
    - Python module with PALETTE_RGB list
    - JSON array of [R, G, B] arrays

    Args:
        palette_path: Path to palette file (.py or .json)

    Returns:
        Tuple of (palette: list or None, error: str or None)
    """
    path = Path(palette_path)
    if not path.exists():
        return (None, f"Palette file not found: {palette_path}")

    if path.suffix == ".json":
        try:
            with open(path) as f:
                data = json.load(f)
            if not isinstance(data, list):
                return (None, "JSON palette must be an array")
            palette = [tuple(c) for c in data]
            return (palette, None)
        except (json.JSONDecodeError, TypeError) as e:
            return (None, f"Invalid palette JSON: {e}")
    elif path.suffix == ".py":
        # For Python modules, just point to existing palette.py
        return (None, f"Python palette files not supported via --palette flag. Use palette.py directly.")
    else:
        return (None, f"Unsupported palette format: {path.suffix}. Use .json")


def validate_palette(
    xp_file,  # XPFile instance
    palette: list = None,
    threshold: int = 15,
    warn_percentage: float = 0.02,
    xp_path: Path = None  # For error messages
) -> ValidationResult:
    """Validate palette compatibility of Layer 2+ cells.

    [DATA-CONTRACT:PALETTE] Checks visual layers (2+) for off-palette colors.
    Layers 0 (metadata) and 1 (depth map) are skipped by design as they
    use non-palette grays (see assembler.py:181).

    Args:
        xp_file: Loaded XPFile instance
        palette: Custom palette (default: PALETTE_RGB from palette.py)
        threshold: L1 distance threshold (default: 15)
        warn_percentage: Warning if >this fraction off-palette (default: 0.02 = 2%)
        xp_path: Optional path for error messages

    Returns:
        ValidationResult with palette compatibility warnings
    """
    if palette is None:
        palette = PALETTE_RGB

    errors = []
    warnings = []
    info = []

    # Verify we have enough layers
    if len(xp_file.layers) < 3:
        errors.append(
            f"XP file has only {len(xp_file.layers)} layer(s), need at least 3 for palette validation"
        )
        return ValidationResult(valid=False, errors=errors, warnings=warnings, info=info)

    # Count off-palette colors in Layer 2+
    off_palette_colors = Counter()
    total_pixels = 0

    # WHY Layer 2+: Layer 0 is metadata, Layer 1 is depth map.
    # Both use non-palette grays by design (assembler.py:181).
    for layer_idx in range(2, len(xp_file.layers)):
        layer = xp_file.layers[layer_idx]
        for y in range(layer.height):
            for x in range(layer.width):
                _, fg, bg = layer.data[y][x]

                # Check foreground color
                fg_dist = compute_min_palette_distance(fg, palette)
                if fg_dist > threshold:
                    off_palette_colors[fg] += 1

                # Check background color
                bg_dist = compute_min_palette_distance(bg, palette)
                if bg_dist > threshold:
                    off_palette_colors[bg] += 1

                total_pixels += 2  # Count both fg and bg

    # Calculate percentage and warn if exceeded
    off_palette_count = sum(off_palette_colors.values())

    if total_pixels > 0 and off_palette_count > 0:
        off_palette_pct = off_palette_count / total_pixels

        if off_palette_pct > warn_percentage:
            path_str = str(xp_path) if xp_path else "XP file"
            warnings.append(
                f"Palette drift in {path_str}: {off_palette_pct*100:.1f}% of pixels off-palette "
                f"(threshold: L1 distance > {threshold}, limit: {warn_percentage*100:.0f}%)"
            )

            # List top offending colors with their distances
            for color, count in off_palette_colors.most_common(5):
                dist = compute_min_palette_distance(color, palette)
                warnings.append(
                    f"  Off-palette color RGB{color}: {count} occurrence(s), "
                    f"L1 distance: {dist}"
                )

    # Add info about palette check even if clean
    if off_palette_count == 0:
        info.append("All pixels within palette tolerance")
    else:
        pct = (off_palette_count / total_pixels * 100) if total_pixels > 0 else 0
        info.append(f"Checked {total_pixels} color values, {off_palette_count} off-palette ({pct:.1f}%)")

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        info=info
    )


def validate_xp(xp_path: Path) -> Dict[str, Any]:
    """
    Validate an XP file structure and content.

    Args:
        xp_path: Path to .xp file to validate

    Returns:
        Dict with keys:
            - valid: bool indicating if file is valid
            - errors: List of error messages (empty if valid)
            - metadata: Dict with angles, anims extracted from file
            - size: Dict with width, height of layers
    """
    errors: List[str] = []
    metadata = {"angles": 1, "anims": [1]}
    size = {"width": 0, "height": 0}

    # Check file exists
    if not xp_path.exists():
        errors.append(f"File not found: {xp_path}")
        return {"valid": False, "errors": errors, "metadata": metadata, "size": size}

    # Check file extension
    if not xp_path.suffix.lower() == ".xp":
        errors.append(f"Invalid file extension: {xp_path.suffix}. Expected .xp")

    try:
        # Load XP file
        xp_file = XPFile(str(xp_path))

        # Verify layers exist
        if not xp_file.layers:
            errors.append("No layers in XP file")
            return {
                "valid": False,
                "errors": errors,
                "metadata": metadata,
                "size": size,
            }

        # WHY: Layer 0 is the metadata layer in the XP sprite convention.
        # It stores angles at cell (0,0) and per-animation frame counts
        # at cells (1..N, 0), encoded as CP437 glyph indices.
        layer0 = xp_file.layers[0]
        size["width"] = layer0.width
        size["height"] = layer0.height

        # Get metadata from xp_core's get_metadata
        try:
            meta = xp_file.get_metadata()
            if meta:
                metadata["angles"] = meta.get("angles", 1)
                metadata["anims"] = meta.get("anims", [1])

                # WHY: The game engine only supports 1 (front-only), 4 (cardinal),
                # NOTE: Removed [1,4,8] hard gate per Phase 2. Engine can load any
                # angle count that divides height evenly (ENG-06 invariant).
                # Uncommon values (e.g., 14 for desert_plants) are allowed at runtime.
                angles = metadata["angles"]
                if angles < 1:
                    errors.append(f"Invalid angles count: {angles}. Must be >= 1")

                # Validate anims
                anims = metadata["anims"]
                if not anims or len(anims) == 0:
                    errors.append("No animation frame counts found")
                else:
                    for idx, count in enumerate(anims):
                        if count < 1:
                            errors.append(
                                f"Animation {idx} has invalid frame count: {count}"
                            )
                        # WHY: 35 is the maximum frame count because glyph
                        # encoding uses 0-9 + A-Z (10+26=36 symbols, but 0 is
                        # reserved), so max representable value is 35.
                        # TODO(PIPELINE-FIX): This magic number 35 should be
                        # a named constant shared with xp_core's encoder.
                        if count > 35:
                            errors.append(
                                f"Animation {idx} frame count exceeds 35: {count}"
                            )
        except Exception as e:
            errors.append(f"Failed to extract metadata: {e}")

        # Validate layer dimensions
        if layer0.width < 1:
            errors.append(f"Invalid layer width: {layer0.width}")
        if layer0.height < 1:
            errors.append(f"Invalid layer height: {layer0.height}")

    except Exception as e:
        errors.append(f"Failed to load XP file: {e}")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "metadata": metadata,
        "size": size,
    }


def validate_sheet_specs(
    sheet_path: Path, angles: int, frames: List[int]
) -> Dict[str, Any]:
    """
    Validate sprite sheet dimensions match expected grid size.

    Args:
        sheet_path: Path to sprite sheet PNG
        angles: Number of angle views
        frames: List of frame counts per animation

    Returns:
        Dict with keys:
            - valid: bool indicating if sheet matches specs
            - errors: List of error messages
            - dimensions: Tuple of (expected_width, expected_height, actual_width, actual_height)
    """
    errors: List[str] = []
    from PIL import Image

    # Check file exists
    if not sheet_path.exists():
        errors.append(f"File not found: {sheet_path}")
        return {
            "valid": False,
            "errors": errors,
            "dimensions": (0, 0, 0, 0),
        }

    try:
        img = Image.open(sheet_path)
        actual_width, actual_height = img.size

        # WHY: 12x12 is the fixed cell size for the Asciicker sprite format.
        # Each cell maps to one glyph in the XP file.  The sheet's total
        # pixel dimensions must be an exact multiple of 12 in both axes.
        # TODO(PIPELINE-FIX): cell_size is duplicated across validator.py,
        # auto_adjust.py, debug_sheet.py -- should be a shared constant.
        cell_size = 12
        expected_cols = sum(frames)
        expected_rows = angles
        expected_width = expected_cols * cell_size
        expected_height = expected_rows * cell_size

        # Validate dimensions
        if actual_width != expected_width:
            errors.append(
                f"Width mismatch: expected {expected_width}px ({expected_cols} cols × {cell_size}px), "
                f"got {actual_width}px"
            )

        if actual_height != expected_height:
            errors.append(
                f"Height mismatch: expected {expected_height}px ({expected_rows} rows × {cell_size}px), "
                f"got {actual_height}px"
            )

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "dimensions": (
                expected_width,
                expected_height,
                actual_width,
                actual_height,
            ),
        }

    except Exception as e:
        errors.append(f"Failed to validate sheet: {e}")
        return {
            "valid": False,
            "errors": errors,
            "dimensions": (0, 0, 0, 0),
        }


def print_validation_report(result: Dict[str, Any], label: str = "XP File") -> None:
    """
    Print formatted validation report.

    Args:
        result: Validation result dict from validate_xp or validate_sheet_specs
        label: Label to use in output (e.g., "XP File" or "Sprite Sheet")
    """
    if result["valid"]:
        print(f"✓ {label} VALID")
    else:
        print(f"✗ {label} INVALID")

    print()
    if result.get("metadata"):
        meta = result["metadata"]
        if meta.get("angles") is not None:
            print(f"  Angles: {meta['angles']}")
        if meta.get("anims"):
            print(f"  Animations: {', '.join(str(f) for f in meta['anims'])} frames")

    if result.get("size"):
        size = result["size"]
        if size.get("width"):
            print(f"  Dimensions: {size['width']} x {size['height']}")

    if result.get("dimensions"):
        exp_w, exp_h, act_w, act_h = result["dimensions"]
        print(f"  Expected: {exp_w} x {exp_h}")
        print(f"  Actual: {act_w} x {act_h}")

    if result["errors"]:
        print()
        print(f"  Errors:")
        for error in result["errors"]:
            print(f"    - {error}")

    print()


def warn_staging_staleness(staging_path: str = None, warn_days: int = 7) -> None:
    """
    Check and warn about stale staging files.

    Called at start of validation pipeline to alert user about
    work-in-progress assets that may have been forgotten. This is
    informational only and does not affect validation results.

    Args:
        staging_path: Path to staging directory. Defaults to asset_gen/staging/.
        warn_days: Age threshold in days (default: 7)

    Example:
        warn_staging_staleness()
        # [STAGING] Warning: 2 stale file(s) in staging/
        #   [xp] 14 days old: staging/xp/old_sprite.xp

    Note:
        Errors during staleness check are caught and logged but do not
        propagate - validation should not fail due to staleness check issues.
    """
    try:
        stale = check_staging_staleness(staging_path, warn_days)
        print_staleness_warnings(stale)
    except Exception as e:
        # Don't fail validation due to staleness check errors
        print(f"[STAGING] Could not check staleness: {e}")


def validate_all(
    xp_path: Path,
    strict: bool = False,
    quiet: bool = False,
    verbose: bool = False,
    color_mode: str = "auto",
    palette_path: str = None
) -> ValidationResult:
    """Run all validation checks on an XP file.

    Orchestrates:
    1. Basic XP structure validation (existing validate_xp)
    2. Timing metadata validation (if sidecar exists)
    3. Palette compatibility validation (Layer 2+)

    Args:
        xp_path: Path to .xp file
        strict: Treat warnings as errors
        quiet: Suppress detailed output
        verbose: Show all checks including passing
        color_mode: ANSI color mode ("always", "auto", "never")
        palette_path: Optional custom palette file path

    Returns:
        Combined ValidationResult from all checks
    """
    # Load custom palette if specified
    palette = None
    if palette_path:
        palette, palette_error = load_custom_palette(palette_path)
        if palette_error:
            return ValidationResult(
                valid=False,
                errors=[palette_error],
                warnings=[],
                info=[]
            )

    # Step 1: Basic XP validation (existing function)
    basic_result = validate_xp(xp_path)

    # Convert old-style dict result to ValidationResult
    xp_result = ValidationResult(
        valid=basic_result.get("valid", False),
        errors=basic_result.get("errors", []),
        warnings=[],
        info=[]
    )

    if not basic_result.get("valid", False):
        # XP file is invalid, skip further checks
        return xp_result

    # Step 2: Timing validation
    timing_result = validate_timing(xp_path, basic_result.get("metadata"))

    # Step 3: Palette validation
    try:
        xp_file = XPFile(str(xp_path))
        palette_result = validate_palette(
            xp_file,
            palette=palette,
            xp_path=xp_path
        )
    except Exception as e:
        palette_result = ValidationResult(
            valid=False,
            errors=[f"Failed to load XP for palette check: {e}"],
            warnings=[],
            info=[]
        )

    # Merge all results
    combined = xp_result.merge(timing_result).merge(palette_result)

    return combined


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser for validator CLI.

    [FLOW:CLI] Provides --strict, --quiet, --verbose, --color, --palette flags
    per internal design notes decisions.
    """
    parser = argparse.ArgumentParser(
        description="Validate XP sprite files with advanced checks for timing and palette",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exit codes:
  0 = Validation passed (clean)
  1 = Validation failed (errors) or warnings in strict mode
  2 = Validation passed with warnings

Examples:
  %(prog)s assets/sprites/player.xp
  %(prog)s assets/sprites/player.xp --strict          # Fail on warnings (CI mode)
  %(prog)s assets/sprites/player.xp --quiet           # One-line summary only
  %(prog)s assets/sprites/player.xp --verbose         # Show all checks
  %(prog)s assets/sprites/player.xp --palette custom.json
"""
    )

    parser.add_argument(
        "xp_file",
        type=str,
        help="Path to .xp file to validate"
    )

    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors (exit code 1 instead of 2)"
    )

    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress warning details, show one-line summary only"
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show all checks performed, including passing ones"
    )

    parser.add_argument(
        "--color",
        choices=["always", "auto", "never"],
        default="auto",
        help="Control ANSI color output (default: auto-detect TTY)"
    )

    parser.add_argument(
        "--palette",
        type=str,
        metavar="PATH",
        help="Override palette for validation (JSON file with [[R,G,B],...] array)"
    )

    return parser


def main() -> int:
    """CLI entry point for validator.

    Returns:
        Exit code: 0=clean, 1=errors, 2=warnings-only
    """
    parser = create_parser()
    args = parser.parse_args()

    xp_path = Path(args.xp_file)

    # Run all validations
    result = validate_all(
        xp_path,
        strict=args.strict,
        quiet=args.quiet,
        verbose=args.verbose,
        color_mode=args.color,
        palette_path=args.palette
    )

    # Print results
    print_validation_result(
        result,
        label=str(xp_path),
        quiet=args.quiet,
        verbose=args.verbose,
        color_mode=args.color
    )

    # Return appropriate exit code
    exit_code = result.exit_code(strict=args.strict)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
