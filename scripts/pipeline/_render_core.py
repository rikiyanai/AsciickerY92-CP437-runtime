"""
_render_core.py -- Shared CP437 font atlas loading and cell rendering.

[DATA-CONTRACT:FONT_ATLAS] [FLOW:RENDER]

Extracted from xp_viewer.py and xp_tool.py to eliminate rendering
duplication. Both the headless renderer (export_service) and the
Tkinter editor (xp_tool) import from here.

No Tkinter dependency -- safe for headless use.
"""

import os
from typing import Dict, Optional, Tuple

from PIL import Image, ImageDraw

# [DATA-CONTRACT:XP] Magic pink marks transparent cells in the engine.
MAGIC_PINK = (255, 0, 255)

# Default font atlas: 16x16 grid of 12x12 CP437 glyphs (192x192 native)
_DEFAULT_CELL_SIZE = 12
_ATLAS_COLS = 16

# Cache for rendered glyph+color combinations
_glyph_cache: Dict[Tuple[int, Tuple, Tuple, int, int], Image.Image] = {}


def find_font_atlas(search_from: Optional[str] = None) -> Optional[str]:
    """Locate the CP437 font atlas PNG.

    Searches relative to the given path (or this file's directory),
    trying common project layouts.

    Args:
        search_from: Directory to search from. Defaults to this file's dir.

    Returns:
        Absolute path to the font atlas, or None if not found.
    """
    base = search_from or os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base, "../../assets/fonts/cp437_12x12.png"),
        os.path.join(base, "../assets/fonts/cp437_12x12.png"),
        os.path.join(base, "assets/fonts/cp437_12x12.png"),
        "assets/fonts/cp437_12x12.png",
    ]
    for p in candidates:
        resolved = os.path.abspath(p)
        if os.path.exists(resolved):
            return resolved
    return None


