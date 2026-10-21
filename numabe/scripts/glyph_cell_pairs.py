#!/usr/bin/env python3
"""
glyph_cell_pairs.py — pairwise / composite-raster features for glyph runs.

This is the second slice of the FL-4512 measured-feature task. The first slice
(scripts/glyph_cell_features.py) measures one glyph in its cell. This module
places two (or more) cells at their real offsets, builds the COMPOSITE raster,
and measures the seam — which is the only place the near-vertical
anti-aliasing question of the Stone Story tutorials can be answered
(``|`` over ``|`` vs ``!`` over ``|``: does the run read as one stroke?).

Method owner: docs/audits/2026-09-08-glyph-feature-vocabulary-fl4512-corpus-audit.md
§3.3 (pairwise), §3.4 (emptiness), §3.6 (acceptance). Step numbers refer to it.
Corpus owners:

    composite scoring       xu-2017 §5 Eq. 19: DSM(S,S') = (D(S,S') + D(S',S)) / 2,
                            D(S,S') = sum_p min_{q in S' ∩ Λ(p)} sqrt(e_p² + e'_q² −
                            2 e_p e'_q cos(o_p − o'_q)), Λ(p) = {q : ||p−q|| < r}, r = 5
    structure map (e, o)    xu-2017 Eq. 14 uses a Gabor + non-CRF map; HERE it is
                            approximated by Scharr magnitude + the chung-2022
                            orientation field of glyph_cell_features (recorded
                            approximation; see structure_map()).
    port overlap            chen-2024 border incidence, Jaccard over side occupancy;
                            pre-filter only (step 40), never the score.
    topology delta          chen-2024 (beta0, beta1) on the composite vs the parts.
    emptiness as topology   chen-2024: topology preservation is global (steps 48-50).

DEFAULT REFERENCE for cross_cell_continuity (step 38/39): the spec compares the
composite with "the corresponding region of the reference structure raster".
When no reference is supplied this module synthesises one: a one-pixel straight
stroke through the composite's ink centroid at the composite's dominant
orientation. The score then answers "how far is this run from reading as one
straight stroke". Pass ``reference=`` to score against anything else.

Offline authoring tooling only. No runtime, atlas, compiler, or shader surface
reads this module.

Usage:
    python3 scripts/glyph_cell_pairs.py --stack '|' '|'        # B below A
    python3 scripts/glyph_cell_pairs.py --stack '!' '|' --raster
    python3 scripts/glyph_cell_pairs.py --beside '_' '.'
    python3 scripts/glyph_cell_pairs.py --combos                # the 9 authored combos
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage
from skimage.filters import scharr_h, scharr_v

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import glyph_cell_features as gcf  # noqa: E402
from glyph_morphology_browser import GlyphScorer  # noqa: E402

REPO_ROOT = SCRIPTS_DIR.parent
COMBOS_JSON = REPO_ROOT / "assets" / "glyphs" / "authored" / "glyph_combinations.v1.json"

DSM_RADIUS = 5.0            # xu-2017: r = 5 "in all our experiments"
RELATIONS = ("stacked", "beside")


# ---------------------------------------------------------------------------
# Composite raster (step 37)
# ---------------------------------------------------------------------------
def composite(ga: np.ndarray, gb: np.ndarray, relation: str) -> np.ndarray:
    """Place B below A (``stacked``) or right of A (``beside``) at real offsets."""
    if relation == "stacked":
        if ga.shape[1] != gb.shape[1]:
            raise ValueError("stacked cells must share cell_w")
        return np.vstack([ga, gb]).astype(np.uint8)
    if relation == "beside":
        if ga.shape[0] != gb.shape[0]:
            raise ValueError("beside cells must share cell_h")
        return np.hstack([ga, gb]).astype(np.uint8)
    raise ValueError(f"relation must be one of {RELATIONS}: {relation!r}")


def grid_composite(cells: list[tuple[int, int, np.ndarray]], cols: int, rows: int) -> np.ndarray:
    """Place (col, row, grid) cells on a cols x rows lattice of one cell size."""
    h, w = cells[0][2].shape
    out = np.zeros((rows * h, cols * w), dtype=np.uint8)
    for c, r, g in cells:
        if g.shape != (h, w):
            raise ValueError("all cells must share one cell size")
        out[r * h:(r + 1) * h, c * w:(c + 1) * w] = g
    return out


# ---------------------------------------------------------------------------
# Seam features (steps 40-43)
# ---------------------------------------------------------------------------
def port_overlap(ga: np.ndarray, gb: np.ndarray, relation: str) -> float:
    """Step 40. Jaccard of A's exit-side occupancy with B's entry-side occupancy.
    Pre-filter only: 0.0 means the border pixels do not line up, nothing more."""
    oa = gcf.contact_occupancy(ga)
    ob = gcf.contact_occupancy(gb)
    a, b = (oa["bottom"], ob["top"]) if relation == "stacked" else (oa["right"], ob["left"])
    a_ = np.asarray(a, dtype=bool)
    b_ = np.asarray(b, dtype=bool)
    union = int((a_ | b_).sum())
    return float((a_ & b_).sum() / union) if union else 0.0


def seam_gap_profile(ga: np.ndarray, gb: np.ndarray, relation: str) -> list[int | None]:
    """Steps 41-42. Per column (stacked) / per row (beside): the number of EMPTY
    pixels between A's last ink and B's first ink along that line. ``None`` where
    either side has no ink on that line. Reported as min/median/max by
    seam_summary(), never as one additive scalar."""
    if relation == "beside":
        ga, gb = ga.T, gb.T
    h = ga.shape[0]
    out: list[int | None] = []
    for u in range(ga.shape[1]):
        ca = np.flatnonzero(ga[:, u])
        cb = np.flatnonzero(gb[:, u])
        if ca.size == 0 or cb.size == 0:
            out.append(None)
            continue
        out.append(int((h - 1 - ca[-1]) + cb[0]))
    return out


def seam_summary(profile: list[int | None]) -> dict:
    vals = [v for v in profile if v is not None]
    if not vals:
        return {"min": None, "median": None, "max": None, "lines_with_ink": 0, "connected": False}
    return {
        "min": int(min(vals)),
        "median": float(np.median(vals)),
        "max": int(max(vals)),
        "lines_with_ink": len(vals),
        "connected": bool(min(vals) == 0),      # step 43
    }


# ---------------------------------------------------------------------------
# Structure map and DSM (xu-2017 Eq. 19)
# ---------------------------------------------------------------------------
def structure_map(grid: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (mask, e, o): foreground mask, edge magnitude e(p), orientation o(p).

    APPROXIMATION (recorded): xu-2017 Eq. 14 builds the structure map from a
    Gabor + non-CRF inhibition model. Here e is the Scharr gradient magnitude of
    the sigma-0.7-smoothed raster restricted to ink pixels, normalised to
    max 1, and o is the chung-2022 stroke orientation field in [0, pi).
    """
    f = ndimage.gaussian_filter(grid.astype(np.float64), gcf.ORIENT_SIGMA, truncate=1.0, mode="constant")
    e = np.hypot(scharr_v(f), scharr_h(f))
    mask = grid.astype(bool)
    e = np.where(mask, e, 0.0)
    if e.max() > 0:
        e = e / e.max()
    o = gcf.orientation_field(grid)
    return mask, e, o


