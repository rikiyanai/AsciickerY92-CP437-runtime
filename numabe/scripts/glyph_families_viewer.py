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
    python3 scripts/glyph_families_viewer.py --saved  # replay your reviewed families
    python3 scripts/glyph_families_viewer.py --foliage-strokes
        # review real stroked glyph sets for tree outer/mid/interior roles
    python3 scripts/glyph_families_viewer.py --mode cycle  # browse every discovered cycle
    python3 scripts/glyph_families_viewer.py --block Arrows
    python3 scripts/glyph_families_viewer.py --mode distract
        # every glyph ranked by DISTRACTION weight, alphanumerics flagged
    python3 scripts/glyph_families_viewer.py --mode combo
    python3 scripts/glyph_families_viewer.py --mode seam   # measured candidates, ranked by continuity
        # the authored 2-3 cell combination dictionary, each beside its mirror
    python3 scripts/glyph_families_viewer.py --mode dir8 --dump
        # text dump of the groups; no TTY needed (headless verification)

Axes (top legend, switch with m):
    ALL  ROTATE(spin)  DIR8  DENSITY(ramp)  FILL  CYCLE  MIRROR  STRUCT(topo)
    DISTRACT  COMBO
Rotation is its own axis; CYCLE holds animation cycles of every length (3..16),
filterable by length so each band is browsable on its own. DISTRACT and COMBO are
authored/derived review screens rather than discovery output, so they are selected
with --mode and replace the family source for that run.

Controls:
    up/down or j/k   select family
    PgUp/PgDn        page
    m                next axis (ALL / ROTATE / DENSITY / FILL / CYCLE / STRUCT)
    1-9              show only families of that length (e.g. 4 = 4-frame cycles)
    0                clear the length filter (all lengths)
    space            pause / resume animation
    + / -            faster / slower
    s                save the family you're looking at to tracked saved_families.jsonl
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
MODES = ["all", "spin", "dir8", "ramp", "fill", "cycle", "mirror", "topo", "stroke",
         "distract", "combo", "seam"]
# Each discovery mode IS an axis; rotation (spin) is its own first-class axis,
# distinct from the near-identical animation cycles. Display names make that
# explicit so "view all axes" reads as real categories. dir8 = 8-way directed
# rotation orbits (up to 8 frames); mirror = left/right chirality pairs.
# distract = every glyph ranked by DISTRACTION weight; combo = the authored
# combination dictionary; seam = MEASURED candidate combinations ranked by the
# xu-2017 continuity distance of their composite raster (glyph_combo_gallery.py).
# Those last three are not discovery output, so they replace the family source
# for the run rather than filtering it.
AXIS_LABEL = {"all": "ALL", "spin": "ROTATE", "dir8": "DIR8", "ramp": "DENSITY",
              "fill": "FILL", "cycle": "CYCLE", "mirror": "MIRROR", "topo": "STRUCT",
              "stroke": "FOLIAGE STROKES", "distract": "DISTRACT", "combo": "COMBO",
              "seam": "SEAM"}
# Axes whose row order carries meaning (ranking / authored file order) and must
# therefore survive the list pane's size sort.
ORDERED_MODES = {"distract", "combo", "seam"}
# Measured candidate data written by scripts/glyph_combo_gallery.py.
MEASURED = gf.CACHE_DIR / "glyph_combo_measured.json"
# The authored combination dictionary (offsets + codepoints + generated mirrors).
COMBINATIONS = gf.REPO_ROOT / "assets" / "glyphs" / "authored" / "glyph_combinations.v1.json"
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


def load_saved_families(c: ga.Corpus, block: str | None) -> list[dict]:
    """Load the durable operator-reviewed sequences in their saved order.

    The discovery catalog remains the owner of *new* candidate families.  This
    reader only replays JSONL records the reviewer explicitly saved, so the
    ``--saved`` screen cannot be diluted by regenerated catalog output.
    """
    if not gf.SAVED_FAMILIES.exists():
        return []
    families: list[dict] = []
    try:
        lines = gf.SAVED_FAMILIES.read_text().splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            cps = [int(cp) for cp in record["cps"]]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
        members = [c.i(cp) for cp in cps]
        if len(members) < 2 or any(member is None for member in members):
            continue
        name = str(record.get("block", ""))
        if block and block.upper() not in name.upper():
            continue
        families.append({
            "mode": str(record.get("mode", "cycle")),
            "members": members,
            "block": name,
            "size": len(members),
            "role_hint": str(record.get("role_hint", "")),
            "note": str(record.get("note", "")),
        })
    return families


