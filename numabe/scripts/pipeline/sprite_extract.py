"""
Sprite extraction from sprite sheets via flood-fill segmentation.

Ported from Sprite Extractor (2ghprojects/index.html). Identifies
individual sprites in a sheet by building a binary foreground map
and flood-filling connected components.

Two segmentation strategies (auto-selected based on image mode):
  - **Alpha-based**: pixel alpha > threshold -> foreground.
    Used when image has transparency (RGBA with any alpha < 255).
  - **Color-based**: L1 (Manhattan) RGB distance from bg_color > tolerance -> foreground.
    Used for opaque images. BG color auto-detected from corners or user-specified.

Two extraction modes:
  - **shape**: Only pixels belonging to the flood-filled connected component
    are copied. Rest of the bounding box is transparent.
  - **bounding_box**: The full rectangular bounding box is cropped from source.

Pipeline position: Stage 0 track, runs on raw upload before slicing.

Exports
-------
- ``ExtractedSprite`` -- dataclass with image, bbox, pixel count, mode.
- ``segment_image()``  -- create binary foreground/background map.
- ``flood_fill()``     -- find one connected component from a seed pixel.
- ``extract_sprites()`` -- full pipeline: segment -> flood fill -> extract.

Tags: [PIPELINE:EXTRACT] [FLOW:EXTRACT] [DEPENDENCY:PIL] [DEPENDENCY:NUMPY]
"""

import logging
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set, Tuple

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class ExtractedSprite:
    """One sprite extracted from a sheet."""

    image: Image.Image
    """Cropped sprite image (RGBA)."""

    bbox: Tuple[int, int, int, int]
    """Bounding box in source image: (x, y, width, height)."""

    pixel_count: int
    """Number of foreground pixels in this component."""

    extraction_mode: str
    """'shape' or 'bbox'."""

    source_region: Optional[Set[int]] = field(default=None, repr=False)
    """Set of linear pixel indices in source image (only kept for shape mode)."""


@dataclass
class ComponentBounds:
    """Bounding box + pixel set from a single flood fill."""

    x: int
    y: int
    width: int
    height: int
    pixels: Set[int]
    """Set of linear indices (y * image_width + x) belonging to this component."""


# ---------------------------------------------------------------------------
# Segmentation map creation
# ---------------------------------------------------------------------------

def create_alpha_segmentation_map(pixels: np.ndarray,
                                  alpha_threshold: int = 25) -> np.ndarray:
    """Create binary foreground map from alpha channel.

    Ported from JS ``createAlphaBasedSegmentationMap``.

    Args:
        pixels: (H, W, 4) uint8 RGBA array.
        alpha_threshold: Pixels with alpha > this value are foreground (0-255).

    Returns:
        (H*W,) uint8 array, 1 = foreground, 0 = background.
    """
    h, w = pixels.shape[:2]
    alpha = pixels[:, :, 3].reshape(-1)
    return (alpha > alpha_threshold).astype(np.uint8)


def create_color_segmentation_map(pixels: np.ndarray,
                                  bg_colors: Iterable[Tuple[int, int, int]],
                                  color_tolerance: float = 30.0) -> np.ndarray:
    """Create binary foreground map from color distance to background.

    Ported from JS ``createColorBasedSegmentationMap``.

    Args:
        pixels: (H, W, 3) or (H, W, 4) uint8 array.
        bg_colors: Background colors as iterable of (R, G, B). Pixel is
            foreground if farther than ``color_tolerance`` from every candidate.
        color_tolerance: L1 (Manhattan) distance threshold; pixels with
            distance > tolerance from all bg_colors are foreground.

    Returns:
        (H*W,) uint8 array, 1 = foreground, 0 = background.
    """
    rgb = pixels[:, :, :3].reshape(-1, 3).astype(np.float32)
    bg = np.array(list(bg_colors), dtype=np.float32)
    if bg.size == 0:
        raise ValueError("bg_colors must contain at least one RGB tuple")
    deltas = rgb[:, np.newaxis, :] - bg[np.newaxis, :, :]
    dists = np.sum(np.abs(deltas), axis=2)
    min_dist = np.min(dists, axis=1)
    return (min_dist > color_tolerance).astype(np.uint8)


# ---------------------------------------------------------------------------
# Flood fill (stack-based, 4-way connectivity)
# ---------------------------------------------------------------------------

