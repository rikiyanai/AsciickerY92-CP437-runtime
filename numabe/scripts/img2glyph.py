#!/usr/bin/env python3
# =============================================================================
# img2glyph.py
# =============================================================================
# Convert an image into ASCII/Unicode art by STRUCTURAL matching — i.e. pick the
# Unicode character whose *shape* best traces the local image structure, not just
# the one with the closest average brightness. This is the idea behind Xu, Zhang &
# Wong, "Structure-based ASCII Art" (SIGGRAPH 2010), the paper cited in the FL-4208
# glyph-rendering lane: a good ASCII rendering matches the STROKES/EDGES of the
# picture to the strokes of the glyphs, so it reads as line-art rather than a grey
# smear. (The old scripts/glyph_matcher.py is tone-based and limited to 256 CP437
# glyphs — the opposite approach.)
#
# It reuses the existing 152,661-glyph shape corpus in
#   .run/glyph_audit/glyph_features.npz   (build: python3 scripts/glyph_features.py --all)
# which already stores, per glyph:
#   norm      16x16 normalized ink bitmap (bbox-cropped, scaled, centred)
#   norm_dil  the same, dilated 1px  -> lets a near-miss stroke still overlap
#             (this is what makes the match "misalignment tolerant", the key trick
#              so a slightly-off diagonal still matches a diagonal glyph)
#   orient    8-bin edge-orientation histogram (which way the strokes run)
# We turn each image cell into the SAME descriptors and score every glyph.
#
# WHY THIS SUITS "2x3 sprite, 8-directional": a tiny sprite is 2 characters wide by
# 3 tall (6 cells). Render (or draw) your subject facing each of the 8 compass
# directions, save 8 images, and run this in --dir8 mode: it emits one 2x3 glyph
# sprite per direction. Because matching is structural, a subject "leaning" NE
# picks up diagonal glyphs, so the 8 sprites actually look directional.
#
# USAGE
#   # one image -> a 2-wide x 3-tall glyph sprite (structural match, whole Unicode)
#   python3 scripts/img2glyph.py hero.png --grid 2x3
#
#   # 8 directional images -> 8 labelled 2x3 sprites (order N NE E SE S SW W NW)
#   python3 scripts/img2glyph.py n.png ne.png e.png se.png s.png sw.png w.png nw.png \
#       --grid 2x3 --dir8
#
#   # tone mode (fill/brightness) instead of structure (edges); restrict to a block
#   python3 scripts/img2glyph.py hero.png --grid 2x3 --mode tone --allow "Block Elements"
# =============================================================================

from __future__ import annotations

import argparse
import sys

import numpy as np
from PIL import Image

# Reuse the corpus loader AND the exact descriptor functions the cache was built
# with, so an image cell is described identically to a glyph (apples to apples).
import glyph_features as gf

N = gf.N  # glyph grid size (16)

# Compass order the frames animate / are emitted in (clockwise from North). Only
# used to label --dir8 output.
_DIR8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def cell_to_norm(cell_gray: np.ndarray, mode: str, invert: bool,
                 edge_frac: float) -> np.ndarray:
    """Turn one grayscale image cell (float 0..1, any size) into a 16x16 0/1 ink
    grid using the SAME normalization the glyph corpus used (gf.crop_fit).

    mode "structure": keep EDGES (gradient magnitude above a fraction of the cell
    max) — this is what makes the match trace strokes/outlines like Xu 2010.
    mode "tone": keep the DARK (or light, if --invert) region by thresholding at
    the cell mean — a fill/brightness match, closer to classic ASCII ramps.
    """
    if mode == "structure":
        # Sobel-ish gradient magnitude via numpy finite differences.
        gy, gx = np.gradient(cell_gray)
        mag = np.hypot(gx, gy)
        peak = float(mag.max())
        binary = (mag > edge_frac * peak).astype(np.uint8) if peak > 0 \
            else np.zeros_like(mag, np.uint8)
    else:  # tone
        thr = float(cell_gray.mean())
        # By default ink = the DARK pixels (dark subject on light bg). --invert flips.
        binary = ((cell_gray < thr) if not invert else (cell_gray >= thr)).astype(np.uint8)
    return gf.crop_fit(binary, N)