def load_foliage_stroke_families(c: ga.Corpus) -> list[dict]:
    """Load review-only real-glyph sets for non-uniform tree foliage.

    These are deliberately not similarity clusters. Each sequence is selected
    for a distinct visible tree role, so reviewers can judge stroke language
    rather than inherit the uniform Katakana texture from the retired canopy
    palette. The atlas and runtime pools remain unchanged.
    """
    candidates = (
        ("CJK Strokes", "outer_leaf_sweep", "thin directional leaf contours",
         (0x31C0, 0x31C1, 0x31C2, 0x31C3, 0x31C4, 0x31C5)),
        ("CJK Strokes", "outer_leaf_hook", "hooked leaf-edge contour variation",
         (0x31CA, 0x31CB, 0x31CC, 0x31CD, 0x31CE, 0x31CF)),
        ("CJK Strokes", "twig_and_vein", "short branch and vein strokes",
         (0x31D0, 0x31D1, 0x31D2, 0x31D3, 0x31D4, 0x31D5)),
        ("CJK Strokes", "outer_leaf_fork", "forked and split leaf-edge strokes",
         (0x31D6, 0x31D7, 0x31D8, 0x31D9, 0x31DA, 0x31DB, 0x31DC, 0x31DD)),
        ("CJK Strokes", "outer_leaf_turn", "long turning strokes for wind-facing edges",
         (0x31DE, 0x31DF, 0x31E0, 0x31E1, 0x31E2, 0x31E3)),
        ("CJK Radicals Supplement", "grass_radical_marks", "radical-scale grass and moss marks",
         (0x2E80, 0x2E81, 0x2E82, 0x2E83, 0x2E84, 0x2E85)),
        ("Hiragana", "outer_leaf_curve", "curved, sparse outer-leaf silhouettes",
         (0x3057, 0x3064, 0x305D, 0x306E, 0x308B, 0x308C)),
        ("Katakana", "outer_leaf_angle", "angular outer-leaf silhouettes",
         (0x30CE, 0x30D5, 0x30CC, 0x30E1, 0x30E9, 0x30EF)),
        ("Hangul Compatibility Jamo", "outer_leaf_chevron", "chevron and fork leaf outlines",
         (0x3145, 0x3148, 0x314A, 0x314B, 0x314C, 0x314D, 0x314E)),
        ("Arabic", "outer_leaf_calligraphic", "flowing, tapered outer-leaf strokes",
         (0x062C, 0x062D, 0x062E, 0x0633, 0x0634, 0x0635, 0x0636)),
        ("Arabic", "mid_canopy_calligraphic", "linked curved mid-canopy marks",
         (0x0639, 0x063A, 0x0641, 0x0642, 0x0646, 0x0647, 0x0648)),
        ("CJK Radicals", "mid_canopy_branch", "branching mid-canopy marks",
         (0x5DDD, 0x6728, 0x6797, 0x68EE)),
        ("CJK Unified Ideographs", "dense_canopy_mass", "dense interior foliage and occlusion",
         (0x8349, 0x6797, 0x68EE, 0x8449, 0x8449)),
        ("CJK Unified Ideographs", "dense_canopy_crosshatch", "high-stroke-count interior leaf mass",
         (0x8449, 0x8449, 0x85C1, 0x85CD, 0x85EA, 0x8607)),
        ("CJK Radicals", "ground_tuft_and_moss", "grass, moss, and root-base accents",
         (0x5C71, 0x5DDD, 0x8349, 0x6728)),
    )
    families: list[dict] = []
    for block, role, note, cps in candidates:
        members = [c.i(cp) for cp in cps]
        members = [member for member in members if member is not None]
        if len(members) >= 2:
            families.append({
                "mode": "stroke", "members": members, "block": block,
                "size": len(members), "role_hint": role, "note": note,
            })
    return families