def flood_fill(segmentation_map: np.ndarray,
               visited: np.ndarray,
               start_x: int, start_y: int,
               width: int, height: int) -> ComponentBounds:
    """Find one connected component via stack-based 4-way flood fill.

    Directly ported from JS ``floodFill`` function.

    Args:
        segmentation_map: Flat (H*W,) uint8 array, 1 = foreground.
        visited: Flat (H*W,) uint8 array, mutated in place.
        start_x, start_y: Seed pixel coordinates.
        width, height: Image dimensions.

    Returns:
        ComponentBounds with bounding box and pixel index set.
    """
    stack = [(start_x, start_y)]
    min_x, max_x = start_x, start_x
    min_y, max_y = start_y, start_y
    sprite_pixels: Set[int] = set()

    while stack:
        x, y = stack.pop()

        if x < 0 or x >= width or y < 0 or y >= height:
            continue

        idx = y * width + x
        if visited[idx] or not segmentation_map[idx]:
            continue

        visited[idx] = 1
        sprite_pixels.add(idx)

        if x < min_x:
            min_x = x
        if x > max_x:
            max_x = x
        if y < min_y:
            min_y = y
        if y > max_y:
            max_y = y

        # 4-way neighbors (matching JS source exactly)
        stack.append((x + 1, y))
        stack.append((x - 1, y))
        stack.append((x, y + 1))
        stack.append((x, y - 1))

    return ComponentBounds(
        x=min_x,
        y=min_y,
        width=max_x - min_x + 1,
        height=max_y - min_y + 1,
        pixels=sprite_pixels,
    )


# ---------------------------------------------------------------------------
# Sprite extraction (two modes)
# ---------------------------------------------------------------------------

def _extract_sprite_shape(source: np.ndarray,
                          bounds: ComponentBounds,
                          source_width: int) -> Image.Image:
    """Extract sprite using exact shape — only flood-filled pixels are copied.

    Ported from JS ``extractSpriteWithShape``.

    Args:
        source: (H, W, 4) uint8 RGBA source image array.
        bounds: Component bounding box and pixel set.
        source_width: Width of the source image.

    Returns:
        RGBA PIL Image with only the component's pixels; rest is transparent.
    """
    target = np.zeros((bounds.height, bounds.width, 4), dtype=np.uint8)

    for y in range(bounds.height):
        for x in range(bounds.width):
            source_idx = (bounds.y + y) * source_width + (bounds.x + x)
            if source_idx in bounds.pixels:
                sy = bounds.y + y
                sx = bounds.x + x
                target[y, x] = source[sy, sx]

    return Image.fromarray(target, "RGBA")


def _extract_sprite_bbox(source: np.ndarray,
                         bounds: ComponentBounds) -> Image.Image:
    """Extract sprite using rectangular bounding box crop.

    Ported from JS ``extractSpriteWithBoundingBox``.

    Args:
        source: (H, W, 4) uint8 RGBA source image array.
        bounds: Component bounding box.

    Returns:
        RGBA PIL Image of the cropped bounding box region.
    """
    crop = source[bounds.y:bounds.y + bounds.height,
                  bounds.x:bounds.x + bounds.width].copy()
    return Image.fromarray(crop, "RGBA")


# ---------------------------------------------------------------------------
# Transparency detection (auto-select segmentation strategy)
# ---------------------------------------------------------------------------

def _check_image_transparency(pixels: np.ndarray) -> bool:
    """Check if image has any transparent pixels.

    Ported from JS ``checkImageTransparency``.

    Returns:
        True if image is fully opaque (no pixel has alpha < 255).
    """
    if pixels.shape[2] < 4:
        return True  # RGB image, no alpha channel = opaque
    return bool(np.all(pixels[:, :, 3] == 255))


def _auto_detect_bg_color(pixels: np.ndarray) -> Tuple[int, int, int]:
    """Auto-detect background color from the top-left pixel.

    Ported from JS ``checkImageTransparency`` (lines 838-840).
    For opaque images, assumes top-left pixel is background.

    Args:
        pixels: (H, W, 3+) uint8 array.

    Returns:
        (R, G, B) tuple of the detected background color.
    """
    r, g, b = int(pixels[0, 0, 0]), int(pixels[0, 0, 1]), int(pixels[0, 0, 2])
    return (r, g, b)


