#!/usr/bin/env python3
# =============================================================================
# fl4547_hemisphere_mirror_walk.py
# =============================================================================
# WHAT THIS IS
# -----------
# A fresh attempt at turning the 8-direction walking-sprite into a *legible*
# Unicode/ASCII walk animation.  Every earlier attempt failed the same way: the
# figure collapsed onto the centre column and read as a vertical smear instead
# of a person (measured: 99.4% of ink landed in 3 of 5 columns, one CJK glyph
# did 56% of the work).  The root cause was that we faithfully rasterised a
# skeleton whose limbs sit close to the spine, then rounded to cells — so head,
# both arms, both legs and torso all aliased onto the same 2 columns.
#
# THE NEW IDEA (operator's plan)
# ------------------------------
# 1. Stop trusting projection to spread the limbs.  Instead use a fixed
#    "body-plan stencil" on a small 3-wide x 5-tall grid.  Each anatomical part
#    is *assigned* a cell (arm_a always left column, arm_b always right, spine
#    centre).  With the layout guaranteed, limbs physically cannot alias onto
#    the midline — the figure is legible by construction.
# 2. Generate only ONE hemisphere from the source art: {S, SW, W, NW, N}.
# 3. Get the other three views by MIRRORING: SE = mirror(SW), E = mirror(W),
#    NE = mirror(NW).  A horizontal mirror is the exact bilateral-symmetry
#    relation, so the left/right halves stay consistent by construction and we
#    do half the work.
# 4. Choose each cell's glyph from the *angle* of that body part in that frame
#    (a short synthetic stroke at the limb's orientation -> nearest Unicode
#    glyph by shape), and smooth the choice across the 18 frames so the limb
#    sweeps instead of flickering.
#
# WHY IT AVOIDS THE OLD DEPENDENCY WALL
# -------------------------------------
# The original rig needs scikit-image to skeletonise each limb.  We do NOT need
# a skeleton here — only each limb's dominant ORIENTATION, which is the
# principal axis (PCA) of that limb's colour mask.  PCA is pure numpy, so this
# script runs with only numpy + Pillow, both already present.
#
# THE SIX-COLOUR RIG (decoded from the .aseprite source)
# ------------------------------------------------------
# The source art is colour-keyed so each limb keeps its identity:
#   head=cyan, torso=orange, arm_a=purple, arm_b=yellow, leg_a=green, leg_b=red.
# arm_a<->arm_b and leg_a<->leg_b are the bilateral mirror pairs.
#
# RUN
# ---
#   python3 scripts/fl4547_hemisphere_mirror_walk.py
# Writes a versioned gif to the Desktop and a tracked copy + manifest under
# docs/research/ascii/verification/fl4547/skeleton-versions/ and appends a row
# to that folder's registry.json.
# =============================================================================
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Paths and the shared rig module.
# ---------------------------------------------------------------------------
# REPO is the project root (this file lives in <root>/scripts/).
REPO = Path(__file__).resolve().parents[1]
# Where every tracked walk-version artifact lives (gif + frames manifest).
TRACK_DIR = REPO / "docs/research/ascii/verification/fl4547/skeleton-versions"
# The append-only registry that lists every generated walk version.
REGISTRY = TRACK_DIR / "registry.json"
# Per the operator's standing rule, every new version also lands on the Desktop.
DESKTOP = Path.home() / "Desktop"
# The hand-authored per-role stroke vocabulary.  When present (the default), the
# solver interpolates over THESE glyphs instead of auto-selecting from all of
# Unicode — the operator authors the vocabulary, the solver animates it.
CODEBOOK = REPO / "docs/research/ascii/verification/fl4547/authored_walk_codebook.v1.json"

# We import fl4547_skeleton_walk ("the rig") ONLY for its dependency-free
# helpers: the aseprite decoder, the 6-colour part splitter, the pinned-font
# renderer, the palette catalog, and the display-grid constants.  We load it by
# file path so that importing it does not accidentally pull in any skimage-bound
# top-level code (all skimage imports inside the rig are lazy, inside functions
# we do not call).
_spec = importlib.util.spec_from_file_location("rig", REPO / "scripts" / "fl4547_skeleton_walk.py")
rig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rig)

# ---------------------------------------------------------------------------
# Grid + direction configuration.
# ---------------------------------------------------------------------------
# The per-figure canvas is deliberately tiny and TALLER than wide: a standing
# human is ~1:2, and 3 columns is exactly {left-limb, spine, right-limb}.
GRID_W = 3
GRID_H = 5

# The five views we generate directly from the source art.  S (front) and
# N (back) are their own mirror, so they are generated, not mirrored.
HEMISPHERE = ["S", "SW", "W", "NW", "N"]
# The three views we synthesise by horizontally mirroring a generated partner.
# A walk cycle mirrored left<->right is the same motion facing the other way.
MIRROR_FROM = {"SE": "SW", "E": "W", "NE": "NW"}