def load_distraction_families(c: ga.Corpus, block: str | None,
                              limit: int = 0) -> list[dict]:
    """Every glyph in scope as a one-glyph row, ranked most-distracting first.

    This is a ranking screen, not a similarity family: the point is to see which
    glyphs pull the eye out of the line art before they are admitted to a
    vocabulary. The alphanumeric hard flag is surfaced on every row, because the
    design source treats it as a category difference rather than a degree — see
    glyph_features.distraction_components for the cited tutorial passages.
    """
    allow = ga.block_allow(c, block)
    rows = ga.rank_distraction(c, allow)
    if limit:
        rows = rows[:limit]
    families: list[dict] = []
    for rank, r in enumerate(rows, 1):
        flag = "ALNUM" if r["alnum"] else "-"
        if r["alnum"] and r["whitelisted"]:
            flag = "ALNUM(kept)"
        elif r["alnum"] and r["marginal"]:
            flag = "ALNUM(marginal)"
        families.append({
            "mode": "distract",
            "members": [r["index"]],
            "block": r["block"],
            "size": 1,
            "role_hint": f"#{rank}  weight {r['weight']:.3f}  {flag}",
            "note": (f"ink {r['density']:.3f}  ncomp {r['ncomp']}  "
                     f"family median {r['family_median_density']:.3f}"),
            "distract": r,
        })
    return families


def _combo_bitmap(c: ga.Corpus, cells: list[dict], cols: int, rows: int):
    """Composite ink bitmap for one combination, on the REAL cell grid.

    Each cell's glyph is pasted at its (col, row) offset using the same rasterized
    ``c.raw`` grid the single-glyph pane draws, so a combination is shown in the
    atlas rasterization rather than as a re-drawn approximation. Returns
    (bitmap, missing_codepoints)."""
    import numpy as np
    bm = np.zeros((rows * N, cols * N), np.uint8)
    missing: list[int] = []
    for d in cells:
        i = c.i(int(d["cp"]))
        if i is None:
            missing.append(int(d["cp"]))
            continue
        col, row = int(d["col"]), int(d["row"])
        bm[row * N:(row + 1) * N, col * N:(col + 1) * N] = c.raw[i].reshape(N, N)
    return bm, missing


def _bitmap_rows(bm) -> list[str]:
    """One character per pixel — combinations are several cells wide, so the
    two-character ink used by the single-glyph pane would not fit beside a mirror."""
    return ["".join("█" if v else " " for v in row) for row in bm]


def load_combination_families(c: ga.Corpus) -> list[dict]:
    """Load the authored combination dictionary in file order.

    Each entry carries the composite bitmap of the combination and of its
    horizontal mirror, so the viewer can show the pair side by side. The mirror
    cells are stored in the file (generated from glyph_audit.find_mirror pairs);
    this reader does not re-derive them — scripts/tests owns that check.
    """
    if not COMBINATIONS.exists():
        return []
    try:
        doc = json.loads(COMBINATIONS.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"bad combination dictionary {COMBINATIONS}: {exc}\n")
        return []
    families: list[dict] = []
    for cb in doc.get("combinations", []):
        cells = cb.get("cells") or []
        mcells = (cb.get("mirror") or {}).get("cells") or []
        if not cells:
            continue
        cols, rows = int(cb.get("cols", 1)), int(cb.get("rows", 1))
        bm, missing = _combo_bitmap(c, cells, cols, rows)
        mbm, mmissing = _combo_bitmap(c, mcells, cols, rows) if mcells else (bm, [])
        members = [c.i(int(d["cp"])) for d in cells]
        members = [m for m in members if m is not None]
        families.append({
            "mode": "combo",
            "members": members,
            "block": "authored",
            "size": len(cells),
            "role_hint": str(cb.get("id", "")),
            "note": str(cb.get("label", "")),
            "combo": {
                "id": str(cb.get("id", "")),
                "label": str(cb.get("label", "")),
                "note": str(cb.get("note", "")),
                "source": str(cb.get("source", doc.get("source", ""))),
                "chars": "".join(str(d.get("char", "")) for d in cells),
                "mirror_chars": "".join(str(d.get("char", "")) for d in mcells),
                "self_symmetric": bool((cb.get("mirror") or {}).get("self_symmetric")),
                "rows": _bitmap_rows(bm),
                "mirror_rows": _bitmap_rows(mbm),
                "missing": missing + mmissing,
            },
        })
    return families



