"""
Auto-adjustments pipeline for sprite preparation.

Provides automatic corrections for sprite sheets before processing:
1. Magenta snap - Snap near-magenta colors to pure magenta (255,0,255)
2. Palette quantize - Quantize to 16-color ANSI palette
3. Grid validation - Validate dimensions match expected grid
4. Crop/center - Center sprite content in 12x12 cells

Usage:
    from asset_gen.auto_adjust import run_auto_adjustments
    adjusted_path = run_auto_adjustments(Path("sprite.png"), config)

ARCHITECTURE:
    This module is an optional pre-processing pass that sits between image
    generation/import and the main SLICE/PROCESS stages.  It normalizes raw
    artwork so that downstream stages (SpriteProcessor, XPAssembler) receive
    images that already satisfy the 16-color ANSI palette and 12x12 grid
    alignment contracts.

    The 4 stages run sequentially in ``run_auto_adjustments``:
      Stage 1  fix_magenta_bg      -- color-space cleanup
      Stage 2  quantize_palette    -- palette reduction
      Stage 3  validate_grid       -- dimension assertion
      Stage 4  crop_and_center     -- spatial normalization

KEY EXPORTS:
    - fix_magenta_bg:          Snap near-magenta to pure magenta (255,0,255)
    - quantize_palette:        Reduce to 16-color ANSI palette
    - validate_grid_structure: Assert sheet dimensions match grid spec
    - crop_and_center_frames:  Center content within each 12x12 cell
    - run_auto_adjustments:    Run all 4 stages end-to-end

PIPELINE CONTEXT:
    [PIPELINE:GENERATE] -- Sits between raw image acquisition and the main
        SLICE/PROCESS stages as an optional normalization pass.
    [PIPELINE:SLICE]    -- Grid validation enforces the same cell_size=12
        contract that the slicer uses to cut frames.
    [PIPELINE:PROCESS]  -- Palette quantization ensures pixel colors are
        already in the 16-color ANSI set before glyph matching begins.
    [DEPENDENCY:PIL]    -- All image manipulation via Pillow.
    [FLOW:CLI]          -- Runnable standalone via ``python -m auto_adjust input.png``.
"""

from pathlib import Path
from typing import Dict, Any, List, Tuple
from PIL import Image
import sys
import os

# WHY: Allows importing color_correction and color_quantizer from the
# parent scripts/ directory when running this file directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def fix_magenta_bg(img: Image, tolerance: int = 15) -> Image:
    """
    Snap near-magenta colors to pure magenta (255,0,255).

    [DEPENDENCY:PIL] [PIPELINE:GENERATE]

    WHY: Raw source images -- especially AI-generated ones -- often have
    near-magenta backgrounds (e.g. (252, 3, 250)) that differ by a few
    LSBs from the canonical (255, 0, 255) color key.  If these aren't
    snapped, downstream stages will treat them as opaque content.

    Args:
        img: PIL Image (RGB mode).
        tolerance: L1 distance threshold for snapping (default 15).
            Pixels whose L1 distance from (255,0,255) is <= tolerance
            are replaced with exact magenta.

    Returns:
        New PIL Image with corrected magenta colors.

    Raises:
        ImportError: If ``color_correction.snap_to_magenta`` cannot be
            resolved (see TODO below).
    """
    # WHY: The duplicate import is a no-op -- both branches import the same
    # symbol.  This appears to be a copy-paste artifact; the second branch
    # was likely intended to try a relative import as a fallback.
    # TODO(PIPELINE-FIX): Collapse to a single import; add a true fallback
    # or inline implementation if color_correction may be absent.
    try:
        from asset_gen.color_correction import snap_to_magenta
    except ImportError:
        from asset_gen.color_correction import snap_to_magenta

    return snap_to_magenta(img, tolerance=tolerance)


