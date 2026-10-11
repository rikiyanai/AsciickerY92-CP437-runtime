# Ad hoc script: Generate direction-aware marker diffs for all per-elv proof rows v2
# Created: 2026-06-20
# Canonical gap: <describe what tool should own this>

from PIL import Image
from pathlib import Path
import json

base = Path('docs/research/ascii/verification/fl4260/2026-06-19-color-precondition-slider-proof')
proof = json.loads((base / 'PROOF_all16_attempt.json').read_text())

for label, row in proof['sliders'].items():
    r = int(label.split('.r')[-1][0])
    sub = 'perelv_r0r1' if r in (0, 1) else 'perelv_r2r3'
    d = base / sub
    suffix = 'after_inc' if row['direction'] == 'period' else 'after_dec'
    before = d / f'{label}_before.png'
    after = d / f'{label}_{suffix}.png'
    if not before.exists() or not after.exists():
        print('missing', label, before.exists(), after.exists())
        continue
    a = Image.open(before).convert('RGB')
    b = Image.open(after).convert('RGB')
    if a.size != b.size:
        print('size mismatch', label)
        continue
    marker = Image.new('RGB', a.size, (0, 0, 0))
    px_a = a.load(); px_b = b.load(); px_m = marker.load()
    changed = 0
    for y in range(a.size[1]):
        for x in range(a.size[0]):
            if px_a[x, y] != px_b[x, y]:
                px_m[x, y] = (255, 0, 0)
                changed += 1
    out = d / f'{label}_diff_marker.png'
    marker.save(out)
    print(label, out.name, changed, 'resolver=' + str(row.get('resolver_changed')))