# Temporal-smoothing weights (see solve_cell_sequence).  These prices encode the
# operator's two-axis objective: pick, per frame, the glyph that best matches the
# skeleton's stroke (fidelity), WHILE minimising abrupt change of that stroke
# between frames.  The three smoothing terms are the "minimise abrupt change"
# axis at increasing order:
# FIDELITY is measured directly in ANGLE, not in 8x8 mask overlap.  A 90-degree
# and a 108-degree stroke differ by only a couple of pixels in an 8x8 cell, so a
# mask-XOR fidelity term cannot see limb rotation and the solver freezes.  We
# instead score a candidate by |its own stroke angle - the limb's angle|, which
# is a smooth, monotonic signal — so the sequence can sweep through intermediate
# glyphs as the limb rotates.
LAMBDA_FIDELITY = 0.020  # cost per degree of MISMATCH between glyph angle and limb angle.
# Weights tuned against the measured motion (side/oblique limbs swing 60-108 deg
# per cycle).  SWITCH/velocity stay small so the swing drives glyph changes; the
# 2nd-order ACC term does the smoothing — it penalises JERK (change of angular
# velocity), not motion, so a steady sweep is free and a jumpy one is expensive.
LAMBDA_SWITCH = 0.05   # cost of changing glyph identity between frames (anti-flicker / hold).
LAMBDA_ANGLE = 0.003   # cost per degree of orientation CHANGE between adjacent frames (velocity).
LAMBDA_ACC = 0.020     # cost per degree of orientation ACCELERATION (the spline/curve-smoothness term).
LAMBDA_BLANK = 3.0     # cost of leaving a present limb blank (keeps the figure whole).
LAMBDA_SHAPE = 0.9     # cost weight for "blobbiness": prefer thin single strokes over dense glyphs.
TOP_K = 40             # candidates pulled from the catalog per frame before pooling.
POOL_K = 22            # size of the shared per-cell candidate pool (union across all frames).
ANGLE_BINS = 12        # angular buckets used to keep the pool spread across the swing.
ICM_ROUNDS = 2         # whole-frame passes that couple neighbouring cells (see compile_direction).
LAMBDA_CONNECT = 0.22  # reward/penalty weight for edge-continuity with occupied neighbours.
FRAMES = 18            # frames per direction in the source walk cycle.

# ---------------------------------------------------------------------------
# THE BODY-PLAN STENCIL.
# ---------------------------------------------------------------------------
# This is the whole point of the redesign.  Each entry pins one anatomical
# sub-part to one fixed (row, col) cell.  "sub" selects which slice of the
# part's colour mask to measure the orientation of:
#   "full"  -> the entire limb (used for short parts like arms and the head)
#   "upper" -> the top half in image space  (thigh / upper spine)
#   "lower" -> the bottom half in image space (shin+foot / lower spine)
# Splitting legs into upper/lower lets a bent knee show up as two different
# stroke angles instead of one averaged-out diagonal.
#
# Column convention: col 0 = side-A limbs, col 1 = midline (head/spine),
# col 2 = side-B limbs.  Rows top->bottom: head, shoulders, mid-torso,
# thighs, shins/feet.  The result is an unambiguous stick-figure silhouette.
STENCIL = [
    {"rc": (0, 1), "part": "head",  "sub": "full",  "kind": "head"},
    {"rc": (1, 1), "part": "torso", "sub": "upper", "kind": "midline"},
    {"rc": (2, 1), "part": "torso", "sub": "lower", "kind": "midline"},
    {"rc": (1, 0), "part": "arm_a", "sub": "full",  "kind": "limb"},
    {"rc": (1, 2), "part": "arm_b", "sub": "full",  "kind": "limb"},
    # The hip/pelvis join cell (row 3, centre) bridges the spine to the two leg
    # columns so the legs are no longer detached bars floating beside the body.
    # Its orientation is the tilt of the line between the two hip tops (pelvis
    # sway), so it also carries a little of the walk's weight-shift.
    {"rc": (3, 1), "part": "hip",   "sub": "full",  "kind": "hip"},
    {"rc": (3, 0), "part": "leg_a", "sub": "upper", "kind": "limb"},
    {"rc": (4, 0), "part": "leg_a", "sub": "lower", "kind": "limb"},
    {"rc": (3, 2), "part": "leg_b", "sub": "upper", "kind": "limb"},
    {"rc": (4, 2), "part": "leg_b", "sub": "lower", "kind": "limb"},
]

# The two limbs whose column swaps under a horizontal mirror.  Used when we
# mirror a hemisphere view: side-A cells must take side-B glyphs and vice versa
# only implicitly (we flip the whole grid), but we ALSO reflect each glyph's
# shape so that, e.g., a forward-leaning "/" becomes a "\".
MIRROR_PART = {"arm_a": "arm_b", "arm_b": "arm_a", "leg_a": "leg_b", "leg_b": "leg_a"}


# ---------------------------------------------------------------------------
# Geometry helpers (pure numpy — no skimage).
# ---------------------------------------------------------------------------
def part_angle(mask_bool, sub: str):
    """Return the dominant orientation (degrees, 0..180) of a limb colour mask.

    mask_bool : a boolean HxW array, True where this limb's colour is painted.
    sub       : "full" | "upper" | "lower" — which vertical slice to measure.

    We take the pixels of the requested slice, centre them, and compute the
    principal axis via the eigenvector of the covariance matrix with the
    largest eigenvalue.  That axis IS the limb's line, so its angle is the
    limb's orientation.  Returns None when the slice has too few pixels (the
    limb is occluded in this frame — a real, expected case in 3/4 views).
    """
    ys, xs = np.nonzero(mask_bool)
    if len(xs) < 4:
        return None
    # Restrict to the upper/lower half in image-space y when asked.  In image
    # coordinates y grows DOWNWARD, so "upper" = smaller y.
    if sub in ("upper", "lower"):
        mid = (ys.min() + ys.max()) / 2.0
        keep = ys <= mid if sub == "upper" else ys > mid
        xs, ys = xs[keep], ys[keep]
        if len(xs) < 4:
            return None
    pts = np.column_stack((xs, ys)).astype(float)
    pts = pts - pts.mean(axis=0)              # centre the pixel cloud
    cov = pts.T @ pts                          # 2x2 scatter matrix
    evals, evecs = np.linalg.eigh(cov)         # ascending eigenvalues
    axis = evecs[:, int(np.argmax(evals))]     # principal direction (largest spread)
    # atan2(dy, dx); fold to 0..180 because a line has no head/tail direction.
    return math.degrees(math.atan2(axis[1], axis[0])) % 180.0