def _auto_detect_bg_colors(
    pixels: np.ndarray, max_colors: int = 4
) -> List[Tuple[int, int, int]]:
    """Auto-detect likely BG colors from corners and sparse border sampling."""
    h, w = pixels.shape[:2]
    samples = [
        (0, 0),
        (max(0, w - 1), 0),
        (0, max(0, h - 1)),
        (max(0, w - 1), max(0, h - 1)),
    ]
    step_x = max(1, w // 8)
    step_y = max(1, h // 8)
    for x in range(0, w, step_x):
        samples.append((x, 0))
        samples.append((x, max(0, h - 1)))
    for y in range(0, h, step_y):
        samples.append((0, y))
        samples.append((max(0, w - 1), y))

    counts: dict[Tuple[int, int, int], int] = {}
    for x, y in samples:
        color = (
            int(pixels[y, x, 0]),
            int(pixels[y, x, 1]),
            int(pixels[y, x, 2]),
        )
        counts[color] = counts.get(color, 0) + 1

    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    colors = [color for color, _ in ranked[:max_colors]]
    return colors or [_auto_detect_bg_color(pixels)]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def segment_image(pixels: np.ndarray,
                  alpha_threshold: int = 25,
                  bg_color: Optional[Tuple[int, int, int]] = None,
                  bg_colors: Optional[List[Tuple[int, int, int]]] = None,
                  color_tolerance: float = 30.0,
                  force_mode: Optional[str] = None) -> np.ndarray:
    """Create a binary segmentation map for an image.

    Auto-selects between alpha-based and color-based segmentation
    depending on whether the image has transparency.

    Args:
        pixels: (H, W, 3+) uint8 array.
        alpha_threshold: For alpha mode — pixels with alpha > threshold are fg.
            The JS source uses a 0-100% slider mapped to 0-255. Pass the
            0-255 value directly here.
        bg_color: For color mode — background color to subtract. If None,
            auto-detected from top-left pixel.
        bg_colors: Optional list of background colors. If present, overrides
            ``bg_color``.
        color_tolerance: For color mode — L1 (Manhattan) distance threshold.
        force_mode: Override auto-detection. "alpha" or "color".

    Returns:
        (H*W,) uint8 flat array, 1 = foreground, 0 = background.

    Tags: [FLOW:EXTRACT]
    """
    is_opaque = _check_image_transparency(pixels)

    if force_mode == "alpha":
        use_alpha = True
    elif force_mode == "color":
        use_alpha = False
    else:
        use_alpha = not is_opaque

    if use_alpha:
        if pixels.shape[2] < 4:
            # No alpha channel — treat everything as foreground
            return np.ones(pixels.shape[0] * pixels.shape[1], dtype=np.uint8)
        logger.debug("Using alpha-based segmentation (threshold=%d)", alpha_threshold)
        return create_alpha_segmentation_map(pixels, alpha_threshold)
    else:
        if bg_colors:
            color_list = [tuple(int(c) for c in color) for color in bg_colors]
        elif bg_color is not None:
            color_list = [tuple(int(c) for c in bg_color)]
        else:
            color_list = _auto_detect_bg_colors(pixels)
        logger.debug(
            "Using color-based segmentation (bg_colors=%s, tolerance=%.1f)",
            color_list,
            color_tolerance,
        )
        return create_color_segmentation_map(pixels, color_list, color_tolerance)


def extract_sprites(image: Image.Image,
                    mode: str = "shape",
                    alpha_threshold: int = 25,
                    bg_color: Optional[Tuple[int, int, int]] = None,
                    bg_colors: Optional[List[Tuple[int, int, int]]] = None,
                    color_tolerance: float = 30.0,
                    min_size: int = 30,
                    max_coverage: float = 0.9,
                    force_segmentation: Optional[str] = None,
                    ) -> List[ExtractedSprite]:
    """Extract individual sprites from a sprite sheet image.

    Full pipeline ported from JS ``segmentImage`` function:
      1. Convert to RGBA.
      2. Build binary segmentation map (alpha or color).
      3. Flood-fill to find connected components.
      4. Filter by minimum size.
      5. Extract each component using selected mode.

    Args:
        image: PIL Image (any mode, converted to RGBA internally).
        mode: Extraction mode — "shape", "bbox", or "bounding_box".
        alpha_threshold: Alpha threshold (0-255) for alpha-based segmentation.
        bg_color: Background color for color-based segmentation.
            None = auto-detect from top-left pixel.
        bg_colors: Optional list of background colors for color-based mode.
        color_tolerance: Euclidean distance threshold for color-based mode.
        min_size: Minimum width AND height of a sprite to keep (matching JS:
            ``bounds.width >= minSize && bounds.height >= minSize``).
        force_segmentation: Override auto-detection of segmentation strategy.
            "alpha" or "color".

    Returns:
        List of ExtractedSprite instances, sorted top-to-bottom, left-to-right.

    Tags: [PIPELINE:EXTRACT] [FLOW:EXTRACT]
    """
    mode_in = str(mode).strip().lower()
    if mode_in == "bbox":
        mode_in = "bounding_box"
    if mode_in not in ("shape", "bounding_box"):
        raise ValueError(f"mode must be 'shape', 'bbox', or 'bounding_box', got {mode!r}")

    # Step 0: Convert to RGBA
    rgba = image.convert("RGBA")
    pixels = np.array(rgba)
    h, w = pixels.shape[:2]

    logger.info("Extracting sprites: %dx%d, mode=%s, min_size=%d",
                w, h, mode_in, min_size)

    # Step 1: Build segmentation map
    seg_map = segment_image(
        pixels,
        alpha_threshold=alpha_threshold,
        bg_color=bg_color,
        bg_colors=bg_colors,
        color_tolerance=color_tolerance,
        force_mode=force_segmentation,
    )

    # Early exit if no foreground pixels
    if not np.any(seg_map):
        logger.info("No foreground pixels found")
        return []

    # Step 2: Flood fill to find connected components
    visited = np.zeros(w * h, dtype=np.uint8)
    components: List[ComponentBounds] = []

    for y in range(h):
        for x in range(w):
            idx = y * w + x
            if seg_map[idx] and not visited[idx]:
                bounds = flood_fill(seg_map, visited, x, y, w, h)
                components.append(bounds)

    logger.debug("Found %d connected components", len(components))

    # Step 3: Filter by minimum size (matching JS exactly:
    #   bounds.width >= minSize && bounds.height >= minSize)
    valid = [c for c in components
             if c.width >= min_size and c.height >= min_size]

    logger.debug("After min_size filter (%d): %d components remain",
                 min_size, len(valid))

    # Step 3b: [FIX:FULL-SHEET] Filter by max foreground coverage
    if max_coverage < 1.0:
        source_pixel_count = w * h
        before_count = len(valid)
        valid = [c for c in valid
                 if len(c.pixels) / source_pixel_count <= max_coverage]
        rejected = before_count - len(valid)
        if rejected > 0:
            logger.warning(
                "Rejected %d component(s) exceeding %.0f%% foreground coverage",
                rejected, max_coverage * 100,
            )

    # Step 4: Extract each sprite using selected mode
    results: List[ExtractedSprite] = []

    for bounds in valid:
        if mode_in == "shape":
            sprite_img = _extract_sprite_shape(pixels, bounds, w)
        else:
            sprite_img = _extract_sprite_bbox(pixels, bounds)

        results.append(ExtractedSprite(
            image=sprite_img,
            bbox=(bounds.x, bounds.y, bounds.width, bounds.height),
            pixel_count=len(bounds.pixels),
            extraction_mode="bbox" if mode_in == "bounding_box" else "shape",
            source_region=bounds.pixels if mode_in == "shape" else None,
        ))

    # Sort top-to-bottom, left-to-right (matching JS scan order)
    results.sort(key=lambda s: (s.bbox[1], s.bbox[0]))

    logger.info("Extracted %d sprites (mode=%s)", len(results), mode_in)
    return results


def segment_cell_region(
    cell_pixels: np.ndarray,
    bg_color: Optional[Tuple[int, int, int]] = None,
    bg_tolerance: float = 30.0,
) -> np.ndarray:
    """Segment a single cell region and return foreground boolean mask.

    Convenience wrapper for cell-level content analysis in the slicer.
    Uses color-based segmentation with auto-detected or explicit BG.

    [PIPELINE:SLICE] Used by content_correct_slice for per-cell analysis.

    Args:
        cell_pixels: (H, W, 3+) numpy array of the cell region.
        bg_color: Background color tuple, or None for auto-detect from corners.
        bg_tolerance: Color distance threshold for foreground classification.

    Returns:
        (H, W) boolean mask where True = foreground pixel.
    """
    h, w = cell_pixels.shape[:2]
    if bg_color is None:
        bg_colors_list = _auto_detect_bg_colors(cell_pixels)
    else:
        bg_colors_list = [bg_color]
    seg_flat = create_color_segmentation_map(
        cell_pixels, bg_colors_list, bg_tolerance
    )
    return seg_flat.reshape(h, w).astype(bool)


def extract_sprites_dual(image: Image.Image,
                         alpha_threshold: int = 25,
                         bg_color: Optional[Tuple[int, int, int]] = None,
                         bg_colors: Optional[List[Tuple[int, int, int]]] = None,
                         color_tolerance: float = 30.0,
                         min_size: int = 30,
                         max_coverage: float = 0.9,
                         force_segmentation: Optional[str] = None,
                         ) -> Tuple[List[ExtractedSprite], List[ExtractedSprite]]:
    """Run both extraction modes on the same image.

    Convenience function for the dual-track pipeline (Phase 21-02).

    Returns:
        (shape_sprites, bbox_sprites) — two lists from the same segmentation.

    Tags: [PIPELINE:EXTRACT] [FLOW:EXTRACT]
    """
    shape_results = extract_sprites(
        image, mode="shape",
        alpha_threshold=alpha_threshold,
        bg_color=bg_color,
        bg_colors=bg_colors,
        color_tolerance=color_tolerance,
        min_size=min_size,
        max_coverage=max_coverage,
        force_segmentation=force_segmentation,
    )
    bbox_results = extract_sprites(
        image, mode="bbox",
        alpha_threshold=alpha_threshold,
        bg_color=bg_color,
        bg_colors=bg_colors,
        color_tolerance=color_tolerance,
        min_size=min_size,
        max_coverage=max_coverage,
        force_segmentation=force_segmentation,
    )
    return shape_results, bbox_results
