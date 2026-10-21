#!/usr/bin/env python3
# =============================================================================
# sprite_walk_compare.py  --  side-by-side WALK asciifier comparison viewer
# =============================================================================
# WHAT THIS DOES
# --------------
# Shows several "sprite boxes" in a row, each one a DIFFERENT asciifier VARIANT
# of the same walk, and plays them all in LOCKSTEP -- same compass direction and
# same walk-cycle frame index across every box -- so you can eyeball, frame for
# frame, how each rendering method (Unifont 5x7, Xu Rw=28, the old 3x5, etc.)
# depicts the exact same pose.
#
# Each variant is just a frames file in the usual "# DIR" / blank-line-separated
# block format that sprite_idle already understands.  Point it at as many as you
# like; boxes are laid left-to-right and sized to each variant's own glyph grid
# (so a 5x7 box and a 28-wide Xu box sit side by side, both showing frame N).
#
# CONTROLS
#   <- / ->      change compass direction (S, SW, W, NW, N, NE, E, SE)
#   space        play / pause the walk cycle
#   , / .        step one frame back / forward (also pauses)
#   + / -        faster / slower
#   q            quit
#
# USE
#   # default set (whatever exists of these three):
#   python3 scripts/sprite_walk_compare.py
#   # custom set -- any number of  label=path.txt :
#   python3 scripts/sprite_walk_compare.py unifont=scripts/sprite_frames_walk_5x7_unifont.txt \
#                                          xu=scripts/sprite_frames_walk_xu.txt
#
# NOTE: needs a REAL terminal (curses/TTY) and a font with wide-Unicode coverage,
# or the exotic glyphs show as tofu.  Run it directly in Terminal/iTerm, not via
# a non-interactive shell.
# =============================================================================
from __future__ import annotations

import argparse
import curses
import locale
import os
import time

import sprite_idle as si          # same scripts/ dir -> reuse its frames loader

locale.setlocale(locale.LC_ALL, "")

# Compass order matches the generators (GIF block order d0..d7).
DIRS = ["S", "SW", "W", "NW", "N", "NE", "E", "SE"]


def load_variant(path):
    """Load one frames file and normalise every frame to a list of row-strings.

    sprite_idle.load_frames_txt returns {DIR: [frame, ...]} where a frame may be
    either a single '\n'-joined string or already a list of rows; we flatten both
    to a plain list[str] so the drawing code is uniform.
    """
    d = si.load_frames_txt(path)
    out = {}
    for k, frames in (d or {}).items():
        norm = []
        for fr in frames:
            rows = fr if isinstance(fr, list) else str(fr).split("\n")
            norm.append([r.rstrip("\n") for r in rows])
        out[k] = norm
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Compare walk asciifier variants side by side, in lockstep.")
    ap.add_argument("--fps", type=float, default=4.0, help="walk speed (default 4)")
    ap.add_argument("variants", nargs="*",
                    help="one or more  label=path.txt  (default: the three walk variants)")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    # Default comparison set: the 52-method 5x7, the Xu Rw=28, and the old 3x5.
    # Only the ones that actually exist on disk are loaded.
    default_specs = [
        f"unifont5x7={os.path.join(here, 'sprite_frames_walk_5x7_unifont.txt')}",
        f"xu_rw28={os.path.join(here, 'sprite_frames_walk_xu.txt')}",
        f"old3x5={os.path.join(here, 'sprite_frames_walk_unifont.txt')}",
    ]
    specs = args.variants or default_specs

    variants = []                       # list of (label, {DIR: [frame_rows,...]})
    for spec in specs:
        label, sep, path = spec.partition("=")
        if not sep:                      # bare path with no label= prefix
            label, path = os.path.basename(spec), spec
        if not os.path.exists(path):
            continue                     # silently skip missing default variants
        frames = load_variant(path)
        if frames:
            variants.append((label, frames))

    if not variants:
        print("no variants loaded -- generate the frames first "
              "(e.g. python3 scripts/sheet_unifont_5x7.py)")
        return 1

    curses.wrapper(_run, variants, args.fps)
    return 0


def _run(stdscr, variants, fps):
    """The curses loop: draw every variant's current (dir, frame) box, advance the
    shared frame index on a timer, and handle keys."""
    curses.curs_set(0)
    stdscr.nodelay(True)                 # non-blocking getch so animation runs
    stdscr.keypad(True)

    di = 0                               # current direction index into DIRS
    fi = 0                               # shared walk-cycle frame index
    playing = True
    last = time.time()

    while True:
        stdscr.erase()
        d = DIRS[di]
        header = (f"WALK compare   dir={d} ({di + 1}/8)   frame={fi}   "
                  f"fps={fps:.1f}   [<-/-> dir] [space play] [,/. step] [+/- speed] [q quit]")
        try:
            stdscr.addstr(0, 1, header[:curses.COLS - 2], curses.A_BOLD)
        except curses.error:
            pass

        # Lay the variant boxes left to right.
        x = 1
        label_row, art_row = 2, 4
        for label, frames in variants:
            seq = frames.get(d, [])
            # Box width = widest row across THIS variant's frames for this dir.
            box_w = 6
            for fr in seq:
                for r in fr:
                    box_w = max(box_w, len(r))
            try:
                stdscr.addstr(label_row, x, label[:box_w + 2], curses.A_REVERSE)
            except curses.error:
                pass
            if seq:
                fr = seq[fi % len(seq)]  # per-variant wrap (frame counts may differ)
                for r, row in enumerate(fr):
                    try:
                        stdscr.addstr(art_row + r, x, row)
                    except curses.error:
                        pass             # row runs past screen edge -> clip
            x += box_w + 4               # gap between boxes

        stdscr.refresh()

        # Advance the shared frame on the fps timer while playing.
        now = time.time()
        if playing and now - last >= 1.0 / max(0.5, fps):
            fi += 1
            last = now

        ch = stdscr.getch()
        if ch == ord("q"):
            break
        elif ch == ord(" "):
            playing = not playing
        elif ch == curses.KEY_LEFT:
            di = (di - 1) % 8; fi = 0
        elif ch == curses.KEY_RIGHT:
            di = (di + 1) % 8; fi = 0
        elif ch == ord(","):
            fi = max(0, fi - 1); playing = False
        elif ch == ord("."):
            fi += 1; playing = False
        elif ch in (ord("+"), ord("=")):
            fps = min(30.0, fps + 1)
        elif ch == ord("-"):
            fps = max(0.5, fps - 1)
        time.sleep(0.01)                 # keep CPU quiet between polls


if __name__ == "__main__":
    raise SystemExit(main())
