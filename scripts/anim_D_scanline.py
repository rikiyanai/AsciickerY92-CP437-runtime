"""D: Scanline — CRT beam sweeps banner top-to-bottom, one terminal row at a time.

The leading-edge row blends toward white (phosphor glow). Rows above show
at normal brightness. Rows below are hidden (transparent). At 80 cols the
PNG banner is 12 rows → 12 × 40 ms = 480 ms total.

Loops until 'q' or Ctrl-C. Any other key restarts.
Resize: restarts sweep at new size (banner height changes with width).

Run: python3 testing/anim_D_scanline.py
"""
from __future__ import annotations
import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from _common import (
    AltScreen, banner_grid_dims, brighten_toward_white,
    is_interactive, key_ready, load_banner_pixels,
    render_banner, sigwinch_flag, sigwinch_restore,
    target_cols, tty_enter_raw, tty_restore,
)

ROW_DELAY_S = 0.040


def _scanline_fn(cur: int):
    def fn(r, g, b, a, gx, gy, gw, gh, _c=cur):
        row = gy // 2
        if row > _c:
            return 0, 0, 0, 0
        if row == _c:
            return brighten_toward_white(r, g, b, a, t=0.40)
        return r, g, b, a
    return fn


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
            cols = target_cols()
            _, total_rows = banner_grid_dims(src_w, src_h, cols)

            while True:                          # replay loop
                cur         = 0
                needs_clear = True              # must clear at loop start so prior
                                                # full-frame doesn't bleed into early rows

                while cur < total_rows:
                    if resize[0]:
                        resize[0] = False
                        cols = target_cols()
                        _, total_rows = banner_grid_dims(src_w, src_h, cols)
                        cur = 0
                        needs_clear = True

                    if key_ready(fd):
                        ch = os.read(fd, 1)
                        if ch in (b"q", b"Q", b"\x03"):
                            return
                        break

                    prefix = "\033[H\033[2J" if needs_clear else "\033[H"
                    needs_clear = False
                    banner, _ = render_banner(pixels, src_w, src_h, cols, _scanline_fn(cur))
                    sys.stdout.write(prefix + banner)
                    sys.stdout.flush()

                    time.sleep(ROW_DELAY_S)
                    cur += 1
                else:
                    # Sweep complete: render final frame at full brightness
                    banner, _ = render_banner(pixels, src_w, src_h, cols)
                    sys.stdout.write("\033[H" + banner)
                    sys.stdout.flush()
                    time.sleep(0.20)             # brief hold before looping
        finally:
            tty_restore(fd, old_tty)
            sigwinch_restore(old_sig)


if __name__ == "__main__":
    main()