def _half_block_rows(rows: list[str]) -> list[str]:
    """Pack two raster rows per text row with half blocks, so an 8x16 cell shows
    as 8x8 text cells and the terminal's ~1:2 cell restores the true 1:2 pixel
    aspect. One char per pixel row would stretch every glyph 2x vertically (the
    tilde read as an N)."""
    out = []
    for y in range(0, len(rows), 2):
        top = rows[y]
        bot = rows[y + 1] if y + 1 < len(rows) else "." * len(top)
        out.append("".join(
            "\u2588" if t == "#" and b == "#" else "\u2580" if t == "#" else "\u2584" if b == "#" else " "
            for t, b in zip(top, bot)))
    return out


def _seam_family(c: ga.Corpus, rec: dict, role: str, block: str, note: str, source: str) -> dict:
    """One measured combination as a viewer family.

    The composite rows come from the measured data itself (cells cropped to
    their real 8- or 16-px width), so a beside seam is drawn touching, unlike
    the authored ``combo`` path which pastes 16-px canvases. The label shows the
    glyphs on their lattice in reading order, so a corner reads as two rows."""
    cells, mcells = rec["cells"], rec.get("mirror_cells") or rec["cells"]
    members = [m for m in (c.i(int(d["cp"])) for d in cells) if m is not None]
    lattice = rec.get("lattice") or ["".join(str(d.get("char", "")) for d in cells)]
    mlattice = rec.get("mirror_lattice") or ["".join(str(d.get("char", "")) for d in mcells)]
    chars, mchars = " / ".join(lattice), " / ".join(mlattice)
    rows = _half_block_rows(rec["rows"])
    mrows = _half_block_rows(rec.get("mirror_rows") or rec["rows"])
    return {
        "mode": "seam", "members": members, "block": block, "size": len(cells),
        "role_hint": f"{role}  {chars}", "note": note,
        "combo": {"id": role, "label": note, "note": note, "source": source,
                  "chars": chars, "mirror_chars": mchars, "self_symmetric": chars == mchars,
                  "rows": rows, "mirror_rows": mrows, "missing": []},
    }


