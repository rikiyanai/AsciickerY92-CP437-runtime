"""
Canonical 16-color ANSI palette and magenta-key transparency detection.

Architecture
------------
This module is the **single source of truth** for the Asciicker sprite
pipeline's color palette.  Every stage that needs to reason about ANSI
colors or transparency should import from here rather than defining its
own copy.

Exports
~~~~~~~
- ``PALETTE_RGB``        -- The 16 standard ANSI colors as (R, G, B) tuples.
- ``COLOR_TRANSPARENT``  -- The magenta transparency key (255, 0, 255).
- ``MAGENTA_RGB``        -- Alias for ``COLOR_TRANSPARENT`` (legacy compat).
- ``is_transparent()``   -- Fuzzy magenta-key test with configurable tolerance.
- ``make_transparency_mask()`` -- Vectorized L1 magenta detection (numpy).
- ``get_palette_color()``-- Safe index-to-RGB lookup with white fallback.

Pipeline context
~~~~~~~~~~~~~~~~
Palette data flows through the pipeline as follows::

    [PIPELINE:GENERATE]  generator.py  -- uses is_transparent / COLOR_TRANSPARENT
                                          to detect magic-pink backgrounds
    [PIPELINE:PROCESS]   processor.py  -- uses is_transparent / MAGENTA_RGB to
                                          mark transparent cells in XP output
    [PIPELINE:PROCESS]   color_correction.py -- lazy-imports is_transparent for
                                                per-pixel transparency audit
    (external)           compare_images.py   -- imports is_transparent for QA diffs

.. note::

   ``quantizer.py`` and ``xp_tool.py`` maintain their own ``ANSI_COLORS``
   lists that are *value-identical* to ``PALETTE_RGB``.  These duplicates
   exist for historical reasons; a future cleanup should alias them here.

Tags
----
[DATA-CONTRACT:PALETTE]  Authoritative color definitions for the pipeline.
[DEPENDENCY:PIL]         None -- pure Python, no external deps beyond typing.
"""

from typing import Sequence, Tuple

from .service.constants import DEFAULT_TRANSPARENCY_TOLERANCE

# [DATA-CONTRACT:PALETTE] The Standard 16 ANSI Colors
# Format: (R, G, B)
#
# WHY: These exact values match the classic CGA / DOS 16-color convention.
# quantizer.py (ANSI_COLORS) and xp_tool.py (ANSI_COLORS) duplicate this
# table -- any change here MUST be mirrored there until the duplication is
# resolved.
# TODO(PIPELINE-FIX): Consolidate ANSI_COLORS in quantizer.py and xp_tool.py
#   to import from this module instead of redeclaring their own copies.
#   See also auto_adjust.py which has yet another inline copy.
PALETTE_RGB = [
    (0, 0, 0),        # 0: Black
    (128, 0, 0),      # 1: Red
    (0, 128, 0),      # 2: Green
    (128, 128, 0),    # 3: Yellow
    (0, 0, 128),      # 4: Blue
    (128, 0, 128),    # 5: Magenta
    (0, 128, 128),    # 6: Cyan
    (192, 192, 192),  # 7: White
    (128, 128, 128),  # 8: Bright Black
    (255, 0, 0),      # 9: Bright Red
    (0, 255, 0),      # 10: Bright Green
    (255, 255, 0),    # 11: Bright Yellow
    (0, 0, 255),      # 12: Bright Blue
    (255, 0, 255),    # 13: Bright Magenta (Transparency Key)
    (0, 255, 255),    # 14: Bright Cyan
    (255, 255, 255)   # 15: Bright White
]

# [DATA-CONTRACT:PALETTE] Transparent placeholder
# WHY: Magenta (255, 0, 255) is the engine-wide "chroma key".  The C++ renderer,
# the XP serializer, and every pipeline stage treat this exact RGB triple as
# "fully transparent".  Changing it would break rendering in world.h / render.cpp.
COLOR_TRANSPARENT = (255, 0, 255)
MAGENTA_RGB = COLOR_TRANSPARENT  # Legacy alias used by processor.py


