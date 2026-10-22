"""A: Auto-splash — static banner, auto-advances after 0.8 s.

Loops until 'q' or Ctrl-C. Any other key restarts the timer.
Resize: redraws at new size, continues the current timer.

Run: python3 testing/anim_A_autosplash.py
"""
from __future__ import annotations
import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from _common import (
    AltScreen, is_interactive, key_ready, load_banner_pixels,
    render_banner, sigwinch_flag, sigwinch_restore,
    target_cols, tty_enter_raw, tty_restore,
)

DURATION = 0.8


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
                deadline = time.monotonic() + DURATION
                needs_clear = True

                while time.monotonic() < deadline:
                    if resize[0]:
                        resize[0] = False
                        cols = target_cols()
                        needs_clear = True

                    prefix = "\033[H\033[2J" if needs_clear else "\033[H"
                    needs_clear = False
                    banner, _ = render_banner(pixels, src_w, src_h, cols)
                    sys.stdout.write(prefix + banner)
                    sys.stdout.flush()

                    if key_ready(fd):
                        ch = os.read(fd, 1)
                        if ch in (b"q", b"Q", b"\x03"):
                            return
                        break                    # any other key: restart
                    time.sleep(0.05)
                # deadline reached → loop back to start
        finally:
            tty_restore(fd, old_tty)
            sigwinch_restore(old_sig)


if __name__ == "__main__":
    main()
