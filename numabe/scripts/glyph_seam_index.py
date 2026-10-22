#!/usr/bin/env python3
"""Generate glyph PAIR candidates from measured features over the whole repertoire.

The typed-alphabet generator (glyph_combo_candidates.py) enumerates a product
over 29 plate glyphs. This module replaces the search space with the measured
cache of every atlas-admitted codepoint (glyph_cell_features.py --build) and
GENERATES pairs by joining on seam features instead of enumerating:

    port join    A's exit-edge contact mask  ∩  B's entry-edge contact mask
                 (box drawing, CJK strokes, anything that reaches the cell edge)
    band join    the same on the 3-pixel EDGE BANDS with a seam gap ≤ --max-gap
                 (the anti-aliasing family: | ! , ' . stop short of the edge)

both with Jaccard ≥ --jaccard. Every joined pair is then placed on the
composite raster and scored with glyph_cell_pairs (seam gap, port overlap,
D_SM against a straight stroke); the best --keep rows are written per
(relation, cell width) for the seam viewer and the sheet.

Sizing, measured on the 2026-09-08 cache (69,062 native inked glyphs):
110k x 110k is 12 billion ordered pairs; the exact-edge-mask join alone is
34k (8-wide stacked) to 813k (16-wide stacked) among stroke-like glyphs, so
--stroke-like, --blocks, --per-glyph and --max-pairs bound the scored set.
The PAGE alphabet, alphanumeric status and block are TAGS on the output, not
the search space (chafa's rule: tags select the set, geometry selects the member).

Offline authoring tooling. No runtime, atlas, compiler, or shader surface reads it.

Usage:
    python3 scripts/glyph_seam_index.py --stats
    python3 scripts/glyph_seam_index.py --relation stacked --width 8 --stroke-like
    python3 scripts/glyph_seam_index.py --relation beside --width 16 --blocks "CJK Strokes" --keep 200
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

import numpy as np

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import glyph_cell_features as gcf  # noqa: E402
import glyph_cell_pairs as gcp  # noqa: E402
import glyph_combo_candidates as gcc  # noqa: E402

OUT_DIR = gcf.CACHE_DIR
STROKE_INK_CAP = {8: 40, 16: 80}
PAGE_SET = set(gcc.USEFUL_LINE_GLYPHS)
WHITELIST = set(gcc.SOURCE_ALNUM_GLYPHS)


def popcount(x: int) -> int:
    return bin(x).count("1")


def jaccard(a: int, b: int) -> float:
    u = popcount(a | b)
    return popcount(a & b) / u if u else 0.0


def is_alnum(cp: int) -> bool:
    return unicodedata.category(chr(cp))[0] in "LN"


def tags_for(cp: int) -> list[str]:
    ch = chr(cp)
    t = []
    if ch in PAGE_SET:
        t.append("page")
    if is_alnum(cp):
        t.append("whitelist" if ch in WHITELIST else "alnum")
    return t


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
def select(z: dict, meta: dict, width: int, stroke_like: bool, blocks: list[str] | None,
           exclude_alnum: bool) -> np.ndarray:
    m = z["native"] & (z["ink"] > 0) & (z["cell_w"] == width)
    if stroke_like:
        m &= (z["beta0"] <= 2) & (z["ink"] <= STROKE_INK_CAP[width])
    if blocks:
        names = meta["blocks"]
        want = {i for i, n in enumerate(names) if any(b.strip().lower() in n.lower() for b in blocks)}
        m &= np.isin(z["block"], list(want))
    idx = np.flatnonzero(m)
    if exclude_alnum:
        idx = np.asarray([i for i in idx if not is_alnum(int(z["cp"][i]))], dtype=idx.dtype)
    return idx


# ---------------------------------------------------------------------------
# Joins
# ---------------------------------------------------------------------------
def _sides(relation: str) -> tuple[str, str]:
    return ("bottom", "top") if relation == "stacked" else ("right", "left")


def join(z: dict, idx: np.ndarray, relation: str, kind: str, jacc_min: float,
         max_gap: int, per_glyph: int, rng: random.Random) -> list[tuple[int, int, float, int]]:
    """Return (ia, ib, jaccard, gap) for joined pairs. ``kind`` is 'port' or 'band'."""
    exit_side, entry_side = _sides(relation)
    key = "mask" if kind == "port" else "band"
    a_masks = z[f"{key}_{exit_side}"]
    b_masks = z[f"{key}_{entry_side}"]
    # gap along the relation axis, from ink extents (cells are 16 tall; width from cell_w)
    if relation == "stacked":
        a_end = 15 - z["ink_bottom"].astype(int)
        b_start = z["ink_top"].astype(int)
    else:
        a_end = (z["cell_w"].astype(int) - 1) - z["ink_right"].astype(int)
        b_start = z["ink_left"].astype(int)
    by_b: dict[int, list[int]] = defaultdict(list)
    for i in idx:
        mb = int(b_masks[i])
        if mb:
            by_b[mb].append(int(i))
    distinct_b = list(by_b)
    out: list[tuple[int, int, float, int]] = []
    jcache: dict[int, list[tuple[int, float]]] = {}
    for ia in idx:
        ma = int(a_masks[ia])
        if not ma:
            continue
        if ma not in jcache:
            jcache[ma] = [(mb, j) for mb in distinct_b if (j := jaccard(ma, mb)) >= jacc_min]
        cands: list[tuple[int, int, float, int]] = []
        for mb, j in jcache[ma]:
            for ib in by_b[mb]:
                gap = int(a_end[ia] + b_start[ib])
                if kind == "band" and gap > max_gap:
                    continue
                cands.append((int(ia), ib, j, gap))
        if per_glyph and len(cands) > per_glyph:
            cands.sort(key=lambda t: (-t[2], t[3]))
            head = cands[:per_glyph // 2]
            tail = rng.sample(cands[per_glyph // 2:], per_glyph - len(head))
            cands = head + tail
        out.extend(cands)
    return out


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
class Rasters:
    def __init__(self):
        self.scorer = gcf.GlyphScorer()
        self._g: dict[int, np.ndarray] = {}

    def grid(self, cp: int) -> np.ndarray:
        if cp not in self._g:
            self._g[cp] = gcf.cell_raster(self.scorer, cp).grid
        return self._g[cp]


def score_pairs(z: dict, meta: dict, pairs: list[tuple[int, int, float, int]], relation: str,
                width: int, kind: str, rasters: Rasters, progress: bool = True) -> list[dict]:
    rows = []
    t0 = time.perf_counter()
    for n, (ia, ib, j, gap) in enumerate(pairs):
        if progress and n and n % 2000 == 0:
            print(f"  scored {n}/{len(pairs)}  ({time.perf_counter() - t0:.0f}s)", flush=True)
        cpa, cpb = int(z["cp"][ia]), int(z["cp"][ib])
        ga, gb = rasters.grid(cpa), rasters.grid(cpb)
        p = gcp.pair_features(ga, gb, relation)
        comp = gcp.composite(ga, gb, relation)
        cols, rows_n = (1, 2) if relation == "stacked" else (2, 1)
        cells = ([{"col": 0, "row": 0, "cp": cpa, "char": chr(cpa)}, {"col": 0, "row": 1, "cp": cpb, "char": chr(cpb)}]
                 if relation == "stacked" else
                 [{"col": 0, "row": 0, "cp": cpa, "char": chr(cpa)}, {"col": 1, "row": 0, "cp": cpb, "char": chr(cpb)}])
        rows.append({
            "id": f"seam_{relation}_w{width}_{cpa:04X}_{cpb:04X}",
            "shape": f"discovered_{relation}_w{width}",
            "chars": chr(cpa) + chr(cpb), "mirror": "",
            "cols": cols, "rows_n": rows_n,
            "rows": ["".join("#" if v else "." for v in r) for r in comp],
            "lattice": [chr(cpa), chr(cpb)] if relation == "stacked" else [chr(cpa) + chr(cpb)],
            "mirror_lattice": ["(not derived)"],
            "dsm": None if p["continuity"]["dsm"] is None else round(p["continuity"]["dsm"], 4),
            "connected": p["seam"]["connected"],
            "port": round(p["port_overlap"], 4),
            "gaps": [p["seam"]["min"]],
            "gap_axis": gap,
            "join": kind, "join_jaccard": round(j, 3),
            "orientation": None if p["apparent_slope_deg"] is None else round(p["apparent_slope_deg"], 1),
            "topology_delta": p["topology"]["delta"],
            "distract": 0.0,
            "names": [unicodedata.name(chr(cpa), ""), unicodedata.name(chr(cpb), "")],
            "blocks": [meta["blocks"][int(z["block"][ia])], meta["blocks"][int(z["block"][ib])]],
            "tags": [tags_for(cpa), tags_for(cpb)],
            "cells": cells,
        })
    return rows


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
def count_join(z: dict, idx: np.ndarray, relation: str, kind: str, jacc_min: float, max_gap: int) -> int:
    """Size of join() without materialising it: bucket both sides by mask (and,
    for the band join, by distance-to-edge) and multiply bucket sizes."""
    exit_side, entry_side = _sides(relation)
    key = "mask" if kind == "port" else "band"
    a_masks, b_masks = z[f"{key}_{exit_side}"], z[f"{key}_{entry_side}"]
    if relation == "stacked":
        a_end, b_start = 15 - z["ink_bottom"].astype(int), z["ink_top"].astype(int)
    else:
        a_end = (z["cell_w"].astype(int) - 1) - z["ink_right"].astype(int)
        b_start = z["ink_left"].astype(int)
    A: dict[int, np.ndarray] = defaultdict(lambda: np.zeros(17, np.int64))
    B: dict[int, np.ndarray] = defaultdict(lambda: np.zeros(17, np.int64))
    for i in idx:
        if a_masks[i]:
            A[int(a_masks[i])][min(int(a_end[i]), 16)] += 1
        if b_masks[i]:
            B[int(b_masks[i])][min(int(b_start[i]), 16)] += 1
    total = 0
    for ma, ha in A.items():
        for mb, hb in B.items():
            if jaccard(ma, mb) < jacc_min:
                continue
            if kind == "port":
                total += int(ha.sum() * hb.sum())
            else:
                for ga in range(17):
                    if ha[ga]:
                        total += int(ha[ga] * hb[:max(0, max_gap - ga + 1)].sum())
    return total


def stats(z: dict, meta: dict, jacc_min: float, max_gap: int) -> None:
    print(f"cache: {meta['count']} codepoints, schema {meta['schema']}, {len(meta['blocks'])} blocks")
    nat = z["native"] & (z["ink"] > 0)
    print(f"native inked: {int(nat.sum())}")
    for w in (8, 16):
        for stroke in (False, True):
            idx = select(z, meta, w, stroke, None, False)
            for rel in ("stacked", "beside"):
                for kind in ("port", "band"):
                    n = count_join(z, idx, rel, kind, jacc_min, max_gap)
                    print(f"w={w:2} {'stroke-like' if stroke else 'all        '} {rel:7} {kind:4} "
                          f"glyphs={len(idx):6}  joined pairs={n:,}", flush=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stats", action="store_true", help="print join sizes and exit")
    ap.add_argument("--relation", choices=gcp.RELATIONS, default="stacked")
    ap.add_argument("--width", type=int, choices=(8, 16), default=8)
    ap.add_argument("--kind", choices=("port", "band", "both"), default="both")
    ap.add_argument("--stroke-like", action="store_true", help="beta0 <= 2 and low ink (line art, measured)")
    ap.add_argument("--blocks", type=str, default=None, help="comma-separated block-name substrings")
    ap.add_argument("--exclude-alnum", action="store_true")
    ap.add_argument("--jaccard", type=float, default=0.5)
    ap.add_argument("--max-gap", type=int, default=4, help="band join: max seam gap in px")
    ap.add_argument("--per-glyph", type=int, default=24, help="cap joined partners per A glyph (0 = none)")
    ap.add_argument("--max-pairs", type=int, default=20000, help="cap the scored set (random sample beyond)")
    ap.add_argument("--keep", type=int, default=300, help="rows written, best D_SM first")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)

    z, meta = gcf.load_cache()
    if int(meta.get("schema", 0)) < 2:
        raise SystemExit("cache schema < 2 (no edge bands / blocks); rebuild: glyph_cell_features.py --build")
    if a.stats:
        stats(z, meta, a.jaccard, a.max_gap)
        return 0
    rng = random.Random(a.seed)
    blocks = [b for b in a.blocks.split(",")] if a.blocks else None
    idx = select(z, meta, a.width, a.stroke_like, blocks, a.exclude_alnum)
    print(f"{len(idx)} glyphs selected (w={a.width}, stroke_like={a.stroke_like}, blocks={blocks})")
    kinds = ("port", "band") if a.kind == "both" else (a.kind,)
    pairs: dict[tuple[int, int], tuple[int, int, float, int, str]] = {}
    for kind in kinds:
        for ia, ib, j, gap in join(z, idx, a.relation, kind, a.jaccard, a.max_gap, a.per_glyph, rng):
            key = (ia, ib)
            if key not in pairs or pairs[key][2] < j:
                pairs[key] = (ia, ib, j, gap, kind)
    allp = list(pairs.values())
    print(f"{len(allp):,} joined pairs")
    if a.max_pairs and len(allp) > a.max_pairs:
        allp.sort(key=lambda t: (-t[2], t[3]))
        keep_head = allp[:a.max_pairs // 2]
        allp = keep_head + rng.sample(allp[a.max_pairs // 2:], a.max_pairs - len(keep_head))
        print(f"scoring {len(allp):,} (half best-join, half random sample)")
    rasters = Rasters()
    by_kind: dict[str, list] = defaultdict(list)
    for t in allp:
        by_kind[t[4]].append(t[:4])
    rows: list[dict] = []
    for kind, ps in by_kind.items():
        rows.extend(score_pairs(z, meta, ps, a.relation, a.width, kind, rasters))
    rows.sort(key=lambda r: (r["dsm"] is None, r["dsm"] or 0.0))
    out = OUT_DIR / f"seam_pairs_{a.relation}_w{a.width}.json"
    out.write_text(json.dumps({
        "schema": "fl4512.seam_pairs.v1", "relation": a.relation, "width": a.width,
        "selection": {"stroke_like": a.stroke_like, "blocks": blocks, "exclude_alnum": a.exclude_alnum,
                      "jaccard": a.jaccard, "max_gap": a.max_gap, "per_glyph": a.per_glyph,
                      "glyphs": int(len(idx)), "joined": len(pairs), "scored": len(rows)},
        "rows": rows[:a.keep],
    }, ensure_ascii=False))
    print(f"wrote {out}: {min(a.keep, len(rows))} of {len(rows)} scored rows")
    for r in rows[:15]:
        print(f"  {r['lattice']!s:14} D_SM {r['dsm']:<8} gap {r['gaps'][0]!s:4} port {r['port']:<5} "
              f"{r['join']:4} {r['blocks'][0]} / {r['blocks'][1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
