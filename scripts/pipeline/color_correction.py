"""
Color correction utilities for the palette quantization pipeline.

ARCHITECTURE
------------
This module provides pre-processing color correction that runs *before*
palette quantization (quantizer.py) and glyph matching (processor.py).
Its primary job is to normalize near-magenta background pixels so that the
downstream transparency detection (palette.is_transparent) works reliably.

The Asciicker engine uses magenta (255, 0, 255) as a "color key" for
transparency -- any pixel matching that exact RGB value is treated as
see-through.  AI image generators and Blender renders rarely produce
pixel-perfect magenta, so this module snaps "close enough" pixels to the
canonical value before further processing.

All color distance calculations use L1 (Manhattan) distance in raw RGB space.
This is intentional: the tolerance thresholds were tuned empirically for the
16-color ANSI palette, and L1 in RGB is fast enough for batch processing
without pulling in a perceptual color space (CIELAB/DeltaE).

KEY EXPORTS
~~~~~~~~~~~
- ``snap_to_magenta()``          -- Vectorized near-magenta snapping.
- ``replace_background_color()`` -- Replace an arbitrary background with magenta.
- ``count_magenta_pixels()``     -- Audit: count transparent pixels in an image.
- ``analyze_background()``       -- Heuristic dominant-color background detection.
- ``auto_magenta_correction()``  -- High-level auto-detect-and-fix entry point.
- ``main()``                     -- CLI wrapper for manual / scripted usage.

PIPELINE CONTEXT
~~~~~~~~~~~~~~~~
This module is used in two places in the pipeline flow::

    [PIPELINE:GENERATE] generator.py  -- produces raw sprite sheet PNG
         |
         v
    [PIPELINE:PROCESS] color_correction.py  -- THIS MODULE: normalize magenta
         |                                     before quantization
         v
    [PIPELINE:PROCESS] processor.py / quantizer.py  -- quantize to 16-color ANSI

It is also invoked standalone via CLI for manual batch correction of
sprite sheets that fail the magenta QA check.

Data Contracts
~~~~~~~~~~~~~~
[DATA-CONTRACT:PALETTE]  Pure magenta (255, 0, 255) is the transparency key.
                         This value must stay in sync with palette.COLOR_TRANSPARENT
                         and quantizer.ANSI_COLORS[13] (Bright Magenta).

Tags: [PIPELINE:PROCESS] [DATA-CONTRACT:PALETTE] [DEPENDENCY:PIL] [DEPENDENCY:NUMPY]
"""

from collections import deque

from PIL import Image  # [DEPENDENCY:PIL] Pillow for image I/O and pixel access
import numpy as np  # [DEPENDENCY:NUMPY] Vectorized pixel math for snap_to_magenta

from .palette import MAGENTA_RGB
from .service.constants import (
    DEFAULT_SNAP_TOLERANCE,
    DEFAULT_BG_TOLERANCE,
    DEFAULT_TRANSPARENCY_TOLERANCE,
)


def snap_to_magenta(img, tolerance=DEFAULT_SNAP_TOLERANCE):
    """
    Snap near-magenta colors to pure magenta (255, 0, 255).

    [PIPELINE:PROCESS] This is the core color correction function.  It converts
    all pixels within ``tolerance`` L1 distance of magenta to exact magenta,
    ensuring clean transparency detection downstream.

    WHY L1 distance (not Euclidean or perceptual): L1 is sufficient because
    the magenta key sits at an extreme corner of the RGB cube (255, 0, 255).
    Nearby colors in L1 are also perceptually "magenta-ish", so there is no
    risk of snapping visually distinct colors.  L1 is also ~3x faster than
    Euclidean on large sprite sheets due to avoiding sqrt.

    Args:
        img: PIL Image (RGB or RGBA)
        tolerance: Maximum L1 distance from magenta to snap (default 15).
                   Values above ~30 risk snapping purple/pink sprite content.

    Returns:
        New PIL Image (always RGB) with corrected magenta colors.
        Note: alpha channel is DISCARDED if present.
    """
    # [DEPENDENCY:PIL] Convert PIL Image to numpy array for vectorized ops
    if img.mode == "RGBA":
        img_array = np.array(img)
        has_alpha = True
    else:
        img_array = np.array(img)
        has_alpha = False

    # [DATA-CONTRACT:PALETTE] Target magenta must match palette.COLOR_TRANSPARENT
    magenta = np.array([255, 0, 255])

    # Extract channel planes for L1 distance calculation
    r = img_array[:, :, 0]
    g = img_array[:, :, 1]
    b = img_array[:, :, 2]

    # WHY L1 (Manhattan) distance: |R-255| + |G-0| + |B-255|
    # This weights all three channels equally.  For magenta detection this is
    # appropriate because the green channel (which should be near 0) is the
    # primary discriminator -- any significant green pushes L1 above tolerance.
    distance = np.abs(r - magenta[0]) + np.abs(g - magenta[1]) + np.abs(b - magenta[2])

    # Create mask for pixels within tolerance
    mask = distance <= tolerance

    # Snap those pixels to pure magenta
    # WHY only modify RGB channels: alpha (if present) is irrelevant because
    # the Asciicker engine uses color-key transparency, not alpha transparency.
    img_array[mask, 0] = 255  # R
    img_array[mask, 1] = 0    # G
    img_array[mask, 2] = 255  # B

    # [DEPENDENCY:PIL] Convert back to PIL Image
    # TODO(PIPELINE-FIX): Alpha channel is silently discarded here.  If any
    # future pipeline stage needs RGBA output, this will need a mode parameter.
    if has_alpha:
        # Keep RGB part only for array-to-image conversion
        result = Image.fromarray(img_array[:, :, :3], mode="RGB")
    else:
        result = Image.fromarray(img_array, mode="RGB")

    return result