# [PIPELINE:GENERATE] [PIPELINE:PROCESS]
def is_transparent(rgb: Sequence[int], tolerance: int = DEFAULT_TRANSPARENCY_TOLERANCE) -> bool:
    """
    Test whether *rgb* is close enough to the magenta chroma key to be
    treated as transparent.

    Uses Manhattan (L1) distance rather than Euclidean so that the
    tolerance threshold is intuitive: ``tolerance=5`` allows at most
    5 total across all three channels, not 5 per channel.

    Args:
        rgb: An RGB-like sequence in 0-255 range.
            RGBA tuples are accepted; alpha channel is ignored.
        tolerance: Maximum summed per-channel deviation from the
            magenta key that is still considered transparent.
            Default 5 matches the convention used by generator.py,
            processor.py, and color_correction.py.

    Returns:
        True if the color is within *tolerance* of COLOR_TRANSPARENT.

    Note:
        processor.py uses ``make_transparency_mask()`` (below) for its
        vectorized fast-path, which applies the same L1 metric as this
        scalar function.  Both must agree on edge-case colors.

    .. seealso:: ``make_transparency_mask()`` for the numpy fast-path.
    """
    # Accept RGBA tuples from PNG workflows (alpha ignored for key-color test).
    # Return False for malformed values rather than crashing pipeline runs.
    try:
        if len(rgb) < 3:
            return False
        r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
    except (TypeError, ValueError, IndexError):
        return False
    # WHY guard: out-of-range values can arrive from bad image data or
    # float-to-int rounding; reject early rather than give a false positive.
    if not (0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255):
        return False

    tr, tg, tb = COLOR_TRANSPARENT
    # WHY Manhattan distance: cheaper than Euclidean and the tolerance=5
    # contract is already embedded in every caller.  Switching metric
    # would silently change which pixels are transparent.
    # RESOLVED (Phase 4, BG-03): processor.py now uses make_transparency_mask()
    # which applies the same L1 metric. No more disagreement on edge cases.
    distance = abs(r - tr) + abs(g - tg) + abs(b - tb)
    return distance <= tolerance


# [PIPELINE:PROCESS]
def make_transparency_mask(
    pixels_rgb: "np.ndarray",
    tolerance: int = DEFAULT_TRANSPARENCY_TOLERANCE,
) -> "np.ndarray":
    """Vectorized L1 magenta transparency detection.

    Canonical replacement for processor.py's ad-hoc per-channel check.
    Uses the same L1 (Manhattan) metric as is_transparent().

    The tolerance parameter flows from BackgroundSpec.tolerance
    (user-configurable via --bg-tolerance, default 8). This ensures
    a single source of truth for what counts as "close to magenta."
    Both the alignment pass and processor glyph assignment use the
    same value from the same BackgroundSpec.

    Args:
        pixels_rgb: (H, W, 3) numpy array of RGB pixels.
        tolerance: L1 distance threshold from COLOR_TRANSPARENT.

    Returns:
        (H, W) boolean mask -- True where pixel is transparent.
    """
    import numpy as np
    tr, tg, tb = COLOR_TRANSPARENT
    dist = (
        np.abs(pixels_rgb[:, :, 0].astype(np.int16) - tr)
        + np.abs(pixels_rgb[:, :, 1].astype(np.int16) - tg)
        + np.abs(pixels_rgb[:, :, 2].astype(np.int16) - tb)
    )
    return dist <= tolerance


# [DATA-CONTRACT:PALETTE]
def get_palette_color(index: int) -> Tuple[int, int, int]:
    """
    Safe palette lookup by ANSI color index (0--15).

    Args:
        index: ANSI color index.  Valid range is 0-15 inclusive.

    Returns:
        The (R, G, B) tuple for the given index, or white (255, 255, 255)
        if *index* is out of range.

    Note:
        The white fallback is intentional: it makes out-of-range indices
        visually obvious in debug sheets without crashing the pipeline.
    """
    # WHY bounds-check + white fallback: pipeline stages can pass indices
    # produced by np.argmin which are always 0-15, but CLI / template
    # callers could pass arbitrary ints.  White is a visible sentinel.
    if 0 <= index < len(PALETTE_RGB):
        return PALETTE_RGB[index]
    return (255, 255, 255)
