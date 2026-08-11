#!/usr/bin/env python3
"""Standalone Unicode glyph morphology browser (TUI spike).

Browse *all* of Unicode by rendered shape and morphology. Navigation spans every
official Unicode block across all 17 planes (~347 named blocks, ~148.8k assigned
codepoints). Each glyph is rendered from the repo's bundled ``unifont-17.0.04.otf``
into a 16x16 ink grid (the same ``cell_px`` the material shape catalog uses) and
scored with the exact same morphology functions the real catalog baker uses
(:mod:`generate_glyph_shape_catalog`). Nothing here touches Godot or asciiid --
it is a pure exploration spike for picking distinctive terrain/material glyphs.

Coverage reality: morphology only exists for glyphs the font can actually draw.
``unifont-17.0.04.otf`` renders ~58,910 codepoints (essentially the whole BMP
plus a slice of CJK Ext-B); the remaining ~91k assigned codepoints (most of the
SMP -- emoji, math alphanumerics, ancient scripts) have no glyph in this font and
appear as tofu with no shape data. Use the scope toggle to switch between:
    renderable  -- only codepoints unifont draws (default; every row has metrics)
    assigned    -- every assigned codepoint in the block (font-blanks shown tofu)
    range       -- every codepoint in the block range, assigned or not
To actually render the SMP, drop ``unifont_upper-17.0.04.otf`` next to the base
font (GNU Unifont ships it) -- this browser will pick up the wider cmap.

Run standalone:

    python3 scripts/glyph_morphology_browser.py
    python3 scripts/glyph_morphology_browser.py --block "Geometric Shapes"
    python3 scripts/glyph_morphology_browser.py --list-blocks

Or launch it from ``scripts/launcher.py`` -> Dev Tool Scripts.

Controls (shown in the footer):
    up/down or j/k    move selection
    PgUp/PgDn         page
    left/right or [ ]  previous / next Unicode block
    n / p             next / prev *non-empty* block (skips blocks the font lacks)
    s                 cycle sort key (then scans the block)
    S                 toggle sort direction
    f                 cycle stroke-class filter (then scans the block)
    t                 cycle scope (renderable / assigned / range)
    /                 jump to codepoint (hex) or search by name substring
    b                 jump to a block by name substring
    q or Esc          quit
"""

from __future__ import annotations

import argparse
import curses
import locale
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

FONT_PATH = REPO_ROOT / "assets" / "fonts" / "unifont-17.0.04.otf"
CELL_PX = 16  # matches material.*.shape_catalog.json cell_px so metrics are comparable
INK_THRESHOLD = 96
# Sorting/filtering a block needs every member scored. CJK Unified is ~20k glyphs;
# scanning all of that per keypress is too slow for a spike, so we cap and SAY SO.
MAX_BLOCK_SCAN = 8192

# Reuse the canonical morphology vocabulary instead of re-deriving it.
from generate_glyph_shape_catalog import (  # noqa: E402
    analyze_grid,
    repertoire_for_scalar,
    shape6_metrics,
)
from fl4482_font_chain import FONT_DIR, discover_font_chain  # noqa: E402

try:
    from PIL import Image, ImageDraw, ImageFont  # noqa: E402
except Exception as exc:  # pragma: no cover - dependency guard
    print(f"Pillow (PIL) is required for the glyph browser: {exc}", file=sys.stderr)
    raise SystemExit(2)


# ---------------------------------------------------------------------------
# Full official Unicode block table, sourced from fontTools (all 17 planes).
# Falls back to a tiny curated table only if fontTools is unavailable.
# ---------------------------------------------------------------------------
def build_blocks() -> list[tuple[int, int, str]]:
    try:
        from fontTools.unicodedata import Blocks
    except Exception:
        return [
            (0x0021, 0x007E, "Basic Latin"),
            (0x2190, 0x21FF, "Arrows"),
            (0x2200, 0x22FF, "Mathematical Operators"),
            (0x2500, 0x257F, "Box Drawing"),
            (0x2580, 0x259F, "Block Elements"),
            (0x25A0, 0x25FF, "Geometric Shapes"),
            (0x2600, 0x26FF, "Miscellaneous Symbols"),
            (0x3040, 0x309F, "Hiragana"),
            (0x30A0, 0x30FF, "Katakana"),
            (0x4E00, 0x9FFF, "CJK Unified Ideographs"),
        ]
    starts = list(Blocks.RANGES)
    names = list(Blocks.VALUES)
    out: list[tuple[int, int, str]] = []
    for i, lo in enumerate(starts):
        hi = (starts[i + 1] - 1) if i + 1 < len(starts) else 0x10FFFF
        name = names[i]
        if name == "No_Block":
            continue
        out.append((lo, hi, name))
    return out