def hip_angle(masks) -> float:
    """Orientation (deg) of the pelvis line = tilt between the two hip tops.

    The hip cell joins the spine to the legs.  We estimate the pelvis line from
    the top-centroid of each leg's colour mask (where the thigh meets the hip)
    and return the angle of the segment connecting them.  When a leg is occluded
    we fall back to 0 degrees (a flat, horizontal hip connector).
    """
    def top_centroid(mask):
        if mask is None:
            return None
        ys, xs = np.nonzero(mask)
        if len(xs) < 2:
            return None
        mid = (ys.min() + ys.max()) / 2.0     # split at vertical midpoint
        keep = ys <= mid                       # thigh-top half (smaller y = higher up)
        if keep.sum() < 2:
            keep = np.ones_like(ys, dtype=bool)
        return float(xs[keep].mean()), float(ys[keep].mean())

    ca = top_centroid(masks.get("leg_a"))
    cb = top_centroid(masks.get("leg_b"))
    if ca is None or cb is None:
        return 0.0
    return math.degrees(math.atan2(cb[1] - ca[1], cb[0] - ca[0])) % 180.0


def stroke_mask(angle_deg: float) -> int:
    """Rasterise a short straight stroke at `angle_deg` into an 8x8 bitmask.

    The rig's glyph catalog stores each glyph as a 64-bit mask over an 8x8 cell
    (bit (8*y + x) set means that sub-pixel is inked).  To find the Unicode
    glyph that best matches a limb pointing at some angle, we draw the limb as a
    centred 2-pixel-wide line and then ask the catalog for the nearest shape.
    """
    from PIL import Image, ImageDraw
    img = Image.new("L", (8, 8), 0)
    draw = ImageDraw.Draw(img)
    a = math.radians(angle_deg)
    dx, dy = math.cos(a) * 3.2, math.sin(a) * 3.2   # half-length ~3.2 px from centre
    draw.line((3.5 - dx, 3.5 - dy, 3.5 + dx, 3.5 + dy), fill=255, width=2)
    value = 0
    px = img.load()
    for y in range(8):
        for x in range(8):
            if px[x, y] > 0:
                value |= 1 << (8 * y + x)
    return value


def head_mask() -> int:
    """A small ring in the centre of an 8x8 cell — the target shape for a head.

    Rather than a stroke angle (a head has no meaningful orientation), we match
    the head cell against a small circle so the catalog returns round glyphs
    (o, O, circles, ring-like ideographs), which read as a head.
    """
    from PIL import Image, ImageDraw
    img = Image.new("L", (8, 8), 0)
    ImageDraw.Draw(img).ellipse((2, 1, 5, 4), outline=255, width=1)
    value = 0
    px = img.load()
    for y in range(8):
        for x in range(8):
            if px[x, y] > 0:
                value |= 1 << (8 * y + x)
    return value


# Cache of per-glyph "strokeness" so we only measure each mask once.
_STROKE_CACHE = {}


def strokeness(mask: int) -> float:
    """How un-stroke-like a glyph is: 0 = ideal thin single line, higher = blob.

    A limb should read as a thin line, but the raw 8x8 mask matcher happily
    returns dense ideographs that fill a stroke's pixels better than a clean "/"
    does.  We score each glyph by two defects and prefer the low-scoring ones:
      * thickness = minor/major PCA eigenvalue ratio (0 for a line, ->1 for a blob)
      * over-density = how much of the 8x8 cell is inked beyond a thin stroke
    """
    if mask in _STROKE_CACHE:
        return _STROKE_CACHE[mask]
    pts = [(x, y) for y in range(8) for x in range(8) if mask & (1 << (8 * y + x))]
    n = len(pts)
    if n < 2:
        _STROKE_CACHE[mask] = 1.0
        return 1.0
    arr = np.asarray(pts, dtype=float)
    arr -= arr.mean(axis=0)
    evals = np.sort(np.linalg.eigvalsh(arr.T @ arr))   # [minor, major]
    ratio = float(evals[0] / evals[1]) if evals[1] > 1e-9 else 1.0
    density = n / 64.0
    penalty = 0.75 * ratio + 0.9 * max(0.0, density - 0.16)
    _STROKE_CACHE[mask] = penalty
    return penalty


def hflip_mask(mask: int) -> int:
    """Horizontally mirror an 8x8 bitmask: sub-pixel (x, y) -> (7 - x, y)."""
    out = 0
    for y in range(8):
        for x in range(8):
            if mask & (1 << (8 * y + x)):
                out |= 1 << (8 * y + (7 - x))
    return out