def replace_background_color(img, target_color, tolerance=DEFAULT_SNAP_TOLERANCE):
    """
    Replace a specific background color with pure magenta.

    [PIPELINE:PROCESS] Used when the source image has a non-magenta background
    (e.g., green-screen or solid gray from a Blender render).  The target color
    is detected by ``analyze_background()`` or specified manually.

    WHY replace rather than re-render: Re-rendering from Blender is expensive
    (minutes per turntable).  Post-hoc color replacement is instant and works
    for any source, including AI-generated images where we cannot control the
    background color at generation time.

    Args:
        img: PIL Image
        target_color: Tuple (r, g, b) of color to replace
        tolerance: L1 tolerance for color matching (same metric as snap_to_magenta)

    Returns:
        New PIL Image (RGB) with background replaced by magenta (255, 0, 255)
    """
    # [DEPENDENCY:PIL] Force RGB -- alpha is irrelevant for color-key transparency
    if img.mode == "RGBA":
        img_array = np.array(img.convert("RGB")) # Force RGB for background replacement
    else:
        img_array = np.array(img)

    target = np.array(target_color)

    # [PIPELINE:PROCESS] L1 distance from target background color
    diff = np.abs(img_array[:,:,0] - target[0]) + \
           np.abs(img_array[:,:,1] - target[1]) + \
           np.abs(img_array[:,:,2] - target[2])

    mask = diff <= tolerance

    # [DATA-CONTRACT:PALETTE] Replace with canonical magenta transparency key
    img_array[mask, 0] = 255
    img_array[mask, 1] = 0
    img_array[mask, 2] = 255

    return Image.fromarray(img_array, mode="RGB")


