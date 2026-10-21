#!/usr/bin/env python3
# =============================================================================
# sprite_idle.py
# =============================================================================
# Terminal (TUI) previewer that ROTATES an 8-direction character sprite in place,
# so you can watch the "idle" turntable (the figure facing N, NE, E, SE, S, SW, W,
# NW and around again). Each pose is drawn planted in the same spot and stepped on
# a timer.
#
# TWO ALIGNMENT GUARANTEES so poses don't jump around as they cycle:
#   1. SAME HEIGHT: every pose is padded to TARGET_H rows (default 5).
#   2. NO OFFSET: each pose is placed by its INK CENTROID, not by the spaces you
#      happened to type. That is the key fix — a west pose typed at column 3 and
#      its east mirror typed at column 0 used to jump sideways; now both are
#      centred on their own centre of mass, so the body stays put while it turns.
#
# USE
#   python3 scripts/sprite_idle.py           # rotate at 4 frames/sec
#   python3 scripts/sprite_idle.py --fps 2   # slower
#   controls:  space pause · ←/→ step one dir · +/- speed · r reverse · q quit
#
# EDIT: the FRAMES dict below. Keep each pose to TARGET_H (5) rows for best
# results (short ones are auto-padded, taller ones are allowed but set the box).
# To add a WALK CYCLE later, change a direction's value from a single string to a
# LIST of strings — the loop advances through a list automatically.
# =============================================================================

from __future__ import annotations

import argparse
import curses
import locale
import os
import time

# Enable wide/Unicode glyphs in curses (runic, kana, Adlam, box-drawing…).
locale.setlocale(locale.LC_ALL, "")

TARGET_H = 5   # every pose is normalised to this many rows (box HEIGHT)
TARGET_W = 5   # box WIDTH in glyphs — the sprite cell is TARGET_W x TARGET_H
# The PELVIS is the pivot the figure turns around: horizontal centre of the box,
# and the lower-torso row. Every pose is planted so its centre of mass sits here,
# and the guide (press g) marks it so you can align each pose's pelvis to it.
PELVIS_COL = TARGET_W // 2   # = 2 for a 5-wide box (0-indexed centre column)
PELVIS_ROW = 3               # 0-indexed hip/legs row of a 5-row pose

# ---------------------------------------------------------------------------
# ====== EDIT YOUR SPRITE HERE ======  (one pose per direction, 5 rows each)
# Closing """ sits on its own line so a pose may safely END in a backslash.
# ---------------------------------------------------------------------------
FRAMES: dict[str, object] = {
    # NOTE: FALLBACK only (frame 1). Live poses+walks load from
    # sprite_frames.txt (vertical blank-line frames). Edit the .txt.
    "N": r"""
  9
 /)\
  𞤧 .
 | |
 , ,
""",
    "NE": r"""
  p
 /𑪴2
 `𑪴 \
  Γ\
  l L
""",
    "E": r"""
 (}
r[)
\ 𞤈|
 |\
 ╚╚
""",
    "SE": r"""
 (ᛅ
 /ⴡ)
| Ɏ \
  /7
 / ╚
""",
    "S": r"""
  o
 rY|
 \M \
 / |
 J L
""",
    "SW": r"""
ᚾ)
(ꟶ\
/༆ |
؟\
╝ \
""",
    "W": r"""
 {)
 (]𐔾
 /ꭹ|
 /|
 ╝╝
""",
    "NW": r"""
 q
𞤃𑪲 \
/ 𑪲´
/ר
ᒧ l
""",
}

# Clockwise turn order for the idle rotation.
ORDER = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
ARROW = {"N": "↑", "NE": "↗", "E": "→", "SE": "↘",
         "S": "↓", "SW": "↙", "W": "←", "NW": "↖"}


def _as_lines(block: str) -> list[str]:
    """Pose string -> list of rows: drop surrounding blank lines, strip trailing
    spaces, then PAD to TARGET_H rows so every pose is the same height. (Poses
    taller than TARGET_H are kept as-is; the render just uses their real height.)"""
    lines = [ln.rstrip() for ln in block.strip("\n").split("\n")]
    # Leading spaces are KEPT: in raw mode they position the figure exactly (what
    # you type in sprite_frames.txt is what shows); centred mode ignores them by
    # using the ink bounding box. Pad short poses to the target height.
    while len(lines) < TARGET_H:
        lines.append("")             # bottom-pad short poses to the target height
    return lines


def _get_pose(direction: str, tick: int) -> list[str]:
    """Rows to draw for a direction. A single string = static pose; a list of
    strings = per-direction animation (walk cycle) that advances one per tick."""
    val = FRAMES[direction]
    if isinstance(val, list):
        return _as_lines(val[tick % len(val)])
    return _as_lines(val)


def _ink_centroid(lines: list[str]) -> tuple[float, float]:
    """Centre of mass (col, row) of the non-space characters — the anchor we plant
    at screen centre so every pose lines up regardless of typed indentation."""
    xs = 0.0
    ys = 0.0
    n = 0
    for r, line in enumerate(lines):
        for cidx, ch in enumerate(line):
            if ch != " ":
                xs += cidx
                ys += r
                n += 1
    if n == 0:
        return 0.0, 0.0
    return xs / n, ys / n