# ---------------------------------------------------------------------------
# Source decoding: per direction, per frame, the orientation of every stencil
# part.  This is the only place we touch the raw art.
# ---------------------------------------------------------------------------
def decode_direction_angles(direction: str):
    """For one source direction, return a list (length 18) of per-cell angles.

    angles[frame_index][stencil_index] = orientation in degrees, or None when
    that part is occluded in that frame.  Occlusion is later repaired by
    carrying the nearest known frame's value forward (temporal fill).
    """
    src = rig.SOURCE_ROOT / direction / f"{direction}.aseprite"
    frames = rig.decode_aseprite(src)
    per_frame = []
    for frame_rgba in frames:
        masks = rig.part_masks(frame_rgba)   # dict: part_name -> boolean HxW mask
        cell_angles = []
        for entry in STENCIL:
            if entry["kind"] == "head":
                # Heads are matched by shape, not angle; sentinel 0.0 is unused.
                cell_angles.append(0.0 if entry["part"] in masks else None)
                continue
            if entry["kind"] == "hip":
                # The hip cell derives its tilt from both legs, not one mask.
                cell_angles.append(hip_angle(masks))
                continue
            mask = masks.get(entry["part"])
            cell_angles.append(None if mask is None else part_angle(mask, entry["sub"]))
        per_frame.append(cell_angles)
    return per_frame


def fill_occlusions(angle_series):
    """Replace None angles with the nearest known frame's value (per cell).

    A part missing in a frame (occluded behind the body in 3/4 views) would
    otherwise have no glyph.  We walk each cell's 18-frame series and fill gaps
    from the closest frame that DID see the part, so the limb persists smoothly
    instead of blinking out.  If a cell is never seen, it stays None (blank).
    """
    n_cells = len(STENCIL)
    for cell in range(n_cells):
        series = [frame[cell] for frame in angle_series]
        known = [i for i, v in enumerate(series) if v is not None]
        if not known:
            continue
        for i, v in enumerate(series):
            if v is None:
                # nearest known frame index (ties -> earlier)
                j = min(known, key=lambda k: (abs(k - i), k))
                angle_series[i][cell] = angle_series[j][cell]
    return angle_series


# ---------------------------------------------------------------------------
# Glyph selection + per-cell temporal smoothing.
# ---------------------------------------------------------------------------
def glyph_angle(mask: int, cache: dict) -> float:
    """Orientation (deg, 0..180) of a catalog glyph, from the PCA of its mask.

    Cached because we ask for the same glyph's angle many times while smoothing.
    Blank / single-pixel glyphs return 0.0 (no meaningful orientation).
    """
    if mask in cache:
        return cache[mask]
    pts = [(x, y) for y in range(8) for x in range(8) if mask & (1 << (8 * y + x))]
    if len(pts) < 2:
        cache[mask] = 0.0
        return 0.0
    arr = np.asarray(pts, dtype=float)
    arr -= arr.mean(axis=0)
    evals, evecs = np.linalg.eigh(arr.T @ arr)
    axis = evecs[:, int(np.argmax(evals))]
    ang = math.degrees(math.atan2(axis[1], axis[0])) % 180.0
    cache[mask] = ang
    return ang


def cell_target(angle, kind):
    """8x8 target mask this cell should look like in one frame (None -> blank).

    A head is matched against a ring; every other cell is matched against a
    short stroke at the measured limb/pelvis angle.
    """
    if angle is None:
        return None
    return head_mask() if kind == "head" else stroke_mask(angle)


def role_for(entry):
    """Map a stencil cell to a codebook role (head/torso/arm/leg_upper/leg_lower/hip)."""
    if entry["kind"] == "head":
        return "head"
    if entry["kind"] == "hip":
        return "hip"
    if entry["part"] == "torso":
        return "torso"
    if entry["part"] in ("arm_a", "arm_b"):
        return "arm"
    if entry["part"] in ("leg_a", "leg_b"):
        return "leg_upper" if entry["sub"] == "upper" else "leg_lower"
    return "arm"


def load_codebook(path, catalog):
    """Load the hand-authored per-role vocabulary -> {role: [catalog_index, ...]}.

    Each authored character is mapped to its catalog index; glyphs that are not
    admitted or have no pinned-font coverage are skipped and reported (so a typo
    in the codebook degrades gracefully instead of crashing).
    """
    data = json.loads(Path(path).read_text())
    scalar_to_index = {int(s): i for i, s in enumerate(catalog["scalars"])}
    font_chain, _ = rig._canonical_glyph_renderer()
    pools, missing = {}, []
    for role, chars in data["roles"].items():
        indices = []
        for ch in chars:
            if len(ch) != 1:
                missing.append((role, ch))
                continue
            idx = scalar_to_index.get(ord(ch))
            if idx is None or font_chain.render(ord(ch)) is None:
                missing.append((role, ch))
                continue
            indices.append(idx)
        pools[role] = indices
    return pools, missing


