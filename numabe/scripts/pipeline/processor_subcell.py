"""
Subcell dithering processor for the Asciicker asset pipeline.

Ported from png2xp.cpp. Converts 2x2 pixel subcells into single XP cells
using perceptual color matching, half-block glyphs, and error diffusion.

Algorithm per 2x2 subcell (ported from ``Do()`` in png2xp.cpp):
  1. Compute gamma-corrected average of 4 pixels.
  2. Look up best (c0, c1, blend_factor) from 216-color palette.
  3. Try dither blocks (glyph 176 = 25% blend, 177 = 50% blend).
  4. Try vertical half-block (glyph 221: left = fg, right = bg).
  5. Try horizontal half-block (glyph 220: top = fg, bottom = bg).
  6. Pick whichever has lowest perceptual error.
  7. If fg == bg, use solid block 219.
  8. Distribute residual error to neighboring subcells (Atkinson).

Palette: 216-color 6x6x6 RGB cube (R,G,B each in {0,51,102,153,204,255}).
This matches the Asciicker terminal palette from png2xp.cpp lines 552-560.

Pipeline position: Stage 3 "quality" processor mode.

Exports
-------
- ``SubcellProcessor`` -- stateful processor (holds gamma tables + palette LUT).
- ``process_subcell()`` -- process a single frame (PIL Image -> cell grid).

Output format matches ``SpriteProcessor.process_image()``:
    ``List[List[Tuple[int, Tuple[int,int,int], Tuple[int,int,int]]]]``

Tags: [PIPELINE:PROCESS] [DATA-CONTRACT:XP] [DATA-CONTRACT:CP437]
      [FLOW:QUALITY] [DEPENDENCY:NUMPY]
"""

import logging
from functools import lru_cache
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Type alias for a single cell: (glyph, fg_rgb, bg_rgb)
Cell = Tuple[int, Tuple[int, int, int], Tuple[int, int, int]]

# ---------------------------------------------------------------------------
# sRGB gamma correction (ported from png2xp.cpp Gamma struct, lines 103-157)
# ---------------------------------------------------------------------------

# Precomputed decode table: sRGB uint8 -> linear int16 (0..8192)
_GAMMA_DEC = np.zeros(256, dtype=np.int16)
for _i in range(256):
    _t = _i / 255.0
    _t = pow((_t + 0.055) / 1.055, 2.4) if _t >= 0.04045 else _t / 12.92
    _GAMMA_DEC[_i] = int(round(_t * 8192.0))

# Precomputed encode table: linear int16 (0..8192) -> sRGB uint8
_GAMMA_ENC = np.zeros(8193, dtype=np.uint8)
for _i in range(8193):
    _t = _i / 8192.0
    _t = 1.055 * pow(_t, 1.0 / 2.4) - 0.055 if _t > 0.0031308 else 12.92 * _t
    _GAMMA_ENC[_i] = int(round(255.0 * max(0.0, min(1.0, _t))))


def _dec(e: int) -> int:
    """sRGB uint8 -> linear int16 (0..8192). Ported from DEC()."""
    return int(_GAMMA_DEC[max(0, min(255, e))])


def _enc(d: int) -> int:
    """Linear int16 (0..8192) -> sRGB uint8. Ported from ENC()."""
    if d < 0:
        return 0
    if d > 8192:
        return 255
    return int(_GAMMA_ENC[d])


# ---------------------------------------------------------------------------
# 216-color palette (6x6x6 RGB cube, ported from lines 552-560)
# ---------------------------------------------------------------------------

def _build_palette() -> np.ndarray:
    """Build the 216-color Asciicker terminal palette.

    Palette index i encodes: R = (i % 6) * 51, G = ((i/6) % 6) * 51,
    B = ((i/36) % 6) * 51. Ported from png2xp.cpp lines 553-560.
    """
    pal = np.zeros((216, 3), dtype=np.uint8)
    for i in range(216):
        j = i
        pal[i, 2] = (j % 6) * 51   # B channel (note: cpp uses pal[i][2] = j%6*51)
        j //= 6
        pal[i, 1] = (j % 6) * 51   # G channel
        j //= 6
        pal[i, 0] = (j % 6) * 51   # R channel
    return pal


_PALETTE = _build_palette()


# ---------------------------------------------------------------------------
# Perceptual error (ported from ERR(), lines 159-223)
# ---------------------------------------------------------------------------

def _err_rgb(c: Tuple[int, int, int], r: Tuple[int, int, int]) -> int:
    """Weighted Manhattan distance. Ported from ERR() (line 214-222).

    Uses: 2*|dR| + 3*|dG| + 1*|dB| (green-weighted, matching png2xp).
    """
    return (2 * abs(c[0] - r[0]) +
            3 * abs(c[1] - r[1]) +
            1 * abs(c[2] - r[2]))