def _directed_polar_distance(pa: np.ndarray, ea: np.ndarray, oa: np.ndarray,
                             pb: np.ndarray, eb: np.ndarray, ob: np.ndarray,
                             r: float) -> float:
    """D(S, S') of Eq. 19. A point with no partner inside Λ(p) pays its own
    magnitude e(p) (the cost against an empty q). Orientation difference is
    taken axially so o and o+pi are the same stroke direction."""
    if pa.shape[0] == 0:
        return 0.0
    if pb.shape[0] == 0:
        return float(ea.sum())
    total = 0.0
    for i in range(pa.shape[0]):
        d = np.hypot(pb[:, 0] - pa[i, 0], pb[:, 1] - pa[i, 1])
        near = d < r
        if not near.any():
            total += float(ea[i])
            continue
        dtheta = np.abs(oa[i] - ob[near])
        dtheta = np.minimum(dtheta, math.pi - dtheta)
        cost = np.sqrt(np.maximum(ea[i] ** 2 + eb[near] ** 2 - 2.0 * ea[i] * eb[near] * np.cos(dtheta), 0.0))
        total += float(cost.min())
    return total


def dsm(sa: np.ndarray, sb: np.ndarray, r: float = DSM_RADIUS) -> dict:
    """Step 39. Bidirectional DSM between two rasters of equal shape.
    Returns the raw sum (the paper's quantity) and a per-pixel normalisation."""
    if sa.shape != sb.shape:
        raise ValueError(f"DSM needs equal shapes: {sa.shape} vs {sb.shape}")
    ma, ea, oa = structure_map(sa)
    mb, eb, ob = structure_map(sb)
    pa = np.argwhere(ma).astype(np.float64)
    pb = np.argwhere(mb).astype(np.float64)
    d_ab = _directed_polar_distance(pa, ea[ma], oa[ma], pb, eb[mb], ob[mb], r)
    d_ba = _directed_polar_distance(pb, eb[mb], ob[mb], pa, ea[ma], oa[ma], r)
    total = 0.5 * (d_ab + d_ba)
    n = max(int(ma.sum() + mb.sum()), 1)
    return {"dsm": float(total), "d_ab": float(d_ab), "d_ba": float(d_ba),
            "dsm_per_pixel": float(2.0 * total / n), "r": r}


