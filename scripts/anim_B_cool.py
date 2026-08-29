"""B-cool: Scrolling hue sweep restricted to the green-cyan-blue band.

Hue maps to [0.45 → 0.70] — mint green through cyan to deep blue.
Saturation forced to minimum 0.70 for vivid cool tones.

Loops until 'q' or Ctrl-C. Any other key restarts.
Resize: restarts sweep at new size.

Run: python3 testing/anim_B_cool.py
"""
from __future__ import annotations
import colorsys, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from _common import (
    AltScreen, is_interactive, key_ready, load_banner_pixels,
    render_banner, sigwinch_flag, sigwinch_restore,
    target_cols, tty_enter_raw, tty_restore,
)

FRAMES     = 22
FRAME_S    = 0.040
PHASE_STEP = 0.045

# Hue band: 0.45 (mint/teal) → 0.70 (deep blue), span = 0.25
_BAND_START = 0.45
_BAND_SPAN  = 0.25


def _cool(r, g, b, a, gx, gy, gw, gh, _p=0.0):
    if a < 16:
        return 0, 0, 0, 0
    _, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    t = (gx / gw + _p) % 1.0
    h = _BAND_START + t * _BAND_SPAN
    s = max(s, 0.70)
    v = max(v, 0.30)
    nr, ng, nb = colorsys.hsv_to_rgb(h, s, v)
    return int(nr * 255), int(ng * 255), int(nb * 255), a


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
            while True:
                phase       = 0.0
                frame       = 0
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
                        return _cool(r, g, b, a, gx, gy, gw, gh, _p)

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