# ---------------------------------------------------------------------------
# Precomputed half-tone blend table (ported from INIT_HACK / HACK, lines 252-279)
# ---------------------------------------------------------------------------

def _build_hack_table(pal: np.ndarray) -> np.ndarray:
    """Precompute blended colors for dither block glyphs.

    hack[g][c0][c1] = gamma-correct blend of pal[c0] and pal[c1].
    g=0 -> 3/4 c0 + 1/4 c1 (glyph 176, 25% coverage)
    g=1 -> 2/4 c0 + 2/4 c1 (glyph 177, 50% coverage)

    Ported from INIT_HACK() lines 253-271.
    """
    hack = np.zeros((2, 216, 216, 3), dtype=np.uint8)
    for gl in range(1, 3):  # gl=1,2 -> g=0,1
        g = gl - 1
        c0_w = 4 - gl
        c1_w = gl
        for c0 in range(216):
            for c1 in range(216):
                for c in range(3):
                    blended = (c0_w * _dec(pal[c0, c]) + c1_w * _dec(pal[c1, c])) // 4
                    hack[g, c0, c1, c] = _enc(blended)
    return hack


def _build_lookup_table(pal: np.ndarray, hack: np.ndarray,
                        step: int = 17) -> Tuple[np.ndarray, int]:
    """Build quantized RGB -> best (c0, c1, blend, solid) lookup table.

    Ported from Make() (lines 281-353) and the TEACH mode (lines 564-653).
    The lookup maps quantized (R,G,B) -> packed uint32:
      bits 0-7:   c0 palette index
      bits 8-15:  c1 palette index
      bits 16-23: best solid palette index
      bits 24-25: (blend_factor - 1), 0 = glyph 176, 1 = glyph 177

    Args:
        pal: (216, 3) palette array.
        hack: (2, 216, 216, 3) precomputed blend table.
        step: Quantization step size (default 17, giving 16 steps per channel).

    Returns:
        (lookup_table, step) where lookup is a flat array indexed by
        quantized (R + G*steps + B*steps*steps).
    """
    steps = 255 // step + 1
    lut = np.zeros(steps * steps * steps, dtype=np.uint32)

    max_dither_steps = 2  # matches png2xp line 319

    for b_q in range(steps):
        b_val = min(b_q * step, 255)
        for g_q in range(steps):
            g_val = min(g_q * step, 255)
            for r_q in range(steps):
                r_val = min(r_q * step, 255)
                src = (r_val, g_val, b_val)

                d_err = None
                d_gl = 0
                d_c0 = 0
                d_c1 = 0

                # Find best dither block pair (ported from Make() lines 288-337)
                for gl in range(1, 3):
                    for c0 in range(216):
                        c0_rgb = (int(pal[c0, 0]), int(pal[c0, 1]), int(pal[c0, 2]))
                        t = c0
                        r0 = t % 6; t //= 6
                        g0 = t % 6; t //= 6
                        b0 = t

                        for c1 in range(216):
                            t2 = c1
                            r1 = t2 % 6; t2 //= 6
                            g1 = t2 % 6; t2 //= 6
                            b1 = t2

                            # Max dither steps limit = 2 (line 319)
                            if (abs(r0 - r1) > max_dither_steps or
                                    abs(g0 - g1) > max_dither_steps or
                                    abs(b0 - b1) > max_dither_steps):
                                continue

                            g_idx = gl - 1
                            G = (int(hack[g_idx, c0, c1, 0]),
                                 int(hack[g_idx, c0, c1, 1]),
                                 int(hack[g_idx, c0, c1, 2]))

                            g_err = 4 * _err_rgb(G, src)
                            if d_err is None or g_err < d_err:
                                d_err = g_err
                                d_gl = gl
                                d_c0 = c0
                                d_c1 = c1

                # Find best solid color (ported from lines 341-350)
                best_solid = 0
                best_solid_err = None
                for i in range(216):
                    ie = _err_rgb(src, (int(pal[i, 0]), int(pal[i, 1]), int(pal[i, 2])))
                    if best_solid_err is None or ie < best_solid_err:
                        best_solid_err = ie
                        best_solid = i

                # Pack into uint32 (line 352)
                if d_c0 == d_c1:
                    packed = d_c0 | (d_c1 << 8) | (best_solid << 16)
                else:
                    packed = d_c0 | (d_c1 << 8) | (best_solid << 16) | ((d_gl - 1) << 24)

                idx = r_q + g_q * steps + b_q * steps * steps
                lut[idx] = int(packed)

    return lut, step