def _frames_list(direction: str) -> list[str]:
    """The animation frames for a direction as a list (>=1). The text file loads a
    list; the embedded fallback stores a single string — normalise both to a list."""
    val = FRAMES[direction]
    return val if isinstance(val, list) else [val]


def load_frames_txt(path: str) -> dict:
    """Parse a sprite_frames.txt into {DIRECTION: [frame, frame, ...]}.

    LAYOUT: a line "# DIR" (DIR in ORDER) starts a direction. Under it, each pose
    is a block of up to 5 art rows; consecutive frames are STACKED, separated by a
    BLANK LINE. Frame 1 is the idle/standing pose; frames 2+ are the walk cycle.
    The next "# DIR" label ends the direction. A direction with one block is idle
    only. Leading spaces are kept (they position the figure); other #-lines are
    comments. Returns {} if the file is missing so the embedded FRAMES stay."""
    def _push(buf: list[str], acc: list[str]) -> list[str]:
        # Turn the accumulated lines into one frame and append it (skip empties).
        text = "\n".join(buf).strip("\n")
        if text.strip():
            acc.append(text)
        return []

    frames: dict[str, list[str]] = {}
    cur: str | None = None
    acc: list[str] = []            # frames collected for the current direction
    buf: list[str] = []            # lines of the frame currently being read
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.rstrip("\n")
                s = line.strip()
                if s.startswith("#"):
                    token = s[1:].strip()
                    if token in ORDER:             # new direction header
                        buf = _push(buf, acc)
                        if cur is not None:
                            frames[cur] = acc or [""]
                        cur, acc, buf = token, [], []
                    continue                       # header or comment: not art
                if s == "":                        # blank line -> frame boundary
                    buf = _push(buf, acc)
                    continue
                if cur is not None:
                    buf.append(line)               # a pose row (leading spaces kept)
            buf = _push(buf, acc)                  # flush last frame + direction
            if cur is not None:
                frames[cur] = acc or [""]
    except OSError:
        return {}
    return frames


