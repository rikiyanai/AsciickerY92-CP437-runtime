"""E: Throbber — banner appears instantly, braille spinner cycles below it.

The spinner would mask fast_probes() latency in the real launcher. Here it
runs one full 10-frame braille cycle (~800 ms) then loops.

Loops until 'q' or Ctrl-C. Any other key restarts.
Resize: redraws banner at new size; spinner frame continues uninterrupted.

Run: python3 testing/anim_E_throbber.py
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

BRAILLE = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
FRAME_S = 0.080
CYCLES  = 1

CYAN  = "\033[36m"
DIM   = "\033[2m"
RESET = "\033[0m"

# \r\n here too — the banner already ends without a trailing newline,
# so these literal \r\n sequences position the spinner correctly in raw mode.
_SPINNER_SUFFIX = "\r\n\r\n  {cyan}{ch}{reset}  {dim}starting up{reset}"


def main() -> None:
    if not is_interactive():
        return
    pixels, src_w, src_h = load_banner_pixels()
    if not pixels:
        return

    total_frames = len(BRAILLE) * CYCLES

    with AltScreen():
        fd, old_tty = tty_enter_raw()
        resize, old_sig = sigwinch_flag()
        try:
            cols   = target_cols()
            banner, _ = render_banner(pixels, src_w, src_h, cols)

            while True:                          # replay loop
                needs_clear = True

                for frame in range(total_frames):
                    if resize[0]:
                        resize[0] = False
                        cols   = target_cols()
                        banner, _ = render_banner(pixels, src_w, src_h, cols)
                        needs_clear = True

                    if key_ready(fd):
                        ch = os.read(fd, 1)
                        if ch in (b"q", b"Q", b"\x03"):
                            return
                        break

                    spin = BRAILLE[frame % len(BRAILLE)]
                    suffix = _SPINNER_SUFFIX.format(
                        cyan=CYAN, ch=spin, reset=RESET, dim=DIM
                    )
                    prefix = "\033[H\033[2J" if needs_clear else "\033[H"
                    needs_clear = False
                    sys.stdout.write(prefix + banner + suffix)
                    sys.stdout.flush()
                    time.sleep(FRAME_S)
        finally:
            tty_restore(fd, old_tty)
            sigwinch_restore(old_sig)


if __name__ == "__main__":
    main()
