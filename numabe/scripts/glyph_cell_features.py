#!/usr/bin/env python3
"""
glyph_cell_features.py — measured per-glyph CELL features on the AS-POSITIONED raster.

WHY THIS MODULE EXISTS
----------------------
scripts/glyph_features.py computes its descriptors on ``norm`` (crop-to-ink,
refit-to-cell), which is deliberately position-blind. Every feature that decides
whether two glyphs CONNECT across a cell seam (the near-vertical anti-aliasing
problem of the Stone Story tutorials: ``¡ ! . '`` plus space) depends on where
ink sits inside the cell, so it has to be measured on the raw, as-positioned
raster instead. This module is that measurement layer. It is offline authoring
tooling; no runtime, atlas, compiler, or shader surface reads it.

Method owner: docs/audits/2026-09-08-glyph-feature-vocabulary-fl4512-corpus-audit.md
§3.1 (raster/normalisation) and §3.2 (per-glyph features). Step numbers below
refer to that spec. Corpus owners per §1 of the same audit:

    AISS descriptor         xu-2010 §3 (anisotropic (Tw/2)x(Th/2) sampling,
                            log-polar 5 radial x 12 angular, shared scale)
    border incidence classes chen-2024 (through / terminal / floating; ink is
                            8-connected, background is 4-connected)
    (beta0, beta1) pair     chen-2024 (never collapsed to an Euler number)
    orientation field       chung-2022 Eq. 4-7 (Gaussian sigma 0.7, Scharr, 5x5 window)
    stability under 1 px    akiyama-2017 augmentation range;
                            decarlo-2004 "analyse stability first"

Explicitly NOT here (spec step 31): a skeleton stroke count. Skeleton
endpoint/junction counting is unreliable at this resolution.

RASTER DOMAIN
-------------
GlyphScorer.ink_grid renders a pixel font (unifont) natively into a 16x16
canvas at (0, 0). Half-width glyphs therefore occupy columns 0-7 and the right
half is canvas padding, not cell. This module crops the canvas to the glyph's
advance width so that ``left``/``right`` contact means the real cell edge:

    cell_w = advance width (8 for half-width, 16 for full-width unifont)
    cell_h = 16
    alpha  = cell_h / cell_w   (declared once, spec step 4; 2.0 for half-width)

Glyphs that fall through to an outline font are rendered crop-fit by the
scorer and are position-blind. They are tagged ``position_source == "fit"`` and
their border/contact features are reported as ``None``.

MEASURED FACT WORTH KNOWING BEFORE READING RESULTS
--------------------------------------------------
In unifont the ASCII bar ``|`` (U+007C) spans rows 2-15: it touches the bottom
edge only, so it is ``terminal``, not ``through_v``. The box-drawing bar ``│``
(U+2502) spans rows 0-15 and IS ``through_v``. ``!`` (U+0021) spans rows 4-13
and is ``floating``. The tests pin these as measured, not assumed.

Usage:
    python3 scripts/glyph_cell_features.py --chars '|!│'            # table
    python3 scripts/glyph_cell_features.py --chars '|!' --json      # full dicts
    python3 scripts/glyph_cell_features.py --chars '|!' --raster    # ASCII dump
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import ndimage
from skimage import measure
from skimage.filters import scharr_h, scharr_v

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from glyph_morphology_browser import BLOCKS, GlyphScorer, find_block_for_cp  # noqa: E402

SIDES = ("top", "bottom", "left", "right")
CANVAS_PX = 16

# AISS parameters (spec steps 7-10; xu-2010 §3).
AISS_RADIAL_BINS = 5
AISS_ANGULAR_BINS = 12
AISS_R_INNER = 0.5
AISS_GAUSS_KERNEL = 7          # 7x7 pre-filter, spec step 10
AISS_GAUSS_SIGMA = 1.0         # 7 == 2 * ceil(3 * sigma) + 1

# Orientation field parameters (spec step 29; chung-2022 Eq. 4-7).
ORIENT_SIGMA = 0.7
ORIENT_WINDOW = 5
ORIENT_BINS = 12

# Features recomputed under a one-pixel shift for the stability mark (steps 52-54).
STABILITY_KEYS = ("beta0", "beta1", "incidence_classes", "contact_occupancy", "ink_extents")


# ---------------------------------------------------------------------------
# Raster
# ---------------------------------------------------------------------------
@dataclass
class CellRaster:
    cp: int
    char: str
    font: str
    position_source: str            # "native" (as-positioned) | "fit" (position-blind)
    cell_w: int
    cell_h: int
    grid: np.ndarray                # (cell_h, cell_w) uint8 0/1

    @property
    def alpha(self) -> float:
        return self.cell_h / self.cell_w

    def ascii(self) -> str:
        return "\n".join("".join("#" if v else "." for v in row) for row in self.grid)


def cell_raster(scorer: GlyphScorer, cp: int) -> CellRaster:
    """Crop the scorer's 16x16 canvas to the glyph's real cell (advance width)."""
    ch = chr(cp)
    small, big, name = scorer._pick_entry(cp)
    canvas = np.asarray(scorer.ink_grid(ch), dtype=np.uint8)
    if big is not None:
        # Outline fallback: scorer already crop-fit the ink. Position is meaningless.
        return CellRaster(cp, ch, name, "fit", CANVAS_PX, CANVAS_PX, canvas)
    try:
        adv = int(round(float(small.getlength(ch))))
    except Exception:
        adv = CANVAS_PX
    cell_w = adv if 0 < adv <= CANVAS_PX else CANVAS_PX
    grid = np.ascontiguousarray(canvas[:, :cell_w])
    return CellRaster(cp, ch, name, "native", cell_w, canvas.shape[0], grid)


def raster_from_rows(rows: list[str]) -> np.ndarray:
    """Test helper: build a 0/1 grid from '#'/'.' rows."""
    return np.asarray([[1 if c == "#" else 0 for c in r] for r in rows], dtype=np.uint8)


# ---------------------------------------------------------------------------
# Per-glyph features (spec §3.2)
# ---------------------------------------------------------------------------
def ink_extents(grid: np.ndarray) -> dict | None:
    """Steps 26-27. Derived summaries; never a match key."""
    rows = np.flatnonzero(grid.any(axis=1))
    cols = np.flatnonzero(grid.any(axis=0))
    if rows.size == 0:
        return None
    return {
        "ink_top": int(rows[0]),
        "ink_bottom": int(rows[-1]),
        "ink_left": int(cols[0]),
        "ink_right": int(cols[-1]),
    }


def contact_occupancy(grid: np.ndarray) -> dict[str, list[bool]]:
    """Step 19. One boolean per border pixel, per side."""
    return {
        "top": [bool(v) for v in grid[0, :]],
        "bottom": [bool(v) for v in grid[-1, :]],
        "left": [bool(v) for v in grid[:, 0]],
        "right": [bool(v) for v in grid[:, -1]],
    }


def runs_of(vec: list[bool]) -> list[tuple[int, int]]:
    """Step 20. Ordered (start, end) half-open runs of True."""
    out: list[tuple[int, int]] = []
    start = None
    for i, v in enumerate(vec):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(vec)))
    return out


def contact_runs(occ: dict[str, list[bool]]) -> dict[str, list[tuple[int, int]]]:
    return {s: runs_of(occ[s]) for s in SIDES}


def contact_span(runs: dict[str, list[tuple[int, int]]]) -> dict[str, tuple[int, int] | None]:
    """Step 21. Derived summary only."""
    return {s: ((r[0][0], r[-1][1]) if r else None) for s, r in runs.items()}


def topology(grid: np.ndarray) -> tuple[int, int]:
    """Steps 13-17. beta0 = 8-connected ink components; beta1 = 4-connected
    background components that do not touch the cell border (chen-2024's
    asymmetric adjacency). Returned as a pair, never as an Euler number."""
    ink = grid.astype(bool)
    lab_ink = measure.label(ink, connectivity=2, background=0)
    beta0 = int(lab_ink.max())
    lab_bg = measure.label(~ink, connectivity=1, background=0)
    border = np.concatenate([lab_bg[0, :], lab_bg[-1, :], lab_bg[:, 0], lab_bg[:, -1]])
    border_labels = set(int(v) for v in np.unique(border) if v != 0)
    all_labels = set(int(v) for v in np.unique(lab_bg) if v != 0)
    beta1 = len(all_labels - border_labels)
    return beta0, beta1


def border_incidence(sides_touched: set[str]) -> set[str]:
    """Steps 23-24. A component may be both through_v and through_h."""
    inc: set[str] = set()
    if {"top", "bottom"} <= sides_touched:
        inc.add("through_v")
    if {"left", "right"} <= sides_touched:
        inc.add("through_h")
    if inc:
        return inc
    if len(sides_touched) == 0:
        return {"floating"}
    return {"terminal"}


def components(grid: np.ndarray) -> list[dict]:
    """Step 22-23. Per 8-connected ink component: size, sides touched, incidence."""
    lab = measure.label(grid.astype(bool), connectivity=2, background=0)
    out = []
    for k in range(1, int(lab.max()) + 1):
        m = lab == k
        touched = set()
        if m[0, :].any():
            touched.add("top")
        if m[-1, :].any():
            touched.add("bottom")
        if m[:, 0].any():
            touched.add("left")
        if m[:, -1].any():
            touched.add("right")
        ys, xs = np.nonzero(m)
        out.append({
            "label": k,
            "size": int(m.sum()),
            "row_centroid": float(ys.mean()),      # altitude of this component (derived summary)
            "col_centroid": float(xs.mean()),
            "sides_touched": sorted(touched),
            "border_incidence": sorted(border_incidence(touched)),
        })
    out.sort(key=lambda c: c["row_centroid"])      # top-most component first
    return out


# ---------------------------------------------------------------------------
# AISS descriptor (spec §3.1 steps 5-10, step 28; xu-2010 §3)
# ---------------------------------------------------------------------------
def aiss_sample_points(cell_w: int, cell_h: int) -> np.ndarray:
    """Step 7. (cell_w/2) x (cell_h/2) sample points; anisotropic in the cell ratio.
    Points sit at pixel centres of every second row and column."""
    xs = np.arange(0, cell_w, 2) + 0.5
    ys = np.arange(0, cell_h, 2) + 0.5
    gx, gy = np.meshgrid(xs, ys)
    return np.stack([gx.ravel(), gy.ravel()], axis=1)


def aiss_radius(cell_w: int, cell_h: int) -> float:
    """Step 8. Half the SHORTER cell side."""
    return min(cell_w, cell_h) / 2.0


def aiss_prefilter(grid: np.ndarray) -> np.ndarray:
    """Step 10. 7x7 Gaussian on the raster before histogramming."""
    truncate = ((AISS_GAUSS_KERNEL - 1) / 2) / AISS_GAUSS_SIGMA
    return ndimage.gaussian_filter(grid.astype(np.float64), AISS_GAUSS_SIGMA,
                                   truncate=truncate, mode="constant", cval=0.0)


def aiss_descriptor(grid: np.ndarray, cell_w: int, cell_h: int) -> np.ndarray:
    """Step 28. Returns (S, R*T) float64: one log-polar histogram per sample point.

    All glyphs of one cell size share the same sample grid, radius and bin
    edges (xu-2010's shared scale, step 5). No mean-distance normalisation
    (step 6). Histogram mass is the Gaussian-filtered ink inside the window.
    """
    if grid.shape != (cell_h, cell_w):
        raise ValueError(f"grid {grid.shape} does not match cell ({cell_h}, {cell_w})")
    pts = aiss_sample_points(cell_w, cell_h)
    r_out = aiss_radius(cell_w, cell_h)
    weights = aiss_prefilter(grid)
    ys, xs = np.nonzero(weights > 1e-6)
    w = weights[ys, xs]
    px = xs + 0.5
    py = ys + 0.5
    r_edges = np.logspace(math.log10(AISS_R_INNER), math.log10(r_out), AISS_RADIAL_BINS + 1)
    out = np.zeros((pts.shape[0], AISS_RADIAL_BINS * AISS_ANGULAR_BINS), dtype=np.float64)
    if w.size == 0:
        return out
    for si, (sx, sy) in enumerate(pts):
        dx = px - sx
        dy = py - sy
        dist = np.hypot(dx, dy)
        keep = (dist >= AISS_R_INNER) & (dist < r_out)
        if not keep.any():
            continue
        ang = np.arctan2(dy[keep], dx[keep]) % (2 * math.pi)
        rb = np.clip(np.searchsorted(r_edges, dist[keep], side="right") - 1, 0, AISS_RADIAL_BINS - 1)
        tb = np.minimum((ang / (2 * math.pi) * AISS_ANGULAR_BINS).astype(int), AISS_ANGULAR_BINS - 1)
        np.add.at(out[si], rb * AISS_ANGULAR_BINS + tb, w[keep])
    return out


def aiss_distance(ha: np.ndarray, hb: np.ndarray) -> float:
    """Per-sample-point L2 distance summed over the shared sample grid (xu-2010 Eq. 3 shape)."""
    if ha.shape != hb.shape:
        raise ValueError(f"descriptor shapes differ: {ha.shape} vs {hb.shape}")
    return float(np.linalg.norm(ha - hb, axis=1).sum())


# ---------------------------------------------------------------------------
# Orientation field (spec steps 29-30; chung-2022 Eq. 4-7)
# ---------------------------------------------------------------------------
def orientation_field(grid: np.ndarray) -> np.ndarray:
    """theta(p) in [0, pi) for every pixel; callers mask to foreground.
    Gaussian sigma 0.7 -> Scharr Gx, Gy -> 5x5 window sums of (2GxGy, Gx^2-Gy^2)
    -> theta = 0.5*atan2(Vy, Vx) + pi/2 (orientation ALONG the stroke)."""
    f = ndimage.gaussian_filter(grid.astype(np.float64), ORIENT_SIGMA, truncate=1.0, mode="constant")
    gx = scharr_v(f)      # d/dx (vertical-edge response)
    gy = scharr_h(f)      # d/dy
    k = np.ones((ORIENT_WINDOW, ORIENT_WINDOW))
    vx = ndimage.convolve(gx * gx - gy * gy, k, mode="constant")
    vy = ndimage.convolve(2.0 * gx * gy, k, mode="constant")
    theta = 0.5 * np.arctan2(vy, vx) + math.pi / 2.0   # gradient normal -> stroke direction
    return np.mod(theta, math.pi)


def orientation_hist(grid: np.ndarray, bins: int = ORIENT_BINS) -> list[float]:
    """Step 30. Histogram of theta(p) over foreground pixels only; sums to 1 (or all-zero)."""
    theta = orientation_field(grid)
    fg = grid.astype(bool)
    if not fg.any():
        return [0.0] * bins
    h, _ = np.histogram(theta[fg], bins=bins, range=(0.0, math.pi))
    h = h.astype(np.float64)
    return [float(v) for v in (h / h.sum())]


def dominant_orientation_deg(grid: np.ndarray) -> float | None:
    """Circular mean of theta over foreground, in degrees [0,180). 90 = vertical stroke."""
    theta = orientation_field(grid)
    fg = grid.astype(bool)
    if not fg.any():
        return None
    ang = 2.0 * theta[fg]
    m = math.atan2(np.sin(ang).mean(), np.cos(ang).mean()) / 2.0
    return float(math.degrees(m % math.pi))


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def _measure(grid: np.ndarray, cell_w: int, cell_h: int) -> dict:
    beta0, beta1 = topology(grid)
    occ = contact_occupancy(grid)
    runs = contact_runs(occ)
    comps = components(grid)
    classes = sorted({c for comp in comps for c in comp["border_incidence"]})
    ys, xs = np.nonzero(grid)
    centroid = [float(ys.mean()), float(xs.mean())] if ys.size else None
    return {
        "ink": int(grid.sum()),
        "ink_extents": ink_extents(grid),
        "ink_centroid": centroid,                  # (row, col) on the raw cell; derived summary
        "beta0": beta0,
        "beta1": beta1,
        "components": comps,
        "incidence_classes": classes,
        "contact_occupancy": occ,
        "contact_runs": runs,
        "contact_span": contact_span(runs),
        "orientation_hist": orientation_hist(grid),
        "dominant_orientation_deg": dominant_orientation_deg(grid),
        "aiss": aiss_descriptor(grid, cell_w, cell_h),
    }


def shifted(grid: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """Shift with zero fill (ink pushed past the border is lost — that IS the instability)."""
    out = np.zeros_like(grid)
    h, w = grid.shape
    ys = slice(max(dy, 0), h + min(dy, 0))
    yd = slice(max(-dy, 0), h + min(-dy, 0))
    xs = slice(max(dx, 0), w + min(dx, 0))
    xd = slice(max(-dx, 0), w + min(-dx, 0))
    out[ys, xs] = grid[yd, xd]
    return out


def stability_marks(grid: np.ndarray, cell_w: int, cell_h: int, base: dict) -> list[str]:
    """Steps 52-53. Names of features whose value changes under any 1 px shift."""
    unstable: set[str] = set()
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        m = _measure(shifted(grid, dy, dx), cell_w, cell_h)
        for k in STABILITY_KEYS:
            if m[k] != base[k]:
                unstable.add(k)
    return sorted(unstable)


def glyph_features(scorer: GlyphScorer, cp: int) -> dict:
    """Full per-glyph record. JSON-serialisable except ``aiss`` (ndarray; see to_json)."""
    r = cell_raster(scorer, cp)
    rec = {
        "cp": cp,
        "char": r.char,
        "font": r.font,
        "position_source": r.position_source,
        "cell_w": r.cell_w,
        "cell_h": r.cell_h,
        "alpha": r.alpha,
    }
    m = _measure(r.grid, r.cell_w, r.cell_h)
    if r.position_source == "fit":
        # Position-blind render: border/contact/altitude are not measurements of the cell.
        for k in ("ink_extents", "components", "incidence_classes", "contact_occupancy",
                  "contact_runs", "contact_span"):
            m[k] = None
        m["unstable"] = None
    else:
        m["unstable"] = stability_marks(r.grid, r.cell_w, r.cell_h, m)
    rec.update(m)
    return rec


def to_json(rec: dict) -> dict:
    out = dict(rec)
    if isinstance(out.get("aiss"), np.ndarray):
        out["aiss"] = out["aiss"].round(4).tolist()
    return out


# ---------------------------------------------------------------------------
# Whole-repertoire cache (feature-driven candidate generation reads this)
# ---------------------------------------------------------------------------
CACHE_DIR = Path(__file__).resolve().parent.parent / ".run" / "glyph_audit"
CELL_CACHE_NPZ = CACHE_DIR / "glyph_cell_features.npz"
CELL_CACHE_META = CACHE_DIR / "glyph_cell_features.meta.json"


BAND = 3


def _mask(vec: list[bool]) -> int:
    return sum(1 << i for i, v in enumerate(vec) if v)


def build_cache(scorer: GlyphScorer, cps: list[int], progress: bool = True) -> dict:
    """Measure every codepoint on its as-positioned cell and store parallel arrays.

    Skips the stability marks (4x cost) and keeps the AISS descriptor per cell
    width (8-wide and 16-wide glyphs have different sample grids, so they live
    in two arrays and are only ever compared within one width).
    """
    n = len(cps)
    cols = {
        "cp": np.zeros(n, np.int32), "cell_w": np.zeros(n, np.int8), "native": np.zeros(n, np.bool_),
        "ink": np.zeros(n, np.int16), "ink_top": np.full(n, -1, np.int8), "ink_bottom": np.full(n, -1, np.int8),
        "ink_left": np.full(n, -1, np.int8), "ink_right": np.full(n, -1, np.int8),
        "cen_row": np.full(n, np.nan, np.float32), "cen_col": np.full(n, np.nan, np.float32),
        "beta0": np.zeros(n, np.int16), "beta1": np.zeros(n, np.int16),
        "through_v": np.zeros(n, np.bool_), "through_h": np.zeros(n, np.bool_),
        "terminal": np.zeros(n, np.bool_), "floating": np.zeros(n, np.bool_),
        "mask_top": np.zeros(n, np.uint16), "mask_bottom": np.zeros(n, np.uint16),
        "mask_left": np.zeros(n, np.uint16), "mask_right": np.zeros(n, np.uint16),
        # 3-pixel edge BANDS (union of the three rows/columns nearest each side):
        # the port descriptor for glyphs that stop short of the edge (| ! , ' .).
        "band_top": np.zeros(n, np.uint16), "band_bottom": np.zeros(n, np.uint16),
        "band_left": np.zeros(n, np.uint16), "band_right": np.zeros(n, np.uint16),
        "block": np.zeros(n, np.int16),
        "dom_deg": np.full(n, np.nan, np.float32),
        "orient_hist": np.zeros((n, ORIENT_BINS), np.float32),
    }
    aiss8, aiss16, idx8, idx16 = [], [], [], []
    block_index: dict[str, int] = {}
    for k, cp in enumerate(cps):
        if progress and k % 5000 == 0:
            print(f"  {k}/{n}", flush=True)
        r = cell_raster(scorer, cp)
        g = r.grid
        cols["cp"][k] = cp
        cols["cell_w"][k] = r.cell_w
        cols["native"][k] = r.position_source == "native"
        cols["ink"][k] = int(g.sum())
        ext = ink_extents(g)
        if ext:
            for key in ("ink_top", "ink_bottom", "ink_left", "ink_right"):
                cols[key][k] = ext[key]
            ys, xs = np.nonzero(g)
            cols["cen_row"][k], cols["cen_col"][k] = ys.mean(), xs.mean()
        b0, b1 = topology(g)
        cols["beta0"][k], cols["beta1"][k] = b0, b1
        classes = {c for comp in components(g) for c in comp["border_incidence"]}
        for key in ("through_v", "through_h", "terminal", "floating"):
            cols[key][k] = key in classes
        occ = contact_occupancy(g)
        for side in SIDES:
            cols[f"mask_{side}"][k] = _mask(occ[side])
        cols["band_top"][k] = _mask(list(g[:BAND, :].any(axis=0)))
        cols["band_bottom"][k] = _mask(list(g[-BAND:, :].any(axis=0)))
        cols["band_left"][k] = _mask(list(g[:, :BAND].any(axis=1)))
        cols["band_right"][k] = _mask(list(g[:, -BAND:].any(axis=1)))
        bi = find_block_for_cp(cp)
        bname = BLOCKS[bi][2] if bi is not None else "?"
        cols["block"][k] = block_index.setdefault(bname, len(block_index))
        d = dominant_orientation_deg(g)
        cols["dom_deg"][k] = np.nan if d is None else d
        cols["orient_hist"][k] = orientation_hist(g)
        if r.position_source == "native" and cols["ink"][k] > 0:
            h = aiss_descriptor(g, r.cell_w, r.cell_h).astype(np.float32)
            (aiss8 if r.cell_w == 8 else aiss16).append(h)
            (idx8 if r.cell_w == 8 else idx16).append(k)
    cols["aiss8"] = np.stack(aiss8) if aiss8 else np.zeros((0, 32, 60), np.float32)
    cols["aiss8_idx"] = np.asarray(idx8, np.int32)
    cols["aiss16"] = np.stack(aiss16) if aiss16 else np.zeros((0, 64, 60), np.float32)
    cols["aiss16_idx"] = np.asarray(idx16, np.int32)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(CELL_CACHE_NPZ, **cols)
    meta = {"schema": 2, "count": n, "font": scorer.font_names[0], "cell_h": CANVAS_PX, "band": BAND,
            "blocks": sorted(block_index, key=block_index.get),
            "native": int(cols["native"].sum()), "w8": len(idx8), "w16": len(idx16),
            "note": "as-positioned cell features; see glyph_cell_features.py; stability marks not cached"}
    CELL_CACHE_META.write_text(json.dumps(meta, indent=1))
    return meta


def load_cache() -> tuple[dict, dict]:
    if not CELL_CACHE_NPZ.exists() or not CELL_CACHE_META.exists():
        raise SystemExit(f"missing {CELL_CACHE_NPZ}; run: python3 scripts/glyph_cell_features.py --build --admitted")
    z = np.load(CELL_CACHE_NPZ)
    return {k: z[k] for k in z.files}, json.loads(CELL_CACHE_META.read_text())


def _cps_for_build(scorer: GlyphScorer, want_all: bool) -> list[int]:
    cps = sorted(scorer.font_cps)
    if want_all:
        return cps
    import glyph_audit as ga
    admitted = set(int(c) for c in ga.load_admitted())
    return [cp for cp in cps if cp in admitted]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _table_row(rec: dict) -> str:
    ext = rec["ink_extents"]
    if rec["position_source"] == "fit":
        return f"U+{rec['cp']:04X} {rec['char']!r:6} fit  position-blind (outline fallback: {rec['font']})"
    alt = f"rows {ext['ink_top']:>2}-{ext['ink_bottom']:<2} cols {ext['ink_left']:>2}-{ext['ink_right']:<2}" if ext else "blank"
    occ = rec["contact_occupancy"]
    touch = "".join(s[0].upper() if any(occ[s]) else "-" for s in SIDES)
    dom = rec["dominant_orientation_deg"]
    dom_s = f"{dom:5.1f}°" if dom is not None else "   -  "
    return (f"U+{rec['cp']:04X} {rec['char']!r:6} {rec['cell_w']}x{rec['cell_h']} "
            f"{alt:24} b0={rec['beta0']} b1={rec['beta1']} TBLR={touch} "
            f"{','.join(rec['incidence_classes']) or '-':18} dom={dom_s} "
            f"unstable={','.join(rec['unstable']) or '-'}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--chars", default="|!│", help="glyphs to measure (a string)")
    ap.add_argument("--json", action="store_true", help="emit full records as JSON")
    ap.add_argument("--raster", action="store_true", help="dump the cropped as-positioned raster")
    ap.add_argument("--build", action="store_true", help="build the whole-repertoire cache under .run/glyph_audit/")
    ap.add_argument("--all", action="store_true", help="--build: every renderable codepoint (default: atlas-admitted only)")
    a = ap.parse_args(argv)
    scorer = GlyphScorer()
    if a.build:
        cps = _cps_for_build(scorer, a.all)
        print(f"measuring {len(cps)} codepoints ...", flush=True)
        meta = build_cache(scorer, cps)
        print(json.dumps(meta))
        return 0
    recs = [glyph_features(scorer, ord(c)) for c in a.chars]
    if a.json:
        print(json.dumps([to_json(r) for r in recs], ensure_ascii=False, indent=1))
        return 0
    for r in recs:
        print(_table_row(r))
        if a.raster:
            print(cell_raster(scorer, r["cp"]).ascii())
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