def align_background_to_magenta(
    img: Image.Image,
    mode: str = "key_color",
    key_color=(255, 0, 255),
    tolerance: int = DEFAULT_BG_TOLERANCE,
    alpha_threshold: int = 128,
    return_stats: bool = False,
) -> Image.Image | tuple[Image.Image, dict]:
    """Normalize background transparency to canonical magenta.

    This is the "between generation and slicing" middleware pass:
    - ``mode="key_color"``: edge-seeded flood-fill of pixels near key_color.
      Only background connected to sheet edges is remapped to magenta, so
      intentional interior pixels are preserved.
    - ``mode="alpha"``: alpha<threshold pixels become magenta.
    - ``mode="none"``: no-op (RGB conversion only).

    When ``return_stats`` is True, returns ``(image, stats_dict)`` where
    stats include the number of remapped pixels and mode-specific counters.
    """
    def _ret(image: Image.Image, stats: dict):
        if return_stats:
            return image, stats
        return image

    total_pixels = int(img.width * img.height)

    if mode == "none":
        out = img.convert("RGB")
        return _ret(
            out,
            {
                "mode": "none",
                "total_pixels": total_pixels,
                "pixels_remapped": 0,
            },
        )

    if mode == "alpha":
        rgba = img.convert("RGBA")
        arr = np.array(rgba)
        alpha = arr[:, :, 3]
        rgb = arr[:, :, :3].copy()
        mask = alpha < alpha_threshold
        remapped = int(mask.sum())
        rgb[mask] = MAGENTA_RGB
        out = Image.fromarray(rgb, mode="RGB")
        return _ret(
            out,
            {
                "mode": "alpha",
                "total_pixels": total_pixels,
                "alpha_threshold": int(alpha_threshold),
                "pixels_remapped": remapped,
            },
        )

    # key_color mode (default)
    rgb = img.convert("RGB")
    arr = np.array(rgb)
    if arr.size == 0:
        return _ret(
            rgb,
            {
                "mode": "key_color",
                "total_pixels": 0,
                "pixels_remapped": 0,
                "tolerance": int(tolerance),
                "key_color": [int(c) for c in key_color],
                "key_match_pixels": 0,
                "edge_seed_count": 0,
            },
        )

    target = np.array(key_color, dtype=np.int16)
    diff = np.abs(arr.astype(np.int16) - target).sum(axis=2)
    key_mask = diff <= max(0, int(tolerance))
    key_match_pixels = int(key_mask.sum())

    if not key_mask.any():
        return _ret(
            rgb,
            {
                "mode": "key_color",
                "total_pixels": total_pixels,
                "pixels_remapped": 0,
                "tolerance": int(tolerance),
                "key_color": [int(c) for c in key_color],
                "key_match_pixels": key_match_pixels,
                "edge_seed_count": 0,
            },
        )

    h, w = key_mask.shape
    bg_mask = np.zeros_like(key_mask, dtype=bool)
    queue = deque()

    def _seed(y: int, x: int) -> None:
        if key_mask[y, x] and not bg_mask[y, x]:
            bg_mask[y, x] = True
            queue.append((y, x))

    for x in range(w):
        _seed(0, x)
        _seed(h - 1, x)
    for y in range(h):
        _seed(y, 0)
        _seed(y, w - 1)
    edge_seed_count = int(len(queue))

    while queue:
        y, x = queue.popleft()
        if y > 0:
            _seed(y - 1, x)
        if y + 1 < h:
            _seed(y + 1, x)
        if x > 0:
            _seed(y, x - 1)
        if x + 1 < w:
            _seed(y, x + 1)

    arr[bg_mask] = MAGENTA_RGB
    remapped = int(bg_mask.sum())
    out = Image.fromarray(arr, mode="RGB")
    return _ret(
        out,
        {
            "mode": "key_color",
            "total_pixels": total_pixels,
            "pixels_remapped": remapped,
            "tolerance": int(tolerance),
            "key_color": [int(c) for c in key_color],
            "key_match_pixels": key_match_pixels,
            "edge_seed_count": edge_seed_count,
        },
    )


def count_magenta_pixels(img, tolerance=DEFAULT_TRANSPARENCY_TOLERANCE):
    """
    Count pixels that are magenta or near-magenta.

    [PIPELINE:PROCESS] QA/diagnostic function used to verify that color
    correction produced a reasonable amount of transparent background.
    A sprite with 0% magenta likely has a broken background; one with >90%
    is likely an empty or corrupt frame.

    WHY per-pixel loop (not vectorized): This function lazy-imports
    ``palette.is_transparent`` to stay consistent with the canonical
    transparency definition.  The per-pixel loop is acceptable because this
    is only called during QA diagnostics, not in the hot path.

    Args:
        img: PIL Image
        tolerance: Tolerance for distance check (default 5, tighter than
                   snap_to_magenta's 15 because this runs after correction)

    Returns:
        Tuple (magenta_count, total_pixels, percentage)
    """
    # [DATA-CONTRACT:PALETTE] Uses the canonical is_transparent from palette.py
    # rather than reimplementing the magenta check locally.
    from .palette import is_transparent

    # Convert to RGB if RGBA
    if img.mode == "RGBA":
        img_rgb = img.convert("RGB")
    else:
        img_rgb = img

    # TODO(PIPELINE-FIX): This pixel-by-pixel loop is O(w*h) with Python
    # overhead.  For large sprite sheets (e.g., 8-angle turntable at 1024px),
    # this can take seconds.  Consider vectorizing with numpy + the same L1
    # threshold used by is_transparent.
    transparent_count = 0
    for y in range(img_rgb.height):
        for x in range(img_rgb.width):
            pixel = img_rgb.getpixel((x, y))
            if is_transparent(pixel, tolerance=tolerance):
                transparent_count += 1

    total = img_rgb.width * img_rgb.height
    percentage = (transparent_count / total) * 100 if total > 0 else 0

    return transparent_count, total, percentage