def load_seam_families(c: ga.Corpus, limit: int = 0) -> list[dict]:
    """MEASURED candidate combinations, ranked by continuity (lower DSM first).

    Source: .run/glyph_audit/glyph_combo_measured.json from glyph_combo_gallery.py
    (built here when missing; ~7 s). Order: the anti-aliasing family stacked over
    ``|`` (ranked by distance from a continuous bar), then every candidate whose
    seams all have a 0 px gap, then the top rows per lattice shape. Numbers are
    measured on the composite raster; see glyph_cell_pairs.py."""
    if not MEASURED.exists():
        import glyph_combo_gallery as gcg
        sys.stderr.write("measuring combinations (first run)...\n")
        gcg.main([])
    try:
        d = json.loads(MEASURED.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"bad measured data {MEASURED}: {exc}\n")
        return []
    fams: list[dict] = []
    ref = d["study_ref"]
    for n, s in enumerate(d["study"], 1):
        note = (f"gap {s['gap_min']} px   D_SM vs \u2502\u2502 {s['dsm_box']}   vs straight stroke {s['dsm_stroke']}"
                f"   (\u2502 over \u2502: gap {ref['gap_min']} px, D_SM 0; lower = reads more as one stroke)")
        fams.append(_seam_family(c, s, f"study #{n}  {s['char']} over |", "over |", note,
                                 "anti-aliasing family stacked over |, ranked by DSM vs a continuous bar"))
    for n, r in enumerate(d["connected"], 1):
        note = f"CONNECTED  D_SM {r['dsm']}   gaps {r['gaps']}   port {r['port']}   distract {r['distract']}"
        fams.append(_seam_family(c, r, f"connected #{n}  {r['shape']}", "0 px seams", note,
                                 " / ".join(r["names"])))
    for shape, rows in d["shapes"].items():
        for n, r in enumerate(rows, 1):
            if limit and n > limit:
                break
            if r["shape"].startswith("mined_"):
                mem = "  ".join(f"{m['chars']}\u00d7{m['count']}" for m in r.get("members", [])[:4])
                note = (f"USED {r['count']}\u00d7 in the author's plates   {r.get('profile', '')}   "
                        f"orient {r.get('orientation')}   gaps {r.get('gaps')}   D_SM {r.get('dsm')}   [{mem}]")
            elif r["shape"].startswith("runs_"):
                note = (f"{r.get('profile', '')}   rules {'/'.join(r.get('rules', []))}   score {r.get('score')}   "
                        f"gaps {r.get('gaps')}   orient {r.get('orientation')}   D_SM {r.get('dsm')}   "
                        f"x{len(r.get('members', []))}   " + " / ".join(dict.fromkeys(r.get("blocks") or [])))
            else:
                note = (f"D_SM {r['dsm']}   gaps {r['gaps']}   port {r['port']}   orient {r.get('orientation')}   "
                        f"distract {r['distract']}   " + " / ".join(r.get("blocks") or []))
            fams.append(_seam_family(c, r, f"#{n} of {d['counts'][shape]}  {shape}", shape, note,
                                     " / ".join(r["names"])))
    return fams


def dump_families(c: ga.Corpus, fams: list[dict], mode: str) -> None:
    """Print the groups as text so they can be verified without a TTY.

    The curses screen is the review surface, but every axis must also be readable
    from a pipe: that is what makes a change to dir8/distract/combo checkable in a
    test or a report."""
    from collections import Counter
    counts = Counter(f["mode"] for f in fams)
    # Honour the selected axis exactly as the curses list pane does: `--mode dir8
    # --dump` must print the dir8 groups, not the whole catalog.
    shown = fams if mode == "all" else [f for f in fams if f["mode"] == mode]
    print(f"# glyph_families_viewer --dump   mode={mode}   families={len(shown)}"
          f"   (loaded {len(fams)})")
    print("# axes: " + ", ".join(f"{k}:{v}" for k, v in sorted(counts.items())))
    for n, f in enumerate(shown, 1):
        members = " ".join(f"U+{int(c.cps[i]):04X}({chr(int(c.cps[i]))})"
                           for i in f["members"])
        head = (f"#{n:4} [{f['mode']:8}] size={f['size']:<3} [{f['block']}]")
        role = f.get("role_hint", "")
        print(f"{head}  {role}  {members}".rstrip())
        combo = f.get("combo")
        if combo:
            print(f"       {combo['id']}: {combo['chars']}  mirror {combo['mirror_chars']}"
                  + ("  (self-symmetric)" if combo["self_symmetric"] else ""))
            print(f"       source: {combo['source']}")
            if combo["missing"]:
                print("       MISSING from cache: "
                      + " ".join(f"U+{cp:04X}" for cp in combo["missing"]))
            gap = "   "
            for a, b in zip(combo["rows"], combo["mirror_rows"]):
                print(f"       |{a}|{gap}|{b}|")