# ---------------------------------------------------------------------------
# Reference stroke and cross-cell continuity (steps 38-39, 44, 46)
# ---------------------------------------------------------------------------
def straight_stroke(shape: tuple[int, int], centroid: tuple[float, float], angle_deg: float) -> np.ndarray:
    """One-pixel straight stroke through ``centroid`` (row, col) at ``angle_deg``
    (0 = horizontal, 90 = vertical, screen coords) spanning the whole raster."""
    h, w = shape
    out = np.zeros((h, w), dtype=np.uint8)
    a = math.radians(angle_deg)
    dx, dy = math.cos(a), math.sin(a)
    if abs(dx) < 1e-9:
        dx = 0.0
    if abs(dy) < 1e-9:
        dy = 0.0
    cr, cc = centroid

    def snap(x: float) -> int:                  # half-up, not banker's rounding
        return int(math.floor(x + 0.5))

    if abs(dy) >= abs(dx):                      # step in rows
        for row in range(h):
            col = snap(cc + (row - cr) * dx / dy)
            if 0 <= col < w:
                out[row, col] = 1
    else:                                       # step in cols
        for col in range(w):
            row = snap(cr + (col - cc) * dy / dx)
            if 0 <= row < h:
                out[row, col] = 1
    return out


def apparent_slope(comp: np.ndarray) -> float | None:
    """Step 44. Dominant orientation of the COMPOSITE's orientation field, degrees."""
    return gcf.dominant_orientation_deg(comp)


def default_reference(comp: np.ndarray) -> np.ndarray | None:
    ys, xs = np.nonzero(comp)
    if ys.size == 0:
        return None
    ang = apparent_slope(comp)
    return straight_stroke(comp.shape, (float(ys.mean()), float(xs.mean())), ang if ang is not None else 90.0)


def cross_cell_continuity(comp: np.ndarray, reference: np.ndarray | None = None,
                          r: float = DSM_RADIUS) -> dict:
    """Steps 38-39. DSM between the composite and a reference structure raster.
    Lower is more continuous. ``reference_source`` records which reference was used."""
    src = "supplied"
    if reference is None:
        reference = default_reference(comp)
        src = "synthetic_straight_stroke"
        if reference is None:
            return {"dsm": None, "reference_source": src}
    out = dsm(comp, reference, r)
    out["reference_source"] = src
    return out


def topology_delta(ga: np.ndarray, gb: np.ndarray, comp: np.ndarray) -> dict:
    """Step 46. (beta0, beta1) of the composite minus the parts measured separately."""
    a0, a1 = gcf.topology(ga)
    b0, b1 = gcf.topology(gb)
    c0, c1 = gcf.topology(comp)
    return {"parts": [a0 + b0, a1 + b1], "composite": [c0, c1],
            "delta": [c0 - (a0 + b0), c1 - (a1 + b1)]}


# ---------------------------------------------------------------------------
# Emptiness / anti-aliasing candidate (steps 48-50)
# ---------------------------------------------------------------------------
def emptiness_check(cells: list[tuple[int, int, np.ndarray]], cols: int, rows: int,
                    blank_index: int) -> dict:
    """Blank one cell of a lattice and report whether (beta0, beta1) of the
    composite changed. A change REJECTS the candidate (chen-2024: topology
    preservation is global; no local rule decides it)."""
    before = grid_composite(cells, cols, rows)
    h, w = cells[0][2].shape
    edited = [(c, r, np.zeros((h, w), dtype=np.uint8) if i == blank_index else g)
              for i, (c, r, g) in enumerate(cells)]
    after = grid_composite(edited, cols, rows)
    tb, ta = gcf.topology(before), gcf.topology(after)
    return {"before": list(tb), "after": list(ta), "accepted": tb == ta}


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def pair_features(ga: np.ndarray, gb: np.ndarray, relation: str,
                  reference: np.ndarray | None = None) -> dict:
    """Every §3.3 number for an ordered pair (A, B)."""
    comp = composite(ga, gb, relation)
    profile = seam_gap_profile(ga, gb, relation)
    return {
        "relation": relation,
        "port_overlap": port_overlap(ga, gb, relation),
        "seam_gap_profile": profile,
        "seam": seam_summary(profile),
        "apparent_slope_deg": apparent_slope(comp),
        "topology": topology_delta(ga, gb, comp),
        "continuity": cross_cell_continuity(comp, reference),
        "composite_shape": list(comp.shape),
    }


