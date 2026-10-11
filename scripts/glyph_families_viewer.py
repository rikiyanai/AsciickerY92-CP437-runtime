#!/usr/bin/env python3
"""Browse ALL discovered glyph families in one animated curses viewer.

Left pane: every family (spinners, ramps, fill-pairs, cycles) in one scrollable
list. Right pane: the selected family ANIMATING in place, so a spinner spins, a
ramp ramps and a fill pulses while you browse. Reads the catalog written by
``glyph_audit.py families --json`` for instant startup; if it is missing the
families are computed on launch (slower).

Run:
    python3 scripts/glyph_audit.py families --json   # build catalog once
    python3 scripts/glyph_families_viewer.py
    python3 scripts/glyph_families_viewer.py --block Arrows

Axes (top legend, switch with m):
    ALL  ROTATE(spin)  DENSITY(ramp)  FILL  CYCLE  STRUCT(topo)
Rotation is its own axis; CYCLE holds animation cycles of every length (3..16),
filterable by length so each band is browsable on its own.

Controls:
    up/down or j/k   select family
    PgUp/PgDn        page
    m                next axis (ALL / ROTATE / DENSITY / FILL / CYCLE / STRUCT)
    1-9              show only families of that length (e.g. 4 = 4-frame cycles)
    0                clear the length filter (all lengths)
    space            pause / resume animation
    + / -            faster / slower
    s                save the family you're looking at to saved_families.jsonl
                     (feeds `glyph_audit.py export-candidates --from saved`)
    w                toggle raw vs normalized rendering
    q or Esc         quit
"""

from __future__ import annotations

import argparse
import curses
import json
import locale
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import glyph_features as gf  # noqa: E402
import glyph_audit as ga  # noqa: E402

N = gf.N
CATALOG = gf.CACHE_DIR / "families.jsonl"
META = gf.CACHE_DIR / "families.meta.json"
MODES = ["all", "spin", "ramp", "fill", "cycle", "topo"]
# Each discovery mode IS an axis; rotation (spin) is its own first-class axis,
# distinct from the near-identical animation cycles. Display names make that
# explicit so "view all axes" reads as real categories.
AXIS_LABEL = {"all": "ALL", "spin": "ROTATE", "ramp": "DENSITY",
              "fill": "FILL", "cycle": "CYCLE", "topo": "STRUCT"}
# the original four are REQUIRED for a usable catalog; topo is optional (present
# only when the catalog was built after the global topology cache existed), so a
# pre-topo catalog stays valid and simply shows no topo families.
CATALOG_MODES = {"spin", "ramp", "fill", "cycle"}


