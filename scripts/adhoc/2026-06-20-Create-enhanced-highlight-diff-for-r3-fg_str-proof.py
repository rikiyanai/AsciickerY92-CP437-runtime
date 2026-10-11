# Ad hoc script: Create enhanced highlight diff for r3 fg_str proof
# Created: 2026-06-20
# Canonical gap: <describe what tool should own this>

from PIL import Image, ImageChops, ImageEnhance
from pathlib import Path
base = Path('docs/research/ascii/verification/fl4260/2026-06-19-color-precondition-slider-proof/perelv_r2r3')
a = Image.open(base / 'color.fg_str.r3_before.png').convert('RGBA')
b = Image.open(base / 'color.fg_str.r3_after_inc.png').convert('RGBA')
diff = ImageChops.difference(a, b)
mask = diff.convert('L')
# Boost mask brightness so subtle differences are visible
enhancer = ImageEnhance.Brightness(mask)
mask = enhancer.enhance(8.0)
red_overlay = Image.new('RGBA', a.size, (255, 0, 0, 0))
red_overlay.putalpha(mask)
combined = Image.alpha_composite(a, red_overlay)
out = base / 'color.fg_str.r3_diff_red_bright.png'
combined.save(out)
print(out)