def quantize_palette(img: Image) -> Image:
    """
    Quantize image to 16-color ANSI palette.

    [DEPENDENCY:PIL] [PIPELINE:PROCESS]

    WHY: The Asciicker engine renders glyphs using exactly 16 ANSI colors.
    Reducing the palette *before* the PROCESS stage's glyph matcher runs
    avoids quantization noise during matching and ensures color fidelity
    between the preview sheet and the final XP output.

    The function uses a two-tier import strategy:
      1. Try the external ``color_quantizer`` module (faster, C-optimized).
      2. Fall back to an inline pure-Python L1-distance quantizer.

    Args:
        img: PIL Image (RGB mode).

    Returns:
        New PIL Image with all pixels mapped to the nearest of 16 ANSI colors.
    """
    # WHY: Two-tier import -- the external quantizer lives in scripts/ and
    # may not be importable depending on how the module is invoked (package
    # vs. standalone).  The inline fallback guarantees the pipeline never
    # fails just because of an import path issue.
    try:
        from scripts.color_quantizer import quantize_rgb_to_index
    except ImportError:
        try:
            from color_quantizer import quantize_rgb_to_index
        except ImportError:
            # Fallback to simple quantization
            quantize_rgb_to_index = None

    if quantize_rgb_to_index is None:
        # WHY: Fallback inline quantizer for environments where the
        # standalone color_quantizer module is not on sys.path.
        # Uses L1 (Manhattan) distance for speed; good enough for
        # the 16-color ANSI palette which has wide color spacing.
        ANSI_COLORS = [
            (0, 0, 0),
            (128, 0, 0),
            (0, 128, 0),
            (128, 128, 0),
            (0, 0, 128),
            (128, 0, 128),
            (0, 128, 128),
            (128, 128, 128),
            (192, 192, 192),
            (255, 0, 0),
            (0, 255, 0),
            (255, 255, 0),
            (0, 0, 255),
            (255, 0, 255),
            (0, 255, 255),
            (255, 255, 255),
        ]

        def nearest_color(rgb):
            """Return the index of the nearest ANSI color by L1 distance.

            Args:
                rgb: (R, G, B) tuple to match.

            Returns:
                Index into ANSI_COLORS of the closest match.
            """
            r, g, b = rgb
            distances = [
                (abs(r - c[0]) + abs(g - c[1]) + abs(b - c[2]), idx)
                for idx, c in enumerate(ANSI_COLORS)
            ]
            return min(distances)[1]

        # Apply quantization
        # TODO(PIPELINE-FIX): Per-pixel Python loop is O(W*H*16) and very slow
        # for large images (e.g. 2048x2048 AI outputs).  Consider using NumPy
        # vectorized distance or Pillow's built-in quantize() with a custom palette.
        result = Image.new("RGB", img.size)
        pixels = img.load()
        for y in range(img.height):
            for x in range(img.width):
                result.putpixel((x, y), ANSI_COLORS[nearest_color(pixels[x, y])])
        return result

    # WHY: When the external quantizer is available, use it for index
    # mapping but still need the ANSI_COLORS table to convert back to RGB.
    # TODO(PIPELINE-FIX): ANSI_COLORS is duplicated above and below --
    # extract to a module-level constant or import from palette.py.
    result = Image.new("RGB", img.size)
    pixels = img.load()
    result_pixels = result.load()

    for y in range(img.height):
        for x in range(img.width):
            r, g, b = pixels[x, y]
            color_idx = quantize_rgb_to_index(r, g, b)
            ANSI_COLORS = [
                (0, 0, 0),
                (128, 0, 0),
                (0, 128, 0),
                (128, 128, 0),
                (0, 0, 128),
                (128, 0, 128),
                (0, 128, 128),
                (128, 128, 128),
                (192, 192, 192),
                (255, 0, 0),
                (0, 255, 0),
                (255, 255, 0),
                (0, 0, 255),
                (255, 0, 255),
                (0, 255, 255),
                (255, 255, 255),
            ]
            result_pixels[x, y] = ANSI_COLORS[color_idx]

    return result


def validate_grid_structure(
    img: Image, angles: int, frames: List[int]
) -> Dict[str, Any]:
    """
    Validate sprite sheet dimensions match expected grid size.

    [DEPENDENCY:PIL] [PIPELINE:SLICE]

    WHY: Catching dimension mismatches early (before slicing) produces a
    clear error message rather than silent corruption -- e.g. a 96x96
    image with angles=8 and frames=[4] expects 48x96 and would silently
    produce half-empty cells if not caught here.

    Args:
        img: PIL Image to validate.
        angles: Number of angle views (1, 4, or 8).
        frames: List of frame counts per animation (e.g. [4] for a single
            4-frame walk cycle, [4, 8] for idle + walk).

    Returns:
        Dict with validation results:
            - valid: bool indicating if dimensions match
            - expected_size: Tuple (expected_width, expected_height)
            - actual_size: Tuple (actual_width, actual_height)
    """
    # WHY: 12x12 is the Asciicker engine's fixed glyph-cell size in pixels.
    # TODO(PIPELINE-FIX): Duplicated magic number -- should import from a
    # shared constants module (also used in validator.py, debug_sheet.py).
    cell_size = 12

    # Calculate expected dimensions
    expected_width = sum(frames) * cell_size
    expected_height = angles * cell_size

    actual_width, actual_height = img.size

    return {
        "valid": (actual_width == expected_width and actual_height == expected_height),
        "expected_size": (expected_width, expected_height),
        "actual_size": (actual_width, actual_height),
    }


