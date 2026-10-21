"""Shared infrastructure for launcher startup animation test scripts.

Run any script directly: python3 testing/anim_X_name.py
Run all in sequence:     python3 testing/run_all.py

Key design rules for resize-safe animations:
  - render_banner joins lines with \\r\\n (not \\n) — in raw TTY mode, bare LF
    moves cursor DOWN but NOT to column 0, causing a staircase on wide terminals.
  - Callers use \\033[H\\033[2J only on the first draw of each loop/resize;
    subsequent frames use \\033[H alone (home + overwrite, no blank-flash between frames).
  - SIGWINCH handler sets a flag; the animation loop checks it each frame and recomputes
    terminal size before the next draw.
"""
from __future__ import annotations

import colorsys
import os
import select
import shutil
import signal
import sys
import time
from pathlib import Path

# ── Repo path bootstrap ──────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parent.parent.resolve()  # scripts/ -> repo root
sys.path.insert(0, str(REPO_ROOT))

from scripts.launcher_lib.banner import (
    BANNER_MAX_COLS,
    BANNER_PNG,
    BANNER_XP,
    MAGENTA_BG,
    XPFile,
    _banner_target_cols,
    _cell_pixel_color,
    _load_png_rgba,
    render_xp,
)

UPPER = "\u2580"   # ▀
LOWER = "\u2584"   # ▄
RESET = "\033[0m"


def _fg(r: int, g: int, b: int) -> str:
    return f"\033[38;2;{r};{g};{b}m"


def _bg(r: int, g: int, b: int) -> str:
    return f"\033[48;2;{r};{g};{b}m"


# ── Banner pixel loading ─────────────────────────────────────────────────────

def load_banner_pixels() -> tuple[list | None, int, int]:
    """Return (pixels, src_w, src_h). pixels = list of (R,G,B,A) tuples.
    Tries PNG first, falls back to XP layer 2 (or 0 if <3 layers).
    Returns (None, 0, 0) if no asset found."""
    if BANNER_PNG.exists():
        w, h, pixels = _load_png_rgba(BANNER_PNG)
        return pixels, w, h
    if BANNER_XP.exists():
        xp = XPFile(BANNER_XP)
        layer = xp.layers[2] if len(xp.layers) >= 3 else xp.layers[0]
        w, h = layer.width, layer.height
        pixels = []
        for y in range(h):
            for x in range(w):
                glyph, fg, bg = layer.data[y][x]
                c = _cell_pixel_color(glyph, fg, bg)
                a = 0 if c == MAGENTA_BG else 255
                pixels.append((*c, a))
        return pixels, w, h
    return None, 0, 0


# ── Grid dimension helper ────────────────────────────────────────────────────

def banner_grid_dims(src_w: int, src_h: int, cols: int) -> tuple[int, int]:
    """Return (tgt_w, grid_rows) — grid_rows is the number of terminal rows."""
    tgt_h = max(2, int(src_h * cols / src_w))
    if tgt_h % 2:
        tgt_h += 1
    return cols, tgt_h // 2


# ── Half-block renderer with optional per-cell color transform ───────────────

def render_banner(
    pixels: list,
    src_w: int,
    src_h: int,
    cols: int,
    color_fn=None,
) -> tuple[str, int]:
    """Render banner as half-block ANSI string.

    color_fn(r, g, b, a, gx, gy, tgt_w, tgt_h) -> (r, g, b, a)
      gx    = terminal column  (0 .. cols-1)
      gy    = pixel row        (0 .. tgt_h-1)  two pixel rows per terminal row
      tgt_w = cols
      tgt_h = total pixel rows (even)

    Lines are joined with \\r\\n so the cursor returns to column 0 in raw TTY mode.
    Returns (ansi_string, grid_rows).
    """
    tgt_w = cols
    tgt_h = max(2, int(src_h * tgt_w / src_w))
    if tgt_h % 2:
        tgt_h += 1
    grid_rows = tgt_h // 2

    lines: list[str] = []
    for grid_row in range(grid_rows):
        row: list[str] = []
        for gx in range(tgt_w):
            sx = int(gx * src_w / tgt_w)
            sy0 = int((grid_row * 2) * src_h / tgt_h)
            sy1 = int((grid_row * 2 + 1) * src_h / tgt_h)

            top = pixels[sy0 * src_w + sx]
            bot = pixels[sy1 * src_w + sx] if sy1 < src_h else (0, 0, 0, 0)

            if color_fn is not None:
                top = color_fn(*top, gx, grid_row * 2,     tgt_w, tgt_h)
                bot = color_fn(*bot, gx, grid_row * 2 + 1, tgt_w, tgt_h)

            r0, g0, b0, a0 = top
            r1, g1, b1, a1 = bot
            tv, bv = a0 >= 16, a1 >= 16

            if not tv and not bv:
                row.append(" ")
            elif tv and not bv:
                row.append(f"\033[49m{_fg(r0,g0,b0)}{UPPER}{RESET}")
            elif not tv and bv:
                row.append(f"\033[49m{_fg(r1,g1,b1)}{LOWER}{RESET}")
            else:
                row.append(f"{_fg(r0,g0,b0)}{_bg(r1,g1,b1)}{UPPER}{RESET}")
        lines.append("".join(row))

    # \r\n: CR returns cursor to col 0 before LF advances the row.
    # Without \r, raw TTY mode leaves cursor at whatever column the last
    # character landed on — subsequent lines staircase to the right.
    return "\r\n".join(lines), grid_rows


