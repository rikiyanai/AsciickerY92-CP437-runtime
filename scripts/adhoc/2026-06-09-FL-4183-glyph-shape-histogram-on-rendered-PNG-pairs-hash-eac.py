# Ad hoc script: FL-4183 glyph-shape histogram on rendered PNG pairs: hash each cell's fg/bg mask and count unique shapes per image, list shapes in one but not the other, for proving whether the baseline A actually has more distinct grass-region glyph shapes than the current candidate
# Created: 2026-06-09
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4183 glyph-shape histogram on rendered A/B PNG pairs.

For each PNG:
  * Walk the image as a CELL_W x CELL_H grid
  * Per cell, compute a fg/bg mask (foreground = pixels far from cell mode color)
  * Hash the mask as the shape signature
  * Tally unique shapes
  * Output: total unique, top-K, set diff (A - B, B - A)

The goal: prove whether baseline A's grass region carries more distinct glyph
shapes than the current candidate (operator observation 2026-06-09 that
"BASELINE STILL HAS MANY MORE GLYPHS"). Color-owner attempts (olive-tint
disable, surface_stable_dither widening) were rejected because they can change
hue/luma but cannot add new glyph shapes.

This is a SHAPE-only signature (color stripped). It treats each cell as a
binary mask, so a baseline glyph and a candidate glyph of the same shape but
different color collide to the same bucket.