def analyze_background(img):
    """
    Analyze image background to detect dominant color and determine if magenta-like.

    [PIPELINE:PROCESS] Heuristic analysis used by ``auto_magenta_correction()``
    to decide whether correction is needed and what tolerance to use.

    The algorithm samples every 10th pixel (1% of total) for speed, finds the
    most common color, and measures its L1 distance from pure magenta.

    WHY sampling instead of full scan: Sprite sheets can be large (e.g.,
    2048x2048 for 8-angle turntables).  Sampling every 10th pixel in both
    axes gives a 100x speedup while still reliably detecting the dominant
    background color, which typically covers >50% of the image.

    Args:
        img: PIL Image

    Returns:
        Dict with analysis results:
          - dominant_color: (r, g, b) most frequent sampled color
          - distance_from_magenta: L1 distance of dominant color from (255,0,255)
          - is_magenta_like: True if distance <= 50
          - recommended_correction: suggested tolerance for snap_to_magenta
    """
    # Convert to RGB if RGBA
    if img.mode == "RGBA":
        img_rgb = img.convert("RGB")
    else:
        img_rgb = img

    # WHY stride=10: ~1% pixel sampling is enough to find the dominant background
    # color reliably.  Background pixels vastly outnumber sprite content pixels
    # in typical sprite sheets (60-80% background).
    samples = []
    for y in range(0, img_rgb.height, 10):
        for x in range(0, img_rgb.width, 10):
            samples.append(img_rgb.getpixel((x, y)))

    # Count colors
    from collections import Counter

    color_counter = Counter(samples)

    # Get most common color
    if color_counter:
        most_common = color_counter.most_common(1)[0][0]
    else:
        most_common = (0, 0, 0)

    # [DATA-CONTRACT:PALETTE] Calculate L1 distance from canonical magenta
    r, g, b = most_common
    magenta = (255, 0, 255)
    distance = abs(r - magenta[0]) + abs(g - magenta[1]) + abs(b - magenta[2])

    # FIX(35-03): Add dominant_percentage for solid-color detection
    # Needed to avoid replacing solid test images (e.g., TDD fixtures)
    if color_counter and len(samples) > 0:
        dominant_count = color_counter.most_common(1)[0][1]
        dominant_percentage = (dominant_count / len(samples)) * 100
    else:
        dominant_percentage = 0

    # TODO(PIPELINE-FIX): The threshold 50 for is_magenta_like is a magic number
    # tuned empirically.  If AI generators shift their "magenta" further, this
    # may need adjustment.  Consider making it configurable.
    is_magenta_like = distance <= 50

    return {
        "dominant_color": most_common,
        "distance_from_magenta": distance,
        "is_magenta_like": is_magenta_like,
        "dominant_percentage": dominant_percentage,
        # WHY max(15, distance): ensures at least the default tolerance of 15
        # even when the background is very close to (but not exactly) magenta.
        # When is_magenta_like is True, no correction is needed so return 0.
        "recommended_correction": max(15, distance) if not is_magenta_like else 0,
    }


