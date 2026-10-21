#!/usr/bin/env python3
"""png2xp2png.py -- Pixel-exact XP sprite sheet renderer.

Each XP cell maps to exactly SCALE×SCALE pixels.  Proportions are always
exact: a 16×16 XP at --scale 100 -> 1600×1600 PNG.

Modes
-----
XP -> PNG (primary -- use with existing .xp sprite sheets)
  python scripts/png2xp2png.py assets/sprites/player-1112.xp -o /tmp/player.png --scale 10

PNG -> XP -> PNG (round-trip; requires built workspace/png2xp/png2xp binary)
  python scripts/png2xp2png.py my_sprite.png --plt my.plt -o /tmp/rt.png --scale 10

Font rendering (optional, --font)
-----------------------------------
  --font          auto-select the best-matching BDF from assets/fonts/ for the scale
  --font PATH     use a specific BDF file
  Without --font: geometric block-only renderer (fast, no file dependency)

Glyph matching algorithm
  CP437 code -> Python cp437 codec -> Unicode codepoint -> BDF ENCODING lookup.
  Bitmap is nearest-neighbour scaled to SCALE×SCALE pixels.
  Missing glyphs fall back to the geometric renderer.

Block-glyph geometric fallback (no font)
-----------------------------------------
  219  solid block    -> fill with bg
  220  lower half     -> top half = bg, bottom half = fg
  221  left half      -> left half = fg, right half = bg
  176  light dither   -> 2x2 tile: fg at (0,0) only  (25% fg)
  177  med dither     -> checkerboard                 (50% fg)
  178  dark dither    -> inverse 2x2 tile             (75% fg)
   32  space          -> fill with bg
  other               -> fill with bg; fg dot at cell centre
  bg=(255,0,255)      -> transparent -> grey checkerboard

Grid overlay (--grid): 1-pixel dark lines at every cell boundary.
"""

import argparse
import gzip
import os
import struct
import subprocess
import sys
import tempfile
import zlib


# Shared transparency constants live in pipeline_v2.xp_codec. Bootstrap the
# import path so this script works whether invoked from scripts/ or directly.
try:
    from pipeline_v2.xp_codec import (
        LEGACY_YELLOW_KEY_RGB,
        MAGENTA_KEY_RGB,
        sprite_transparency_keys,
    )
except ImportError:
    _here = os.path.dirname(os.path.abspath(__file__))
    for _candidate in (
        os.path.join(_here, "..", "pipeline-v3", "src"),
        os.path.join(_here, "..", "src"),
    ):
        _candidate = os.path.normpath(_candidate)
        if os.path.isdir(os.path.join(_candidate, "pipeline_v2")):
            sys.path.insert(0, _candidate)
            break
    from pipeline_v2.xp_codec import (  # type: ignore[no-redef]
        LEGACY_YELLOW_KEY_RGB,
        MAGENTA_KEY_RGB,
        sprite_transparency_keys,
    )


# ---------------------------------------------------------------------------
# BDF font loader & glyph matcher
# ---------------------------------------------------------------------------

_BDF_SIZES = [4, 6, 8, 10, 12, 14, 16, 18, 20, 24, 28, 32, 36, 40]


