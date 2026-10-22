"""
Literal (1:1 pixel-to-cell) processor for the Asciicker asset pipeline.

Ported from PNG2Rex (main.cpp). Maps each pixel directly to one XP cell
using alpha-channel density to select CP437 block glyphs:

    Alpha == 0      -> transparent cell (glyph 0, MAGENTA bg)
    Alpha == 255    -> solid block 219, fg = pixel RGB
    Alpha < 64      -> light shade 176 (25% coverage)
    Alpha < 128     -> medium shade 177 (50% coverage)
    Alpha < 192     -> dark shade 178 (75% coverage)
    Alpha >= 192    -> solid block 219

This produces a 1:1 mapping (one pixel = one cell), which is the fastest
track in the 4-track pipeline. No color decomposition or glyph matching
is needed -- each cell's foreground is simply the pixel's RGB color.

Pipeline position: Stage 3 alternative processor, selected when
``PipelineConfig.process_settings.mode == "literal"``.

Exports
-------
- ``process_literal()``  -- process a single frame (PIL Image -> cell grid)
- ``process_literal_np()`` -- vectorized version for ndarray input

Output format matches ``SpriteProcessor.process_image()``:
    ``List[List[Tuple[int, Tuple[int,int,int], Tuple[int,int,int]]]]``
    i.e. row-major grid of (glyph, fg_rgb, bg_rgb) tuples.

Tags: [PIPELINE:PROCESS] [DATA-CONTRACT:XP] [DATA-CONTRACT:CP437]
      [FLOW:LITERAL]
"""

import logging
from typing import List, Tuple

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# [DATA-CONTRACT:CP437] -- Block element glyphs used for alpha density.
GLYPH_TRANSPARENT = 0       # REXPaint transparent marker
GLYPH_LIGHT_SHADE = 176     # CP437 ░ (~25% fill)
GLYPH_MEDIUM_SHADE = 177    # CP437 ▒ (~50% fill)
GLYPH_DARK_SHADE = 178      # CP437 ▓ (~75% fill)
GLYPH_FULL_BLOCK = 219      # CP437 █ (100% fill)

# Alpha thresholds matching PNG2Rex behavior (0-255 scale).
ALPHA_TIER_LIGHT = 64       # < 25% = light shade
ALPHA_TIER_MEDIUM = 128     # < 50% = medium shade
ALPHA_TIER_DARK = 192       # < 75% = dark shade

# [DATA-CONTRACT:PALETTE] -- Magenta key color for transparent cells.
MAGENTA_RGB = (255, 0, 255)
BLACK_RGB = (0, 0, 0)

# Type alias for a single cell.
Cell = Tuple[int, Tuple[int, int, int], Tuple[int, int, int]]


def _alpha_to_glyph(alpha: int) -> int:
    """Map an alpha value (0-255) to the appropriate CP437 block glyph."""
    if alpha == 0:
        return GLYPH_TRANSPARENT
    if alpha == 255:
        return GLYPH_FULL_BLOCK
    if alpha < ALPHA_TIER_LIGHT:
        return GLYPH_LIGHT_SHADE
    if alpha < ALPHA_TIER_MEDIUM:
        return GLYPH_MEDIUM_SHADE
    if alpha < ALPHA_TIER_DARK:
        return GLYPH_DARK_SHADE
    return GLYPH_FULL_BLOCK


def process_literal(image: Image.Image) -> List[List[Cell]]:
    """Convert a PIL Image to a cell grid using literal 1:1 mapping.

    Each pixel becomes one cell. The image MUST have an alpha channel
    (mode RGBA). RGB images are treated as fully opaque (all glyph 219).

    Args:
        image: PIL Image in RGBA or RGB mode.

    Returns:
        Row-major grid of (glyph, fg_rgb, bg_rgb) tuples.
        Grid dimensions match image pixel dimensions.

    Tags: [PIPELINE:PROCESS] [FLOW:LITERAL]
    """
    if image.mode == "RGB":
        image = image.convert("RGBA")
    elif image.mode != "RGBA":
        image = image.convert("RGBA")

    arr = np.array(image)
    return process_literal_np(arr)


def process_literal_np(pixels: np.ndarray) -> List[List[Cell]]:
    """Vectorized literal processor operating on an RGBA ndarray.

    Args:
        pixels: (H, W, 4) uint8 array with RGBA channels.

    Returns:
        Row-major grid of (glyph, fg_rgb, bg_rgb) tuples.

    Tags: [PIPELINE:PROCESS] [FLOW:LITERAL]
    """
    if pixels.ndim != 3 or pixels.shape[2] < 3:
        raise ValueError(
            f"Expected RGBA array with shape (H, W, 4), got {pixels.shape}"
        )

    h, w = pixels.shape[:2]
    has_alpha = pixels.shape[2] >= 4

    logger.info("Literal processor: %dx%d pixels -> %dx%d cells", w, h, w, h)

    grid: List[List[Cell]] = []

    for y in range(h):
        row: List[Cell] = []
        for x in range(w):
            r, g, b = int(pixels[y, x, 0]), int(pixels[y, x, 1]), int(pixels[y, x, 2])
            a = int(pixels[y, x, 3]) if has_alpha else 255

            if a == 0:
                # Transparent: glyph 0, magenta bg (REXPaint convention)
                row.append((GLYPH_TRANSPARENT, BLACK_RGB, MAGENTA_RGB))
            else:
                glyph = _alpha_to_glyph(a)
                row.append((glyph, (r, g, b), BLACK_RGB))
        grid.append(row)

    return grid


def process_literal_to_flat(image: Image.Image) -> Tuple[List[Cell], int, int]:
    """Convenience wrapper returning a flat cell list with dimensions.

    Returns:
        (cells, width, height) where cells is a flat list in row-major order.

    Tags: [PIPELINE:PROCESS] [FLOW:LITERAL]
    """
    grid = process_literal(image)
    h = len(grid)
    w = len(grid[0]) if h > 0 else 0
    flat = [cell for row in grid for cell in row]
    return flat, w, h