def auto_magenta_correction(img_path, tolerance=None):
    """
    Automatically detect and correct near-magenta colors.

    [PIPELINE:PROCESS] High-level convenience function that chains
    ``analyze_background()`` with the appropriate correction strategy:
      1. If background is already close to magenta (distance <= 30): no-op.
      2. If top-left pixel matches the dominant color: assume it is the
         background color-key and replace it with magenta.
      3. Otherwise: fall back to the generic ``snap_to_magenta()`` approach.

    WHY top-left pixel heuristic: Many rendering tools (Blender included)
    guarantee that the top-left corner is background.  Comparing it to the
    detected dominant color gives a simple two-point confirmation that avoids
    accidentally replacing foreground sprite pixels.

    Args:
        img_path: Path to image file
        tolerance: Manual tolerance (auto-detected if None via analyze_background)

    Returns:
        Path to corrected image (may be the original path if no correction needed)
    """
    from PIL import Image  # [DEPENDENCY:PIL] Local import to avoid circular at module level

    img = Image.open(img_path)
    analysis = analyze_background(img)

    # Auto-detect tolerance if not provided
    if tolerance is None:
        tolerance = analysis["recommended_correction"]

    print(f"Analyzing: {img_path}")
    print(f"  Dominant color: {analysis['dominant_color']}")
    print(f"  Distance from magenta: {analysis['distance_from_magenta']}")
    print(f"  Magenta-like: {analysis['is_magenta_like']}")
    print(f"  Recommended tolerance: {tolerance}")

    # WHY threshold 30 (not 50 like is_magenta_like): This is a stricter gate
    # for "already good enough" -- distances 30-50 are close to magenta but may
    # still cause single-pixel transparency misses in the engine's exact match.
    if analysis["distance_from_magenta"] > 30:  # Not already close
        # Check Top-Left pixel for "Color Key" transparency
        tl_pixel = img.getpixel((0, 0))
        if img.mode == 'RGBA': tl_pixel = tl_pixel[:3] # Ignore alpha for check

        # TODO(PIPELINE-FIX): The top-left pixel heuristic fails for sprite
        # sheets where (0,0) is part of the sprite content (e.g., full-bleed
        # layouts with no margin).  Consider also sampling corners (0,H-1),
        # (W-1,0), (W-1,H-1) and taking a majority vote.

        corrected = None

        # Check if Top-Left is likely background by comparing to dominant color
        dom = analysis['dominant_color']
        diff_tl = abs(tl_pixel[0]-dom[0]) + abs(tl_pixel[1]-dom[1]) + abs(tl_pixel[2]-dom[2])

        # WHY threshold 30: This is the same "close enough" gate used in the
        # distance_from_magenta check above.  If the top-left pixel's L1
        # distance to the dominant color is < 30, they are effectively the
        # same color, confirming the top-left is background, not sprite content.
        if diff_tl < 30: # Top-left matches dominant color -> treat as background
             print(f"  * Detected non-magenta background (Top-Left: {tl_pixel})")
             corrected = replace_background_color(img, tl_pixel, tolerance=tolerance)
        else:
             # WHY fallback to snap_to_magenta: if the top-left pixel is NOT the
             # dominant color, we cannot confidently identify the background, so
             # we fall back to the generic near-magenta snapping approach.
             corrected = snap_to_magenta(img, tolerance=tolerance)

        # Save
        if img_path.endswith(".png"):
            corrected_path = img_path.replace(".png", "_corrected.png")
        else:
            corrected_path = img_path + "_corrected.png"

        corrected.save(corrected_path)
        print(f"  Corrected image saved: {corrected_path}")
        return corrected_path
    else:
        print(f"  Image already has good magenta background")
        return img_path


def main():
    """
    Command line interface for standalone color correction.

    [PIPELINE:PROCESS] CLI entry point that can be invoked directly or via
    ``python -m scripts.pipeline.color_correction``.

    Supports two modes:
      - ``--auto``: Analyzes the image, auto-detects tolerance, and corrects.
      - Manual (default): Applies ``snap_to_magenta`` with explicit or default
        tolerance of 15.

    Args:
        None (reads from ``sys.argv`` via argparse).

    Returns:
        None. Writes corrected image to disk and prints results to stdout.

    Raises:
        SystemExit: If argparse encounters invalid arguments.
        FileNotFoundError: If the input image path does not exist (raised by PIL).

    Usage::

        python -m scripts.pipeline.color_correction input.png [output.png] [--tolerance 20]
        python -m scripts.pipeline.color_correction input.png --auto
    """
    import argparse
    import os

    parser = argparse.ArgumentParser(
        description="Snap near-magenta colors to pure magenta"
    )
    parser.add_argument("input", help="Input image path")
    parser.add_argument(
        "output", nargs="?", help="Output path (default: input_corrected.png)"
    )
    parser.add_argument(
        "--tolerance",
        type=int,
        default=None,
        help="Snap tolerance (auto-detected if not set)",
    )
    parser.add_argument("--auto", action="store_true", help="Auto-detect and correct")

    args = parser.parse_args()

    if args.auto:
        # Auto mode
        result = auto_magenta_correction(args.input)
        print(f"\nDone! Result: {result}")
    else:
        # Manual mode
        img = Image.open(args.input)
        tolerance = args.tolerance

        if tolerance is None:
            tolerance = DEFAULT_SNAP_TOLERANCE

        corrected = snap_to_magenta(img, tolerance=tolerance)

        # Determine output path
        if not args.output:
            input_dir = os.path.dirname(args.input)
            input_name, input_ext = os.path.splitext(os.path.basename(args.input))
            args.output = os.path.join(input_dir, f"{input_name}_corrected{input_ext}")

        corrected.save(args.output)
        print(f"Saved corrected image: {args.output}")


if __name__ == "__main__":
    main()
