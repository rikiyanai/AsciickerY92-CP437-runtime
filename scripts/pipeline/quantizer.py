"""
Color quantization: maps arbitrary RGB pixels to the 16-color ANSI palette.

Architecture
------------
This module owns the color-distance math that reduces 24-bit RGB values to
one of 16 standard ANSI color indices (0-15).  It provides both a scalar
path (``get_closest_color_index`` / ``quantize_pixel``) for single-pixel or
legacy use, and a vectorised NumPy path (``quantize_image_np``) that can
quantise an entire image frame in one broadcast operation.

Key Exports
~~~~~~~~~~~
- ``ANSI_COLORS``          -- The 16-entry palette as (R, G, B) tuples.
- ``PALETTE_NP``           -- Same palette as an ``(16, 3)`` int32 ndarray for
                              vectorised distance computation.
- ``get_closest_color_index(rgb)`` -- Scalar nearest-palette-index lookup.
- ``quantize_pixel(rgb)``  -- Convenience wrapper returning the palette RGB.
- ``quantize_image_np(image_np)`` -- Vectorised quantisation of an (H, W, 3)
                                     image, returning an (H, W) uint8 index map.
- ``color_distance(c1, c2)`` -- Euclidean RGB distance (with sqrt, for callers
                                 that need a true metric rather than a ranking).

Pipeline Context
~~~~~~~~~~~~~~~~
::

    [PIPELINE:PROCESS]  processor.py  -- calls quantize_image_np() on each
                                         12x12 cell block to find dominant
                                         palette colours (fg/bg).
    [PIPELINE:PROCESS]  auto_adjust.py -- calls quantize helpers for whole-
                                          image palette reduction.
    (downstream)        matcher.py     -- receives the fg/bg palette colours
                                          chosen here and renders trial glyphs.

.. note::
   ``ANSI_COLORS`` duplicates the canonical palette defined in ``palette.py``.
   See the TODO below for the planned consolidation.

[PIPELINE:PROCESS]
"""

import math
from typing import Tuple, List
import numpy as np

# ---------------------------------------------------------------------------
# Palette definition
# ---------------------------------------------------------------------------

# [DATA-CONTRACT:PALETTE]
# The standard 16-color ANSI/VGA palette.  Indices 0-7 are the "normal"
# colours; 8-15 are the "bright" variants.  The ordering matches the
# ANSI SGR colour codes used by the Asciicker engine's XP format.
#
# TODO(PIPELINE-FIX): This table duplicates palette.py:PALETTE_RGB.
#   Consolidate to a single source of truth so palette tweaks cannot drift.
ANSI_COLORS = [
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
    (255, 0, 255),    # 13: Bright Magenta
    (0, 255, 255),    # 14: Bright Cyan
    (255, 255, 255)   # 15: Bright White
]

# [DATA-CONTRACT:PALETTE]
# Pre-calculate numpy palette for fast vectorised lookup.
# int32 avoids overflow when squaring differences (max diff 255,
# 255^2 * 3 = 195_075 fits comfortably in int32).
PALETTE_NP = np.array(ANSI_COLORS, dtype=np.int32)

def quantize_pixel(rgb: Tuple[int, int, int]) -> Tuple[int, int, int]:
    """
    Quantize a single pixel (r, g, b) to the nearest palette color.

    Retained for backward compatibility and single-pixel use cases
    (e.g. transparency-key comparisons).  For bulk work, prefer
    :func:`quantize_image_np`.

    Args:
        rgb: Input color as an ``(R, G, B)`` tuple with values in ``[0, 255]``.

    Returns:
        The closest (R, G, B) tuple from ``ANSI_COLORS``.
    """
    idx = get_closest_color_index(rgb)
    return ANSI_COLORS[idx]

def get_closest_color_index(rgb: Tuple[int, int, int]) -> int:
    """
    Find the index of the closest color in ``ANSI_COLORS`` for a single pixel.

    Uses a simple linear scan over the 16-entry palette with squared
    Euclidean distance as the metric (see WHY note below).

    Args:
        rgb: Input color as an ``(R, G, B)`` tuple with values in ``[0, 255]``.

    Returns:
        Integer index in ``[0, 15]``.
    """
    # [PIPELINE:PROCESS]
    r, g, b = rgb
    best_dist = float('inf')
    best_idx = 0

    for i, (pr, pg, pb) in enumerate(ANSI_COLORS):
        # WHY squared Euclidean distance (no sqrt):
        #   We only need the *ranking* of distances, not their absolute
        #   values. Because sqrt is monotonically increasing, omitting it
        #   preserves the ordering while saving ~16 sqrt calls per pixel.
        dist = (r - pr)**2 + (g - pg)**2 + (b - pb)**2
        if dist < best_dist:
            best_dist = dist
            best_idx = i

    return best_idx

def quantize_image_np(image_np: np.ndarray) -> np.ndarray:
    """
    Vectorised quantisation of an entire image to palette indices.

    Computes the squared Euclidean distance from every pixel to all 16
    palette entries in a single broadcast operation, then picks the argmin.

    Args:
        image_np: NumPy array of shape ``(H, W, 3)`` with uint8 RGB values.

    Returns:
        NumPy uint8 array of shape ``(H, W)`` where each element is a
        palette index in ``[0, 15]``.

    [PIPELINE:PROCESS]
    """
    # Reshape image to (N, 3) where N = H*W
    h, w, c = image_np.shape
    pixels = image_np.reshape(-1, 3).astype(np.int32)

    # WHY broadcast arithmetic instead of a Python loop:
    #   A 12x12 cell has 144 pixels; a full sprite frame can have thousands.
    #   Broadcasting the (N, 1, 3) - (1, 16, 3) subtraction into a single
    #   NumPy operation is ~50-100x faster than a per-pixel Python loop.

    # Calculate distances to all palette colors efficiently
    # shapes: pixels (N, 3), palette (16, 3)
    # We want (N, 16) distance matrix
    # dist = sum((pixel - color)^2)

    # Broadcast subtraction: (N, 1, 3) - (1, 16, 3) -> (N, 16, 3)
    diff = pixels[:, np.newaxis, :] - PALETTE_NP[np.newaxis, :, :]

    # WHY squared distance (no sqrt):
    #   Same rationale as get_closest_color_index -- argmin is invariant
    #   under monotonic transforms, so the sqrt is unnecessary work.
    # Square and sum over color channels: (N, 16)
    dist_sq = np.sum(diff**2, axis=2)

    # Find argmin for each pixel: (N,)
    nearest_indices = np.argmin(dist_sq, axis=1)

    # Reshape back to (H, W)
    return nearest_indices.reshape(h, w).astype(np.uint8)

def color_distance(c1: Tuple[int, int, int], c2: Tuple[int, int, int]) -> float:
    """
    Calculate the true Euclidean distance between two RGB colors.

    Unlike the internal ranking helpers (which skip sqrt), this returns
    the actual distance for callers that need a real metric value
    (e.g. threshold comparisons or human-readable diagnostics).

    Args:
        c1: First color as ``(R, G, B)``.
        c2: Second color as ``(R, G, B)``.

    Returns:
        Floating-point Euclidean distance in RGB space.
    """
    # WHY full sqrt here (unlike the ranking helpers):
    #   Some callers compare against an absolute tolerance threshold,
    #   so they need the true metric, not the squared proxy.
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(c1, c2)))
