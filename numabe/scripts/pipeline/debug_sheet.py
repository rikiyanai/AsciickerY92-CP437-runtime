"""
Debug sheet rendering for sprite editing workflows.

Generates visual debugging sheets with semantic labels showing angle/frame
information, grid overlays, and source file abbreviations.

ARCHITECTURE:
    This module produces transparent RGBA overlay images that can be composited
    on top of a sprite sheet PNG to visualize the grid structure.  The overlay
    contains:
      - Dotted 12x12 grid lines (white, 50% alpha) to show cell boundaries
      - Per-cell labels: angle/frame identifier, pixel coordinates, source file

    The main ``AssetPipeline`` in pipeline.py calls ``render_debug_sheet``
    after assembly to produce a companion ``*_debug.png`` alongside every
    output .xp file.

KEY EXPORTS:
    - render_debug_sheet: Build a full debug overlay for a template+frames list
    - draw_dotted_grid:   Draw dotted grid lines on an ImageDraw context
    - format_label:       Format a cell label string from angle/frame metadata

PIPELINE CONTEXT:
    [PIPELINE:PROCESS]   -- Runs as a diagnostic side-output after the PROCESS
        or ASSEMBLE stage; does not modify any pipeline data.
    [DEPENDENCY:PIL]     -- All rendering via Pillow (Image, ImageDraw, ImageFont).
    [DATA-CONTRACT:XP]   -- Output debug sheets correspond 1:1 with assembled .xp
        sprite sheets; grid dimensions must match the template used for assembly.
"""

from PIL import Image, ImageDraw, ImageFont
from typing import List, Dict, Optional, Any
from pathlib import Path


# ==============================
# Constants
# ==============================

# WHY: 12x12 is the fixed pixel size of one glyph cell in the Asciicker
# engine.  This constant must stay in sync with the same value used in
# validator.py, auto_adjust.py, slicer.py, and the C++ renderer.
# TODO(PIPELINE-FIX): Should be imported from a shared constants module.
CELL_SIZE = 12


# ==============================
# render_debug_sheet
# ==============================


def render_debug_sheet(
    template: Any,
    frames_info: List[Dict[str, Any]],
    label_format: Optional[str] = None,
) -> Image.Image:
    """
    Render debug sheet with grid lines and semantic labels.

    Args:
        template: Template object with layout_cols() and layout_rows() methods
        frames_info: List of frame metadata dicts, each containing:
            - 'angle': Angle index (0-7)
            - 'frame': Frame index
            - 'anim_name': Optional animation name
            - 'source_path': Path to source file
            - 'col': Column position in grid
            - 'row': Row position in grid
        label_format: Optional custom format template for labels

    Returns:
        PIL.Image: Debug PNG sheet with RGBA mode and transparent background

    Raises:
        AttributeError: If template lacks layout_cols() or layout_rows() methods.
        KeyError: If any frame_info dict is missing required keys
            ('angle', 'frame', 'source_path', 'col', 'row').
    """
    # Calculate grid dimensions
    cols = template.layout_cols()
    rows = template.layout_rows()
    width = cols * CELL_SIZE
    height = rows * CELL_SIZE

    # WHY: RGBA with fully-transparent white background so the overlay
    # can be alpha-composited onto the actual sprite sheet without
    # obscuring pixel content.
    debug_img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(debug_img)
    # WHY: load_default() returns Pillow's built-in bitmap font, which
    # is always available and does not require external .ttf files.
    font = ImageFont.load_default()

    # Draw dotted grid lines
    draw_dotted_grid(draw, width, height)

    # Draw cell labels
    for frame_info in frames_info:
        angle = frame_info["angle"]
        frame = frame_info["frame"]
        anim = frame_info.get("anim_name")
        source = frame_info["source_path"]
        col = frame_info["col"]
        row = frame_info["row"]

        # Get cell position
        x = col * CELL_SIZE
        y = row * CELL_SIZE

        # Format label with optional custom format template
        label_top = format_label(angle, frame, anim=anim, format_template=label_format)

        # Abbreviate source path
        source_short = Path(source).parts[-1]

        # WHY: Three stacked label lines per cell, each with decreasing alpha
        # so that the most important info (angle/frame) is brightest.
        # Line 1: angle+frame ID (full white, alpha 255)
        # Line 2: pixel coordinates for manual cross-reference (alpha 200)
        # Line 3: source filename abbreviation for provenance (alpha 180)
        # TODO(PIPELINE-FIX): At cell_size=12, these labels overflow the cell
        # boundary vertically (line 3 starts at y+17, cell ends at y+12).
        # This works because the overlay is transparent, but can obscure
        # adjacent cells.  Consider truncating or only showing on hover.
        draw.text((x + 1, y + 1), label_top, fill=(255, 255, 255, 255), font=font)
        draw.text((x + 1, y + 9), f"({x},{y})", fill=(255, 255, 255, 200), font=font)
        draw.text((x + 1, y + 17), source_short, fill=(255, 255, 255, 180), font=font)

    return debug_img


