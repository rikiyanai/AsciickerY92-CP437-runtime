"""C: CRT pulse — banner powers on with a capacitor-discharge brightness curve.

Piecewise brightness envelope (~1 s):
  frame  0 →  6:  0.08 → 1.0   quick flash on
  frame  6 → 13:  1.0  → 0.22  bloom decay
  frame 13 → 22:  0.22 → 1.0   settle to full
  frame 22 → 25:  1.0  → 1.0   hold

Loops until 'q' or Ctrl-C. Any other key restarts.
Resize: re-renders at new size; envelope phase continues uninterrupted.

Run: python3 testing/anim_C_pulse.py
"""
from __future__ import annotations
import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from _common import (
    AltScreen, is_interactive, key_ready, load_banner_pixels,
    render_banner, scale_v, sigwinch_flag, sigwinch_restore,
    target_cols, tty_enter_raw, tty_restore,
)

FRAME_S = 0.040

_KF = [(0, 0.08), (6, 1.00), (13, 0.22), (22, 1.00), (25, 1.00)]
TOTAL = _KF[-1][0]


def _env(f: int) -> float:
    for i in range(len(_KF) - 1):
        f0, v0 = _KF[i]
        f1, v1 = _KF[i + 1]
        if f0 <= f <= f1:
            t = (f - f0) / (f1 - f0)
            return v0 + (v1 - v0) * t
    return 1.0


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
                frame       = 0
                needs_clear = True

                while frame <= TOTAL:
                    if resize[0]:
                        resize[0] = False
                        cols = target_cols()
                        needs_clear = True
                        # no frame reset: envelope is time-based, not layout-based

                    if key_ready(fd):
                        ch = os.read(fd, 1)
                        if ch in (b"q", b"Q", b"\x03"):
                            return
                        break

                    _f = _env(frame)
                    def color_fn(r, g, b, a, gx, gy, gw, gh, _f=_f):
                        return scale_v(r, g, b, a, _f)

                    prefix = "\033[H\033[2J" if needs_clear else "\033[H"
                    needs_clear = False
                    banner, _ = render_banner(pixels, src_w, src_h, cols, color_fn)
                    sys.stdout.write(prefix + banner)
                    sys.stdout.flush()

                    time.sleep(FRAME_S)
                    frame += 1
        finally:
            tty_restore(fd, old_tty)
            sigwinch_restore(old_sig)


if __name__ == "__main__":
    main()
