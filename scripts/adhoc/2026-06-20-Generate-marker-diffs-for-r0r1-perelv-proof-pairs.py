# Ad hoc script: Generate marker diffs for r0/r1 perelv proof pairs
# Created: 2026-06-20
# Canonical gap: <describe what tool should own this>

from PIL import Image
from pathlib import Path
import json

base = Path('docs/research/ascii/verification/fl4260/2026-06-19-color-precondition-slider-proof')
pairs = [
    ('perelv_r0r1', 'color.band_thres.r0'),
    ('perelv_r0r1', 'color.fg_str.r1'),
    ('perelv_r2r3', 'color.shade_contrast.r2'),
]

for sub, label in pairs:
    d = base / sub
    a = Image.open(d / f'{label}_before.png').convert('RGB')
    b = Image.open(d / f'{label}_after_inc.png').convert('RGB')
    px_a = a.load(); px_b = b.load()
    marker = Image.new('RGB', a.size, (0, 0, 0))
    px_m = marker.load()
    changed = 0
    for y in range(a.size[1]):
        for x in range(a.size[0]):
            if px_a[x, y] != px_b[x, y]:
                px_m[x, y] = (255, 0, 0)
                changed += 1
    out = d / f'{label}_diff_marker.png'
    marker.save(out)
    print(out, changed)
