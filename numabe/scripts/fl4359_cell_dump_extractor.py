#!/usr/bin/env python3
# =============================================================================
# fl4359_cell_dump_extractor.py
# =============================================================================
# FL-4359 PER-CELL FRAME-INDEXED CELL DUMP: the offline extractor that turns a
# short screen capture into a per-cell, per-frame record of what the ASCII
# renderer actually published, and into the per-frame-PAIR deltas that localize
# the shimmer.
#
# WHAT PROBLEM THIS SOLVES
# ------------------------
# Every existing FL-4359 proof tool measures an AGGREGATE (changed-pixel
# fraction, mean flip counts, owner churn totals) or reads a GPU-side diagnostic
# dump that only a live instrumented run can produce
# (fl4359_backstop_oscillation_analyzer.py needs frame_NNN_diag.bin;
# fl4359_owner_churn_gate.py needs the sparse recorder). None of them answers
# the question the operator's capture raises: for ONE screen cell, across FIVE
# consecutive frames, WHICH glyph shape and WHICH two colours were published,
# and did that cell return to an earlier state.
#
# This extractor answers exactly that, from rendered pixels alone. It needs no
# instrumented build, so it can measure a capture of the shipping renderer and
# it can measure an arm that no longer exists.
#
# THE FALSIFIER THIS EXISTS TO EVALUATE
# -------------------------------------
# The capture under proof is MONOTONE TRANSLATION with HELD ORIENTATION
# (yaw 135, pitch -21, constant across the whole capture). Under that motion
# every world feature moves across the screen in one direction at a near-constant
# rate, so the similarity between a cell's published glyph mask at frame N and at
# frame N+k must DECAY MONOTONICALLY in k. Camera motion cannot make frame N+2
# look MORE like frame N than frame N+1 does. So:
#
#     OSCILLATION FLAG: IoU(N, N+2) >= IoU(N, N+1) + 0.15
#                  AND  IoU(N, N+2) >= IoU(N+1, N+2) + 0.15
#
# A flagged cell returned to an earlier published identity. That cannot be
# produced by the camera; it is renderer-side identity churn. The 0.15 margin
# keeps sampling noise out of the set.
#
# MASK EXTRACTOR ROBUSTNESS (mandatory self-falsification)
# --------------------------------------------------------
# The finding above is only as real as the glyph-mask estimator. If the
# oscillation set is an artifact of per-cell Otsu thresholding, a different
# estimator will not reproduce it. This script therefore runs the whole
# measurement under EVERY method named by --mask-method and reports the
# oscillation count for each. A method that collapses the count FALSIFIES the
# finding; that is a valid, reportable result and this tool does not hide it.
#
# GRID DISCOVERY
# --------------
# The cell lattice is comb-FIT from the capture, never hard-coded: the per-axis
# edge energy of the image is projected onto a complex exponential at every
# candidate pitch, and the pitch maximizing that response wins. The phase comes
# from the argument of the same response, so pitch and phase are one
# measurement, not two. --grid-json pins a previously fitted lattice when an
# exact re-measurement of an old capture is required.
#
# OUTPUT
# ------
#   <out>/fl4359_cell_dump_receipt.json   schema fl4359_cell_dump_receipt.v1
#   <out>/cell_dump_<method>.npz          packed masks + FG/BG/coverage + valid
# Exit codes: 0 = measured (and, with --baseline, every metric inside
# tolerance); 1 = a baseline metric fell outside tolerance, or the input could
# not be measured. A non-zero exit means THIS extractor disagrees with the
# recorded baseline; that divergence must be reconciled, never waved through.
#
# USAGE
#   python3 scripts/fl4359_cell_dump_extractor.py \
#       --frames artifacts/run_receipts_extract_20260901/fl4359_airplane_5frame_fixture \
#       --pose-json  <same dir>/FL4359_gif_measurement.json \
#       --baseline   <same dir>/FL4359_gif_measurement.json \
#       --arm-label baseline_capture --out .run/fl4359_cell_dump/baseline
# =============================================================================

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
# The Product ABI has exactly one owner in this tree. Import it; never restate it.
from fl4359_product_cell_dump import (  # noqa: E402
    compare_product_frames, load_product_frames)

ROOT = Path(__file__).resolve().parents[1]

RECEIPT_SCHEMA = "fl4359_cell_dump_receipt.v1"

# --- Frozen measurement constants -------------------------------------------
# Rec.601 luma. The renderer publishes an FG and a BG colour per cell; a scalar
# ordering of the two is all the mask election needs, and 601 matches the
# reference measurement this extractor must reproduce.
LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)
# A cell whose luminance spread is below this has no glyph contrast: one flat
# colour was published, so there is no mask to elect and no identity to track.
GLYPH_CONTRAST_MIN_SPREAD = 12.0
# Otsu bin count. Frozen because the reference baseline was measured with it and
# the elected threshold is bin-centre quantized.
OTSU_BINS = 32
# Oscillation margin. See the falsifier note above.
OSCILLATION_MARGIN = 0.15
# A pixel counts as changed when the summed absolute per-channel delta exceeds
# this. Reconciled against the recorded baseline pixels-changed fractions.
PIXEL_CHANGED_SUM_ABS_MIN = 12
# Composite-metric thresholds. These name the two failure shapes the operator
# capture shows: the mask flipping hard while the two colours hold (a glyph
# identity swap on stable material), and the colours jumping while the mask
# holds (a material swap under a stable glyph).
MASK_FLIP_HARD_IOU_MAX = 0.35
COLOUR_HOLDS_DELTA_MAX = 20.0
MASK_HOLDS_IOU_MIN = 0.90
COLOUR_JUMP_DELTA_MIN = 50.0
# Corrected channel split for the polarity-invariant column, as stated in the
# 2026-09-01 review that supplied FL4359_polarity_corrected.json. Kept here
# because that JSON carries the per-pair rows but not these two aggregates.
POLARITY_CORRECTED_MASK_FLIP_HARD_PCT = 6.5
POLARITY_CORRECTED_COLOUR_JUMP_PCT = 1.6
# Ink-coverage band in which the polarity-flip test is geometrically unbiased.
# Outside it the complement comparison wins or loses for reasons of area rather
# than of polarity. See polarity_flip_pct_balanced_coverage.
POLARITY_BALANCED_COVERAGE_MIN = 0.25
POLARITY_BALANCED_COVERAGE_MAX = 0.75

DEFAULT_TITLEBAR_PX = 28
# Rows of the capture that are window chrome or HUD text rather than published
# world. The HUD row is measured (it is real published output) but excluded from
# the `viewport` roll-up, because HUD glyph churn is not renderer shimmer.
DEFAULT_HUD_ROWS = 1

MASK_METHODS = ("otsu", "kmeans2", "midrange", "otsu_dark_ink")


# ---------------------------------------------------------------------------
# Grid comb fit
# ---------------------------------------------------------------------------
def _edge_energy(gray: np.ndarray, axis: int) -> np.ndarray:
    """Per-column (axis=1) or per-row (axis=0) glyph-boundary edge energy."""
    d = np.abs(np.diff(gray, axis=axis))
    profile = d.sum(axis=1 - axis)
    # Remove the DC term so the comb response measures periodic structure only.
    return profile - profile.mean()


