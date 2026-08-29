# Ad hoc script: Convert FL-4257 render probe raw AnsiCell grid to PNG for local visual review
# Created: 2026-06-11
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

XTERM = [
    (0,0,0),(128,0,0),(0,128,0),(128,128,0),(0,0,128),(128,0,128),(0,128,128),(192,192,192),
    (128,128,128),(255,0,0),(0,255,0),(255,255,0),(0,0,255),(255,0,255),(0,255,255),(255,255,255),
]
for r in range(6):
    for g in range(6):
        for b in range(6):
            def c(v: int) -> int:
                return 55 + 40 * v if v else 0
            XTERM.append((c(r), c(g), c(b)))
for i in range(24):
    v = 8 + i * 10
    XTERM.append((v, v, v))


def font(size: int) -> ImageFont.ImageFont:
    for p in (
        "/System/Library/Fonts/Menlo.ttc",
        "/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Supplemental/Courier New.ttf",
    ):
        try:
            return ImageFont.truetype(p, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: fl4257_raw_to_png.py input.raw output.png", file=sys.stderr)
        return 2
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    with src.open("r", encoding="utf-8", errors="replace") as f:
        header = f.readline().split()
        if len(header) != 2:
            raise SystemExit(f"bad raw header in {src}")
        w, h = int(header[0]), int(header[1])
        cells = []
        for line in f:
            parts = line.split()
            if len(parts) < 3:
                continue
            fg, bg, gl = int(parts[0]), int(parts[1]), int(parts[2])
            cells.append((fg & 255, bg & 255, gl & 255))
    if len(cells) != w * h:
        raise SystemExit(f"bad raw cell count in {src}: {len(cells)} != {w*h}")
    cell_w, cell_h = 8, 14
    img = Image.new("RGB", (w * cell_w, h * cell_h), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    fnt = font(12)
    for y in range(h):
        for x in range(w):
            fg, bg, gl = cells[y * w + x]
            x0, y0 = x * cell_w, y * cell_h
            draw.rectangle((x0, y0, x0 + cell_w - 1, y0 + cell_h - 1), fill=XTERM[bg])
            ch = chr(gl) if 32 <= gl < 127 else " "
            if ch != " ":
                draw.text((x0, y0 - 1), ch, font=fnt, fill=XTERM[fg])
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst)
    print(dst)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