def crop_and_center_frames(img: Image) -> Image:
    """
    Detect bounding box of non-magenta content and center in 12x12 cells.

    [DEPENDENCY:PIL] [PIPELINE:PROCESS]

    WHY: Source artwork is rarely pixel-perfect aligned to the 12x12 grid.
    Centering each cell's content improves glyph matching accuracy in the
    PROCESS stage and produces visually consistent sprites.

    Algorithm:
      1. Full-image scan to find the axis-aligned bounding box of all
         non-magenta content (fuzzy threshold: r>240, g<15, b>240).
      2. For each 12x12 cell in the grid, extract the proportional slice
         of the bounding box and center it within the cell.
      3. Content larger than 12x12 is scaled down to fit.

    Args:
        img: PIL Image with sprite content (RGB mode).

    Returns:
        New PIL Image (same dimensions) with sprites centered in cells.
    """
    # Load pixel data
    pixels = img.load()
    w, h = img.size

    # WHY: We scan the entire image to find the axis-aligned bounding box
    # of all non-magenta (non-transparent) content.  The fuzzy threshold
    # (r>240, g<15, b>240) matches the tolerance used by fix_magenta_bg
    # to account for JPEG artifacts or slight color drift.
    # TODO(PIPELINE-FIX): The fuzzy magenta threshold here differs from
    # fix_magenta_bg's configurable tolerance -- should be unified.
    min_x = w
    min_y = h
    max_x = 0
    max_y = 0

    for y in range(h):
        for x in range(w):
            r, g, b = pixels[x, y]
            if not (r > 240 and g < 15 and b > 240):
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y

    # If no content found, return original
    if min_x >= max_x or min_y >= max_y:
        return img.copy()

    # Calculate centered position
    content_w = max_x - min_x + 1
    content_h = max_y - min_y + 1

    # Create new image for centered result
    result = Image.new("RGB", (w, h), (255, 0, 255))

    # WHY: After finding the global bounding box, we redistribute content
    # into a per-cell centering pass.  Each 12x12 cell gets its proportional
    # slice of the original content, centered with equal padding on all sides.
    # This ensures the glyph matcher in the PROCESS stage sees well-centered
    # patterns rather than content shoved against a cell edge.
    # TODO(PIPELINE-FIX): This assumes content is uniformly distributed
    # across the grid, which breaks for sparse sheets where only some cells
    # have content.  A per-cell bounding-box pass would be more robust.
    cell_w = 12
    cell_h = 12
    grid_w = w // cell_w
    grid_h = h // cell_h

    for grid_y in range(grid_h):
        for grid_x in range(grid_w):
            cell_left = grid_x * cell_w
            cell_top = grid_y * cell_h
            cell_right = cell_left + cell_w
            cell_bottom = cell_top + cell_h

            content_region = img.crop(
                (
                    min_x + grid_x * content_w,
                    min_y + grid_y * content_h,
                    min_x + (grid_x + 1) * content_w,
                    min_y + (grid_y + 1) * content_h,
                )
            )

            # Resize to fit cell and center in new image
            if content_region.size[0] > 0 and content_region.size[1] > 0:
                # Calculate centering offset
                offset_x = (cell_w - content_w) // 2
                offset_y = (cell_h - content_h) // 2

                paste_x = cell_left + offset_x
                paste_y = cell_top + offset_y

                # If content is larger than cell, scale down
                if content_w > cell_w or content_h > cell_h:
                    content_region = content_region.resize((cell_w, cell_h))
                    paste_x = cell_left
                    paste_y = cell_top

                result.paste(content_region, (paste_x, paste_y))

    return result


