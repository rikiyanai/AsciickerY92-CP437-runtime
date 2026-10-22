#!/usr/bin/env python3
"""Enumerate and MEASURE 2- and 3-cell glyph combinations for authoring review.

This is an offline FL-4512 authoring tool. It does not read or write atlas,
compiler, shader, renderer, or runtime state.

WHAT CHANGED (FL-4512, measured-feature task)
---------------------------------------------
The first version enumerated ``itertools.product`` over a hand-typed tuple and
filtered by a distraction ceiling; every candidate was otherwise unmeasured.
This version keeps the exhaustive search space (every connected 2- and 3-cell
arrangement over the source alphabet) but ADMITS AND RANKS candidates by
measured features of the composite raster:

    per adjacent pair       seam gap min / connected, port_overlap, topology
                            delta, cross_cell_continuity (xu-2017 Eq. 19 DSM
                            against a straight-stroke reference)
    per candidate           continuity_mean (lower = reads more as one stroke),
                            seam_connected_all, distract_max (an ATTRIBUTE for
                            ranking, no longer a default filter)

Feature owners: scripts/glyph_cell_features.py (per glyph, as-positioned
raster) and scripts/glyph_cell_pairs.py (composite). Method owner:
docs/audits/2026-09-08-glyph-feature-vocabulary-fl4512-corpus-audit.md §3.

ALPHABET
--------
The alphabet is the author's written plate (Stone Story tutorial page §5,
archived in articles/2026-09-07-stone-story-video-transcripts-media/): 25
basic glyphs, 4 extended glyphs, and a 12-glyph alphanumeric whitelist of
which ``U c C`` are borderline. The plate outranks the auto-caption reading.

Usage:
    python3 scripts/glyph_combo_candidates.py --no-cache --limit 20            # enumerate only
    python3 scripts/glyph_combo_candidates.py --measure --max-size 2 --limit 30 # measured, ranked
    python3 scripts/glyph_combo_candidates.py --measure --connected-only --json
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import unicodedata
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import glyph_audit as ga  # noqa: E402

REPO_ROOT = SCRIPTS_DIR.parent

# Stone Story tutorial page §5 plate (PAGE outranks caption; see ascii-art-authoring skill §4.1).
PAGE_BASIC_GLYPHS = "`~!^()-_+=;:'\",.\\/|<>[]{}"          # 25
PAGE_EXTENDED_GLYPHS = "´‾¡·"           # ´ ‾ ¡ ·  (4)
USEFUL_LINE_GLYPHS = tuple(dict.fromkeys(PAGE_BASIC_GLYPHS + PAGE_EXTENDED_GLYPHS))   # 29

# The plate's alphanumeric whitelist (12). Three-valued: whitelisted, borderline, banned.
SOURCE_ALNUM_GLYPHS = tuple("oOvVTL7UcCxX")
SOURCE_ALNUM_MARGINAL = frozenset("UcC")

SHAPES = {
    2: {
        "domino_h": ((0, 0), (1, 0)),
        "domino_v": ((0, 0), (0, 1)),
    },
    3: {
        "line_h": ((0, 0), (1, 0), (2, 0)),
        "line_v": ((0, 0), (0, 1), (0, 2)),
        "corner_ne": ((0, 1), (0, 0), (1, 0)),
        "corner_se": ((0, 0), (0, 1), (1, 1)),
        "corner_sw": ((1, 0), (1, 1), (0, 1)),
        "corner_nw": ((1, 1), (1, 0), (0, 0)),
    },
}

# Mirror partners when no measured corpus is available. ``´`` mirrors the
# backtick (skill §4.2); the apostrophe is its own mirror.
FALLBACK_MIRRORS = {
    "/": "\\", "\\": "/",
    "(": ")", ")": "(",
    "<": ">", ">": "<",
    "[": "]", "]": "[",
    "{": "}", "}": "{",
    "`": "´", "´": "`",
}


def _dims(offsets: tuple[tuple[int, int], ...]) -> tuple[int, int]:
    return max(c for c, _r in offsets) + 1, max(r for _c, r in offsets) + 1


def _mirror_map(c: ga.Corpus | None, glyphs: tuple[str, ...]) -> dict[str, str]:
    mirrors = {ch: FALLBACK_MIRRORS.get(ch, ch) for ch in glyphs}
    if c is None:
        return mirrors
    cp_to_ch = {ord(ch): ch for ch in glyphs}
    for a, b in ga.find_mirror(c):
        acp, bcp = int(c.cps[a]), int(c.cps[b])
        if acp in cp_to_ch and bcp in cp_to_ch:
            mirrors[cp_to_ch[acp]] = cp_to_ch[bcp]
            mirrors[cp_to_ch[bcp]] = cp_to_ch[acp]
    return mirrors


def _alnum_term(ch: str) -> str:
    if not ch.isalnum():
        return "none"
    if ch in SOURCE_ALNUM_MARGINAL:
        return "borderline"
    if ch in SOURCE_ALNUM_GLYPHS:
        return "whitelisted"
    return "banned"


def _metrics(c: ga.Corpus | None, glyphs: tuple[str, ...]) -> dict[str, dict]:
    if c is None:
        return {ch: {"renderable": True, "weight": 0.0, "alnum": ch.isalnum(),
                     "alnum_term": _alnum_term(ch)} for ch in glyphs}
    out: dict[str, dict] = {}
    for ch in glyphs:
        i = c.i(ord(ch))
        if i is None:
            out[ch] = {"renderable": False, "weight": 1.0, "alnum": ch.isalnum(),
                       "alnum_term": _alnum_term(ch)}
            continue
        row = ga.distraction_row(c, i)
        out[ch] = {
            "renderable": True,
            "weight": round(float(row["weight"]), 4),
            "alnum": bool(row["alnum"]),
            "alnum_term": _alnum_term(ch),
        }
    return out


def _adjacent_pairs(offsets: tuple[tuple[int, int], ...]) -> list[tuple[int, int, str]]:
    """(index_a, index_b, relation) for every horizontally/vertically adjacent cell pair."""
    pos = {o: i for i, o in enumerate(offsets)}
    out = []
    for (c, r), i in pos.items():
        if (c + 1, r) in pos:
            out.append((i, pos[(c + 1, r)], "beside"))
        if (c, r + 1) in pos:
            out.append((i, pos[(c, r + 1)], "stacked"))
    return out


def enumerate_candidates(
    *,
    c: ga.Corpus | None = None,
    max_size: int = 3,
    include_source_alnum: bool = False,
    max_distraction: float | None = None,
) -> list[dict]:
    """Return every connected 2- or 3-cell ordered glyph arrangement in scope.

    ``max_distraction`` is None by default: distraction is carried as an
    attribute (``distract_max``) for ranking, not applied as a filter.
    """
    glyphs = USEFUL_LINE_GLYPHS + (SOURCE_ALNUM_GLYPHS if include_source_alnum else ())
    glyphs = tuple(dict.fromkeys(glyphs))
    metrics = _metrics(c, glyphs)
    mirrors = _mirror_map(c, glyphs)

    rows: list[dict] = []
    for size in range(2, max_size + 1):
        for shape_id, offsets in SHAPES.get(size, {}).items():
            cols, rows_count = _dims(offsets)
            adjacency = _adjacent_pairs(offsets)
            for chars in itertools.product(glyphs, repeat=size):
                cell_metrics = [metrics[ch] for ch in chars]
                if not all(m["renderable"] for m in cell_metrics):
                    continue
                if any(m["alnum"] for m in cell_metrics) and not include_source_alnum:
                    continue
                distract_max = max(m["weight"] for m in cell_metrics)
                if max_distraction is not None and distract_max > max_distraction:
                    continue
                cells = [
                    {"col": col, "row": row, "cp": ord(ch), "char": ch}
                    for (col, row), ch in zip(offsets, chars)
                ]
                mirror_cells = [
                    {
                        "col": cols - 1 - col,
                        "row": row,
                        "cp": ord(mirrors[ch]),
                        "char": mirrors[ch],
                    }
                    for (col, row), ch in zip(offsets, chars)
                ]
                mirror_cells.sort(key=lambda d: (d["row"], d["col"]))
                cps = "_".join(f"{ord(ch):04X}" for ch in chars)
                rows.append({
                    "id": f"auto_{shape_id}_{cps}",
                    "shape": shape_id,
                    "cols": cols,
                    "rows": rows_count,
                    "chars": "".join(chars),
                    "cells": cells,
                    "adjacency": [[a, b, rel] for a, b, rel in adjacency],
                    "mirror_chars": "".join(d["char"] for d in mirror_cells),
                    "mirror": {"method": "horizontal", "cells": mirror_cells},
                    "distract_max": round(distract_max, 4),
                    "alnum_terms": [m["alnum_term"] for m in cell_metrics],
                    "names": [unicodedata.name(ch, "") for ch in chars],
                })
    return rows


# ---------------------------------------------------------------------------
# Measurement (glyph_cell_features + glyph_cell_pairs)
# ---------------------------------------------------------------------------
class PairMeasurer:
    """Caches per-glyph cell rasters and per-(A, B, relation) pair features."""

    def __init__(self, scorer=None):
        import glyph_cell_features as gcf
        import glyph_cell_pairs as gcp
        self.gcf, self.gcp = gcf, gcp
        self.scorer = scorer or gcf.GlyphScorer()
        self._raster: dict[int, object] = {}
        self._pair: dict[tuple[int, int, str], dict] = {}

    def raster(self, cp: int):
        if cp not in self._raster:
            self._raster[cp] = self.gcf.cell_raster(self.scorer, cp)
        return self._raster[cp]

    def pair(self, cp_a: int, cp_b: int, relation: str) -> dict:
        key = (cp_a, cp_b, relation)
        if key not in self._pair:
            ra, rb = self.raster(cp_a), self.raster(cp_b)
            if ra.grid.shape != rb.grid.shape:
                self._pair[key] = {"relation": relation, "unmeasurable": "cell size differs"}
            else:
                p = self.gcp.pair_features(ra.grid, rb.grid, relation)
                self._pair[key] = {
                    "relation": relation,
                    "position_source": [ra.position_source, rb.position_source],
                    "seam_min": p["seam"]["min"],
                    "seam_connected": p["seam"]["connected"],
                    "port_overlap": round(p["port_overlap"], 4),
                    "topology_delta": p["topology"]["delta"],
                    "apparent_slope_deg": (None if p["apparent_slope_deg"] is None
                                           else round(p["apparent_slope_deg"], 1)),
                    "continuity_dsm": (None if p["continuity"]["dsm"] is None
                                       else round(p["continuity"]["dsm"], 4)),
                }
        return self._pair[key]


def measure_candidates(rows: list[dict], measurer: PairMeasurer) -> list[dict]:
    """Attach measured pair features and candidate-level summaries in place."""
    for row in rows:
        cps = [c["cp"] for c in row["cells"]]
        pairs = []
        for a, b, rel in row["adjacency"]:
            p = dict(measurer.pair(cps[a], cps[b], rel))
            p["cells"] = [a, b]
            pairs.append(p)
        row["pairs"] = pairs
        dsms = [p["continuity_dsm"] for p in pairs if p.get("continuity_dsm") is not None]
        row["continuity_mean"] = round(sum(dsms) / len(dsms), 4) if dsms else None
        row["seam_connected_all"] = bool(pairs) and all(p.get("seam_connected") for p in pairs)
        row["seam_connected_any"] = any(p.get("seam_connected") for p in pairs)
        row["port_overlap_mean"] = (round(sum(p.get("port_overlap", 0.0) for p in pairs) / len(pairs), 4)
                                    if pairs else None)
    return rows


RANK_KEYS = {
    "continuity": lambda r: (r.get("continuity_mean") is None, r.get("continuity_mean") or 0.0),
    "port": lambda r: -(r.get("port_overlap_mean") or 0.0),
    "distract": lambda r: r.get("distract_max", 0.0),
}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--max-size", type=int, default=3, choices=[2, 3])
    p.add_argument("--include-source-alnum", action="store_true")
    p.add_argument("--max-distraction", type=float, default=None,
                   help="optional ceiling; by default distraction is an attribute, not a filter")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--json", action="store_true",
                   help="write .run/glyph_audit/glyph_combo_candidates.json")
    p.add_argument("--no-cache", action="store_true",
                   help="skip renderability/distraction checks and enumerate by source alphabet only")
    p.add_argument("--measure", action="store_true",
                   help="measure seam/continuity features on the composite raster (needs scipy, scikit-image)")
    p.add_argument("--rank", choices=sorted(RANK_KEYS), default="continuity",
                   help="ordering when --measure is given")
    p.add_argument("--connected-only", action="store_true",
                   help="with --measure: keep only candidates whose every seam has a 0 px gap")
    p.add_argument("--max-dsm", type=float, default=None,
                   help="with --measure: keep only candidates whose continuity_mean is at or under this")
    args = p.parse_args(argv)

    c = None
    if not args.no_cache:
        try:
            c = ga.Corpus()
        except SystemExit as exc:
            sys.stderr.write(f"cache unavailable; rerun with --no-cache or build glyph_features: {exc}\n")
            return 2

    rows = enumerate_candidates(
        c=c,
        max_size=args.max_size,
        include_source_alnum=args.include_source_alnum,
        max_distraction=args.max_distraction,
    )
    if args.measure:
        measure_candidates(rows, PairMeasurer())
        if args.connected_only:
            rows = [r for r in rows if r["seam_connected_all"]]
        if args.max_dsm is not None:
            rows = [r for r in rows if r["continuity_mean"] is not None and r["continuity_mean"] <= args.max_dsm]
        rows.sort(key=RANK_KEYS[args.rank])

    by_shape: dict[str, int] = {}
    for row in rows:
        by_shape[row["shape"]] = by_shape.get(row["shape"], 0) + 1

    print(f"{len(rows)} glyph-combination candidates" + (" (measured)" if args.measure else ""))
    print("shapes: " + ", ".join(f"{k}:{by_shape[k]}" for k in sorted(by_shape)))
    for row in rows[:args.limit]:
        line = (f"{row['id']}  {row['chars']!r}  mirror {row['mirror_chars']!r}  "
                f"shape={row['shape']}  distract={row['distract_max']:.4f}")
        if args.measure:
            line += (f"  dsm={row['continuity_mean']}  connected={row['seam_connected_all']}  "
                     f"port={row['port_overlap_mean']}")
        print(line)

    if args.json:
        out = REPO_ROOT / ".run" / "glyph_audit" / "glyph_combo_candidates.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "schema": "fl4512.glyph_combo_candidates.v2",
            "source": "Stone Story tutorial page §5 plate, archived 2026-09-07",
            "max_size": args.max_size,
            "include_source_alnum": args.include_source_alnum,
            "max_distraction": args.max_distraction,
            "measured": bool(args.measure),
            "rank": args.rank if args.measure else None,
            "count": len(rows),
            "candidates": rows,
        }, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