def pair_from_chars(scorer: GlyphScorer, a: str, b: str, relation: str,
                    reference: np.ndarray | None = None) -> dict:
    ra = gcf.cell_raster(scorer, ord(a))
    rb = gcf.cell_raster(scorer, ord(b))
    rec = pair_features(ra.grid, rb.grid, relation, reference)
    rec.update({"a": a, "b": b, "cp_a": ord(a), "cp_b": ord(b),
                "position_source": [ra.position_source, rb.position_source]})
    return rec


def load_combos(path: Path = COMBOS_JSON) -> list[dict]:
    return json.loads(path.read_text())["combinations"]


def combo_features(scorer: GlyphScorer, combo: dict) -> dict:
    """Per-cell features plus every horizontally/vertically adjacent pair of one authored combo."""
    cells = []
    per_cell = {}
    for c in combo["cells"]:
        r = gcf.cell_raster(scorer, c["cp"])
        cells.append((c["col"], c["row"], r.grid))
        per_cell[(c["col"], c["row"])] = {
            "char": r.char, "cp": c["cp"],
            "ink_extents": gcf.ink_extents(r.grid),
            "ink_centroid": gcf._measure(r.grid, r.cell_w, r.cell_h)["ink_centroid"],
            "components": gcf.components(r.grid),
            "dominant_orientation_deg": gcf.dominant_orientation_deg(r.grid),
            "beta": list(gcf.topology(r.grid)),
        }
    grids = {(c, r): g for c, r, g in cells}
    pairs = []
    for (c, r), g in sorted(grids.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if (c + 1, r) in grids:
            p = pair_features(g, grids[(c + 1, r)], "beside")
            p["cells"] = [[c, r], [c + 1, r]]
            pairs.append(p)
        if (c, r + 1) in grids:
            p = pair_features(g, grids[(c, r + 1)], "stacked")
            p["cells"] = [[c, r], [c, r + 1]]
            pairs.append(p)
    comp = grid_composite(cells, combo["cols"], combo["rows"])
    return {
        "id": combo["id"],
        "chars": "".join(x["char"] for x in combo["cells"]),
        "cells": {f"{k[0]},{k[1]}": v for k, v in per_cell.items()},
        "pairs": pairs,
        "composite_beta": list(gcf.topology(comp)),
        "composite_slope_deg": apparent_slope(comp),
        "composite_continuity": cross_cell_continuity(comp),
    }


def ascii_raster(grid: np.ndarray) -> str:
    return "\n".join("".join("#" if v else "." for v in row) for row in grid)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _fmt_pair(rec: dict) -> str:
    s = rec["seam"]
    cont = rec["continuity"]
    return (f"{rec.get('a', '?')!r} {rec['relation']:7} {rec.get('b', '?')!r}  "
            f"port={rec['port_overlap']:.2f}  gap min/med/max={s['min']}/{s['median']}/{s['max']} "
            f"connected={s['connected']}  slope={rec['apparent_slope_deg'] if rec['apparent_slope_deg'] is None else round(rec['apparent_slope_deg'], 1)}  "
            f"dBeta={rec['topology']['delta']}  DSM={cont['dsm'] if cont['dsm'] is None else round(cont['dsm'], 3)} "
            f"({cont['reference_source']})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stack", nargs=2, metavar=("A", "B"), help="B below A")
    ap.add_argument("--beside", nargs=2, metavar=("A", "B"), help="B right of A")
    ap.add_argument("--combos", action="store_true", help="score the 9 authored combinations")
    ap.add_argument("--raster", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    scorer = GlyphScorer()
    out = []
    if a.stack:
        out.append(pair_from_chars(scorer, a.stack[0], a.stack[1], "stacked"))
    if a.beside:
        out.append(pair_from_chars(scorer, a.beside[0], a.beside[1], "beside"))
    if a.combos:
        for combo in load_combos():
            out.append(combo_features(scorer, combo))
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    for rec in out:
        if "pairs" in rec:
            print(f"{rec['id']}  {rec['chars']!r}  beta={rec['composite_beta']} "
                  f"slope={rec['composite_slope_deg'] if rec['composite_slope_deg'] is None else round(rec['composite_slope_deg'], 1)} "
                  f"DSM={round(rec['composite_continuity']['dsm'], 3) if rec['composite_continuity']['dsm'] is not None else None}")
            for p in rec["pairs"]:
                print("   " + _fmt_pair({**p, "a": rec['cells'][f"{p['cells'][0][0]},{p['cells'][0][1]}"]["char"],
                                         "b": rec['cells'][f"{p['cells'][1][0]},{p['cells'][1][1]}"]["char"]}))
        else:
            print(_fmt_pair(rec))
            if a.raster:
                ga = gcf.cell_raster(scorer, rec["cp_a"]).grid
                gb = gcf.cell_raster(scorer, rec["cp_b"]).grid
                print(ascii_raster(composite(ga, gb, rec["relation"])))
                print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
