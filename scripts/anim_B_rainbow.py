"""B: Rainbow sweep — scrolling HSV hue wave across all banner pixels.

hue = (col/width + phase) % 1.0 per cell. Phase increments each frame,
making the rainbow appear to scroll left → right. Original pixel brightness
preserved; saturation boosted to min 0.65 so near-grey areas catch colour.

Loops until 'q' or Ctrl-C. Any other key restarts.
Resize: restarts sweep at new terminal size.

Run: python3 testing/anim_B_rainbow.py
"""
from __future__ import annotations
import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from _common import (
    AltScreen, hue_shift, is_interactive, key_ready, load_banner_pixels,
    render_banner, sigwinch_flag, sigwinch_restore,
    target_cols, tty_enter_raw, tty_restore,
)

FRAMES     = 22
FRAME_S    = 0.040
PHASE_STEP = 0.045


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
            while True:                          # replay loop
                phase      = 0.0
                frame      = 0
                needs_clear = True

                while frame <= FRAMES:
                    if resize[0]:
                        resize[0] = False
                        cols  = target_cols()
                        frame = 0
                        phase = 0.0
                        needs_clear = True

                    if key_ready(fd):
                        ch = os.read(fd, 1)
                        if ch in (b"q", b"Q", b"\x03"):
                            return
                        break

                    _p = phase
                    def color_fn(r, g, b, a, gx, gy, gw, gh, _p=_p):
                        return hue_shift(r, g, b, a, (gx / gw + _p) % 1.0)

                    prefix = "\033[H\033[2J" if needs_clear else "\033[H"
                    needs_clear = False
                    banner, _ = render_banner(pixels, src_w, src_h, cols, color_fn)
                    sys.stdout.write(prefix + banner)
                    sys.stdout.flush()

                    time.sleep(FRAME_S)
                    phase = (phase + PHASE_STEP) % 1.0
                    frame += 1
        finally:
            tty_restore(fd, old_tty)
            sigwinch_restore(old_sig)


if __name__ == "__main__":
    main()