def _find_font_dir():
    """Return the assets/fonts/ directory relative to this script."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "assets", "fonts"))


def auto_select_bdf(scale):
    """Pick the BDF path whose cell size best matches *scale*.

    Strategy: largest available size <= scale (so we scale up, not down).
    If scale < smallest available, use the smallest.
    """
    font_dir = _find_font_dir()
    best = _BDF_SIZES[0]
    for s in _BDF_SIZES:
        if s <= scale:
            best = s
    path = os.path.join(font_dir, f"cp437_{best}x{best}.png.bdf")
    return path, best


class BdfFont:
    """Loaded BDF font: maps Unicode codepoints -> NxN bit-masks (list of 0/1)."""

    def __init__(self, path):
        self.path = path
        self.cell_w = 0
        self.cell_h = 0
        self.ascent = 0
        # unicode_codepoint -> flat list[int] of length cell_w*cell_h (0=bg, 1=fg)
        self._masks = {}
        self._load(path)

    def _load(self, path):
        with open(path, "r", encoding="ascii", errors="ignore") as f:
            lines = f.readlines()

        font_w = font_h = ascent = descent = None
        i = 0
        while i < len(lines):
            s = lines[i].strip()
            if s.startswith("FONTBOUNDINGBOX"):
                parts = s.split()
                font_w, font_h = int(parts[1]), int(parts[2])
            elif s.startswith("FONT_ASCENT"):
                ascent = int(s.split()[1])
            elif s.startswith("FONT_DESCENT"):
                descent = int(s.split()[1])
            elif s == "CHARS":
                pass  # skip
            elif s.startswith("STARTCHAR"):
                i += 1
                cp = bbx = None
                rows = []
                while i < len(lines):
                    s2 = lines[i].strip()
                    if s2.startswith("ENCODING"):
                        cp = int(s2.split()[1])
                    elif s2.startswith("BBX"):
                        p = s2.split()
                        bbx = (int(p[1]), int(p[2]), int(p[3]), int(p[4]))
                    elif s2 == "BITMAP":
                        if bbx:
                            bw, bh, bx, by = bbx
                            for _ in range(bh):
                                i += 1
                                row_hex = lines[i].strip()
                                bits = int(row_hex, 16)
                                rows.append((bits, len(row_hex) * 4))
                    elif s2 == "ENDCHAR":
                        if cp is not None and bbx is not None and font_w and font_h:
                            mask = self._make_mask(cp, bbx, rows, font_w, font_h, ascent or font_h)
                            self._masks[cp] = mask
                        break
                    i += 1
            i += 1

        if font_w is None or font_h is None:
            raise ValueError(f"BDF missing FONTBOUNDINGBOX: {path}")
        self.cell_w = font_w
        self.cell_h = font_h
        self.ascent = ascent if ascent is not None else font_h

    def _make_mask(self, cp, bbx, rows, cw, ch, ascent):
        """Build a flat cw*ch mask from BDF glyph data."""
        bw, bh, bx, by = bbx
        mask = [0] * (cw * ch)
        top = ascent - (by + bh)   # top row offset within the cell
        for row_i, (bits, row_w) in enumerate(rows):
            y = top + row_i
            if y < 0 or y >= ch:
                continue
            for x in range(bw):
                if (bits >> (row_w - 1 - x)) & 1:
                    xx = bx + x
                    if 0 <= xx < cw:
                        mask[y * cw + xx] = 1
        return mask

    def get_mask(self, unicode_cp):
        """Return the flat mask for *unicode_cp*, or None if not in font."""
        return self._masks.get(unicode_cp)

    def scale_mask(self, mask, target):
        """Nearest-neighbour scale mask from (cell_w x cell_h) to (target x target).

        Returns a flat list of length target*target.
        """
        cw, ch = self.cell_w, self.cell_h
        if cw == target and ch == target:
            return mask
        out = [0] * (target * target)
        for oy in range(target):
            sy = int(oy * ch / target)
            sy = min(sy, ch - 1)
            for ox in range(target):
                sx = int(ox * cw / target)
                sx = min(sx, cw - 1)
                out[oy * target + ox] = mask[sy * cw + sx]
        return out


# ---------------------------------------------------------------------------
# CP437 -> Unicode codepoint
# ---------------------------------------------------------------------------

def _cp437_to_unicode(code):
    """Convert a CP437 byte value (0-255) to its Unicode codepoint."""
    if 0 <= code <= 255:
        try:
            return ord(bytes([code]).decode("cp437"))
        except Exception:
            pass
    return code  # pass through for >255 or failures


# Pre-build the full lookup table once
_CP437_TO_UNI = {i: _cp437_to_unicode(i) for i in range(256)}


# ---------------------------------------------------------------------------
# XP reader
# ---------------------------------------------------------------------------

def read_xp(path):
    """Return (version, layers).

    Each layer: (width, height, cells).
    cells flat, column-major: index = x*height + y.
    Each cell: (glyph: int, fg: bytes[3], bg: bytes[3]).
    """
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)

    if len(raw) < 8:
        raise ValueError(f"XP file too small: {path}")

    version, layer_count = struct.unpack_from("<iI", raw, 0)
    offset = 8
    layers = []

    for _ in range(layer_count):
        if offset + 8 > len(raw):
            raise ValueError("Truncated XP layer header")
        w, h = struct.unpack_from("<ii", raw, offset)
        offset += 8
        needed = w * h * 10
        if offset + needed > len(raw):
            raise ValueError(f"Truncated XP cell data (need {needed} bytes)")
        cells = []
        for _ in range(w * h):
            glyph = struct.unpack_from("<I", raw, offset)[0]
            offset += 4
            fg = bytes(raw[offset:offset + 3]); offset += 3
            bg = bytes(raw[offset:offset + 3]); offset += 3
            cells.append((glyph, fg, bg))
        layers.append((w, h, cells))

    return version, layers


# ---------------------------------------------------------------------------
# Cell renderer — geometric fallback
# ---------------------------------------------------------------------------

# Backwards-compatible alias kept for any external callers that imported the
# old constant. New code should import MAGENTA_KEY_RGB from pipeline_v2.xp_codec.
TRANSPARENT_BG = MAGENTA_KEY_RGB
_GRID_COLOR = (40, 40, 40, 255)

# Separator colour for triptych view (dark grey, fully opaque)
_SEP_COLOR = (30, 30, 30, 255)
_SEP_W = 4  # separator width in pixels

# Default key set used when no per-sprite key is supplied (renders called
# without a `keys` argument still treat magenta + legacy yellow as transparent).
_DEFAULT_KEYS: frozenset[tuple[int, int, int]] = frozenset({MAGENTA_KEY_RGB, LEGACY_YELLOW_KEY_RGB})


def _is_transparent(color, keys=_DEFAULT_KEYS):
    """Return True if (r,g,b) of *color* matches any transparency key."""
    return (color[0], color[1], color[2]) in keys


def _fg_px(fg):
    return (fg[0], fg[1], fg[2], 255)


def _bg_px(bg):
    return (bg[0], bg[1], bg[2], 255)


def _tp_px():
    """Fully transparent pixel."""
    return (0, 0, 0, 0)


def render_cell_geo(glyph, fg, bg, scale, px, py, keys=_DEFAULT_KEYS):
    """Geometric block-glyph renderer.  Returns (r, g, b, a).

    Both fg and bg are independently checked against the transparency *keys*:
    a key-matching fg makes fg pixels transparent, a key-matching bg makes bg
    pixels transparent. This mirrors upstream sprite.cpp.
    """
    bg_tp = _is_transparent(bg, keys)
    fg_tp = _is_transparent(fg, keys)
    fg_px_ = (_tp_px() if fg_tp else _fg_px(fg))
    bg_px_ = (_tp_px() if bg_tp else _bg_px(bg))

    half = scale // 2

    if glyph in (219, 32, 0):
        return bg_px_

    if glyph == 220:                        # lower half ▄: top=bg, bottom=fg
        return fg_px_ if py >= half else bg_px_

    if glyph == 221:                        # left half  ▌: left=fg, right=bg
        return fg_px_ if px < half else bg_px_

    if glyph == 222:                        # right half ▐: left=bg, right=fg
        return fg_px_ if px >= half else bg_px_

    if glyph == 223:                        # upper half ▀: top=fg, bottom=bg
        return fg_px_ if py < half else bg_px_

    if glyph == 176:                        # light dither ░ 25% fg
        on_fg = px % 2 == 0 and py % 2 == 0
        return fg_px_ if on_fg else bg_px_

    if glyph == 177:                        # medium dither ▒ 50%
        on_fg = (px + py) % 2 == 0
        return fg_px_ if on_fg else bg_px_

    if glyph == 178:                        # dark dither ▓ 75% fg
        on_bg = px % 2 == 0 and py % 2 == 0
        return bg_px_ if on_bg else fg_px_

    # unknown — bg fill + tiny fg dot at centre
    cx, cy = scale // 2, scale // 2
    if abs(px - cx) <= 1 and abs(py - cy) <= 1:
        return fg_px_
    return bg_px_


# ---------------------------------------------------------------------------
# Cell renderer — BDF font path
# ---------------------------------------------------------------------------

def render_cell_font(glyph, fg, bg, scale, px, py, font, mask_cache, keys=_DEFAULT_KEYS):
    """Font-based renderer.  Returns (r, g, b, a).  Falls back to geo for missing glyphs.

    Both fg and bg are checked against transparency *keys* independently.
    """
    bg_tp = _is_transparent(bg, keys)
    fg_tp = _is_transparent(fg, keys)

    # glyph matching: CP437 -> Unicode -> BDF mask
    uni = _CP437_TO_UNI.get(glyph & 0xFF, glyph)
    if uni not in mask_cache:
        raw_mask = font.get_mask(uni)
        mask_cache[uni] = font.scale_mask(raw_mask, scale) if raw_mask is not None else None

    scaled = mask_cache[uni]
    if scaled is None:
        return render_cell_geo(glyph, fg, bg, scale, px, py, keys)

    on_fg = bool(scaled[py * scale + px])
    if on_fg:
        return _tp_px() if fg_tp else _fg_px(fg)
    return _tp_px() if bg_tp else _bg_px(bg)


# ---------------------------------------------------------------------------
# Layer renderer
# ---------------------------------------------------------------------------

def render_layer(width, height, cells, scale, grid, font=None, keys=_DEFAULT_KEYS):
    """Render one XP layer to a flat RGBA bytearray (row-major).

    cells column-major: index = x * height + y.
    Returns (img_w, img_h, bytearray).  4 bytes per pixel (RGBA).
    *keys* is the set of transparent color keys (magenta, legacy yellow, and
    optionally the per-sprite L0[0,0].bg).
    """
    img_w = width  * scale + (width  - 1 if grid else 0)
    img_h = height * scale + (height - 1 if grid else 0)
    stride = img_w * 4
    img = bytearray(img_w * img_h * 4)   # all zeros = fully transparent

    mask_cache = {}

    for cx in range(width):
        for cy in range(height):
            glyph, fg, bg = cells[cx * height + cy]

            ox = cx * scale + (cx if grid else 0)
            oy = cy * scale + (cy if grid else 0)

            for py in range(scale):
                row_start = (oy + py) * stride + ox * 4
                for px in range(scale):
                    if font is not None:
                        r, g, b, a = render_cell_font(glyph, fg, bg, scale, px, py, font, mask_cache, keys)
                    else:
                        r, g, b, a = render_cell_geo(glyph, fg, bg, scale, px, py, keys)
                    off = row_start + px * 4
                    img[off]     = r
                    img[off + 1] = g
                    img[off + 2] = b
                    img[off + 3] = a

    if grid:
        gc = _GRID_COLOR
        for cx in range(1, width):
            lx = cx * scale + cx - 1
            for gy in range(img_h):
                off = gy * stride + lx * 4
                img[off]=gc[0]; img[off+1]=gc[1]; img[off+2]=gc[2]; img[off+3]=gc[3]
        for cy in range(1, height):
            ly = cy * scale + cy - 1
            off = ly * stride
            for gx in range(img_w):
                img[off]=gc[0]; img[off+1]=gc[1]; img[off+2]=gc[2]; img[off+3]=gc[3]
                off += 4

    return img_w, img_h, img


def render_triptych(layers, scale, grid, font=None, keys=_DEFAULT_KEYS):
    """Render all XP layers side by side into one RGBA image.

    Returns (img_w, img_h, bytearray).
    Layers are separated by _SEP_W opaque dark columns.
    """
    rendered = []
    for w, h, cells in layers:
        lw, lh, buf = render_layer(w, h, cells, scale, grid, font, keys)
        rendered.append((lw, lh, buf))

    sep = _SEP_W
    total_w = sum(lw for lw, _, _ in rendered) + sep * (len(rendered) - 1)
    total_h = max(lh for _, lh, _ in rendered)
    out_stride = total_w * 4
    out = bytearray(total_w * total_h * 4)

    x_off = 0
    for li, (lw, lh, buf) in enumerate(rendered):
        src_stride = lw * 4
        for y in range(lh):
            src_row = buf[y * src_stride:(y + 1) * src_stride]
            dst_start = y * out_stride + x_off * 4
            out[dst_start:dst_start + src_stride] = src_row
        x_off += lw
        # draw separator
        if li < len(rendered) - 1:
            for y in range(total_h):
                for sx in range(sep):
                    off = y * out_stride + (x_off + sx) * 4
                    out[off]   = _SEP_COLOR[0]
                    out[off+1] = _SEP_COLOR[1]
                    out[off+2] = _SEP_COLOR[2]
                    out[off+3] = _SEP_COLOR[3]
            x_off += sep

    return total_w, total_h, out


# ---------------------------------------------------------------------------
# PNG writer (pure stdlib)
# ---------------------------------------------------------------------------

def write_png(path, width, height, rgba):
    """Write an RGBA PNG (colour type 6, 8-bit per channel)."""
    def chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)                               # filter byte = None
        raw.extend(rgba[y * stride:(y + 1) * stride])

    # colour type 6 = RGBA (truecolour + alpha)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = b"".join([
        b"\x89PNG\r\n\x1a\n",
        chunk(b"IHDR", ihdr),
        chunk(b"IDAT", zlib.compress(bytes(raw), level=6)),
        chunk(b"IEND", b""),
    ])
    with open(path, "wb") as f:
        f.write(png)


# ---------------------------------------------------------------------------
# PNG -> XP via C++ png2xp binary
# ---------------------------------------------------------------------------

def find_png2xp():
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "..", "workspace", "png2xp", "png2xp"),
        os.path.join(here, "..", "workspace", "png2xp", "png2xp.exe"),
        "png2xp",
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def png_to_xp(png_path, plt_path, xp_path):
    binary = find_png2xp()
    if not binary:
        raise RuntimeError(
            "png2xp binary not found.  Build it with:\n"
            "  cd workspace/png2xp && bash build.sh"
        )
    result = subprocess.run([binary, plt_path, png_path, xp_path],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"png2xp failed:\n{result.stderr}")
    print(result.stdout, end="")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("input", help=".xp file  OR  .png file (round-trip mode)")
    p.add_argument("-o", "--output",
                   help="Output .png path (default: derived from input)")
    p.add_argument("--scale", type=int, default=10,
                   help="Pixels per XP cell (default: 10)")
    p.add_argument("--layer", type=int, default=-1,
                   help="Layer index to render; -1 = last (visual) layer (default)")
    p.add_argument("--grid", action="store_true",
                   help="Draw 1-pixel grid lines between cells")
    p.add_argument("--plt", default=None,
                   help="Palette .plt file (required for PNG->XP round-trip mode)")
    p.add_argument("--all-layers", action="store_true",
                   help="Render all layers as separate output PNGs")
    p.add_argument("--triptych", action="store_true",
                   help="Render all layers side by side in one PNG (L0 | L1 | L2)")
    p.add_argument(
        "--font", nargs="?", const="auto", default=None, metavar="PATH",
        help=(
            "Enable BDF font rendering.  "
            "Omit PATH for auto-select from assets/fonts/ (picks largest size <= --scale).  "
            "Provide PATH to use a specific .bdf file.  "
            "Without this flag: geometric block renderer (faster, no font dependency)."
        ),
    )
    return p.parse_args()


def main():
    args = parse_args()

    if args.scale < 1:
        print("Error: --scale must be >= 1", file=sys.stderr)
        sys.exit(1)

    # ---- Resolve font ------------------------------------------------------
    font = None
    if args.font is not None:
        if args.font == "auto":
            bdf_path, font_size = auto_select_bdf(args.scale)
            if not os.path.isfile(bdf_path):
                print(f"Warning: auto-selected BDF not found: {bdf_path}",
                      file=sys.stderr)
                print("  Falling back to geometric renderer.", file=sys.stderr)
            else:
                font = BdfFont(bdf_path)
                print(f"Font: {bdf_path}  ({font.cell_w}x{font.cell_h} -> scaled to {args.scale}x{args.scale})")
        else:
            if not os.path.isfile(args.font):
                print(f"Error: BDF file not found: {args.font}", file=sys.stderr)
                sys.exit(1)
            font = BdfFont(args.font)
            print(f"Font: {args.font}  ({font.cell_w}x{font.cell_h} -> scaled to {args.scale}x{args.scale})")

    inp = args.input
    ext = os.path.splitext(inp)[1].lower()

    # ---- PNG -> XP -> PNG round-trip ---------------------------------------
    if ext == ".png":
        if not args.plt:
            print("Error: --plt <palette.plt> required for PNG input", file=sys.stderr)
            sys.exit(1)
        tmp_xp = tempfile.mktemp(suffix=".xp")
        print(f"PNG -> XP: {inp} -> {tmp_xp}")
        png_to_xp(inp, args.plt, tmp_xp)
        xp_path = tmp_xp
        base = os.path.splitext(inp)[0]
        cleanup_xp = True
    # ---- XP -> PNG (primary mode) ------------------------------------------
    elif ext == ".xp":
        xp_path = inp
        base = os.path.splitext(inp)[0]
        cleanup_xp = False
    else:
        print(f"Error: unrecognised extension '{ext}' (expected .xp or .png)",
              file=sys.stderr)
        sys.exit(1)

    # ---- Load XP -----------------------------------------------------------
    version, layers = read_xp(xp_path)
    if cleanup_xp:
        os.unlink(xp_path)

    if not layers:
        print("Error: XP file has no layers", file=sys.stderr)
        sys.exit(1)

    total = len(layers)
    xp_w, xp_h, _ = layers[0]
    keys = sprite_transparency_keys(layers)
    sprite_key = next(iter(keys - {MAGENTA_KEY_RGB, LEGACY_YELLOW_KEY_RGB}), None)
    key_tag = f"L0[0,0].bg=rgb{sprite_key}" if sprite_key else "(no extra L0 key)"
    print(f"Loaded: {total} layer(s), {xp_w}x{xp_h} cells, "
          f"output {xp_w * args.scale}x{xp_h * args.scale} px per layer")
    print(f"Transparency keys: magenta + legacy_yellow + {key_tag}")

    # ---- Render ------------------------------------------------------------
    mode_tag = f"font({font.cell_w}x{font.cell_h})" if font else "geo"

    if args.triptych:
        out_path = args.output or f"{base}_triptych.png"
        img_w, img_h, rgba = render_triptych(layers, args.scale, args.grid, font, keys)
        write_png(out_path, img_w, img_h, rgba)
        labels = " | ".join(f"L{i}({layers[i][0]}x{layers[i][1]})" for i in range(total))
        print(f"  [{mode_tag}] triptych [{labels}] -> {img_w}x{img_h} px  ->  {out_path}")
        print("Wrote 1 PNG.")
        return

    if args.all_layers:
        indices = list(range(total))
    else:
        idx = args.layer if args.layer >= 0 else total - 1
        if idx >= total:
            print(f"Error: layer {idx} out of range ({total} layers in file)",
                  file=sys.stderr)
            sys.exit(1)
        indices = [idx]

    for idx in indices:
        w, h, cells = layers[idx]
        img_w, img_h, rgba = render_layer(w, h, cells, args.scale, args.grid, font, keys)

        if args.output and not args.all_layers:
            out_path = args.output
        elif args.all_layers:
            out_base = os.path.splitext(args.output)[0] if args.output else base
            out_path = f"{out_base}_L{idx}.png"
        else:
            out_path = args.output or f"{base}_sheet.png"

        write_png(out_path, img_w, img_h, rgba)
        print(f"  [{mode_tag}] Layer {idx}: {w}x{h} cells -> {img_w}x{img_h} px  ->  {out_path}")

    print("Wrote", len(indices), "PNG(s).")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