def run(stdscr, fps: float) -> None:
    # Some otherwise usable terminals do not expose cursor-visibility control.
    # Drawing and input remain valid there, so treat this cosmetic capability as
    # optional instead of aborting the viewer before its first frame.
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    stdscr.nodelay(True)
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_YELLOW, -1)

    i = 0          # which direction (index into ORDER)
    tick = 0       # per-direction animation counter (walk lists)
    step = 1       # +1 clockwise, -1 counter-clockwise
    paused = False
    walk = False   # False: idle turntable (rotate through directions, standing pose).
                   # True: walk preview — HOLD a direction and cycle its walk frames.
    wtick = 0      # walk-frame counter (advances only in walk mode)
    guide = True   # draw the TARGET_W x TARGET_H box + pelvis pivot marker
    auto = True    # True (default): pose fills the cell vertically and is CENTRED
                   #   horizontally on the pelvis column, so every pose pivots
                   #   around the ┼ (col 2) with no sideways jump.
                   # False (raw): pose sits top-left in the cell exactly as typed,
                   #   for nudging a pose's pelvis onto the ┼ by hand.
    last = time.monotonic()

    while True:
        now = time.monotonic()
        if not paused and now - last >= 1.0 / max(0.5, fps):
            if walk:
                wtick += 1                     # advance the walk frame (direction held)
            else:
                i = (i + step) % len(ORDER)    # rotate to the next direction
            last = now

        d = ORDER[i]
        flist = _frames_list(d)
        if walk:
            lines = _as_lines(flist[wtick % len(flist)])
        else:
            lines = _as_lines(flist[0])        # idle shows the standing (first) pose

        stdscr.erase()
        H, W = stdscr.getmaxyx()
        c1 = curses.color_pair(1) if curses.has_colors() else 0
        c2 = curses.color_pair(2) if curses.has_colors() else 0

        if walk:
            nfr = len(flist)
            state = "PAUSED" if paused else f"{fps:.1f}/s"
            title = f" WALK   {d} {ARROW[d]}   frame {wtick % nfr + 1}/{nfr}   {state} "
        else:
            state = "PAUSED" if paused else f"{fps:.1f}/s {'CW' if step > 0 else 'CCW'}"
            title = f" idle rotation   {d} {ARROW[d]}   {i + 1}/{len(ORDER)}   {state} "
        try:
            stdscr.addstr(1, 2, title, c2 | curses.A_BOLD)
        except curses.error:
            pass

        # The 5x5 sprite cell is centred on screen; the pelvis pivot is its centre
        # column / hip row. box_top/left is the cell's top-left corner.
        box_top = H // 2 - PELVIS_ROW
        box_left = W // 2 - PELVIS_COL

        # ---- guide: a frame drawn JUST OUTSIDE the cell (never overlaps the pose)
        # plus the pelvis-pivot marker inside (the pose draws over it). ----
        if guide:
            dim = curses.A_DIM
            t, b = box_top - 1, box_top + TARGET_H       # frame rows (outside cell)
            lft, rgt = box_left - 1, box_left + TARGET_W  # frame cols (outside cell)
            for cc in range(lft, rgt + 1):
                for yy in (t, b):
                    try:
                        stdscr.addstr(yy, cc, "─", dim)
                    except curses.error:
                        pass
            for row in range(t, b + 1):
                for xx in (lft, rgt):
                    try:
                        stdscr.addstr(row, xx, "│", dim)
                    except curses.error:
                        pass
            for yy, xx, corner in ((t, lft, "┌"), (t, rgt, "┐"), (b, lft, "└"), (b, rgt, "┘")):
                try:
                    stdscr.addstr(yy, xx, corner, dim)
                except curses.error:
                    pass
            # Pelvis pivot shown as TICKS ON THE FRAME (never drawn over the pose):
            # ┬/┴ mark the centre column, ├/┤ mark the hip row. Their crossing point
            # is the pivot — but nothing is drawn inside the cell, so your art is
            # exactly what you typed.
            pcol = box_left + PELVIS_COL
            prow = box_top + PELVIS_ROW
            for yy, mark in ((t, "┬"), (b, "┴")):
                try:
                    stdscr.addstr(yy, pcol, mark, c2 | curses.A_DIM)
                except curses.error:
                    pass
            for xx, mark in ((lft, "├"), (rgt, "┤")):
                try:
                    stdscr.addstr(prow, xx, mark, c2 | curses.A_DIM)
                except curses.error:
                    pass

        # ---- draw the pose (always fills the cell vertically -> no spill) ----
        origin_row = box_top
        if auto:
            # Anchor on the SPINE — the head (row 0) + torso (row 2, the "𞤧/𞤈"
            # body glyph) — and centre THAT on the pelvis column. The arms (row 1)
            # and the walking legs (rows 3-4) are excluded, so a step or arm-swing
            # never shifts the body: the head+torso stay on the centreline frame to
            # frame. Falls back to the whole figure if those rows are empty.
            spine = []
            for rr in (0, 2):
                if rr < len(lines):
                    spine += [ci for ci, ch in enumerate(lines[rr]) if ch != " "]
            if not spine:
                spine = [ci for line in lines for ci, ch in enumerate(line) if ch != " "]
            centre = (min(spine) + max(spine)) / 2.0 if spine else PELVIS_COL
            origin_col = box_left + PELVIS_COL - int(round(centre))
        else:
            origin_col = box_left      # raw: pose top-left in the cell, as typed
        for r, line in enumerate(lines):
            row = origin_row + r
            if 0 < row < H - 1:
                try:
                    stdscr.addstr(row, max(0, origin_col), line, c1)
                except curses.error:
                    pass

        controls = ("space pause · ←/→ dir · +/- speed · r reverse · "
                    "g guide · a auto/raw · w walk/idle · q quit")
        try:
            stdscr.addstr(H - 2, 2, controls[:max(0, W - 4)])
        except curses.error:
            pass
        stdscr.refresh()

        ch = stdscr.getch()
        if ch == -1:
            time.sleep(0.02)
            continue
        if ch in (ord("q"), 27):
            break
        elif ch == ord(" "):
            paused = not paused
        elif ch == ord("w"):
            walk = not walk       # toggle walk-cycle preview vs idle rotation
            wtick = 0
            paused = False
        elif ch in (curses.KEY_RIGHT, ord("l")):
            # change facing; in idle mode also pause on the new direction
            i = (i + 1) % len(ORDER); wtick = 0
            if not walk:
                paused = True
        elif ch in (curses.KEY_LEFT, ord("h")):
            i = (i - 1) % len(ORDER); wtick = 0
            if not walk:
                paused = True
        elif ch in (ord("+"), ord("=")):
            fps = min(30.0, fps + 0.5)
        elif ch in (ord("-"), ord("_")):
            fps = max(0.5, fps - 0.5)
        elif ch == ord("r"):
            step = -step
        elif ch == ord("g"):
            guide = not guide
        elif ch == ord("a"):
            auto = not auto


def main() -> int:
    ap = argparse.ArgumentParser(description="Rotate an 8-direction sprite in place (idle preview).")
    ap.add_argument("--fps", type=float, default=4.0, help="rotation speed frames/sec (default 4)")
    ap.add_argument("--frames", default=None,
                    help="path to a sprite_frames.txt (default: alongside this script)")
    args = ap.parse_args()
    # Poses come from the editable text file if present — it OVERRIDES the embedded
    # FRAMES so you can iterate on the art without touching any Python.
    txt = args.frames or os.path.join(os.path.dirname(os.path.abspath(__file__)), "sprite_frames.txt")
    loaded = load_frames_txt(txt)
    if loaded:
        FRAMES.update(loaded)
    curses.wrapper(run, args.fps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
