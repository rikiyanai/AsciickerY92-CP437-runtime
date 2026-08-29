"""
Grid detection for sprite sheet cell boundaries.

Ported from Perfect Pixel (noCV2 variant) — FFT-based and gradient-based
grid estimation with Sobel refinement. Pure numpy, no OpenCV dependency.

Pipeline integration point: called by ``_run_pre_slice_check()`` in
pipeline.py and by the 4-track orchestrator for automatic grid sizing.

Algorithm overview:
  1. **FFT method** — compute 2D FFT magnitude spectrum, project onto
     row/col axes, detect symmetric peak pairs. Distance from center
     peak to first harmonic gives cell size.
  2. **Gradient method** — Sobel edge detector accumulated along each
     axis. Peak intervals give cell size.
  3. **Refinement** — walk Sobel gradient sums from center outward,
     snapping grid lines to local maxima.
  4. **Confidence** — agreement between FFT and gradient estimates,
     weighted by peak prominence and grid regularity.

Exports
-------
- ``GridDetectionResult`` — dataclass with cell_w, cell_h, confidence, etc.
- ``detect_grid()`` — main entry point accepting PIL Image or ndarray.

Tags: [PIPELINE:SLICE] [FLOW:GRID] [DEPENDENCY:NUMPY]
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class GridDetectionResult:
    """Outcome of grid detection on a sprite sheet image."""

    cell_w: int
    """Detected cell width in pixels."""

    cell_h: int
    """Detected cell height in pixels."""

    grid_cols: int
    """Number of detected grid columns."""

    grid_rows: int
    """Number of detected grid rows."""

    confidence: float
    """Detection confidence in [0.0, 1.0].

    - >= 0.8: both FFT and gradient agree
    - 0.5-0.8: single method or partial agreement
    - < 0.5: weak signal or manual override needed
    """

    method: str
    """Which method produced the result: 'fft', 'gradient', 'both', or 'manual'."""

    source_cell_px: Optional[int] = None
    """Rounded square cell size when cell_w ~= cell_h (convenience field)."""

    x_coords: list = field(default_factory=list)
    """Refined x grid line coordinates (pixel positions)."""

    y_coords: list = field(default_factory=list)
    """Refined y grid line coordinates (pixel positions)."""


# ---------------------------------------------------------------------------
# Low-level utilities (numpy-only replacements for cv2)
# ---------------------------------------------------------------------------

def _rgb_to_gray(image_rgb: np.ndarray) -> np.ndarray:
    """Convert RGB uint8/float array to grayscale float32."""
    img = image_rgb.astype(np.float32)
    if img.ndim == 2:
        return img
    return (0.299 * img[..., 0] + 0.587 * img[..., 1] + 0.114 * img[..., 2]).astype(np.float32)


def _normalize_minmax(x: np.ndarray, a: float = 0.0, b: float = 1.0) -> np.ndarray:
    """Min-max normalize array to [a, b]."""
    x = x.astype(np.float32, copy=False)
    mn, mx = float(x.min()), float(x.max())
    if mx - mn < 1e-8:
        return np.zeros_like(x, dtype=np.float32) + a
    y = (x - mn) / (mx - mn)
    return (a + (b - a) * y).astype(np.float32)


def _conv2d_same(image: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """2D convolution (same padding) for grayscale float32."""
    img = image.astype(np.float32, copy=False)
    k = kernel.astype(np.float32, copy=False)
    kh, kw = k.shape
    ph, pw = kh // 2, kw // 2
    pad = np.pad(img, ((ph, ph), (pw, pw)), mode="reflect")
    out = np.zeros_like(img, dtype=np.float32)
    for dy in range(kh):
        for dx in range(kw):
            w = k[dy, dx]
            if w == 0:
                continue
            out += w * pad[dy:dy + img.shape[0], dx:dx + img.shape[1]]
    return out


def _sobel_xy(gray: np.ndarray, ksize: int = 3) -> Tuple[np.ndarray, np.ndarray]:
    """Return (gx, gy) Sobel gradients."""
    if ksize == 3:
        kx = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=np.float32)
        ky = np.array([[-1, -2, -1], [0, 0, 0], [1, 2, 1]], dtype=np.float32)
    elif ksize == 5:
        kx = np.array([
            [-5, -4, 0, 4, 5],
            [-8, -10, 0, 10, 8],
            [-10, -20, 0, 20, 10],
            [-8, -10, 0, 10, 8],
            [-5, -4, 0, 4, 5],
        ], dtype=np.float32)
        ky = kx.T
    else:
        raise ValueError("ksize must be 3 or 5")
    return _conv2d_same(gray, kx), _conv2d_same(gray, ky)


# ---------------------------------------------------------------------------
# FFT-based grid estimation
# ---------------------------------------------------------------------------

def _compute_fft_magnitude(gray: np.ndarray) -> np.ndarray:
    f = np.fft.fft2(gray.astype(np.float32))
    fshift = np.fft.fftshift(f)
    mag = np.abs(fshift)
    mag = 1 - np.log1p(mag)
    return _normalize_minmax(mag, 0.0, 1.0)


def _smooth_1d(v: np.ndarray, k: int = 17) -> np.ndarray:
    k = int(k)
    if k < 3:
        return v
    if k % 2 == 0:
        k += 1
    sigma = k / 6.0
    x = np.arange(k) - k // 2
    ker = np.exp(-(x * x) / (2 * sigma * sigma))
    ker = ker / (ker.sum() + 1e-8)
    return np.convolve(v, ker, mode="same")


def _detect_peak(proj: np.ndarray, peak_width: int = 6,
                 rel_thr: float = 0.35, min_dist: int = 6) -> Optional[float]:
    """Find symmetric peak pair distance (half-distance = grid count)."""
    center = len(proj) // 2
    mx = float(proj.max())
    if mx < 1e-6:
        return None

    thr = mx * float(rel_thr)
    candidates = []

    for i in range(1, len(proj) - 1):
        is_peak = True
        for j in range(1, peak_width):
            if i - j < 0 or i + j >= len(proj):
                continue
            if proj[i - j + 1] < proj[i - j] or proj[i + j - 1] < proj[i + j]:
                is_peak = False
                break
        if not is_peak or proj[i] < thr:
            continue

        left_climb = 0.0
        for k_idx in range(i, 0, -1):
            if proj[k_idx] > proj[k_idx - 1]:
                left_climb = abs(proj[i] - proj[k_idx - 1])
            else:
                break

        right_fall = 0.0
        for k_idx in range(i, len(proj) - 1):
            if proj[k_idx] > proj[k_idx + 1]:
                right_fall = abs(proj[i] - proj[k_idx + 1])
            else:
                break

        candidates.append({
            "index": i,
            "score": max(left_climb, right_fall),
        })

    if not candidates:
        return None

    left = [c for c in candidates
            if center * 0.25 < c["index"] < center - min_dist]
    right = [c for c in candidates
             if center + min_dist < c["index"] < center * 1.75]

    left.sort(key=lambda x: x["score"], reverse=True)
    right.sort(key=lambda x: x["score"], reverse=True)

    if not left or not right:
        return None

    return abs(right[0]["index"] - left[0]["index"]) / 2


def _estimate_grid_fft(gray: np.ndarray,
                       peak_width: int = 6) -> Optional[Tuple[int, int]]:
    """Return (grid_w, grid_h) from FFT spectrum or None."""
    mag = _compute_fft_magnitude(gray)
    H, W = gray.shape

    row_sum = np.sum(mag[:, :], axis=1)
    col_sum = np.sum(mag[:, :], axis=0)

    row_sum = _normalize_minmax(row_sum, 0.0, 1.0).flatten()
    col_sum = _normalize_minmax(col_sum, 0.0, 1.0).flatten()

    row_sum = _smooth_1d(row_sum, k=17)
    col_sum = _smooth_1d(col_sum, k=17)

    scale_row = _detect_peak(row_sum, peak_width=peak_width)
    scale_col = _detect_peak(col_sum, peak_width=peak_width)

    if scale_row is None or scale_col is None or scale_col <= 0:
        return None

    return int(round(scale_col)), int(round(scale_row))


# ---------------------------------------------------------------------------
# Gradient-based grid estimation
# ---------------------------------------------------------------------------

def _estimate_grid_gradient(gray: np.ndarray,
                            rel_thr: float = 0.2) -> Optional[Tuple[int, int]]:
    """Return (grid_w, grid_h) from Sobel gradient peaks or None."""
    H, W = gray.shape
    grad_x, grad_y = _sobel_xy(gray, ksize=3)

    grad_x_sum = np.sum(np.abs(grad_x), axis=0).reshape(-1)
    grad_y_sum = np.sum(np.abs(grad_y), axis=1).reshape(-1)

    thr_x = float(rel_thr) * float(grad_x_sum.max())
    thr_y = float(rel_thr) * float(grad_y_sum.max())

    min_interval = 4

    peak_x = []
    for i in range(1, len(grad_x_sum) - 1):
        if (grad_x_sum[i] > grad_x_sum[i - 1]
                and grad_x_sum[i] > grad_x_sum[i + 1]
                and grad_x_sum[i] >= thr_x):
            if not peak_x or i - peak_x[-1] >= min_interval:
                peak_x.append(i)

    peak_y = []
    for i in range(1, len(grad_y_sum) - 1):
        if (grad_y_sum[i] > grad_y_sum[i - 1]
                and grad_y_sum[i] > grad_y_sum[i + 1]
                and grad_y_sum[i] >= thr_y):
            if not peak_y or i - peak_y[-1] >= min_interval:
                peak_y.append(i)

    if len(peak_x) < 4 or len(peak_y) < 4:
        return None

    intervals_x = [peak_x[i] - peak_x[i - 1] for i in range(1, len(peak_x))]
    intervals_y = [peak_y[i] - peak_y[i - 1] for i in range(1, len(peak_y))]

    median_x = float(np.median(intervals_x))
    median_y = float(np.median(intervals_y))

    if median_x < 1 or median_y < 1:
        return None

    grid_w = int(round(W / median_x))
    grid_h = int(round(H / median_y))

    logger.debug("Gradient grid estimate: %dx%d (intervals: %.1f, %.1f)",
                 grid_w, grid_h, median_x, median_y)
    return grid_w, grid_h


# ---------------------------------------------------------------------------
# Grid refinement (Sobel-guided grid line snapping)
# ---------------------------------------------------------------------------

def _find_best_grid(origin: float, range_min: float, range_max: float,
                    grad_mag: np.ndarray, thr: float = 0.0) -> int:
    """Snap a grid line candidate to the nearest gradient peak."""
    best = round(origin)
    peaks = []
    mx = float(np.max(grad_mag))
    if mx < 1e-6:
        return best
    rel_thr = mx * thr
    for i in range(-round(range_min), round(range_max) + 1):
        candidate = round(origin + i)
        if candidate <= 0 or candidate >= len(grad_mag) - 1:
            continue
        if (grad_mag[candidate] > grad_mag[candidate - 1]
                and grad_mag[candidate] > grad_mag[candidate + 1]
                and grad_mag[candidate] >= rel_thr):
            peaks.append((grad_mag[candidate], candidate))
    if not peaks:
        return best
    peaks.sort(key=lambda x: x[0], reverse=True)
    return peaks[0][1]


def _refine_grids(image: np.ndarray, grid_x: int, grid_y: int,
                  refine_intensity: float = 0.25) -> Tuple[list, list]:
    """Refine grid line positions using Sobel gradient peaks."""
    H, W = image.shape[:2]
    cell_w = W / grid_x
    cell_h = H / grid_y

    gray = _rgb_to_gray(image)
    gx, gy = _sobel_xy(gray, ksize=3)

    grad_x_sum = np.sum(np.abs(gx), axis=0).reshape(-1)
    grad_y_sum = np.sum(np.abs(gy), axis=1).reshape(-1)

    x_coords = []
    x = _find_best_grid(W / 2, cell_w, cell_w, grad_x_sum)
    while x < W + cell_w / 2:
        x = _find_best_grid(x, cell_w * refine_intensity,
                            cell_w * refine_intensity, grad_x_sum)
        x_coords.append(x)
        x += cell_w

    x = _find_best_grid(W / 2, cell_w, cell_w, grad_x_sum) - cell_w
    while x > -cell_w / 2:
        x = _find_best_grid(x, cell_w * refine_intensity,
                            cell_w * refine_intensity, grad_x_sum)
        x_coords.append(x)
        x -= cell_w

    y_coords = []
    y = _find_best_grid(H / 2, cell_h, cell_h, grad_y_sum)
    while y < H + cell_h / 2:
        y = _find_best_grid(y, cell_h * refine_intensity,
                            cell_h * refine_intensity, grad_y_sum)
        y_coords.append(y)
        y += cell_h

    y = _find_best_grid(H / 2, cell_h, cell_h, grad_y_sum) - cell_h
    while y > -cell_h / 2:
        y = _find_best_grid(y, cell_h * refine_intensity,
                            cell_h * refine_intensity, grad_y_sum)
        y_coords.append(y)
        y -= cell_h

    return sorted(x_coords), sorted(y_coords)


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

def _compute_confidence(fft_result: Optional[Tuple[int, int]],
                        grad_result: Optional[Tuple[int, int]],
                        image_w: int, image_h: int,
                        final_cell_w: float, final_cell_h: float) -> float:
    """Score detection confidence in [0.0, 1.0].

    Factors:
    - Both methods agreeing (within 10%) = high base confidence
    - Only one method = moderate base confidence
    - Aspect ratio plausibility (cell should be roughly square)
    - Grid evenly divides image dimensions
    """
    base = 0.0

    if fft_result is not None and grad_result is not None:
        fft_cell_w = image_w / fft_result[0]
        fft_cell_h = image_h / fft_result[1]
        grad_cell_w = image_w / grad_result[0]
        grad_cell_h = image_h / grad_result[1]
        w_agree = abs(fft_cell_w - grad_cell_w) / max(fft_cell_w, 1) < 0.10
        h_agree = abs(fft_cell_h - grad_cell_h) / max(fft_cell_h, 1) < 0.10
        if w_agree and h_agree:
            base = 0.85
        elif w_agree or h_agree:
            base = 0.65
        else:
            base = 0.50
    elif fft_result is not None:
        base = 0.60
    elif grad_result is not None:
        base = 0.55
    else:
        return 0.0

    # Aspect ratio bonus: square-ish cells get a boost
    ratio = max(final_cell_w, final_cell_h) / max(min(final_cell_w, final_cell_h), 0.1)
    if ratio < 1.15:
        base += 0.10
    elif ratio > 1.5:
        base -= 0.15

    # Divisibility bonus: clean division = more likely correct
    w_remainder = image_w % round(final_cell_w) if final_cell_w > 0 else image_w
    h_remainder = image_h % round(final_cell_h) if final_cell_h > 0 else image_h
    if w_remainder == 0 and h_remainder == 0:
        base += 0.05

    return max(0.0, min(1.0, base))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_grid(image, grid_size: Optional[Tuple[int, int]] = None,
                min_size: float = 4.0, peak_width: int = 6,
                refine_intensity: float = 0.25,
                fix_square: bool = True,
                method: str = "both") -> GridDetectionResult:
    """Detect cell grid in a sprite sheet image.

    Args:
        image: PIL Image (RGB/RGBA) or numpy ndarray (H, W, 3).
        grid_size: Manual override (grid_cols, grid_rows). Bypasses detection.
        min_size: Minimum plausible cell size in pixels.
        peak_width: FFT peak width threshold.
        refine_intensity: Sobel refinement search range (0.0-0.5).
        fix_square: Snap near-square grids to exact square.

    Returns:
        GridDetectionResult with cell dimensions and confidence.

    Tags: [FLOW:GRID] [PIPELINE:SLICE]
    """
    # Accept PIL Image or ndarray
    if hasattr(image, "convert"):
        arr = np.array(image.convert("RGB"))
    else:
        arr = np.asarray(image)

    H, W = arr.shape[:2]

    # Manual override path
    if grid_size is not None:
        cols, rows = grid_size
        if cols <= 0 or rows <= 0:
            raise ValueError(
                f"grid_size must contain positive integers, got {grid_size!r}"
            )
        cell_w = W / cols if cols > 0 else W
        cell_h = H / rows if rows > 0 else H
        x_coords, y_coords = _refine_grids(arr, cols, rows, refine_intensity)
        sq = round((cell_w + cell_h) / 2) if abs(cell_w - cell_h) < 2 else None
        return GridDetectionResult(
            cell_w=round(cell_w),
            cell_h=round(cell_h),
            grid_cols=cols,
            grid_rows=rows,
            confidence=1.0,
            method="manual",
            source_cell_px=sq,
            x_coords=x_coords,
            y_coords=y_coords,
        )

    if method not in ("fft", "gradient", "both"):
        raise ValueError(f"method must be 'fft', 'gradient', or 'both', got {method!r}")

    gray = _rgb_to_gray(arr)

    # Try FFT first (skip when method="gradient")
    fft_result = None
    if method in ("fft", "both"):
        fft_result = _estimate_grid_fft(gray, peak_width=peak_width)
    fft_ok = False
    if fft_result is not None:
        pixel_w = W / fft_result[0]
        pixel_h = H / fft_result[1]
        max_pixel = 20.0
        ratio = max(pixel_w, pixel_h) / max(min(pixel_w, pixel_h), 0.1)
        if min(pixel_w, pixel_h) >= min_size and max(pixel_w, pixel_h) <= max_pixel and ratio <= 1.5:
            fft_ok = True
            logger.debug("FFT grid estimate: %dx%d (cell: %.1fx%.1f)",
                         fft_result[0], fft_result[1], pixel_w, pixel_h)
        else:
            logger.debug("FFT result rejected (cell %.1fx%.1f, ratio %.2f)",
                         pixel_w, pixel_h, ratio)

    # Try gradient (skip when method="fft")
    grad_result = None
    if method in ("gradient", "both"):
        grad_result = _estimate_grid_gradient(gray)

    # Pick best estimate
    if fft_ok and grad_result is not None:
        # Average when both available
        avg_cols = (fft_result[0] + grad_result[0]) / 2
        avg_rows = (fft_result[1] + grad_result[1]) / 2
        grid_w = int(round(avg_cols))
        grid_h = int(round(avg_rows))
        method = "both"
    elif fft_ok:
        grid_w, grid_h = fft_result
        method = "fft"
    elif grad_result is not None:
        grid_w, grid_h = grad_result
        method = "gradient"
    else:
        logger.warning("Grid detection failed for %dx%d image", W, H)
        return GridDetectionResult(
            cell_w=W, cell_h=H,
            grid_cols=1, grid_rows=1,
            confidence=0.0,
            method="none",
        )

    # Compute pixel size and normalize
    pixel_w = W / grid_w
    pixel_h = H / grid_h

    if pixel_w / pixel_h > 1.5 or pixel_h / pixel_w > 1.5:
        pixel_size = min(pixel_w, pixel_h)
    else:
        pixel_size = (pixel_w + pixel_h) / 2.0

    grid_w = int(round(W / pixel_size))
    grid_h = int(round(H / pixel_size))

    # Refine grid lines
    x_coords, y_coords = _refine_grids(arr, grid_w, grid_h, refine_intensity)

    refined_cols = len(x_coords) - 1 if len(x_coords) > 1 else grid_w
    refined_rows = len(y_coords) - 1 if len(y_coords) > 1 else grid_h

    # Fix near-square
    if fix_square and abs(refined_cols - refined_rows) == 1:
        target = min(refined_cols, refined_rows)
        if target % 2 == 0:
            refined_cols = target
            refined_rows = target

    cell_w = W / refined_cols if refined_cols > 0 else W
    cell_h = H / refined_rows if refined_rows > 0 else H

    confidence = _compute_confidence(
        fft_result if fft_ok else None,
        grad_result, W, H, cell_w, cell_h,
    )

    sq = round(pixel_size) if abs(cell_w - cell_h) < 2 else None

    logger.info("Grid detected: %dx%d cells (%.1fx%.1fpx each), confidence=%.2f, method=%s",
                refined_cols, refined_rows, cell_w, cell_h, confidence, method)

    return GridDetectionResult(
        cell_w=round(cell_w),
        cell_h=round(cell_h),
        grid_cols=refined_cols,
        grid_rows=refined_rows,
        confidence=confidence,
        method=method,
        source_cell_px=sq,
        x_coords=x_coords,
        y_coords=y_coords,
    )
