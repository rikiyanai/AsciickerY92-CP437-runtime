"""
Glyph matching: finds the best CP437 glyph for a given pixel block.

Architecture
------------
This module loads a CP437 font bitmap (a 16x16 grid of 12x12-pixel glyphs,
covering all 256 code points) and provides a brute-force matcher that,
given a 12x12 RGB block and pre-determined foreground/background colors,
selects the glyph whose rendered appearance is closest to the original
pixels.

The matching algorithm works as follows:

1. Each of the 256 CP437 glyphs is stored as a normalised ``(12, 12)``
   float array where ``1.0`` = fully foreground and ``0.0`` = fully
   background (derived from the font bitmap's grayscale luminance).
2. For a candidate glyph, we *render* it by linear-blending:
   ``rendered = pattern * fg_rgb + (1 - pattern) * bg_rgb``.
3. We compute the sum of squared pixel differences between the rendered
   image and the target block.  The glyph with the lowest total wins.

Key Exports
~~~~~~~~~~~
- ``GlyphMatcher``  -- Stateful matcher that holds the loaded font atlas.
    - ``match_block(block, fg_rgb, bg_rgb)`` -- Returns ``(glyph_index, score)``.

Pipeline Context
~~~~~~~~~~~~~~~~
::

    [PIPELINE:PROCESS]  processor.py calls GlyphMatcher.match_block() once
                        per non-transparent cell after quantizer.py has
                        determined the two dominant palette colours (fg/bg).
    [DATA-CONTRACT:CP437]  The font atlas ``assets/fonts/cp437_12x12.png`` is the
                           reference bitmap; its grid layout (16 cols x 16 rows,
                           12x12 px per glyph) is load-bearing.
    [DEPENDENCY:PIL]    Pillow is used to load and convert the font bitmap.

[PIPELINE:PROCESS]
"""

import os
import math
from typing import Tuple, List, Dict
# [DEPENDENCY:PIL] -- Pillow is used only at font-load time (Image.open).
from PIL import Image
import numpy as np

# ---------------------------------------------------------------------------
# Font atlas path
# ---------------------------------------------------------------------------

# [DATA-CONTRACT:CP437]
# Default path to the reference CP437 font bitmap.
# The bitmap must be a 16-column x 16-row grid of monospaced glyphs.
# TODO(PIPELINE-FIX): The 12x12 cell size is hardcoded in several places
#   (char_w, char_h, and processor.py's block slicing). If the font atlas
#   resolution ever changes, all three must be updated in lockstep.
FONT_PATH = os.path.join(os.path.dirname(__file__), "../../assets/fonts/cp437_12x12.png")