# ── Color math helpers ───────────────────────────────────────────────────────

def hue_shift(r: int, g: int, b: int, a: int, shift: float) -> tuple:
    """Shift hue by `shift` (0.0–1.0). Boosts saturation to min 0.65 so
    near-grey pixels still catch the rainbow colour."""
    if a < 16:
        return r, g, b, a
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    h = (h + shift) % 1.0
    s = max(s, 0.65)
    nr, ng, nb = colorsys.hsv_to_rgb(h, s, v)
    return int(nr * 255), int(ng * 255), int(nb * 255), a


def scale_v(r: int, g: int, b: int, a: int, factor: float) -> tuple:
    """Scale brightness (V channel) by factor. Clamps [0, 1]."""
    if a < 16:
        return r, g, b, a
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    v = min(1.0, max(0.0, v * factor))
    nr, ng, nb = colorsys.hsv_to_rgb(h, s, v)
    return int(nr * 255), int(ng * 255), int(nb * 255), a


def brighten_toward_white(r: int, g: int, b: int, a: int, t: float = 0.45) -> tuple:
    """Blend toward white by fraction t. Works on already-bright pixels."""
    if a < 16:
        return r, g, b, a
    return (
        int(r + (255 - r) * t),
        int(g + (255 - g) * t),
        int(b + (255 - b) * t),
        a,
    )


# ── Terminal utilities ───────────────────────────────────────────────────────

def target_cols() -> int:
    """Capped terminal column count (respects BANNER_MAX_COLS)."""
    cols, _ = shutil.get_terminal_size(fallback=(80, 24))
    return _banner_target_cols(cols)


def key_ready(fd: int) -> bool:
    """Non-blocking poll: is a keypress available?"""
    return bool(select.select([fd], [], [], 0)[0])


def tty_enter_raw():
    """Enter raw mode. Returns (fd, old_settings)."""
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setraw(fd)
    return fd, old


def tty_restore(fd: int, old) -> None:
    import termios
    termios.tcsetattr(fd, termios.TCSADRAIN, old)


def sigwinch_flag() -> tuple[list, object]:
    """Returns ([False], old_handler). Flag becomes True on SIGWINCH."""
    flag = [False]
    def _h(*_): flag[0] = True
    old = signal.signal(signal.SIGWINCH, _h)
    return flag, old


def sigwinch_restore(old) -> None:
    signal.signal(signal.SIGWINCH, old)


class AltScreen:
    def __enter__(self):
        sys.stdout.write("\033[?1049h\033[?25l")
        sys.stdout.flush()
        return self

    def __exit__(self, *_):
        sys.stdout.write("\033[?1049l\033[?25h")
        sys.stdout.flush()


def is_interactive() -> bool:
    return (
        sys.stdin.isatty()
        and sys.stdout.isatty()
        and not os.environ.get("CI")
        and not os.environ.get("NO_COLOR")
    )


# ── XP sprite helpers (for overlay animations) ───────────────────────────────

def xp_to_pixels(path: Path) -> tuple[list, int, int]:
    """Load XP file's last layer as (pixels, src_w, src_h) RGBA list.
    Returns (None, 0, 0) if file not found."""
    if not path.exists():
        return None, 0, 0
    xp = XPFile(path)
    layer = xp.layers[-1]
    src_w, src_h = layer.width, layer.height
    pixels = []
    for y in range(src_h):
        for x in range(src_w):
            glyph, fg, bg = layer.data[y][x]
            c = _cell_pixel_color(glyph, fg, bg)
            a = 0 if c == MAGENTA_BG else 255
            pixels.append((*c, a))
    return pixels, src_w, src_h


def render_xp_lines(path: Path, target_cols: int) -> list[str]:
    """Render XP sprite at target_cols width.
    Returns list of ANSI strings (one per terminal row).
    Uses render_xp() which correctly handles XP terminal-cell encoding."""
    result = render_xp(path, target_cols)
    if not result:
        return []
    return result.split("\n")


def place_sprite(lines: list[str], term_row: int, term_col: int) -> str:
    """Return ANSI string that positions sprite lines at (term_row, term_col).
    Both coordinates are 1-indexed terminal positions."""
    parts = []
    for i, line in enumerate(lines):
        parts.append(f"\033[{term_row + i};{term_col}H{line}")
    return "".join(parts)
