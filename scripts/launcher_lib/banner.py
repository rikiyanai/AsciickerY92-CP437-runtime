"""Launcher banner — renders asciicker_logo.png with resize-aware interactive mode.

Derived from rextuul.py (_WatchRenderer pattern).
Fallback: renders the legacy asciicker.xp if PNG is missing.
"""

from __future__ import annotations

import gzip
import os
import shutil
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent.resolve()
BANNER_PNG = REPO_ROOT / "assets" / "sprites" / "asciicker_logo.png"
BANNER_XP = REPO_ROOT / "assets" / "sprites" / "asciicker.xp"
MAGENTA_BG = (255, 0, 255)
RESET = "\033[0m"
UPPER = "\u2580"
LOWER = "\u2584"
BANNER_MAX_COLS = 80


def _fg(r: int, g: int, b: int) -> str:
    return f"\033[38;2;{r};{g};{b}m"


def _bg(r: int, g: int, b: int) -> str:
    return f"\033[48;2;{r};{g};{b}m"


# ── Pure-Python PNG loader (zero deps) ──────────────────────────────────────
# Adapted from rextuul.py

def _load_png_rgba(path: Path) -> tuple[int, int, list[tuple[int, int, int, int]]]:
    """Decode an 8-bit RGB or RGBA PNG. Returns (width, height, pixels)."""
    with open(path, "rb") as f:
        sig = f.read(8)
        if sig != b"\x89PNG\r\n\x1a\n":
            raise ValueError("Not a PNG file")
        width = height = 0
        color_type = 6
        idat = bytearray()
        while True:
            chunk_head = f.read(8)
            if not chunk_head:
                break
            length, chunk_type = struct.unpack(">I4s", chunk_head)
            data = f.read(length)
            f.read(4)  # CRC
            if chunk_type == b"IHDR":
                width, height, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", data)
                if bit_depth != 8 or interlace != 0 or color_type not in (2, 6):
                    raise ValueError(f"Unsupported PNG: depth={bit_depth} interlace={interlace} color_type={color_type}")
            elif chunk_type == b"IDAT":
                idat.extend(data)
            elif chunk_type == b"IEND":
                break

    decompressed = zlib.decompress(idat)
    bpp = 4 if color_type == 6 else 3
    stride = width * bpp
    pixels: list[tuple[int, int, int, int]] = []

    def paeth(a: int, b: int, c: int) -> int:
        p = a + b - c
        pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
        return a if pa <= pb and pa <= pc else (b if pb <= pc else c)

    prev_row = bytearray(stride)
    for y in range(height):
        row_start = y * (stride + 1)
        filter_type = decompressed[row_start]
        row_data = decompressed[row_start + 1: row_start + 1 + stride]
        recon = bytearray(stride)
        for x in range(stride):
            a = recon[x - bpp] if x >= bpp else 0
            b = prev_row[x]
            c = prev_row[x - bpp] if x >= bpp else 0
            if filter_type == 0:
                val = row_data[x]
            elif filter_type == 1:
                val = (row_data[x] + a) & 0xFF
            elif filter_type == 2:
                val = (row_data[x] + b) & 0xFF
            elif filter_type == 3:
                val = (row_data[x] + (a + b) // 2) & 0xFF
            elif filter_type == 4:
                val = (row_data[x] + paeth(a, b, c)) & 0xFF
            else:
                raise ValueError(f"Unknown PNG filter {filter_type}")
            recon[x] = val
        for i in range(0, stride, bpp):
            if color_type == 6:
                pixels.append((recon[i], recon[i + 1], recon[i + 2], recon[i + 3]))
            else:
                pixels.append((recon[i], recon[i + 1], recon[i + 2], 255))
        prev_row = recon
    return width, height, pixels


# ── Half-block renderer for PNG pixels ──────────────────────────────────────
# Adapted from rextuul.py::_render_png_halfblock_raw

def _render_png_halfblock(pixels: list[tuple[int, int, int, int]], sw: int, sh: int, cols: int) -> str:
    tgt_w = cols
    tgt_h = max(2, int(sh * tgt_w / sw))
    if tgt_h % 2:
        tgt_h += 1
    lines: list[str] = []
    for y in range(0, tgt_h, 2):
        row: list[str] = []
        for x in range(tgt_w):
            sx = int(x * sw / tgt_w)
            sy0 = int(y * sh / tgt_h)
            sy1 = int((y + 1) * sh / tgt_h)
            r0, g0, b0, a0 = pixels[sy0 * sw + sx]
            r1, g1, b1, a1 = pixels[sy1 * sw + sx] if sy1 < sh else (0, 0, 0, 0)
            tv = a0 >= 16
            bv = a1 >= 16
            if not tv and not bv:
                row.append(" ")
            elif tv and not bv:
                row.append(f"\033[49m{_fg(r0, g0, b0)}{UPPER}{RESET}")
            elif not tv and bv:
                row.append(f"\033[49m{_fg(r1, g1, b1)}{LOWER}{RESET}")
            else:
                row.append(f"{_fg(r0, g0, b0)}{_bg(r1, g1, b1)}{UPPER}{RESET}")
        lines.append("".join(row))
    return "\n".join(lines)


def _render_png_banner(path: Path, target_cols: int) -> str:
    try:
        w, h, pixels = _load_png_rgba(path)
        return _render_png_halfblock(pixels, w, h, target_cols)
    except Exception:
        return ""


# ── Legacy XP renderer (fallback if PNG missing) ────────────────────────────

def _cell_pixel_color(glyph: int, fg: tuple[int, int, int], bg: tuple[int, int, int]) -> tuple[int, int, int]:
    if bg == MAGENTA_BG:
        return MAGENTA_BG if glyph in (0, 32) else fg
    return fg if glyph == 219 else bg


@dataclass
class XPLayer:
    width: int
    height: int
    data: list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]]


