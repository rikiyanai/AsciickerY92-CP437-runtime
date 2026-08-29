# Ad hoc script: Create pure-red marker diff for r3 fg_str proof
# Created: 2026-06-20
# Canonical gap: <describe what tool should own this>

from PIL import Image
from pathlib import Path
base = Path('docs/research/ascii/verification/fl4260/2026-06-19-color-precondition-slider-proof/perelv_r2r3')
a = Image.open(base / 'color.fg_str.r3_before.png').convert('RGB')
b = Image.open(base / 'color.fg_str.r3_after_inc.png').convert('RGB')
px_a = a.load(); px_b = b.load()
w, h = a.size
marker = Image.new('RGB', (w, h), (0, 0, 0))
px_m = marker.load()
changed = 0
for y in range(h):
    for x in range(w):
        if px_a[x, y] != px_b[x, y]:
            px_m[x, y] = (255, 0, 0)
            changed += 1
out = base / 'color.fg_str.r3_diff_marker.png'
marker.save(out)
print(out, changed)
