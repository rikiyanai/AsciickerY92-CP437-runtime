# Ad hoc script: Scan FL-4131 admitted material atlas cells for empty diagnostic candidates
# Created: 2026-06-01
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
import json
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
manifest = json.loads((repo / "assets/glyphs/fixtures/extended_glyph_material_additive_v1.json").read_text(encoding="utf-8"))
page = json.loads((repo / "assets/glyphs/atlases/material.additive.v1.page16_rgba8.json").read_text(encoding="utf-8"))
width = int(page["width"])
cell = int(page["cell_px"])
entries = manifest["entries"]
empty = []
nonempty = []
for i, entry in enumerate(entries):
    gid = int(entry["glyph_id"])
    x0 = (i % 16) * cell
    y0 = (i // 16) * cell
    ink = 0
    alpha = 0
    for y in range(cell):
        for x in range(cell):
            off = ((y0 + y) * width + x0 + x) * 4
            r, g, b, a = page["rgba8"][off:off + 4]
            if a:
                alpha += 1
            if a and (r or g or b):
                ink += 1
    rec = (gid, entry.get("label"), entry.get("unicode"), ink, alpha)
    if ink == 0:
        empty.append(rec)
    else:
        nonempty.append(rec)
print(f"entries={len(entries)} nonempty={len(nonempty)} empty={len(empty)}")
for rec in empty[:200]:
    print("EMPTY", rec)
