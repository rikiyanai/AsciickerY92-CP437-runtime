# Ad hoc script: Compute FL-4208 water glyph receipt integers from compiled material atlas page16
# Created: 2026-06-07
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "assets/glyphs/atlases/material.additive.v1.page16_rgba8.json"
AOA = ROOT / "assets/glyphs/atlases/material.additive.v1.atlas_of_atlases.json"
GLYPHS = range(512, 520)

def int31(hex_text: str) -> int:
    return (int(hex_text[:8], 16) & 0x7fffffff) or 1

def main() -> int:
    page = json.loads(PAGE.read_text(encoding="utf-8"))
    aoa = json.loads(AOA.read_text(encoding="utf-8"))
    width = int(page["width"])
    cell_px = int(page["cell_px"])
    rgba8 = page["rgba8"]
    font_hash = int31(str(aoa["font_sha256"]))
    for glyph_id in GLYPHS:
        rect = aoa["glyph_index"][str(glyph_id)]
        x0 = int(rect[1])
        y0 = int(rect[2])
        raw = bytearray()
        for y in range(cell_px):
            off = ((y0 + y) * width + x0) * 4
            for x in range(cell_px):
                raw.extend(int(v) for v in rgba8[off + x * 4: off + x * 4 + 4])
        digest = hashlib.sha256(bytes(raw)).hexdigest()
        print(json.dumps({
            "glyph_id": glyph_id,
            "font_hash": font_hash,
            "rendered_bitmap_hash": int31(digest),
            "rendered_bitmap_sha256": digest,
        }, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