BLOCKS: list[tuple[int, int, str]] = build_blocks()


def char_width(ch: str) -> int:
    """Terminal columns a glyph occupies (wide East-Asian glyphs take 2)."""
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


@dataclass
class GlyphEntry:
    cp: int
    char: str
    name: str
    repertoire: str
    blank: bool
    metrics: dict = field(default_factory=dict)
    shape6: dict = field(default_factory=dict)


class GlyphScorer:
    """Renders codepoints through a font chain and scores them; results cached.

    Every font's cmap is read up front so we know exactly which codepoints are
    renderable, and which font to rasterize each one with, without trial render.
    """

    def __init__(self, font_path: Path | None = None, cell_px: int = CELL_PX):
        self.cell_px = cell_px
        self._cache: dict[int, GlyphEntry] = {}
        chain = discover_font_chain()
        if font_path is not None and font_path not in chain and font_path.exists():
            chain = [font_path] + chain
        if not chain:
            raise SystemExit(f"no fonts found in {FONT_DIR}")
        # chain entries: (cmap_set, small_font, big_font_or_None, display_name)
        # unifont is a pixel font designed for the 16px cell -> render native.
        # outline fonts (Noto/BabelStone) need a hi-res render + crop + fit so the
        # glyph fills the cell instead of sitting tiny at the baseline.
        self.chain: list[tuple] = []
        self.font_cps: set[int] = set()
        for p in chain:
            try:
                small = ImageFont.truetype(str(p), cell_px)
            except Exception:
                continue
            is_pixel = p.name.startswith("unifont")
            big = None
            if not is_pixel:
                try:
                    big = ImageFont.truetype(str(p), cell_px * 6)
                except Exception:
                    big = None
            cm = self._load_cmap(p)
            self.chain.append((cm, small, big, p.name))
            self.font_cps |= cm
        if not self.chain:
            raise SystemExit("no loadable fonts in chain")
        # First font is the default for unknown/blank codepoints.
        self.font = self.chain[0][1]
        self.font_names = [name for _cm, _s, _b, name in self.chain]

    @staticmethod
    def _load_cmap(path: Path) -> set[int]:
        try:
            from fontTools.ttLib import TTFont
            tt = TTFont(str(path), fontNumber=0, lazy=True)
            cps = set(tt.getBestCmap().keys())
            tt.close()
            return cps
        except Exception:
            return set()

    def renderable(self, cp: int) -> bool:
        return (cp in self.font_cps) if self.font_cps else True

    def font_for(self, cp: int) -> str:
        for cm, _s, _b, name in self.chain:
            if cp in cm:
                return name
        return self.font_names[0]

    def _pick_entry(self, cp: int):
        for cm, small, big, name in self.chain:
            if cp in cm:
                return small, big, name
        return self.font, None, self.font_names[0]

    def ink_grid(self, ch: str) -> list[list[int]]:
        n = self.cell_px
        small, big, _name = self._pick_entry(ord(ch))
        if big is None:
            # Pixel font (unifont): native render, exact 16px cell.
            img = Image.new("L", (n, n), 0)
            try:
                ImageDraw.Draw(img).text((0, 0), ch, fill=255, font=small)
            except Exception:
                return [[0] * n for _ in range(n)]
            px = img.load()
            return [[1 if px[x, y] > INK_THRESHOLD else 0 for x in range(n)] for y in range(n)]
        return self._render_fit(ch, big)

    def _render_fit(self, ch: str, big_font) -> list[list[int]]:
        """Hi-res render of an outline glyph, cropped to ink and fit-centered
        into the NxN cell so its shape (not its font metrics) drives morphology."""
        n = self.cell_px
        zero = [[0] * n for _ in range(n)]
        try:
            bbox = big_font.getbbox(ch)
        except Exception:
            return zero
        if not bbox:
            return zero
        bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if bw <= 0 or bh <= 0:
            return zero
        pad = 6
        canvas = Image.new("L", (bw + pad * 2, bh + pad * 2), 0)
        try:
            ImageDraw.Draw(canvas).text((pad - bbox[0], pad - bbox[1]), ch, fill=255, font=big_font)
        except Exception:
            return zero
        ink = canvas.getbbox()
        if ink is None:
            return zero
        crop = canvas.crop(ink)
        cw, ch_ = crop.size
        scale = min(n / cw, n / ch_)
        nw, nh = max(1, round(cw * scale)), max(1, round(ch_ * scale))
        rs = crop.resize((nw, nh), Image.LANCZOS)
        out = Image.new("L", (n, n), 0)
        out.paste(rs, ((n - nw) // 2, (n - nh) // 2))
        px = out.load()
        return [[1 if px[x, y] > INK_THRESHOLD else 0 for x in range(n)] for y in range(n)]

    def score(self, cp: int) -> GlyphEntry:
        cached = self._cache.get(cp)
        if cached is not None:
            return cached
        ch = chr(cp)
        grid = self.ink_grid(ch)
        ink = sum(sum(row) for row in grid)
        name = unicodedata.name(ch, "")
        rep = repertoire_for_scalar(cp)
        if ink == 0:
            entry = GlyphEntry(cp, ch, name, rep, blank=True)
        else:
            metrics = analyze_grid(grid)
            shape6 = shape6_metrics(grid)
            entry = GlyphEntry(cp, ch, name, rep, blank=False, metrics=metrics, shape6=shape6)
            entry.grid = grid  # type: ignore[attr-defined]
        self._cache[cp] = entry
        return entry


SORT_KEYS = [
    ("codepoint", lambda e: e.cp),
    ("density", lambda e: e.metrics.get("density", 0.0)),
    ("curve_score", lambda e: e.metrics.get("curve_score", 0.0)),
    ("corner_score", lambda e: e.metrics.get("corner_score", 0.0)),
    ("stroke_class", lambda e: e.metrics.get("stroke_class", "")),
    ("top_weight", lambda e: e.metrics.get("top_weight", 0.0)),
    ("bottom_weight", lambda e: e.metrics.get("bottom_weight", 0.0)),
    ("diag_nw_se", lambda e: e.metrics.get("diag_nw_se", 0.0)),
    ("diag_ne_sw", lambda e: e.metrics.get("diag_ne_sw", 0.0)),
]

STROKE_CLASSES = [
    "all", "dot", "line", "curve", "corner", "diagonal",
    "horizontal", "vertical", "cross", "block",
]

# Codepoint-membership scopes.
SCOPE_RENDERABLE = 0  # only what the font draws
SCOPE_ASSIGNED = 1    # every assigned codepoint (font-blanks shown tofu)
SCOPE_RANGE = 2       # every codepoint in the block range
SCOPE_NAMES = ["renderable", "assigned", "range"]


class BrowserState:
    def __init__(self, scorer: GlyphScorer, start_block: int = 0):
        self.scorer = scorer
        self.block_idx = start_block
        self.sel = 0
        self.top = 0
        self.sort_idx = 0
        self.sort_desc = False
        self.filter_idx = 0  # index into STROKE_CLASSES
        self.scope = SCOPE_RENDERABLE
        self.view: list[int] = []
        self.status = ""
        self.scanned_note = ""
        # Whole-font coverage numbers for the header (computed once).
        self.total_renderable = len(scorer.font_cps)
        self.refresh_block(scan=False)

    @property
    def block(self) -> tuple[int, int, str]:
        return BLOCKS[self.block_idx]

    @property
    def stroke_filter(self) -> str:
        return STROKE_CLASSES[self.filter_idx]

    def _block_cps(self, lo: int, hi: int) -> list[int]:
        if self.scope == SCOPE_RENDERABLE and self.scorer.font_cps:
            return [cp for cp in range(lo, hi + 1) if cp in self.scorer.font_cps]
        if self.scope == SCOPE_ASSIGNED:
            return [cp for cp in range(lo, hi + 1) if unicodedata.name(chr(cp), "")]
        return list(range(lo, hi + 1))

    def block_count(self, idx: int) -> int:
        lo, hi, _n = BLOCKS[idx]
        return len(self._block_cps(lo, hi))

    def refresh_block(self, scan: bool, progress=None) -> None:
        lo, hi, _name = self.block
        cps = self._block_cps(lo, hi)
        sort_active = self.sort_idx != 0
        filter_active = self.filter_idx != 0
        self.scanned_note = ""
        if scan or sort_active or filter_active:
            scan_cps = cps[:MAX_BLOCK_SCAN]
            if len(cps) > MAX_BLOCK_SCAN:
                self.scanned_note = f"scanned {MAX_BLOCK_SCAN} of {len(cps)} (capped)"
            entries = []
            total = len(scan_cps)
            for i, cp in enumerate(scan_cps):
                e = self.scorer.score(cp)
                if progress and (i % 256 == 0):
                    progress(i, total)
                if filter_active and (e.blank or e.metrics.get("stroke_class") != self.stroke_filter):
                    continue
                entries.append(e)
            if sort_active:
                # blanks (no metrics) sort to the end regardless of direction
                key = SORT_KEYS[self.sort_idx][1]
                entries.sort(key=lambda e: (e.blank, key(e)), reverse=self.sort_desc)
            self.view = [e.cp for e in entries]
        else:
            self.view = cps
            if len(cps) > MAX_BLOCK_SCAN:
                self.scanned_note = f"{len(cps)} cps (lazy)"
        self.sel = 0
        self.top = 0

    def current_entry(self) -> GlyphEntry | None:
        if not self.view:
            return None
        cp = self.view[max(0, min(self.sel, len(self.view) - 1))]
        return self.scorer.score(cp)

    def step_nonempty(self, direction: int, progress=None) -> None:
        n = len(BLOCKS)
        for _ in range(n):
            self.block_idx = (self.block_idx + direction) % n
            if self.block_count(self.block_idx) > 0:
                break
        self.refresh_block(scan=False, progress=progress)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def init_colors() -> dict[str, int]:
    curses.start_color()
    try:
        curses.use_default_colors()
    except curses.error:
        pass
    pairs = {}
    if curses.COLORS >= 256:
        spec = {
            "hud": (252, -1), "help": (245, -1), "sel": (16, 252),
            "glyph": (231, -1), "low": (39, -1), "mid": (190, -1),
            "high": (208, -1), "dim": (240, -1), "accent": (201, -1),
            "ink": (231, -1),
        }
    else:
        spec = {
            "hud": (curses.COLOR_WHITE, -1), "help": (curses.COLOR_CYAN, -1),
            "sel": (curses.COLOR_BLACK, curses.COLOR_WHITE),
            "glyph": (curses.COLOR_WHITE, -1), "low": (curses.COLOR_BLUE, -1),
            "mid": (curses.COLOR_GREEN, -1), "high": (curses.COLOR_RED, -1),
            "dim": (curses.COLOR_BLUE, -1), "accent": (curses.COLOR_MAGENTA, -1),
            "ink": (curses.COLOR_WHITE, -1),
        }
    for i, (name, (fg, bg)) in enumerate(spec.items(), start=1):
        try:
            curses.init_pair(i, fg, bg)
        except curses.error:
            pass
        pairs[name] = curses.color_pair(i)
    return pairs


def bar_color(value: float, c: dict[str, int]) -> int:
    if value < 0.34:
        return c["low"]
    if value < 0.66:
        return c["mid"]
    return c["high"]


def safe_addstr(win, y: int, x: int, text: str, attr: int = 0) -> None:
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    if x < 0:
        text = text[-x:]
        x = 0
    avail = w - x
    if avail <= 0:
        return
    try:
        win.addstr(y, x, text[:avail], attr)
    except (curses.error, ValueError):
        pass


def printable_glyph(entry: "GlyphEntry") -> str:
    """A glyph safe to draw: control/format/surrogate codepoints can't be sent
    to curses.addstr (embedded nulls etc.), and have no meaningful shape."""
    if entry.blank:
        return "·"
    if unicodedata.category(entry.char).startswith("C") or ord(entry.char) < 0x20:
        return "·"
    return entry.char


def draw_bar(win, y: int, x: int, width: int, value: float, c: dict[str, int], label: str) -> None:
    value = max(0.0, min(1.0, float(value)))
    filled = int(round(value * width))
    bar = "█" * filled + "░" * (width - filled)
    safe_addstr(win, y, x, f"{label:<11}", c["hud"])
    safe_addstr(win, y, x + 12, bar, bar_color(value, c))
    safe_addstr(win, y, x + 12 + width + 1, f"{value:.3f}", c["dim"])


def draw(stdscr, st: BrowserState, c: dict[str, int]) -> None:
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    lo, hi, bname = st.block
    sort_name = SORT_KEYS[st.sort_idx][0]
    arrow = "↓" if st.sort_desc else "↑"
    plane = lo >> 16

    # Header
    title = "Glyph Morphology Browser — spike (unifont-17.0.04, cell 16px)"
    safe_addstr(stdscr, 0, 1, title, c["hud"] | curses.A_BOLD)
    cov = f"font renders {st.total_renderable:,} cps · all {len(BLOCKS)} Unicode blocks"
    safe_addstr(stdscr, 0, max(1, w - len(cov) - 1), cov, c["dim"])
    hdr = (f"Block [{st.block_idx + 1}/{len(BLOCKS)}] {bname}  U+{lo:04X}–U+{hi:04X} (plane {plane})"
           f"   sort={sort_name}{arrow}  filter={st.stroke_filter}  scope={SCOPE_NAMES[st.scope]}")
    safe_addstr(stdscr, 1, 1, hdr, c["help"])
    note = st.scanned_note or f"{len(st.view)} shown"
    safe_addstr(stdscr, 1, max(1, w - len(note) - 1), note, c["dim"])

    list_top = 3
    list_h = h - list_top - 2
    list_w = 34
    detail_x = list_w + 3

    if st.sel < st.top:
        st.top = st.sel
    elif st.sel >= st.top + list_h:
        st.top = st.sel - list_h + 1

    # List pane
    if not st.view:
        safe_addstr(stdscr, list_top, 2, "(no codepoints in this block for this scope — press 't')", c["dim"])
    for row in range(list_h):
        idx = st.top + row
        if idx >= len(st.view):
            break
        entry = st.scorer.score(st.view[idx])
        y = list_top + row
        is_sel = idx == st.sel
        attr = c["sel"] if is_sel else c["hud"]
        marker = ">" if is_sel else " "
        gly = printable_glyph(entry)
        gw = char_width(gly)
        sc = entry.metrics.get("stroke_class", "—blank—")
        dens = entry.metrics.get("density", 0.0)
        line1 = f"{marker} U+{entry.cp:04X} "
        safe_addstr(stdscr, y, 1, line1, attr)
        gx = 1 + len(line1)
        gattr = (c["sel"] if is_sel else (c["dim"] if entry.blank else c["glyph"]))
        safe_addstr(stdscr, y, gx, gly, gattr | curses.A_BOLD)
        rest = f" {sc:<10} {dens:.2f}" if not entry.blank else f" {sc:<10}"
        safe_addstr(stdscr, y, gx + gw, rest, attr)

    for y in range(list_top, list_top + list_h):
        safe_addstr(stdscr, y, list_w + 1, "│", c["dim"])

    # Detail pane
    entry = st.current_entry()
    if entry is not None:
        dy = list_top
        safe_addstr(stdscr, dy, detail_x, f"U+{entry.cp:04X}  {entry.name or '(unnamed)'}",
                    c["accent"] | curses.A_BOLD)
        safe_addstr(stdscr, dy + 1, detail_x,
                    f"repertoire: {entry.repertoire}   font: {st.scorer.font_for(entry.cp)}", c["help"])
        if entry.blank:
            why = ("unassigned codepoint" if not entry.name else "no glyph in this font")
            safe_addstr(stdscr, dy + 3, detail_x, f"(blank — {why})", c["dim"])
            safe_addstr(stdscr, dy + 4, detail_x,
                        "no morphology; only font-renderable glyphs have shape data", c["dim"])
        else:
            grid = getattr(entry, "grid", None)
            if grid:
                for gy, gridrow in enumerate(grid):
                    s = "".join("██" if v else "  " for v in gridrow)
                    safe_addstr(stdscr, dy + 3 + gy, detail_x, s, c["ink"])
            mx = detail_x + CELL_PX * 2 + 3
            m = entry.metrics
            safe_addstr(stdscr, dy + 3, mx, f"stroke_class: {m.get('stroke_class')}", c["hud"] | curses.A_BOLD)
            bars = [
                ("density", m.get("density", 0)),
                ("curve", m.get("curve_score", 0)),
                ("corner", m.get("corner_score", 0)),
                ("top", m.get("top_weight", 0)),
                ("mid", m.get("mid_weight", 0)),
                ("bottom", m.get("bottom_weight", 0)),
                ("left", m.get("left_weight", 0)),
                ("right", m.get("right_weight", 0)),
                ("diag NW-SE", m.get("diag_nw_se", 0)),
                ("diag NE-SW", m.get("diag_ne_sw", 0)),
            ]
            for i, (lbl, val) in enumerate(bars):
                draw_bar(stdscr, dy + 5 + i, mx, 16, val, c, lbl)
            s6 = entry.shape6.get("shape6_norm") or entry.shape6.get("shape6")
            if s6:
                safe_addstr(stdscr, dy + 5 + len(bars) + 1, mx,
                            "shape6: " + " ".join(f"{v:.2f}" for v in s6), c["dim"])

    foot = ("[↑↓/jk] move  [PgDn/Up] page  [←→/[]] block  [n/p] non-empty  "
            "[s]ort [S]dir  [f]ilter  [t]scope  [/]find  [b]lock  [q]uit")
    safe_addstr(stdscr, h - 1, 1, foot[:w - 2], c["help"])
    if st.status:
        safe_addstr(stdscr, h - 2, 1, st.status[:w - 2], c["accent"])
    stdscr.refresh()


def prompt(stdscr, c: dict[str, int], label: str) -> str:
    h, w = stdscr.getmaxyx()
    safe_addstr(stdscr, h - 2, 1, " " * (w - 2))
    safe_addstr(stdscr, h - 2, 1, label, c["accent"] | curses.A_BOLD)
    curses.echo()
    curses.curs_set(1)
    try:
        stdscr.move(h - 2, 1 + len(label))
        raw = stdscr.getstr(h - 2, 1 + len(label), 40)
        text = raw.decode("utf-8", "ignore").strip()
    except Exception:
        text = ""
    finally:
        curses.noecho()
        curses.curs_set(0)
    return text


def progress_drawer(stdscr, c: dict[str, int]):
    def _draw(i: int, total: int):
        h, w = stdscr.getmaxyx()
        pct = (i / total) if total else 1.0
        msg = f"scanning… {i}/{total} ({pct*100:.0f}%)"
        safe_addstr(stdscr, h - 2, 1, " " * (w - 2))
        safe_addstr(stdscr, h - 2, 1, msg, c["accent"])
        stdscr.refresh()
    return _draw


def find_block_for_cp(cp: int) -> int | None:
    for i, (lo, hi, _n) in enumerate(BLOCKS):
        if lo <= cp <= hi:
            return i
    return None


def run(stdscr, start_block: int) -> None:
    curses.curs_set(0)
    stdscr.keypad(True)
    c = init_colors()
    scorer = GlyphScorer(FONT_PATH)
    st = BrowserState(scorer, start_block)
    pd = lambda: progress_drawer(stdscr, c)

    while True:
        st.status = ""
        draw(stdscr, st, c)
        ch = stdscr.getch()

        if ch in (ord("q"), 27):
            return
        elif ch in (curses.KEY_DOWN, ord("j")):
            st.sel = min(st.sel + 1, max(0, len(st.view) - 1))
        elif ch in (curses.KEY_UP, ord("k")):
            st.sel = max(st.sel - 1, 0)
        elif ch == curses.KEY_NPAGE:
            st.sel = min(st.sel + 20, max(0, len(st.view) - 1))
        elif ch == curses.KEY_PPAGE:
            st.sel = max(st.sel - 20, 0)
        elif ch == curses.KEY_HOME:
            st.sel = 0
        elif ch == curses.KEY_END:
            st.sel = max(0, len(st.view) - 1)
        elif ch in (curses.KEY_RIGHT, ord("]")):
            st.block_idx = (st.block_idx + 1) % len(BLOCKS)
            st.refresh_block(scan=False, progress=pd())
        elif ch in (curses.KEY_LEFT, ord("[")):
            st.block_idx = (st.block_idx - 1) % len(BLOCKS)
            st.refresh_block(scan=False, progress=pd())
        elif ch == ord("n"):
            st.step_nonempty(1, progress=pd())
        elif ch == ord("p"):
            st.step_nonempty(-1, progress=pd())
        elif ch == ord("s"):
            st.sort_idx = (st.sort_idx + 1) % len(SORT_KEYS)
            st.refresh_block(scan=True, progress=pd())
        elif ch == ord("S"):
            st.sort_desc = not st.sort_desc
            st.refresh_block(scan=True, progress=pd())
        elif ch == ord("f"):
            st.filter_idx = (st.filter_idx + 1) % len(STROKE_CLASSES)
            st.refresh_block(scan=True, progress=pd())
        elif ch == ord("t"):
            st.scope = (st.scope + 1) % len(SCOPE_NAMES)
            st.refresh_block(scan=False, progress=pd())
        elif ch == ord("/"):
            q = prompt(stdscr, c, "find U+hex or name: ")
            target = None
            if q:
                qs = q.strip()
                hexpart = qs[2:] if qs.lower().startswith("u+") else qs
                try:
                    target = int(hexpart, 16)
                except ValueError:
                    up = qs.upper()
                    for i, cp in enumerate(st.view):
                        if up in scorer.score(cp).name:
                            st.sel = i
                            target = None
                            break
                    else:
                        st.status = f"no name match for '{qs}' in this block/scope"
            if target is not None:
                bi = find_block_for_cp(target)
                if bi is None:
                    st.status = f"U+{target:04X} is outside the assigned block table"
                else:
                    st.block_idx = bi
                    st.refresh_block(scan=False, progress=pd())
                    if target in st.view:
                        st.sel = st.view.index(target)
                    else:
                        st.status = f"U+{target:04X} not in current scope — press 't'"
        elif ch == ord("b"):
            q = prompt(stdscr, c, "block name contains: ")
            if q:
                up = q.upper()
                for i, (_lo, _hi, n) in enumerate(BLOCKS):
                    if up in n.upper():
                        st.block_idx = i
                        st.refresh_block(scan=False, progress=pd())
                        break
                else:
                    st.status = f"no block matches '{q}'"


def main() -> int:
    locale.setlocale(locale.LC_ALL, "")
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--block", type=str, default=None,
                        help="start on a block whose name contains this substring")
    parser.add_argument("--list-blocks", action="store_true", help="print the block table and exit")
    parser.add_argument("--coverage", action="store_true",
                        help="print font/Unicode coverage stats and exit")
    args = parser.parse_args()

    if args.list_blocks:
        for i, (lo, hi, n) in enumerate(BLOCKS):
            print(f"{i:3d}  U+{lo:05X}–U+{hi:05X}  plane {lo >> 16}  {n}")
        print(f"\n{len(BLOCKS)} named Unicode blocks")
        return 0

    if args.coverage:
        sc = GlyphScorer(FONT_PATH)
        assigned = sum(1 for cp in range(0x110000) if unicodedata.name(chr(cp), ""))
        print(f"font renderable codepoints : {len(sc.font_cps):,}")
        print(f"assigned codepoints (U16.0): {assigned:,}")
        if sc.font_cps:
            inter = sum(1 for cp in sc.font_cps if unicodedata.name(chr(cp), ""))
            print(f"renderable AND assigned    : {inter:,}")
            print(f"assigned but NOT in font   : {assigned - inter:,}  (tofu in 'assigned'/'range' scope)")
        print(f"named Unicode blocks       : {len(BLOCKS)}")
        print(f"font chain ({len(sc.chain)}):")
        for cm, _s, _b, name in sc.chain:
            print(f"    {len(cm):>7,}  {name}")
        return 0

    start = 0
    if args.block:
        up = args.block.upper()
        for i, (_lo, _hi, n) in enumerate(BLOCKS):
            if up in n.upper():
                start = i
                break

    if not sys.stdout.isatty():
        print("glyph_morphology_browser needs an interactive TTY.", file=sys.stderr)
        return 2
    curses.wrapper(run, start)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