# ---------------------------------------------------------------------------
# Core subcell processing (ported from Do(), lines 356-531)
# ---------------------------------------------------------------------------

def _do_subcell(src: List[Tuple[int, int, int]],
                pal: np.ndarray,
                hack: np.ndarray,
                lut: np.ndarray,
                lut_step: int) -> Tuple[Cell, Tuple[int, int, int]]:
    """Process one 2x2 subcell into a single XP cell.

    Ported from Do() in png2xp.cpp (lines 356-531).

    Args:
        src: 4 pixels as [(R,G,B), ...] in order [lower-left, lower-right,
             upper-left, upper-right] (matching png2xp column-major).
        pal: (216, 3) palette.
        hack: (2, 216, 216, 3) blend table.
        lut: Precomputed lookup table.
        lut_step: Quantization step for the LUT.

    Returns:
        (cell, dev) where cell is (glyph, fg, bg) and dev is the
        (dR, dG, dB) residual error in linear space for Atkinson diffusion.
    """
    ll, lr, ul, ur = src[0], src[1], src[2], src[3]

    lut_offset = lut_step // 2
    lut_steps = 255 // lut_step + 1

    # --- Dither block candidate (lines 367-389) ---
    # Target = gamma-correct average of 4 pixels
    G_avg = [
        _enc((_dec(ll[c]) + _dec(ul[c]) + _dec(lr[c]) + _dec(ur[c])) // 4)
        for c in range(3)
    ]

    # Quantize and look up
    gq = [(G_avg[c] + lut_offset) // lut_step for c in range(3)]
    slot_idx = gq[0] + gq[1] * lut_steps + gq[2] * lut_steps * lut_steps
    slot = int(lut[min(slot_idx, len(lut) - 1)])

    d_c0 = slot & 0xFF
    d_c1 = (slot >> 8) & 0xFF
    d_gl = ((slot >> 24) & 0xFF) + 1

    # Reconstruct blended color
    g_idx = d_gl - 1
    G_dither = (int(hack[g_idx, d_c0, d_c1, 0]),
                int(hack[g_idx, d_c0, d_c1, 1]),
                int(hack[g_idx, d_c0, d_c1, 2]))

    d_err = _err_rgb(G_dither, ll) + _err_rgb(G_dither, lr) + \
            _err_rgb(G_dither, ul) + _err_rgb(G_dither, ur)

    # --- Vertical half-block candidate: glyph 221 (lines 396-425) ---
    # Left half = average of ll, ul; Right half = average of lr, ur
    L = [_enc((_dec(ll[c]) + _dec(ul[c])) // 2) for c in range(3)]
    R = [_enc((_dec(lr[c]) + _dec(ur[c])) // 2) for c in range(3)]

    # Quantize L and R to nearest palette solid
    lq = [(L[c] + lut_offset) // lut_step for c in range(3)]
    rq = [(R[c] + lut_offset) // lut_step for c in range(3)]

    l_slot = int(lut[min(lq[0] + lq[1] * lut_steps + lq[2] * lut_steps * lut_steps, len(lut) - 1)])
    r_slot = int(lut[min(rq[0] + rq[1] * lut_steps + rq[2] * lut_steps * lut_steps, len(lut) - 1)])

    l_solid = (l_slot >> 16) & 0xFF
    r_solid = (r_slot >> 16) & 0xFF

    L_pal = (int(pal[l_solid, 0]), int(pal[l_solid, 1]), int(pal[l_solid, 2]))
    R_pal = (int(pal[r_solid, 0]), int(pal[r_solid, 1]), int(pal[r_solid, 2]))

    v_err = _err_rgb(L_pal, ll) + _err_rgb(L_pal, ul) + \
            _err_rgb(R_pal, lr) + _err_rgb(R_pal, ur)

    # --- Horizontal half-block candidate: glyph 220 (lines 427-455) ---
    # Bottom half = average of ll, lr; Top half = average of ul, ur
    B = [_enc((_dec(ll[c]) + _dec(lr[c])) // 2) for c in range(3)]
    T = [_enc((_dec(ul[c]) + _dec(ur[c])) // 2) for c in range(3)]

    bq = [(B[c] + lut_offset) // lut_step for c in range(3)]
    tq = [(T[c] + lut_offset) // lut_step for c in range(3)]

    b_slot = int(lut[min(bq[0] + bq[1] * lut_steps + bq[2] * lut_steps * lut_steps, len(lut) - 1)])
    t_slot = int(lut[min(tq[0] + tq[1] * lut_steps + tq[2] * lut_steps * lut_steps, len(lut) - 1)])

    b_solid = (b_slot >> 16) & 0xFF
    t_solid = (t_slot >> 16) & 0xFF

    B_pal = (int(pal[b_solid, 0]), int(pal[b_solid, 1]), int(pal[b_solid, 2]))
    T_pal = (int(pal[t_solid, 0]), int(pal[t_solid, 1]), int(pal[t_solid, 2]))

    h_err = _err_rgb(B_pal, ll) + _err_rgb(B_pal, lr) + \
            _err_rgb(T_pal, ul) + _err_rgb(T_pal, ur)

    # --- Pick minimum error (lines 464-527) ---
    dev = [0, 0, 0]

    if d_err < v_err and d_err < h_err:
        # Dither block wins
        bg = (int(pal[d_c0, 0]), int(pal[d_c0, 1]), int(pal[d_c0, 2]))
        fg = (int(pal[d_c1, 0]), int(pal[d_c1, 1]), int(pal[d_c1, 2]))
        glyph = d_gl + 175  # gl=1 -> 176, gl=2 -> 177

        # Accumulate error (lines 478-481)
        for c in range(3):
            dev[c] += _dec(G_dither[c]) - _dec(ll[c])
            dev[c] += _dec(G_dither[c]) - _dec(lr[c])
            dev[c] += _dec(G_dither[c]) - _dec(ul[c])
            dev[c] += _dec(G_dither[c]) - _dec(ur[c])

    elif v_err < h_err:
        # Vertical half-block wins (glyph 221: left = fg, right = bg)
        bg = R_pal
        fg = L_pal
        glyph = 221

        for c in range(3):
            dev[c] += _dec(L_pal[c]) - _dec(ll[c])
            dev[c] += _dec(L_pal[c]) - _dec(ul[c])
            dev[c] += _dec(R_pal[c]) - _dec(lr[c])
            dev[c] += _dec(R_pal[c]) - _dec(ur[c])

    else:
        # Horizontal half-block wins (glyph 220: bottom = bg, top = fg)
        bg = B_pal
        fg = T_pal
        glyph = 220

        for c in range(3):
            dev[c] += _dec(B_pal[c]) - _dec(ll[c])
            dev[c] += _dec(B_pal[c]) - _dec(lr[c])
            dev[c] += _dec(T_pal[c]) - _dec(ul[c])
            dev[c] += _dec(T_pal[c]) - _dec(ur[c])

    # If fg == bg, use solid block (line 524-527)
    if fg == bg:
        glyph = 219

    return (glyph, fg, bg), tuple(dev)


# ---------------------------------------------------------------------------
# Atkinson error diffusion (ported from lines 790-842)
# ---------------------------------------------------------------------------

def _apply_atkinson_error(pixels: np.ndarray, x: int, y: int,
                          w: int, h: int, dev: Tuple[int, int, int]) -> None:
    """Distribute residual error to neighboring 2x2 blocks using Atkinson weights.

    Ported from png2xp.cpp lines 790-842. Atkinson distributes 6/8 of the
    error to 6 neighbors (each gets 1/8). The dev values are already
    accumulated over 4 pixels, so we divide by -4*8 = -32.

    Modifies pixels array in place (matching C++ mutation pattern).
    The error is applied to the 2x2 pixel blocks at offsets:
      (+2, 0), (+4, 0),        -- right neighbors
      (-2, +2), (0, +2), (+2, +2),  -- row below
      (0, +4)                  -- two rows below

    Args:
        pixels: (H, W, 3) uint8 array, modified in place.
        x, y: Current 2x2 block's top-left corner (in pixel coordinates).
        w, h: Image dimensions.
        dev: Linear-space error (dR, dG, dB) accumulated over 4 pixels.
    """
    # Atkinson: 6x 1/8, divided over 4 pixels per block = / (-4 * 8)
    err = [d // -32 for d in dev]

    def _add(px: int, py: int) -> None:
        if 0 <= px < w and 0 <= py < h:
            for c in range(3):
                old_lin = _dec(int(pixels[py, px, c]))
                pixels[py, px, c] = _enc(old_lin + err[c])

    # Apply to each pixel in the 6 neighboring 2x2 blocks
    # Right +2 (lines 798-801)
    if x + 2 < w:
        for dy in range(2):
            for dx in range(2):
                _add(x + 2 + dx, y + dy)
        # Right +4 (lines 803-808)
        if x + 4 < w:
            for dy in range(2):
                for dx in range(2):
                    _add(x + 4 + dx, y + dy)

    # Below +2 row (lines 812-833)
    if y + 2 < h:
        # Below-left -2 (lines 814-820)
        if x >= 2:
            for dy in range(2):
                for dx in range(2):
                    _add(x - 2 + dx, y + 2 + dy)
        # Below center (lines 822-825)
        for dy in range(2):
            for dx in range(2):
                _add(x + dx, y + 2 + dy)
        # Below-right +2 (lines 827-833)
        if x + 2 < w:
            for dy in range(2):
                for dx in range(2):
                    _add(x + 2 + dx, y + 2 + dy)
        # Two rows below (lines 835-841)
        if y + 4 < h:
            for dy in range(2):
                for dx in range(2):
                    _add(x + dx, y + 4 + dy)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _process_frame_numpy(pixels: np.ndarray, pal: np.ndarray, hack: np.ndarray,
                         lut: np.ndarray, lut_step: int) -> np.ndarray:
    """Vectorized batch processing of all cells without error diffusion.

    Processes all (cell_h, cell_w) cells simultaneously using numpy
    broadcasting and array indexing. No Python loop over individual cells.

    Args:
        pixels: (H, W, 3) uint8 image array (H and W must be even).
        pal: (216, 3) palette array.
        hack: (2, 216, 216, 3) precomputed blend table.
        lut: Packed uint32 lookup table.
        lut_step: Quantization step matching the lut.

    Returns:
        (cell_h, cell_w, 7) int32 array where axis-2 holds:
          [glyph, fg_r, fg_g, fg_b, bg_r, bg_g, bg_b]

    Notes:
        - Error diffusion is intentionally omitted; it is inherently
          sequential (each cell's output modifies neighbors' input pixels).
        - Magenta transparent cells (all 4 pixels r>240, g<15, b>240)
          are emitted as glyph=0 fg=bg=(255,0,255).
    """
    h, w = pixels.shape[:2]
    cell_h = h // 2
    cell_w = w // 2
    lut_steps = 255 // lut_step + 1
    lut_offset = lut_step // 2

    # ---- Extract the 4 corner pixel blocks --------------------------------
    # Each has shape (cell_h, cell_w, 3) — no copy needed (views into pixels)
    p00 = pixels[0::2, 0::2, :]   # top-left     = (y,   x)   = "ll" in _do_subcell
    p01 = pixels[0::2, 1::2, :]   # top-right    = (y,   x+1) = "lr"
    p10 = pixels[1::2, 0::2, :]   # bottom-left  = (y+1, x)   = "ul"
    p11 = pixels[1::2, 1::2, :]   # bottom-right = (y+1, x+1) = "ur"

    # Cast to int32 for arithmetic; (cell_h, cell_w, 3)
    p00i = p00.astype(np.int32)
    p01i = p01.astype(np.int32)
    p10i = p10.astype(np.int32)
    p11i = p11.astype(np.int32)

    # ---- Magenta mask: all 4 pixels are transparent -----------------------
    def _is_magenta(px: np.ndarray) -> np.ndarray:
        # (cell_h, cell_w) bool
        return (px[:, :, 0] > 240) & (px[:, :, 1] < 15) & (px[:, :, 2] > 240)

    magenta_mask = (_is_magenta(p00) & _is_magenta(p01) &
                    _is_magenta(p10) & _is_magenta(p11))  # (cell_h, cell_w)

    # ---- Decode to linear space via _GAMMA_DEC table ----------------------
    # _GAMMA_DEC is shape (256,), indexed by uint8 values -> int16.
    # Cast to int32 for accumulation headroom.
    dec00 = _GAMMA_DEC[p00].astype(np.int32)   # (cell_h, cell_w, 3)
    dec01 = _GAMMA_DEC[p01].astype(np.int32)
    dec10 = _GAMMA_DEC[p10].astype(np.int32)
    dec11 = _GAMMA_DEC[p11].astype(np.int32)

    # ---- Dither block candidate -------------------------------------------
    # G_avg = enc(mean of 4 decoded pixels), shape (cell_h, cell_w, 3)
    avg_lin = (dec00 + dec01 + dec10 + dec11) // 4          # (cell_h, cell_w, 3) int32
    # Clamp to [0, 8192] before indexing _GAMMA_ENC
    avg_lin_c = np.clip(avg_lin, 0, 8192).astype(np.int32)
    g_avg = _GAMMA_ENC[avg_lin_c].astype(np.int32)          # (cell_h, cell_w, 3) uint8 -> int32

    # Quantize g_avg to LUT index
    gq = np.clip((g_avg + lut_offset) // lut_step, 0, lut_steps - 1)  # (cell_h, cell_w, 3)
    slot_idx_d = gq[:, :, 0] + gq[:, :, 1] * lut_steps + gq[:, :, 2] * lut_steps * lut_steps
    slot_idx_d = np.clip(slot_idx_d, 0, len(lut) - 1)
    slot_d = lut[slot_idx_d].astype(np.int64)               # (cell_h, cell_w) uint32 -> int64

    d_c0 = (slot_d & 0xFF).astype(np.int32)                 # (cell_h, cell_w)
    d_c1 = ((slot_d >> 8) & 0xFF).astype(np.int32)
    d_gl = (((slot_d >> 24) & 0xFF) + 1).astype(np.int32)   # 1 or 2
    g_idx = d_gl - 1                                          # 0 or 1

    # Look up hack[g_idx, d_c0, d_c1] — fancy indexing, shape (cell_h, cell_w, 3)
    g_dither = hack[g_idx, d_c0, d_c1].astype(np.int32)     # (cell_h, cell_w, 3)

    # Dither error = sum of weighted Manhattan over 4 pixels
    # _err_rgb: 2*|dR| + 3*|dG| + 1*|dB|
    _weights = np.array([2, 3, 1], dtype=np.int32)

    def _err_vec(color: np.ndarray, ref: np.ndarray) -> np.ndarray:
        # color, ref: (cell_h, cell_w, 3). Returns (cell_h, cell_w).
        return np.sum(np.abs(color - ref) * _weights, axis=2)

    d_err = (_err_vec(g_dither, p00i) + _err_vec(g_dither, p01i) +
             _err_vec(g_dither, p10i) + _err_vec(g_dither, p11i))   # (cell_h, cell_w)

    # ---- Vertical half-block candidate (glyph 221) -----------------------
    # Left  = enc(mean of p00, p10); Right = enc(mean of p01, p11)
    l_lin = np.clip((dec00 + dec10) // 2, 0, 8192)
    r_lin = np.clip((dec01 + dec11) // 2, 0, 8192)
    L = _GAMMA_ENC[l_lin].astype(np.int32)   # (cell_h, cell_w, 3)
    R = _GAMMA_ENC[r_lin].astype(np.int32)

    lq = np.clip((L + lut_offset) // lut_step, 0, lut_steps - 1)
    rq = np.clip((R + lut_offset) // lut_step, 0, lut_steps - 1)
    slot_l = lut[np.clip(lq[:, :, 0] + lq[:, :, 1] * lut_steps +
                         lq[:, :, 2] * lut_steps * lut_steps, 0, len(lut) - 1)].astype(np.int64)
    slot_r = lut[np.clip(rq[:, :, 0] + rq[:, :, 1] * lut_steps +
                         rq[:, :, 2] * lut_steps * lut_steps, 0, len(lut) - 1)].astype(np.int64)

    l_solid = ((slot_l >> 16) & 0xFF).astype(np.int32)
    r_solid = ((slot_r >> 16) & 0xFF).astype(np.int32)
    L_pal = pal[l_solid].astype(np.int32)    # (cell_h, cell_w, 3)
    R_pal = pal[r_solid].astype(np.int32)

    v_err = (_err_vec(L_pal, p00i) + _err_vec(L_pal, p10i) +
             _err_vec(R_pal, p01i) + _err_vec(R_pal, p11i))

    # ---- Horizontal half-block candidate (glyph 220) ---------------------
    # Bottom = enc(mean of p00, p01); Top = enc(mean of p10, p11)
    b_lin = np.clip((dec00 + dec01) // 2, 0, 8192)
    t_lin = np.clip((dec10 + dec11) // 2, 0, 8192)
    B = _GAMMA_ENC[b_lin].astype(np.int32)
    T = _GAMMA_ENC[t_lin].astype(np.int32)

    bq = np.clip((B + lut_offset) // lut_step, 0, lut_steps - 1)
    tq = np.clip((T + lut_offset) // lut_step, 0, lut_steps - 1)
    slot_b = lut[np.clip(bq[:, :, 0] + bq[:, :, 1] * lut_steps +
                         bq[:, :, 2] * lut_steps * lut_steps, 0, len(lut) - 1)].astype(np.int64)
    slot_t = lut[np.clip(tq[:, :, 0] + tq[:, :, 1] * lut_steps +
                         tq[:, :, 2] * lut_steps * lut_steps, 0, len(lut) - 1)].astype(np.int64)

    b_solid = ((slot_b >> 16) & 0xFF).astype(np.int32)
    t_solid = ((slot_t >> 16) & 0xFF).astype(np.int32)
    B_pal = pal[b_solid].astype(np.int32)
    T_pal = pal[t_solid].astype(np.int32)

    h_err = (_err_vec(B_pal, p00i) + _err_vec(B_pal, p01i) +
             _err_vec(T_pal, p10i) + _err_vec(T_pal, p11i))

    # ---- Pick winner per cell -------------------------------------------
    # 0 = dither, 1 = vertical, 2 = horizontal
    # np.where is element-wise: pick dither when it beats both others
    dither_wins = (d_err < v_err) & (d_err < h_err)   # (cell_h, cell_w)
    vert_wins   = (~dither_wins) & (v_err < h_err)
    # horiz_wins = ~dither_wins & ~vert_wins

    # Glyph: dither=176 or 177, vert=221, horiz=220
    glyph_dither = d_gl + 175                          # gl=1->176, gl=2->177
    glyph = np.where(dither_wins, glyph_dither,
             np.where(vert_wins,  221, 220)).astype(np.int32)

    # fg/bg per winner
    fg = np.where(dither_wins[:, :, None],
                  pal[d_c1],
         np.where(vert_wins[:, :, None],
                  L_pal, T_pal)).astype(np.int32)     # (cell_h, cell_w, 3)

    bg = np.where(dither_wins[:, :, None],
                  pal[d_c0],
         np.where(vert_wins[:, :, None],
                  R_pal, B_pal)).astype(np.int32)

    # Solid block override: if fg == bg → glyph 219
    fg_eq_bg = np.all(fg == bg, axis=2)               # (cell_h, cell_w)
    glyph = np.where(fg_eq_bg, 219, glyph)

    # Magenta transparent cells override everything
    magenta_rgb = np.array([255, 0, 255], dtype=np.int32)
    glyph = np.where(magenta_mask, 0, glyph)
    fg = np.where(magenta_mask[:, :, None],
                  np.broadcast_to(magenta_rgb, fg.shape), fg)
    bg = np.where(magenta_mask[:, :, None],
                  np.broadcast_to(magenta_rgb, bg.shape), bg)

    # ---- Pack result into (cell_h, cell_w, 7) ----------------------------
    result = np.stack([glyph,
                       fg[:, :, 0], fg[:, :, 1], fg[:, :, 2],
                       bg[:, :, 0], bg[:, :, 1], bg[:, :, 2]], axis=2)
    return result.astype(np.int32)


def _result_to_grid(result: np.ndarray) -> List[List[Cell]]:
    """Convert (cell_h, cell_w, 7) int32 array to row-major Cell grid."""
    cell_h, cell_w = result.shape[:2]
    grid: List[List[Cell]] = []
    for cy in range(cell_h):
        row: List[Cell] = []
        for cx in range(cell_w):
            v = result[cy, cx]
            row.append((int(v[0]),
                        (int(v[1]), int(v[2]), int(v[3])),
                        (int(v[4]), int(v[5]), int(v[6]))))
        grid.append(row)
    return grid


class SubcellProcessor:
    """Subcell dithering processor with precomputed lookup tables.

    Instantiate once and reuse — the LUT is expensive to build.

    Args:
        lut_step: Quantization step for the palette lookup table.
            Smaller = more accurate but slower to build.
            Default 17 matches png2xp typical usage.
        error_diffusion: Whether to apply Atkinson cross-cell error diffusion.
        use_numpy: When True (default), use vectorized numpy path for
            processing cells without error diffusion. Falls back to the
            scalar path automatically when error_diffusion=True, since
            Atkinson diffusion is inherently sequential (each cell's
            output modifies neighboring cells' input pixels). Setting this
            to False always uses the original scalar path regardless of
            error_diffusion setting — useful for debugging or regression
            testing to confirm output equivalence.

    Tags: [PIPELINE:PROCESS] [FLOW:QUALITY]
    """

    def __init__(self, lut_step: int = 17, error_diffusion: bool = True,
                 use_numpy: bool = True):
        self._pal = _PALETTE
        self._hack = _build_hack_table(self._pal)
        logger.info("Building subcell LUT (step=%d)...", lut_step)
        self._lut, self._step = _build_lookup_table(
            self._pal, self._hack, step=lut_step,
        )
        self._error_diffusion = error_diffusion
        self._use_numpy = use_numpy
        logger.info(
            "SubcellProcessor ready (216-color palette, step=%d, "
            "use_numpy=%s, error_diffusion=%s)",
            lut_step, use_numpy, error_diffusion,
        )

    def process_frame(self, image) -> List[List[Cell]]:
        """Convert a PIL Image or numpy array to a cell grid using subcell dithering.

        Image dimensions MUST be even (matching png2xp.cpp line 685-689:
        ``if ((w|h)&1) ... return -2``).

        Each 2x2 pixel block becomes one cell. Output grid is (H/2) x (W/2).

        When use_numpy=True and error_diffusion=False the entire frame is
        processed in one vectorized numpy pass (no Python cell loop).
        When error_diffusion=True the scalar path is used to preserve the
        sequential nature of Atkinson diffusion.

        Args:
            image: PIL Image (RGB/RGBA) or numpy (H, W, 3+) array.

        Returns:
            Row-major grid of (glyph, fg_rgb, bg_rgb) tuples.

        Tags: [PIPELINE:PROCESS] [FLOW:QUALITY]
        """
        if hasattr(image, "convert"):
            arr = np.array(image.convert("RGB"))
        else:
            arr = np.asarray(image)[:, :, :3].copy()

        h, w = arr.shape[:2]
        if w % 2 != 0 or h % 2 != 0:
            raise ValueError(
                f"Image dimensions must be even for subcell processing, "
                f"got {w}x{h}"
            )

        cell_w = w // 2
        cell_h = h // 2
        logger.info("Subcell processor: %dx%d pixels -> %dx%d cells",
                     w, h, cell_w, cell_h)

        # ---- Fast numpy path (no error diffusion) -----------------------
        if self._use_numpy and not self._error_diffusion:
            pixels = arr.astype(np.uint8)   # read-only view is fine; no diffusion
            result = _process_frame_numpy(
                pixels, self._pal, self._hack, self._lut, self._step,
            )
            return _result_to_grid(result)

        # ---- Scalar path (with or without error diffusion) --------------
        # Make a mutable copy for error diffusion
        pixels = arr.astype(np.uint8).copy()

        grid: List[List[Cell]] = []

        # Process in column-major order matching png2xp.cpp (lines 729-731):
        # for x in range(0, w, 2): for y in range(0, h, 2):
        # But we store results in row-major grid for consistency.
        # Pre-allocate grid
        for _ in range(cell_h):
            grid.append([None] * cell_w)  # type: ignore

        for x in range(0, w, 2):
            for y in range(0, h, 2):
                # 4 source pixels: ll, lr, ul, ur
                # Matching png2xp.cpp ordering (lines 741-747):
                #   src[0] = pix[x + w*y]         -> (x, y)
                #   src[1] = pix[x+1 + w*y]       -> (x+1, y)
                #   src[2] = pix[x + w*y + w]      -> (x, y+1)
                #   src[3] = pix[x+1 + w*y + w]    -> (x+1, y+1)
                src = [
                    (int(pixels[y, x, 0]), int(pixels[y, x, 1]), int(pixels[y, x, 2])),
                    (int(pixels[y, x + 1, 0]), int(pixels[y, x + 1, 1]), int(pixels[y, x + 1, 2])),
                    (int(pixels[y + 1, x, 0]), int(pixels[y + 1, x, 1]), int(pixels[y + 1, x, 2])),
                    (int(pixels[y + 1, x + 1, 0]), int(pixels[y + 1, x + 1, 1]), int(pixels[y + 1, x + 1, 2])),
                ]

                # Transparent cell: all 4 pixels are magenta → preserve as-is
                if all(r > 240 and g < 15 and b > 240 for r, g, b in src):
                    grid[y // 2][x // 2] = (0, (255, 0, 255), (255, 0, 255))
                    continue

                cell, dev = _do_subcell(src, self._pal, self._hack,
                                        self._lut, self._step)

                grid[y // 2][x // 2] = cell

                # Error diffusion (lines 790-842)
                if self._error_diffusion:
                    _apply_atkinson_error(pixels, x, y, w, h, dev)

        return grid


def process_subcell(image, lut_step: int = 17,
                    error_diffusion: bool = True,
                    use_numpy: bool = True) -> List[List[Cell]]:
    """Convenience function: create a SubcellProcessor and process one frame.

    For multiple frames, instantiate SubcellProcessor once and call
    process_frame() repeatedly to reuse the LUT.

    Tags: [PIPELINE:PROCESS] [FLOW:QUALITY]
    """
    proc = get_subcell_processor(lut_step=lut_step, error_diffusion=error_diffusion,
                                 use_numpy=use_numpy)
    return proc.process_frame(image)


@lru_cache(maxsize=8)
def _cached_subcell_processor(lut_step: int, error_diffusion: bool,
                               use_numpy: bool) -> SubcellProcessor:
    """Create/cache expensive SubcellProcessor instances by configuration."""
    return SubcellProcessor(lut_step=lut_step, error_diffusion=error_diffusion,
                            use_numpy=use_numpy)


def get_subcell_processor(lut_step: int = 17, error_diffusion: bool = True,
                          use_numpy: bool = True) -> SubcellProcessor:
    """Return a shared SubcellProcessor instance for the given settings."""
    return _cached_subcell_processor(int(lut_step), bool(error_diffusion),
                                     bool(use_numpy))