def best_glyph(ng: np.ndarray, data: dict, allow_mask: np.ndarray | None,
               orient_w: float) -> int | None:
    """Return the codepoint of the glyph whose shape best matches the 16x16 cell
    grid `ng`, or None for an empty cell (renders as a space).

    Score = BIDIRECTIONAL DILATED COVERAGE (misalignment-tolerant structural
    overlap), optionally reweighted by edge-orientation agreement:
      cover_a = fraction of the CELL's ink covered by the glyph's dilated ink
      cover_b = fraction of the GLYPH's ink covered by the cell's dilated ink
      overlap = min(cover_a, cover_b)   # BOTH directions must agree -> real shape
                                        #   match, not "glyph ink is a superset"
    Taking the min punishes a glyph that only partially explains the cell OR that
    spills far beyond it, which is exactly the structure-preserving behaviour we
    want. orient_w in [0,1] blends in cosine similarity of the 8-bin orientation
    histograms so a horizontal cell prefers horizontal glyphs even at equal overlap.
    """
    cn = ng.reshape(-1).astype(np.uint8)
    if int(cn.sum()) == 0:
        return None  # blank cell -> caller emits a space
    cnd = gf.dilate(ng).reshape(-1).astype(np.uint8)

    norm = data["norm"].astype(np.uint8)          # (M, 256) glyph bitmaps
    norm_dil = data["norm_dil"].astype(np.uint8)  # (M, 256) dilated glyph bitmaps

    cell_ink = max(1, int(cn.sum()))
    glyph_ink = np.maximum(1, norm.sum(1))        # (M,)
    cover_a = (norm_dil & cn).sum(1) / cell_ink   # cell explained by glyph
    cover_b = (norm & cnd).sum(1) / glyph_ink     # glyph explained by cell
    score = np.minimum(cover_a, cover_b).astype(np.float64)

    if orient_w > 0.0:
        co = gf.orientation_hist(ng)              # (8,) this cell's edge directions
        go = data["orient"]                       # (M, 8) every glyph's
        denom = (np.linalg.norm(go, axis=1) * (np.linalg.norm(co) + 1e-9)) + 1e-9
        orient_cos = (go @ co) / denom            # (M,) direction agreement 0..1
        # Multiplicative reweight: full overlap weight, scaled by how much we trust
        # direction. (1-w) keeps pure overlap when orient is ambiguous.
        score = score * ((1.0 - orient_w) + orient_w * orient_cos)

    if allow_mask is not None:
        score = np.where(allow_mask, score, -1.0)  # forbid disallowed blocks

    return int(data["cps"][int(score.argmax())])


def convert_one(img: Image.Image, cols: int, rows: int, data: dict,
                allow_mask: np.ndarray | None, mode: str, invert: bool,
                orient_w: float, edge_frac: float) -> list[str]:
    """Convert one image into a rows-tall list of cols-wide glyph strings."""
    gray = np.asarray(img.convert("L"), np.float64) / 255.0
    H, W = gray.shape
    out_rows: list[str] = []
    for r in range(rows):
        line = []
        for cx in range(cols):
            # Slice the cell region for grid position (cx, r). Integer bounds so the
            # whole image is covered with no gaps.
            y0, y1 = r * H // rows, (r + 1) * H // rows
            x0, x1 = cx * W // cols, (cx + 1) * W // cols
            cell = gray[y0:y1, x0:x1]
            ng = cell_to_norm(cell, mode, invert, edge_frac)
            cp = best_glyph(ng, data, allow_mask, orient_w)
            line.append(" " if cp is None else chr(cp))
        out_rows.append("".join(line))
    return out_rows


def build_allow_mask(data: dict, meta: dict, allow: str | None) -> np.ndarray | None:
    """Build a boolean per-glyph mask restricting matches to blocks whose name
    contains `allow` (comma-separated substrings). None = allow every glyph."""
    if not allow:
        return None
    needles = [s.strip().lower() for s in allow.split(",") if s.strip()]
    blocks = meta["blocks"]
    mask = np.array([any(nd in b.lower() for nd in needles) for b in blocks], bool)
    if not mask.any():
        sys.stderr.write(f"  [warn] --allow '{allow}' matched no block; allowing all\n")
        return None
    return mask


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+", help="image file(s); with --dir8 pass 8 in N..NW order")
    ap.add_argument("--grid", default="2x3", help="COLSxROWS char grid per image (default 2x3)")
    ap.add_argument("--mode", choices=("structure", "tone"), default="structure",
                    help="structure = match edges (Xu-style, default); tone = match fill")
    ap.add_argument("--invert", action="store_true",
                    help="tone mode: treat LIGHT pixels as ink (light subject on dark bg)")
    ap.add_argument("--allow", default=None,
                    help="restrict to block-name substrings, e.g. 'Block Elements,Box Drawing'")
    ap.add_argument("--orient-weight", type=float, default=0.35, dest="orient_w",
                    help="0..1 blend of edge-direction agreement into the score (default 0.35)")
    ap.add_argument("--edge-frac", type=float, default=0.30,
                    help="structure mode: edge kept above this fraction of cell max (default 0.30)")
    ap.add_argument("--dir8", action="store_true",
                    help="treat the (8) images as N NE E SE S SW W NW and label each sprite")
    args = ap.parse_args()

    try:
        cols, rows = (int(v) for v in args.grid.lower().split("x"))
    except ValueError:
        sys.stderr.write(f"bad --grid '{args.grid}'; want COLSxROWS like 2x3\n")
        return 2

    try:
        data, meta = gf.load()
    except SystemExit as e:
        sys.stderr.write(f"{e}\n(build it: python3 scripts/glyph_features.py --all)\n")
        return 2

    allow_mask = build_allow_mask(data, meta, args.allow)

    if args.dir8 and len(args.images) != 8:
        sys.stderr.write(f"--dir8 wants exactly 8 images (N..NW); got {len(args.images)}\n")
        return 2

    for k, path in enumerate(args.images):
        try:
            img = Image.open(path)
        except OSError as e:
            sys.stderr.write(f"cannot open {path}: {e}\n")
            continue
        sprite = convert_one(img, cols, rows, data, allow_mask,
                             args.mode, args.invert, args.orient_w, args.edge_frac)
        label = f"[{_DIR8[k]}] " if args.dir8 else ""
        print(f"{label}{path}  ({cols}x{rows}, {args.mode})")
        for line in sprite:
            print(f"  {line}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