def init_colors() -> dict[str, int]:
    curses.start_color()
    try:
        curses.use_default_colors()
    except curses.error:
        pass
    spec = ({
        "hud": (252, -1), "dim": (240, -1), "sel": (16, 252), "ink": (231, -1),
        "spin": (213, -1), "dir8": (198, -1), "ramp": (84, -1), "fill": (215, -1),
        "cycle": (81, -1), "mirror": (147, -1), "topo": (208, -1),
        "distract": (203, -1), "combo": (114, -1), "seam": (214, -1),
    } if curses.COLORS >= 256 else {
        "hud": (curses.COLOR_WHITE, -1), "dim": (curses.COLOR_BLUE, -1),
        "sel": (curses.COLOR_BLACK, curses.COLOR_WHITE), "ink": (curses.COLOR_WHITE, -1),
        "spin": (curses.COLOR_MAGENTA, -1), "dir8": (curses.COLOR_MAGENTA, -1),
        "ramp": (curses.COLOR_GREEN, -1),
        "fill": (curses.COLOR_YELLOW, -1), "cycle": (curses.COLOR_CYAN, -1),
        "mirror": (curses.COLOR_BLUE, -1), "topo": (curses.COLOR_RED, -1),
        "distract": (curses.COLOR_RED, -1), "combo": (curses.COLOR_GREEN, -1),
        "seam": (curses.COLOR_YELLOW, -1),
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


def run(stdscr, c: ga.Corpus, fams_all: list[dict], initial_mode: str, saved_only: bool) -> None:
    curses.curs_set(0)
    stdscr.keypad(True)
    cset = init_colors()
    from collections import Counter
    axis_counts = Counter(f["mode"] for f in fams_all)
    mode_idx = MODES.index(initial_mode)
    sel = 0
    top = 0
    frame = 0
    paused = False
    which = "raw"
    fps = 4.0
    len_filter = 0          # 0 = all lengths; 1-9 = only families of that size
    saved_note = ""
    saved_note_until = 0.0
    saved_path = gf.SAVED_FAMILIES
    last = time.monotonic()
    stdscr.timeout(60)

    def view():
        m = MODES[mode_idx]
        fs = fams_all if m == "all" else [f for f in fams_all if f["mode"] == m]
        if len_filter:
            fs = [f for f in fs if f["size"] == len_filter]
        if m in ORDERED_MODES:
            # ranking order (distract) and authored file order (combo) ARE the
            # information; a size sort would destroy them.
            return fs
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
        scope = "SAVED" if saved_only else ("FOLIAGE STROKES" if all(
            f["mode"] == "stroke" for f in fams_all) else "DISCOVERED")
        safe(stdscr, 0, 1, f"Glyph Families ({scope}) — axes: " + legend(), cset["hud"] | curses.A_BOLD)
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
            dx = list_w + 3
            avail = max(0, w - dx - 1)
            safe(stdscr, 3, dx, f"{f['mode'].upper()}  size {f['size']}  [{f['block']}]",
                 cset.get(f["mode"], cset["hud"]) | curses.A_BOLD)
            combo = f.get("combo")
            if combo:
                # A combination is a multi-cell picture, so the pane shows the whole
                # composite next to its horizontal mirror rather than one animating
                # glyph. Side by side when the pane is wide enough, stacked when not.
                safe(stdscr, 4, dx, f"{combo['chars']}   mirror {combo['mirror_chars']}"
                     + ("   (self-symmetric)" if combo["self_symmetric"] else ""),
                     cset["hud"])
                safe(stdscr, 5, dx, combo["note"][:avail], cset["dim"])
                safe(stdscr, 6, dx, f"source: {combo['source']}"[:avail], cset["dim"])
                main_rows, mir_rows = combo["rows"], combo["mirror_rows"]
                cw = len(main_rows[0]) if main_rows else 0
                if cw and cw * 2 + 4 <= avail:
                    safe(stdscr, 7, dx, "ORIGINAL".ljust(cw + 4) + "MIRROR", cset["dim"])
                    for gy, (a, b) in enumerate(zip(main_rows, mir_rows)):
                        safe(stdscr, 8 + gy, dx, a, cset["ink"])
                        safe(stdscr, 8 + gy, dx + cw + 4, b, cset["ink"])
                else:
                    safe(stdscr, 7, dx, "ORIGINAL", cset["dim"])
                    for gy, a in enumerate(main_rows):
                        safe(stdscr, 8 + gy, dx, a, cset["ink"])
                    y0 = 9 + len(main_rows)
                    safe(stdscr, y0 - 1, dx, "MIRROR", cset["dim"])
                    for gy, b in enumerate(mir_rows):
                        safe(stdscr, y0 + gy, dx, b, cset["ink"])
            elif members:
                fr = frame % len(members)
                mi = members[fr]
                cp = int(c.cps[mi])
                safe(stdscr, 4, dx, "frame " + " ".join(
                    (f"[{chr(int(c.cps[i]))}]" if k == fr else f" {chr(int(c.cps[i]))} ")
                    for k, i in enumerate(members[:16])), cset["hud"])
                safe(stdscr, 5, dx, f"U+{cp:04X}  {c.names[mi]}", cset["dim"])
                role_hint = f.get("role_hint", "")
                if role_hint:
                    safe(stdscr, 6, dx, f"role: {role_hint}", cset["dim"])
                note = f.get("note", "")
                if note:
                    safe(stdscr, 7, dx, note[:avail], cset["dim"])
                grid = getattr(c, which)[mi].reshape(N, N)
                for gy, gr in enumerate(grid):
                    safe(stdscr, 8 + gy, dx,
                         "".join("██" if v else "  " for v in gr), cset["ink"])

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
                    saved_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(saved_path, "a") as fh:
                        fh.write(json.dumps(rec) + "\n")
                    saved_note = f"SAVED {rec['chars'][:10]} -> {saved_path.relative_to(gf.REPO_ROOT)}"
                except OSError as exc:
                    saved_note = f"save failed: {exc}"
                saved_note_until = time.monotonic() + 2.5


def main() -> int:
    locale.setlocale(locale.LC_ALL, "")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--block", type=str, default=None, help="scope to a block-name substring")
    ap.add_argument("--saved", action="store_true",
                    help="replay only the tracked operator-reviewed saved_families registry")
    ap.add_argument("--foliage-strokes", action="store_true",
                    help="review actual stroked glyph sets for tree foliage; no atlas or runtime mutation")
    ap.add_argument("--mode", choices=MODES[1:], default="all",
                    help="open on one animation axis; use 'cycle' for all discovered "
                         "cycles, 'distract' for the DISTRACTION ranking, 'combo' for "
                         "the authored combination dictionary, 'seam' for measured "
                         "candidates ranked by continuity")
    ap.add_argument("--dump", action="store_true",
                    help="print the groups as text and exit; no TTY required, so the "
                         "selected axis can be verified from a pipe or a test")
    ap.add_argument("--limit", type=int, default=0,
                    help="distract / seam: keep only the top N rows (per shape for seam; 0 = all)")
    args = ap.parse_args()
    c = ga.Corpus()
    if args.saved and args.foliage_strokes:
        ap.error("--saved and --foliage-strokes select different review sources")
    if (args.saved or args.foliage_strokes) and args.mode in ("distract", "combo", "seam"):
        ap.error(f"--mode {args.mode} replaces the family source; it cannot be "
                 f"combined with --saved / --foliage-strokes")
    if args.foliage_strokes:
        fams = load_foliage_stroke_families(c)
        initial_mode = "stroke"
    elif args.mode == "distract":
        fams = load_distraction_families(c, args.block, args.limit)
        initial_mode = "distract"
    elif args.mode == "combo":
        fams = load_combination_families(c)
        initial_mode = "combo"
    elif args.mode == "seam":
        fams = load_seam_families(c, args.limit)
        initial_mode = "seam"
    else:
        fams = load_saved_families(c, args.block) if args.saved else load_families(c, args.block)
        initial_mode = args.mode
    if not fams:
        source = {"distract": "ranked glyphs", "combo": "combinations",
                  "seam": "measured combinations"}.get(
            args.mode, "saved families" if args.saved else "families")
        print(f"no {source} found.", file=sys.stderr)
        return 1
    if args.dump:
        dump_families(c, fams, initial_mode)
        return 0
    if not sys.stdout.isatty():
        print(f"{len(fams)} families (needs an interactive TTY to view, or --dump).",
              file=sys.stderr)
        return 2
    curses.wrapper(run, c, fams, initial_mode, args.saved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
