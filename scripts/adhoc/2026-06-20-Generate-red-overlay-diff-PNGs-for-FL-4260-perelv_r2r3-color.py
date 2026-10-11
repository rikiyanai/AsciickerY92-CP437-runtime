# Ad hoc script: Generate red-overlay diff PNGs for FL-4260 perelv_r2r3 color.fg_str.r3 proof pair
# Created: 2026-06-20
# Canonical gap: <describe what tool should own this>

from PIL import Image, ImageChops
from pathlib import Path
base = Path('docs/research/ascii/verification/fl4260/2026-06-19-color-precondition-slider-proof/perelv_r2r3')
a = Image.open(base / 'color.fg_str.r3_before.png').convert('RGBA')
b = Image.open(base / 'color.fg_str.r3_after_inc.png').convert('RGBA')
diff = ImageChops.difference(a, b)
mask = diff.convert('L')
red_overlay = Image.new('RGBA', a.size, (255, 0, 0, 0))
red_overlay.putalpha(mask)
combined = Image.alpha_composite(a, red_overlay)
out = base / 'color.fg_str.r3_diff_red.png'
combined.save(out)
print(out)
