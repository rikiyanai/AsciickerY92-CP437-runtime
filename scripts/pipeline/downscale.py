"""
Image downscaling algorithms for sprite sheet generation.

Architecture
------------
This module provides the downscaling layer that sits between raw image input
and the grid-aligned processing stage.  Sprite sheets rendered from 3D tools
(Blender turntables, AI generators) arrive at arbitrary resolutions and must
be resized to exact multiples of the 12px cell grid before slicing and
quantization can proceed.

Four downscaling algorithms are exposed:

- **nearest** -- Nearest-neighbor interpolation.  Preserves hard pixel edges;
  ideal for pixel-art sources or previews where sub-pixel blending is unwanted.
- **box** -- Box/area averaging.  Each target pixel is the mean of the source
  pixels that map into it.  Produces smoother results than nearest; good
  default for photographic or 3D-rendered inputs.
- **area** -- Alias for box.  PIL exposes ``Image.BOX`` (not ``Image.AREA``)
  for area-averaging, so this alias keeps the public API intuitive.
- **block-majority** -- Mode-based downsampling via ``BlockMajoritySampler``.
  Picks the most frequent color in each source block.  Designed for images
  that have already been palette-quantized, where averaging would create
  off-palette colors.

Additionally, ``resize_with_letterbox()`` scales an image to fit within a
target canvas while preserving aspect ratio, padding the remainder with a
configurable background color.

Key Exports
~~~~~~~~~~~
- ``ImageResizer``           -- Facade mapping algorithm names to resize calls.
- ``BlockMajoritySampler``   -- Mode-based categorical downsampler.
- ``resize_with_letterbox``  -- Aspect-preserving resize with padding.

Pipeline Context
~~~~~~~~~~~~~~~~
::

    [PIPELINE:GENERATE] -> **[PIPELINE:SLICE / DOWNSCALE]** -> [PIPELINE:PROCESS] -> [PIPELINE:ASSEMBLE]

Downscaling runs *before* slicing when the source image is larger than the
template expects.  ``AssetPipeline.validate_and_downscale()`` in pipeline.py
calls ``ImageResizer.resize()`` after ``GridValidator`` detects an oversize
input.

Tags: [PIPELINE:SLICE] [PIPELINE:PROCESS] [DEPENDENCY:PIL] [DATA-CONTRACT:PALETTE] [DATA-CONTRACT:XP]
"""

from PIL import Image
from typing import Literal


# ==============================
# ImageResizer
# ==============================


class ImageResizer:
    """
    Facade that maps human-readable algorithm names to PIL resize calls.

    Supported algorithms:
    - nearest: Use NEAREST neighbor (pixel-art preservation)
    - box: Use BOX filter (area averaging)
    - area: Alias for box (PIL.Image.AREA does not exist, BOX does area-averaging)
    - block-majority: Mode-based downsampling (uses BlockMajoritySampler)

    For standard algorithms (nearest, box, area), this returns:
    image.resize(target_size, filter) with the appropriate PIL filter constant.

    For block-majority, delegates to BlockMajoritySampler.resize_block_majority().
    """

    # [DEPENDENCY:PIL] -- Maps algorithm strings to PIL resampling filter constants.
    # WHY: PIL's BOX filter performs true area-averaging (each output pixel =
    # mean of its source footprint).  There is no Image.AREA constant; BOX
    # is the correct equivalent.  NEAREST is the only filter that never
    # introduces new colors, which matters for already-quantized sprites.
    DEFAULT_FILTERS = {
        "nearest": Image.NEAREST,
        "box": Image.BOX,  # BOX is area-averaging per PIL documentation
        "area": Image.BOX,  # Map "area" to BOX (no Image.AREA constant)
    }

    def __init__(
        self, algorithm: Literal["nearest", "box", "area", "block-majority"] = "nearest"
    ):
        """
        Initialize ImageResizer with algorithm selection.

        Args:
            algorithm: Downscaling algorithm name.  One of "nearest", "box",
                       "area", or "block-majority".
        """
        self.algorithm = algorithm

        # Set filter for standard algorithms
        if algorithm in self.DEFAULT_FILTERS:
            self.filter = self.DEFAULT_FILTERS[algorithm]
        elif algorithm == "block-majority":
            self.filter = None  # Handled by BlockMajoritySampler
        else:
            # WHY LANCZOS fallback: Unknown algorithm names should not crash
            # the pipeline silently.  LANCZOS is a safe high-quality default,
            # though it *will* introduce new colors (problematic for quantized
            # images).  Callers should always pass a known algorithm.
            # TODO(PIPELINE-FIX): Consider raising ValueError instead of
            # silently falling back to LANCZOS for unknown algorithms.
            self.filter = Image.LANCZOS

    def resize(self, image: Image.Image, target_size: tuple[int, int]) -> Image.Image:
        """
        Resize image to target_size using the configured algorithm.

        The target_size must be an exact multiple of the 12px cell grid for
        downstream slicing to work correctly.  This method does NOT enforce
        that constraint -- the caller (typically ``GridValidator``) must
        ensure alignment before calling.

        Args:
            image: PIL Image to resize.
            target_size: (width, height) tuple.  Must be cell-aligned for
                         pipeline correctness.

        Returns:
            Resized PIL Image in the same mode as the input.
        """
        # [PIPELINE:SLICE] -- Entry point: image dimensions change here.
        if self.algorithm == "block-majority":
            # Delegate to BlockMajoritySampler
            from .downscale import BlockMajoritySampler

            sampler = BlockMajoritySampler()
            return sampler.resize_block_majority(image, target_size)

        # [DEPENDENCY:PIL] -- Standard PIL resize with the pre-selected filter.
        # Standard algorithm
        return image.resize(target_size, self.filter)


