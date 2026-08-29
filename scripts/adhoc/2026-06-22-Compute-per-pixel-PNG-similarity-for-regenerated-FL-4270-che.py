# Ad hoc script: Compute per-pixel PNG similarity for regenerated FL-4270 checkpoint baseline versus current exact-pose frames
# Created: 2026-06-22
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
from __future__ import annotations
import json
import sys
from pathlib import Path
from PIL import Image, ImageChops

if len(sys.argv) < 3:
    raise SystemExit('usage: png_similarity.py baseline.png current.png [out.json]')
base = Path(sys.argv[1])
cur = Path(sys.argv[2])
out = Path(sys.argv[3]) if len(sys.argv) >= 4 else None
im_a = Image.open(base).convert('RGB')
im_b = Image.open(cur).convert('RGB')
if im_a.size != im_b.size:
    im_b = im_b.resize(im_a.size, Image.Resampling.LANCZOS)
diff = ImageChops.difference(im_a, im_b)
px = diff.load()
w, h = im_a.size
total = w * h
exact = 0
near = 0
sum_abs = 0
for y in range(h):
    for x in range(w):
        r, g, b = px[x, y]
        d = r + g + b
        sum_abs += d
        if d == 0:
            exact += 1
        if d <= 30:
            near += 1
max_abs = total * 255 * 3
result = {
    'baseline': str(base),
    'current': str(cur),
    'size': [w, h],
    'exact_match_ratio': exact / total if total else 0.0,
    'near_match_ratio_sum_abs_le_30': near / total if total else 0.0,
    'mean_abs_channel_delta': sum_abs / float(total * 3) if total else 0.0,
    'similarity_1_minus_normalized_abs': 1.0 - (sum_abs / float(max_abs)) if max_abs else 0.0,
}
text = json.dumps(result, indent=2)
print(text)
if out is not None:
    out.write_text(text + '\n')