def load_font_atlas(
    path: str,
    char_w: int = _DEFAULT_CELL_SIZE,
    char_h: int = _DEFAULT_CELL_SIZE,
) -> Dict[int, Image.Image]:
    """Load a CP437 font sprite sheet and extract individual glyph images.

    [DATA-CONTRACT:CP437] The sprite sheet is a PNG grid of 256 glyphs
    (16 columns) in standard CP437 order. Each glyph cell is char_w x char_h.

    Args:
        path: Path to the font atlas PNG.
        char_w: Width of each glyph cell in pixels.
        char_h: Height of each glyph cell in pixels.

    Returns:
        Dict mapping glyph index (0-255) to PIL RGBA Image.

    Raises:
        FileNotFoundError: If path does not exist.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Font atlas not found: {path}")

    sheet = Image.open(path).convert("RGBA")
    cols = sheet.width // char_w
    rows = sheet.height // char_h

    glyphs: Dict[int, Image.Image] = {}
    for i in range(256):
        col = i % cols
        row = i // cols
        if row >= rows:
            break
        x = col * char_w
        y = row * char_h
        glyphs[i] = sheet.crop((x, y, x + char_w, y + char_h))

    return glyphs


def render_cell(
    glyph_idx: int,
    fg: Tuple[int, int, int],
    bg: Tuple[int, int, int],
    glyphs: Dict[int, Image.Image],
    char_w: int = _DEFAULT_CELL_SIZE,
    char_h: int = _DEFAULT_CELL_SIZE,
    scale: int = 1,
) -> Image.Image:
    """Render a single XP cell (glyph + fg + bg) to an RGBA image.

    Uses the glyph's red channel as an alpha mask for the foreground color,
    matching the C++ engine's rendering approach.

    Transparent cells (bg == MAGIC_PINK) return a fully transparent image.

    Args:
        glyph_idx: CP437 codepoint (0-255). Values >255 are clamped to 0.
        fg: Foreground RGB tuple.
        bg: Background RGB tuple.
        glyphs: Dict of glyph index -> PIL Image (from load_font_atlas).
        char_w: Cell width in pixels.
        char_h: Cell height in pixels.
        scale: Output scale factor.

    Returns:
        RGBA PIL Image of size (char_w*scale, char_h*scale).
    """
    out_w = char_w * scale
    out_h = char_h * scale
    is_transparent = (bg[0] == 255 and bg[1] == 0 and bg[2] == 255)

    if is_transparent:
        return Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))

    # Clamp out-of-range glyphs
    if glyph_idx < 0 or glyph_idx > 255:
        glyph_idx = 0

    # Check cache
    cache_key = (glyph_idx, fg, bg, char_w * scale, char_h * scale)
    cached = _glyph_cache.get(cache_key)
    if cached is not None:
        return cached

    # Background fill
    cell = Image.new("RGBA", (out_w, out_h), bg + (255,))

    # Render glyph if available and not space (glyph 0 or 32)
    glyph_img = glyphs.get(glyph_idx)
    if glyph_img is not None and glyph_idx not in (0, 32):
        if glyph_img.mode != "RGBA":
            glyph_img = glyph_img.convert("RGBA")

        # Red channel as alpha mask for fg color
        colored = Image.new("RGBA", glyph_img.size, fg + (255,))
        r, _g, _b, _a = glyph_img.split()
        colored.putalpha(r)

        if scale > 1:
            colored = colored.resize((out_w, out_h), Image.NEAREST)

        cell = Image.alpha_composite(cell, colored)

    _glyph_cache[cache_key] = cell
    return cell


def clear_cache() -> None:
    """Clear the glyph rendering cache."""
    _glyph_cache.clear()


def render_xp_layer_to_png(
    layer_data: list,
    glyphs: Dict[int, Image.Image],
    char_w: int = _DEFAULT_CELL_SIZE,
    char_h: int = _DEFAULT_CELL_SIZE,
    scale: int = 1,
) -> Image.Image:
    """Render an XP visual layer to a transparency-preserving RGBA image.

    Unlike XPViewer.render_all_frames() which uses an opaque black canvas,
    this function starts with a fully transparent canvas and uses render_cell()
    for each cell. Transparent cells (MAGIC_PINK bg) produce alpha=0 pixels,
    making them recoverable by reverse_render_sheet().

    [DATA-CONTRACT:XP] [FLOW:RENDER]

    Args:
        layer_data: 2D list of (glyph, fg_rgb, bg_rgb) tuples (row-major).
        glyphs: Dict of glyph index -> PIL Image (from load_font_atlas).
        char_w: Cell width in pixels.
        char_h: Cell height in pixels.
        scale: Output scale factor.

    Returns:
        RGBA PIL Image with transparency preserved for MAGIC_PINK cells.
    """
    rows = len(layer_data)
    cols = len(layer_data[0]) if rows > 0 else 0
    out_w = cols * char_w * scale
    out_h = rows * char_h * scale

    sheet = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))

    for cy in range(rows):
        for cx in range(cols):
            glyph_idx, fg, bg = layer_data[cy][cx]
            cell_img = render_cell(glyph_idx, fg, bg, glyphs, char_w, char_h, scale)
            px = cx * char_w * scale
            py = cy * char_h * scale
            sheet.alpha_composite(cell_img, (px, py))

    return sheet


# ---------------------------------------------------------------------------
# Reverse rendering: recover (glyph, fg, bg) from rendered pixels
# ---------------------------------------------------------------------------

import numpy as np

# Cached binary glyph masks for reverse matching
_glyph_masks: Optional[Dict[int, np.ndarray]] = None


def load_glyph_masks(
    atlas_path: str,
    char_w: int = _DEFAULT_CELL_SIZE,
    char_h: int = _DEFAULT_CELL_SIZE,
) -> Dict[int, np.ndarray]:
    """Load font atlas as binary masks for reverse rendering.

    Each mask is a (char_h, char_w) boolean ndarray where True = foreground.

    [DATA-CONTRACT:CP437] Atlas must be binary (grayscale values 0 or 255).

    Args:
        atlas_path: Path to the font atlas PNG.
        char_w: Glyph cell width in pixels.
        char_h: Glyph cell height in pixels.

    Returns:
        Dict mapping glyph index (0-255) to boolean ndarray.
    """
    global _glyph_masks
    if _glyph_masks is not None:
        return _glyph_masks

    sheet = Image.open(atlas_path).convert("L")
    arr = np.array(sheet)
    cols = sheet.width // char_w

    masks: Dict[int, np.ndarray] = {}
    for i in range(256):
        col = i % cols
        row = i // cols
        if row >= (sheet.height // char_h):
            break
        x = col * char_w
        y = row * char_h
        masks[i] = arr[y:y + char_h, x:x + char_w] > 127

    _glyph_masks = masks
    return masks


def reverse_render_cell(
    block: np.ndarray,
    glyph_masks: Dict[int, np.ndarray],
) -> Tuple[int, Tuple[int, int, int], Tuple[int, int, int]]:
    """Recover (glyph_idx, fg, bg) from a rendered 12x12 pixel block.

    Assumes the block was produced by render_cell() with a binary font atlas,
    meaning every pixel is either the exact fg or exact bg color.

    Algorithm:
        1. If block is fully transparent (alpha=0), return transparent cell.
        2. Find unique RGB colors in the block.
        3. If 1 color: it's bg with a space glyph (0 or 32).
        4. If 2 colors: try both as fg, match binary pattern against masks.
        5. If >2 colors: fall back to most/least frequent as bg/fg.

    Args:
        block: (H, W, 3) or (H, W, 4) uint8 ndarray of pixel data.
        glyph_masks: Dict from load_glyph_masks().

    Returns:
        (glyph_idx, fg_rgb, bg_rgb) tuple.
    """
    h, w = block.shape[:2]

    # Handle RGBA: check for transparent block
    if block.shape[2] == 4:
        alpha = block[:, :, 3]
        if np.all(alpha == 0):
            return (32, (0, 0, 0), MAGIC_PINK)
        rgb = block[:, :, :3]
    else:
        rgb = block

    # Find unique colors
    flat = rgb.reshape(-1, 3)
    unique_colors = np.unique(flat, axis=0)

    if len(unique_colors) == 1:
        # Single color = space glyph, all bg
        bg = tuple(int(c) for c in unique_colors[0])
        return (32, (0, 0, 0), bg)

    if len(unique_colors) == 2:
        c0 = tuple(int(c) for c in unique_colors[0])
        c1 = tuple(int(c) for c in unique_colors[1])

        # Build binary mask: True where pixel == c0
        mask_c0 = np.all(rgb == unique_colors[0], axis=2)

        # Try c0 as fg (mask_c0 = fg pixels)
        best_glyph = 0
        best_score = h * w + 1
        best_fg, best_bg = c0, c1

        for idx, glyph_mask in glyph_masks.items():
            if idx in (0, 32):
                continue
            # c0 as fg: mask_c0 should match glyph_mask
            diff0 = np.sum(mask_c0 != glyph_mask)
            if diff0 < best_score:
                best_score = diff0
                best_glyph = idx
                best_fg, best_bg = c0, c1
            # c1 as fg: ~mask_c0 should match glyph_mask
            diff1 = np.sum((~mask_c0) != glyph_mask)
            if diff1 < best_score:
                best_score = diff1
                best_glyph = idx
                best_fg, best_bg = c1, c0

        if best_score == 0:
            return (best_glyph, best_fg, best_bg)

        # No exact match — could be glyph 0 or 32 (all bg)
        # or a partially-matching glyph
        if best_score <= 2:
            return (best_glyph, best_fg, best_bg)

        # Fall back: more frequent color is bg
        count_c0 = np.sum(mask_c0)
        if count_c0 <= h * w // 2:
            return (best_glyph, c0, c1)
        return (best_glyph, c1, c0)

    # >2 unique colors: degenerate case (shouldn't happen with binary atlas)
    # Use most frequent as bg, least frequent as fg
    color_counts: Dict[Tuple[int, int, int], int] = {}
    for pixel in flat:
        key = tuple(int(c) for c in pixel)
        color_counts[key] = color_counts.get(key, 0) + 1
    sorted_colors = sorted(color_counts.items(), key=lambda x: -x[1])
    bg = sorted_colors[0][0]
    fg = sorted_colors[-1][0]
    return (0, fg, bg)


def reverse_render_sheet(
    image: Image.Image,
    glyph_masks: Dict[int, np.ndarray],
    char_w: int = _DEFAULT_CELL_SIZE,
    char_h: int = _DEFAULT_CELL_SIZE,
) -> list:
    """Reverse-render an entire sheet into a 2D grid of (glyph, fg, bg).

    Args:
        image: Rendered sprite sheet (RGBA or RGB).
        glyph_masks: Dict from load_glyph_masks().
        char_w: Cell width in pixels.
        char_h: Cell height in pixels.

    Returns:
        Row-major grid: list[list[(glyph, fg, bg)]]
    """
    arr = np.array(image)
    cols = image.width // char_w
    rows = image.height // char_h

    grid = []
    for y in range(rows):
        row = []
        for x in range(cols):
            px_x = x * char_w
            px_y = y * char_h
            block = arr[px_y:px_y + char_h, px_x:px_x + char_w]
            cell = reverse_render_cell(block, glyph_masks)
            row.append(cell)
        grid.append(row)
    return grid