class GlyphMatcher:
    """
    Loads a CP437 font atlas and matches 12x12 RGB blocks to glyphs.

    The matcher pre-computes normalised glyph patterns at construction
    time so that ``match_block`` only needs to perform the rendering
    comparison loop.

    Attributes:
        char_w:  Glyph cell width in pixels (12).
        char_h:  Glyph cell height in pixels (12).
        glyphs:  Dict mapping CP437 index (0-255) to a ``(12, 12)``
                 float ndarray normalised to ``[0.0, 1.0]``.

    [DATA-CONTRACT:CP437]
    """

    def __init__(self, font_path: str = FONT_PATH, char_w: int = 12, char_h: int = 12):
        """
        Load and parse the CP437 font atlas.

        Args:
            font_path: Filesystem path to the font bitmap PNG.
                       Falls back to ``assets/fonts/cp437_12x12.png`` relative
                       to the working directory if the primary path is
                       missing.
            char_w: Glyph cell width in pixels (default 12). Must match
                    the font atlas grid pitch.
            char_h: Glyph cell height in pixels (default 12). Must match
                    the font atlas grid pitch.
        """
        self.char_w = char_w
        self.char_h = char_h
        self.glyphs = {}  # idx -> numpy array (char_h, char_w)
        self._load_font(font_path)

    def _load_font(self, path: str):
        """
        Parse the font atlas PNG into per-glyph normalised float arrays.

        The atlas is expected to be a regular grid: ``cols = width // 12``,
        ``rows = height // 12``, covering CP437 indices 0-255 in row-major
        order (index ``i`` is at column ``i % cols``, row ``i // cols``).

        Args:
            path: Filesystem path to the CP437 font atlas PNG.  If the
                  file does not exist, a fallback relative path is tried.

        Raises:
            FileNotFoundError: If neither the primary ``path`` nor the
                fallback ``assets/fonts/cp437_12x12.png`` can be located.

        [DEPENDENCY:PIL]
        [DATA-CONTRACT:CP437]
        """
        if not os.path.exists(path):
            # Fallback path logic
            path = "assets/fonts/cp437_12x12.png"
            if not os.path.exists(path):
                raise FileNotFoundError(f"Could not find font at {path}")

        # WHY grayscale conversion ("L"):
        #   The font bitmap may be RGB or RGBA, but glyph shapes are
        #   monochrome.  Converting to grayscale gives us a single
        #   luminance channel that directly encodes coverage (0 = bg,
        #   255 = fg).  This avoids dealing with per-channel differences
        #   in anti-aliased font bitmaps.
        img = Image.open(path).convert("L") # Grayscale
        width, height = img.size
        cols = width // self.char_w
        rows = height // self.char_h

        # Validate atlas dimensions match requested cell size.
        # A 16x16 CP437 atlas should yield exactly 256 glyphs.
        expected_glyphs = 256
        available_glyphs = cols * rows
        if available_glyphs < expected_glyphs:
            import warnings
            warnings.warn(
                f"Font atlas {path} at {width}x{height} with cell size "
                f"{self.char_w}x{self.char_h} yields only {available_glyphs} "
                f"glyphs (expected {expected_glyphs}). Glyph matching will be "
                f"incomplete. Use a font atlas sized for {self.char_w}x{self.char_h} cells.",
                stacklevel=2,
            )

        arr = np.array(img)

        for i in range(256):
            col = i % cols
            row = i // cols
            if row >= rows: break

            x = col * self.char_w
            y = row * self.char_h

            # Slice the glyph (0-255 values, 0=black, 255=white)
            glyph_data = arr[y:y+self.char_h, x:x+self.char_w]

            # WHY normalise to [0.0, 1.0]:
            #   Normalised patterns act as blend weights in match_block's
            #   rendering formula: ``rendered = pattern * fg + (1-pattern) * bg``.
            #   Keeping them as floats avoids repeated int-to-float casts
            #   during the per-glyph comparison loop.
            self.glyphs[i] = glyph_data.astype(float) / 255.0

    def match_block(self, block: np.ndarray, fg_rgb: Tuple[int, int, int], bg_rgb: Tuple[int, int, int]) -> Tuple[int, float]:
        """
        Find the CP437 glyph whose rendered appearance best matches a block.

        For each of the 256 glyphs, the method *renders* a synthetic
        12x12x3 image using ``rendered = pattern * fg + (1 - pattern) * bg``
        and scores it against the target ``block`` via sum-of-squared
        differences.  The glyph with the lowest score wins.

        Args:
            block:  ``(12, 12, 3)`` uint8 ndarray -- the raw RGB pixels
                    from the source image for this cell.
            fg_rgb: Foreground palette color as ``(R, G, B)`` (from quantizer).
            bg_rgb: Background palette color as ``(R, G, B)`` (from quantizer).

        Returns:
            ``(glyph_index, score)`` where ``glyph_index`` is in ``[0, 255]``
            and ``score`` is the total sum-of-squared-differences (lower is
            better).

        [PIPELINE:PROCESS]
        [DATA-CONTRACT:CP437]
        """
        # WHY brute-force over all 256 glyphs:
        #   With only 256 candidates and small 12x12 blocks, the total
        #   work (~256 * 144 * 3 multiplications) completes in ~1-2 ms per
        #   cell on modern hardware.  More complex pruning strategies
        #   (e.g. early exit, spatial hashing) add code complexity for
        #   negligible gain at this scale.

        best_glyph = 0
        min_diff = float('inf')

        # Convert RGB tuple to numpy arrays
        fg = np.array(fg_rgb)
        bg = np.array(bg_rgb)

        # Make block (12, 12, 3) float for vectorised comparison
        target = block.astype(float)

        for idx, pattern in self.glyphs.items():
            # WHY linear blend rendering:
            #   CP437 glyphs are 1-bit patterns but the font bitmap may
            #   contain anti-aliased edges (grayscale values between 0
            #   and 255).  Linear blending ``pattern * fg + (1-pattern) * bg``
            #   correctly models how these sub-pixel coverages would appear
            #   when rendered with the chosen fg/bg colours.

            # Pattern is (12, 12) float 0..1
            # Render: pixel = pattern * fg + (1-pattern) * bg
            # We need to broadcast pattern to (12, 12, 3)

            pat_3d = np.dstack([pattern]*3)
            rendered = pat_3d * fg + (1.0 - pat_3d) * bg

            # WHY sum-of-squared-differences (SSD):
            #   SSD is the standard L2 image-matching metric.  It penalises
            #   large per-pixel errors more than small ones, which visually
            #   favours glyphs that get the overall shape right even if a
            #   few pixels are slightly off.  No sqrt is needed because we
            #   only compare scores against each other (ranking, not absolute).
            # Sum of squared differences
            diff = np.sum((target - rendered) ** 2)

            if diff < min_diff:
                min_diff = diff
                best_glyph = idx

        return best_glyph, min_diff