class XPFile:
    def __init__(self, path: Path) -> None:
        self.layers: list[XPLayer] = []
        self._load(path)

    def _load(self, path: Path) -> None:
        with gzip.open(path, "rb") as handle:
            content = handle.read()
        offset = 0
        offset += 4  # version
        layer_count = struct.unpack("<I", content[offset:offset + 4])[0]
        offset += 4
        for _ in range(layer_count):
            width = struct.unpack("<i", content[offset:offset + 4])[0]
            offset += 4
            height = struct.unpack("<i", content[offset:offset + 4])[0]
            offset += 4
            rows: list[list] = [[None for _ in range(width)] for _ in range(height)]
            for x in range(width):
                for y in range(height):
                    glyph = struct.unpack("<I", content[offset:offset + 4])[0]
                    offset += 4
                    fg = tuple(content[offset:offset + 3])
                    offset += 3
                    bg = tuple(content[offset:offset + 3])
                    offset += 3
                    rows[y][x] = (glyph, fg, bg)
            self.layers.append(XPLayer(width=width, height=height, data=rows))


def render_xp(path: Path, target_cols: int | None = None) -> str:
    xp = XPFile(path)
    if not xp.layers:
        return ""
    layer = xp.layers[2] if len(xp.layers) >= 3 else xp.layers[0]
    source_w, source_h = layer.width, layer.height
    target_w = target_cols or source_w
    lines: list[str] = []
    for y in range(0, source_h, 2):
        row: list[str] = []
        for x in range(target_w):
            src_x = int(x * source_w / target_w)
            glyph0, fg0, bg0 = layer.data[y][src_x]
            color0 = _cell_pixel_color(glyph0, fg0, bg0)
            if y + 1 < source_h:
                glyph1, fg1, bg1 = layer.data[y + 1][src_x]
                color1 = _cell_pixel_color(glyph1, fg1, bg1)
            else:
                color1 = MAGENTA_BG
            top_transparent = color0 == MAGENTA_BG
            bottom_transparent = color1 == MAGENTA_BG
            if top_transparent and bottom_transparent:
                row.append(" ")
            elif top_transparent:
                row.append(f"\033[49m{_fg(*color1)}{LOWER}{RESET}")
            elif bottom_transparent:
                row.append(f"\033[49m{_fg(*color0)}{UPPER}{RESET}")
            else:
                row.append(f"{_fg(*color0)}{_bg(*color1)}{UPPER}{RESET}")
        lines.append("".join(row))
    return "\n".join(lines)


# ── Banner rendering helpers ─────────────────────────────────────────────────

def _build_banner_str(target_cols: int) -> str:
    if BANNER_PNG.exists():
        return _render_png_banner(BANNER_PNG, target_cols)
    if BANNER_XP.exists():
        return render_xp(BANNER_XP, target_cols)
    return ""


def _banner_target_cols(terminal_cols: int) -> int:
    return max(8, min(terminal_cols - 2, BANNER_MAX_COLS))


# ── Interactive banner (resize-aware, alternate screen) ─────────────────────
# Pattern derived from rextuul.py::_WatchRenderer

def show_banner_interactive() -> None:
    """Show the banner in alternate screen mode. Redraws on SIGWINCH. Exits on any key."""
    if os.name == "nt":
        return  # termios/tty not available on Windows
    if os.environ.get("CI") or not sys.stdin.isatty() or not sys.stdout.isatty():
        return
    if not BANNER_PNG.exists() and not BANNER_XP.exists():
        return

    import select
    import signal
    import termios
    import tty

    _redraw_pending = [True]

    def _on_resize(*_: object) -> None:
        _redraw_pending[0] = True

    old_sigwinch = signal.signal(signal.SIGWINCH, _on_resize)

    sys.stdout.write("\033[?1049h\033[?25l")  # alternate screen + hide cursor
    sys.stdout.flush()

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    try:
        tty.setraw(fd)
        while True:
            if _redraw_pending[0]:
                _redraw_pending[0] = False
                cols, _ = shutil.get_terminal_size(fallback=(80, 24))
                target_w = _banner_target_cols(cols)
                banner_str = _build_banner_str(target_w)
                hint = "\033[2m  press any key\033[0m"
                sys.stdout.write("\033[H\033[2J" + banner_str + "\n" + hint)
                sys.stdout.flush()
            if select.select([fd], [], [], 0.05)[0]:
                ch = os.read(fd, 1)
                if ch:  # any key (including ctrl-c b'\x03') exits
                    break
    except Exception:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        sys.stdout.write("\033[?1049l\033[?25h")  # restore screen + show cursor
        sys.stdout.flush()
        signal.signal(signal.SIGWINCH, old_sigwinch)


# ── Non-interactive banner (CI / pipe fallback) ──────────────────────────────

def print_banner() -> None:
    """Render the banner once to stdout. Used in non-interactive / CI paths."""
    if not BANNER_PNG.exists() and not BANNER_XP.exists():
        return
    if os.environ.get("CI") or not sys.stdout.isatty():
        return
    try:
        cols, _ = shutil.get_terminal_size(fallback=(80, 24))
        target_w = _banner_target_cols(cols)
        result = _build_banner_str(target_w)
        if result:
            sys.stdout.write(result + "\n")
            sys.stdout.flush()
    except (OSError, EOFError, gzip.BadGzipFile, struct.error, ValueError):
        return