def build_cell_pool(per_frame_angles, cell_index, kind, catalog, angle_cache, authored=None):
    """Build ONE shared candidate pool for a cell (union of top-K over frames).

    The old code picked candidates per frame in isolation, so a glyph that was
    great in frame 5 might be absent from frame 6's short-list — forcing a
    switch and causing flicker.  Here we pool the best glyphs seen across ALL 18
    frames, so the temporal solver can HOLD one glyph whenever that is cheaper,
    yet the pool spans the whole angular range the limb sweeps through.

    For limb/pelvis cells the pool is chosen to be BOTH angularly spread AND
    stroke-like: we bucket candidates by their own orientation into ANGLE_BINS
    and keep the cleanest (least blobby) glyphs in each bucket.  That way the
    solver always has a thin line available at every angle the limb passes
    through, instead of only dense ideographs.

    Returns (pool_indices, per_frame_targets).  Each target is a small tuple that
    tells the fidelity function how to score this cell that frame:
        ("absent", None)      the limb is occluded -> only blank is free
        ("head",   ring_mask) match a head by 8x8 shape (heads have no angle)
        ("angle",  degrees)   match a limb/pelvis by orientation
    """
    targets = []
    union = {}   # index -> glyph mask (deduplicated across frames)
    for f in range(FRAMES):
        angle = per_frame_angles[f][cell_index]
        if angle is None:
            targets.append(("absent", None))
            continue
        if kind == "head":
            mask = head_mask()
            targets.append(("head", mask))
        else:
            mask = stroke_mask(angle)
            targets.append(("angle", float(angle)))
        if authored is not None:
            continue   # the pool is the hand-authored vocabulary; skip auto-search
        for cand in rig.glyph_candidates(mask, catalog, TOP_K):
            scalar = int(catalog["scalars"][cand["index"]])
            # Skip the Control Pictures block (U+2400..U+243F): glyphs like
            # U+2400 render as the literal label "NUL"/"SOH" — they are
            # non-emoji but read as text, not as a limb stroke.
            if 0x2400 <= scalar <= 0x243F:
                continue
            union[cand["index"]] = int(catalog["masks"][cand["index"]])

    # Hand-authored path: the pool IS the operator's vocabulary for this role.
    # The angle-fidelity + second-order smoothing then interpolate across it.
    if authored is not None:
        pool = list(authored)
        if catalog["space_index"] not in pool:
            pool.append(catalog["space_index"])
        return pool, targets

    if kind == "head":
        # Heads: keep the best shape matches as-is (roundness is desired here).
        pool = list(union.keys())[:POOL_K]
    else:
        # Limbs/pelvis: bucket by orientation, keep the cleanest strokes per bin.
        buckets = {}
        for idx, gmask in union.items():
            gang = glyph_angle(gmask, angle_cache)
            b = min(ANGLE_BINS - 1, int(gang / (180.0 / ANGLE_BINS)))
            buckets.setdefault(b, []).append((strokeness(gmask), idx))
        pool = []
        per_bin = max(1, POOL_K // max(1, len(buckets)))
        for b in sorted(buckets):
            for _pen, idx in sorted(buckets[b])[:per_bin]:
                pool.append(idx)
        # Top up to POOL_K with the globally cleanest remaining candidates.
        if len(pool) < POOL_K:
            extra = sorted(((strokeness(m), i) for i, m in union.items() if i not in pool))
            pool += [i for _p, i in extra[:POOL_K - len(pool)]]

    # Blank must always be reachable (for occluded frames / never-seen cells).
    if catalog["space_index"] not in pool:
        pool.append(catalog["space_index"])
    return pool, targets


def folded_angle_distance(a, b):
    """Distance between two orientations folded to 0..90 (a stroke == its flip)."""
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def unary_cost(target, idx, catalog, glyph_angle_value):
    """Fidelity cost of using glyph `idx` for one cell in one frame.

    `target` is the tuple from build_cell_pool; `glyph_angle_value` is this
    glyph's own stroke orientation (precomputed once in the solver).
      absent -> only blank is free, anything else is expensive.
      head   -> 8x8 shape match against the ring (heads carry no angle).
      angle  -> LAMBDA_FIDELITY * |glyph angle - limb angle|  (the smooth signal).
    A present limb rendered blank pays LAMBDA_BLANK so the figure stays whole.
    """
    mode, value = target
    if mode == "absent":
        return 0.0 if idx == catalog["space_index"] else 6.0
    if idx == catalog["space_index"]:
        return LAMBDA_BLANK
    if mode == "head":
        return rig._glyph_index_cost(value, idx, catalog)
    # mode == "angle": direct orientation match (smooth and monotonic) plus a
    # blobbiness penalty so a clean thin stroke beats a dense ideograph at the
    # same angle.
    angle_cost = LAMBDA_FIDELITY * folded_angle_distance(glyph_angle_value, value)
    shape_cost = LAMBDA_SHAPE * strokeness(int(catalog["masks"][idx]))
    return angle_cost + shape_cost


def solve_cell_sequence(pool, targets, catalog, angle_cache, spatial=None):
    """Second-order Viterbi over the 18 frames for one cell.

    This is the operator's two-axis objective made exact:
      * FIDELITY  — unary_cost: match this frame's skeleton stroke.
      * SMOOTHNESS — three "minimise abrupt change" terms of rising order:
          LAMBDA_SWITCH  : identity change     (0th order — anti-flicker)
          LAMBDA_ANGLE   : orientation change  (1st order — velocity)
          LAMBDA_ACC     : orientation bend    (2nd order — the spline/curve term)
    Because the second-order term needs the two previous angles, the Viterbi
    state is a PAIR (previous glyph, current glyph); we minimise over the glyph
    before that.  `spatial(frame, idx)` optionally adds a whole-frame coupling
    penalty (edge continuity with held-constant neighbours) during the ICM
    passes.  Returns a list of catalog indices, one per frame.
    """
    # Orientation of every pool glyph (cached across the whole run).
    ang = {idx: glyph_angle(int(catalog["masks"][idx]), angle_cache) for idx in pool}

    def d1(i, j):
        """Folded orientation distance 0..90 (a stroke == its 180-flip)."""
        d = abs(ang[i] - ang[j])
        return min(d, 180.0 - d)

    def signed(delta):
        """Fold an angle step into (-90, 90] so acceleration is well-defined."""
        return ((delta + 90.0) % 180.0) - 90.0

    def d2(i, j, k):
        """Second difference of orientation = how sharply the sweep bends."""
        return abs(signed(ang[k] - ang[j]) - signed(ang[j] - ang[i]))

    def U(frame, idx):
        base = unary_cost(targets[frame], idx, catalog, ang[idx])
        return base + (spatial(frame, idx) if spatial is not None else 0.0)

    # Initialise every (frame0, frame1) pair state.
    cost, path = {}, {}
    u0 = {i: U(0, i) for i in pool}
    u1 = {j: U(1, j) for j in pool}
    for i in pool:
        for j in pool:
            cost[(i, j)] = u0[i] + u1[j] + (LAMBDA_SWITCH if i != j else 0.0) + LAMBDA_ANGLE * d1(i, j)
            path[(i, j)] = [i, j]

    # Sweep frames 2..N-1, carrying the acceleration term across each triple.
    for f in range(2, FRAMES):
        uf = {k: U(f, k) for k in pool}
        ncost, npath = {}, {}
        for j in pool:
            for k in pool:
                switch_ang = (LAMBDA_SWITCH if j != k else 0.0) + LAMBDA_ANGLE * d1(j, k)
                best = None
                for i in pool:
                    prev = cost.get((i, j))
                    if prev is None:
                        continue
                    total = prev + LAMBDA_ACC * d2(i, j, k)
                    if best is None or total < best[0]:
                        best = (total, i)
                if best is None:
                    continue
                ncost[(j, k)] = best[0] + uf[k] + switch_ang
                npath[(j, k)] = path[(best[1], j)] + [k]
        cost, path = ncost, npath

    end = min(cost, key=lambda state: cost[state])
    return path[end]


# ---------------------------------------------------------------------------
# Direction compilation + mirroring.
# ---------------------------------------------------------------------------
def connectivity_penalty(idx, r, c, grid, catalog):
    """Edge-continuity coupling for one candidate glyph against its neighbours.

    This is the whole-frame ("reassembled") coupling.  Per-cell smoothing alone
    optimises each limb in isolation, so nothing makes the strokes actually MEET
    across cell borders — the silhouette can be smooth yet disconnected.  Here,
    for a candidate glyph in cell (r, c), we look at each occupied neighbour in
    the CURRENT frame grid and compare shared-boundary ink:
      * neighbour reaches the shared edge AND we meet it  -> reward (connected)
      * neighbour reaches the shared edge AND we do NOT   -> penalty (a visible gap)
    `rig._edge_signature(mask, side)` returns a bitmask over the 8 sub-pixel
    positions along that edge; opposite sides index the same axis, so a bitwise
    AND tells us whether the two strokes touch.
    """
    if idx == catalog["space_index"]:
        return 0.0
    mask = int(catalog["masks"][idx])
    penalty = 0.0
    # (row-delta, col-delta, my-side, neighbour-side)
    for dr, dc, side, opposite in ((-1, 0, "top", "bottom"), (1, 0, "bottom", "top"),
                                   (0, -1, "left", "right"), (0, 1, "right", "left")):
        nr, nc = r + dr, c + dc
        if not (0 <= nr < GRID_H and 0 <= nc < GRID_W):
            continue
        nidx = grid[nr * GRID_W + nc]
        if nidx == catalog["space_index"]:
            continue
        nsig = rig._edge_signature(int(catalog["masks"][nidx]), opposite)
        if not nsig:
            continue                              # neighbour has no ink at this border
        sig = rig._edge_signature(mask, side)
        penalty += -LAMBDA_CONNECT if (sig & nsig) else LAMBDA_CONNECT
    return penalty


def compile_direction(direction: str, catalog, angle_cache, codebook_pools=None):
    """Compile one GENERATED direction into per-frame 3x5 glyph grids.

    Two stages, matching the operator's plan:
      1. Per body part: solve each stencil cell's 18-frame sequence on its own
         (fidelity + the three smoothness terms).
      2. Reassembled whole frame: ICM rounds that re-solve each cell with an
         added edge-continuity coupling against the current neighbours, so the
         assembled silhouette connects instead of floating apart.
    When `codebook_pools` is given, each cell's candidate pool is the operator's
    hand-authored vocabulary for that role, and the solver interpolates over it.
    Returns selected[frame] = list of GRID_W*GRID_H catalog indices.
    """
    angles = fill_occlusions(decode_direction_angles(direction))

    # Precompute each cell's candidate pool + per-frame target masks once.
    pools, targets_all = [], []
    for cell_index, entry in enumerate(STENCIL):
        authored = codebook_pools.get(role_for(entry)) if codebook_pools else None
        pool, targets = build_cell_pool(angles, cell_index, entry["kind"], catalog,
                                        angle_cache, authored=authored)
        pools.append(pool)
        targets_all.append(targets)

    # Stage 1 — independent per-cell temporal solve (no spatial coupling yet).
    seqs = [solve_cell_sequence(pools[ci], targets_all[ci], catalog, angle_cache)
            for ci in range(len(STENCIL))]

    def assemble():
        grids = []
        for f in range(FRAMES):
            grid = [catalog["space_index"]] * (GRID_W * GRID_H)
            for ci, entry in enumerate(STENCIL):
                r, c = entry["rc"]
                grid[r * GRID_W + c] = seqs[ci][f]
            grids.append(grid)
        return grids

    grids = assemble()

    # Stage 2 — whole-frame ICM: re-solve each cell against its held-constant
    # neighbours, updating the grid in place (coordinate descent).  A couple of
    # rounds is enough to knit the silhouette together without over-committing.
    for _ in range(ICM_ROUNDS):
        for ci, entry in enumerate(STENCIL):
            r, c = entry["rc"]
            spatial = (lambda frame, idx, r=r, c=c: connectivity_penalty(idx, r, c, grids[frame], catalog))
            seqs[ci] = solve_cell_sequence(pools[ci], targets_all[ci], catalog, angle_cache, spatial=spatial)
            for f in range(FRAMES):
                grids[f][r * GRID_W + c] = seqs[ci][f]

    return grids


def mirror_glyph(index: int, catalog, mirror_cache: dict) -> int:
    """Return the catalog glyph whose shape is the horizontal mirror of `index`.

    We flip the glyph's 8x8 mask left<->right and ask the catalog for the
    closest shape.  If an exact mirror exists it is returned; otherwise the
    nearest available shape is used (the operator's "closest shape when no
    mirror glyph exists" rule).  Cached per source glyph.
    """
    if index == catalog["space_index"]:
        return index
    if index in mirror_cache:
        return mirror_cache[index]
    flipped = hflip_mask(int(catalog["masks"][index]))
    if flipped == 0:
        mirror_cache[index] = index
        return index
    best = rig.glyph_candidates(flipped, catalog, 1)
    mirror_cache[index] = best[0]["index"] if best else index
    return mirror_cache[index]


def mirror_direction(source_selected, catalog, mirror_cache: dict):
    """Produce a mirrored view from a generated partner's compiled grids.

    Two operations, both required for a true bilateral mirror:
      1. Flip the grid horizontally: cell (r, c) -> (r, GRID_W-1-c), so the
         left/right limb columns swap.
      2. Replace each glyph with its horizontal-mirror-shape partner, so a
         forward-leaning stroke becomes a backward-leaning stroke.
    """
    out = []
    for grid in source_selected:
        new = [catalog["space_index"]] * (GRID_W * GRID_H)
        for r in range(GRID_H):
            for c in range(GRID_W):
                idx = grid[r * GRID_W + c]
                new[r * GRID_W + (GRID_W - 1 - c)] = mirror_glyph(idx, catalog, mirror_cache)
        out.append(new)
    return out


# ---------------------------------------------------------------------------
# Rendering + tracked output.
# ---------------------------------------------------------------------------
def render_and_write(compiled, catalog, frame_ms: int, codebook_used: bool):
    """Render all 8 directions into a versioned gif + manifest + registry row."""
    from PIL import Image, ImageDraw, ImageFont

    font_chain, font_receipt = rig._canonical_glyph_renderer()
    tile_cache = {}
    missing = []

    def tile(index, cell_px):
        """Rasterise a catalog glyph to an RGB tile (green ink on near-black)."""
        scalar = int(catalog["scalars"][index])
        if scalar not in tile_cache:
            rendered = font_chain.render(scalar)
            if rendered is None:
                missing.append(scalar)
                raise ValueError(f"selected U+{scalar:04X} has no pinned font owner")
            rgba = Image.frombytes("RGBA", (font_chain.cell_px, font_chain.cell_px), rendered.rgba8)
            base = Image.new("RGB", rgba.size, (8, 8, 8))
            ink = Image.new("RGB", rgba.size, (120, 230, 120))
            base.paste(ink, mask=rgba.getchannel("A"))
            tile_cache[scalar] = base
        return tile_cache[scalar].resize((cell_px, cell_px), Image.Resampling.NEAREST)

    cell_px, label_h, pad = 24, 16, 6
    panel_w, panel_h = GRID_W * cell_px, GRID_H * cell_px
    canvas_w = pad + rig.GRID_COLS * (panel_w + pad)
    canvas_h = pad + rig.GRID_ROWS * (panel_h + label_h + pad)
    label_font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 12)

    gif_frames = []
    manifest_frames = {d: [] for d in rig.DISPLAY_DIRS}
    for fi in range(FRAMES):
        canvas = Image.new("RGB", (canvas_w, canvas_h), (8, 8, 8))
        draw = ImageDraw.Draw(canvas)
        for di, direction in enumerate(rig.DISPLAY_DIRS):
            gc, gr = di % rig.GRID_COLS, di // rig.GRID_COLS
            x0 = pad + gc * (panel_w + pad)
            y0 = pad + gr * (panel_h + label_h + pad)
            draw.text((x0, y0), f"{direction} f{fi}", fill=(150, 210, 150), font=label_font)
            rows, cells = [], []
            for r in range(GRID_H):
                chars = []
                for c in range(GRID_W):
                    idx = compiled[direction][fi][r * GRID_W + c]
                    scalar = int(catalog["scalars"][idx])
                    chars.append(chr(scalar))
                    if idx != catalog["space_index"]:
                        canvas.paste(tile(idx, cell_px), (x0 + c * cell_px, y0 + label_h + r * cell_px))
                    cells.append({"cell": [c, r], "glyph_id": int(catalog["glyph_ids"][idx]),
                                  "unicode_scalar": scalar, "character": chr(scalar)})
                rows.append("".join(chars))
            manifest_frames[direction].append({"frame_index": fi, "rows": rows, "cells": cells})
        gif_frames.append(canvas)

    timestamp = datetime.now().strftime("v%Y%m%dT%H%M%S")
    name = f"{timestamp}__hemisphere-mirror-bodyplan-{GRID_W}x{GRID_H}__walk_all_directions"
    TRACK_DIR.mkdir(parents=True, exist_ok=True)
    tracked_gif = TRACK_DIR / f"{name}.gif"
    desktop_gif = DESKTOP / f"{name}.gif"
    tracked_manifest = TRACK_DIR / f"{name}.frames.json"
    gif_frames[0].save(tracked_gif, save_all=True, append_images=gif_frames[1:],
                       duration=frame_ms, loop=0, disposal=2)
    desktop_gif.write_bytes(tracked_gif.read_bytes())

    manifest = {
        "schema": "fl4547.hemisphere_mirror_bodyplan.v1",
        "status": "experimental_first_look",
        "primary_owner": "FL-4547",
        "lineage": ["FL-4512", "FL-4561"],
        "name": name,
        "timestamp": timestamp,
        "grid": [GRID_W, GRID_H],
        "frames_per_direction": FRAMES,
        "palette_mode": "hand_authored_codebook" if codebook_used else "canonical_full_unicode_non_emoji",
        "authored_codebook": str(CODEBOOK.relative_to(REPO)) if codebook_used else None,
        "generated_directions": HEMISPHERE,
        "mirrored_directions": MIRROR_FROM,
        "stencil": [{"rc": e["rc"], "part": e["part"], "sub": e["sub"], "kind": e["kind"]}
                    for e in STENCIL],
        "final_selection_owner": "(direction, stencil_cell, frame_index)",
        "cell_role": "assigned body-plan slot; layout is a stencil, glyph chosen by limb angle",
        "temporal_smoothing": {
            "objective": "per-frame skeleton fidelity vs minimised abrupt line change",
            "switch": LAMBDA_SWITCH,           # 0th order: identity hold
            "angle_per_deg": LAMBDA_ANGLE,     # 1st order: orientation velocity
            "accel_per_deg": LAMBDA_ACC,       # 2nd order: spline/curve smoothness
            "blank": LAMBDA_BLANK,
            "shared_pool_size": POOL_K,
            "solver": "second-order Viterbi over shared per-cell pool",
        },
        "whole_frame_pass": {
            "rounds": ICM_ROUNDS,
            "coupling": "edge-continuity ICM against held-constant neighbours",
            "connect_weight": LAMBDA_CONNECT,
        },
        "font_receipt": font_receipt,
        "descriptor_sha256": catalog["descriptor_sha256"],
        "descriptor_manifest_sha256": catalog["manifest_sha256"],
        "frames": manifest_frames,
    }
    tracked_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")

    entry = {
        "name": name,
        "kind": "hemisphere-mirror-bodyplan",
        "schema": manifest["schema"],
        "status": manifest["status"],
        "timestamp": timestamp,
        "grid": [GRID_W, GRID_H],
        "frames_per_dir": FRAMES,
        "palette_mode": manifest["palette_mode"],
        "tracked_gif": str(tracked_gif.relative_to(REPO)),
        "tracked_manifest": str(tracked_manifest.relative_to(REPO)),
        "desktop_gif": str(desktop_gif),
        "gif_sha256": hashlib.sha256(tracked_gif.read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256(tracked_manifest.read_bytes()).hexdigest(),
    }
    registry = json.loads(REGISTRY.read_text())
    registry.append(entry)
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=1) + "\n")
    return tracked_gif, desktop_gif, tracked_manifest, missing


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------
def run(args):
    catalog = rig.load_descriptor_catalog()
    angle_cache = {}    # glyph mask -> orientation, reused across all cells
    mirror_cache = {}   # glyph index -> mirror-partner index

    # Load the hand-authored codebook unless --no-codebook is set.  With it, each
    # limb interpolates over the operator's chosen strokes; without it, the
    # solver auto-selects from all of Unicode (the previous behaviour).
    codebook_pools = None
    if not args.no_codebook and CODEBOOK.exists():
        codebook_pools, missing_authored = load_codebook(CODEBOOK, catalog)
        if missing_authored:
            print(f"  codebook: skipped unavailable glyphs -> {missing_authored}")
        print("  codebook: " + ", ".join(f"{role}={len(idxs)}"
                                          for role, idxs in codebook_pools.items()))

    compiled = {}
    # 1-2. Generate the hemisphere directly from the source art.
    for direction in HEMISPHERE:
        compiled[direction] = compile_direction(direction, catalog, angle_cache, codebook_pools)
        print(f"  generated {direction}: body-plan stencil + per-cell temporal smoothing")
    # 3. Mirror the remaining three views from their bilateral partners.
    for direction, source in MIRROR_FROM.items():
        compiled[direction] = mirror_direction(compiled[source], catalog, mirror_cache)
        print(f"  mirrored  {direction} <- {source}")

    tracked, desktop, manifest, missing = render_and_write(
        compiled, catalog, args.frame_ms, codebook_used=codebook_pools is not None)
    if missing:
        raise SystemExit(f"pinned font missing selected scalars: {sorted(set(missing))}")

    # A tiny legibility summary so the run reports what it actually produced.
    distinct = set()
    for direction in rig.DISPLAY_DIRS:
        for grid in compiled[direction]:
            for idx in grid:
                if idx != catalog["space_index"]:
                    distinct.add(int(catalog["scalars"][idx]))
    print(json.dumps({
        "distinct_non_space_glyphs": len(distinct),
        "stencil_cells": len(STENCIL),
        "generated": HEMISPHERE,
        "mirrored": list(MIRROR_FROM.keys()),
    }, indent=2))
    print(f"desktop: {desktop}\ntracked: {tracked.relative_to(REPO)}\nmanifest: {manifest.relative_to(REPO)}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frame-ms", type=int, default=90, help="gif frame duration in ms")
    parser.add_argument("--no-codebook", action="store_true",
                        help="ignore the hand-authored codebook and auto-select from all of Unicode")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