# ==============================
# BlockMajoritySampler
# ==============================


class BlockMajoritySampler:
    """
    Mode-based downsampling for categorical color images.

    Divides the source image into non-overlapping blocks and picks the most
    frequent (mode) color per block.  This preserves palette membership:
    averaging two palette colors would produce an off-palette color, but
    the mode is always a color that already exists in the source.

    Uses ``scipy.stats.mode`` for per-block frequency counting and
    ``skimage.measure.block_reduce`` for the tiling loop.

    Note: Transparent pixels (magenta RGB 255,0,255) are NOT excluded from
    mode calculation yet -- if the block is majority-magenta, the output
    pixel will be magenta, which is correct for transparency but could
    produce artifacts when magenta is only *slightly* dominant.
    """

    def resize_block_majority(
        self, image: Image.Image, target_size: tuple[int, int]
    ) -> Image.Image:
        """
        Resize image using block-majority (statistical mode) downsampling.

        Each output pixel is the most frequent color in its corresponding
        source block.  Falls back to NEAREST for very small scale factors
        where blocks would be < 2px (mode of a single pixel is meaningless).

        Args:
            image: PIL Image to resize (RGB or grayscale).
            target_size: (width, height) tuple for the output.

        Returns:
            Resized PIL Image.
        """
        import numpy as np
        from scipy.stats import mode
        from skimage.measure import block_reduce

        # [PIPELINE:SLICE] -- Compute block dimensions from source/target ratio.
        # WHY integer truncation: block_reduce requires integer block sizes.
        # Non-integer ratios mean the last row/column of source pixels may be
        # silently dropped.  This is acceptable for downscale ratios > 2x but
        # can cause 1px misalignment at small ratios.
        # TODO(PIPELINE-FIX): Handle non-integer scale ratios explicitly --
        # either pad the source or use a fractional-aware reduction.
        target_w, target_h = target_size
        scale_w = image.width / target_w
        scale_h = image.height / target_h
        block_w = int(scale_w)
        block_h = int(scale_h)

        # WHY fallback to NEAREST: With block size < 2, mode() returns the
        # single pixel value, making block-majority equivalent to nearest
        # neighbor but with much higher overhead from scipy/skimage.
        # Fall back to NEAREST for small scale factors (block < 2px)
        if block_w < 2 or block_h < 2:
            return image.resize(target_size, Image.NEAREST)

        # [DEPENDENCY:PIL] -- Convert PIL Image to numpy for scipy processing.
        # Convert PIL Image to numpy array
        image_array = np.array(image)

        # Handle RGB/RGBA images - need to reduce per channel
        if len(image_array.shape) == 2:
            # Grayscale image: (height, width)
            block_size_val = (block_h, block_w)

            def mode_wrapper(x, axis=None):
                """Wrapper for scipy.stats.mode to work with block_reduce."""
                return mode(x, axis=axis, keepdims=False)[0]

            resized = block_reduce(
                image_array, block_size=block_size_val, func=mode_wrapper
            )
        else:
            # RGB/RGBA image: (height, width, channels)
            # WHY per-channel reduction: block_reduce with a 3D block size
            # would collapse channels together.  Processing each channel
            # independently preserves the per-channel mode, which keeps
            # palette colors intact when R, G, B modes coincide.
            # [DATA-CONTRACT:PALETTE] -- Per-channel mode preserves palette
            # membership only when the most frequent color dominates all
            # three channels.  Mixed-dominance blocks can produce off-palette
            # output.  This is a known limitation.
            # TODO(PIPELINE-FIX): Consider computing mode over packed RGB
            # tuples (e.g., R*65536 + G*256 + B) to guarantee palette
            # membership in the output.
            channels = image_array.shape[2]
            resized_channels = []
            block_size_val = (block_h, block_w)

            def mode_wrapper(x, axis=None):
                """Wrapper for scipy.stats.mode to work with block_reduce."""
                return mode(x, axis=axis, keepdims=False)[0]

            for c in range(channels):
                channel = image_array[:, :, c]
                reduced = block_reduce(
                    channel, block_size=block_size_val, func=mode_wrapper
                )
                resized_channels.append(reduced)

            # Stack channels back together
            resized = np.stack(resized_channels, axis=2)

        # [DEPENDENCY:PIL] -- Convert numpy result back to PIL Image.
        # Convert back to PIL Image
        return Image.fromarray(resized)

        # TODO: Filter magenta pixels (255,0,255) from mode calculation for transparency support
        # TODO: Use palette.is_transparent() helper to identify transparent pixels


