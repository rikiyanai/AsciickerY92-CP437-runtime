"""F: ASCII fire — Doom-style heat propagation fills terminal below the banner.

Algorithm (id Software / Fabien Sanglard):
  - Heat grid: w × h integers (0=cold, 255=max)
  - Bottom row seeded at 200-255 each frame
  - Each cell: heat[y][x] = heat[y+1][(x + rand(-1,1)) % w] - rand(0,2)
  - Heat map → ASCII char + 24-bit ANSI colour from a 36-stop Doom palette

Banner stays static at top. Fire grows upward into the remaining rows.
Resize-safe: recomputes grid dimensions on SIGWINCH.

Loops until 'q' or Ctrl-C.

Run: python3 testing/anim_F_fire.py
"""
from __future__ import annotations
import os, random, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from _common import (
    AltScreen, banner_grid_dims, is_interactive, key_ready,
    load_banner_pixels, render_banner, sigwinch_flag,
    sigwinch_restore, target_cols, tty_enter_raw, tty_restore,
)

import shutil

FRAME_S = 0.033   # ~30 fps

# Doom fire colour palette (36 stops, black → white-yellow)
_PAL: list[tuple[int, int, int]] = [
    (0,0,0),(7,7,7),(31,7,7),(47,15,7),(71,15,7),(87,23,7),
    (103,31,7),(119,31,7),(143,39,7),(159,47,7),(175,63,7),
    (191,71,7),(199,71,7),(223,79,7),(223,87,7),(215,95,7),
    (215,103,15),(207,111,15),(207,119,15),(207,127,15),
    (199,135,23),(199,143,23),(191,151,31),(191,159,31),
    (191,167,39),(191,167,39),(191,175,47),(183,175,47),
    (183,183,47),(183,183,55),(207,207,111),(223,223,159),
    (239,239,199),(255,255,255),(255,255,255),(255,255,255),
]
_CHARS = " `·.,:;~!|/\\({[#@$"


def _fire_cell(h: int) -> str:
    if h < 4:
        return " "
    r, g, b = _PAL[min(len(_PAL) - 1, h * len(_PAL) // 256)]
    ch = _CHARS[min(len(_CHARS) - 1, h * len(_CHARS) // 256)]
    return f"\033[38;2;{r};{g};{b}m{ch}\033[0m"


def _init_grid(w: int, h: int) -> list[int]:
    grid = [0] * (w * h)
    for x in range(w):
        grid[(h - 1) * w + x] = 255
    return grid


def _step(grid: list[int], w: int, h: int) -> None:
    # Re-seed bottom row
    for x in range(w):
        grid[(h - 1) * w + x] = random.randint(200, 255)
    # Propagate upward
    for y in range(h - 1):
        for x in range(w):
            src_x = (x + random.randint(-1, 1)) % w
            decay = random.randint(0, 2)
            grid[y * w + x] = max(0, grid[(y + 1) * w + src_x] - decay)


def _render_fire(grid: list[int], w: int, h: int, start_row: int) -> str:
    parts: list[str] = []
    for y in range(h):
        row = "".join(_fire_cell(grid[y * w + x]) for x in range(w))
        parts.append(f"\033[{start_row + y};1H{row}")
    return "".join(parts)


def main() -> None:
    if not is_interactive():
        return
    pixels, src_w, src_h = load_banner_pixels()
    if not pixels:
        return

    with AltScreen():
        fd, old_tty = tty_enter_raw()
        resize, old_sig = sigwinch_flag()
        try:
            def _dims():
                cols, rows = shutil.get_terminal_size(fallback=(80, 24))
                b_cols = target_cols()
                _, b_rows = banner_grid_dims(src_w, src_h, b_cols)
                fire_start = b_rows + 2          # 1-indexed terminal row
                fire_h = max(1, rows - fire_start)
                fire_w = cols
                return b_cols, b_rows, fire_start, fire_w, fire_h

            b_cols, b_rows, fire_start, fire_w, fire_h = _dims()
            grid = _init_grid(fire_w, fire_h)
            banner, _ = render_banner(pixels, src_w, src_h, b_cols)
            needs_clear = True

            while True:
                if resize[0]:
                    resize[0] = False
                    b_cols, b_rows, fire_start, fire_w, fire_h = _dims()
                    grid = _init_grid(fire_w, fire_h)
                    banner, _ = render_banner(pixels, src_w, src_h, b_cols)
                    needs_clear = True

                if key_ready(fd):
                    ch = os.read(fd, 1)
                    if ch in (b"q", b"Q", b"\x03"):
                        return
                    # any other key: continue (fire keeps running)

                _step(grid, fire_w, fire_h)

                prefix = "\033[H\033[2J" if needs_clear else "\033[H"
                needs_clear = False
                fire_str = _render_fire(grid, fire_w, fire_h, fire_start)
                sys.stdout.write(prefix + banner + fire_str)
                sys.stdout.flush()

                time.sleep(FRAME_S)

        finally:
            tty_restore(fd, old_tty)
            sigwinch_restore(old_sig)


if __name__ == "__main__":
    main()
