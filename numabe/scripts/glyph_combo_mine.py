#!/usr/bin/env python3
"""Mine the author's own ASCII art for the glyph combinations he actually uses.

"Used a lot because useful" is a claim about USE, and the only evidence of use
is shipped art. The Stone Story tutorial page plates (archived under
articles/2026-09-07-stone-story-video-transcripts-media/o5v-NS9o4yc/
ascii-tutorial-page/0*.txt: the sacrificial pit layers, the poison-adept walk
cycle, the lines/materials/anti-aliasing plate, the depth/dithering plate, the
subtractive-animation plate) are that evidence: 1,534 lines of the author's
art. This tool counts what recurs in them:

    beside runs      horizontal n-grams, n = 2..MAX_RUN, no spaces inside
    stacked pairs    vertical bigrams (same column, adjacent rows)
    blocks           2x2 cells fully inked

and normalises each under HORIZONTAL MIRROR (reverse + partner substitution,
skill §4.7.7) and VERTICAL FLIP (partner table measured on the raster), so
``_.-´`` and ``´-._`` and ``¯`-.`` count as one family with a member list.
Prose inside the plates (captions such as "(tongue omitted)") is excluded by
dropping any n-gram that is entirely alphanumeric or contains a letter
outside the plate's 12-glyph whitelist.

Each mined family is then rasterised and measured with glyph_cell_pairs
(seam gaps, D_SM, per-cell orientation, altitude profile class:
flat / rising / falling / cap ∩ / cup ∪ / wave), so the mined evidence and the
generated candidates share one vocabulary and one viewer.

Offline authoring tooling; no runtime surface reads it.

Usage:
    python3 scripts/glyph_combo_mine.py                 # table
    python3 scripts/glyph_combo_mine.py --json          # .run/glyph_audit/mined_combos.json (viewer + sheet)
    python3 scripts/glyph_combo_mine.py --min-count 3 --max-run 6
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import glyph_cell_features as gcf  # noqa: E402
import glyph_cell_pairs as gcp  # noqa: E402
import glyph_combo_candidates as gcc  # noqa: E402

REPO_ROOT = SCRIPTS_DIR.parent
PLATE_DIR = (REPO_ROOT / "articles" / "2026-09-07-stone-story-video-transcripts-media"
             / "o5v-NS9o4yc" / "ascii-tutorial-page")
PLATE_GLOB = "0*.txt"
OUT_JSON = gcf.CACHE_DIR / "mined_combos.json"
WHITELIST = set(gcc.SOURCE_ALNUM_GLYPHS)
TRANSPARENT = "#"           # Stone Story's transparency character (skill §7.6)
MAX_RUN_DEFAULT = 6


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------
def load_plates(plate_dir: Path = PLATE_DIR) -> dict[str, list[str]]:
    out = {}
    for f in sorted(glob.glob(str(plate_dir / PLATE_GLOB))):
        text = Path(f).read_text(encoding="utf-8").replace("\t", " ")
        out[Path(f).name] = text.split("\n")
    return out


def _is_art_gram(g: str) -> bool:
    if " " in g or TRANSPARENT in g:
        return False
    if all(c.isalnum() for c in g):
        return False
    return all((not c.isalnum()) or (c in WHITELIST) for c in g)


def count_runs(plates: dict[str, list[str]], max_run: int) -> tuple[dict, dict, dict]:
    beside: dict[str, Counter] = {}
    stacked: Counter = Counter()
    blocks: Counter = Counter()
    src_b: dict[str, Counter] = defaultdict(Counter)
    src_s: dict[str, Counter] = defaultdict(Counter)
    for name, lines in plates.items():
        for l in lines:
            for n in range(2, max_run + 1):
                for i in range(len(l) - n + 1):
                    g = l[i:i + n]
                    if _is_art_gram(g):
                        beside.setdefault(g, Counter())[name] += 1
        for a, b in zip(lines, lines[1:]):
            for i in range(min(len(a), len(b))):
                g = a[i] + b[i]
                if _is_art_gram(g):
                    stacked[g] += 1
                    src_s[g][name] += 1
                if i + 1 < min(len(a), len(b)):
                    q = a[i:i + 2] + b[i:i + 2]
                    if _is_art_gram(q):
                        blocks[q] += 1
    return beside, {g: (c, src_s[g]) for g, c in stacked.items()}, blocks


# ---------------------------------------------------------------------------
# Symmetry normalisation
# ---------------------------------------------------------------------------
class Partners:
    """Horizontal-mirror and vertical-flip partners MEASURED on the raster:
    a glyph's partner is the glyph whose crop-fit raster equals its mirrored /
    flipped crop-fit raster. Falls back to the typed table for the mirror."""

    def __init__(self, scorer: gcf.GlyphScorer, inventory: set[str]):
        self.scorer = scorer
        self.h: dict[str, str] = {}
        self.v: dict[str, str] = {}
        from glyph_features import crop_fit
        keyed: dict[bytes, str] = {}
        grids: dict[str, np.ndarray] = {}
        for ch in inventory:
            g = gcf.cell_raster(scorer, ord(ch)).grid
            if not g.any():
                continue
            grids[ch] = g
            keyed.setdefault(crop_fit(g).tobytes(), ch)
        for ch, g in grids.items():
            # horizontal mirror on the real cell (position matters: ´ vs `)
            hm = np.ascontiguousarray(g[:, ::-1])
            vf = np.ascontiguousarray(g[::-1, :])
            for cand, key in ((crop_fit(hm).tobytes(), "h"), (crop_fit(vf).tobytes(), "v")):
                partner = keyed.get(cand)
                if partner is not None:
                    getattr(self, key)[ch] = partner
        for a, b in gcc.FALLBACK_MIRRORS.items():
            self.h.setdefault(a, b)

    def mirror(self, s: str) -> str:
        return "".join(self.h.get(c, c) for c in reversed(s))

    def flip_pair(self, a: str, b: str) -> str:
        return self.v.get(b, b) + self.v.get(a, a)


def canonical(gram: str, p: Partners) -> str:
    """Canonical member of the horizontal-mirror pair (lexicographically smaller)."""
    return min(gram, p.mirror(gram))


# ---------------------------------------------------------------------------
# Measurement of a mined run
# ---------------------------------------------------------------------------
def profile_class(rows: list[float]) -> str:
    """Altitude (row centroid, y down) along a beside run -> shape word."""
    if len(rows) < 2:
        return "point"
    d = np.diff(rows)
    if np.all(np.abs(d) < 0.75):
        return "flat"
    up = d < -0.75          # ink moving UP the cell (smaller row)
    down = d > 0.75
    if up.any() and not down.any():
        return "rising"
    if down.any() and not up.any():
        return "falling"
    first_up = np.argmax(up) if up.any() else len(d)
    first_down = np.argmax(down) if down.any() else len(d)
    if len(rows) >= 3 and first_up < first_down and not down[:first_down].any():
        return "cap"        # ∩ : goes up then comes down
    if len(rows) >= 3 and first_down < first_up and not up[:first_up].any():
        return "cup"        # ∪ : goes down then comes up
    return "wave"


def measure_run(scorer: gcf.GlyphScorer, gram: str, relation: str) -> dict | None:
    grids = [gcf.cell_raster(scorer, ord(c)) for c in gram]
    if any(r.position_source != "native" or not r.grid.any() for r in grids):
        return None
    widths = {r.cell_w for r in grids}
    if len(widths) != 1:
        return None
    gs = [r.grid for r in grids]
    if relation == "beside":
        cells = [(i, 0, g) for i, g in enumerate(gs)]
        cols, rows_n = len(gs), 1
    else:
        cells = [(0, i, g) for i, g in enumerate(gs)]
        cols, rows_n = 1, len(gs)
    comp = gcp.grid_composite(cells, cols, rows_n)
    pairs = [gcp.pair_features(a, b, relation) for a, b in zip(gs, gs[1:])]
    cen = [float(np.nonzero(g)[0].mean()) for g in gs]
    orient = [gcf.dominant_orientation_deg(g) for g in gs]
    return {
        "rows": ["".join("#" if v else "." for v in r) for r in comp],
        "cols": cols, "rows_n": rows_n,
        "gaps": [p["seam"]["min"] for p in pairs],
        "connected_all": all(p["seam"]["connected"] for p in pairs),
        "pair_dsm": [None if p["continuity"]["dsm"] is None else round(p["continuity"]["dsm"], 3) for p in pairs],
        "dsm": None if gcp.cross_cell_continuity(comp)["dsm"] is None else round(gcp.cross_cell_continuity(comp)["dsm"], 3),
        "orientation": [None if o is None else round(o, 1) for o in orient],
        "composite_orientation": (lambda o: None if o is None else round(o, 1))(gcp.apparent_slope(comp)),
        "altitude": [round(c, 2) for c in cen],
        "profile": profile_class(cen) if relation == "beside" else "stacked",
        "beta": list(gcf.topology(comp)),
    }


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def mine(min_count: int, max_run: int, measure: bool = True) -> dict:
    plates = load_plates()
    beside, stacked, blocks = count_runs(plates, max_run)
    inventory = {c for lines in plates.values() for l in lines for c in l if c not in " #"}
    scorer = gcf.GlyphScorer()
    p = Partners(scorer, inventory)

    fam: dict[tuple[str, str], dict] = {}
    for gram, srcs in beside.items():
        key = ("beside", canonical(gram, p))
        f = fam.setdefault(key, {"relation": "beside", "canonical": key[1], "members": Counter(), "sources": Counter()})
        f["members"][gram] += sum(srcs.values())
        f["sources"].update(srcs)
    for gram, (cnt, srcs) in stacked.items():
        key = ("stacked", canonical(gram, p))
        f = fam.setdefault(key, {"relation": "stacked", "canonical": key[1], "members": Counter(), "sources": Counter()})
        f["members"][gram] += cnt
        f["sources"].update(srcs)

    out = []
    for (rel, canon), f in fam.items():
        total = sum(f["members"].values())
        if total < min_count:
            continue
        # a run that is a repeat of a shorter run counts as the shorter run's extension
        rep = min(f["members"], key=lambda g: (-f["members"][g], g))
        rec = {
            "id": f"mined_{rel}_" + "_".join(f"{ord(c):04X}" for c in rep),
            "shape": f"mined_{rel}",
            "relation": rel,
            "chars": rep, "canonical": canon, "length": len(rep),
            "count": total,
            "members": [{"chars": g, "count": c} for g, c in f["members"].most_common()],
            "sources": dict(f["sources"].most_common()),
            "mirror": p.mirror(rep),
            "flip": "".join(p.v.get(c, c) for c in rep) if rel == "beside" else p.flip_pair(rep[0], rep[1]),
            "self_mirror": p.mirror(rep) == rep,
            "lattice": [rep] if rel == "beside" else list(rep),
            "mirror_lattice": [p.mirror(rep)] if rel == "beside" else list(p.mirror(rep)),
            "cells": ([{"col": i, "row": 0, "cp": ord(c), "char": c} for i, c in enumerate(rep)] if rel == "beside"
                      else [{"col": 0, "row": i, "cp": ord(c), "char": c} for i, c in enumerate(rep)]),
            "names": [__import__("unicodedata").name(c, "") for c in rep],
            "distract": 0.0,
        }
        if measure:
            m = measure_run(scorer, rep, rel)
            if m:
                rec.update(m)
                rec["mirror_rows"] = rec["rows"]
        out.append(rec)
    out.sort(key=lambda r: (-r["count"], r["length"], r["chars"]))
    blocks_out = [{"chars": q, "count": c, "lattice": [q[:2], q[2:]]} for q, c in blocks.most_common(60) if c >= min_count]
    return {
        "plates": {k: len(v) for k, v in plates.items()},
        "inventory": "".join(sorted(inventory)),
        "mirror_partners": dict(sorted(p.h.items())),
        "flip_partners": dict(sorted(p.v.items())),
        "min_count": min_count, "max_run": max_run,
        "families": out,
        "blocks_2x2": blocks_out,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--min-count", type=int, default=3)
    ap.add_argument("--max-run", type=int, default=MAX_RUN_DEFAULT)
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--json", action="store_true", help=f"write {OUT_JSON}")
    ap.add_argument("--no-measure", action="store_true")
    a = ap.parse_args(argv)
    d = mine(a.min_count, a.max_run, measure=not a.no_measure)
    print(f"plates: {d['plates']}")
    print(f"inventory: {len(d['inventory'])} glyphs")
    print(f"mirror partners (measured): {d['mirror_partners']}")
    print(f"flip partners (measured):   {d['flip_partners']}")
    print(f"{len(d['families'])} families with count >= {a.min_count}")
    for r in d["families"][:a.limit]:
        mem = " ".join(f"{m['chars']}×{m['count']}" for m in r["members"][:3])
        meas = (f"  {r.get('profile', '-'):7} gaps {r.get('gaps')} D_SM {r.get('dsm')}"
                if "rows" in r else "  (unmeasured)")
        print(f"{r['relation']:7} {r['chars']!r:12} n={r['count']:<4} len={r['length']} [{mem}]{meas}")
    if a.json:
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(d, ensure_ascii=False))
        print(f"wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
