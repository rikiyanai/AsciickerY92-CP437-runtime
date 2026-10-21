"""
Sprite image-to-glyph processor for the Asciicker asset pipeline.

Architecture
------------
This module sits at Stage 3 of the 4-stage sprite generation pipeline:

    [PIPELINE:GENERATE] -> [PIPELINE:SLICE] -> **[PIPELINE:PROCESS]** -> [PIPELINE:ASSEMBLE]

SpriteProcessor receives a single *frame* (PIL Image, already sliced from a
sprite sheet) and converts it into a 2-D grid of (glyph, fg_color, bg_color)
tuples -- the intermediate representation consumed by XPAssembler (Stage 4).

Processing algorithm per 12x12 pixel cell:
  1. Detect transparency via magenta key (255, 0, 255) with fuzzy threshold.
  2. Quantize non-transparent pixels to the 16-color ANSI palette.
  3. Select the two most-frequent palette colors as foreground / background.
  4. Match the cell against all 256 CP437 glyph bitmaps (brute-force SSD).

Exports
-------
- ``SpriteProcessor`` -- stateful processor (holds a GlyphMatcher instance).

Callers
-------
- ``scripts/generate_demo_asset.py`` imports SpriteProcessor directly.
- The main ``AssetPipeline.run()`` in pipeline.py currently uses the *separate*
  ``processor_core.ImageProcessor`` instead (single-dominant-color approach).

Data Contracts
--------------
- Input:  PIL.Image (RGB, dimensions = AssetDef.size * 12 px).
- Output: ``List[List[Tuple[glyph_idx, fg_rgb, bg_rgb]]]``  -- row-major grid.

Tags: [PIPELINE:PROCESS] [DATA-CONTRACT:XP] [DATA-CONTRACT:CP437]
      [DATA-CONTRACT:PALETTE] [DEPENDENCY:PIL]
"""

from typing import List, Tuple
from PIL import Image
import numpy as np

# [DEPENDENCY:PIL] -- Pillow for image loading/resizing/mode conversion.
# [DATA-CONTRACT:PALETTE] -- ANSI_COLORS is the canonical 16-color palette
#   shared with quantizer.py, palette.py, and xp_tool.py.
from .quantizer import quantize_pixel, quantize_image_np, ANSI_COLORS
# [DATA-CONTRACT:CP437] -- GlyphMatcher loads assets/fonts/cp437_12x12.png at init.
from .matcher import GlyphMatcher
from .schemas import AssetDef
from .palette import is_transparent, MAGENTA_RGB, make_transparency_mask
from scripts.pipeline.service.constants import CELL_SIZE, DEFAULT_BG_TOLERANCE