# ==============================
# draw_dotted_grid
# ==============================


def draw_dotted_grid(
    draw: ImageDraw.ImageDraw, width: int, height: int, cell_size: int = CELL_SIZE
) -> None:
    """
    Draw dotted grid lines on image.

    Note: PIL doesn't natively support dotted lines.
    This draws 2px dots with 4px gaps in a loop.

    Args:
        draw: ImageDraw.Draw context to render onto (mutated in place).
        width: Image width in pixels.
        height: Image height in pixels.
        cell_size: Cell size in pixels (default 12).

    Returns:
        None. The draw context is mutated in place.
    """
    # WHY: PIL has no built-in dotted-line primitive, so we simulate it
    # by drawing short 2px segments separated by 4px gaps.  The 2:4 ratio
    # keeps the grid visible without dominating the overlay.
    dot_len = 2
    gap_len = 4
    step = dot_len + gap_len

    # Calculate number of columns and rows
    cols = width // cell_size
    rows = height // cell_size

    # Draw vertical dotted lines
    for col in range(cols + 1):
        x = col * cell_size
        for y in range(0, height, step):
            draw.line(
                [(x, y), (x, min(y + dot_len, height))],
                fill=(255, 255, 255, 128),
                width=2,
            )

    # Draw horizontal dotted lines
    for row in range(rows + 1):
        y = row * cell_size
        for x in range(0, width, step):
            draw.line(
                [(x, y), (min(x + dot_len, width), y)],
                fill=(255, 255, 255, 128),
                width=2,
            )


# ==============================
# format_label
# ==============================


def format_label(
    angle: int,
    frame: int,
    anim: Optional[str] = None,
    format_template: Optional[str] = None,
) -> str:
    """
    Generate label using template format.

    Supported placeholders: {angle}, {frame}, {anim}
    Format examples:
      - "A{angle:02}-F{frame:02}"         → "A07-F03"
      - "A{angle:02}-{anim}:{frame:02}"  → "A07-walk:03"

    Args:
        angle: Angle index (0-7)
        frame: Frame index (0-35)
        anim: Optional animation name
        format_template: Custom format string (uses default if None)

    Returns:
        str: Formatted label with zero-padding
    """
    if anim:
        # Animation name preferred
        if format_template:
            return format_template.format(angle=angle, frame=frame, anim=anim)
        else:
            # Default format with animation name
            return f"A{angle:02}-{anim}:{frame:02}"
    else:
        # Fallback to frame numbers when animation unavailable
        if format_template and "{anim}" not in format_template:
            return format_template.format(angle=angle, frame=frame)
        else:
            # Use frame format or default
            return f"A{angle:02}-F{frame:02}"


# Export symbols
__all__ = ["render_debug_sheet", "draw_dotted_grid", "format_label", "CELL_SIZE"]