def _catalog_usable(block: str | None) -> tuple[bool, str]:
    if not CATALOG.exists():
        return False, "missing catalog"
    if not META.exists():
        return False, "missing catalog metadata"
    try:
        meta = json.loads(META.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"bad catalog metadata: {exc}"
    if int(meta.get("schema", 0)) != 1:
        return False, "unsupported catalog metadata schema"
    modes = set(meta.get("modes") or [])
    if not CATALOG_MODES.issubset(modes):
        return False, f"catalog modes are narrowed: {sorted(modes)}"
    cached_block = meta.get("block")
    if cached_block and (not block or block.upper() != str(cached_block).upper()):
        return False, f"catalog block is narrowed: {cached_block}"
    return True, ""


def load_families(c: ga.Corpus, block: str | None) -> list[dict]:
    """Families as {mode, members:[corpus idx], block, size}. Prefer the catalog."""
    usable, reason = _catalog_usable(block)
    if usable:
        fams = []
        try:
            for line in CATALOG.read_text().splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                members = [c.i(cp) for cp in r["cps"]]
                members = [m for m in members if m is not None]
                if len(members) < 2:
                    continue
                if block and block.upper() not in (r.get("block", "") or "").upper():
                    continue
                fams.append({"mode": r["mode"], "members": members,
                             "block": r.get("block", ""), "size": len(members)})
            if fams:
                return fams
            reason = "catalog had no usable rows"
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            reason = f"bad catalog rows: {exc}"
    # fallback: compute now
    sys.stderr.write(f"computing families ({reason}; run `families --json` to cache)…\n")
    return ga.collect_families(c, block=block)


def init_colors() -> dict[str, int]:
    curses.start_color()
    try:
        curses.use_default_colors()
    except curses.error:
        pass
    spec = ({
        "hud": (252, -1), "dim": (240, -1), "sel": (16, 252), "ink": (231, -1),
        "spin": (213, -1), "ramp": (84, -1), "fill": (215, -1), "cycle": (81, -1),
        "topo": (208, -1),
    } if curses.COLORS >= 256 else {
        "hud": (curses.COLOR_WHITE, -1), "dim": (curses.COLOR_BLUE, -1),
        "sel": (curses.COLOR_BLACK, curses.COLOR_WHITE), "ink": (curses.COLOR_WHITE, -1),
        "spin": (curses.COLOR_MAGENTA, -1), "ramp": (curses.COLOR_GREEN, -1),
        "fill": (curses.COLOR_YELLOW, -1), "cycle": (curses.COLOR_CYAN, -1),
        "topo": (curses.COLOR_RED, -1),
    })
    pairs = {}
    for i, (name, (fg, bg)) in enumerate(spec.items(), start=1):
        try:
            curses.init_pair(i, fg, bg)
        except curses.error:
            pass
        pairs[name] = curses.color_pair(i)
    return pairs


def safe(win, y, x, text, attr=0):
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w or x < 0:
        return
    try:
        win.addstr(y, x, text[:w - x], attr)
    except curses.error:
        pass


def run(stdscr, c: ga.Corpus, fams_all: list[dict]) -> None:
    curses.curs_set(0)
    stdscr.keypad(True)
    cset = init_colors()
    from collections import Counter
    axis_counts = Counter(f["mode"] for f in fams_all)
    mode_idx = 0
    sel = 0
    top = 0
    frame = 0
    paused = False
    which = "raw"
    fps = 4.0
    len_filter = 0          # 0 = all lengths; 1-9 = only families of that size
    saved_note = ""
    saved_note_until = 0.0
    saved_path = gf.CACHE_DIR / "saved_families.jsonl"
    last = time.monotonic()
    stdscr.timeout(60)

    def view():
        m = MODES[mode_idx]
        fs = fams_all if m == "all" else [f for f in fams_all if f["mode"] == m]
        if len_filter:
            fs = [f for f in fs if f["size"] == len_filter]
        # sort by size so multiple lengths group together (cycles of 3,4,..16
        # become visible bands); rotation/structure read by length too
        return sorted(fs, key=lambda f: (f["mode"], -f["size"]))

    def legend():
        parts = []
        for i, m in enumerate(MODES):
            cnt = len(fams_all) if m == "all" else axis_counts.get(m, 0)
            tag = f"{AXIS_LABEL[m]} {cnt}"
            parts.append(f"[{tag}]" if i == mode_idx else tag)
        return "  ".join(parts)

    while True:
        fams = view()
        if sel >= len(fams):
            sel = max(0, len(fams) - 1)
        h, w = stdscr.getmaxyx()
        list_w = 40
        list_h = h - 4

        if sel < top:
            top = sel
        elif sel >= top + list_h:
            top = sel - list_h + 1

        stdscr.erase()
        safe(stdscr, 0, 1, "Glyph Families — axes: " + legend(), cset["hud"] | curses.A_BOLD)
        lf = f"len={len_filter}" if len_filter else "len=all"
        note = "  " + saved_note if time.monotonic() < saved_note_until else ""
        safe(stdscr, 1, 1, f"{len(fams)} shown  {lf}  "
                           f"fps={fps:.0f} {'PAUSED' if paused else 'PLAY'}  render={which}"
                           + note, cset["dim"])

        # list pane
        for row in range(list_h):
            idx = top + row
            if idx >= len(fams):
                break
            f = fams[idx]
            y = 3 + row
            issel = idx == sel
            attr = cset["sel"] if issel else cset["hud"]
            chars = "".join(chr(int(c.cps[i])) for i in f["members"][:6])
            line = f"{'>' if issel else ' '}[{f['mode']:5}] sz{f['size']:<2} {chars}"
            safe(stdscr, y, 1, line.ljust(list_w), attr)
            if not issel:
                safe(stdscr, y, 2, f"[{f['mode']:5}]", cset.get(f["mode"], cset["hud"]))

        for y in range(3, 3 + list_h):
            safe(stdscr, y, list_w + 1, "│", cset["dim"])

        # detail / animation pane
        if fams:
            f = fams[sel]
            members = f["members"]
            fr = frame % len(members)
            mi = members[fr]
            cp = int(c.cps[mi])
            dx = list_w + 3
            safe(stdscr, 3, dx, f"{f['mode'].upper()}  size {f['size']}  [{f['block']}]",
                 cset.get(f["mode"], cset["hud"]) | curses.A_BOLD)
            safe(stdscr, 4, dx, "frame " + " ".join(
                (f"[{chr(int(c.cps[i]))}]" if k == fr else f" {chr(int(c.cps[i]))} ")
                for k, i in enumerate(members[:16])), cset["hud"])
            safe(stdscr, 5, dx, f"U+{cp:04X}  {c.names[mi]}", cset["dim"])
            grid = getattr(c, which)[mi].reshape(N, N)
            for gy, gr in enumerate(grid):
                safe(stdscr, 7 + gy, dx, "".join("██" if v else "  " for v in gr), cset["ink"])

        safe(stdscr, h - 1, 1,
             "[jk]sel [PgUp/Dn]page [m]axis [1-9]len [0]all [s]ave "
             "[space]pause [+/-]speed [w]raw/norm [q]uit",
             cset["dim"])
        stdscr.refresh()

        # advance animation on a timer regardless of key input
        now = time.monotonic()
        if not paused and now - last >= 1.0 / max(0.5, fps):
            frame += 1
            last = now

        ch = stdscr.getch()
        if ch == -1:
            continue
        if ch in (ord("q"), 27):
            return
        elif ch in (curses.KEY_DOWN, ord("j")):
            sel = min(sel + 1, max(0, len(fams) - 1)); frame = 0
        elif ch in (curses.KEY_UP, ord("k")):
            sel = max(sel - 1, 0); frame = 0
        elif ch == curses.KEY_NPAGE:
            sel = min(sel + list_h, max(0, len(fams) - 1)); frame = 0
        elif ch == curses.KEY_PPAGE:
            sel = max(sel - list_h, 0); frame = 0
        elif ch == ord("m"):
            mode_idx = (mode_idx + 1) % len(MODES); sel = 0; top = 0; frame = 0
        elif ord("1") <= ch <= ord("9"):
            v = ch - ord("0")
            len_filter = 0 if len_filter == v else v; sel = 0; top = 0; frame = 0
        elif ch == ord("0"):
            len_filter = 0; sel = 0; top = 0; frame = 0
        elif ch == ord(" "):
            paused = not paused
        elif ch in (ord("+"), ord("=")):
            fps = min(30.0, fps + 1)
        elif ch == ord("-"):
            fps = max(1.0, fps - 1)
        elif ch == ord("w"):
            which = "norm" if which == "raw" else "raw"
        elif ch == ord("s"):
            if fams:
                f = fams[sel]
                rec = {"mode": f["mode"], "size": f["size"], "block": f["block"],
                       "cps": [int(c.cps[i]) for i in f["members"]],
                       "chars": "".join(chr(int(c.cps[i])) for i in f["members"])}
                try:
                    with open(saved_path, "a") as fh:
                        fh.write(json.dumps(rec) + "\n")
                    saved_note = f"SAVED {rec['chars'][:10]} -> {saved_path.name}"
                except OSError as exc:
                    saved_note = f"save failed: {exc}"
                saved_note_until = time.monotonic() + 2.5


def main() -> int:
    locale.setlocale(locale.LC_ALL, "")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--block", type=str, default=None, help="scope to a block-name substring")
    args = ap.parse_args()
    c = ga.Corpus()
    fams = load_families(c, args.block)
    if not fams:
        print("no families found.", file=sys.stderr)
        return 1
    if not sys.stdout.isatty():
        print(f"{len(fams)} families (needs an interactive TTY to view).", file=sys.stderr)
        return 2
    curses.wrapper(run, c, fams)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