# ==============================
# resize_with_letterbox
# ==============================


def resize_with_letterbox(
    image: Image.Image, target_size: tuple[int, int], background_color: int = 0
) -> Image.Image:
    """
    Resize image to fit within target_size, preserving aspect ratio, with padding.

    Uses the letterboxing pattern: scale the image to the largest size that
    fits within the target bounds, then center-paste it onto a solid-color
    canvas.  The background color defaults to black (0), which is safe for
    sprites where magenta (255,0,255) marks transparency.

    Called by ``AssetPipeline.validate_and_downscale()`` when an oversize
    source image needs to be brought down to template dimensions without
    distorting the sprite's proportions.

    Args:
        image: PIL Image to resize.
        target_size: (width, height) tuple for the output canvas.
        background_color: Background fill for padding bars.
                          0 = black, 255 = white, or an (R,G,B) tuple.

    Returns:
        PIL Image of exactly target_size, with the source centered inside.
    """
    target_w, target_h = target_size

    # [PIPELINE:SLICE] -- Compute the uniform scale factor that fits the
    # source inside the target without cropping.
    # WHY min(): Scaling by the smaller ratio guarantees the image fits
    # entirely within the target bounds.  The other axis gets padded.
    ratio = min(target_w / image.width, target_h / image.height)
    new_w = int(image.width * ratio)
    new_h = int(image.height * ratio)

    # WHY NEAREST: Pixel-art sprites should not be anti-aliased during
    # resize.  NEAREST preserves sharp cell boundaries that downstream
    # slicing depends on.
    # [DEPENDENCY:PIL] -- PIL resize with NEAREST filter.
    # Resize image to ratio-preserving dimensions
    # Use NEAREST to preserve pixel art style
    resized = image.resize((new_w, new_h), Image.NEAREST)

    # Create canvas with target_size
    # TODO(PIPELINE-FIX): When background_color is an int, the canvas is
    # filled with (color, color, color) which maps 0 -> black correctly,
    # but 255 -> white, not magenta.  If transparency is needed in the
    # letterbox bars, callers must pass (255, 0, 255) explicitly.
    if isinstance(background_color, tuple):
        canvas = Image.new(image.mode, (target_w, target_h), background_color)
    else:
        canvas = Image.new(image.mode, (target_w, target_h), (background_color,) * 3)

    # Center paste: calculate padding for equal left/right or top/bottom
    paste_x = (target_w - new_w) // 2
    paste_y = (target_h - new_h) // 2

    # Paste resized image onto canvas
    canvas.paste(resized, (paste_x, paste_y))

    return canvas


# Export symbols
__all__ = ["ImageResizer", "BlockMajoritySampler", "resize_with_letterbox"]