Region filter: --region "x0,y0,x1,y1" (in cell coords) restricts the histogram
to a cell rectangle. Useful for isolating grass-only regions.
"""
import argparse
import collections
import hashlib
import json
import pathlib
import sys
from PIL import Image

CELL_W = 8
CELL_H = 16

def cell_signature(pixels, x0, y0, cw, ch):
    """Return (shape_hash, fg_pixel_count, dominant_bg_rgb).

    Signature is color-blind: each cell is split by per-cell luminance median
    into two groups. The group with fewer pixels is the foreground (the glyph).
    Two cells with the same glyph but different fg/bg colors collide to the
    same shape hash. This avoids the noise sensitivity of color-distance
    thresholding when there is subtle per-cell dither / chromatic variation.
    """
    lumas = []
    for y in range(y0, y0 + ch):
        for x in range(x0, x0 + cw):
            r, g, b = pixels[x, y][:3]
            lumas.append(0.2126*r + 0.7152*g + 0.0722*b)
    sorted_l = sorted(lumas)
    median = sorted_l[len(sorted_l) // 2]
    # Count pixels above/below median; the smaller group is the glyph fg.
    above_count = sum(1 for v in lumas if v > median)
    below_count = sum(1 for v in lumas if v < median)
    # If image is solid (single luma), call it empty -> all-zero mask.
    if sorted_l[-1] - sorted_l[0] < 16.0:
        mask = '0' * (cw * ch)
        mode_rgb = pixels[x0, y0][:3]
        return hashlib.sha1(mask.encode()).hexdigest()[:12], 0, mode_rgb
    fg_is_above = above_count <= below_count
    bits = []
    bg_pixels = []
    fg_count = 0
    i = 0
    for y in range(y0, y0 + ch):
        for x in range(x0, x0 + cw):
            l = lumas[i]
            is_fg = (l > median) if fg_is_above else (l < median)
            bits.append('1' if is_fg else '0')
            if is_fg:
                fg_count += 1
            else:
                bg_pixels.append(pixels[x, y][:3])
            i += 1
    mask = ''.join(bits)
    if bg_pixels:
        avg_bg = (
            sum(p[0] for p in bg_pixels) // len(bg_pixels),
            sum(p[1] for p in bg_pixels) // len(bg_pixels),
            sum(p[2] for p in bg_pixels) // len(bg_pixels),
        )
    else:
        avg_bg = pixels[x0, y0][:3]
    h = hashlib.sha1(mask.encode()).hexdigest()[:12]
    return h, fg_count, avg_bg

def hist_png(path, region=None):
    img = Image.open(path).convert('RGB')
    W, H = img.size
    pixels = img.load()
    cols = W // CELL_W
    rows = H // CELL_H
    if region:
        x0c, y0c, x1c, y1c = region
        x0c = max(0, x0c); y0c = max(0, y0c)
        x1c = min(cols, x1c); y1c = min(rows, y1c)
    else:
        x0c, y0c, x1c, y1c = 0, 0, cols, rows
    sig_counts = collections.Counter()
    sig_fg = {}
    sig_examples = {}
    sig_bg_examples = {}
    for r in range(y0c, y1c):
        for c in range(x0c, x1c):
            x = c * CELL_W
            y = r * CELL_H
            h, fg, bg = cell_signature(pixels, x, y, CELL_W, CELL_H)
            sig_counts[h] += 1
            sig_fg[h] = fg
            sig_examples.setdefault(h, (c, r))
            sig_bg_examples.setdefault(h, bg)
    return {
        'path': str(path),
        'image_size': [W, H],
        'cell_grid': [cols, rows],
        'region_cells': [x0c, y0c, x1c, y1c],
        'total_cells': (x1c - x0c) * (y1c - y0c),
        'unique_shapes': len(sig_counts),
        'sig_counts': dict(sig_counts),
        'sig_fg_pixels': sig_fg,
        'sig_first_cell': {k: list(v) for k, v in sig_examples.items()},
        'sig_bg_rgb': {k: list(v) for k, v in sig_bg_examples.items()},
    }

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--a', required=True, help='Baseline PNG')
    ap.add_argument('--b', required=True, help='Candidate PNG')
    ap.add_argument('--region', help='Cell rect "x0,y0,x1,y1" (cell coords)')
    ap.add_argument('--top', type=int, default=20)
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    region = None
    if args.region:
        region = tuple(int(v) for v in args.region.split(','))
        assert len(region) == 4
    A = hist_png(args.a, region=region)
    B = hist_png(args.b, region=region)
    a_set = set(A['sig_counts'].keys())
    b_set = set(B['sig_counts'].keys())
    only_a = sorted(a_set - b_set, key=lambda k: -A['sig_counts'][k])
    only_b = sorted(b_set - a_set, key=lambda k: -B['sig_counts'][k])
    summary = {
        'a': {'path': A['path'], 'cells': A['total_cells'], 'unique_shapes': A['unique_shapes']},
        'b': {'path': B['path'], 'cells': B['total_cells'], 'unique_shapes': B['unique_shapes']},
        'shapes_only_in_a': len(only_a),
        'shapes_only_in_b': len(only_b),
        'top_only_in_a': [
            {'sig': k, 'count_in_a': A['sig_counts'][k], 'fg_pixels': A['sig_fg_pixels'][k],
             'first_cell': A['sig_first_cell'][k], 'bg_rgb': A['sig_bg_rgb'][k]}
            for k in only_a[:args.top]
        ],
        'top_only_in_b': [
            {'sig': k, 'count_in_b': B['sig_counts'][k], 'fg_pixels': B['sig_fg_pixels'][k],
             'first_cell': B['sig_first_cell'][k], 'bg_rgb': B['sig_bg_rgb'][k]}
            for k in only_b[:args.top]
        ],
    }
    if args.json:
        print(json.dumps(summary, indent=2))
        return
    print(f"A unique shapes: {A['unique_shapes']}  cells={A['total_cells']}")
    print(f"B unique shapes: {B['unique_shapes']}  cells={B['total_cells']}")
    print(f"Shapes only in A: {len(only_a)}")
    print(f"Shapes only in B: {len(only_b)}")
    print(f"\nTop {args.top} shapes present in A but missing from B (by count in A):")
    print(f"  {'sig':<14} {'count':>7} {'fg_px':>7} {'first_cell':>12}  bg_rgb")
    for s in summary['top_only_in_a']:
        cell = f"({s['first_cell'][0]},{s['first_cell'][1]})"
        print(f"  {s['sig']:<14} {s['count_in_a']:>7} {s['fg_pixels']:>7} {cell:>12}  {tuple(s['bg_rgb'])}")
    print(f"\nTop {args.top} shapes present in B but missing from A (by count in B):")
    print(f"  {'sig':<14} {'count':>7} {'fg_px':>7} {'first_cell':>12}  bg_rgb")
    for s in summary['top_only_in_b']:
        cell = f"({s['first_cell'][0]},{s['first_cell'][1]})"
        print(f"  {s['sig']:<14} {s['count_in_b']:>7} {s['fg_pixels']:>7} {cell:>12}  {tuple(s['bg_rgb'])}")

if __name__ == '__main__':
    main()