def run_auto_adjustments(img_path: Path, config: Dict = None) -> Path:
    """
    Run all 4 adjustment stages on an image.

    [PIPELINE:GENERATE] [DEPENDENCY:PIL] [FLOW:CLI]

    This is the main orchestrator for the auto-adjustment pipeline.
    It chains the 4 stages sequentially and writes the result to disk.

    Stages:
      1. Magenta snap   -- Fix background colors   (fix_magenta_bg)
      2. Palette quantize -- Reduce to 16 ANSI      (quantize_palette)
      3. Grid validation  -- Check dimensions        (validate_grid_structure)
      4. Crop/center      -- Center sprites in cells (crop_and_center_frames)

    Args:
        img_path: Path to input image.
        config: Optional config dict with:
            - tolerance (int): Magenta snap tolerance (default 15).
            - angles (int): Number of angles for grid validation (default 1).
            - frames (List[int]): Frame counts for grid validation (default [1]).
            - output_path (str): Custom output path. If omitted, writes to
              ``staging/sheets/{name}_adjusted.png``.

    Returns:
        Path to the adjusted output image.

    Raises:
        ValueError: If grid validation (Stage 3) fails -- dimensions do not
            match the expected angles * frames * 12 layout.
        FileNotFoundError: If ``img_path`` does not exist.
    """
    if config is None:
        config = {}

    # Load image
    img = Image.open(str(img_path)).convert("RGB")
    original_path = img_path
    img_name = img_path.stem

    print(f"Auto-adjustments for: {img_path.name}")

    # Stage 1/4: Magenta snap
    print("  Stage 1/4: Magenta snap...")
    tolerance = config.get("tolerance", 15)
    img = fix_magenta_bg(img, tolerance=tolerance)
    print("    ✓ Snapped near-magenta colors")

    # Stage 2/4: Palette quantize
    print("  Stage 2/4: Palette quantize...")
    img = quantize_palette(img)
    print("    ✓ Quantized to 16-color ANSI palette")

    # Stage 3/4: Grid validation
    print("  Stage 3/4: Grid validation...")
    angles = config.get("angles", 1)
    frames = config.get("frames", [1])
    validation = validate_grid_structure(img, angles, frames)

    if not validation["valid"]:
        # TODO(PIPELINE-FIX): Hard failure on grid mismatch prevents
        # processing non-standard sheets (e.g. single sprites, tilesheets).
        # Consider adding a config flag to downgrade this to a warning and
        # skip crop_and_center instead of aborting entirely.
        exp_w, exp_h = validation["expected_size"]
        act_w, act_h = validation["actual_size"]
        raise ValueError(
            f"Grid validation failed: expected {exp_w}x{exp_h}, got {act_w}x{act_h}"
        )
    print(f"    ✓ Grid dimensions validated")

    # Stage 4/4: Crop/center
    print("  Stage 4/4: Crop and center...")
    img = crop_and_center_frames(img)
    print("    ✓ Sprites centered in cells")

    # Save adjusted image
    if "output_path" in config:
        output_path = Path(config["output_path"])
    else:
        # Default output: staging/sheets/{name}_adjusted.png
        # TODO(PIPELINE-FIX): Relative path "staging/sheets" depends on CWD,
        # which varies between CLI invocation, pipeline.py, and CI runners.
        # Should resolve relative to the project root or the input file's parent.
        staging_dir = Path("staging/sheets")
        staging_dir.mkdir(parents=True, exist_ok=True)
        output_path = staging_dir / f"{img_name}_adjusted.png"

    img.save(output_path)
    print(f"  ✓ Adjusted image saved: {output_path}")

    return output_path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Auto-adjustments pipeline for sprite preparation"
    )
    parser.add_argument("input", help="Input image path")
    parser.add_argument("--output", help="Output path (optional)")
    parser.add_argument(
        "--tolerance", type=int, default=15, help="Magenta snap tolerance"
    )
    parser.add_argument("--angles", type=int, default=1, help="Number of angles")
    parser.add_argument(
        "--frames", nargs="+", type=int, default=[1], help="Frame counts"
    )

    args = parser.parse_args()

    config = {
        "tolerance": args.tolerance,
        "angles": args.angles,
        "frames": args.frames,
    }

    if args.output:
        config["output_path"] = args.output

    result = run_auto_adjustments(Path(args.input), config)
    print(f"\nOutput: {result}")