# [PIPELINE:PROCESS] -- Entry point for Stage 3 of the sprite pipeline.
class SpriteProcessor:
    """Converts raster sprite frames into CP437 glyph grids.

    This is the *two-color decomposition* processor: each 12x12 cell is
    reduced to a foreground color, a background color, and a CP437 glyph
    index that best approximates the pixel pattern using those two colors.

    Contrast with ``processor_core.ImageProcessor``, which uses a simpler
    single-dominant-color approach and is what ``pipeline.py`` currently
    wires into ``AssetPipeline.run()``.

    Attributes:
        matcher: GlyphMatcher instance pre-loaded with the CP437 font atlas.
    """

    def __init__(self, cell_size: int = CELL_SIZE):
        """Initialize the processor with a pre-loaded CP437 glyph matcher.

        Args:
            cell_size: Glyph cell dimension in pixels (width and height).
                       Defaults to CELL_SIZE from constants (12).
                       Extensibility point for future per-job override.

        Loads the CP437 font atlas from disk and caches all 256
        glyph bitmaps for use in subsequent match_block() calls.
        """
        self.cell_size = cell_size
        # [DATA-CONTRACT:CP437] -- GlyphMatcher loads the font atlas matching
        # the configured cell_size and caches all 256 glyph bitmaps as
        # normalised float arrays.
        self.matcher = GlyphMatcher(char_w=cell_size, char_h=cell_size)

    # [DATA-CONTRACT:XP] -- The return type is the grid format expected by
    # XPAssembler.assemble(): List[rows] of List[cols] of (glyph, fg, bg).
    def process_image(
        self, image: Image.Image, asset_def: AssetDef
    ) -> List[List[Tuple[int, Tuple[int, int, int], Tuple[int, int, int]]]]:
        """Convert an RGB image into a 2-D grid of (glyph, fg, bg) cells.

        The image is divided into a grid of cell_size x cell_size pixel cells
        (the CP437 glyph size). Each cell undergoes: transparency detection ->
        palette quantization -> two-color decomposition -> glyph matching.

        Args:
            image: Source PIL Image (any mode -- will be converted to RGB).
            asset_def: Defines target dimensions in character cells via
                ``asset_def.size`` (width_chars, height_chars).

        Returns:
            Row-major grid ``[y][x]`` where each element is a tuple of
            ``(glyph_index, fg_rgb, bg_rgb)``.
            - glyph_index: CP437 code point 0-255 (32 = space = transparent).
            - fg_rgb / bg_rgb: Tuple[int,int,int] from the 16-color ANSI palette.

        Raises:
            ValueError: If the image cannot be converted to RGB mode, or
                if the resulting numpy array has != 3 channels (RGBA images
                must be resolved upstream by align_background_to_magenta).
            IndexError: If ``asset_def.size`` produces a grid that extends
                beyond the (possibly resized) image boundaries.
        """
        # 1. Resize/Scale check
        # WHY: asset_def.size is in *character cells* (each cell_size px).
        # The image must match exactly so that the cell grid aligns.
        # TODO(PIPELINE-FIX): Nearest-neighbour resize can destroy thin features
        # on large downscale factors. Consider LANCZOS or area-averaging for
        # images more than 2x oversized.
        if asset_def.size == (0, 0):
            raise ValueError(
                "AssetDef.size is (0,0) sentinel — size must be set "
                "explicitly before processing. The pipeline should derive "
                "size from sliced frame dimensions (frame.width // CELL_SIZE)."
            )
        cs = self.cell_size
        target_w_px = asset_def.size[0] * cs
        target_h_px = asset_def.size[1] * cs

        if image.width != target_w_px or image.height != target_h_px:
            # Simple resize for now - Nearest Neighbor to preserve pixel art
            image = image.resize((target_w_px, target_h_px), Image.Resampling.NEAREST)

        if image.mode != "RGB":
            image = image.convert("RGB")

        width_chars = asset_def.size[0]
        height_chars = asset_def.size[1]

        output_grid = []

        # Convert image to numpy for faster access.
        # WHY numpy up-front: iterating per-pixel in pure Python is ~100x
        # slower than vectorised numpy operations. The bulk of the work
        # (quantize_image_np, transparency mask) runs on the full array.
        pixels = np.array(image)
        
        # CHANGED (Phase 4, BG-01): Alpha must be resolved upstream by
        # align_background_to_magenta(). If RGBA leaks here, it's a pipeline bug.
        if pixels.shape[2] != 3:
            raise ValueError(
                f"Processor received {pixels.shape[2]}-channel image "
                f"(expected 3-channel RGB). Alpha channel should have been "
                f"resolved by align_background_to_magenta() before reaching "
                f"the processor. Check --bg-mode setting or pipeline flow."
            )
        pixels_rgb = pixels
        
        # [DATA-CONTRACT:PALETTE] -- Magenta (255,0,255) is the engine's
        # transparency key. Uses canonical L1 metric from palette.py
        # (unified in Phase 4, BG-03). Tolerance flows from BackgroundSpec.
        bg_tolerance = DEFAULT_BG_TOLERANCE  # matches BackgroundSpec.tolerance
        bg_spec = getattr(asset_def, "background", None)
        if bg_spec and hasattr(bg_spec, "tolerance"):
            bg_tolerance = bg_spec.tolerance
        is_trans_mask = make_transparency_mask(pixels_rgb, tolerance=bg_tolerance)

        # WHY row-major (y-outer, x-inner): the output grid is indexed as
        # grid[y][x] to match the XPAssembler's expectation and the .xp
        # file's visual row ordering (top row first).
        for y in range(height_chars):
            row_data = []
            for x in range(width_chars):
                # Extract cell_size x cell_size block
                px_x = x * cs
                px_y = y * cs

                # Block slices
                block = pixels_rgb[px_y : px_y + cs, px_x : px_x + cs]
                trans_block = is_trans_mask[px_y : px_y + cs, px_x : px_x + cs]

                # Check for transparency early
                transparent_count = np.sum(trans_block)

                # WHY threshold: a cell = cs*cs pixels. If >85% of pixels
                # are magenta, the cell is likely intended to be transparent.
                # Anti-aliasing during downscaling often leaves non-magenta
                # fringes that fail a strict 100% threshold check.
                transparency_threshold = int(cs * cs * 0.85)
                if transparent_count >= transparency_threshold:  # >85% coverage
                    row_data.append(
                        (32, (0, 0, 0), MAGENTA_RGB)
                    )  # Space with transparency key
                    continue

                # 2. Determine Dominant Colors
                # Vectorized quantization of the block
                # Only quantize non-transparent pixels
                
                # We can quantize the whole block efficiently
                q_indices = quantize_image_np(block)
                
                # Flatten for counting
                flat_indices = q_indices.flatten()
                flat_trans = trans_block.flatten()
                
                # Filter out transparent pixels, but keep magenta-quantized
                # pixels for background selection. We'll avoid picking magenta
                # as FG unless it's the only color present.
                non_trans_mask = ~flat_trans
                valid_indices = flat_indices[non_trans_mask]
                
                if len(valid_indices) == 0:
                    row_data.append((32, (0, 0, 0), MAGENTA_RGB))
                    continue

                # WHY two-color decomposition: CP437 glyphs are 1-bit bitmaps,
                # so each cell can display exactly two colors (fg where the glyph
                # pixel is lit, bg where it is not). We pick the two most frequent
                # quantised palette colours as fg and bg, then let the glyph
                # matcher decide which bitmap best separates them spatially.
                counts = np.bincount(valid_indices, minlength=16)

                # Get indices sorted by count (descending)
                sorted_indices = np.argsort(counts)[::-1]

                # Remove indices with 0 count
                sorted_indices = [idx for idx in sorted_indices if counts[idx] > 0]

                if not sorted_indices:
                    row_data.append((32, (0, 0, 0), MAGENTA_RGB))
                    continue

                # Prefer non-magenta for FG if available.
                fg_idx = next((idx for idx in sorted_indices if idx != 13), 13)
                # BG is the next most frequent color (can be magenta).
                bg_idx = next((idx for idx in sorted_indices if idx != fg_idx), 13)

                fg = ANSI_COLORS[fg_idx]
                bg = ANSI_COLORS[bg_idx]

                # Ensure contrast (If FG=BG it's a solid block)
                if fg == bg:
                    bg = MAGENTA_RGB

                # 3. Match Glyph
                # [PIPELINE:PROCESS] -> [DATA-CONTRACT:CP437]
                # GlyphMatcher.match_block renders each of the 256 CP437 glyphs
                # as (pattern * fg + (1-pattern) * bg) and picks the one with
                # lowest sum-of-squared-differences against the raw RGB block.
                # WHY raw block (not quantised): matching against the original
                # pixels preserves sub-palette gradients that would be lost if
                # we compared quantised colors to a quantised rendering.
                glyph, score = self.matcher.match_block(block, fg, bg)

                row_data.append((glyph, fg, bg))
            output_grid.append(row_data)

        return output_grid
