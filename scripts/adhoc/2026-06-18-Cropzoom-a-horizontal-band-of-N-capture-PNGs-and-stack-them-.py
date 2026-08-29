# Ad hoc script: Crop+zoom a horizontal band of N capture PNGs and stack them vertically for side-by-side iteration comparison (FL-4231 mountain/foliage 2D->3D A/B). Args: --band top|mid|bottom --zoom N --out PATH img1 [img2 ...]
# Created: 2026-06-18
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""Crop a horizontal band (top/mid/bottom third) of each input PNG, upscale, and
stack vertically with a label, so successive FL-4231 capture iterations can be
compared at real glyph detail instead of downsampled full frames."""
import argparse
from pathlib import Path
from PIL import Image, ImageDraw

BANDS = {"top": (0.0, 0.42), "mid": (0.30, 0.72), "bottom": (0.55, 1.0)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--band", default="top", choices=list(BANDS))
    ap.add_argument("--zoom", type=int, default=3)
    ap.add_argument("--out", default="/tmp/crop_compare.png")
    ap.add_argument("imgs", nargs="+")
    a = ap.parse_args()
    lo, hi = BANDS[a.band]
    tiles = []
    for p in a.imgs:
        im = Image.open(p).convert("RGB")
        y0, y1 = int(im.height * lo), int(im.height * hi)
        crop = im.crop((0, y0, im.width, y1))
        crop = crop.resize((crop.width * a.zoom, crop.height * a.zoom), Image.NEAREST)
        band = Image.new("RGB", (crop.width, crop.height + 22), (16, 16, 16))
        band.paste(crop, (0, 22))
        ImageDraw.Draw(band).text((6, 5), Path(p).parent.name + "/" + Path(p).name, fill=(230, 230, 120))
        tiles.append(band)
    w = max(t.width for t in tiles)
    h = sum(t.height for t in tiles) + 4 * (len(tiles) - 1)
    canvas = Image.new("RGB", (w, h), (40, 40, 40))
    y = 0
    for t in tiles:
        canvas.paste(t, (0, y)); y += t.height + 4
    canvas.save(a.out)
    print("WROTE", a.out, canvas.size)

if __name__ == "__main__":
    raise SystemExit(main())
