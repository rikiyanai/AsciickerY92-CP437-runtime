#!/usr/bin/env python3
"""Walk the seam graph for coherent glyph RUNS (2..N cells) over the whole repertoire.

A "useful combination" in the Stone Story sense is a short run of cells whose
seams are coherent AND whose per-cell property changes consistently along the
run. This walker generates such runs from MEASURED geometry only — no typed
alphabet, no usage prior — so discovery works the same for Box Drawing, CJK
Strokes, Hiragana, Arabic or Ogham as for ASCII punctuation. The author's mined
plates (glyph_combo_mine.py) are used ONLY as acceptance falsifiers: the walker
must rediscover the stair step, the rotation triple and the bracketed orb from
the graph; it never weights by them.

EDGE RULES (each produces (A, B) edges from cache-v2 buckets; stated for
beside runs — --relation stacked transposes every role: 'altitude' becomes
the column centroid (drift), 'short' means narrow, 'end' means left/right end)

    contact   A and B share ink rows and the extent gap (A's right margin +
              B's left margin) <= max_gap; edge-band Jaccard lowers the cost
                                          -> junctions (_|_), joined curves
    step      short marks (ink height <= step_h rows) whose altitude (ink row
              centroid) differs by <= step_dy rows; no contact needed
                                          -> slopes, stairs, caps, cups
    turn      both glyphs oriented, |dtheta| <= turn_max (axial), sharing rows
                                          -> rotation triples, arcs
    end       a short mark riding on the top or bottom END of a taller glyph
              without sharing rows          -> arches ( ‾ ), hats, feet
    repeat    A followed by A, always     -> straight runs (____ |||| ,,,,)

Each rule keeps its own top-K partners per node so no rule starves another.

PROFILE of a run = altitude sequence -> flat / rising / falling / cap / cup / wave
ORIENTATION of a run = per-cell dominant angle sequence; monotone -> rotation

SCORE (lower is better) = worst seam gap along the run (contact edges) plus
altitude and orientation roughness (second differences); ties broken by
shorter D_SM of the composite, computed only for kept runs.

EQUIVALENCE: runs with an identical composite raster collapse to one row with
a member list; runs with the same (profile, rounded altitude deltas,
orientation deltas) form a family, so ( ‾ ) and /¯\\ sit in one family with
different materials.

Offline authoring tooling; no runtime, atlas, compiler, or shader surface reads it.

Usage:
    python3 scripts/glyph_run_walker.py --width 8 --plate            # the 29+12 plate glyphs (acceptance)
    python3 scripts/glyph_run_walker.py --width 8 --line-like --exclude-alnum --length 4
    python3 scripts/glyph_run_walker.py --width 16 --blocks "CJK Strokes,Hiragana" --length 3
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
import glyph_combo_mine as gcm  # noqa: E402
import glyph_seam_index as gsi  # noqa: E402

OUT_DIR = gcf.CACHE_DIR
CELL_H = 16


class Axis:
    """Feature roles for one run direction.

    beside  : cells go left->right; 'along' = columns, 'cross' = rows;
              the coherence property is altitude (row centroid).
    stacked : cells go top->bottom; 'along' = rows, 'cross' = columns;
              the coherence property is the column centroid (drift).
    """

    def __init__(self, z, relation: str):
        self.relation = relation
        if relation == "beside":
            self.a0, self.a1 = z["ink_left"].astype(int), z["ink_right"].astype(int)     # along-axis extents
            self.along_len = z["cell_w"].astype(int)
            self.c0, self.c1 = z["ink_top"].astype(int), z["ink_bottom"].astype(int)     # cross-axis extents
            self.cen = z["cen_row"]
            self.band_exit, self.band_entry = z["band_right"], z["band_left"]
        elif relation == "stacked":
            self.a0, self.a1 = z["ink_top"].astype(int), z["ink_bottom"].astype(int)
            self.along_len = np.full(len(z["cp"]), CELL_H, dtype=int)
            self.c0, self.c1 = z["ink_left"].astype(int), z["ink_right"].astype(int)
            self.cen = z["cen_col"]
            self.band_exit, self.band_entry = z["band_bottom"], z["band_top"]
        else:
            raise ValueError(relation)
        self.dom = z["dom_deg"]
        self.cross_size = self.c1 - self.c0 + 1          # "height" for beside, "width" for stacked

    def profile_name(self, p: str) -> str:
        if self.relation == "beside":
            return p
        return {"flat": "straight", "rising": "drift-left", "falling": "drift-right",
                "cap": "bow-left", "cup": "bow-right", "wave": "wave", "point": "point"}.get(p, p)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
def select(z, meta, width, line_like, blocks, exclude_alnum, plate, chars: str | None = None) -> np.ndarray:
    if chars:
        m = np.isin(z["cp"], [ord(c) for c in chars]) & z["native"] & (z["ink"] > 0) & (z["cell_w"] == width)
        return np.flatnonzero(m)
    if plate:
        want = {ord(c) for c in gcc.USEFUL_LINE_GLYPHS} | {ord(c) for c in gcc.SOURCE_ALNUM_GLYPHS}
        m = np.isin(z["cp"], list(want)) & z["native"] & (z["ink"] > 0) & (z["cell_w"] == width)
        return np.flatnonzero(m)
    return gsi.select(z, meta, width, line_like, blocks, exclude_alnum)


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------
def _axial(d: float) -> float:
    d = abs(d) % 180.0
    return min(d, 180.0 - d)


def build_edges(z, idx: np.ndarray, *, relation: str = "beside", jacc: float, max_gap: int, step_h: int,
                step_dy: float, turn_max: float, per_node: int) -> dict[int, list[tuple[int, str, float]]]:
    """Return adjacency: a -> [(b, rule, cost)], ``per_node`` best per node PER RULE."""
    ax = Axis(z, relation)
    cen, dom = ax.cen, ax.dom
    a0, a1, along = ax.a0, ax.a1, ax.along_len
    c0, c1 = ax.c0, ax.c1
    size = ax.cross_size
    idx_list = [int(i) for i in idx]
    adj: dict[int, list[tuple[int, str, float]]] = defaultdict(list)

    def share_cross(a: int, b: int) -> bool:
        return min(c1[a], c1[b]) >= max(c0[a], c0[b])

    # contact: shared cross-axis extent + along-axis extent gap <= max_gap
    by_entry: dict[int, list[int]] = defaultdict(list)
    for i in idx_list:
        by_entry[int(a0[i])].append(i)
    for a in idx_list:
        ra = int(along[a] - 1 - a1[a])
        for lm in range(0, max_gap - ra + 1):
            for b in by_entry.get(lm, ()):
                if b == a or not share_cross(a, b):
                    continue
                gap = ra + lm
                j = (gsi.jaccard(int(ax.band_exit[a]), int(ax.band_entry[b]))
                     if ax.band_exit[a] and ax.band_entry[b] else 0.0)
                adj[a].append((b, "contact", gap + (1.0 - j)))

    # repeat: a glyph after itself is always a candidate run
    for a in idx_list:
        adj[a].append((a, "repeat", 0.5))

    # step: short (cross-axis) marks bucketed by rounded centroid
    short = [i for i in idx_list if size[i] <= step_h]
    by_alt: dict[int, list[int]] = defaultdict(list)
    for i in short:
        by_alt[int(round(float(cen[i])))].append(i)
    for a in short:
        ca = float(cen[a])
        for alt in range(int(round(ca - step_dy)), int(round(ca + step_dy)) + 1):
            for b in by_alt.get(alt, ()):
                dy = abs(float(cen[b]) - ca)
                if dy <= step_dy and b != a:
                    adj[a].append((b, "step", 0.5))       # any step in range; consistency is scored on the run

    # turn: oriented glyphs sharing cross extent, bucketed by 15-degree orientation bins
    oriented = [i for i in idx_list if not np.isnan(dom[i]) and size[i] >= 4]
    by_bin: dict[int, list[int]] = defaultdict(list)
    for i in oriented:
        by_bin[int(dom[i] // 15) % 12].append(i)
    span = int(np.ceil(turn_max / 15.0))
    for a in oriented:
        ba = int(dom[a] // 15) % 12
        for k in range(-span, span + 1):
            for b in by_bin.get((ba + k) % 12, ()):
                if b == a:
                    continue
                dth = _axial(float(dom[b]) - float(dom[a]))
                if dth > turn_max or not share_cross(a, b):
                    continue
                adj[a].append((b, "turn", 1.0 + dth / turn_max))

    # end: a short mark riding on the END (cross-axis) of a bigger glyph without sharing cross extent
    for a in idx_list:
        for b in idx_list:
            if a == b or share_cross(a, b):
                continue
            if size[a] <= step_h and size[b] <= step_h:
                continue
            t, sh = (a, b) if size[a] > step_h else (b, a)
            if size[t] <= step_h:
                continue
            dist = min(abs(int(c0[sh]) - int(c0[t])), abs(int(c1[sh]) - int(c1[t])),
                       abs(int(c0[sh]) - int(c1[t])), abs(int(c1[sh]) - int(c0[t])))
            if dist <= max_gap:
                adj[a].append((b, "end", 1.0 + dist / max(max_gap, 1)))

    for a in list(adj):
        best: dict[tuple[int, str], tuple[int, str, float]] = {}
        for b, rule, cost in adj[a]:
            k = (b, rule)
            if k not in best or cost < best[k][2]:
                best[k] = (b, rule, cost)
        kept: list[tuple[int, str, float]] = []
        for rule in ("contact", "step", "turn", "end", "repeat"):
            cands = sorted((t for t in best.values() if t[1] == rule), key=lambda t: t[2])
            buckets: dict[int, list] = defaultdict(list)
            for t in cands:
                if rule == "turn":
                    key = int(_axial(float(dom[t[0]]) - float(dom[a])) // 15)
                elif rule == "step":
                    key = int(round(float(cen[t[0]]) - float(cen[a])))
                else:
                    key = int(t[2])
                buckets[key].append(t)
            quota = max(1, per_node // max(len(buckets), 1))
            out = [t for b in sorted(buckets) for t in buckets[b][:quota]]
            if len(out) < per_node:
                seen = {(t[0], t[1]) for t in out}
                out += [t for t in cands if (t[0], t[1]) not in seen][:per_node - len(out)]
            kept.extend(out[:per_node] if rule != "repeat" else out)
        adj[a] = kept
    return adj


# ---------------------------------------------------------------------------
# Walk
# ---------------------------------------------------------------------------
def coherent(alts: list[float], oris: list[float | None], rules: list[str]) -> bool:
    """A run stays coherent while its altitude profile has at most one direction
    change (flat / slope / cap / cup) and, ACROSS TURN EDGES ONLY, orientation
    does not jump by more than 60 degrees. Dots and dashes have no reliable
    orientation, so orientation is not policed on step or contact edges."""
    if len(alts) >= 3:
        d = np.diff(alts)
        s = np.sign(np.where(np.abs(d) < 0.75, 0.0, d))
        nz = s[s != 0]
        if nz.size > 1 and int(np.sum(nz[1:] != nz[:-1])) > 1:
            return False
    for k, rule in enumerate(rules):
        if rule == "turn" and oris[k] is not None and oris[k + 1] is not None:
            if _axial(oris[k + 1] - oris[k]) > 60.0:
                return False
    return True


def walk(z, adj, starts: list[int], length: int, per_start: int,
         budget: int = 4000, relation: str = "beside") -> list[list[tuple[int, str, float]]]:
    """Breadth-first over coherent paths from each start, at most ``budget``
    expansions per start; keep the ``per_start`` best by mean edge cost, longer first."""
    ax = Axis(z, relation)
    cen, dom = ax.cen, ax.dom

    def alt(i: int) -> float:
        return float(cen[i])

    def ori(i: int) -> float | None:
        return None if np.isnan(dom[i]) else float(dom[i])

    runs: list[list[tuple[int, str, float]]] = []
    for s in starts:
        found: list[list[tuple[int, str, float]]] = []
        frontier = [[(s, "start", 0.0)]]
        expansions = 0
        while frontier and expansions < budget:
            nxt: list[list[tuple[int, str, float]]] = []
            for path in frontier:
                if len(path) >= length:
                    continue
                last = path[-1][0]
                for b, rule, cost in adj.get(last, ()):
                    expansions += 1
                    if b == last and rule != "repeat":
                        continue                    # A directly after A only as a repeat run
                    # a glyph may recur non-adjacently (_|_, |__|, (o) mirrors)
                    cand = path + [(b, rule, cost)]
                    alts = [alt(p[0]) for p in cand]
                    oris = [ori(p[0]) for p in cand]
                    if not coherent(alts, oris, [p[1] for p in cand[1:]]):
                        continue
                    found.append(cand)
                    nxt.append(cand)
                    if expansions >= budget:
                        break
                if expansions >= budget:
                    break
            frontier = nxt
        # Stratify the per-start quota over rule mixes so cheap step/repeat runs
        # do not crowd out turn, contact and end runs (\|/, (o), _|_, ( ‾ )).
        found.sort(key=lambda p: (sum(c for _, _, c in p[1:]) / max(len(p) - 1, 1), -len(p)))
        groups: dict[tuple, list] = defaultdict(list)
        for p in found:
            groups[tuple(sorted({r for _, r, _ in p[1:]}))].append(p)
        if per_start <= 0:
            runs.extend(found)                      # no cap: every coherent path
            continue
        quota = max(1, per_start // max(len(groups), 1))
        picked = [p for g in groups.values() for p in g[:quota]]
        if len(picked) < per_start:
            seen = {id(p) for p in picked}
            picked += [p for p in found if id(p) not in seen][:per_start - len(picked)]
        runs.extend(picked)
    return runs


# ---------------------------------------------------------------------------
# Scoring, collapsing, packing
# ---------------------------------------------------------------------------
def pack_run(z, meta, rasters: gsi.Rasters, path, width, with_dsm: bool = True,
             relation: str = "beside") -> dict | None:
    ax = Axis(z, relation)
    cps = [int(z["cp"][i]) for i, _, _ in path]
    grids = [rasters.grid(cp) for cp in cps]
    if any(g.shape[1] != width for g in grids):
        return None
    if relation == "beside":
        cells = [(i, 0, g) for i, g in enumerate(grids)]
        cols, rows_n = len(grids), 1
    else:
        cells = [(0, i, g) for i, g in enumerate(grids)]
        cols, rows_n = 1, len(grids)
    comp = gcp.grid_composite(cells, cols, rows_n)
    pairs = [gcp.pair_features(a, b, relation) for a, b in zip(grids, grids[1:])]
    axis_idx = 0 if relation == "beside" else 1
    alts = [float(np.nonzero(g)[axis_idx].mean()) for g in grids]
    oris = [gcf.dominant_orientation_deg(g) for g in grids]
    gaps = [p["seam"]["min"] for p in pairs]
    rough_alt = float(np.abs(np.diff(alts, n=2)).sum()) if len(alts) >= 3 else 0.0
    o = [x for x in oris if x is not None]
    rough_ori = float(sum(_axial(b - a) for a, b in zip(o, o[1:]))) / 90.0 if len(o) >= 2 else 0.0
    worst_gap = max([g for g in gaps if g is not None], default=0)
    cont = gcp.cross_cell_continuity(comp) if with_dsm else {"dsm": None}
    chars = "".join(chr(c) for c in cps)
    profile = ax.profile_name(gcm.profile_class(alts))
    tag = f"{relation}_w{width}"
    return {
        "id": f"run_{tag}_" + "_".join(f"{c:04X}" for c in cps),
        "shape": f"runs_{tag}",
        "relation": relation,
        "chars": chars, "mirror": "", "cols": cols, "rows_n": rows_n,
        "rows": ["".join("#" if v else "." for v in r) for r in comp],
        "lattice": [chars] if relation == "beside" else list(chars),
        "mirror_lattice": ["(not derived)"],
        "rules": [r for _, r, _ in path[1:]],
        "profile": profile,
        "altitude": [round(a, 2) for a in alts],
        "orientation": [None if x is None else round(x, 1) for x in oris],
        "gaps": gaps, "worst_gap": worst_gap,
        "score": round(worst_gap + rough_alt / 4.0 + rough_ori, 3),
        "dsm": None if cont["dsm"] is None else round(cont["dsm"], 3),
        "connected": all(p["seam"]["connected"] for p in pairs),
        "port": None, "distract": 0.0,
        "beta": list(gcf.topology(comp)),
        "names": [unicodedata.name(chr(c), "") for c in cps],
        "blocks": [meta["blocks"][int(z["block"][i])] for i, _, _ in path],
        "tags": [gsi.tags_for(c) for c in cps],
        "cells": ([{"col": i, "row": 0, "cp": c, "char": chr(c)} for i, c in enumerate(cps)] if relation == "beside"
                  else [{"col": 0, "row": i, "cp": c, "char": chr(c)} for i, c in enumerate(cps)]),
        "family": None, "members": [],
    }


def collapse(rows: list[dict]) -> list[dict]:
    """Identical composite -> one row with members; then family signature."""
    by_comp: dict[str, dict] = {}
    for r in rows:
        key = hashlib.sha1("\n".join(r["rows"]).encode()).hexdigest()
        if key in by_comp:
            by_comp[key]["members"].append({"chars": r["chars"], "count": 1})
            continue
        r["members"] = [{"chars": r["chars"], "count": 1}]
        by_comp[key] = r
    out = list(by_comp.values())
    for r in out:
        d_alt = tuple(int(round(b - a)) for a, b in zip(r["altitude"], r["altitude"][1:]))
        o = [x for x in r["orientation"] if x is not None]
        d_ori = tuple(int(round(_axial(b - a) / 30.0)) for a, b in zip(o, o[1:]))
        r["family"] = f"{r['profile']}|alt{d_alt}|ori{d_ori}|n{len(r['altitude'])}"
    out.sort(key=lambda r: (r["score"], r["dsm"] if r["dsm"] is not None else 99.0, -len(r["altitude"])))
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def run_walker(a) -> list[dict]:
    z, meta = gcf.load_cache()
    if int(meta.get("schema", 0)) < 2:
        raise SystemExit("cache schema < 2; rebuild: glyph_cell_features.py --build")
    blocks = [b for b in a.blocks.split(",")] if a.blocks else None
    idx = select(z, meta, a.width, a.line_like, blocks, a.exclude_alnum, a.plate, a.chars)
    t0 = time.perf_counter()
    adj = build_edges(z, idx, relation=a.relation, jacc=a.jaccard, max_gap=a.max_gap, step_h=a.step_h,
                      step_dy=a.step_dy, turn_max=a.turn_max, per_node=a.per_node)
    n_edges = sum(len(v) for v in adj.values())
    print(f"{len(idx)} glyphs, {n_edges:,} edges ({time.perf_counter() - t0:.1f}s)", flush=True)
    paths = walk(z, adj, [int(i) for i in idx], a.length, a.per_start, a.budget, a.relation)
    print(f"{len(paths):,} coherent paths of length 2..{a.length}", flush=True)
    rasters = gsi.Rasters()
    rows = []
    t0 = time.perf_counter()
    for n, p in enumerate(paths[:a.max_score]):
        r = pack_run(z, meta, rasters, p, a.width, with_dsm=not a.no_dsm, relation=a.relation)
        if r:
            rows.append(r)
        if n and n % 5000 == 0:
            print(f"  scored {n}/{min(len(paths), a.max_score)} ({time.perf_counter() - t0:.0f}s)", flush=True)
    rows = collapse(rows)
    print(f"{len(rows):,} distinct composites after collapse")
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--width", type=int, choices=(8, 16), default=8)
    ap.add_argument("--relation", choices=("beside", "stacked"), default="beside",
                    help="beside = horizontal runs (altitude profile); stacked = vertical runs (drift profile)")
    ap.add_argument("--plate", action="store_true", help="the plate alphabet + whitelist only (acceptance run)")
    ap.add_argument("--chars", type=str, default=None, help="explicit glyph set (overrides every other selection)")
    ap.add_argument("--line-like", action="store_true")
    ap.add_argument("--blocks", type=str, default=None)
    ap.add_argument("--exclude-alnum", action="store_true")
    ap.add_argument("--length", type=int, default=4, help="max run length in cells")
    ap.add_argument("--jaccard", type=float, default=0.5)
    ap.add_argument("--max-gap", type=int, default=4)
    ap.add_argument("--step-h", type=int, default=5, help="step rule: max ink height (rows) of a short mark")
    ap.add_argument("--step-dy", type=float, default=8.0, help="step rule: max altitude change per cell (rows)")
    ap.add_argument("--turn-max", type=float, default=45.0, help="turn rule: max orientation change (deg)")
    ap.add_argument("--per-node", type=int, default=24, help="partners kept per node PER RULE, spread over cost buckets")
    ap.add_argument("--per-start", type=int, default=16, help="paths kept per start node (0 = all)")
    ap.add_argument("--no-dsm", action="store_true", help="skip the composite D_SM (faster; presence checks)")
    ap.add_argument("--budget", type=int, default=200000, help="edge expansions per start node")
    ap.add_argument("--max-score", type=int, default=40000)
    ap.add_argument("--keep", type=int, default=400)
    ap.add_argument("--json", action="store_true", help="write .run/glyph_audit/runs_<relation>_w<width>[_<tag>].json")
    ap.add_argument("--tag", type=str, default="")
    ap.add_argument("--limit", type=int, default=40)
    a = ap.parse_args(argv)
    rows = run_walker(a)
    for r in rows[:a.limit]:
        print(f"  {r['chars']!r:12} {r['profile']:8} score {r['score']:<6} gaps {r['gaps']!s:14} "
              f"ori {r['orientation']!s:28} rules {'/'.join(r['rules'])}  x{len(r['members'])}  {' / '.join(dict.fromkeys(r['blocks']))}")
    if a.json:
        out = OUT_DIR / f"runs_{a.relation}_w{a.width}{('_' + a.tag) if a.tag else ''}.json"
        out.write_text(json.dumps({"schema": "fl4512.glyph_runs.v1", "width": a.width, "args": vars(a),
                                   "rows": rows[:a.keep]}, ensure_ascii=False))
        print(f"wrote {out}: {min(a.keep, len(rows))} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
