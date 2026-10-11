#!/usr/bin/env python3
"""Glyph shape-feature precompute for the Unicode similarity audit.

One pass over every renderable codepoint in the font chain (see
``glyph_morphology_browser.GlyphScorer``) rasterizes it to a 16x16 ink grid and
derives a bundle of shape descriptors used by ``glyph_audit.py`` to discover
density ramps, animation cycles, outline/fill pairs and rotation orbits.

Descriptors per glyph:
    raw          16x16 binary grid as positioned
    norm         crop-to-ink + refit-to-cell (shape ignoring size/position)
    norm_dil     dilated norm (thin strokes overlap -> ramps connect)
    ink          lit-pixel count
    ncomp        8-connected component count (~"how many strokes/parts")
    zoning       4x4 density of the normalized grid (coarse layout)
    hu           7 log-scaled Hu moments of norm (rotation/scale invariant)
    orient       8-bin undirected gradient-orientation histogram (flow)
    d4           crc32 of the D4-canonical norm (rotation/reflection orbit key)

Cache (regenerable; lives under the gitignored .run/):
    .run/glyph_audit/glyph_features.npz    numeric arrays, parallel to cps[]
    .run/glyph_audit/glyph_features.meta.json   cps/names/fonts/blocks

Usage:
    python3 scripts/glyph_features.py --blocks "Ogham,Miscellaneous Symbols,Katakana"
    python3 scripts/glyph_features.py --all
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
import zlib
from pathlib import Path

import numpy as np
from PIL import Image

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from glyph_morphology_browser import (  # noqa: E402
    BLOCKS,
    GlyphScorer,
    find_block_for_cp,
)

REPO_ROOT = SCRIPTS_DIR.parent
CACHE_DIR = REPO_ROOT / ".run" / "glyph_audit"
CACHE_NPZ = CACHE_DIR / "glyph_features.npz"
CACHE_META = CACHE_DIR / "glyph_features.meta.json"
N = 16
MIN_INK = 3  # drop near-blank glyphs (space, lone-pixel artifacts) from the corpus


# ---------------------------------------------------------------------------
# Per-glyph descriptors (operate on a NxN uint8 0/1 numpy grid)
# ---------------------------------------------------------------------------
def crop_fit(g: np.ndarray, n: int = N) -> np.ndarray:
    ys, xs = np.where(g > 0)
    if xs.size == 0:
        return np.zeros((n, n), np.uint8)
    crop = g[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    ch, cw = crop.shape
    scale = min(n / cw, n / ch)
    nw, nh = max(1, round(cw * scale)), max(1, round(ch * scale))
    im = Image.fromarray((crop * 255).astype(np.uint8)).resize((nw, nh), Image.NEAREST)
    out = np.zeros((n, n), np.uint8)
    a = (np.asarray(im) > 0).astype(np.uint8)
    oy, ox = (n - nh) // 2, (n - nw) // 2
    out[oy:oy + nh, ox:ox + nw] = a
    return out


def dilate(g: np.ndarray) -> np.ndarray:
    d = g.copy()
    d[:-1] |= g[1:]
    d[1:] |= g[:-1]
    d[:, :-1] |= g[:, 1:]
    d[:, 1:] |= g[:, :-1]
    return d


def n_components(g: np.ndarray) -> int:
    visited = np.zeros_like(g, dtype=bool)
    h, w = g.shape
    count = 0
    for i in range(h):
        for j in range(w):
            if g[i, j] and not visited[i, j]:
                count += 1
                stack = [(i, j)]
                visited[i, j] = True
                while stack:
                    y, x = stack.pop()
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            ny, nx = y + dy, x + dx
                            if 0 <= ny < h and 0 <= nx < w and g[ny, nx] and not visited[ny, nx]:
                                visited[ny, nx] = True
                                stack.append((ny, nx))
    return count


def zoning(g: np.ndarray, k: int = 4) -> np.ndarray:
    h, w = g.shape
    out = np.empty(k * k, np.float32)
    idx = 0
    for by in range(k):
        for bx in range(k):
            cell = g[by * h // k:(by + 1) * h // k, bx * w // k:(bx + 1) * w // k]
            out[idx] = cell.mean()
            idx += 1
    return out


def hu_moments(g: np.ndarray) -> np.ndarray:
    gf = g.astype(np.float64)
    m00 = gf.sum()
    if m00 == 0:
        return np.zeros(7, np.float32)
    ys, xs = np.mgrid[0:gf.shape[0], 0:gf.shape[1]]
    xbar = (xs * gf).sum() / m00
    ybar = (ys * gf).sum() / m00
    xc, yc = xs - xbar, ys - ybar

    def eta(p, q):
        return (xc ** p * yc ** q * gf).sum() / (m00 ** (1 + (p + q) / 2.0))

    n20, n02, n11 = eta(2, 0), eta(0, 2), eta(1, 1)
    n30, n12, n21, n03 = eta(3, 0), eta(1, 2), eta(2, 1), eta(0, 3)
    h = np.zeros(7)
    h[0] = n20 + n02
    h[1] = (n20 - n02) ** 2 + 4 * n11 ** 2
    h[2] = (n30 - 3 * n12) ** 2 + (3 * n21 - n03) ** 2
    h[3] = (n30 + n12) ** 2 + (n21 + n03) ** 2
    h[4] = ((n30 - 3 * n12) * (n30 + n12) * ((n30 + n12) ** 2 - 3 * (n21 + n03) ** 2)
            + (3 * n21 - n03) * (n21 + n03) * (3 * (n30 + n12) ** 2 - (n21 + n03) ** 2))
    h[5] = ((n20 - n02) * ((n30 + n12) ** 2 - (n21 + n03) ** 2)
            + 4 * n11 * (n30 + n12) * (n21 + n03))
    h[6] = ((3 * n21 - n03) * (n30 + n12) * ((n30 + n12) ** 2 - 3 * (n21 + n03) ** 2)
            - (n30 - 3 * n12) * (n21 + n03) * (3 * (n30 + n12) ** 2 - (n21 + n03) ** 2))
    h = np.sign(h) * np.log10(np.abs(h) + 1e-30)
    return h.astype(np.float32)


def orientation_hist(g: np.ndarray, bins: int = 8) -> np.ndarray:
    gf = g.astype(np.float64)
    gx = np.zeros_like(gf)
    gy = np.zeros_like(gf)
    gx[:, 1:-1] = gf[:, 2:] - gf[:, :-2]
    gy[1:-1, :] = gf[2:, :] - gf[:-2, :]
    mag = np.hypot(gx, gy)
    ang = np.arctan2(gy, gx) % np.pi  # undirected 0..pi
    hist = np.zeros(bins, np.float64)
    idx = (ang / np.pi * bins).astype(int) % bins
    for b in range(bins):
        hist[b] = mag[idx == b].sum()
    s = hist.sum()
    return (hist / s).astype(np.float32) if s > 0 else hist.astype(np.float32)


def d4_canonical_crc(g: np.ndarray) -> int:
    a = g
    best = None
    for _ in range(4):
        for cand in (a.tobytes(), np.ascontiguousarray(np.fliplr(a)).tobytes()):
            if best is None or cand < best:
                best = cand
        a = np.rot90(a)
    return int(zlib.crc32(best))


def grid_from_scorer(scorer: GlyphScorer, cp: int) -> np.ndarray:
    return np.asarray(scorer.ink_grid(chr(cp)), dtype=np.uint8)


# ---------------------------------------------------------------------------
# Cache build / load
# ---------------------------------------------------------------------------
def select_codepoints(scorer: GlyphScorer, block_filter: list[str] | None, want_all: bool) -> list[int]:
    cps = sorted(scorer.font_cps)
    if want_all or not block_filter:
        return cps
    ranges = []
    for needle in block_filter:
        up = needle.strip().upper()
        for lo, hi, name in BLOCKS:
            if up in name.upper():
                ranges.append((lo, hi))
    out = [cp for cp in cps if any(lo <= cp <= hi for lo, hi in ranges)]
    return out


def build(block_filter: list[str] | None, want_all: bool, progress: bool = True) -> dict:
    scorer = GlyphScorer()
    cps_all = select_codepoints(scorer, block_filter, want_all)
    rows = []
    total = len(cps_all)
    for i, cp in enumerate(cps_all):
        raw = grid_from_scorer(scorer, cp)
        ink = int(raw.sum())
        if ink < MIN_INK:
            continue
        norm = crop_fit(raw)
        rows.append((cp, raw, norm, ink))
        if progress and i % 2000 == 0:
            sys.stderr.write(f"\r  rendering {i}/{total} ({len(rows)} kept)…")
            sys.stderr.flush()
    if progress:
        sys.stderr.write(f"\r  rendered {total}, kept {len(rows)} non-blank glyphs.\n")

    m = len(rows)
    cps = np.array([r[0] for r in rows], np.int32)
    raw = np.stack([r[1].reshape(-1) for r in rows]).astype(np.uint8)
    norm = np.stack([r[2].reshape(-1) for r in rows]).astype(np.uint8)
    ink = np.array([r[3] for r in rows], np.int32)
    norm_dil = np.empty_like(norm)
    ncomp = np.empty(m, np.int32)
    zon = np.empty((m, 16), np.float32)
    hu = np.empty((m, 7), np.float32)
    orient = np.empty((m, 8), np.float32)
    d4 = np.empty(m, np.int64)
    for j in range(m):
        ng = norm[j].reshape(N, N)
        norm_dil[j] = dilate(ng).reshape(-1)
        ncomp[j] = n_components(raw[j].reshape(N, N))
        zon[j] = zoning(ng)
        hu[j] = hu_moments(ng)
        orient[j] = orientation_hist(ng)
        d4[j] = d4_canonical_crc(ng)
        if progress and j % 2000 == 0:
            sys.stderr.write(f"\r  features {j}/{m}…")
            sys.stderr.flush()
    if progress:
        sys.stderr.write(f"\r  features computed for {m} glyphs.        \n")

    names, fonts, blocks = [], [], []
    for cp in cps.tolist():
        names.append(unicodedata.name(chr(cp), ""))
        fonts.append(scorer.font_for(cp))
        bi = find_block_for_cp(cp)
        blocks.append(BLOCKS[bi][2] if bi is not None else "")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(CACHE_NPZ, cps=cps, raw=raw, norm=norm, norm_dil=norm_dil,
                        ink=ink, ncomp=ncomp, zoning=zon, hu=hu, orient=orient, d4=d4)
    CACHE_META.write_text(json.dumps({
        "cps": cps.tolist(), "names": names, "fonts": fonts, "blocks": blocks,
        "count": m, "cell_px": N, "min_ink": MIN_INK,
        "font_chain": scorer.font_names,
    }))
    return {"count": m}


def load() -> tuple[dict, dict]:
    if not CACHE_NPZ.exists():
        raise SystemExit("no feature cache — run: python3 scripts/glyph_features.py --blocks \"...\" (or --all)")
    data = dict(np.load(CACHE_NPZ))
    meta = json.loads(CACHE_META.read_text())
    return data, meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--blocks", type=str, default=None,
                    help="comma-separated block-name substrings to restrict the corpus")
    ap.add_argument("--all", action="store_true", help="build for every renderable codepoint")
    args = ap.parse_args()
    bf = [b for b in args.blocks.split(",")] if args.blocks else None
    res = build(bf, args.all)
    print(f"cache written: {CACHE_NPZ} ({res['count']} glyphs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