def _comb_score(profile: np.ndarray, pitch: float, phases: np.ndarray) -> np.ndarray:
    """Mean edge energy landing on the predicted lattice boundaries.

    For each candidate phase this samples `profile` at x = phase + k*pitch and
    averages. A single-frequency DFT cannot do this job: at pitch ~32 over ~2280
    samples its main lobe is ~0.45 wide in pitch, which is 30x too coarse. The
    comb sum uses every harmonic of the boundary spike train at once, so its
    optimum is sharp enough that the accumulated drift across the last cell stays
    well under one pixel. The MEAN (not the sum) keeps the score unbiased with
    respect to pitch, since the boundary count falls as pitch grows.
    """
    n = int((profile.size - float(phases.max())) // pitch) + 1
    idx = np.rint(phases[:, None] + np.arange(n)[None, :] * pitch).astype(np.int64)
    np.clip(idx, 0, profile.size - 1, out=idx)
    return profile[idx].mean(1)


def fit_axis_lattice(profile: np.ndarray, lo: float, hi: float) -> tuple[float, float]:
    """Return (pitch, phase) for one axis.

    Phase is the offset of the first lattice BOUNDARY, matching the cell-origin
    convention: cell k spans [phase + k*pitch, phase + (k+1)*pitch).
    """
    def scan(pitches, phase_step):
        best = (-np.inf, lo, 0.0)
        for pitch in pitches:
            phases = np.arange(0.0, pitch, phase_step)
            score = _comb_score(profile, pitch, phases)
            k = int(score.argmax())
            if score[k] > best[0]:
                best = (float(score[k]), float(pitch), float(phases[k]))
        return best

    _, coarse_pitch, _ = scan(np.arange(lo, hi, 0.02), 0.25)
    _, pitch, phase = scan(
        np.arange(max(lo, coarse_pitch - 0.04), min(hi, coarse_pitch + 0.04), 0.0005), 0.02)
    # The comb sum is piecewise constant in pitch (boundaries are sampled at
    # rounded integers), so its argmax sits somewhere on a plateau rather than at
    # the true value. Snapping each predicted boundary to its local energy peak
    # and least-squares refitting position = phase + k*pitch removes that
    # plateau bias and drives the drift at the last cell well under a pixel.
    return _refit_lattice(profile, pitch, phase)


def _refit_lattice(profile: np.ndarray, pitch: float, phase: float,
                   window: int = 2, rounds: int = 3) -> tuple[float, float]:
    for _ in range(rounds):
        n = int((profile.size - phase) // pitch) + 1
        ks, xs = [], []
        for k in range(max(n, 0)):
            centre = int(round(phase + k * pitch))
            lo = centre - window
            hi = centre + window + 1
            if lo < 1 or hi > profile.size - 1:
                continue
            w = profile[lo:hi]
            j = int(w.argmax())
            if w[j] <= 0:
                continue
            # Parabolic sub-sample interpolation around the local peak.
            a, b, c = profile[lo + j - 1], w[j], profile[lo + j + 1]
            denom = a - 2.0 * b + c
            delta = 0.5 * (a - c) / denom if denom != 0 else 0.0
            ks.append(k)
            xs.append(lo + j + float(np.clip(delta, -1.0, 1.0)))
        if len(ks) < 4:
            return pitch, phase
        slope, intercept = np.polyfit(np.array(ks, dtype=float), np.array(xs), 1)
        pitch, phase = float(slope), float(intercept)
    return pitch, phase % pitch if pitch > 0 else phase


def fit_grid(gray: np.ndarray, pitch_lo: float, pitch_hi: float) -> dict:
    px, phx = fit_axis_lattice(_edge_energy(gray, axis=1), pitch_lo, pitch_hi)
    py, phy = fit_axis_lattice(_edge_energy(gray, axis=0), pitch_lo, pitch_hi)
    return {"px": px, "phx": phx, "py": py, "phy": phy}


# ---------------------------------------------------------------------------
# Per-cell mask election
# ---------------------------------------------------------------------------
def _otsu_threshold(lum: np.ndarray) -> np.ndarray:
    """Per-cell Otsu threshold. lum is (n_cells, n_px)."""
    n, tot = lum.shape
    lo = lum.min(1)
    hi = lum.max(1)
    rng = hi - lo
    safe = np.where(rng > 0, rng, 1.0)
    idx = np.clip(((lum - lo[:, None]) / safe[:, None] * OTSU_BINS).astype(np.int32),
                  0, OTSU_BINS - 1)
    hist = np.zeros((n, OTSU_BINS), dtype=np.float64)
    np.add.at(hist, (np.repeat(np.arange(n), tot), idx.ravel()), 1.0)
    centres = lo[:, None] + (np.arange(OTSU_BINS) + 0.5)[None, :] * (rng / OTSU_BINS)[:, None]
    sum_total = (hist * centres).sum(1)
    w_below = np.cumsum(hist, 1)
    s_below = np.cumsum(hist * centres, 1)
    w_above = tot - w_below
    usable = (w_below > 0) & (w_above > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        m_below = s_below / np.where(w_below > 0, w_below, 1)
        m_above = (sum_total[:, None] - s_below) / np.where(w_above > 0, w_above, 1)
        variance = w_below * w_above * (m_below - m_above) ** 2
    variance = np.where(usable, variance, -1.0)
    return centres[np.arange(n), variance.argmax(1)]


def _kmeans2_threshold(lum: np.ndarray, iters: int = 12) -> np.ndarray:
    """Per-cell 1-D k-means (k=2) threshold: the midpoint of the two centroids.

    Independent of Otsu: it minimizes within-cluster L2 by Lloyd iteration on the
    raw samples instead of maximizing between-class variance over a 32-bin
    histogram. It is NOT bin-quantized, so it disagrees with Otsu on cells whose
    luminance histogram is smooth.
    """
    lo = lum.min(1)
    hi = lum.max(1)
    c0 = lo + 0.25 * (hi - lo)
    c1 = lo + 0.75 * (hi - lo)
    for _ in range(iters):
        mid = 0.5 * (c0 + c1)
        upper = lum > mid[:, None]
        n_up = upper.sum(1)
        n_dn = lum.shape[1] - n_up
        s_up = np.where(upper, lum, 0.0).sum(1)
        s_dn = lum.sum(1) - s_up
        c1 = np.where(n_up > 0, s_up / np.maximum(n_up, 1), c1)
        c0 = np.where(n_dn > 0, s_dn / np.maximum(n_dn, 1), c0)
    return 0.5 * (c0 + c1)


def _midrange_threshold(lum: np.ndarray) -> np.ndarray:
    """Per-cell midrange threshold: (min + max) / 2.

    The crudest independent estimator: a constant global rule applied per cell
    that ignores the luminance distribution entirely, so it is maximally unlike
    Otsu. If the oscillation set survives here it is not a thresholding artifact.
    """
    return 0.5 * (lum.min(1) + lum.max(1))


THRESHOLD_FN = {
    "otsu": _otsu_threshold,
    "kmeans2": _kmeans2_threshold,
    "midrange": _midrange_threshold,
    # Same Otsu threshold, OPPOSITE polarity convention. See POLARITY below.
    "otsu_dark_ink": _otsu_threshold,
}
# Which side of the threshold this method calls ink.
#
# THIS IS THE AXIS THE FIRST ROBUSTNESS ARM MISSED. otsu, kmeans2 and midrange
# disagree about WHERE to cut, but all three assume the brighter side is the
# glyph. That shared assumption is a single point of failure: when a cell keeps
# its glyph while FG and BG swap relative lightness, all three return the
# inverted mask together and all three score the unmoved glyph as a total flip.
# Three arms that share a convention are one arm with three names. `otsu_dark_ink`
# varies the convention itself, so the polarity class of error has an arm that
# can see it.
POLARITY_BRIGHT_IS_INK = {
    "otsu": True,
    "kmeans2": True,
    "midrange": True,
    "otsu_dark_ink": False,
}
# Which threshold rule each method uses, so the two axes can be separated. Two
# methods sharing a rule and differing only in convention isolate the polarity
# axis exactly; comparing across rules would conflate the two.
THRESHOLD_RULE = {
    "otsu": "otsu",
    "kmeans2": "kmeans2",
    "midrange": "midrange",
    "otsu_dark_ink": "otsu",
}


# ---------------------------------------------------------------------------
# Frame extraction
# ---------------------------------------------------------------------------
class Lattice:
    """Cell lattice bound to one capture size."""

    def __init__(self, grid: dict, width: int, height: int, titlebar_px: int):
        self.px = float(grid["px"])
        self.phx = float(grid["phx"])
        self.py = float(grid["py"])
        self.phy = float(grid["phy"])
        # Integer sample box per cell. The lattice pitch is fractional, so the
        # box is the pitch rounded to the published cell size and the origin is
        # the rounded lattice boundary: every cell samples the same number of
        # pixels, which is what makes per-cell masks comparable across frames and
        # across cells. Rounding (not flooring) keeps the box equal to the glyph
        # cell the renderer actually publishes; neighbouring boxes may overlap by
        # one pixel where the fractional pitch falls short, which is preferable to
        # dropping a column of every glyph.
        self.box_w = int(np.rint(self.px))
        self.box_h = int(np.rint(self.py))
        self.cols = int((width - self.phx) // self.px)
        self.rows = int((height - self.phy) // self.py)
        self.xs = np.round(self.phx + np.arange(self.cols) * self.px).astype(int)
        self.ys = np.round(self.phy + np.arange(self.rows) * self.py).astype(int)
        # Drop any trailing cell whose sample box would run past the capture.
        while self.cols and self.xs[self.cols - 1] + self.box_w > width:
            self.cols -= 1
        while self.rows and self.ys[self.rows - 1] + self.box_h > height:
            self.rows -= 1
        self.xs = self.xs[:self.cols]
        self.ys = self.ys[:self.rows]
        # First fully-published row: the window title bar is capture chrome, not
        # renderer output.
        self.row0 = max(int(np.ceil((titlebar_px - self.phy) / self.py)), 0)

    def as_dict(self) -> dict:
        return {
            "pitch_x": self.px, "pitch_y": self.py,
            "phase_x": self.phx, "phase_y": self.phy,
            "cols": self.cols, "rows": self.rows,
            "cells_total": self.cols * self.rows,
            "sample_box_px": [self.box_w, self.box_h],
            "first_published_row": self.row0,
        }

    def cell_pixels(self, img: np.ndarray) -> np.ndarray:
        """(rows, cols, box_h, box_w, 3) float32 view of every cell."""
        out = np.empty((self.rows, self.cols, self.box_h, self.box_w, 3), dtype=np.float32)
        for r, y in enumerate(self.ys):
            for c, x in enumerate(self.xs):
                out[r, c] = img[y:y + self.box_h, x:x + self.box_w]
        return out


def load_frames(paths: list[Path]) -> list[np.ndarray]:
    frames = []
    for p in paths:
        frames.append(np.asarray(Image.open(p).convert("RGB")).astype(np.float32))
    shapes = {f.shape for f in frames}
    if len(shapes) != 1:
        raise SystemExit(f"ABORT: frames differ in size: {sorted(shapes)}")
    return frames


def extract_frame(img: np.ndarray, lat: Lattice, method: str) -> dict:
    cells = lat.cell_pixels(img)
    n_px = lat.box_h * lat.box_w
    flat = cells.reshape(lat.rows * lat.cols, n_px, 3)
    lum = (flat * LUMA).sum(-1)
    spread = lum.max(1) - lum.min(1)
    has_contrast = spread >= GLYPH_CONTRAST_MIN_SPREAD
    thr = THRESHOLD_FN[method](lum)
    # The polarity convention lives here and nowhere else, so an arm can vary it
    # without touching the threshold rule.
    mask = (lum > thr[:, None]) if POLARITY_BRIGHT_IS_INK[method] else (lum < thr[:, None])
    fg_n = mask.sum(1)
    bg_n = n_px - fg_n
    fg = np.where(fg_n[:, None] > 0,
                  (flat * mask[..., None]).sum(1) / np.maximum(fg_n, 1)[:, None], np.nan)
    bg = np.where(bg_n[:, None] > 0,
                  (flat * (~mask)[..., None]).sum(1) / np.maximum(bg_n, 1)[:, None], np.nan)
    shape = (lat.rows, lat.cols)
    return {
        "mask": mask.reshape(*shape, lat.box_h, lat.box_w),
        "valid": has_contrast.reshape(shape),
        "fg": fg.reshape(*shape, 3),
        "bg": bg.reshape(*shape, 3),
        "coverage": (fg_n / float(n_px)).reshape(shape),
    }


def mask_iou(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    inter = (a & b).sum((-1, -2)).astype(np.float64)
    union = (a | b).sum((-1, -2)).astype(np.float64)
    # Two empty masks are identical, not undefined.
    return np.where(union > 0, inter / np.maximum(union, 1), 1.0)


# ---------------------------------------------------------------------------
# Elected glyph identity and the GLYPH CHANGE DELTA
# ---------------------------------------------------------------------------
# WHY THIS IS A SEPARATE LANE FROM MASK IoU.
#
# `mask_iou` is a CONTINUOUS overlap score. It answers "how much did the ink
# move", and it answers it with a real number that a threshold has to interpret.
# It cannot answer the question the operator actually asked, which is DISCRETE:
# did this cell elect the SAME glyph in frame N+1 that it elected in frame N,
# yes or no. Two cells can both score IoU 0.62 while one of them holds its glyph
# (sub-pixel resampling of the same shape) and the other swapped '#' for '%'.
# Folding that into a mean IoU destroys exactly the fact under proof.
#
# ELECTION RULE. The elected glyph of a cell in a frame IS its binary ink
# bitmap, so its identity is a hash of that bitmap. Equality of ids is EXACT
# bitmap equality; there is no tolerance and no threshold. FNV-1a over the
# packed bits is used because it is a stated, dependency-free, byte-order-stable
# rule that this file can restate — not because the hash is cryptographic.
#
# POLARITY. A luminance threshold cannot distinguish a glyph change from an
# FG/BG lightness swap (see the polarity_flip block in `measure`). The
# polarity-invariant id is therefore min(hash(mask), hash(NOT mask)): a cell
# whose glyph held while its two colours swapped roles keeps ONE id under this
# rule, and the swap stays visible in the colour channel where it belongs.
# `min` is used rather than "pick the sparser half" because the sparser-half
# rule is ambiguous at exactly 50% coverage, and an ambiguous id would report a
# phantom glyph change.
#
# PROVENANCE BOUNDARY. This id is elected from PIXELS. It is not the Product's
# own glyph id. When `--product-dump` supplies one final_render_cell_dump per
# frame the receipt's `measurement_path` switches to `product_glyph_id` and that
# record is the authority; this lane is the fallback and is labelled as one.
GLYPH_ID_FNV_OFFSET = np.uint64(0xCBF29CE484222325)
GLYPH_ID_FNV_PRIME = np.uint64(0x100000001B3)


def _fnv1a_over_bytes(packed: np.ndarray) -> np.ndarray:
    """FNV-1a over the last axis of a uint8 array, vectorised over all leading axes."""
    acc = np.full(packed.shape[:-1], GLYPH_ID_FNV_OFFSET, dtype=np.uint64)
    with np.errstate(over="ignore"):
        for byte_index in range(packed.shape[-1]):
            acc = (acc ^ packed[..., byte_index].astype(np.uint64)) * GLYPH_ID_FNV_PRIME
    return acc


def glyph_ids(masks: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (raw_id, polarity_invariant_id) per frame per cell.

    `masks` is (frames, rows, cols, box_h, box_w) boolean. Both outputs are
    (frames, rows, cols) uint64. Equality of ids is exact bitmap equality.
    """
    lead = masks.shape[:-2]
    flat = masks.reshape(*lead, -1)
    straight = _fnv1a_over_bytes(np.packbits(flat, axis=-1))
    inverted = _fnv1a_over_bytes(np.packbits(~flat, axis=-1))
    return straight, np.minimum(straight, inverted)


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------
def measure(frames: list[np.ndarray], lat: Lattice, method: str, hud_rows: int) -> dict:
    per_frame = [extract_frame(f, lat, method) for f in frames]
    n = len(per_frame)
    masks = np.stack([p["mask"] for p in per_frame])
    fgs = np.stack([p["fg"] for p in per_frame])
    bgs = np.stack([p["bg"] for p in per_frame])

    # A cell is measurable only where every frame published glyph contrast:
    # a cell that is flat in one frame has no mask to compare there.
    valid = np.stack([p["valid"] for p in per_frame]).all(0)
    valid[:lat.row0, :] = False
    viewport = valid.copy()
    viewport[lat.row0:lat.row0 + hud_rows, :] = False

    # RAW vs POLARITY-INVARIANT, measured side by side and both reported.
    #
    # The raw IoU is the historical reading and stays the reproducible baseline.
    # It is also WRONG about one class of cell: a cell that keeps its glyph while
    # FG and BG swap relative lightness yields an inverted extracted mask and
    # scores IoU near zero for a glyph that never moved. The polarity-invariant
    # score max(IoU(a,b), IoU(a,~b)) is blind to that swap, and the swap itself
    # is kept as its own first-class metric instead of vanishing into a
    # correction — it is a real visible defect, just a COLOUR one, not a mask one.
    iou = {}
    iou_pinv = {}
    flip = {}
    for a in range(n):
        for b in range(a + 1, n):
            straight = mask_iou(masks[a], masks[b])
            inverted = mask_iou(masks[a], ~masks[b])
            iou[(a, b)] = straight
            iou_pinv[(a, b)] = np.maximum(straight, inverted)
            flip[(a, b)] = inverted > straight

    consecutive = np.stack([iou[(i, i + 1)] for i in range(n - 1)])
    consecutive_pinv = np.stack([iou_pinv[(i, i + 1)] for i in range(n - 1)])
    polarity_flip = np.stack([flip[(i, i + 1)] for i in range(n - 1)])

    # --- GLYPH CHANGE DELTA lane (discrete, distinct from the IoU lane) -----
    # One elected glyph id per cell per frame; the delta is exact id inequality
    # between consecutive frames. Shapes: ids (n, rows, cols), changed
    # (n-1, rows, cols).
    gid, gid_pinv = glyph_ids(masks)
    glyph_changed = gid[1:] != gid[:-1]
    glyph_changed_pinv = gid_pinv[1:] != gid_pinv[:-1]

    d_fg = np.linalg.norm(fgs[1:] - fgs[:-1], axis=-1)
    d_bg = np.linalg.norm(bgs[1:] - bgs[:-1], axis=-1)
    # Polarity-ALIGNED colour deltas: where the polarity flipped, the two
    # published colours swapped roles, so the honest comparison is fg(a) against
    # bg(b). This is what moves the inversion into the colour channel: an
    # inverted cell whose two colours are otherwise unchanged shows a small
    # aligned delta and a large straight delta, and the difference between them
    # IS the swap.
    d_fg_aligned = np.where(polarity_flip,
                            np.linalg.norm(bgs[1:] - fgs[:-1], axis=-1), d_fg)
    d_bg_aligned = np.where(polarity_flip,
                            np.linalg.norm(fgs[1:] - bgs[:-1], axis=-1), d_bg)

    # Oscillation: any (N, N+1, N+2) triple that violates monotone decay.
    # Computed under both readings; the polarity-invariant one is the corrected
    # count, the raw one stays the historical baseline.
    def _oscillation(table):
        hits = np.zeros((lat.rows, lat.cols), dtype=bool)
        per_triple = []
        for t in range(n - 2):
            hit = ((table[(t, t + 2)] >= table[(t, t + 1)] + OSCILLATION_MARGIN)
                   & (table[(t, t + 2)] >= table[(t + 1, t + 2)] + OSCILLATION_MARGIN))
            hits |= hit
            per_triple.append({"triple": [t, t + 1, t + 2],
                               "flagged_cells": int((hit & valid).sum()),
                               "flagged_cells_viewport": int((hit & viewport).sum())})
        return (hits & valid), per_triple

    osc, osc_triples = _oscillation(iou)
    osc_pinv, osc_triples_pinv = _oscillation(iou_pinv)

    pixels_changed = [
        float((np.abs(frames[i + 1] - frames[i]).sum(-1) > PIXEL_CHANGED_SUM_ABS_MIN).mean())
        for i in range(n - 1)
    ]

    vp = np.broadcast_to(viewport, consecutive.shape)
    io_v = consecutive[vp]
    io_p = consecutive_pinv[vp]
    fl_v = polarity_flip[vp]
    fg_v = np.nan_to_num(d_fg[vp])
    bg_v = np.nan_to_num(d_bg[vp])
    fg_a = np.nan_to_num(d_fg_aligned[vp])
    bg_a = np.nan_to_num(d_bg_aligned[vp])
    coverage = np.stack([p["coverage"] for p in per_frame])
    balanced = ((coverage >= POLARITY_BALANCED_COVERAGE_MIN)
                & (coverage <= POLARITY_BALANCED_COVERAGE_MAX))
    balanced_pairs = (balanced[:-1] & balanced[1:])[vp]
    gc_v = glyph_changed[vp]
    gc_p = glyph_changed_pinv[vp]

    rollup = {
        "cells_total": lat.rows * lat.cols,
        "cells_with_glyph_contrast_all_frames": int(valid.sum()),
        "cells_in_viewport": int(viewport.sum()),
        "pixels_changed_frac_per_frame": [round(v, 4) for v in pixels_changed],
        "mean_mask_iou_consecutive": [round(float(consecutive[i][valid].mean()), 4)
                                      for i in range(n - 1)],
        "cells_mask_iou_below_0p5": [int((consecutive[i][valid] < 0.5).sum())
                                     for i in range(n - 1)],
        "oscillating_cells_total": int(osc.sum()),
        "oscillating_cells_viewport": int((osc & viewport).sum()),
        "oscillating_pct": round(100.0 * (osc & viewport).sum() / max(int(viewport.sum()), 1), 2),
        "fg_delta_mean": round(float(fg_v.mean()), 2),
        "fg_delta_median": round(float(np.median(fg_v)), 2),
        "fg_delta_gt30_pct": round(100.0 * float((fg_v > 30).mean()), 2),
        "bg_delta_mean": round(float(bg_v.mean()), 2),
        "bg_delta_median": round(float(np.median(bg_v)), 2),
        "bg_delta_gt30_pct": round(100.0 * float((bg_v > 30).mean()), 2),
        "mask_iou_mean": round(float(io_v.mean()), 3),
        "mask_iou_median": round(float(np.median(io_v)), 3),
        "mask_iou_lt0p5_pct": round(100.0 * float((io_v < 0.5).mean()), 2),
        "mask_flip_hard_colour_holds_pct": round(100.0 * float(
            ((io_v < MASK_FLIP_HARD_IOU_MAX)
             & (fg_v < COLOUR_HOLDS_DELTA_MAX)
             & (bg_v < COLOUR_HOLDS_DELTA_MAX)).mean()), 2),
        "colour_jump_mask_holds_pct": round(100.0 * float(
            ((io_v > MASK_HOLDS_IOU_MIN)
             & ((fg_v > COLOUR_JUMP_DELTA_MIN) | (bg_v > COLOUR_JUMP_DELTA_MIN))).mean()), 2),
        "oscillation_triples": osc_triples,

        # --- GLYPH CHANGE DELTA (first-class, not derived from IoU) ---------
        # `glyph_change_*` counts CELLS WHOSE ELECTED GLYPH ID CHANGED between
        # two consecutive frames. It is a set cardinality, not an average, and
        # it is the metric the operator asked for alongside the IoU lane.
        "glyph_change_count_per_pair": [
            int((glyph_changed[i] & viewport).sum()) for i in range(n - 1)],
        "glyph_change_rate_pct_per_pair": [
            round(100.0 * float((glyph_changed[i] & viewport).sum())
                  / max(int(viewport.sum()), 1), 2) for i in range(n - 1)],
        "glyph_change_pct": round(100.0 * float(gc_v.mean()), 2),
        "glyph_hold_pct": round(100.0 * float((~gc_v).mean()), 2),
        "glyph_change_cells_any_pair": int((glyph_changed.any(0) & viewport).sum()),
        "glyph_change_cells_every_pair": int((glyph_changed.all(0) & viewport).sum()),
        "glyph_change_cells_viewport_total": int(gc_v.sum()),
        "glyph_change_pairs_measured": int(vp.sum()),
        # The two lanes cross-tabulated. A non-zero
        # `glyph_change_while_mask_iou_ge_0p9_pct` is the direct evidence that
        # this lane is NOT a restatement of the IoU lane: those cells swapped
        # glyph while the overlap score still read "almost identical".
        "glyph_change_while_mask_iou_ge_0p9_pct": round(
            100.0 * float((gc_v & (io_v >= 0.9)).mean()), 2),
        "glyph_hold_while_mask_iou_lt0p5_pct": round(
            100.0 * float((~gc_v & (io_v < 0.5)).mean()), 2),
        # Channel separation for the discrete lane: the glyph changed while both
        # published colours held, so the defect is an identity defect.
        "glyph_change_colour_holds_pct": round(100.0 * float(
            (gc_v & (fg_v < COLOUR_HOLDS_DELTA_MAX)
             & (bg_v < COLOUR_HOLDS_DELTA_MAX)).mean()), 2),
        "glyph_change_count_per_pair_polarity_invariant": [
            int((glyph_changed_pinv[i] & viewport).sum()) for i in range(n - 1)],
        "glyph_change_rate_pct_per_pair_polarity_invariant": [
            round(100.0 * float((glyph_changed_pinv[i] & viewport).sum())
                  / max(int(viewport.sum()), 1), 2) for i in range(n - 1)],
        "glyph_change_pct_polarity_invariant": round(100.0 * float(gc_p.mean()), 2),
        "glyph_change_cells_any_pair_polarity_invariant": int(
            (glyph_changed_pinv.any(0) & viewport).sum()),
        # How much of the raw glyph churn was only an FG/BG lightness swap.
        "glyph_change_pct_attributable_to_polarity_swap": round(
            100.0 * float(gc_v.mean()) - 100.0 * float(gc_p.mean()), 2),

        # --- polarity-invariant reading (the corrected column) --------------
        "polarity_flip_pct_per_pair": [
            round(100.0 * float(polarity_flip[i][viewport].mean()), 2) for i in range(n - 1)],
        "polarity_flip_pct": round(100.0 * float(fl_v.mean()), 2),
        "polarity_flip_cells_any_pair": int(
            (polarity_flip.any(0) & viewport).sum()),
        # Sensitivity check on the flip test itself. IoU(a, NOT b) is favoured
        # for purely geometric reasons when the ink is sparse: two disjoint
        # thin strokes score 0 against each other but non-zero against a
        # complement that covers most of the cell. Restricting to cells whose
        # ink coverage sits in a balanced band removes that bias. A balanced
        # rate close to the headline rate means the flips are real inversions;
        # a much lower one would mean the headline is partly a sparsity artifact.
        "polarity_flip_pct_balanced_coverage": round(100.0 * float(
            fl_v[balanced_pairs].mean()) if balanced_pairs.any() else 0.0, 2),
        "polarity_flip_balanced_coverage_band": [
            POLARITY_BALANCED_COVERAGE_MIN, POLARITY_BALANCED_COVERAGE_MAX],
        "polarity_flip_balanced_pairs_measured": int(balanced_pairs.sum()),
        "mean_mask_iou_consecutive_polarity_invariant": [
            round(float(consecutive_pinv[i][valid].mean()), 4) for i in range(n - 1)],
        "cells_mask_iou_below_0p5_polarity_invariant": [
            int((consecutive_pinv[i][valid] < 0.5).sum()) for i in range(n - 1)],
        "oscillating_cells_total_polarity_invariant": int(osc_pinv.sum()),
        "oscillating_cells_viewport_polarity_invariant": int((osc_pinv & viewport).sum()),
        "oscillating_pct_polarity_invariant": round(
            100.0 * (osc_pinv & viewport).sum() / max(int(viewport.sum()), 1), 2),
        "mask_iou_mean_polarity_invariant": round(float(io_p.mean()), 3),
        "mask_iou_median_polarity_invariant": round(float(np.median(io_p)), 3),
        "mask_iou_lt0p5_pct_polarity_invariant": round(
            100.0 * float((io_p < 0.5).mean()), 2),
        # Channel split under the corrected reading. The mask channel shrinks and
        # the colour channel grows by roughly what the polarity flips were
        # contributing, which is the whole point of separating them.
        "mask_flip_hard_colour_holds_pct_polarity_invariant": round(100.0 * float(
            ((io_p < MASK_FLIP_HARD_IOU_MAX)
             & (fg_a < COLOUR_HOLDS_DELTA_MAX)
             & (bg_a < COLOUR_HOLDS_DELTA_MAX)).mean()), 2),
        "colour_jump_mask_holds_pct_polarity_invariant": round(100.0 * float(
            ((io_p > MASK_HOLDS_IOU_MIN)
             & ((fg_a > COLOUR_JUMP_DELTA_MIN) | (bg_a > COLOUR_JUMP_DELTA_MIN))).mean()), 2),
        "oscillation_triples_polarity_invariant": osc_triples_pinv,
    }

    return {
        "method": method,
        "rollup": rollup,
        "masks": masks,
        "fgs": fgs,
        "bgs": bgs,
        "coverage": np.stack([p["coverage"] for p in per_frame]),
        "valid": valid,
        "viewport": viewport,
        "oscillating": osc,
        "oscillating_polarity_invariant": osc_pinv,
        "iou": iou,
        "iou_polarity_invariant": iou_pinv,
        "polarity_flip": polarity_flip,
        "consecutive_iou": consecutive,
        "consecutive_iou_polarity_invariant": consecutive_pinv,
        "fg_delta": d_fg,
        "bg_delta": d_bg,
        "fg_delta_polarity_aligned": d_fg_aligned,
        "bg_delta_polarity_aligned": d_bg_aligned,
        "glyph_id": gid,
        "glyph_id_polarity_invariant": gid_pinv,
        "glyph_changed": glyph_changed,
        "glyph_changed_polarity_invariant": glyph_changed_pinv,
    }


def cell_record(res: dict, row: int, col: int, n_frames: int) -> dict:
    """Full per-frame / per-pair record for one named cell."""
    rec = {
        "row": row, "col": col,
        "valid": bool(res["valid"][row, col]),
        "in_viewport": bool(res["viewport"][row, col]),
        "oscillating": bool(res["oscillating"][row, col]),
        "oscillating_polarity_invariant": bool(
            res["oscillating_polarity_invariant"][row, col]),
        "per_frame": [],
        "per_pair": [],
    }
    for f in range(n_frames):
        fg = res["fgs"][f, row, col]
        bg = res["bgs"][f, row, col]
        rec["per_frame"].append({
            "frame": f,
            "fg_rgb": None if np.isnan(fg).any() else [round(float(v), 1) for v in fg],
            "bg_rgb": None if np.isnan(bg).any() else [round(float(v), 1) for v in bg],
            "mask_coverage": round(float(res["coverage"][f, row, col]), 4),
            # Hex so the id survives JSON without float rounding: uint64 exceeds
            # the exact-integer range of a JSON number in several readers.
            "glyph_id": "%016x" % int(res["glyph_id"][f, row, col]),
            "glyph_id_polarity_invariant": "%016x" % int(
                res["glyph_id_polarity_invariant"][f, row, col]),
        })
    for a in range(n_frames):
        for b in range(a + 1, n_frames):
            rec["per_pair"].append({
                "pair": [a, b],
                "mask_iou": round(float(res["iou"][(a, b)][row, col]), 4),
                "mask_iou_polarity_invariant": round(
                    float(res["iou_polarity_invariant"][(a, b)][row, col]), 4),
                "polarity_flip": bool(res["polarity_flip"][a, row, col])
                                 if b == a + 1 else None,
                # The discrete lane, per pair: exact elected-glyph inequality.
                "glyph_changed": (bool(res["glyph_changed"][a, row, col])
                                  if b == a + 1 else
                                  bool(res["glyph_id"][a, row, col]
                                       != res["glyph_id"][b, row, col])),
                "glyph_changed_polarity_invariant": (
                    bool(res["glyph_changed_polarity_invariant"][a, row, col])
                    if b == a + 1 else
                    bool(res["glyph_id_polarity_invariant"][a, row, col]
                         != res["glyph_id_polarity_invariant"][b, row, col])),
                "fg_delta": (round(float(res["fg_delta"][a, row, col]), 2)
                             if b == a + 1 else None),
                "bg_delta": (round(float(res["bg_delta"][a, row, col]), 2)
                             if b == a + 1 else None),
                "fg_delta_polarity_aligned": (
                    round(float(res["fg_delta_polarity_aligned"][a, row, col]), 2)
                    if b == a + 1 else None),
                "bg_delta_polarity_aligned": (
                    round(float(res["bg_delta_polarity_aligned"][a, row, col]), 2)
                    if b == a + 1 else None),
            })
    return rec


# ---------------------------------------------------------------------------
# Baseline comparison
# ---------------------------------------------------------------------------
# Tolerances are per metric family and are stated, not implied. Integer cell
# counts must match EXACTLY: they are set cardinalities, and any difference
# means the two extractors elected different masks.
TOLERANCE = {
    "exact_int": 0,
    "fraction": 0.001,     # pixels-changed fractions
    "iou_mean": 0.02,      # mean/median IoU
    "delta": 1.0,          # FG/BG colour delta means and medians
    "percent": 1.0,        # any percentage metric, in percentage points
}
BASELINE_FIELDS = [
    ("cells_with_glyph_contrast_all_frames", "exact_int",
     ("grid", "cells_with_glyph_contrast_all_frames")),
    ("pixels_changed_frac_per_frame", "fraction",
     ("measurements", "pixels_changed_frac_per_frame")),
    ("mean_mask_iou_consecutive", "iou_mean",
     ("measurements", "mean_mask_iou_consecutive")),
    ("cells_mask_iou_below_0p5", "exact_int",
     ("measurements", "cells_mask_iou_below_0p5")),
    ("oscillating_cells_total", "exact_int", ("measurements", "oscillating_cells_total")),
    ("oscillating_cells_viewport", "exact_int", ("measurements", "oscillating_cells_viewport")),
    ("oscillating_pct", "percent", ("measurements", "oscillating_pct")),
    ("fg_delta_mean", "delta", ("measurements", "fg_delta_mean")),
    ("fg_delta_median", "delta", ("measurements", "fg_delta_median")),
    ("fg_delta_gt30_pct", "percent", ("measurements", "fg_delta_gt30_pct")),
    ("bg_delta_mean", "delta", ("measurements", "bg_delta_mean")),
    ("bg_delta_median", "delta", ("measurements", "bg_delta_median")),
    ("bg_delta_gt30_pct", "percent", ("measurements", "bg_delta_gt30_pct")),
    ("mask_iou_mean", "iou_mean", ("measurements", "mask_iou_mean")),
    ("mask_iou_median", "iou_mean", ("measurements", "mask_iou_median")),
    ("mask_iou_lt0p5_pct", "percent", ("measurements", "mask_iou_lt0p5_pct")),
    ("mask_flip_hard_colour_holds_pct", "percent",
     ("measurements", "mask_flip_hard_colour_holds_pct")),
    ("colour_jump_mask_holds_pct", "percent",
     ("measurements", "colour_jump_mask_holds_pct")),
]
# The corrected column. Both columns are kept and both are checked: the raw one
# remains the reproducible historical baseline, and the polarity-invariant one
# is the reading that survives an FG/BG lightness swap. A run that reproduces
# only one of them has not reproduced the measurement.
BASELINE_FIELDS_POLARITY = [
    ("mean_mask_iou_consecutive_polarity_invariant", "iou_mean",
     ("polarity_invariant", "mean_mask_iou_consecutive")),
    ("cells_mask_iou_below_0p5_polarity_invariant", "exact_int",
     ("polarity_invariant", "cells_mask_iou_below_0p5")),
    ("oscillating_cells_total_polarity_invariant", "exact_int",
     ("polarity_invariant", "oscillating_cells_total")),
    ("polarity_flip_pct_per_pair", "percent",
     ("polarity_invariant", "polarity_flip_pct_per_pair")),
    ("mask_flip_hard_colour_holds_pct_polarity_invariant", "percent",
     ("polarity_invariant", "mask_flip_hard_colour_holds_pct")),
    ("colour_jump_mask_holds_pct_polarity_invariant", "percent",
     ("polarity_invariant", "colour_jump_mask_holds_pct")),
]


def polarity_baseline_from_corrected_set(doc: dict) -> dict:
    """Adapt the machine-readable corrected set into the baseline shape.

    Accepts the `FL4359_polarity_corrected.json` layout (a `consecutive` list of
    per-pair rows plus `osc_raw` / `osc_pinv`) so the corrected column can be
    supplied without hand-transcribing it into a second file.
    """
    rows = doc.get("consecutive")
    if not rows:
        return {}
    out = {
        "mean_mask_iou_consecutive": [r["pinv_mean"] for r in rows],
        "cells_mask_iou_below_0p5": [r["pinv_lt05"] for r in rows],
        "polarity_flip_pct_per_pair": [r["polarity_flip_pct"] for r in rows],
    }
    if "osc_pinv" in doc:
        out["oscillating_cells_total"] = doc["osc_pinv"]
    # The corrected channel split was stated in the review that supplied this
    # set but is not carried in the JSON itself. Defaulted here with that
    # provenance so the corrected column covers both channels; a doc that
    # carries its own values always wins.
    for key, stated in (("mask_flip_hard_colour_holds_pct",
                         POLARITY_CORRECTED_MASK_FLIP_HARD_PCT),
                        ("colour_jump_mask_holds_pct",
                         POLARITY_CORRECTED_COLOUR_JUMP_PCT)):
        out[key] = doc.get(key, stated)
    return out


def _dig(doc: dict, path: tuple):
    node = doc
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def compare_baseline(rollup: dict, baseline: dict, count_rel_tol: float = 0.0) -> dict:
    """Compare a measured roll-up with a recorded baseline.

    `count_rel_tol` widens the SET CARDINALITY metrics only. It must stay 0 when
    the lattice is pinned to the baseline's own grid: with the identical lattice
    the elected mask sets are identical, so any count difference is a real
    extractor divergence. A comb-FIT lattice lands on a slightly different
    sub-pixel phase, which reassigns a small number of borderline cells; that is
    a lattice difference, not an extractor difference, and it gets a stated
    relative allowance rather than passing unremarked as exact.
    """
    rows = []
    fields = list(BASELINE_FIELDS)
    # Only check the corrected column when the baseline actually supplies it, so
    # an older single-column baseline still passes on its own terms instead of
    # failing on fields it never had.
    if _dig(baseline, ("polarity_invariant",)):
        fields += BASELINE_FIELDS_POLARITY
    for name, kind, path in fields:
        want = _dig(baseline, path)
        got = rollup.get(name)
        if want is None or got is None:
            rows.append({"metric": name, "baseline": want, "measured": got,
                         "tolerance_kind": kind, "result": "ABSENT"})
            continue
        w = want if isinstance(want, list) else [want]
        g = got if isinstance(got, list) else [got]
        if len(w) != len(g):
            rows.append({"metric": name, "baseline": want, "measured": got,
                         "tolerance_kind": kind, "result": "SHAPE_MISMATCH"})
            continue
        if kind == "exact_int":
            tol = max((count_rel_tol * abs(float(a)) for a in w), default=0.0)
        else:
            tol = TOLERANCE[kind]
        worst = max(abs(float(a) - float(b)) for a, b in zip(w, g))
        rows.append({"metric": name, "baseline": want, "measured": got,
                     "tolerance_kind": kind, "tolerance": round(tol, 5),
                     "worst_abs_delta": round(worst, 5),
                     "result": "PASS" if worst <= tol else "FAIL"})
    return {
        "tolerances": TOLERANCE,
        "count_relative_tolerance": count_rel_tol,
        "fields": rows,
        "failed": [r["metric"] for r in rows if r["result"] != "PASS"],
        "result": "PASS" if all(r["result"] == "PASS" for r in rows) else "FAIL",
    }


# ---------------------------------------------------------------------------
# Pose identity
# ---------------------------------------------------------------------------
def bind_poses(pose_doc: dict | None, n_frames: int) -> dict:
    """Bind one pose to every captured frame, or record exactly which lack one.

    FL-4359 identity rule: a frame with no recorded pose is NOT proof-usable,
    because the falsifier depends on the motion being monotone translation with
    held orientation, and that can only be asserted from the pose actually in
    effect when the frame was published. This never invents a pose.
    """
    supplied = (pose_doc or {}).get("choreography") or (pose_doc or {}).get("poses") or []
    per_frame = []
    for i in range(n_frames):
        match = next((p for p in supplied if int(p.get("frame", -1)) - 1 == i), None)
        per_frame.append({"frame": i, "pose": match})
    covered = sum(1 for p in per_frame if p["pose"] is not None)
    orientations = {(p["pose"].get("yaw"), p["pose"].get("pitch"))
                    for p in per_frame if p["pose"]}
    return {
        "frames": n_frames,
        "poses_supplied": len(supplied),
        "frames_with_pose": covered,
        "pose_identity_closed": covered == n_frames,
        "unbound_frames": [p["frame"] for p in per_frame if p["pose"] is None],
        "orientation_held": len(orientations) <= 1,
        "per_frame": per_frame,
        "reason": (None if covered == n_frames else
                   "capture supplied fewer poses than frames; the capture harness must write "
                   "one pose per published frame (see fl4359_cell_dump_airplane_proof.py "
                   "plan: poses.json) before this run can carry a monotone-translation claim"),
    }


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="FL-4359 per-cell frame-indexed cell dump extractor")
    ap.add_argument("--frames", type=Path, required=True,
                    help="directory of capture frames (f*.png, sorted) or a GIF file")
    ap.add_argument("--frame-glob", default="f*.png")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--arm-label", default="unlabelled")
    ap.add_argument("--grid-json", type=Path, default=None,
                    help="pin a previously fitted lattice instead of comb-fitting")
    ap.add_argument("--pitch-lo", type=float, default=24.0)
    ap.add_argument("--pitch-hi", type=float, default=48.0)
    ap.add_argument("--titlebar-px", type=int, default=DEFAULT_TITLEBAR_PX)
    ap.add_argument("--hud-rows", type=int, default=DEFAULT_HUD_ROWS)
    ap.add_argument("--mask-method", action="append", choices=MASK_METHODS, default=None,
                    help="repeatable; first is primary. Default: otsu kmeans2 midrange")
    ap.add_argument("--pose-json", type=Path, default=None)
    ap.add_argument("--product-dump", action="append", default=None,
                    help="final_render_cell_dump directory for one frame (repeatable, in "
                         "frame order). Supplying these switches the comparison to exact "
                         "Product glyph-id equality and removes mask inference entirely.")
    ap.add_argument("--baseline", type=Path, default=None)
    ap.add_argument("--polarity-baseline", type=Path, default=None,
                    help="FL4359_polarity_corrected.json; adds the corrected column to the "
                         "baseline check. Both columns are then required to reproduce.")
    ap.add_argument("--comb-fit-count-tolerance", type=float, default=0.03,
                    help="relative allowance on set-cardinality baseline metrics when the "
                         "lattice is comb-fit rather than pinned (default 0.03). Pinning the "
                         "baseline grid forces exact count agreement.")
    ap.add_argument("--cell", action="append", default=None,
                    help="ROW,COL to dump in full into the receipt (repeatable)")
    ap.add_argument("--top-cells", type=int, default=25,
                    help="also dump the N most strongly oscillating cells")
    ap.add_argument("--save-npz", action="store_true", default=True)
    ap.add_argument("--no-save-npz", dest="save_npz", action="store_false")
    args = ap.parse_args(argv)

    methods = args.mask_method or list(MASK_METHODS)

    if args.frames.is_file():
        gif = Image.open(args.frames)
        frames = []
        for i in range(getattr(gif, "n_frames", 1)):
            gif.seek(i)
            frames.append(np.asarray(gif.convert("RGB")).astype(np.float32))
        frame_paths = [str(args.frames) + f"#{i}" for i in range(len(frames))]
    else:
        paths = sorted(args.frames.glob(args.frame_glob))
        if not paths:
            print(f"ABORT: no frames matched {args.frames}/{args.frame_glob}", file=sys.stderr)
            return 1
        frames = load_frames(paths)
        frame_paths = [str(p) for p in paths]
    if len(frames) < 3:
        print("ABORT: the oscillation falsifier needs at least 3 frames", file=sys.stderr)
        return 1

    height, width, _ = frames[0].shape
    gray = (frames[0] * LUMA).sum(-1).astype(np.float64)
    if args.grid_json:
        grid = json.loads(args.grid_json.read_text())
        grid_source = f"pinned:{args.grid_json}"
    else:
        grid = fit_grid(gray, args.pitch_lo, args.pitch_hi)
        grid_source = "comb_fit"
    lat = Lattice(grid, width, height, args.titlebar_px)

    args.out.mkdir(parents=True, exist_ok=True)

    pose_doc = json.loads(args.pose_json.read_text()) if args.pose_json else None
    poses = bind_poses(pose_doc, len(frames))

    # Preferred evidence path. When a Product dump is supplied there is no mask
    # to infer and therefore no polarity to get wrong.
    product_report = None
    if args.product_dump:
        product = load_product_frames([Path(p) for p in args.product_dump])
        product_report = compare_product_frames(product["frames"])
        product_report["sources"] = [f["source"] for f in product["frames"]]
        product_report["untested_against_live_capture"] = True

    results = {m: measure(frames, lat, m, args.hud_rows) for m in methods}
    primary = results[methods[0]]

    named = []
    for spec in (args.cell or []):
        r, c = (int(v) for v in spec.split(","))
        named.append(cell_record(primary, r, c, len(frames)))
    if args.top_cells:
        # Rank by how far the strongest triple violates monotone decay.
        strength = np.full((lat.rows, lat.cols), -1.0)
        for t in range(len(frames) - 2):
            m = np.minimum(primary["iou"][(t, t + 2)] - primary["iou"][(t, t + 1)],
                           primary["iou"][(t, t + 2)] - primary["iou"][(t + 1, t + 2)])
            strength = np.maximum(strength, m)
        strength = np.where(primary["viewport"], strength, -1.0)
        for k in np.argsort(strength.ravel())[::-1][:args.top_cells]:
            r, c = divmod(int(k), lat.cols)
            if strength[r, c] <= 0:
                break
            rec = cell_record(primary, r, c, len(frames))
            rec["monotone_violation_margin"] = round(float(strength[r, c]), 4)
            named.append(rec)

    robustness = {
        "primary_method": methods[0],
        "axes_varied": {
            "threshold_rule": sorted({m for m in methods}),
            "polarity_convention": {m: ("bright_is_ink" if POLARITY_BRIGHT_IS_INK[m]
                                        else "dark_is_ink") for m in methods},
        },
        "methods": {m: {
            "polarity_convention": ("bright_is_ink" if POLARITY_BRIGHT_IS_INK[m]
                                    else "dark_is_ink"),
            "oscillating_cells_total": results[m]["rollup"]["oscillating_cells_total"],
            "oscillating_cells_viewport": results[m]["rollup"]["oscillating_cells_viewport"],
            "oscillating_pct": results[m]["rollup"]["oscillating_pct"],
            "oscillating_cells_viewport_polarity_invariant":
                results[m]["rollup"]["oscillating_cells_viewport_polarity_invariant"],
            "mask_iou_mean": results[m]["rollup"]["mask_iou_mean"],
            "mask_iou_mean_polarity_invariant":
                results[m]["rollup"]["mask_iou_mean_polarity_invariant"],
            "polarity_flip_pct": results[m]["rollup"]["polarity_flip_pct"],
            # The discrete lane is threshold-dependent too, so it is reported
            # per method rather than only for the primary one.
            "glyph_change_pct": results[m]["rollup"]["glyph_change_pct"],
            "glyph_change_pct_polarity_invariant":
                results[m]["rollup"]["glyph_change_pct_polarity_invariant"],
            "glyph_change_cells_any_pair":
                results[m]["rollup"]["glyph_change_cells_any_pair"],
        } for m in methods},
    }
    counts = [robustness["methods"][m]["oscillating_cells_viewport"] for m in methods]
    ref = counts[0]
    # "Collapse" = an alternative estimator finds under a quarter of the primary
    # set. That is the stated falsification threshold for the finding.
    robustness["collapse_threshold_frac_of_primary"] = 0.25
    robustness["min_alternative_frac_of_primary"] = (
        round(min(counts[1:]) / ref, 4) if len(counts) > 1 and ref else None)
    robustness["finding"] = (
        "NOT_EVALUATED" if len(counts) < 2 else
        "FALSIFIED_EXTRACTOR_ARTIFACT" if min(counts[1:]) < 0.25 * max(ref, 1) else
        "CONFIRMED_ACROSS_INDEPENDENT_EXTRACTORS")
    # Isolate the polarity axis. Two methods that share a threshold rule and
    # differ only in convention are a clean invariance test; comparing across
    # rules would mix in the threshold axis and mean nothing.
    pinv_counts = {m: robustness["methods"][m][
        "oscillating_cells_viewport_polarity_invariant"] for m in methods}
    robustness["polarity_invariant_count_spread_all_methods"] = int(
        max(pinv_counts.values()) - min(pinv_counts.values())) if pinv_counts else 0
    pairs = []
    for rule in sorted({THRESHOLD_RULE[m] for m in methods}):
        same = [m for m in methods if THRESHOLD_RULE[m] == rule]
        if len({POLARITY_BRIGHT_IS_INK[m] for m in same}) < 2:
            continue
        counts = [pinv_counts[m] for m in same]
        pairs.append({
            "threshold_rule": rule, "methods": same,
            "polarity_invariant_counts": counts,
            "spread": int(max(counts) - min(counts)),
            "spread_frac_of_max": round((max(counts) - min(counts)) / max(max(counts), 1), 4),
            "raw_counts": [robustness["methods"][m]["oscillating_cells_viewport"]
                           for m in same],
        })
    robustness["polarity_isolated_pairs"] = pairs
    conventions = {POLARITY_BRIGHT_IS_INK[m] for m in methods}
    robustness["polarity_axis_covered"] = len(conventions) > 1
    robustness["polarity_axis_note"] = (
        "the first robustness arm varied only the threshold rule; all of otsu, "
        "kmeans2 and midrange assume bright pixels are ink, so a polarity "
        "inversion fooled all three together. otsu_dark_ink varies the "
        "convention itself."
        if robustness["polarity_axis_covered"] else
        "NO polarity-varying method selected: this run cannot detect a polarity "
        "artifact, and its oscillation count must not be read as convention-independent")

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "arm_label": args.arm_label,
        "capture": {
            "frames": len(frames),
            "frame_paths": frame_paths,
            "capture_px": [width, height],
            "titlebar_px": args.titlebar_px,
            "hud_rows_excluded_from_viewport": args.hud_rows,
        },
        "grid": {**lat.as_dict(), "source": grid_source},
        "pose_identity": poses,
        "definitions": {
            "glyph_contrast_min_spread": GLYPH_CONTRAST_MIN_SPREAD,
            "pixel_changed_sum_abs_min": PIXEL_CHANGED_SUM_ABS_MIN,
            "oscillation_margin": OSCILLATION_MARGIN,
            "oscillation_rule": ("IoU(N,N+2) >= IoU(N,N+1)+margin AND "
                                 "IoU(N,N+2) >= IoU(N+1,N+2)+margin"),
            "oscillation_rationale": ("under monotone translation with held orientation, mask "
                                      "similarity must decay monotonically with frame gap, so "
                                      "this condition cannot be produced by camera motion"),
            "mask_flip_hard_colour_holds": (f"mask_iou < {MASK_FLIP_HARD_IOU_MAX} and "
                                            f"fg_delta < {COLOUR_HOLDS_DELTA_MAX} and "
                                            f"bg_delta < {COLOUR_HOLDS_DELTA_MAX}"),
            "colour_jump_mask_holds": (f"mask_iou > {MASK_HOLDS_IOU_MIN} and "
                                       f"(fg_delta > {COLOUR_JUMP_DELTA_MIN} or "
                                       f"bg_delta > {COLOUR_JUMP_DELTA_MIN})"),
            "colour_delta": "euclidean RGB distance between consecutive per-cell mean colours",
            "viewport": "valid cells minus the HUD rows immediately below the title bar",
            "polarity_flip": ("IoU(a, NOT b) > IoU(a, b): the extracted mask inverted "
                              "between frames. A luminance threshold cannot tell a glyph "
                              "change from an FG/BG lightness swap, so this is reported as "
                              "its own metric rather than folded into mask churn"),
            "mask_iou_polarity_invariant": "max(IoU(a, b), IoU(a, NOT b))",
            "elected_glyph_id": (
                "FNV-1a over the packed ink bitmap of one cell in one frame. "
                "Two cells hold the same glyph exactly when the ids are equal; "
                "there is no tolerance and no threshold"),
            "elected_glyph_id_polarity_invariant": (
                "min(hash(mask), hash(NOT mask)), so an FG/BG lightness swap "
                "that preserves the glyph does not read as a glyph change"),
            "glyph_change_delta": (
                "DISCRETE per-cell metric, reported separately from mask IoU: "
                "the elected glyph id of a cell differs between consecutive "
                "frames. Reported as a per-frame-pair count and rate, a "
                "whole-run percentage, and the any-pair/every-pair cell sets. "
                "IoU is a continuous overlap score and cannot answer this "
                "question; `glyph_change_while_mask_iou_ge_0p9_pct` is the "
                "cross-tab that shows the two lanes are not redundant"),
            "glyph_change_lane_provenance": (
                "elected from PIXELS by the threshold rule named in "
                "`mask_method`. It is not the Product's own glyph id. With "
                "--product-dump the receipt's measurement_path becomes "
                "product_glyph_id and that record is the authority"),
            "colour_delta_polarity_aligned": ("on a flipped pair the two published colours "
                                              "swapped roles, so fg(a) is compared with "
                                              "bg(b) and bg(a) with fg(b). This is what "
                                              "puts the inversion in the COLOUR channel"),
            "polarity_reading": ("both columns are reported everywhere: the raw column is "
                                 "the reproducible historical baseline, the "
                                 "_polarity_invariant column is the corrected reading"),
        },
        "product_metadata": product_report,
        "product_metadata_reason": (
            None if product_report else
            "an offline pixel capture carries no Product record. Supply --product-dump "
            "(one final_render_cell_dump per frame) and the comparison switches to exact "
            "Product glyph-id equality, where mask polarity is not a question at all. "
            "The pixel path below is the fallback, not the preferred evidence."),
        "measurement_path": "product_glyph_id" if product_report else "pixel_threshold_fallback",
        "rollup": primary["rollup"],
        "rollup_by_method": {m: results[m]["rollup"] for m in methods},
        "mask_extractor_robustness": robustness,
        "cells": named,
    }
    if args.baseline:
        baseline_doc = json.loads(args.baseline.read_text())
        if args.polarity_baseline:
            baseline_doc = dict(baseline_doc)
            baseline_doc["polarity_invariant"] = polarity_baseline_from_corrected_set(
                json.loads(args.polarity_baseline.read_text()))
        receipt["baseline_comparison"] = compare_baseline(
            primary["rollup"], baseline_doc,
            count_rel_tol=(0.0 if args.grid_json else args.comb_fit_count_tolerance))
        receipt["baseline_columns"] = (
            ["raw", "polarity_invariant"] if args.polarity_baseline else ["raw"])

    if args.save_npz:
        for m in methods:
            res = results[m]
            np.savez_compressed(
                args.out / f"cell_dump_{m}.npz",
                mask_packed=np.packbits(res["masks"], axis=-1),
                mask_shape=np.array(res["masks"].shape),
                fg=res["fgs"].astype(np.float32),
                bg=res["bgs"].astype(np.float32),
                coverage=res["coverage"].astype(np.float32),
                valid=res["valid"], viewport=res["viewport"],
                oscillating=res["oscillating"],
                oscillating_polarity_invariant=res["oscillating_polarity_invariant"],
                polarity_flip=res["polarity_flip"],
                consecutive_iou=res["consecutive_iou"].astype(np.float32),
                consecutive_iou_polarity_invariant=res[
                    "consecutive_iou_polarity_invariant"].astype(np.float32),
                fg_delta=res["fg_delta"].astype(np.float32),
                bg_delta=res["bg_delta"].astype(np.float32),
                # The glyph-change lane, saved so the GIF builder can render the
                # FG-glyph-change highlight from the SAME election this receipt
                # measured instead of re-deriving a second, divergent one.
                glyph_id=res["glyph_id"],
                glyph_id_polarity_invariant=res["glyph_id_polarity_invariant"],
                glyph_changed=res["glyph_changed"],
                glyph_changed_polarity_invariant=res[
                    "glyph_changed_polarity_invariant"],
                fg_delta_polarity_aligned=res[
                    "fg_delta_polarity_aligned"].astype(np.float32),
                bg_delta_polarity_aligned=res[
                    "bg_delta_polarity_aligned"].astype(np.float32),
            )

    out_path = args.out / "fl4359_cell_dump_receipt.json"
    out_path.write_text(json.dumps(receipt, indent=1) + "\n")
    print(json.dumps({
        "receipt": str(out_path),
        "arm": args.arm_label,
        "grid": receipt["grid"],
        "pose_identity_closed": poses["pose_identity_closed"],
        "rollup": {k: v for k, v in primary["rollup"].items() if k != "oscillation_triples"},
        "robustness": robustness,
        "baseline": receipt.get("baseline_comparison", {}).get("result", "NOT_REQUESTED"),
    }, indent=1))

    if args.baseline and receipt["baseline_comparison"]["result"] != "PASS":
        print("FAIL: extractor diverged from the recorded baseline: "
              + ", ".join(receipt["baseline_comparison"]["failed"]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
